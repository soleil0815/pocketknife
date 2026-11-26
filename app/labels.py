"""2.58.0: labels with computer name and serial number as PDF - for label printers such as Brother QL.

2.62.0: the size is a setting (one for all labels, default 62 × 29 mm) and the same layout is also drawn as a picture
for printing directly on a label printer (app/labelfont.py, app/labelprint.py).

Format: 62 × 29 mm (Brother QL standard label DK-11209 or 62 mm continuous tape), one page per label. Content: computer
name (large), serial number and model, optionally a barcode (Code 128) with the serial number or the computer name.
Taken over from AssetLotse (same author, AGPL-3.0) - without extra libraries; the PDF uses only the standard fonts
Helvetica/Helvetica-Bold, which every PDF viewer has. The browser opens the PDF; it is printed from there at 100 %.
"""
from __future__ import annotations

from dataclasses import dataclass

MM = 72 / 25.4                   # PDF points per millimeter
LABEL_W_MM, LABEL_H_MM = 62.0, 29.0
MARGIN_MM = 2.5
MARGIN_Y_MM = 3.2   # 2.62.0: top/bottom - die-cut labels are not printable at the edge (Brother DK-11209: 3 mm)
# 2.62.0: label sizes - one for all labels (setting label_size); "custom" = own width × height
SIZES = {
    "62x29": (62.0, 29.0, "62 × 29 mm – Brother DK-11209 / 62-mm-Endlosband"),
    "54x25": (54.0, 25.0, "54 × 25 mm – Dymo 11352"),
    "57x32": (57.0, 32.0, "57 × 32 mm – Dymo 11354"),
    "51x25": (50.8, 25.4, "51 × 25 mm – Zebra 2 × 1 Zoll"),
    "76x25": (76.2, 25.4, "76 × 25 mm – Zebra 3 × 1 Zoll"),
}
MIN_W, MAX_W, MIN_H, MAX_H = 30.0, 102.0, 15.0, 60.0
MIN_MODULE_MM = 0.25   # thinner bars are no longer read reliably by hand scanners
BARCODE_H_MM = 9.0
QUIET_MODULES = 10               # quiet zone left and right (required for reliable scanning)
MAX_MODULE_MM = 0.5
MAX_LABELS = 500
MAX_COPIES = 10
MAX_CODE_B = 20    # longer codes get too fine on 62 mm for 203/300 dpi label printers
MAX_CODE_C = 32    # digits only (even count) need only half the space
BARCODES = {"serial": "Seriennummer", "name": "Computername", "none": "keiner"}
# What BIOS/UEFI report when the manufacturer did not fill in a serial number - not a usable serial
DUMMY_SERIALS = {"", "0", "none", "n/a", "na", "default string", "to be filled by o.e.m.", "to be filled by oem",
                 "system serial number", "chassis serial number", "not specified", "not applicable", "invalid",
                 "0123456789", "1234567890", "123456789", "xxxxxxxxxx", "serial"}

# Code 128 patterns (bar, space, bar, … in module widths) for the values 0–106
_PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232 2331112"
).split()
START_B, START_C, STOP = 104, 105, 106


def code128_values(text: str) -> list[int]:
    """Symbol values including start, check character and stop. Digit-only strings of even length (≥ 4)
    as Code C (twice as compact), otherwise Code B."""
    if len(text) >= 4 and len(text) % 2 == 0 and text.isascii() and text.isdigit():
        values = [START_C] + [int(text[i:i + 2]) for i in range(0, len(text), 2)]
    else:
        if not text or any(not 32 <= ord(c) <= 126 for c in text):
            raise ValueError("Code 128 B only allows printable ASCII characters")
        values = [START_B] + [ord(c) - 32 for c in text]
    check = (values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))) % 103
    return values + [check, STOP]


def code128_modules(text: str) -> list[tuple[int, int]]:
    """Bars as (start module, width in modules), without the quiet zone."""
    bars: list[tuple[int, int]] = []
    pos = 0
    for v in code128_values(text):
        for i, ch in enumerate(_PATTERNS[v]):
            w = int(ch)
            if i % 2 == 0:
                bars.append((pos, w))
            pos += w
    return bars


