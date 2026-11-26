"""2.62.0: print labels directly on a label printer - without the print dialog and without extra software.

POCKET KNIFE draws the label itself (app/labelfont.py) and sends the printer's own commands:
- Brother QL (QL-500 … QL-820NWB, 720 dots print head): raster commands from Brother's "Raster Command Reference",
  62 mm wide labels (62 × 29 mm die-cut DK-11209 or 62 mm continuous tape).
- Zebra (ZPL): the label as one graphic (^GFA) - every ZPL printer, 203/300/600 dpi.
- Dymo LabelWriter 4xx (400/450/450 Turbo …, 300 dpi, 672 dots): LabelWriter raster commands. The 550 series is not
  supported (only labels with a chip, no public command description) - it prints through the print dialog.

Two ways to the printer:
- "net": printer in the network - the server sends the data to host:port (raw, usually 9100). Zebra and Brother QL
  with network (QL-810W, QL-820NWB …).
- "usb": printer on a computer (USB). The data is copied to the computer (ADMIN$) and sent as SYSTEM through the
  Windows printer share of the same computer ("copy /b … \\\\computer\\share" - the spooler passes it on unchanged).
  Without a share, POCKET KNIFE shares the printer for this print only (name PK-Etiketten-…) and removes the share
  again afterwards. No .NET, no printing cmdlets - only CIM and cmd (Constrained Language Mode).

What is shown in the label dialog is exactly what runs: the dialog names the printer; there is no silent fallback to
another printer or to the PDF.
"""
from __future__ import annotations

import math
import re
import secrets
import socket
import struct

from . import db, labelfont, labels, pklog, remote, util

LANGS = {"brother": "Brother QL", "zebra": "Zebra (ZPL)", "dymo": "Dymo LabelWriter 4xx"}
KINDS = {"usb": "am Computer (USB)", "net": "im Netzwerk"}
DPIS = {"brother": (300,), "dymo": (300,), "zebra": (203, 300, 600)}
MAX_DIRECT = 200          # labels per direct print (USB: about 30 KB each for Brother)
NET_TIMEOUT = 10
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
PRINTER_RE = re.compile(r"^[^\x00-\x1f\"]{1,200}$")

# Brother QL with 720 dots (90 bytes per row): 62 mm = 696 printable dots, 12 dots from the right edge (mirrored)
QL_BYTES, QL_OFFSET, QL_PRINTABLE = 90, 12, 696
QL_DIECUT = {29: 271}     # height in mm → printable rows (62 × 29 mm: DK-11209)
QL_WIDE = re.compile(r"QL-?1[01]\d\d", re.I)   # QL-1050/1060N/1100/1110 have a 1296 dots head - not supported
DYMO_BYTES = 84           # 672 dots
DYMO_550 = re.compile(r"LabelWriter\s*5\d\d", re.I)


class PrintError(Exception):
    pass


# --- drawing -----------------------------------------------------------------------------------------------------
def render(label: labels.Label, w_mm: float, h_mm: float, dpi: int) -> labelfont.Bitmap:
    """The label (same layout as the PDF) as dots, the top left corner first."""
    W, H, items = labels.layout(label, w_mm, h_mm)
    k = dpi / 72.0
    bm = labelfont.Bitmap(round(w_mm / 25.4 * dpi), round(h_mm / 25.4 * dpi))
    for it in items:
        if isinstance(it, labels.Text):
            labelfont.draw_text(bm, it.x * k, (H - it.y) * k, it.text, it.size * k, it.bold)
        else:
            # whole dots per module - same width for every bar, otherwise scanners stumble
            n = labels.module_count(it.code) + 2 * labels.QUIET_MODULES
            mod = max(1, math.floor(min(labels.MAX_MODULE_MM / 25.4 * dpi, it.width * k / n)))
            x0 = round(it.left * k + (it.width * k - n * mod) / 2) + labels.QUIET_MODULES * mod
            top, bottom = round((H - it.y - it.h) * k), round((H - it.y) * k)
            for start, width in labels.code128_modules(it.code):
                bm.rect(x0 + start * mod, top, x0 + (start + width) * mod, bottom)
    return bm


