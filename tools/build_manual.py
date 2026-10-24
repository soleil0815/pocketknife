"""Erzeugt die Handbücher als PDF aus app/static/help/de.md und en.md.

Voraussetzungen (nur auf dem Rechner, der die PDFs baut – nicht im Container): pandoc und Python-Playwright mit Chromium.
Aufruf: python3 tools/build_manual.py
"""
import base64
import datetime
import os
import subprocess
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
HELP = os.path.normpath(os.path.join(HERE, "..", "app", "static", "help"))
VERSION = open(os.path.join(HERE, "..", "VERSION")).read().strip()
LOGO = base64.b64encode(open(os.path.join(HERE, "..", "app", "static", "logo.svg"), "rb").read()).decode()

OUT = {"de": ("POCKET-KNIFE-Handbuch.pdf", "Inhalt", "Version", "Stand", "Seite"),
       "en": ("POCKET-KNIFE-Manual.pdf", "Contents", "Version", "Date", "Page")}

CSS = r"""
@page { size: A4; margin: 20mm 18mm 20mm 18mm; }
html { font-family: Inter, "DejaVu Sans", Arial, sans-serif; font-size: 10pt; color: #1d2330; line-height: 1.5; }
body { margin: 0; }
.cover { height: 240mm; display: flex; flex-direction: column; justify-content: center; page-break-after: always; }
.cover img { width: 64px; height: 64px; margin-bottom: 18px; }
.cover .ctitle { font-size: 30pt; font-weight: 700; margin: 0; letter-spacing: .5px; }
.cover .sub { font-size: 13pt; color: #556; margin-top: 8px; max-width: 140mm; }
.cover .meta { margin-top: 40px; color: #556; font-size: 10pt; }
.cover .bar { width: 60px; height: 4px; background: #d0213a; margin: 22px 0; border-radius: 2px; }
nav#TOC { page-break-after: always; }
nav#TOC h2 { font-size: 18pt; margin: 0 0 12px; }
nav#TOC ul { list-style: none; padding-left: 0; margin: 0; }
nav#TOC { font-size: 9.5pt; line-height: 1.35; }
nav#TOC > ul > li { margin: 5px 0 1px; font-weight: 600; break-inside: avoid; }
nav#TOC ul ul { padding-left: 16px; font-weight: 400; }
nav#TOC ul ul li { margin: 0; }
nav#TOC a { color: #1d2330; text-decoration: none; }
h1 { font-size: 19pt; margin: 0 0 10px; padding-bottom: 6px; border-bottom: 2px solid #d0213a; page-break-before: always; }
h2 { font-size: 13.5pt; margin: 18px 0 6px; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14px 0 4px; page-break-after: avoid; }
p { margin: 6px 0; }
a { color: #b01b31; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 8.8pt; background: #f2f4f7; border: 1px solid #e3e6eb; border-radius: 3px; padding: 0 3px; }
pre { background: #f5f6f8; border: 1px solid #e3e6eb; border-radius: 5px; padding: 8px 10px; font-size: 8.6pt; line-height: 1.4; white-space: pre-wrap; word-break: break-word; page-break-inside: avoid; }
pre code { background: none; border: 0; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 10px; font-size: 9pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #d9dde3; padding: 4px 7px; vertical-align: top; text-align: left; }
th { background: #f0f2f5; }
blockquote { margin: 10px 0; padding: 6px 12px; border-left: 3px solid #d0213a; background: #fbf3f4; border-radius: 0 5px 5px 0; }
blockquote p { margin: 2px 0; }
figure { margin: 10px 0 14px; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 105mm; border: 1px solid #d9dde3; border-radius: 6px; }
figcaption { font-size: 8.5pt; color: #667; margin-top: 3px; }
ul, ol { margin: 4px 0 6px; padding-left: 20px; }
li { margin: 2px 0; }
header#title-block-header { display: none; }
"""


def build(lang):
    md = os.path.join(HELP, f"{lang}.md")
    pdf_name, toc_title, v_lbl, d_lbl, page_lbl = OUT[lang]
    lines = open(md, encoding="utf-8").read().splitlines()
    title = lines[0][2:] if lines and lines[0].startswith("% ") else "POCKET KNIFE"
    sub = lines[1][2:] if len(lines) > 1 and lines[1].startswith("% ") else ""
    html_body = subprocess.run(["pandoc", md, "-f", "markdown", "-t", "html5", "--toc", "--toc-depth=2", "-s",
                                "--metadata", f"toc-title={toc_title}", "--metadata", f"lang={lang}"],
                               capture_output=True, text=True, check=True).stdout
    date = datetime.date.today().strftime("%d.%m.%Y" if lang == "de" else "%Y-%m-%d")
    cover = (f'<section class="cover"><img src="data:image/svg+xml;base64,{LOGO}"><div class="ctitle">{title}</div><div class="bar"></div>'
             f'<div class="sub">{sub}</div><div class="meta">{v_lbl} {VERSION} · {d_lbl} {date}</div></section>')
    html_body = html_body.replace("<style>", "<style>" + CSS + "\n", 1) if "<style>" in html_body else \
        html_body.replace("</head>", f"<style>{CSS}</style></head>", 1)
    html_body = html_body.replace("<body>", "<body>" + cover, 1)
    tmp = os.path.join(HELP, f"_{lang}_print.html")
    open(tmp, "w", encoding="utf-8").write(html_body)
    try:
        with sync_playwright() as p:
            br = p.chromium.launch()
            pg = br.new_page()
            pg.goto("file://" + tmp)
            pg.wait_for_timeout(500)
            footer = (f'<div style="font-size:7.5pt;color:#889;width:100%;padding:0 18mm;display:flex;justify-content:space-between;'
                      f'font-family:Inter,DejaVu Sans,sans-serif"><span>POCKET KNIFE {VERSION}</span>'
                      f'<span>{page_lbl} <span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')
            pg.pdf(path=os.path.join(HELP, pdf_name), format="A4", print_background=True, display_header_footer=True,
                   header_template="<div></div>", footer_template=footer,
                   margin={"top": "18mm", "bottom": "20mm", "left": "18mm", "right": "18mm"})
            br.close()
    finally:
        os.remove(tmp)
    print(pdf_name, os.path.getsize(os.path.join(HELP, pdf_name)) // 1024, "KB")


if __name__ == "__main__":
    for lang in sys.argv[1:] or ["de", "en"]:
        build(lang)