def module_count(text: str) -> int:
    return sum(sum(int(w) for w in _PATTERNS[v]) for v in code128_values(text))


# ---------------------------------------------------------------- Text (Helvetica, WinAnsi)

# Widths in 1/1000 em for Helvetica-Bold and Helvetica (ASCII 32–126), from the Adobe AFM metrics
_W_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
           556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778,
           722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333,
           278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
           611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_W_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556,
          556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778,
          722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
          278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
          556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


_W_WIDE = {"…": 1000, "—": 1000, "‰": 1000, "™": 1000, "Æ": 1000, "æ": 889, "Œ": 1000, "œ": 944, "–": 556,
           "ß": 611, "·": 278, "€": 556, "°": 400, "„": 500, "“": 500, "”": 500, "‚": 278, "‘": 278, "’": 278}


def printable(text: str) -> str:
    """Prepare text as it will be printed: NFC, characters outside cp1252 without accents where possible
    (Ł → L, ő → o), otherwise "?"."""
    import unicodedata
    out = []
    for c in unicodedata.normalize("NFC", text or ""):
        try:
            c.encode("cp1252")
            out.append(c)
        except UnicodeEncodeError:
            base = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
            base = {"Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Ø": "O", "ø": "o"}.get(c, base)
            try:
                base.encode("cp1252")
                out.append(base or "?")
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _char_width(c: str, table) -> int:
    import unicodedata
    o = ord(c)
    if 32 <= o <= 126:
        return table[o - 32]
    if c in _W_WIDE:
        return _W_WIDE[c]
    base = unicodedata.normalize("NFKD", c)[:1]           # Ä → A, ö → o: width of the base letter
    if base and 32 <= ord(base) <= 126 and base != c:
        return table[ord(base) - 32]
    return 1000                                            # unknown: better estimate too wide


def text_width(text: str, size: float, bold: bool = False) -> float:
    table = _W_BOLD if bold else _W_REG
    return sum(_char_width(c, table) for c in text) * size / 1000


def fit(text: str, size: float, max_w: float, bold: bool = False) -> str:
    """Shorten text to the width (with "…")."""
    text = printable(" ".join((text or "").split()))
    if text_width(text, size, bold) <= max_w:
        return text
    while text and text_width(text + "…", size, bold) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def _pdf_string(text: str) -> str:
    raw = text.encode("cp1252", errors="replace")
    out = []
    for b in raw:
        ch = chr(b)
        if ch in "\\()":
            out.append("\\" + ch)
        elif 32 <= b <= 126:
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")



@dataclass
class Label:
    title: str      # computer name
    serial: str     # serial number (empty: unknown)
    model: str      # manufacturer/model
    code: str       # barcode content ("" = none)
    footer: str     # e.g. "Eigentum der Firma …"


def clean_footer(v) -> str:
    """One line, no control characters, at most 80 characters (fits on the label in small print)."""
    return " ".join("".join(ch if ch >= " " else " " for ch in str(v or "")).split())[:80]


def real_serial(serial) -> str:
    s = " ".join(str(serial or "").split())
    return "" if s.lower() in DUMMY_SERIALS or set(s) <= {"0", " "} else s


def barcode_ok(text: str) -> bool:
    if not text or not all(32 <= ord(c) <= 126 for c in text):
        return False
    numeric = text.isdigit() and len(text) % 2 == 0 and len(text) >= 4
    return len(text) <= (MAX_CODE_C if numeric else MAX_CODE_B)


