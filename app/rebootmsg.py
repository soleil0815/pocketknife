"""Text of the Windows message shown on restart/shutdown (shutdown.exe /c ...) - configurable under
Einstellungen -> Am Client -> "Meldung bei Neustart und Herunterfahren".

Sender (default "POCKET KNIFE") and a separate text per occasion with placeholders:
{absender}, {sekunden}, {minuten}, {benutzer}. An empty field means the default text.
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
MAX_TEXT = 400  # shutdown.exe /c allows at most 512 characters - leaves headroom for placeholders


def clean(s, limit):
    """Strip control characters and double quotes (they break the shutdown.exe argument)."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(s or "")).replace('"', "'")
    # Trailing backslash: PowerShell 5.1 wraps the argument in "..." - with ...\" shutdown.exe would read a quotation mark
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


# 2.32.3: restart/shutdown with a selectable delay - the default text then names minutes or "jetzt" (now)
# (a custom text from the settings applies unchanged)
DELAY_DEFAULTS = {
    "restart": ("{absender}: Neustart jetzt.", "{absender}: Neustart in {minuten} Minuten."),
    "shutdown": ("{absender}: Herunterfahren jetzt.", "{absender}: Herunterfahren in {minuten} Minuten."),
}
MAX_DELAY = 24 * 3600


def text_delay(kind, sekunden):
    """Message for restart/shutdown from the computer menu with an arbitrary delay (0 ... 24 h)."""
    s = int(sekunden)
    if clean(db.setting(KEYS[kind]), MAX_TEXT) or 0 < s < 120:
        return text(kind, sekunden=s)
    return render(DELAY_DEFAULTS[kind][0 if s == 0 else 1], s, round(s / 60, 1))


def text(kind, sekunden=None, minuten=None, benutzer=None):
    """Finished message text for one occasion ("restart", "shutdown", "deploy", "step", "kiosk")."""
    if sekunden is not None and minuten is None:
        minuten = round(sekunden / 60, 1)
    if minuten is not None and sekunden is None:
        sekunden = int(float(minuten) * 60)
    tpl = clean(db.setting(KEYS[kind]), MAX_TEXT) or DEFAULTS[kind]
    return render(tpl, sekunden, minuten, benutzer)
