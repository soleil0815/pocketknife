"""2.62.0: draws labels as a 1-bit picture for label printers (Brother QL, Dymo LabelWriter, Zebra).

The printers get dots, not a PDF - so the server draws the label itself. Without extra libraries: a small TrueType reader
(cmap format 4, glyf incl. composite glyphs, hmtx) and a scanline filler with the non-zero winding rule. No hinting -
at 203/300 dpi the letters are 20-60 dots high, that is enough. The font is Liberation Sans 2.1.5 (SIL OFL 1.1,
app/fonts) - the same widths as Helvetica, which the PDF uses, so text that fits on the PDF also fits on the picture.
"""
from __future__ import annotations

import os
import struct
import threading

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FONTS = {False: "LiberationSans-Regular.ttf", True: "LiberationSans-Bold.ttf"}


class Bitmap:
    """1 bit per dot, stored as one byte per dot (simple and fast enough for labels up to about 1300 × 1300 dots)."""

    def __init__(self, w: int, h: int):
        self.w, self.h = int(w), int(h)
        self.px = bytearray(self.w * self.h)

    def get(self, x: int, y: int) -> int:
        return self.px[y * self.w + x]

    def fill_span(self, y: int, x0: int, x1: int):
        """Dots x0 .. x1-1 of row y black (clipped)."""
        if y < 0 or y >= self.h:
            return
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 > x0:
            o = y * self.w
            self.px[o + x0:o + x1] = b"\x01" * (x1 - x0)

    def rect(self, x0: int, y0: int, x1: int, y1: int):
        for y in range(max(0, y0), min(self.h, y1)):
            self.fill_span(y, x0, x1)

    def row(self, y: int) -> bytes:
        o = y * self.w
        return bytes(self.px[o:o + self.w])

    def rotate_left(self) -> "Bitmap":
        """90° counter-clockwise: the right edge becomes the top."""
        out = Bitmap(self.h, self.w)
        for y in range(self.h):
            for x in range(self.w):
                if self.px[y * self.w + x]:
                    out.px[(self.w - 1 - x) * out.w + y] = 1
        return out

    def crop(self, x: int, y: int, w: int, h: int) -> "Bitmap":
        out = Bitmap(w, h)
        for r in range(h):
            sy = y + r
            if 0 <= sy < self.h:
                for c in range(w):
                    sx = x + c
                    if 0 <= sx < self.w and self.px[sy * self.w + sx]:
                        out.px[r * w + c] = 1
        return out

    def black(self) -> int:
        return sum(self.px)


def pack_row(bits: bytes, nbytes: int, offset: int = 0, mirror: bool = False) -> bytes:
    """Dots of one row (1 = black) → nbytes bytes, MSB first, starting at dot `offset`; mirror = right to left."""
    total = nbytes * 8
    out = bytearray(nbytes)
    n = len(bits)
    for i in range(n):
        if bits[i]:
            pos = offset + i
            if mirror:
                pos = total - 1 - pos
            if 0 <= pos < total:
                out[pos >> 3] |= 0x80 >> (pos & 7)
    return bytes(out)