def label_for(c: dict, barcode: str = "serial", footer: str = "") -> Label:
    """c = computer row (name, serial, manufacturer, model). barcode: serial | name | none. Without a usable serial the
    barcode takes the computer name - a label is never left without a readable code when one was asked for."""
    serial = real_serial(c.get("serial"))
    model = " ".join(p for p in (str(c.get("manufacturer") or "").strip(), str(c.get("model") or "").strip()) if p)
    maker = str(c.get("manufacturer") or "").strip().split(" ")[0].lower()
    if maker and str(c.get("model") or "").strip().lower().split(" ")[0] == maker:
        model = str(c.get("model") or "").strip()   # "Dell Inc. Dell Latitude 5440" → "Dell Latitude 5440"
    name = str(c.get("name") or "")
    code = ""
    if barcode == "serial":
        code = serial if barcode_ok(serial) else (name if barcode_ok(name) else "")
    elif barcode == "name":
        code = name if barcode_ok(name) else ""
    return Label(title=name, serial=serial, model=model, code=code, footer=footer or "")


def parse_size(v) -> tuple[float, float]:
    """Setting label_size: a key of SIZES or "W×H" in mm (also with x). Unknown or out of range → 62 × 29."""
    t = str(v or "").strip().lower().replace("×", "x").replace(",", ".").replace(" ", "")
    if t in SIZES:
        return SIZES[t][0], SIZES[t][1]
    m = __import__("re").fullmatch(r"(\d{2,3}(?:\.\d)?)x(\d{2}(?:\.\d)?)(?:mm)?", t)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
        if MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H and w >= h:
            return w, h
    return LABEL_W_MM, LABEL_H_MM


def clean_size(v) -> str:
    """For saving: a preset key or "W×H"; raises ValueError for sizes the layout cannot handle."""
    t = str(v or "").strip().lower().replace("×", "x").replace(",", ".").replace(" ", "")
    if t in ("", "62x29"):
        return "62x29"
    if t in SIZES:
        return t
    w, h = parse_size(t)
    if (w, h) == (LABEL_W_MM, LABEL_H_MM) and t not in ("62x29", "62x29mm"):
        raise ValueError(f"Etikettengröße: Breite {MIN_W:.0f}–{MAX_W:.0f} mm und Höhe {MIN_H:.0f}–{MAX_H:.0f} mm "
                         "(quer, also Breite ≥ Höhe), z. B. 62x29.")
    return f"{_f(w)}x{_f(h)}"


def size_label(v) -> str:
    t = str(v or "").strip().lower()
    if t in SIZES:
        return SIZES[t][2]
    w, h = parse_size(v)
    return f"{_f(w)} × {_f(h)} mm"


@dataclass
class Text:
    x: float      # points from the left
    y: float      # baseline, points from the bottom
    size: float
    bold: bool
    text: str


@dataclass
class Bars:
    code: str
    left: float   # area for the barcode (points): left edge and width - the barcode is centred in it
    width: float
    y: float      # lower edge of the bars, points from the bottom
    h: float


def layout(label: Label, w_mm: float = LABEL_W_MM, h_mm: float = LABEL_H_MM) -> tuple[float, float, list]:
    """The label as positions in points (origin bottom left) - the PDF and the printer picture draw the same."""
    W, H = w_mm * MM, h_mm * MM
    sc = min(w_mm / LABEL_W_MM, h_mm / LABEL_H_MM)   # 62 × 29 mm = 1
    M, MY = MARGIN_MM * MM, MARGIN_Y_MM * MM
    ops: list = []
    inner = W - 2 * M
    big = not label.code    # without a barcode: name and serial number larger
    size = (15 if big else 11) * sc
    y = (H / 2 + 6 * sc) if big else (H - MY - size * 0.78)  # without a barcode: name and serial in the middle
    ops.append(Text(M, y, size, True, fit(label.title, size, inner, True)))
    sn = "S/N " + (label.serial or "unbekannt")
    ssize = (10 if big else 7.5) * sc
    y -= ssize + (6 if big else 3) * sc
    sn_txt = fit(sn, ssize, inner, True)
    ops.append(Text(M, y, ssize, True, sn_txt))
    if label.model:
        room = inner - text_width(sn_txt, ssize, True) - 6 * sc
        msize = (7 if big else 6) * sc
        if room > 25 * sc:
            mt = fit(label.model, msize, room)
            ops.append(Text(M + inner - text_width(mt, msize), y, msize, False, mt))
    csize, fsize = 6.5 * sc, 5.5 * sc
    if label.code:
        bar_h = min(BARCODE_H_MM * MM * sc, y - ssize * 0.25 - (MY + 8 * sc) - 2 * sc)
        ops.append(Bars(label.code, M, inner, MY + 8 * sc, bar_h))
        code_text = fit(label.code, csize, inner * 0.6)
        cw = text_width(code_text, csize)
        ops.append(Text((W - cw) / 2, MY + 1.2 * sc, csize, False, code_text))
        right = (W + cw) / 2 + 6 * sc
    else:
        right = M
    if label.footer:
        room = (W - M) - right
        foot = fit(label.footer, fsize, room) if room > 20 * sc else ""
        if foot and foot != "…":
            ops.append(Text(W - M - text_width(foot, fsize), MY + 1.2 * sc, fsize, False, foot))
    return W, H, ops