def module_dots(code: str, w_mm: float, h_mm: float, dpi: int) -> int:
    lab = labels.Label("X", code, "", code, "")
    _W, _H, items = labels.layout(lab, w_mm, h_mm)
    bars = [x for x in items if isinstance(x, labels.Bars)]
    if not bars:
        return 0
    n = labels.module_count(code) + 2 * labels.QUIET_MODULES
    return max(1, math.floor(min(labels.MAX_MODULE_MM / 25.4 * dpi, bars[0].width * dpi / 72.0 / n)))


# --- printer languages ---------------------------------------------------------------------------------------------
def check_size(lang: str, w_mm: float, h_mm: float, model: str = "") -> str:
    """Empty when this printer can print the label size, otherwise the reason (German, for the user)."""
    if lang == "brother":
        if QL_WIDE.search(model or ""):
            return "Brother QL-1050/1060N/1100/1110 (breiter Druckkopf) wird nicht direkt unterstützt – bitte über PDF drucken."
        if abs(w_mm - 62) > 0.6:
            return ("Brother QL druckt direkt nur 62 mm breite Etiketten (62 × 29 mm DK-11209 oder 62-mm-Endlosband) – "
                    f"eingestellt sind {labels._f(w_mm)} × {labels._f(h_mm)} mm.")
    if lang == "dymo":
        if DYMO_550.search(model or ""):
            return "Dymo LabelWriter 550/5XL lassen sich nicht direkt ansprechen – bitte über PDF drucken."
        if h_mm / 25.4 * 300 > DYMO_BYTES * 8:
            return "Das Etikett ist für den Dymo LabelWriter zu hoch (höchstens 56 mm)."
    return ""


def brother(pages: list[labelfont.Bitmap], w_mm: float, h_mm: float) -> bytes:
    """Brother QL raster job: one page per label, cut after every label (if the printer has a cutter)."""
    die = QL_DIECUT.get(round(h_mm)) if abs(h_mm - round(h_mm)) < 0.3 else None
    out = bytearray(b"\x00" * 400 + b"\x1b\x40" + b"\x1b\x69\x61\x01")   # invalidate, initialise, raster mode
    for i, full in enumerate(pages):
        x0 = (full.w - QL_PRINTABLE) // 2
        if die:
            rows = die
            y0 = (full.h - rows) // 2
            media, length, margin = 0x0B, round(h_mm), 0
        else:
            y0, rows = 35, max(1, full.h - 70)     # continuous tape: 35 dots feed margin at the top and bottom
            media, length, margin = 0x0A, 0, 35
        bm = full.crop(x0, y0, QL_PRINTABLE, rows)
        out += b"\x1b\x69\x7a" + bytes([0x8E, media, 62, length]) + struct.pack("<I", rows) + bytes([0 if i == 0 else 1, 0])
        out += b"\x1b\x69\x4d\x40"          # auto cut
        out += b"\x1b\x69\x41\x01"          # cut after every label
        out += b"\x1b\x69\x4b\x08"          # cut at the end
        out += b"\x1b\x69\x64" + struct.pack("<H", margin)
        for y in range(rows):
            out += b"\x67\x00" + bytes([QL_BYTES]) + labelfont.pack_row(bm.row(y), QL_BYTES, QL_OFFSET, mirror=True)
        out += b"\x1a" if i == len(pages) - 1 else b"\x0c"
    return bytes(out)


def zebra(pages: list[labelfont.Bitmap]) -> bytes:
    """ZPL: every label as one graphic field."""
    out = []
    for bm in pages:
        bpr = (bm.w + 7) // 8
        data = b"".join(labelfont.pack_row(bm.row(y), bpr) for y in range(bm.h))
        out.append(f"^XA^PW{bm.w}^LL{bm.h}^LH0,0^FO0,0^GFA,{len(data)},{len(data)},{bpr},{data.hex().upper()}^FS^XZ\n")
    return "".join(out).encode("ascii")


