"""Baut app/static/i18n.js aus app/i18n/en.json (Deutsch -> Englisch) und tools/i18n_runtime.js.
Aufruf nach Änderungen am Wörterbuch: python3 tools/build_i18n.py"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "app")
src = json.load(open(os.path.join(APP, "i18n", "en.json"), encoding="utf-8"))
exact, tpl = {}, []
PREFIX = re.compile(r"^[\s•→✓✕–-]*")
for de, en in src.items():
    if not en:
        continue
    # Wie zur Laufzeit (i18n_runtime.js: tr): führende „– “, „• “, „→ “ … werden vor dem Nachschlagen abgetrennt und
    # danach wieder vorangestellt – also auch hier ohne sie speichern (sonst passte „ – einem Scanprofil …“ nie)
    key = PREFIX.sub("", de).rstrip()
    en = PREFIX.sub("", en).rstrip()
    if en == de and not re.search(r"\{\d+\}", de):
        continue  # gleiche Texte brauchen kein Wörterbuch – Vorlagen schon (ihre Platzhalter werden übersetzt)
    if re.search(r"\{\d+\}", de):
        tpl.append([key, en.strip()])
    else:
        exact[key] = en.strip()
# längere Vorlagen zuerst (spezifischer)
tpl.sort(key=lambda x: -len(re.sub(r"\{\d+\}", "", x[0])))
runtime = open(os.path.join(HERE, "i18n_runtime.js"), encoding="utf-8").read()
data = json.dumps({"exact": exact, "tpl": tpl}, ensure_ascii=False, separators=(",", ":"))
with open(os.path.join(APP, "static", "i18n.js"), "w", encoding="utf-8") as fh:
    fh.write("/* Automatisch erzeugt aus app/i18n/en.json – nicht von Hand bearbeiten (tools/build_i18n.py) */\n")
    fh.write("const PK_I18N_EN = " + data + ";\n")
    fh.write(runtime)
print(f"i18n.js: {len(exact)} Texte, {len(tpl)} Vorlagen")
