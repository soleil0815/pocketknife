"""Text der Windows-Meldung bei Neustart/Herunterfahren (shutdown.exe /c …) – einstellbar unter
Einstellungen → Zeitpläne & Leistung → „Meldung bei Neustart und Herunterfahren“.

Absender (Standard „POCKET KNIFE“) und je Anlass ein eigener Text mit Platzhaltern:
{absender}, {sekunden}, {minuten}, {benutzer}. Leeres Feld = Standardtext.
"""
import re

from . import db

SENDER_DEFAULT = "POCKET KNIFE"
DEFAULTS = {
    "restart": "{absender}: Neustart in {sekunden} Sekunden.",
    "shutdown": "{absender}: Herunterfahren in {sekunden} Sekunden.",
    "deploy": "{absender}: Neustart nach Softwareverteilung in {minuten} Minuten.",
    "step": "{absender}: Neustart für die Softwareverteilung",
    "kiosk": "{absender}: Neustart in {minuten} Minuten (angefordert von {benutzer} im Software-Kiosk). Bitte Dateien speichern.",
}
KEYS = {k: f"reboot_msg_{k}" for k in DEFAULTS}
MAX_SENDER = 60
MAX_TEXT = 400  # shutdown.exe /c erlaubt höchstens 512 Zeichen – mit Platzhaltern bleibt Luft


def clean(s, limit):
    """Steuerzeichen raus, keine doppelten Anführungszeichen (die zerlegen das Argument von shutdown.exe)."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(s or "")).replace('"', "'")
    # Backslash am Ende: PowerShell 5.1 setzt das Argument in "…" – aus …\" würde shutdown.exe ein Anführungszeichen lesen
    return re.sub(r"\s{2,}", " ", t).strip()[:limit].rstrip("\\").rstrip()


def sender():
    return clean(db.setting("reboot_sender"), MAX_SENDER) or SENDER_DEFAULT


def render(tpl, sekunden=None, minuten=None, benutzer=None, absender=None):
    vals = {"absender": absender if absender is not None else sender(),
            "sekunden": "" if sekunden is None else str(int(sekunden)),
            "minuten": "" if minuten is None else (str(int(minuten)) if float(minuten).is_integer() else str(minuten).replace(".", ",")),
            "benutzer": benutzer or ""}
    out = re.sub(r"\{(absender|sekunden|minuten|benutzer)\}", lambda m: vals[m.group(1)], str(tpl or ""))
    return clean(out, 500) or clean(re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), ""), DEFAULTS["step"]), 500)


# 2.32.3: Neustart/Herunterfahren mit wählbarer Zeit – der Standardtext nennt dann Minuten bzw. „jetzt“
# (ein eigener Text aus den Einstellungen gilt unverändert)
DELAY_DEFAULTS = {
    "restart": ("{absender}: Neustart jetzt.", "{absender}: Neustart in {minuten} Minuten."),
    "shutdown": ("{absender}: Herunterfahren jetzt.", "{absender}: Herunterfahren in {minuten} Minuten."),
}
MAX_DELAY = 24 * 3600


def text_delay(kind, sekunden):
    """Meldung für Neustart/Herunterfahren aus dem Computer-Menü mit beliebiger Wartezeit (0 … 24 h)."""
    s = int(sekunden)
    if clean(db.setting(KEYS[kind]), MAX_TEXT) or 0 < s < 120:
        return text(kind, sekunden=s)
    return render(DELAY_DEFAULTS[kind][0 if s == 0 else 1], s, round(s / 60, 1))


def text(kind, sekunden=None, minuten=None, benutzer=None):
    """Fertiger Meldungstext für einen Anlass („restart“, „shutdown“, „deploy“, „step“, „kiosk“)."""
    if sekunden is not None and minuten is None:
        minuten = round(sekunden / 60, 1)
    if minuten is not None and sekunden is None:
        sekunden = int(float(minuten) * 60)
    tpl = clean(db.setting(KEYS[kind]), MAX_TEXT) or DEFAULTS[kind]
    return render(tpl, sekunden, minuten, benutzer)