def dymo(pages: list[labelfont.Bitmap]) -> bytes:
    """Dymo LabelWriter 4xx: the label turned by 90° (the short side runs across the print head)."""
    out = bytearray(b"\x1b\x40" + b"\x1b\x44" + bytes([DYMO_BYTES]) + b"\x1b\x68" + b"\x1b\x65")
    for i, full in enumerate(pages):
        bm = full.rotate_left()
        out += b"\x1b\x4c" + struct.pack(">H", bm.h + 6)
        for y in range(bm.h):
            out += b"\x16" + labelfont.pack_row(bm.row(y), DYMO_BYTES)
        out += b"\x1b\x45" if i == len(pages) - 1 else b"\x1b\x47"
    return bytes(out)


def job(printer: dict, items: list[labels.Label], w_mm: float, h_mm: float) -> bytes:
    lang = printer["lang"]
    dpi = int(printer.get("dpi") or DPIS[lang][0])
    why = check_size(lang, w_mm, h_mm, printer.get("model") or printer.get("printer_name") or "")
    if why:
        raise PrintError(why)
    pages = [render(x, w_mm, h_mm, dpi) for x in items]
    if lang == "brother":
        return brother(pages, w_mm, h_mm)
    if lang == "zebra":
        return zebra(pages)
    return dymo(pages)