class Font:
    """The parts of a TrueType file needed for drawing: character → glyph, advance widths, outlines."""

    def __init__(self, data: bytes):
        self.data = data
        num = struct.unpack(">H", data[4:6])[0]
        self.tables = {}
        for i in range(num):
            tag, _sum, off, length = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            self.tables[tag.decode("latin-1")] = (off, length)
        for need in ("head", "cmap", "loca", "glyf", "hhea", "hmtx", "maxp"):
            if need not in self.tables:
                raise ValueError(f"font table missing: {need}")
        head = self.tables["head"][0]
        self.units = struct.unpack(">H", data[head + 18:head + 20])[0]
        self.long_loca = struct.unpack(">h", data[head + 50:head + 52])[0] == 1
        self.num_glyphs = struct.unpack(">H", data[self.tables["maxp"][0] + 4:self.tables["maxp"][0] + 6])[0]
        self.num_hmetrics = struct.unpack(">H", data[self.tables["hhea"][0] + 34:self.tables["hhea"][0] + 36])[0]
        self._cmap = self._read_cmap()
        self._cache: dict[int, list] = {}

    def _t(self, tag):
        return self.tables[tag][0]

    def _read_cmap(self) -> dict[int, int]:
        d, base = self.data, self._t("cmap")
        n = struct.unpack(">H", d[base + 2:base + 4])[0]
        sub = None
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", d[base + 4 + 8 * i:base + 12 + 8 * i])
            if pid == 3 and eid == 1:
                sub = base + off
        if sub is None or struct.unpack(">H", d[sub:sub + 2])[0] != 4:
            raise ValueError("font without a Unicode cmap (format 4)")
        segx2 = struct.unpack(">H", d[sub + 6:sub + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(f">{seg}H", d[sub + 14:sub + 14 + segx2])
        starts_off = sub + 16 + segx2
        starts = struct.unpack(f">{seg}H", d[starts_off:starts_off + segx2])
        deltas = struct.unpack(f">{seg}h", d[starts_off + segx2:starts_off + 2 * segx2])
        ro_off = starts_off + 2 * segx2
        ros = struct.unpack(f">{seg}H", d[ro_off:ro_off + segx2])
        cmap = {}
        for i in range(seg):
            for c in range(starts[i], ends[i] + 1):
                if c == 0xFFFF:
                    continue
                if ros[i] == 0:
                    g = (c + deltas[i]) & 0xFFFF
                else:
                    p = ro_off + 2 * i + ros[i] + 2 * (c - starts[i])
                    g = struct.unpack(">H", d[p:p + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                if g:
                    cmap[c] = g
        return cmap

    def glyph(self, ch: str) -> int:
        return self._cmap.get(ord(ch), self._cmap.get(ord("?"), 0))

    def advance(self, g: int) -> int:
        h = self._t("hmtx")
        i = min(g, self.num_hmetrics - 1)
        return struct.unpack(">H", self.data[h + 4 * i:h + 4 * i + 2])[0]

    def _loca(self, g: int) -> tuple[int, int]:
        b = self._t("loca")
        if self.long_loca:
            a, e = struct.unpack(">II", self.data[b + 4 * g:b + 4 * g + 8])
        else:
            a, e = (x * 2 for x in struct.unpack(">HH", self.data[b + 2 * g:b + 2 * g + 4]))
        return a, e

    def contours(self, g: int, depth: int = 0) -> list[list[tuple[float, float, bool]]]:
        """Outline of a glyph in font units: list of contours, each a list of (x, y, on_curve)."""
        if g in self._cache:
            return self._cache[g]
        if g >= self.num_glyphs or depth > 8:
            return []
        a, e = self._loca(g)
        if e <= a:
            self._cache[g] = []
            return []
        d, p = self.data, self._t("glyf") + a
        nc = struct.unpack(">h", d[p:p + 2])[0]
        out: list[list[tuple[float, float, bool]]] = []
        if nc >= 0:
            ends = struct.unpack(f">{nc}H", d[p + 10:p + 10 + 2 * nc])
            npts = (ends[-1] + 1) if nc else 0
            q = p + 10 + 2 * nc
            ilen = struct.unpack(">H", d[q:q + 2])[0]
            q += 2 + ilen
            flags: list[int] = []
            while len(flags) < npts:
                f = d[q]
                q += 1
                flags.append(f)
                if f & 8:
                    r = d[q]
                    q += 1
                    flags.extend([f] * r)
            flags = flags[:npts]
            xs, ys = [], []
            v = 0
            for f in flags:
                if f & 2:
                    dx = d[q]
                    q += 1
                    v += dx if f & 16 else -dx
                elif not f & 16:
                    v += struct.unpack(">h", d[q:q + 2])[0]
                    q += 2
                xs.append(v)
            v = 0
            for f in flags:
                if f & 4:
                    dy = d[q]
                    q += 1
                    v += dy if f & 32 else -dy
                elif not f & 32:
                    v += struct.unpack(">h", d[q:q + 2])[0]
                    q += 2
                ys.append(v)
            s = 0
            for end in ends:
                out.append([(float(xs[i]), float(ys[i]), bool(flags[i] & 1)) for i in range(s, end + 1)])
                s = end + 1
        else:
            q = p + 10
            while True:
                fl, sub = struct.unpack(">HH", d[q:q + 4])
                q += 4
                if fl & 1:
                    dx, dy = struct.unpack(">hh", d[q:q + 4])
                    q += 4
                else:
                    dx, dy = struct.unpack(">bb", d[q:q + 2])
                    q += 2
                a_, b_, c_, d_ = 1.0, 0.0, 0.0, 1.0
                if fl & 8:
                    a_ = d_ = struct.unpack(">h", d[q:q + 2])[0] / 16384
                    q += 2
                elif fl & 0x40:
                    a_, d_ = (x / 16384 for x in struct.unpack(">hh", d[q:q + 4]))
                    q += 4
                elif fl & 0x80:
                    a_, b_, c_, d_ = (x / 16384 for x in struct.unpack(">hhhh", d[q:q + 8]))
                    q += 8
                if not fl & 2:
                    dx = dy = 0  # point matching (not used by Liberation Sans) - placed at the origin
                for cont in self.contours(sub, depth + 1):
                    out.append([(x * a_ + y * c_ + dx, x * b_ + y * d_ + dy, on) for x, y, on in cont])
                if not fl & 0x20:
                    break
        self._cache[g] = out
        return out


_fonts: dict[bool, Font] = {}
_lock = threading.Lock()


def font(bold: bool = False) -> Font:
    with _lock:
        if bold not in _fonts:
            with open(os.path.join(FONT_DIR, FONTS[bold]), "rb") as fh:
                _fonts[bold] = Font(fh.read())
        return _fonts[bold]


def _flatten(cont, sx, sy, ox, oy):
    """TrueType contour (quadratic) → polygon in dots. sx/sy scale, ox/oy origin (y down)."""
    pts = [(ox + x * sx, oy - y * sy, on) for x, y, on in cont]
    if not pts:
        return []
    # start on an on-curve point (or the midpoint of two off-curve points)
    if not pts[0][2]:
        if pts[-1][2]:
            pts = [pts[-1]] + pts[:-1]
        else:
            m = ((pts[0][0] + pts[-1][0]) / 2, (pts[0][1] + pts[-1][1]) / 2, True)
            pts = [m] + pts
    poly = [(pts[0][0], pts[0][1])]
    n = len(pts)
    i = 1
    cur = pts[0]
    while i <= n:
        p = pts[i % n]
        if p[2]:
            poly.append((p[0], p[1]))
            cur = p
            i += 1
            continue
        nxt = pts[(i + 1) % n]
        end = nxt if nxt[2] else ((p[0] + nxt[0]) / 2, (p[1] + nxt[1]) / 2, True)
        steps = max(2, min(12, int((abs(end[0] - cur[0]) + abs(end[1] - cur[1])) / 2) + 2))
        for k in range(1, steps + 1):
            t = k / steps
            u = 1 - t
            poly.append((u * u * cur[0] + 2 * u * t * p[0] + t * t * end[0], u * u * cur[1] + 2 * u * t * p[1] + t * t * end[1]))
        cur = end
        i += 2 if nxt[2] else 1
    return poly


def fill_polys(bm: Bitmap, polys):
    """Non-zero winding fill: a dot is black when its centre lies inside."""
    edges = []
    ymin, ymax = bm.h, -1
    for poly in polys:
        for i in range(len(poly)):
            x0, y0 = poly[i]
            x1, y1 = poly[(i + 1) % len(poly)]
            if y0 == y1:
                continue
            w = 1 if y1 > y0 else -1
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            edges.append((y0, y1, x0, (x1 - x0) / (y1 - y0), w))
            ymin, ymax = min(ymin, int(y0)), max(ymax, int(y1) + 1)
    for y in range(max(0, ymin), min(bm.h, ymax + 1)):
        cy = y + 0.5
        xs = sorted((x0 + (cy - y0) * k, w) for y0, y1, x0, k, w in edges if y0 <= cy < y1)
        wind = 0
        for j in range(len(xs) - 1):
            wind += xs[j][1]
            if wind:
                bm.fill_span(y, int(xs[j][0] + 0.5), int(xs[j + 1][0] + 0.5))


def text_width(text: str, size_dots: float, bold: bool = False) -> float:
    f = font(bold)
    return sum(f.advance(f.glyph(c)) for c in text) * size_dots / f.units


def draw_text(bm: Bitmap, x: float, baseline_y: float, text: str, size_dots: float, bold: bool = False):
    """Text at x (left) on the baseline y (dots from the top); size = font size in dots."""
    f = font(bold)
    s = size_dots / f.units
    polys = []
    for c in text:
        g = f.glyph(c)
        for cont in f.contours(g):
            p = _flatten(cont, s, s, x, baseline_y)
            if len(p) > 2:
                polys.append(p)
        x += f.advance(g) * s
    fill_polys(bm, polys)
