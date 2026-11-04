"""Builds app/static/i18n.js from app/i18n/en.json (German -> English) and tools/i18n_runtime.js.
Run after changes to the dictionary: python3 tools/build_i18n.py"""
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
    # Same as at runtime (i18n_runtime.js: tr): leading "- ", "• ", "→ " ... are split off before the lookup and
    # put back in front afterwards - so store the entries without them here as well (otherwise " - einem Scanprofil ..." never matched)
    key = PREFIX.sub("", de).rstrip()
    en = PREFIX.sub("", en).rstrip()
    if en == de and not re.search(r"\{\d+\}", de):
        continue  # identical texts need no dictionary entry - templates do (their placeholders are translated)
    if re.search(r"\{\d+\}", de):
        tpl.append([key, en.strip()])
    else:
        exact[key] = en.strip()
# longer templates first (more specific)
tpl.sort(key=lambda x: -len(re.sub(r"\{\d+\}", "", x[0])))
runtime = open(os.path.join(HERE, "i18n_runtime.js"), encoding="utf-8").read()
data = json.dumps({"exact": exact, "tpl": tpl}, ensure_ascii=False, separators=(",", ":"))
with open(os.path.join(APP, "static", "i18n.js"), "w", encoding="utf-8") as fh:
    fh.write("/* Generated automatically from app/i18n/en.json - do not edit by hand (tools/build_i18n.py) */\n")
    fh.write("const PK_I18N_EN = " + data + ";\n")
    fh.write(runtime)
print(f"i18n.js: {len(exact)} texts, {len(tpl)} templates")