# --- configuration -------------------------------------------------------------------------------------------------
def clean(data: dict) -> dict:
    """Checks a label printer from the settings dialog. Raises ValueError with a German message."""
    lang = str(data.get("lang") or "")
    kind = str(data.get("kind") or "")
    if lang not in LANGS:
        raise ValueError("Druckertyp: Brother QL, Zebra oder Dymo LabelWriter.")
    if kind not in KINDS:
        raise ValueError("Anschluss: am Computer (USB) oder im Netzwerk.")
    if kind == "net" and lang == "dymo":
        raise ValueError("Dymo LabelWriter lassen sich nur am Computer (USB) direkt ansprechen.")
    try:
        dpi = int(data.get("dpi") or DPIS[lang][0])
    except (TypeError, ValueError):
        dpi = 0
    if dpi not in DPIS[lang]:
        raise ValueError(f"Auflösung für {LANGS[lang]}: {' oder '.join(str(x) for x in DPIS[lang])} dpi.")
    out = {"lang": lang, "kind": kind, "dpi": dpi, "computer_id": None, "printer_name": None, "host": None, "port": None}
    if kind == "usb":
        try:
            cid = int(data.get("computer_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        c = db.q1("SELECT id, name FROM computers WHERE id=?", (cid,))
        if not c:
            raise ValueError("Bitte den Computer wählen, an dem der Drucker angeschlossen ist.")
        pn = str(data.get("printer_name") or "").strip()
        if not PRINTER_RE.match(pn):
            raise ValueError("Bitte den Drucker aus der Liste wählen.")
        out.update(computer_id=cid, printer_name=pn)
        default_name = f"{pn} an {c['name']}"
    else:
        host = str(data.get("host") or "").strip()
        if not HOST_RE.match(host):
            raise ValueError("Adresse: Name oder IP-Adresse des Druckers, z. B. 10.0.10.50.")
        try:
            port = int(data.get("port") or 9100)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            raise ValueError("Port: 1–65535 (meist 9100).")
        out.update(host=host, port=port)
        default_name = f"{LANGS[lang]} {host}"
    name = " ".join(str(data.get("name") or "").split())[:80] or default_name[:80]
    out["name"] = name
    return out


def listing() -> list[dict]:
    rows = db.q("SELECT p.*, c.name AS computer FROM label_printers p LEFT JOIN computers c ON c.id = p.computer_id "
                "ORDER BY lower(p.name)")
    return [{**dict(r), "lang_label": LANGS.get(r["lang"], r["lang"]), "kind_label": KINDS.get(r["kind"], r["kind"])}
            for r in rows]


def local_printers(q: str = "", limit: int = 100) -> list[dict]:
    """Printers from the scans (category printers) - to choose from. Network printers of Windows (\\\\server\\x) are left out."""
    import json
    out = []
    like = util.like_pattern((q or "").strip())
    for r in db.q("SELECT c.id, c.name, i.json FROM inventory_data i JOIN computers c ON c.id = i.computer_id "
                  "WHERE i.category = 'printers' AND lower(c.name) LIKE lower(?) ESCAPE '\\' ORDER BY lower(c.name)",
                  (f"%{like}%",)):
        try:
            items = json.loads(r["json"] or "[]")
        except ValueError:
            continue
        for p in items if isinstance(items, list) else []:
            if not isinstance(p, dict) or p.get("network") or str(p.get("name") or "").startswith("\\\\"):
                continue
            name, drv = str(p.get("name") or ""), str(p.get("driver") or "")
            if not name or not PRINTER_RE.match(name):
                continue
            out.append({"computer_id": r["id"], "computer": r["name"], "printer_name": name, "driver": drv,
                        "port": str(p.get("port") or ""), "lang": guess_lang(name + " " + drv)})
            if len(out) >= limit:
                return out
    return out


def guess_lang(text: str) -> str:
    t = (text or "").lower()
    if re.search(r"\bql-?\d", t) or "brother ql" in t:
        return "brother"
    if "zebra" in t or "zdesigner" in t or re.search(r"\bz[dt]\d{3}", t):
        return "zebra"
    if "labelwriter" in t or "dymo" in t:
        return "dymo"
    return ""


def for_computers(cids: list[int]) -> list[dict]:
    """USB label printers on these computers (the computer the user sits at)."""
    if not cids:
        return []
    return [x for x in listing() if x["kind"] == "usb" and x["computer_id"] in set(cids)]


# --- sending -------------------------------------------------------------------------------------------------------
def send_net(host: str, port: int, data: bytes):
    try:
        with socket.create_connection((host, int(port)), timeout=NET_TIMEOUT) as s:
            s.sendall(data)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    except socket.timeout:
        raise PrintError(f"Der Drucker {host}:{port} antwortet nicht (Zeitüberschreitung).")
    except OSError as e:
        raise PrintError(f"Drucker {host}:{port} nicht erreichbar: {e.strerror or e}")


USB_PS = r"""$ErrorActionPreference = 'Continue'
$pkN = __NAME__
$pkF = Join-Path $PKDir 'pk_label.bin'
$pkP = @(Get-CimInstance Win32_Printer | Where-Object { $_.Name -eq $pkN })
if ($pkP.Count -eq 0) { 'PKLBL:missing'; exit 3 }
$pkP = $pkP[0]
if ($pkP.WorkOffline) { 'PKLBL:offline'; exit 4 }
$pkShare = '' + $pkP.ShareName
$pkTemp = $false
if (-not $pkP.Shared -or -not $pkShare) {
  # shared only for this print - removed again below
  $pkShare = __SHARE__
  Set-CimInstance -InputObject $pkP -Property @{ Shared = $true; ShareName = $pkShare } -ErrorAction SilentlyContinue -ErrorVariable pkShErr
  if ($pkShErr.Count) { 'PKLBL:share:' + $pkShErr[0]; exit 5 }
  $pkTemp = $true
}
$pkBefore = @(Get-CimInstance Win32_PrintJob | Where-Object { ('' + $_.Name).StartsWith($pkN + ', ') } | ForEach-Object { $_.JobId })
$pkOut = & cmd.exe /c ('copy /b "' + $pkF + '" "\\' + $env:COMPUTERNAME + '\' + $pkShare + '"') 2>&1
$pkRc = $LASTEXITCODE
$pkState = 'done'
if ($pkRc -ne 0) { $pkState = 'copy' }
else {
  for ($pkI = 0; $pkI -lt 25; $pkI++) {
    Start-Sleep -Seconds 1
    $pkJobs = @(Get-CimInstance Win32_PrintJob | Where-Object { ('' + $_.Name).StartsWith($pkN + ', ') -and $pkBefore -notcontains $_.JobId })
    if ($pkJobs.Count -eq 0) { $pkState = 'done'; break }
    $pkS = ('' + $pkJobs[0].JobStatus + ' ' + $pkJobs[0].Status).Trim()
    if ($pkS -match 'Error|Offline|Paper|Intervention|Blocked|Fehler') { $pkState = 'error:' + $pkS; break }
    $pkState = 'waiting:' + $pkS
  }
}
if ($pkTemp) {
  $pkP2 = @(Get-CimInstance Win32_Printer | Where-Object { $_.Name -eq $pkN })
  if ($pkP2.Count) { Set-CimInstance -InputObject $pkP2[0] -Property @{ Shared = $false } -ErrorAction SilentlyContinue -ErrorVariable pkUnErr }
  if ($pkUnErr.Count) { 'PKLBL:unshare:' + $pkUnErr[0] }
}
'PKLBL:state:' + $pkState
'PKLBL:copy:' + (($pkOut | ForEach-Object { '' + $_ }) -join ' ')
if ($pkState -eq 'done') { exit 0 } else { exit 6 }
"""


def usb_script(printer_name: str, share: str) -> str:
    return USB_PS.replace("__SHARE__", util.psq(share)).replace("__NAME__", util.psq(printer_name))


def parse_usb(code: int, out: str) -> str:
    """'' = printed; otherwise the German error message."""
    lines: dict[str, str] = {}
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("PKLBL:"):
            k, _, v = line[6:].partition(":")
            lines.setdefault(k, v)
    if "missing" in lines:
        return "Der Drucker ist auf dem Computer nicht (mehr) eingerichtet – Name geändert oder Treiber entfernt?"
    if "offline" in lines:
        return "Der Drucker ist offline (in Windows auf „Drucker offline verwenden“ gestellt)."
    if "share" in lines:
        return "Der Drucker ließ sich für den Druck nicht kurz freigeben: " + lines["share"][:300]
    st = lines.get("state", "")
    if st == "done" and code == 0:
        return ""
    if st == "copy":
        return "Die Druckdaten kamen nicht beim Drucker an: " + (lines.get("copy") or "")[:300]
    if st.startswith("error:"):
        return f"Der Drucker meldet einen Fehler ({st[6:]}) – Etiketten eingelegt, Deckel zu, Drucker an?"
    if st.startswith("waiting"):
        return ("Der Druckauftrag wartet noch in der Windows-Warteschlange" + (f" ({st[8:]})" if st[8:] else "")
                + " – Drucker prüfen; er wird gedruckt, sobald der Drucker bereit ist.")
    return "Keine Rückmeldung vom Computer (Exitcode " + str(code) + ")."


def send_usb(printer: dict, data: bytes):
    from . import presence
    c = db.q1("SELECT id, name, dns_name, addr_problem, addr_detail FROM computers WHERE id=?", (printer["computer_id"],))
    if not c:
        raise PrintError("Der Computer des Etikettendruckers ist nicht mehr in POCKET KNIFE.")
    host = presence.direct_host(c)
    name = f"label_{secrets.token_hex(4)}"
    share = f"PK-Etiketten-{secrets.token_hex(3)}"
    try:
        remote.put_bytes(host, name, "pk_label.bin", data)
        code, out = remote.run_as_system(host, usb_script(printer["printer_name"], share), name, timeout_s=180)
    except remote.RemoteError as e:
        remote.remove_package_copy(host, name)
        raise PrintError(f"{c['name']}: {e}")
    why = parse_usb(code, out)
    if why:
        raise PrintError(why)


def print_labels(printer: dict, items: list[labels.Label], w_mm: float, h_mm: float, user: str = "") -> int:
    """Sends the labels; raises PrintError (German message). Returns the number of labels."""
    if not items:
        raise PrintError("Keine Etiketten.")
    if len(items) > MAX_DIRECT:
        raise PrintError(f"Direkt höchstens {MAX_DIRECT} Etiketten auf einmal – bitte aufteilen oder über PDF drucken.")
    data = job(printer, items, w_mm, h_mm)
    try:
        if printer["kind"] == "net":
            send_net(printer["host"], printer["port"] or 9100, data)
        else:
            send_usb(printer, data)
    except PrintError as e:
        db.ex("UPDATE label_printers SET last_error=?, last_used=? WHERE id=?", (str(e)[:500], util.now(), printer["id"]))
        pklog.warn("system", f"Etiketten auf „{printer['name']}“ nicht gedruckt: {e}", user=user)
        raise
    db.ex("UPDATE label_printers SET last_error=NULL, last_used=? WHERE id=?", (util.now(), printer["id"]))
    return len(items)