def bar_module(code: str, width: float) -> float:
    """Width of one bar module (points) for a barcode in `width` points incl. quiet zones."""
    return min(MAX_MODULE_MM * MM, width / (module_count(code) + 2 * QUIET_MODULES))


def page_content(label: Label, w_mm: float = LABEL_W_MM, h_mm: float = LABEL_H_MM) -> bytes:
    W, _H, items = layout(label, w_mm, h_mm)
    ops: list[str] = []
    for it in items:
        if isinstance(it, Text):
            ops.append(f"BT /F{2 if it.bold else 1} {_f(it.size)} Tf {_f(it.x)} {_f(it.y)} Td {_pdf_string(it.text)} Tj ET")
        else:
            modules = module_count(it.code) + 2 * QUIET_MODULES
            module = bar_module(it.code, it.width)
            x0 = it.left + (it.width - modules * module) / 2 + QUIET_MODULES * module
            for start, width in code128_modules(it.code):
                ops.append(f"{_f(x0 + start * module)} {_f(it.y)} {_f(width * module)} {_f(it.h)} re")
            ops.append("f")
    return ("0 g\n" + "\n".join(ops) + "\n").encode("latin-1")


def size_check(w_mm: float, h_mm: float) -> str:
    """Hint when the barcode of a typical serial number (12 characters) gets too fine for this size."""
    lab = Label("PC-0001", "ABCD1234EFGH", "", "ABCD1234EFGH", "")
    _W, _H, items = layout(lab, w_mm, h_mm)
    bars = [x for x in items if isinstance(x, Bars)]
    if bars and bar_module(bars[0].code, bars[0].width) / MM < MIN_MODULE_MM:
        return ("Auf dieser Größe wird der Barcode einer langen Seriennummer sehr fein und lässt sich mit manchen "
                "Scannern schlecht lesen – breitere Etiketten sind besser.")
    if bars and bars[0].h < 5 * MM:
        return "Auf dieser Größe wird der Barcode sehr niedrig – höhere Etiketten sind besser."
    return ""


def build_pdf(labels: list[Label], title: str = "Etiketten", w_mm: float = LABEL_W_MM, h_mm: float = LABEL_H_MM) -> bytes:
    """Multi-page PDF, one page (= one label) per entry."""
    if not labels:
        raise ValueError("no labels")
    W, H = w_mm * MM, h_mm * MM
    objects: list[bytes] = []   # object n is at index n-1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")          # filled in below
    pages = add(b"")
    font_reg = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for label in labels:
        stream = page_content(label, w_mm, h_mm)
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream")
        kids.append(add(
            (f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {_f(W)} {_f(H)}] "
             f"/Resources << /Font << /F1 {font_reg} 0 R /F2 {font_bold} 0 R >> >> "
             f"/Contents {content} 0 R >>").encode()))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages} 0 R /ViewerPreferences << /PrintScaling /None >> >>".encode()
    objects[pages - 1] = (f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(f'{k} 0 R' for k in kids)}] >>").encode()
    info = add(f"<< /Title {_pdf_string(title)} /Producer (POCKET KNIFE) >>".encode("latin-1"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)
