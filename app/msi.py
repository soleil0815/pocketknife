"""Read MSI properties - without Windows and without a third-party library.

An MSI file is an OLE compound document (Compound File Binary). The table
"Property" (ProductName, ProductVersion, Manufacturer, ProductCode, UpgradeCode ...) and the
SummaryInformation (platform, language) are read from it. That is enough for the package form.
"""
import struct

SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE, END = 0xFFFFFFFF, 0xFFFFFFFE
MAX_STREAM = 64 * 1024 * 1024
# The names of the table streams are encoded in MSI files (6 bits per character, two characters per UTF-16 character)
_MIME = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._"


class MsiError(Exception):
    pass


def _decode_name(name):
    out = []
    for ch in name:
        c = ord(ch)
        if 0x3800 <= c < 0x4800:
            c -= 0x3800
            out.append(_MIME[c & 0x3F])
            out.append(_MIME[(c >> 6) & 0x3F])
        elif 0x4800 <= c < 0x4840:
            out.append(_MIME[c - 0x4800])
        elif c == 0x4840:
            out.append("!")  # marker for tables
        else:
            out.append(ch)
    return "".join(out)


class _CFB:
    def __init__(self, fh):
        self.fh = fh
        hdr = self._read_at(0, 512)
        if hdr[:8] != SIG:
            raise MsiError("Keine MSI-Datei (kein OLE-Verbunddokument).")
        self.ssz = 1 << min(struct.unpack_from("<H", hdr, 0x1E)[0], 16)
        self.mssz = 1 << min(struct.unpack_from("<H", hdr, 0x20)[0], 16)
        if self.ssz not in (512, 4096) or self.mssz > self.ssz:
            raise MsiError("Ungültiger MSI-Dateikopf.")
        n_fat, first_dir = struct.unpack_from("<II", hdr, 0x2C)
        self.cutoff, first_mini, n_mini, first_difat, n_difat = struct.unpack_from("<IIIII", hdr, 0x38)
        difat = list(struct.unpack_from("<109I", hdr, 0x4C))
        sec, guard = first_difat, 0
        while sec not in (FREE, END) and guard < n_difat + 1:
            data = self._sector(sec)
            vals = struct.unpack(f"<{self.ssz // 4}I", data)
            difat += vals[:-1]
            sec, guard = vals[-1], guard + 1
        self.fat = []
        for s in [x for x in difat if x not in (FREE, END)][:n_fat]:
            self.fat += struct.unpack(f"<{self.ssz // 4}I", self._sector(s))
        self.entries = []
        raw = self._chain(first_dir)
        for i in range(len(raw) // 128):
            e = raw[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            typ = e[66]
            if typ == 0:
                continue
            name = e[:max(0, nlen - 2)].decode("utf-16-le", "replace")
            start, size = struct.unpack_from("<IQ", e, 116)
            if self.ssz == 512:
                size &= 0xFFFFFFFF
            self.entries.append({"name": name, "type": typ, "start": start, "size": size})
        root = next((e for e in self.entries if e["type"] == 5), None)
        self.mini_stream = self._chain(root["start"]) if root and root["start"] not in (FREE, END) else b""
        self.minifat = []
        if n_mini and first_mini not in (FREE, END):
            mf = self._chain(first_mini)
            self.minifat = list(struct.unpack(f"<{len(mf) // 4}I", mf))

    def _read_at(self, off, n):
        self.fh.seek(off)
        b = self.fh.read(n)
        if len(b) < n and off == 0:
            raise MsiError("Datei zu kurz.")
        return b

    def _sector(self, n):
        return self._read_at((n + 1) * self.ssz, self.ssz)

    def _chain(self, start, limit=MAX_STREAM):
        out, sec, seen = bytearray(), start, set()
        while sec not in (FREE, END) and sec < len(self.fat) + 1 and sec not in seen:
            seen.add(sec)
            out += self._sector(sec)
            if len(out) > limit:
                raise MsiError("Stream zu groß.")
            sec = self.fat[sec] if sec < len(self.fat) else END
        return bytes(out)

    def stream(self, decoded_name):
        for e in self.entries:
            if e["type"] == 2 and _decode_name(e["name"]) == decoded_name:
                if e["size"] < self.cutoff:
                    out, sec, seen = bytearray(), e["start"], set()
                    while sec not in (FREE, END) and sec < len(self.minifat) and sec not in seen:
                        seen.add(sec)
                        out += self.mini_stream[sec * self.mssz:(sec + 1) * self.mssz]
                        sec = self.minifat[sec]
                    return bytes(out[:e["size"]])
                return self._chain(e["start"])[:e["size"]]
        return None


def _strings(cfb):
    pool = cfb.stream("!_StringPool")
    data = cfb.stream("!_StringData") or b""
    if pool is None:
        raise MsiError("String-Tabelle fehlt.")
    w = struct.unpack(f"<{len(pool) // 2}H", pool[:len(pool) // 2 * 2])
    if len(w) < 2:
        return [""], 2, "cp1252"
    codepage = w[0] | ((w[1] & 0x7FFF) << 16)
    ref_size = 3 if w[1] & 0x8000 else 2
    enc = {0: "cp1252", 65001: "utf-8"}.get(codepage, f"cp{codepage}")
    try:
        "".encode(enc)
    except LookupError:
        enc = "cp1252"
    strings, off, i = [""], 0, 2
    while i + 1 < len(w):
        ln, refs = w[i], w[i + 1]
        if ln == 0 and refs == 0:
            strings.append("")
            i += 2
            continue
        if ln == 0:  # long string: the length is in the next entry
            if i + 3 >= len(w):
                break
            ln = (w[i + 3] << 16) | w[i + 2]
            i += 4
        else:
            i += 2
        strings.append(data[off:off + ln].decode(enc, "replace"))
        off += ln
    return strings, ref_size, enc


def _property_table(cfb):
    strings, rs, _enc = _strings(cfb)
    raw = cfb.stream("!Property")
    if not raw:
        return {}
    rows = len(raw) // (2 * rs)

    def ref(i):
        b = raw[i * rs:(i + 1) * rs]
        return int.from_bytes(b, "little")
    out = {}
    for r in range(rows):
        k, v = ref(r), ref(rows + r)
        if 0 < k < len(strings):
            out[strings[k]] = strings[v] if 0 <= v < len(strings) else ""
    return out


def _summary(cfb):
    raw = cfb.stream("\x05SummaryInformation")
    if not raw or len(raw) < 48:
        return {}
    try:
        off = struct.unpack_from("<I", raw, 44)[0]
        _size, n = struct.unpack_from("<II", raw, off)
        props, cp = {}, "cp1252"
        for i in range(min(n, 64)):
            pid, poff = struct.unpack_from("<II", raw, off + 8 + i * 8)
            vt = struct.unpack_from("<I", raw, off + poff)[0]
            if vt == 0x1E:  # VT_LPSTR
                ln = struct.unpack_from("<I", raw, off + poff + 4)[0]
                props[pid] = raw[off + poff + 8:off + poff + 8 + ln]
            elif vt in (0x02, 0x03):
                props[pid] = struct.unpack_from("<i" if vt == 3 else "<h", raw, off + poff + 4)[0]
        if isinstance(props.get(1), int):
            cp = {1200: "utf-16-le", 65001: "utf-8"}.get(props[1], f"cp{props[1] & 0xFFFF}")
        out = {}
        for pid, key in ((2, "title"), (3, "subject"), (4, "author"), (7, "template"), (9, "package_code")):
            v = props.get(pid)
            if isinstance(v, bytes):
                try:
                    out[key] = v.decode(cp, "replace").rstrip("\x00")
                except LookupError:
                    out[key] = v.decode("cp1252", "replace").rstrip("\x00")
        if isinstance(props.get(14), int):
            out["min_installer"] = props[14]
        return out
    except struct.error:
        return {}


def read(fh):
    """Reads properties from an opened MSI file (file object with seek/read)."""
    cfb = _CFB(fh)
    props = _property_table(cfb)
    summ = _summary(cfb)
    tmpl = summ.get("template", "")
    platform, _, langs = tmpl.partition(";")
    arch = {"x64": "x64", "amd64": "x64", "intel64": "x64", "arm64": "arm64", "intel": "x86", "": "x86"}.get(
        platform.strip().lower(), platform.strip().lower())
    return {
        "name": props.get("ProductName") or summ.get("subject") or "",
        "version": props.get("ProductVersion") or "",
        "manufacturer": props.get("Manufacturer") or summ.get("author") or "",
        "product_code": props.get("ProductCode") or "",
        "upgrade_code": props.get("UpgradeCode") or "",
        "language": props.get("ProductLanguage") or langs,
        "allusers": props.get("ALLUSERS", ""),
        "arch": arch,
        "reboot": props.get("REBOOT", ""),
        "properties": {k: v for k, v in props.items() if k.isupper() and len(v) < 300},
    }
