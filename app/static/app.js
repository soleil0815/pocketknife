/* POCKET KNIFE – Weboberfläche (ohne externe Bibliotheken) */
'use strict';

// ---------------------------------------------------------------------------
// Grundfunktionen
// ---------------------------------------------------------------------------
const $ = (s, r = document) => r.querySelector(s);
const SVGNS = 'http://www.w3.org/2000/svg';

function h(tag, attrs, ...kids) {
  const m = /^([a-z0-9]*)((?:[.#][\w-]+)*)$/i.exec(tag);
  const el = document.createElement((m && m[1]) || 'div');
  if (m && m[2]) {
    for (const part of m[2].match(/[.#][\w-]+/g)) {
      if (part[0] === '.') el.classList.add(part.slice(1)); else el.id = part.slice(1);
    }
  }
  if (attrs === null || attrs === undefined || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    kids.unshift(attrs);
  } else {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'class') String(v).split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
      else if (k === 'style') Object.assign(el.style, v);
      else if (k === 'value' || k === 'checked' || k === 'selected' || k === 'disabled' || k === 'indeterminate') el[k] = v;
      else if (v === true) el.setAttribute(k, '');
      else if (k === 'placeholder' || k === 'title' || k === 'aria-label' || k === 'alt') el.setAttribute(k, tr(String(v)));
      else el.setAttribute(k, v);
    }
  }
  append(el, kids);
  return el;
}

// Elemente mit Daten (Datei-/Ordnernamen, Ausgaben, Kennwörter …) nie übersetzen: Klasse .raw oder pre.out
const NO_TR = 'pre.out, .raw, [data-notr]';
function append(el, kids) {
  const raw = el.matches && el.matches(NO_TR);
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false || k === '') continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(typeof k === 'string' && !raw ? tr(k) : String(k)));
  }
  return el;
}

const ICONS = {
  dash: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  pc: 'M3 4h18v12H3zM8 20h8M12 16v4',
  box: 'M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8M12 13v8',
  send: 'M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z',
  mail: 'M3 5h18v14H3zM3 6l9 7 9-7',
  bell: 'M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4',
  rule: 'M4 4h16v4H4zM4 12h10v4H4zM4 20h6M17 14l2 2 4-4',
  report: 'M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h8M9 9h3',
  log: 'M4 6h16M4 12h16M4 18h10',
  gear: 'M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.7 1.1V21a2 2 0 11-4 0v-.1A1.6 1.6 0 007 19.4l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.6 1.6 0 003.1 14H3a2 2 0 110-4h.1A1.6 1.6 0 004.6 7l-.1-.1a2 2 0 112.8-2.8l.1.1A1.6 1.6 0 0010 3.1V3a2 2 0 114 0v.1a1.6 1.6 0 002.7 1.1l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8V9c.3.6.9 1 1.6 1H21a2 2 0 110 4h-.1c-.7 0-1.3.4-1.5 1z',
  apps: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  folder: 'M3 6h6l2 2h10v11H3z',
  ad: 'M12 2l9 5v10l-9 5-9-5V7zM12 22V12M3 7l9 5 9-5',
  clock: 'M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2',
  help: 'M12 22a10 10 0 100-20 10 10 0 000 20zM9.1 9a3 3 0 015.8 1c0 2-3 3-3 3M12 17h.01',
  power: 'M12 2v10M18.4 6.6a9 9 0 11-12.8 0',
  coll: 'M4 6h16M4 12h16M4 18h16',
  stat: 'M5 4h14v16H5zM9 9h6M9 13h6',
  refresh: 'M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5',
  plus: 'M12 5v14M5 12h14',
  play: 'M7 4l13 8-13 8z',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  x: 'M6 6l12 12M18 6L6 18',
  file: 'M6 2h9l5 5v15H6zM14 2v6h6',
  up: 'M12 19V5M5 12l7-7 7 7',
  download: 'M12 3v12M6 11l6 6 6-6M4 21h16',
  print: 'M6 9V3h12v6M6 18H4v-7h16v7h-2M8 14h8v7H8z',
  plug: 'M9 2v6M15 2v6M6 8h12v4a6 6 0 01-12 0zM12 18v4',
  users: 'M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM22 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8',
  user: 'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2M12 11a4 4 0 100-8 4 4 0 000 8z',
  cpu: 'M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4',
  mem: 'M2 7h20v10H2zM6 7v10M10 7v10M14 7v10M18 7v10M4 17v3M20 17v3',
  monitor: 'M2 4h20v13H2zM8 21h8M12 17v4',
  drive: 'M3 13h18v7H3zM5 13l2-9h10l2 9M17 17h.01',
  net: 'M4 9h16v6H4zM8 15v4M16 15v4M12 15v4M7 5h10',
  print: 'M6 9V3h12v6M6 18H4v-7h16v7h-2M8 14h8v7H8z',
  key: 'M15 7a4 4 0 11-3.9 5H3v3h3v3h3v-3h2.1A4 4 0 0115 7zM16 10h.01',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  terminal: 'M4 4h16v16H4zM8 9l3 3-3 3M13 15h4',
  calendar: 'M3 5h18v16H3zM16 3v4M8 3v4M3 10h18',
  chip: 'M7 7h10v10H7zM3 10h4M3 14h4M17 10h4M17 14h4M10 3v4M14 3v4M10 17v4M14 17v4',
  env: 'M4 6h16M4 12h10M4 18h7M17 15l3 3-3 3',
  db: 'M12 3c5 0 8 1.3 8 3s-3 3-8 3-8-1.3-8-3 3-3 8-3zM4 6v12c0 1.7 3 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3 3 8 3s8-1.3 8-3',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  share: 'M18 8a3 3 0 100-6 3 3 0 000 6zM6 15a3 3 0 100-6 3 3 0 000 6zM18 22a3 3 0 100-6 3 3 0 000 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
};

function icon(name) {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.8');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  const p = document.createElementNS(SVGNS, 'path');
  p.setAttribute('d', ICONS[name] || ICONS.file);
  s.appendChild(p);
  return s;
}

// Einzahl für Zähler („1 Eintrag“ statt „1 Einträge“)
const SINGULAR = { Einträge: 'Eintrag', Programme: 'Programm', Pakete: 'Paket', Bereitstellungen: 'Bereitstellung', Dienste: 'Dienst',
  Profile: 'Profil', Prozesse: 'Prozess', Sammlungen: 'Sammlung', Scans: 'Scan', Sicherungen: 'Sicherung', Zeilen: 'Zeile',
  Zeitpläne: 'Zeitplan', Ziele: 'Ziel', Mandanten: 'Mandant', 'aktive Sitzungen': 'aktive Sitzung', 'eigene Scanner': 'eigener Scanner',
  Werte: 'Wert', Ausnahmen: 'Ausnahme', Computer: 'Computer' };
const localDate = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : many}`;

function btn(label, onClick, opt = {}) {
  // Asynchrone Aktionen: Knopf bis zum Ende gesperrt – ein Doppelklick startet nichts doppelt
  const click = (e) => {
    const el = e.currentTarget;
    if (el.dataset.busy) return undefined;
    const r = onClick ? onClick(e) : undefined;
    if (r && typeof r.then === 'function') {
      el.dataset.busy = '1'; el.disabled = true;
      r.catch(() => {}).finally(() => { delete el.dataset.busy; el.disabled = !!opt.disabled; });
    }
    return r;
  };
  return h('button', { class: opt.cls || '', title: opt.title, disabled: opt.disabled, type: 'button', onclick: click },
    opt.icon ? icon(opt.icon) : null, label);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, opt = {}) {
  const o = { method: opt.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (o.method !== 'GET') o.headers['X-PK-Request'] = '1';
  if (opt.body !== undefined) {
    if (opt.raw) { o.body = opt.body; o.headers['Content-Type'] = 'application/octet-stream'; }
    else { o.body = JSON.stringify(opt.body); o.headers['Content-Type'] = 'application/json'; }
  }
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (r.status === 401 && !path.startsWith('/api/login')) {
    // Nur beim ersten 401 die Anmeldung zeigen – parallele Anfragen würden sie sonst neu aufbauen
    // und schon Eingetipptes löschen
    const wasIn = S.me;
    S.me = null;
    if (wasIn) renderLogin(data && typeof data.detail === 'string' && data.detail !== 'Nicht angemeldet' ? data.detail : '');
    throw new Error('Sitzung abgelaufen – bitte neu anmelden.');
  }
  if (!r.ok) {
    let msg = 'Fehler ' + r.status;
    let info = null;
    if (data && data.detail) {
      // detail als Objekt {message, …} (z. B. Repository: benutzt von …, Duplikat): Text + Zusatzangaben
      if (typeof data.detail === 'object' && data.detail.message) { msg = data.detail.message; info = data.detail; }
      else msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    }
    const err = new Error(msg);
    err.status = r.status;
    err.info = info;
    throw err;
  }
  return data;
}
const GET = (p) => api(p);
const POST = (p, b) => api(p, { method: 'POST', body: b || {} });
const PUT = (p, b) => api(p, { method: 'PUT', body: b || {} });
const DEL = (p) => api(p, { method: 'DELETE' });

async function run(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(typeof okMsg === 'function' ? okMsg(r) : okMsg, 'ok');
    return r;
  } catch (e) {
    toast(e.message || String(e), 'err');
    return undefined;
  }
}

function toast(msg, kind = '') {
  const t = h('div.toast', { class: kind }, msg);
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'err' ? 7000 : 3500);
}

// ---------------------------------------------------------------------------
// Formatierung
// ---------------------------------------------------------------------------
function fmtDate(s, withTime = true) {
  if (!s) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(s);
  if (!m) return s;
  return `${m[3]}.${m[2]}.${m[1]}` + (withTime && m[4] ? ` ${m[4]}:${m[5]}` : '');
}
function relTime(s) {
  if (!s) return 'nie';
  const d = new Date(s.replace(' ', 'T'));
  const sec = (Date.now() - d.getTime()) / 1000;
  if (isNaN(sec)) return s;
  if (sec < 60) return 'gerade eben';
  if (sec < 3600) return `vor ${Math.floor(sec / 60)} Min.`;
  if (sec < 86400) return `vor ${Math.floor(sec / 3600)} Std.`;
  return `vor ${Math.floor(sec / 86400)} Tg.`;
}
function fmtBytes(n) {
  if (n === null || n === undefined || n === '') return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0; let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toLocaleString('de-DE', { maximumFractionDigits: v < 10 && i > 0 ? 1 : 0 })} ${u[i]}`;
}
const num = (n) => (n === null || n === undefined ? '' : Number(n).toLocaleString('de-DE'));

// Eigene Scanner: Spalten mit Byte-Angaben erkennen und lesbar anzeigen (KB/MB/GB/TB).
// Automatisch: Spaltenname deutet auf eine Größe hin, alle Werte sind Zahlen und mindestens einer ist ≥ 1024.
const BYTES_NAME = /(size|bytes|length|capacity|größe|groesse|kapazit|speicher|space|^used|^free|frei$|belegt|volumen)/i;
const BYTES_NOT = /(count|anzahl|percent|prozent|pct|%|ratio|quote|anteil|index|nummer|^id$|_id$|mhz|rpm|time|dauer|(kb|mb|gb|tb)$)/i;
function bytesColumns(keys, rows, extra = {}) {
  const mode = extra.bytes_mode || 'auto';
  if (mode === 'off') return new Set();
  if (mode === 'list') {
    const want = String(extra.bytes_cols || '').split(',').map((x) => x.trim().toLowerCase()).filter(Boolean);
    return new Set(keys.filter((k) => want.includes(k.toLowerCase())));
  }
  return new Set(keys.filter((k) => {
    if (!BYTES_NAME.test(k) || BYTES_NOT.test(k)) return false;
    let any = false; let big = false;
    for (const r of rows) {
      const v = r[k];
      if (v === null || v === undefined || v === '') continue;
      if (!/^\d+(\.\d+)?$/.test(String(v).trim())) return false;
      any = true; if (Number(v) >= 1024) big = true;
    }
    return any && big;
  }));
}
// Zelle mit Bytewert: lesbar (GB/MB), genauer Wert als Tooltip
const bytesCell = (k) => (r) => {
  const v = r[k];
  if (v === null || v === undefined || v === '' || isNaN(Number(v))) return v ?? '';
  return h('span', { title: `${num(Math.round(Number(v)))} Bytes` }, fmtBytes(v));
};
// Spalten für die Ergebnis-Tabelle eines eigenen Scanners
function customCols(rows, extra) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 15);
  const bc = bytesColumns(keys, rows, extra);
  return keys.map((k) => (bc.has(k) ? { key: k, label: k, cls: 'right', bytes: true, render: (r) => {
    const v = r[k];
    if (v === null || v === undefined || v === '' || isNaN(Number(v))) return v ?? '';
    return h('span', { title: `${num(Math.round(Number(v)))} Bytes` }, fmtBytes(v));
  } } : { key: k, label: k, cls: 'trunc' }));
}

const DEP_STATUS = {
  queued: ['Wartend', ''], running: ['Läuft', 'info'], success: ['Erfolgreich', 'ok'], failed: ['Fehlgeschlagen', 'err'],
  partial: ['Teilweise', 'warn'], cancelled: ['Abgebrochen', ''], scheduled: ['Geplant', 'accent'], skipped: ['Übersprungen', ''],
};
const depBadge = (s) => h('span.badge', { class: (DEP_STATUS[s] || ['', ''])[1] }, (DEP_STATUS[s] || [s])[0]);
const SOURCE = { manual: 'Manuell', rule: 'Regel', schedule: 'Zeitplan' };

function scanBadge(r) {
  if (r.scanning || r.scan_status === 'Läuft') return h('span.badge.info', 'Scan läuft');
  if (r.scan_status === 'OK') return h('span.badge.ok', 'OK');
  if (r.scan_status === 'Fehler') return h('span.badge.err', { title: r.scan_error || '' }, 'Fehler');
  return h('span.badge', 'Neu');
}

function progress(ok, failed, total) {
  const w = (n) => `${total ? (100 * n) / total : 0}%`;
  const rest = Math.max(0, total - ok - failed);
  return h('div.progress', { title: `${ok} erfolgreich, ${failed} fehlgeschlagen, ${rest} offen` },
    h('span.p-ok', { style: { width: w(ok) } }), h('span.p-err', { style: { width: w(failed) } }),
    h('span.p-run', { style: { width: w(rest) } }));
}

function cmpVal(a, b) {
  if (a === b) return 0;
  if (a === null || a === undefined || a === '') return 1;
  if (b === null || b === undefined || b === '') return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'de', { numeric: true, sensitivity: 'base' });
}

// ---------------------------------------------------------------------------
// Tabelle
// ---------------------------------------------------------------------------
// --- Kontextmenü (Rechtsklick) ---------------------------------------------------
let ctxMenu = null;
function closeCtx() { if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; document.removeEventListener('mousedown', ctxOutside, true); document.removeEventListener('keydown', ctxKey, true); } }
function ctxOutside(e) { if (ctxMenu && !ctxMenu.contains(e.target)) closeCtx(); }
function ctxKey(e) { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); closeCtx(); } }
function contextMenu(x, y, items) {
  closeCtx();
  // Trenner bereinigen: keine doppelten, keine am Anfang oder Ende
  const tidy = (list) => list.filter(Boolean).filter((it, i, a) => !((it === '-' || it.sep) && (i === 0 || a[i - 1] === '-' || a[i - 1].sep || i === a.length - 1)));
  const build = (list) => h('div.cmenu', tidy(tidy(list)).map((it) => {
    if (it === '-' || it.sep) return h('div.cmenu-sep');
    const el = h('div.cmenu-item', { class: (it.disabled ? 'disabled ' : '') + (it.sub ? 'has-sub' : '') },
      h('span.cm-ic', it.icon ? icon(it.icon) : null), h('span.cm-l', it.label), it.hint ? h('span.cm-k', it.hint) : null, it.sub ? h('span.cm-k', '▸') : null);
    if (it.sub) {
      el.addEventListener('mouseenter', () => {
        el.querySelectorAll(':scope > .cmenu').forEach((m) => m.remove());
        const sub = typeof it.sub === 'function' ? it.sub() : it.sub;
        Promise.resolve(sub).then((items2) => {
          if (!el.matches(':hover')) return;
          const m = build(items2 && items2.length ? items2 : [{ label: '(leer)', disabled: true }]);
          m.classList.add('sub');
          el.appendChild(m);
          const r = m.getBoundingClientRect();
          if (r.right > window.innerWidth) { m.style.left = 'auto'; m.style.right = '100%'; }
          if (r.bottom > window.innerHeight) m.style.top = `${Math.max(-r.top + 8, window.innerHeight - r.bottom - 8)}px`;
        });
      });
      el.addEventListener('mouseleave', () => el.querySelectorAll(':scope > .cmenu').forEach((m) => m.remove()));
    } else if (!it.disabled) {
      el.addEventListener('click', (e) => { e.stopPropagation(); closeCtx(); it.onClick && it.onClick(); });
    }
    return el;
  }));
  ctxMenu = build(items);
  ctxMenu.classList.add('root');
  document.body.appendChild(ctxMenu);
  const r = ctxMenu.getBoundingClientRect();
  ctxMenu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - r.width - 4))}px`;
  ctxMenu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - r.height - 4))}px`;
  setTimeout(() => { document.addEventListener('mousedown', ctxOutside, true); document.addEventListener('keydown', ctxKey, true); }, 0);
}

// --- Tabelle: Sortieren, Spalten verschieben/ausblenden, Spaltenfilter -------------
const tblPrefs = {
  get(key) { if (!key) return {}; try { return JSON.parse(localStorage.getItem('pk-tbl-' + key) || '{}'); } catch (e) { return {}; } },
  set(key, v) { if (!key) return; try { localStorage.setItem('pk-tbl-' + key, JSON.stringify(v)); } catch (e) { /* egal */ } },
};
const BLANK = '\u0000(leer)';

function table(cols, rows, opt = {}) {
  // Spaltenreihenfolge, ausgeblendete Spalten und Sortierung je Tabelle merken (Ansicht + Spaltensatz)
  if (opt.key === undefined) opt.key = `${parseHash().parts[0] || ''}|${cols.map((c) => c.key).join(',')}`;
  const pref = tblPrefs.get(opt.key);
  const st = { sort: pref.sort || opt.sort || null, dir: pref.dir || opt.dir || 1, q: opt.liveFilter === false ? '' : (opt.q || '').toLowerCase(), sel: new Set(), limit: opt.pageSize || 500,
    order: Array.isArray(pref.order) ? pref.order : null, hidden: new Set(pref.hidden || cols.filter((c) => c.hidden).map((c) => c.key)),
    widths: pref.widths && typeof pref.widths === 'object' ? pref.widths : {}, filters: {}, anchor: null };
  const idOf = opt.id || ((r) => r.id);
  const val = (c, r) => (c.value ? c.value(r) : r[c.key]);
  const textCache = new Map();
  const ftext = (c, r) => {
    // Wert für Spaltenfilter/Suche: value() > Text der Darstellung > Rohwert
    if (c.value || !c.render) { const v = val(c, r); return v === null || v === undefined ? '' : String(v); }
    let m = textCache.get(r); if (!m) { m = {}; textCache.set(r, m); }
    if (!(c.key in m)) { const v = c.render(r); m[c.key] = v instanceof Node ? v.textContent : String(v ?? ''); }
    return m[c.key];
  };
  const save = () => tblPrefs.set(opt.key, { sort: st.sort, dir: st.dir, order: st.order, hidden: [...st.hidden], widths: st.widths });
  const resetCols = () => { st.order = null; st.widths = {}; st.hidden = new Set(cols.filter((c) => c.hidden).map((c) => c.key)); save(); draw(); };
  // Spalte key vor (after=false) bzw. hinter (after=true) die Spalte target setzen
  const moveCol = (key, target, after) => {
    const keys = ordered().map((k) => k.key).filter((k) => k !== key);
    const i = keys.indexOf(target);
    keys.splice(i < 0 ? keys.length : i + (after ? 1 : 0), 0, key);
    st.order = keys; save(); draw();
  };
  const ordered = () => {
    if (!st.order) return cols;
    const by = new Map(cols.map((c) => [c.key, c]));
    const out = st.order.map((k) => by.get(k)).filter(Boolean);
    cols.forEach((c) => { if (!out.includes(c)) out.push(c); });
    return out;
  };
  const visible = () => ordered().filter((c) => !st.hidden.has(c.key));
  const count = h('span.muted.small');
  const actions = h('div.row');
  const fbadge = h('button.small.ghost', { type: 'button', style: { display: 'none' }, onclick: () => { st.filters = {}; draw(); } }, 'Filter zurücksetzen ✕');
  const search = h('input', {
    type: 'search', placeholder: opt.placeholder || 'Filtern …', value: opt.q || '',
    // opt.liveFilter === false: das Feld filtert nicht beim Tippen (z. B. Datei-Explorer: nur Suche per Enter)
    oninput: (e) => { if (opt.liveFilter === false) return; st.q = e.target.value.toLowerCase(); st.limit = opt.pageSize || 500; draw(); },
    // opt.onSearchEnter: Enter im Filterfeld löst eine weitergehende Suche aus (z. B. Datei-Explorer: alle Unterordner)
    onkeydown: (e) => { if (e.key === 'Enter' && opt.onSearchEnter) { e.preventDefault(); opt.onSearchEnter(e.target.value.trim()); } },
  });
  if (opt.searchStyle) Object.assign(search.style, opt.searchStyle);
  const colBtn = cols.length > 3 && opt.columns !== false ? h('button.small.ghost', { type: 'button', title: 'Spalten ein-/ausblenden, Reihenfolge und Breite', onclick: (e) => columnChooser(e) }, icon('list'), 'Spalten') : null;
  const scroll = h('div.tbl-scroll', { tabindex: opt.selectable ? '0' : null });
  const foot = h('div.tbl-foot');
  const wrap = h('div.tbl-wrap', opt.tools === false ? null : h('div.tbl-tools', opt.search === false ? null : search,
    opt.searchAfter ? opt.searchAfter(() => search.value.trim()) : null, count, fbadge, h('div.spacer'), actions, colBtn), scroll, foot);
  if (opt.fill === false) scroll.style.flex = 'none';

  function passes(c, r) {
    const f = st.filters[c.key];
    if (!f) return true;
    const t = ftext(c, r).trim();
    if (f.values && !f.values.has(t === '' ? BLANK : t)) return false;
    if (f.text && !t.toLowerCase().includes(f.text.toLowerCase())) return false;
    return true;
  }
  function filtered() {
    let r = rows;
    const fcols = cols.filter((c) => st.filters[c.key]);
    if (fcols.length) r = r.filter((x) => fcols.every((c) => passes(c, x)));
    if (st.q) r = r.filter((x) => visible().some((c) => !c.noFilter && ftext(c, x).toLowerCase().includes(st.q)));
    if (st.sort) {
      const c = cols.find((k) => k.key === st.sort);
      if (c) {
        const sv = c.sort || ((x) => val(c, x));
        const empty = (v) => v === null || v === undefined || v === '';
        // Leere Werte immer ans Ende – auch beim absteigenden Sortieren
        r = [...r].sort((a, b) => { const x = sv(a); const y = sv(b); return empty(x) || empty(y) ? cmpVal(x, y) : cmpVal(x, y) * st.dir; });
      }
    }
    return r;
  }
  function selChanged() { if (opt.onSelect) opt.onSelect(api.selected()); }

  function filterMenu(c, anchor) {
    closeCtx();
    // Werte unter Berücksichtigung der übrigen Filter
    const others = cols.filter((k) => k !== c && st.filters[k.key]);
    const base = rows.filter((x) => others.every((k) => passes(k, x)));
    const counts = new Map();
    base.forEach((x) => { const t = ftext(c, x).trim(); const k = t === '' ? BLANK : t; counts.set(k, (counts.get(k) || 0) + 1); });
    const vals = [...counts.keys()].sort((a, b) => (a === BLANK ? -1 : b === BLANK ? 1 : cmpVal(a, b)));
    const cur = st.filters[c.key] || {};
    const chosen = new Set(cur.values || vals);
    const txt = h('input', { type: 'search', placeholder: 'Text enthält …', value: cur.text || '' });
    const q = h('input', { type: 'search', placeholder: 'Werte suchen …' });
    const list = h('div.fm-list');
    const drawList = () => {
      const f = q.value.toLowerCase();
      list.replaceChildren(...vals.filter((v) => !f || v.toLowerCase().includes(f)).slice(0, 800).map((v) => h('label.chk',
        h('input', { type: 'checkbox', checked: chosen.has(v), onchange: (e) => (e.target.checked ? chosen.add(v) : chosen.delete(v)) }),
        h('span', v === BLANK ? '(Leer)' : v), h('span.muted.small', ` ${counts.get(v)}`))));
    };
    q.addEventListener('input', drawList);
    drawList();
    const apply = () => {
      const f = {};
      if (chosen.size !== vals.length) f.values = new Set(chosen);
      if (txt.value.trim()) f.text = txt.value.trim();
      if (f.values || f.text) st.filters[c.key] = f; else delete st.filters[c.key];
      closeCtx(); st.limit = opt.pageSize || 500; draw();
    };
    const box = h('div.cmenu.fmenu', h('div.fm-h', h('b', c.label)),
      h('div.fm-row', btn('Aufsteigend', () => { st.sort = c.key; st.dir = 1; save(); closeCtx(); draw(); }, { cls: 'small' }),
        btn('Absteigend', () => { st.sort = c.key; st.dir = -1; save(); closeCtx(); draw(); }, { cls: 'small' })),
      h('div.fm-row', txt), h('div.fm-row', q),
      h('div.fm-row', btn('Alle', () => { vals.forEach((v) => chosen.add(v)); drawList(); }, { cls: 'small ghost' }),
        btn('Keine', () => { chosen.clear(); drawList(); }, { cls: 'small ghost' }),
        btn('(Leer)', () => { chosen.clear(); chosen.add(BLANK); drawList(); }, { cls: 'small ghost' }),
        btn('(Nicht leer)', () => { vals.forEach((v) => chosen.add(v)); chosen.delete(BLANK); drawList(); }, { cls: 'small ghost' })),
      list,
      h('div.fm-row.end', btn('Filter entfernen', () => { delete st.filters[c.key]; closeCtx(); draw(); }, { cls: 'small' }),
        btn('Spalte ausblenden', () => { st.hidden.add(c.key); save(); closeCtx(); draw(); }, { cls: 'small' }),
        btn('OK', apply, { cls: 'small primary' })));
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter') apply(); });
    ctxMenu = box; box.classList.add('root');
    document.body.appendChild(box);
    const r = anchor.getBoundingClientRect(); const br = box.getBoundingClientRect();
    box.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - br.width - 6))}px`;
    box.style.top = `${Math.min(r.bottom + 2, window.innerHeight - br.height - 6)}px`;
    setTimeout(() => { document.addEventListener('mousedown', ctxOutside, true); document.addEventListener('keydown', ctxKey, true); q.focus(); }, 0);
  }

  // Spalten-Fenster: ein-/ausblenden, Reihenfolge (ziehen oder ▲▼), bleibt für mehrere Änderungen offen
  function columnChooser(e) {
    closeCtx();
    const anchor = e.currentTarget;
    const list = h('div.cc-list');
    let ccDrag = null;
    const drawList = () => {
      const all = ordered();
      list.replaceChildren(...all.map((c, i) => {
        const on = !st.hidden.has(c.key);
        const row = h('div.cc-row', { draggable: 'true', class: on ? '' : 'off' },
          h('span.cc-grip', { title: 'Ziehen zum Verschieben' }, '⠿'),
          h('label.chk', h('input', { type: 'checkbox', checked: on, onchange: (ev) => {
            if (ev.target.checked) st.hidden.delete(c.key); else if (visible().length > 1) st.hidden.add(c.key); else ev.target.checked = true;
            save(); draw(); drawList();
          } }), h('span', c.label || c.chooserLabel || 'Symbol')),
          st.widths[c.key] ? h('span.muted.small', { title: 'Breite von Hand gesetzt – Doppelklick auf den Rand im Spaltenkopf setzt sie zurück' }, `${st.widths[c.key]} px`) : null,
          h('span.spacer'),
          h('button.small.ghost', { type: 'button', title: 'Nach links', disabled: i === 0, onclick: () => { moveCol(c.key, all[i - 1].key, false); drawList(); } }, '▲'),
          h('button.small.ghost', { type: 'button', title: 'Nach rechts', disabled: i === all.length - 1, onclick: () => { moveCol(c.key, all[i + 1].key, true); drawList(); } }, '▼'));
        row.addEventListener('dragstart', (ev) => { ccDrag = c.key; ev.dataTransfer.effectAllowed = 'move'; try { ev.dataTransfer.setData('text/plain', c.key); } catch (x) { /* egal */ } row.classList.add('dragging'); });
        row.addEventListener('dragend', () => { ccDrag = null; row.classList.remove('dragging'); });
        row.addEventListener('dragover', (ev) => {
          if (!ccDrag || ccDrag === c.key) return;
          ev.preventDefault();
          const below = ev.clientY > row.getBoundingClientRect().top + row.offsetHeight / 2;
          row.classList.toggle('drop-b', below); row.classList.toggle('drop-a', !below);
        });
        row.addEventListener('dragleave', () => row.classList.remove('drop-a', 'drop-b'));
        row.addEventListener('drop', (ev) => {
          ev.preventDefault();
          const below = row.classList.contains('drop-b');
          row.classList.remove('drop-a', 'drop-b');
          if (ccDrag && ccDrag !== c.key) { moveCol(ccDrag, c.key, below); drawList(); }
          ccDrag = null;
        });
        return row;
      }));
    };
    drawList();
    const box = h('div.cmenu.fmenu.ccmenu', h('div.fm-h', h('b', 'Spalten'), h('span.muted.small', { style: { marginLeft: '6px' } }, '– ein-/ausblenden, ziehen oder ▲▼ zum Verschieben')),
      list,
      h('div.fm-row.end', btn('Alle einblenden', () => { st.hidden.clear(); save(); draw(); drawList(); }, { cls: 'small ghost' }),
        btn('Zurücksetzen', () => { resetCols(); drawList(); }, { cls: 'small', icon: 'refresh' }),
        btn('Schließen', () => closeCtx(), { cls: 'small primary' })));
    ctxMenu = box; box.classList.add('root');
    document.body.appendChild(box);
    const r = anchor.getBoundingClientRect(); const br = box.getBoundingClientRect();
    box.style.left = `${Math.max(4, Math.min(r.right - br.width, window.innerWidth - br.width - 6))}px`;
    box.style.top = `${Math.max(4, Math.min(r.bottom + 2, window.innerHeight - br.height - 6))}px`;
    setTimeout(() => { document.addEventListener('mousedown', ctxOutside, true); document.addEventListener('keydown', ctxKey, true); }, 0);
  }

  let dragKey = null;
  let resizing = false;
  let resizedAt = 0;
  function draw() {
    const list = filtered();
    const shown = list.slice(0, st.limit);
    const vcols = visible();
    const thead = h('thead');
    const hr = h('tr');
    if (opt.selectable) {
      const all = h('input', {
        type: 'checkbox', checked: list.length > 0 && list.every((r) => st.sel.has(idOf(r))),
        onclick: (e) => { list.forEach((r) => (e.target.checked ? st.sel.add(idOf(r)) : st.sel.delete(idOf(r)))); draw(); selChanged(); },
      });
      hr.appendChild(h('th.chk.nosort', all));
    }
    for (const c of vcols) {
      const arr = st.sort === c.key ? h('span.arr', st.dir > 0 ? '▲' : '▼') : null;
      const fon = !!st.filters[c.key];
      const fb = c.nosort || opt.colFilter === false ? null : h('span.thf', { class: fon ? 'on' : '', title: 'Filtern',
        onclick: (e) => { e.stopPropagation(); filterMenu(c, e.currentTarget.closest('th')); } }, '▾');
      const w = st.widths[c.key];
      const th = h('th', {
        class: (c.nosort ? 'nosort ' : '') + (/\bright\b/.test(c.cls || '') ? 'right ' : '') + (fon ? 'filtered' : ''),
        style: w ? { width: `${w}px`, minWidth: `${w}px`, maxWidth: `${w}px` } : (c.width ? { width: c.width } : undefined),
        draggable: opt.columns === false ? null : 'true', 'data-k': c.key,
        onclick: c.nosort ? null : () => { if (resizedAt && Date.now() - resizedAt < 300) return; if (st.sort === c.key) st.dir = -st.dir; else { st.sort = c.key; st.dir = 1; } save(); draw(); },
      }, h('span.thl', c.label, arr), fb);
      if (opt.columns !== false) {
        // Breite ändern: am rechten Rand des Spaltenkopfs ziehen; Doppelklick = automatische Breite
        const rs = h('span.th-rs', { title: 'Ziehen: Breite ändern · Doppelklick: automatisch' });
        rs.addEventListener('click', (e) => e.stopPropagation());
        rs.addEventListener('dblclick', (e) => { e.stopPropagation(); delete st.widths[c.key]; save(); draw(); });
        rs.addEventListener('mousedown', (e) => {
          e.preventDefault(); e.stopPropagation();
          resizing = true;
          const x0 = e.clientX; const w0 = th.getBoundingClientRect().width;
          const cells = () => scroll.querySelectorAll(`td[data-k="${CSS.escape(c.key)}"]`);
          const apply = (px) => {
            [th, ...cells()].forEach((el) => { el.style.width = `${px}px`; el.style.minWidth = `${px}px`; el.style.maxWidth = `${px}px`; });
          };
          const mv = (ev) => apply(Math.max(40, Math.round(w0 + ev.clientX - x0)));
          const up = (ev) => {
            document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up);
            resizing = false;
            if (Math.abs(ev.clientX - x0) < 2) { apply(w0); if (!st.widths[c.key]) [th, ...cells()].forEach((el) => { el.style.width = el.style.minWidth = el.style.maxWidth = ''; }); return; }  // nur geklickt (z. B. Doppelklick)
            resizedAt = Date.now();
            st.widths[c.key] = Math.max(40, Math.round(w0 + ev.clientX - x0)); save(); draw();
          };
          document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
        });
        th.appendChild(rs);
        th.addEventListener('dragstart', (e) => {
          if (resizing) { e.preventDefault(); return; }
          dragKey = c.key; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', c.key); } catch (x) { /* egal */ } th.classList.add('dragging');
        });
        th.addEventListener('dragend', () => { th.classList.remove('dragging'); dragKey = null; });
        // Linke Hälfte: davor einfügen, rechte Hälfte: dahinter – so lässt sich eine Spalte auch ganz nach rechts ziehen
        th.addEventListener('dragover', (e) => {
          if (!dragKey || dragKey === c.key) return;
          e.preventDefault();
          const r = th.getBoundingClientRect(); const right = e.clientX > r.left + r.width / 2;
          th.classList.toggle('dropto-r', right); th.classList.toggle('dropto', !right);
        });
        th.addEventListener('dragleave', () => th.classList.remove('dropto', 'dropto-r'));
        th.addEventListener('drop', (e) => {
          e.preventDefault();
          const right = th.classList.contains('dropto-r');
          th.classList.remove('dropto', 'dropto-r');
          if (!dragKey || dragKey === c.key) return;
          const k = dragKey; dragKey = null;
          moveCol(k, c.key, right);
        });
      }
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    const tbody = h('tbody');
    shown.forEach((r, idx) => {
      const id = idOf(r);
      const tr = h('tr', { class: (opt.onRow ? 'clickable ' : '') + (st.sel.has(id) ? 'selected' : '') });
      if (opt.selectable) {
        tr.appendChild(h('td.chk', h('input', {
          type: 'checkbox', checked: st.sel.has(id),
          onclick: (e) => { e.stopPropagation(); if (e.target.checked) st.sel.add(id); else st.sel.delete(id); st.anchor = idx; tr.classList.toggle('selected', e.target.checked); selChanged(); updateCount(list); },
        })));
      }
      for (const c of vcols) {
        const v = c.render ? c.render(r) : val(c, r);
        // Rohwerte (ohne render) sind Daten – nicht übersetzen (ein Ordner „Daten“ bleibt „Daten“)
        const cw = st.widths[c.key];
        tr.appendChild(h('td', { class: (c.cls || '') + (cw ? ' sized' : ''), title: c.title ? c.title(r) : (cw ? ftext(c, r) || undefined : undefined), 'data-k': c.key,
          'data-notr': rawCol(c) || undefined, // Daten (Namen, Pfade …) nicht übersetzen – erzeugte Texte schon
          style: cw ? { width: `${cw}px`, minWidth: `${cw}px`, maxWidth: `${cw}px` } : undefined },
          v instanceof Node ? v : (!c.render && v !== null && v !== undefined && v !== '' ? document.createTextNode(String(v)) : (v ?? ''))));
      }
      if (opt.selectable) {
        // Wie im Explorer: Klick wählt aus (Strg = hinzufügen, Umschalt = Bereich), Doppelklick öffnet
        tr.addEventListener('click', (e) => {
          if (e.target.closest('button,a,input,select')) return;
          if (e.shiftKey && st.anchor !== null) {
            const [a, b] = [Math.min(st.anchor, idx), Math.max(st.anchor, idx)];
            if (!e.ctrlKey && !e.metaKey) st.sel.clear();
            shown.slice(a, b + 1).forEach((x) => st.sel.add(idOf(x)));
          } else if (e.ctrlKey || e.metaKey) {
            if (st.sel.has(id)) st.sel.delete(id); else st.sel.add(id);
            st.anchor = idx;
          } else if (opt.onRow && opt.clickOpens) {
            opt.onRow(r, e); return;
          } else {
            st.sel.clear(); st.sel.add(id); st.anchor = idx;
          }
          draw(); selChanged();
        });
        if (opt.onRow) tr.addEventListener('dblclick', (e) => { if (!e.target.closest('button,a,input,select')) opt.onRow(r, e); });
      } else if (opt.onRow) {
        tr.addEventListener('click', (e) => { if (!e.target.closest('button,a,input,select')) opt.onRow(r, e); });
      }
      if (opt.dragRows) {
        tr.draggable = true;
        tr.addEventListener('dragstart', (e) => {
          if (!st.sel.has(id)) { st.sel.clear(); st.sel.add(id); st.anchor = idx; tr.classList.add('selected'); selChanged(); }
          const ids = api.selectedIds();
          try { e.dataTransfer.setData('application/x-pk-computers', JSON.stringify(ids)); e.dataTransfer.setData('text/plain', `${ids.length} Computer`); } catch (x) { /* egal */ }
          e.dataTransfer.effectAllowed = 'copy';
          S.dragComputers = ids;
        });
        tr.addEventListener('dragend', () => { S.dragComputers = null; });
      }
      if (opt.dragData) {
        // Allgemein: ausgewählte Zeilen ziehen (z. B. Repository-Ansicht: auf einen Ordner ablegen)
        tr.draggable = true;
        tr.addEventListener('dragstart', (e) => {
          if (!st.sel.has(id)) { st.sel.clear(); st.sel.add(id); st.anchor = idx; tr.classList.add('selected'); selChanged(); }
          opt.dragData(api.selected(), e);
        });
        if (opt.dragEnd) tr.addEventListener('dragend', (e) => opt.dragEnd(e));
      }
      if (opt.canDrop) {
        tr.addEventListener('dragover', (e) => { if (opt.canDrop(r, e)) { e.preventDefault(); tr.classList.add('dropto'); } });
        tr.addEventListener('dragleave', () => tr.classList.remove('dropto'));
        tr.addEventListener('drop', (e) => { tr.classList.remove('dropto'); if (opt.canDrop(r, e)) { e.preventDefault(); e.stopPropagation(); opt.onDrop(r, e); } });
      }
      if (opt.onContext) {
        tr.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          if (opt.selectable && !st.sel.has(id)) { st.sel.clear(); st.sel.add(id); st.anchor = idx; draw(); selChanged(); }
          opt.onContext(r, e, opt.selectable ? api.selected() : [r]);
        });
      }
      tbody.appendChild(tr);
    });
    const tbl = h('table', thead, tbody);
    scroll.replaceChildren(shown.length ? tbl : h('div', h('table', thead), h('div.empty',
      typeof opt.empty === 'function' ? opt.empty(search.value.trim()) : (opt.empty || 'Keine Einträge'))));
    foot.replaceChildren();
    if (list.length > shown.length) {
      foot.appendChild(h('span', `${shown.length} von ${list.length} angezeigt`));
      foot.appendChild(btn('Weitere anzeigen', () => { st.limit += 1000; draw(); }, { cls: 'small' }));
    }
    foot.style.display = foot.childNodes.length ? '' : 'none';
    const nf = Object.keys(st.filters).length;
    fbadge.style.display = nf ? '' : 'none';
    fbadge.lastChild.textContent = `${nf} Spaltenfilter zurücksetzen ✕`;
    updateCount(list);
  }

  function updateCount(list) {
    const n = [...st.sel].length;
    const noun = opt.noun || 'Einträge';
    const word = list.length === 1 && rows.length === 1 ? (SINGULAR[noun] || noun) : noun;
    count.textContent = `${list.length}${list.length !== rows.length ? ' von ' + rows.length : ''} ${word}` + (n ? ` · ${n} ausgewählt` : '');
  }

  if (opt.selectable) {
    scroll.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); api.selectAll(); }
      if (e.key === 'Delete' && opt.onDelete && api.selected().length) opt.onDelete(api.selected());
      if (e.key === 'Enter' && opt.onRow && api.selected().length === 1) opt.onRow(api.selected()[0], e);
    });
  }

  const api = {
    el: wrap,
    actions,
    selected: () => rows.filter((r) => st.sel.has(idOf(r))),
    selectedIds: () => rows.filter((r) => st.sel.has(idOf(r))).map(idOf),
    clearSelection: () => { st.sel.clear(); draw(); selChanged(); },
    selectAll: () => { filtered().forEach((r) => st.sel.add(idOf(r))); draw(); selChanged(); },
    filteredRows: () => filtered(),
    select: (ids) => {
      st.sel.clear(); ids.forEach((i) => st.sel.add(i)); draw(); selChanged();
      const tr = scroll.querySelector('tr.selected');
      if (tr) requestAnimationFrame(() => tr.scrollIntoView({ block: 'center' }));
    },
    setRows: (r) => {
      rows = r;
      textCache.clear();
      const ids = new Set(rows.map(idOf));
      for (const id of [...st.sel]) if (!ids.has(id)) st.sel.delete(id);
      draw();
    },
    visibleColumns: () => visible(),
    filtered,
    redraw: () => draw(),
  };
  draw();
  return api;
}

// ---------------------------------------------------------------------------
// Dialoge
// ---------------------------------------------------------------------------
function modal(title, body, buttons = [], opt = {}) {
  const root = $('#modal-root');
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    bd.remove();
    document.removeEventListener('keydown', esc);
    if (opt.onClose) opt.onClose();
  };
  const esc = async (e) => {
    if (e.key !== 'Escape' || root.lastChild !== bd || ctxMenu || openMenu) return;
    if (e.target && e.target.closest && e.target.closest('.cmenu, .tmenu')) return;
    if (!opt.sticky) { close(); return; }
    // Editoren (sticky): nicht versehentlich mit Esc alle Eingaben verwerfen
    e.preventDefault();
    if (await confirmDlg('Dialog schließen? Nicht gespeicherte Änderungen gehen verloren.', 'Verwerfen', true)) close();
  };
  const foot = h('div.modal-f');
  for (const b of buttons) {
    const el = btn(b.label, async () => {
      if (b.onClick) {
        el.disabled = true;
        try { const r = await b.onClick(close); if (r !== false && b.close !== false) close(); }
        catch (e) { toast(e.message || String(e), 'err'); }
        finally { el.disabled = false; }
      } else close();
    }, { cls: b.cls, icon: b.icon });
    foot.appendChild(el);
  }
  const m = h('div.modal', { class: (opt.wide ? 'wide ' : '') + (opt.xwide ? 'xwide' : ''), role: 'dialog', 'aria-modal': 'true' },
    h('div.modal-h', h('h2', title), opt.help ? h('button.ghost', { type: 'button', title: 'Hilfe', onclick: () => { close(); go(`#/help?s=${opt.help}`); } }, icon('help')) : null,
      h('button.ghost', { type: 'button', onclick: close, 'aria-label': 'Schließen' }, icon('x'))),
    h('div.modal-b', body), buttons.length ? foot : null);
  const bd = h('div.backdrop', { onmousedown: (e) => { if (e.target === bd && !opt.sticky) close(); } }, m);
  root.appendChild(bd);
  document.addEventListener('keydown', esc);
  const first = m.querySelector('input:not([type=checkbox]),select,textarea');
  if (first) setTimeout(() => first.focus(), 30);
  return { close, el: m };
}

// Vor dem Entfernen nachfragen (Elemente in Editoren – gespeichert wird erst mit „Speichern“)
const askRemove = (text) => confirmDlg(text, 'Entfernen', true);

function closeModals() { document.querySelectorAll('#modal-root .backdrop').forEach((b) => b.remove()); }

function confirmDlg(text, okLabel = 'OK', danger = false) {
  return new Promise((resolve) => {
    let ok = false;
    modal('Bestätigen', h('p', text), [
      { label: 'Abbrechen' },
      { label: okLabel, cls: danger ? 'primary' : 'primary', onClick: () => { ok = true; } },
    ], { onClose: () => resolve(ok) });
  });
}

function showOutput(title, text) {
  modal(title, h('pre.out', text || '(keine Ausgabe)'), [{ label: 'Schließen' }], { wide: true });
}

function field(label, input, hint) {
  return [h('label', label), input, hint ? h('div.hint', hint) : null];
}

// Eingabefeld mit Auswahlliste (statt <datalist>): zeigt beim Öffnen IMMER alle Einträge – der Browser
// filtert eine datalist nach dem schon eingetragenen Wert, man müsste ihn erst löschen. Beim Tippen wird gefiltert.
// source: Liste (Text oder [Wert, Bezeichnung]) oder Funktion(q) → Liste/Promise (remote: Server filtert).
let comboOpen = null;
function combo(input, source, opt = {}) {
  const wrap = h('span.combo', { style: opt.width ? { width: opt.width } : {} });
  // mousedown auf der Liste (auch Bildlaufleiste) nimmt dem Feld nicht den Fokus – sonst schließt sie sofort
  const list = h('div.combo-list', { role: 'listbox', onmousedown: (e) => e.preventDefault() });
  let items = []; let active = -1; let timer = null; let gen = 0;
  const norm = (x) => (Array.isArray(x) ? { v: String(x[0]), l: x[1] } : { v: String(x), l: null });
  async function fetchItems(q) {
    const g = ++gen;
    let r = typeof source === 'function' ? source(q) : source;
    if (r && typeof r.then === 'function') { try { r = await r; } catch (e) { r = []; } }
    if (g !== gen) return null;
    return (r || []).map(norm);
  }
  function place() {
    const b = input.getBoundingClientRect();
    const below = window.innerHeight - b.bottom;
    list.style.minWidth = `${Math.max(b.width, 160)}px`;
    list.style.left = `${Math.min(b.left, window.innerWidth - 180)}px`;
    if (below < 200 && b.top > below) { list.style.top = ''; list.style.bottom = `${window.innerHeight - b.top + 2}px`; list.style.maxHeight = `${Math.min(280, b.top - 10)}px`; }
    else { list.style.bottom = ''; list.style.top = `${b.bottom + 2}px`; list.style.maxHeight = `${Math.min(280, below - 10)}px`; }
  }
  function render(filterText) {
    const q = (filterText || '').toLowerCase();
    const shown = q ? items.filter((x) => x.v.toLowerCase().includes(q) || (x.l && String(x.l).toLowerCase().includes(q))) : items;
    if (!shown.length) { close(); return; }
    active = Math.max(-1, Math.min(active, shown.length - 1));
    list.replaceChildren(...shown.slice(0, 300).map((x, i) => h('div.combo-opt', { class: (i === active ? 'on ' : '') + (x.v === input.value ? 'cur' : ''), role: 'option',
      onmousedown: (e) => e.preventDefault(), onclick: () => pick(x) }, h('span', x.v), x.l && x.l !== x.v ? h('span.muted.small', x.l) : null)));
    list._shown = shown;
    if (!list.isConnected) document.body.appendChild(list);
    place();
    comboOpen = close;
    const cur = list.querySelector('.combo-opt.on') || list.querySelector('.combo-opt.cur');
    if (cur) cur.scrollIntoView({ block: 'nearest' });
  }
  function close() { list.remove(); active = -1; if (comboOpen === close) comboOpen = null; }
  async function open(all = true) {
    if (comboOpen && comboOpen !== close) comboOpen();
    const r = await fetchItems(opt.remote && !all ? input.value : '');
    if (r === null || document.activeElement !== input) return;
    items = r;
    render(all ? '' : input.value);
  }
  function pick(x) {
    input.value = x.v;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    close();
  }
  input.setAttribute('autocomplete', 'off');
  input.addEventListener('focus', () => open(true));
  input.addEventListener('click', () => { if (!list.isConnected) open(true); });
  input.addEventListener('input', (e) => {
    if (!e.isTrusted) return; // eigene Auswahl (pick) nicht erneut filtern
    if (opt.remote) { clearTimeout(timer); timer = setTimeout(() => open(false), 250); } else render(input.value);
  });
  input.addEventListener('blur', () => setTimeout(close, 120));
  input.addEventListener('keydown', (e) => {
    const shown = list._shown || [];
    if (e.key === 'ArrowDown') { e.preventDefault(); if (!list.isConnected) { open(true); return; } active = Math.min(active + 1, shown.length - 1); render(list._q); }
    else if (e.key === 'ArrowUp') { if (!list.isConnected) return; e.preventDefault(); active = Math.max(active - 1, 0); render(list._q); }
    else if (e.key === 'Enter' && list.isConnected && active >= 0 && shown[active]) { e.preventDefault(); pick(shown[active]); }
    else if (e.key === 'Escape' && list.isConnected) { e.preventDefault(); e.stopPropagation(); close(); }
  });
  input.addEventListener('input', () => { list._q = opt.remote ? '' : input.value; });
  const tog = h('button.combo-btn', { type: 'button', tabindex: '-1', title: 'Alle Einträge anzeigen', onmousedown: (e) => e.preventDefault(),
    onclick: () => { if (list.isConnected) close(); else { input.focus(); open(true); } } }, '▾');
  wrap.append(input, tog);
  return wrap;
}
// Beim Scrollen der Seite schließen – aber nicht, wenn die Liste selbst scrollt (Mausrad, Pfeiltasten, Bildlaufleiste)
window.addEventListener('scroll', (e) => { if (comboOpen && !(e.target && e.target.classList && e.target.classList.contains('combo-list'))) comboOpen(); }, true);
window.addEventListener('resize', () => { if (comboOpen) comboOpen(); });

function sel(options, value, attrs = {}) {
  return h('select', attrs, options.map(([v, l]) => h('option', { value: v, selected: String(v) === String(value ?? '') }, l)));
}

function kv(pairs) {
  return h('dl.kv', pairs.filter((p) => p && p[1] !== undefined && p[1] !== null && p[1] !== '').map(([k, v]) => [h('dt', k), h('dd', v)]));
}

// ---------------------------------------------------------------------------
// Zustand, Layout, Navigation
// ---------------------------------------------------------------------------
const S = { me: null, timers: [], collections: null, fields: null };

// Wiederkehrende Aktualisierung der aktuellen Ansicht; Fehler (z. B. gelöschtes Objekt) beenden nicht die Seite
// Wiederholt ausführen – ein noch laufender Durchgang (langsame Antwort) wird nicht überholt
function every(ms, fn) {
  let busy = false;
  S.timers.push(setInterval(async () => {
    if (busy) return;
    busy = true;
    try { await fn(); } catch (e) { /* nächster Versuch */ } finally { busy = false; }
  }, ms));
}
function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }

const NAV = [
  ['dashboard', 'Übersicht', 'dash'],
  ['computers', 'Computer', 'pc'],
  ['software', 'Software', 'apps'],
  null,
  ['packages', 'Pakete', 'box'],
  ['repository', 'Repository', 'folder'],
  ['deployments', 'Bereitstellungen', 'send'],
  ['schedules', 'Zeitpläne', 'clock'],
  ['scanners', 'Scanprofile', 'db'],
  ['tools', 'Tools', 'terminal'],
  ['kiosk', 'Kiosk', 'users'],
  null,
  ['reports', 'Berichte', 'report'],
  ['logs', 'Protokoll', 'log'],
  ['settings', 'Einstellungen', 'gear'],
  ['help', 'Hilfe', 'help'],
];

// OU-Admins (Mitglieder der Gruppe eines Mandanten): sehen nur ihre Computer; diese Bereiche gibt es für sie nicht
// (der Server sperrt sie ohnehin – app/access.py)
const OU_HIDDEN = new Set(['scanners', 'kiosk', 'reports', 'settings']);
const isOU = () => !!(S.me && S.me.scoped);
const mayBuild = () => !isOU() || !!S.me.may_build;

// Webseite des Autors (nur ein Link – es wird nichts aus dem Internet geladen)
const WEBSITE = 'https://helfi.space';
function websiteLink(cls = '') {
  return h('a.raw', { class: cls, href: WEBSITE, target: '_blank', rel: 'noopener noreferrer', title: 'Webseite – öffnet in einem neuen Tab' }, 'helfi.space');
}

// Tabellenspalten: Rohwerte (r[key] ohne eigene Darstellung) sind Daten – ein Ordner „Daten“ bleibt „Daten“.
// Spalten mit value()-Funktion oder Schlüsseln wie …_label/…_text/status/message liefern dagegen Texte von POCKET KNIFE
// (z. B. „Dienstkonto (Deploy-Benutzer)“, „(neueste)“) – die werden übersetzt. c.raw / c.tr erzwingen das eine oder andere.
const LABEL_COL = /(_label|_text|_txt|^text$|status|message|^result$|^state$|^kind$|^type$|^action$|^phase$|^level$|^category$|reason|^what$|^mode$)/;
function rawCol(c) {
  if (c.raw !== undefined) return !!c.raw;
  if (c.tr) return false;
  return !c.render && !c.value && !LABEL_COL.test(c.key);
}

function renderShell() {
  const nav = h('nav.nav',
    h('div.brand', h('img', { src: '/static/logo.svg', alt: '' }), h('div', h('b', 'POCKET KNIFE'), h('small', `Version ${S.me.version || ''}`), h('div#news-pill'))),
    h('ul', NAV.filter((n) => !n || ((!isOU() || !OU_HIDDEN.has(n[0])) && (n[0] !== 'repository' || mayBuild()))).map((n) => (n ? h('li', h('a', { href: `#/${n[0]}`, 'data-nav': n[0] }, icon(n[2]), n[1])) : h('li.sep')))),
    h('div.user', h('b', S.me.display_name || S.me.username), h('span.muted', S.me.username),
      isOU() ? h('span.badge.info', { style: { marginTop: '6px', display: 'block', width: 'fit-content' }, title: tr('Sie sehen und verwalten nur die Computer dieses Mandanten.') },
        tr('OU-Admin: ') + (S.me.tenants || []).map((x) => x.name).join(', ')) : null,
      h('div.row', { style: { gap: '6px', marginTop: '8px' } }, btn('Abmelden', logout, { cls: 'small' }), langSwitch(), themeToggle()),
      websiteLink('nav-web')));
  const main = h('div.main', h('header.topbar#topbar'), h('div#tabs'), h('main.content#content'));
  $('#app').replaceChildren(h('div.shell', nav, main));
  loadNews();
}

// --- Neuigkeiten (2.28): dezent in der Seitenleiste unter der Version -------------------------------------------
const NEWS_KIND = { security: ['err', 'Sicherheit'], new: ['info', 'Neu'], improved: ['ok', 'Verbessert'], fixed: ['', 'Behoben'] };
let NEWS = null;
async function loadNews() {
  try { NEWS = await GET('/api/news'); } catch (e) { NEWS = null; }
  drawNewsPill();
}
function drawNewsPill() {
  const el = $('#news-pill');
  if (!el) return;
  const kids = [];
  if (NEWS && NEWS.unseen) {
    kids.push(h('a.news-pill', { href: '#', class: NEWS.security ? 'sec' : '', title: NEWS.security ? tr('Neuigkeiten – mit Sicherheitsupdates') : tr('Neuigkeiten dieser Version'),
      onclick: (e) => { e.preventDefault(); newsDlg(); } }, h('span.dot'), NEWS.security ? tr('Neu · Sicherheit') : tr('Neu')));
  }
  const up = NEWS && NEWS.update;
  if (up) {
    kids.push(h('a.news-pill.up', { href: '#', class: up.security ? 'sec' : '', title: tr('Neue Version verfügbar – Klick für die Neuigkeiten und den Update-Befehl'),
      onclick: (e) => { e.preventDefault(); newsDlg(true); } }, up.security ? '⚠ ' : '↑ ', fmtT('{0} verfügbar', up.latest)));
  }
  if (!kids.length && NEWS) {
    kids.push(h('a.news-link', { href: '#', onclick: (e) => { e.preventDefault(); newsDlg(); } }, tr('Neuigkeiten')));
  }
  el.replaceChildren(...kids);
}
function newsItems(items) {
  const lang = PK_LANG === 'en' ? 'en' : 'de';
  return h('ul.news-list', items.map((i) => h('li', h('span.badge', { class: (NEWS_KIND[i.kind] || ['', ''])[0] }, tr((NEWS_KIND[i.kind] || ['', i.kind])[1])),
    h('span.raw', i[lang] || i.de))));
}
async function newsDlg(focusUpdate = false) {
  if (!NEWS) await loadNews();
  if (!NEWS) return;
  const up = NEWS.update;
  const parts = [];
  if (up) {
    parts.push(h('div.news-up', { class: up.security ? 'sec' : '' },
      h('div.row', { style: { gap: '8px', alignItems: 'center' } }, h('b', fmtT('Version {0} ist verfügbar', up.latest)),
        up.security ? h('span.badge.err', tr('enthält Sicherheitsupdates')) : null, h('div.spacer'),
        up.url ? h('a', { href: up.url, target: '_blank', rel: 'noopener noreferrer' }, tr('Release auf GitHub')) : null),
      ...up.versions.map((v) => h('div', h('div.news-ver', h('b.raw', v.version), v.date ? h('span.muted.small', ' · ' + fmtDate(v.date, false)) : null), newsItems(v.items))),
      h('div.muted.small', { style: { marginTop: '6px' } }, tr('Update auf dem Docker-Host im Programmordner:')),
      h('div.row', { style: { gap: '6px', alignItems: 'center' } }, h('code.raw', up.command), btn('Kopieren', () => copyText(up.command), { cls: 'small ghost' }))));
  }
  const vers = NEWS.versions || [];
  let shown = 6;
  const list = h('div');
  const draw = () => {
    list.replaceChildren(...vers.slice(0, shown).map((v) => h('div.news-block',
      h('div.news-ver', h('b.raw', v.version), v.date ? h('span.muted.small', ' · ' + fmtDate(v.date, false)) : null,
        v.unseen ? h('span.badge.info', { style: { marginLeft: '8px' } }, tr('neu für Sie')) : null,
        v.version === NEWS.current ? h('span.muted.small', ' · ' + tr('installiert')) : null),
      newsItems(v.items))),
    vers.length > shown ? btn('Ältere Neuigkeiten', () => { shown += 10; draw(); }, { cls: 'small ghost' }) : '');
  };
  draw();
  modal(tr('Neuigkeiten'), h('div.news', ...parts, list, h('p.muted.small', { style: { marginTop: '10px' } },
    tr('Alle Einzelheiten stehen im CHANGELOG und in der Hilfe.'), isOU() ? '' : ' ' + tr('Hinweise auf neue Versionen: Einstellungen → Wartung.'))),
  [{ label: 'Schließen', cls: 'primary' }], { wide: true });
  if (focusUpdate) setTimeout(() => { const u = document.querySelector('.news-up'); if (u) u.scrollIntoView({ block: 'nearest' }); }, 50);
  if (NEWS.unseen) {
    try { await POST('/api/news/seen', { version: NEWS.current }); NEWS.unseen = 0; NEWS.security = false; vers.forEach((v) => { v.unseen = false; }); drawNewsPill(); } catch (e) { /* egal */ }
  }
}

// Einstellungen → Wartung: Hinweis auf neue Versionen (der Server fragt GitHub, nie der Browser)
function updatesCard() {
  const body = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Neuigkeiten und Updates')), body);
  async function draw(d) {
    try { d = d || await GET('/api/system/updates'); } catch (e) { body.replaceChildren(h('p.muted', e.message)); return; }
    const en = h('input', { type: 'checkbox', checked: d.enabled });
    const repo = h('input', { value: d.repo || '', placeholder: 'besitzer/name, z. B. soleil0815/pocketknife', style: { width: '320px' } });
    const tok = h('input', { type: 'password', autocomplete: 'new-password', placeholder: d.token_set ? tr('gespeichert – leer lassen zum Behalten') : tr('nur bei privatem Repository'), style: { width: '320px' } });
    const st = d.status;
    const stTxt = !st ? tr('Noch nicht geprüft.') : st.ok
      ? (NEWS && NEWS.update ? fmtT('Geprüft {0}: Version {1} ist verfügbar.', fmtDate(st.checked), st.latest) : fmtT('Geprüft {0}: aktuell (neueste Version {1}).', fmtDate(st.checked), st.latest))
      : fmtT('Geprüft {0}: {1}', fmtDate(st.checked), st.error || tr('Fehler'));
    body.replaceChildren(
      h('p.muted.small', { style: { marginTop: 0 } }, tr('POCKET KNIFE fragt einmal täglich bei GitHub nach einer neuen Version und zeigt sie Hauptadmins dezent in der Seitenleiste – mit den Neuigkeiten (Sicherheitsupdates hervorgehoben) und dem Update-Befehl. Der Server fragt, nie der Browser; über den Proxyserver, falls eingetragen.')),
      h('div.form',
        field('Prüfen', h('label.chk', en, tr('einmal täglich nach neuen Versionen fragen'))),
        field('Repository', repo, 'GitHub-Repository mit den Releases.'),
        field('Lese-Token', h('div.row', { style: { gap: '6px', alignItems: 'center' } }, tok, d.token_set ? btn('Token löschen', async () => {
          const r = await run(() => PUT('/api/system/updates', { clear_token: true }), 'Token gelöscht'); if (r) draw(r);
        }, { cls: 'small ghost' }) : null), 'Nur bei einem privaten Repository: fein granulares Token mit Lesezugriff auf das Repository („Contents: Read“). Wird verschlüsselt gespeichert und nie angezeigt.'),
        field('Stand', h('span.small', { class: st && !st.ok ? 'err-text' : 'muted' }, stTxt))),
      h('div.row', { style: { gap: '8px', marginTop: '8px' } },
        btn('Speichern', async () => {
          const b = { enabled: en.checked, repo: repo.value.trim() };
          if (tok.value.trim()) b.token = tok.value.trim();
          const r = await run(() => PUT('/api/system/updates', b), 'Gespeichert'); if (r) draw(r);
        }, { cls: 'small primary' }),
        btn('Jetzt prüfen', async () => {
          const r = await run(() => POST('/api/system/updates/check'));
          if (!r) return;
          toast(r.ok ? (r.latest ? fmtT('Neueste Version: {0}', r.latest) : tr('Geprüft')) : (r.error || tr('Fehler')), r.ok ? 'ok' : 'err');
          draw(r.settings); loadNews();
        }, { cls: 'small', icon: 'refresh' })));
  }
  draw();
  return card;
}

function setTop(title, sub, ...actions) {
  const hid = HELP_FOR[parseHash().parts[0]];
  $('#topbar').replaceChildren(h('div.title', h('h1', title), sub ? h('small', sub) : null), h('div.row', actions, hid ? helpButton(hid) : null));
}

function setContent(el, flush = false) {
  const c = $('#content');
  c.className = 'content' + (flush ? ' flush' : '');
  c.replaceChildren(el);
  $('#tabs').replaceChildren();
}

function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [p, qs] = raw.split('?');
  return { parts: (p || 'dashboard').split('/').filter(Boolean), params: new URLSearchParams(qs || '') };
}

async function route() {
  if (!S.me) return;
  const gen = (S.nav = (S.nav || 0) + 1);
  clearTimers();
  closeMenu(); closeCtx();
  const { parts, params } = parseHash();
  const view = (isOU() && OU_HIDDEN.has(parts[0])) ? VIEWS.dashboard : (VIEWS[parts[0]] || VIEWS.dashboard);
  document.querySelectorAll('[data-nav]').forEach((a) => {
    const n = a.getAttribute('data-nav');
    const active = n === parts[0] || (n === 'computers' && parts[0] === 'computer') || (n === 'deployments' && parts[0] === 'deployment');
    a.classList.toggle('active', active);
  });
  try { await view(parts.slice(1), params); }
  catch (e) { if (gen === S.nav) setContent(h('div.empty', 'Fehler: ' + e.message)); }
  if (gen !== S.nav && !S.reroute) {
    // Während des Ladens wurde weiternavigiert: diese (veraltete) Ansicht könnte die neue überschrieben oder
    // ihre Timer hinterlassen haben – die aktuelle Seite einmal neu aufbauen
    S.reroute = true;
    setTimeout(() => { S.reroute = false; route(); }, 0);
  }
}

async function collections(reload = false) {
  if (!S.collections || reload) S.collections = await GET('/api/collections');
  return S.collections;
}
// --- Sammlungen auswählen: Baum wie in der Seitenleiste, alphabetisch, mit Suche ------------------------------------
const deCmp = (a, b) => String(a ?? '').localeCompare(String(b ?? ''), 'de', { numeric: true, sensitivity: 'base' });
// Reihenfolge je Ebene: „Standard“, dann AD, dann eigene Ordner/Sammlungen – jeweils alphabetisch
const collRank = (c) => (c.builtin === 1 ? 0 : c.builtin === 2 || c.ad_kind ? 1 : 2);
const collOrder = (a, b) => collRank(a) - collRank(b) || (a.type === 'folder' ? 0 : 1) - (b.type === 'folder' ? 0 : 1) || deCmp(a.name, b.name);
// Sortierte Auswahlliste [id, „Ordner › Name“] für einfache Auswahlfelder. Die Beschriftung ist schon fertig übersetzt
// (eingebaute Namen wie „Standard“/„Alle Computer“ einzeln, eigene Namen nie) – das Auswahlfeld daher mit data-notr anlegen.
const collNameTr = (name, builtin) => (builtin === 1 ? tr(name) : name);
function collChoiceLabel(c) {
  const pp = (c.pparts || []).map(([n, b]) => collNameTr(n, b));
  return [...pp, c.id === 0 ? tr(c.name) : collNameTr(c.name, c.builtin)].join(' › ');
}
function collChoiceOptions(list) {
  return [...list].map((c) => [c.id, collChoiceLabel(c)])
    .sort((a, b) => (a[0] === 0 ? -1 : b[0] === 0 ? 1 : deCmp(a[1], b[1])));
}

// Mehrfachauswahl von Sammlungen (Zeitpläne, Scanprofile). set: Set der gewählten IDs (0 = gesamter Bestand).
async function collPicker(set, opt = {}) {
  const all = await collections(true);  // frisch: neue Sammlungen (AD-Abgleich, andere Admins) sofort sichtbar
  const byParent = new Map();
  for (const c of all) {
    const k = c.parent_id && all.some((x) => x.id === c.parent_id) ? c.parent_id : 0;
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k).push(c);
  }
  for (const v of byParent.values()) v.sort(collOrder);
  const byId = new Map(all.map((c) => [c.id, c]));
  const ALL = { id: 0, name: tr('Alle Computer (gesamter Bestand)'), type: 'special' };
  if (opt.allowAll !== false) byId.set(0, ALL);
  let openState = {};
  try { const x = JSON.parse(localStorage.getItem('pk-collpick-open') || '{}'); openState = x && typeof x === 'object' && !Array.isArray(x) ? x : {}; } catch (e) { openState = {}; }
  const saveOpen = () => { try { localStorage.setItem('pk-collpick-open', JSON.stringify(openState)); } catch (e) { /* egal */ } };
  const q = h('input', { type: 'search', placeholder: tr('Sammlung suchen …'), oninput: () => draw() });
  const onlySel = h('input', { type: 'checkbox', onchange: () => draw() });
  const count = h('span.muted.small');
  const chips = h('div.pick-chips');
  const list = h('div.pick-list.coll-tree');
  const descend = (id, top = false) => (id === 0 && !top ? [] : byParent.get(id) || []);  // 0 = „gesamter Bestand“ hat keine Kinder
  // Enthält dieser Zweig etwas Gewähltes (für „aufgeklappt“ und Zähler)?
  const selIn = (id) => descend(id).reduce((n, c) => n + (set.has(c.id) ? 1 : 0) + selIn(c.id), 0);
  const toggle = (id, on) => { if (on) set.add(id); else set.delete(id); draw(); if (opt.onchange) opt.onchange(); };
  const row = (c, depth, label) => {
    const kidsOf = descend(c.id);
    const isFolder = c.type === 'folder';
    const open = q.value.trim() || openState[c.id] !== undefined ? (q.value.trim() ? true : openState[c.id]) : selIn(c.id) > 0;
    const tw = h('span.twisty', { onclick: (e) => { e.preventDefault(); e.stopPropagation(); openState[c.id] = !open; saveOpen(); draw(); } },
      kidsOf.length ? (open ? '▾' : '▸') : '');
    const n = selIn(c.id);
    const meta = [c.count !== null && c.count !== undefined && !isFolder ? h('span.muted.small', ` ${c.count}`) : null,
      kidsOf.length && !open && n ? h('span.badge.small', fmtT('{0} ausgewählt', n)) : null];
    const el = isFolder
      ? h('div.coll-folder', { style: { paddingLeft: `${6 + depth * 16}px` }, onclick: () => { openState[c.id] = !open; saveOpen(); draw(); } },
        tw, icon('folder'), h('b', { class: c.builtin === 1 ? '' : 'raw' }, label || c.name), ...meta)
      : h('label' + (set.has(c.id) ? '.on' : ''), { style: { paddingLeft: `${6 + depth * 16}px` } }, tw,
        h('input', { type: 'checkbox', checked: set.has(c.id), onchange: (e) => toggle(c.id, e.target.checked) }),
        h('span', { class: c.builtin === 1 || c.id === 0 ? '' : 'raw' }, label || c.name), ...meta);
    return [el, open ? kidsOf.flatMap((k) => row(k, depth + 1)) : []];
  };
  const dispName = (c) => (c.id === 0 ? c.name : collNameTr(c.name, c.builtin));
  const pathOf = (c) => { const out = []; let p = c.parent_id; const seen = new Set(); while (p && byId.has(p) && !seen.has(p)) { seen.add(p); out.unshift(dispName(byId.get(p))); p = byId.get(p).parent_id; } return out.join(' › '); };
  const draw = () => {
    const term = q.value.trim().toLowerCase();
    // Nicht mehr vorhandene Sammlungen (gelöscht) bleiben gespeichert – als eigener Chip sichtbar und entfernbar
    const sel = [...set].map((id) => byId.get(id) || { id, name: fmtT('Sammlung #{0} (nicht mehr vorhanden)', id), missing: true })
      .sort((a, b) => (a.id === 0 ? -1 : b.id === 0 ? 1 : deCmp(dispName(a), dispName(b))));
    count.textContent = fmtT('{0} ausgewählt', sel.length);
    const dup = new Set(sel.map((c) => c.name.toLowerCase()).filter((n, i, a) => a.indexOf(n) !== i));
    chips.replaceChildren(...(sel.length ? sel.map((c) => h('span.chip', { title: pathOf(c) ? `${pathOf(c)} › ${c.name}` : c.name },
      h('span', { class: c.builtin === 1 || c.id === 0 ? '' : 'raw' }, dup.has(c.name.toLowerCase()) && pathOf(c) ? `${c.name} (${pathOf(c)})` : c.name), h('button', { title: tr('Entfernen'), onclick: (e) => { e.preventDefault(); toggle(c.id, false); } }, '✕')))
      : [h('span.muted.small', tr('Noch keine Sammlung ausgewählt'))]));
    let rows;
    if (term || onlySel.checked) {
      // Suche/„nur ausgewählte“: flache Liste mit Pfad, alphabetisch
      const hits = [...byId.values()].filter((c) => c.type !== 'folder' && (!onlySel.checked || set.has(c.id))
        && (!term || `${pathOf(c)} ${dispName(c)} ${c.name}`.toLowerCase().includes(term)))
        .sort((a, b) => (a.id === 0 ? -1 : b.id === 0 ? 1 : deCmp(dispName(a), dispName(b))));
      rows = hits.length ? hits.map((c) => h('label' + (set.has(c.id) ? '.on' : ''), h('input', { type: 'checkbox', checked: set.has(c.id), onchange: (e) => toggle(c.id, e.target.checked) }),
        h('span', { class: c.builtin === 1 || c.id === 0 ? '' : 'raw' }, c.name), pathOf(c) ? h('span.muted.small.raw', ` – ${pathOf(c)}`) : null,
        c.count !== null && c.count !== undefined ? h('span.muted.small', ` ${c.count}`) : null))
        : [h('div.muted', { style: { padding: '8px 10px' } }, tr('Keine passende Sammlung.'))];
    } else {
      rows = [opt.allowAll !== false ? row(ALL, 0) : [], ...descend(0, true).map((c) => row(c, 0))].flat(Infinity);
    }
    list.replaceChildren(...rows);
  };
  draw();
  return h('div.pick', h('div.pick-tools', q, h('label.chk', onlySel, tr('nur ausgewählte')), count), chips, list);
}

function collectionOptions(list, includeAll = true, onlyType = null) {
  const opts = includeAll ? [['', 'Alle Computer']] : [];
  for (const c of list) {
    if (c.type === 'folder') continue;
    if (onlyType && c.type !== onlyType) continue;
    if (includeAll && c.builtin && c.name === 'Alle Computer') continue;
    opts.push([c.id, c.name]);
  }
  return opts;
}

// ---------------------------------------------------------------------------
// Anmeldung
// ---------------------------------------------------------------------------
// Hell/Dunkel mit einem Klick umschalten (Einstellungen → Wartung bietet zusätzlich „Wie System“)
function themeDark() {
  const v = document.documentElement.getAttribute('data-theme');
  return v ? v === 'dark' : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
}
function themeToggle() {
  const b = h('button.small.theme-btn', { type: 'button' });
  const upd = () => { const d = themeDark(); b.textContent = d ? '☀' : '☾'; b.title = d ? 'Helle Darstellung' : 'Dunkle Darstellung'; };
  b.addEventListener('click', () => { setTheme(themeDark() ? 'light' : 'dark'); upd(); });
  b._upd = upd;
  upd();
  return b;
}

// Trennbalken zum Ändern der Breite eines Seitenbereichs (z. B. Sammlungsbaum); Breite je Bereich gemerkt,
// Doppelklick = Standardbreite
function splitter(el, key, min = 160, max = 640) {
  const k = 'pk-w-' + key;
  try { const w = Number(localStorage.getItem(k)); if (w >= min && w <= max) el.style.width = `${w}px`; } catch (e) { /* egal */ }
  const bar = h('div.splitter', { title: 'Ziehen: Breite ändern · Doppelklick: Standardbreite' });
  bar.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const x0 = e.clientX; const w0 = el.getBoundingClientRect().width;
    bar.classList.add('active'); document.body.classList.add('col-resizing');
    const mv = (ev) => { el.style.width = `${Math.max(min, Math.min(max, Math.round(w0 + ev.clientX - x0)))}px`; };
    const up = () => {
      document.removeEventListener('mousemove', mv); document.removeEventListener('mouseup', up);
      bar.classList.remove('active'); document.body.classList.remove('col-resizing');
      try { localStorage.setItem(k, String(Math.round(el.getBoundingClientRect().width))); } catch (x) { /* egal */ }
    };
    document.addEventListener('mousemove', mv); document.addEventListener('mouseup', up);
  });
  bar.addEventListener('dblclick', () => { el.style.width = ''; try { localStorage.removeItem(k); } catch (x) { /* egal */ } });
  return bar;
}

function langSwitch() {
  return h('div.lang-switch', [['de', 'DE'], ['en', 'EN']].map(([k, l]) => h('button', { type: 'button', class: PK_LANG === k ? 'on' : '',
    title: k === 'de' ? 'Deutsch' : 'English', onclick: () => { if (PK_LANG !== k) setLang(k); } }, l)));
}

function renderLogin(msg = '') {
  clearTimers();
  $('#modal-root').replaceChildren();
  const user = h('input', { autocomplete: 'username', required: true, placeholder: 'benutzer oder DOMÄNE\\benutzer' });
  const pass = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const err = h('div.err', msg);
  const submit = h('button.primary', { type: 'submit' }, 'Anmelden');
  const form = h('form', {
    onsubmit: async (e) => {
      e.preventDefault();
      submit.disabled = true;
      err.textContent = '';
      try {
        S.me = await api('/api/login', { method: 'POST', body: { username: user.value, password: pass.value } });
        S.me = await GET('/api/me');
        IDLE.last = Date.now(); IDLE.sent = Date.now();
        renderShell();
        route();
      } catch (x) { err.textContent = x.message; pass.value = ''; pass.focus(); }
      finally { submit.disabled = false; }
    },
  }, h('label', 'Benutzername'), user, h('label', 'Passwort'), pass, submit, err);
  $('#app').replaceChildren(h('div.login', h('div.box',
    h('div.logo', h('img', { src: '/static/logo.svg', alt: '' }), h('div', h('b', 'POCKET KNIFE'), h('span.muted', 'Anmeldung mit Domänenkonto'))),
    form, h('div.row', { style: { marginTop: '12px', justifyContent: 'space-between' } }, websiteLink('login-web'), langSwitch()))));
  setTimeout(() => user.focus(), 30);
}

async function logout() {
  await run(() => POST('/api/logout'));
  S.me = null;
  renderLogin();
}

// ---------------------------------------------------------------------------
// Ansicht: Übersicht
// ---------------------------------------------------------------------------
async function viewDashboard() {
  setTop('Übersicht', 'Zustand der Umgebung auf einen Blick');
  const d = await GET('/api/dashboard');
  const cols = await collections();
  const byName = (n) => (cols.find((c) => c.builtin && c.name === n) || {}).id;
  const toColl = (n) => () => go(`#/computers?c=${byName(n) || ''}`);
  const tile = (v, l, cls, onClick) => h('div.card.tile', { class: cls, onclick: onClick }, h('div.v', num(v)), h('div.l', l));
  const tiles = h('div.grid.tiles',
    tile(d.computers, 'Computer gesamt', 'accent', toColl('Alle Computer')),
    tile(d.online, 'Online', 'ok', toColl('Online')),
    tile(d.scan_failed, 'Scan fehlgeschlagen', d.scan_failed ? 'err' : '', toColl('Scan fehlgeschlagen')),
    tile(d.never_scanned, 'Nie gescannt', d.never_scanned ? 'warn' : '', toColl('Nie gescannt')),
    tile(d.outdated_computers, `Computer mit veralteter Software (${num(d.outdated_installs)} Installationen)`, d.outdated_computers ? 'warn' : 'ok', toColl('Veraltete Software')),
    tile(d.pending_reboot, 'Neustart ausstehend', d.pending_reboot ? 'warn' : '', toColl('Neustart ausstehend')),
    tile(d.deploy_running, 'Laufende Bereitstellungen', 'info', () => go('#/deployments')),
    tile(d.deploy_failed_7d, 'Fehlgeschlagene Ziele (7 Tage)', d.deploy_failed_7d ? 'err' : '', () => go('#/deployments')),
    // 2.26: nur wenn es welche gibt – Pakete mit Bruch im Repository (Bereitstellungen werden unterbrochen)
    d.packages_broken ? tile(d.packages_broken, 'Pakete mit Bruch im Repository', 'err', () => go('#/packages')) : null);

  const maxOs = Math.max(1, ...d.os.map((o) => o.n));
  const osCard = h('div.card', h('div.card-h', h('h2', 'Betriebssysteme')), h('div.card-b',
    d.os.length ? h('div.bars', d.os.map((o) => h('div.bar', h('div.t', o.os), h('div.n', num(o.n)),
      h('div.track', h('div.fill', { style: { width: `${(100 * o.n) / maxOs}%` } }))))) : h('div.muted', 'Noch keine Daten.')));

  const outCard = h('div.card', h('div.card-h', h('h2', 'Häufigste veraltete Software'), btn('Alle', () => go('#/software?outdated=1'), { cls: 'small' })),
    h('div.card-b.flush', d.top_outdated.length ? h('table', h('tbody', d.top_outdated.map((o) => h('tr',
      h('td.wrap', h('b', o.name), h('div.muted.small', `${o.winget_id} → ${o.winget_latest}`)),
      h('td.right', h('span.badge.warn', `${o.n} PCs`)),
      h('td.right', btn('Aktualisieren', () => updateOutdated(o.winget_id), { cls: 'small' })))))) : h('div.empty', 'Alles aktuell – oder noch kein winget-Index geladen.')));

  const depCard = h('div.card', h('div.card-h', h('h2', 'Letzte Bereitstellungen'), btn('Alle', () => go('#/deployments'), { cls: 'small' })),
    h('div.card-b.flush', d.deployments.length ? h('table', h('tbody', d.deployments.map((x) => h('tr.clickable', { onclick: () => go(`#/deployment/${x.id}`) },
      h('td', h('b', x.name), h('div.muted.small', fmtDate(x.created_at))), h('td', depBadge(x.status)),
      h('td.right.nowrap', `${x.ok}/${x.total}`))))) : h('div.empty', 'Noch keine Bereitstellungen.')));

  const logCard = h('div.card', h('div.card-h', h('h2', 'Warnungen & Fehler'), btn('Protokoll', () => go('#/logs'), { cls: 'small' })),
    h('div.card-b.flush', d.logs.length ? h('table', h('tbody', d.logs.map((l) => h('tr',
      h('td.nowrap.muted.small', fmtDate(l.ts)), h('td', h('span.badge', { class: l.level === 'ERROR' ? 'err' : 'warn' }, l.category)),
      h('td.wrap', l.message))))) : h('div.empty', 'Keine Warnungen.')));

  const w = d.winget;
  const wgCard = h('div.card', h('div.card-h', h('h2', 'winget-Katalog')), h('div.card-b',
    w.loaded ? kv([['Pakete', num(w.count)], ['Stand', fmtDate(w.updated)], ['Format', w.schema]])
      : h('div', h('p.muted', w.error || 'Noch nicht geladen.'), isOU() ? null : btn('Jetzt laden', () => run(() => POST('/api/winget/refresh'), 'Download gestartet')))));

  setContent(h('div.grid', guardBanner(d.guard), tiles, h('div.grid.two', osCard, outCard, depCard, logCard, wgCard)));
  every(30000, () => { if (parseHash().parts[0] === 'dashboard') route(); });
}

async function updateOutdated(wid, computerIds = null) {
  let ids = computerIds;
  if (!ids) {
    const sw = await GET('/api/software');
    const names = sw.filter((s) => s.winget_id === wid).map((s) => s.name);
    ids = [];
    for (const n of names) {
      const det = await GET('/api/software/detail?name=' + encodeURIComponent(n));
      det.computers.filter((c) => c.outdated).forEach((c) => ids.push(c.id));
    }
  }
  ids = [...new Set(ids)];
  if (!ids.length) { toast('Keine Computer mit veralteter Version gefunden.'); return; }
  if (!(await confirmDlg(`${wid} auf ${ids.length} Computer(n) per winget aktualisieren?`, 'Aktualisieren'))) return;
  const r = await run(() => POST('/api/deployments', { winget_id: wid, action: 'upgrade', computer_ids: ids }), 'Bereitstellung gestartet');
  if (r) go(`#/deployment/${r.id}`);
}

// ---------------------------------------------------------------------------
// Ansicht: Computer (Sammlungsbaum + Liste)
// ---------------------------------------------------------------------------
// --- Bausteine für Rechtsklick-Menüs ------------------------------------------------------
async function scanSubItems(getIds) {
  const meta = await GET('/api/scanprofiles');
  return meta.profiles.map((p) => ({ label: p.name + (p.is_default ? ' (Standard)' : ''), icon: 'refresh',
    onClick: async () => { const ids = await getIds(); if (!ids.length) { toast('Keine Computer', 'err'); return; } scanIds(ids, p.id); } }));
}

async function toolSubItems(getTargets) {
  const data = await GET('/api/tools');
  const exec = (fn) => async () => { const t = await getTargets(); if (!t.length) { toast('Keine Computer', 'err'); return; } fn(t); };
  const items = BUILTIN_TOOLS.filter((b) => b.key !== 'scan').map((b) => ({ label: b.name, icon: b.icon, onClick: exec((t) => runBuiltin(b, t)) }));
  const folders = {};
  data.tools.forEach((t) => (folders[t.folder || ''] = folders[t.folder || ''] || []).push(t));
  const toolItem = (t) => ({ label: t.name, icon: t.type === 'url' ? 'net' : 'terminal', hint: t.run_as === 'system' ? 'SYSTEM' : '', onClick: exec((x) => runTool(t, x)) });
  const names = Object.keys(folders).filter(Boolean).sort((a, b) => a.localeCompare(b, 'de'));
  if (names.length || folders['']) items.push('-');
  names.forEach((n) => items.push({ label: n, icon: 'folder', sub: folders[n].map(toolItem) }));
  (folders[''] || []).forEach((t) => items.push(toolItem(t)));
  items.push('-', { label: 'Tools verwalten …', icon: 'gear', onClick: () => go('#/tools') });
  return items;
}

async function reportSubItems(collectionId) {
  const defs = await GET('/api/reports');
  return defs.map((d) => ({ label: d.title, icon: 'report',
    onClick: () => go(`#/reports/${d.key}` + (collectionId && d.params.some((p) => p.type === 'collection') ? `?collection=${collectionId}` : '')) }));
}

// Datei vom Server laden und speichern – Fehler erscheinen als Meldung statt als Fehlerseite im Browser
async function downloadUrl(url) {
  let r;
  try { r = await fetch(url, { credentials: 'same-origin' }); } catch (e) { toast(e.message, 'err'); return; }
  if (!r.ok) {
    let msg = `Fehler ${r.status}`;
    try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (e) { /* kein JSON */ }
    toast(msg, 'err');
    return;
  }
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(r.headers.get('content-disposition') || '');
  const blob = await r.blob();
  const a = h('a', { href: URL.createObjectURL(blob), download: m ? decodeURIComponent(m[1]) : 'download' });
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function downloadText(filename, text, type = 'text/csv;charset=utf-8') {
  const blob = new Blob([text], { type });
  const a = h('a', { href: URL.createObjectURL(blob), download: filename });
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

function tableCsv(t, rows) {
  const cols = t.visibleColumns().filter((c) => c.label && !c.nosort);
  const cell = (c, r) => {
    let v = c.value ? c.value(r) : r[c.key];
    if ((v === null || v === undefined || typeof v === 'object') && c.render) { const x = c.render(r); v = x instanceof Node ? x.textContent : x; }
    v = v === null || v === undefined ? '' : String(v);
    // Schutz vor Formel-Injektion beim Öffnen in Excel (Werte stammen teils von den Clients)
    if (/^[=+\-@\t\r]/.test(v) && !/^[-+]?\d+([.,]\d+)?$/.test(v)) v = `'${v}`;
    return /[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  return '﻿' + [cols.map((c) => c.label).join(';'), ...rows.map((r) => cols.map((c) => cell(c, r)).join(';'))].join('\r\n');
}

async function copyText(text, msg = 'In die Zwischenablage kopiert') {
  try { await navigator.clipboard.writeText(text); toast(msg, 'ok'); } catch (e) { toast('Zwischenablage nicht verfügbar', 'err'); }
}

async function heartbeatIds(ids) {
  if (!ids.length) return;
  const r = await run(() => POST('/api/computers/heartbeat', { ids }), (x) => `Heartbeat: ${x.online} online, ${x.offline} offline`);
  if (r) route();
}

async function memberIdsOf(cid) { return (await GET(`/api/computers?collection=${cid}`)).map((x) => x.id); }

// --- Ansicht: Computer mit Sammlungsbaum ------------------------------------------------------
const COLL_ICON = (c) => (c.ad_kind === 'root' ? 'ad' : c.type === 'folder' ? 'folder' : c.type === 'static' ? 'stat' : 'coll');

async function viewComputers(_parts, params) {
  const cols = await collections(true);
  let cid = params.get('c') ? Number(params.get('c')) : (cols.find((c) => c.builtin === 1 && c.name === 'Alle Computer') || {}).id;
  const current = cols.find((c) => c.id === cid);
  const isAd = (c) => c && c.builtin === 2;
  setTop('Computer', current ? `Sammlung: ${collPath(cols, current.id)}${isAd(current) ? ' (aus dem AD)' : ''}` : '',
    btn('Computer hinzufügen', addComputerDlg, { icon: 'plus' }),
    btn('Neue Sammlung', () => collectionDlg(null), { icon: 'coll' }));

  // Sammlungsbaum ---------------------------------------------------------------------------
  const filterIn = h('input', { type: 'search', placeholder: 'Sammlung suchen …' });
  const treeBody = h('div.tree-body', { tabindex: '0' });
  const tree = h('div.tree', h('div.head', h('h3', 'Sammlungen'),
    h('button.ghost.small', { title: 'Neue Sammlung', onclick: () => collectionDlg(null) }, icon('plus'))), h('div.tree-filter', filterIn), treeBody);
  const byParent = {};
  cols.forEach((c) => (byParent[c.parent_id || 0] = byParent[c.parent_id || 0] || []).push(c));
  const openState = (() => { try { return JSON.parse(localStorage.getItem('pk-tree-open') || '{}'); } catch (e) { return {}; } })();
  const saveOpen = () => { try { localStorage.setItem('pk-tree-open', JSON.stringify(openState)); } catch (e) { /* egal */ } };
  let selId = cid;

  const newIn = (c) => (c && !c.builtin ? c.id : null);
  const paste = async (target) => {
    const clip = S.collClip;
    if (!clip) { toast('Nichts zum Einfügen – zuerst kopieren oder ausschneiden.'); return; }
    const parentId = target && !target.builtin ? target.id : null;
    if (clip.mode === 'cut') {
      if (await run(() => POST(`/api/collections/${clip.id}/move`, { parent_id: parentId }), 'Verschoben') !== undefined) { S.collClip = null; route(); }
    } else {
      const r = await run(() => POST(`/api/collections/${clip.id}/duplicate`, { parent_id: parentId }), 'Eingefügt');
      if (r) go(`#/computers?c=${r.id}`);
    }
  };
  const rename = async (c) => {
    if (c.builtin) { toast('Standard- und AD-Sammlungen können nicht umbenannt werden.', 'err'); return; }
    const inp = h('input', { value: c.name });
    modal('Umbenennen', h('div.form', field('Name', inp)), [{ label: 'Abbrechen' },
      { label: 'Speichern', cls: 'primary', onClick: async () => { await POST(`/api/collections/${c.id}/rename`, { name: inp.value }); S.collections = null; route(); } }]);
    setTimeout(() => inp.select(), 40);
  };
  const remove = async (c) => {
    if (c.builtin) { toast('Standard- und AD-Sammlungen können nicht gelöscht werden.', 'err'); return; }
    const n = collDescendants(cols, c.id).length;
    if (!(await confirmDlg(`„${c.name}“${n ? ` und ${n} untergeordnete Sammlung(en)` : ''} löschen? Die Computer selbst bleiben erhalten.`, 'Löschen', true))) return;
    if (await run(() => DEL(`/api/collections/${c.id}`), 'Gelöscht') !== undefined) { S.collections = null; go(c.id === cid ? '#/computers' : location.hash); route(); }
  };
  const duplicate = async (c) => {
    const r = await run(() => POST(`/api/collections/${c.id}/duplicate`, {}), 'Dupliziert');
    if (r) go(`#/computers?c=${r.id}`);
  };
  const treeMenu = (c, e) => {
    selId = c ? c.id : null; drawTree();
    const membersT = async () => (c ? (await GET(`/api/computers?collection=${c.id}`)).map((x) => ({ id: x.id, name: x.name, online: x.online })) : []);
    const clip = S.collClip;
    contextMenu(e.clientX, e.clientY, [
      c && c.type !== 'folder' ? { label: 'Öffnen', icon: 'coll', onClick: () => go(`#/computers?c=${c.id}`) } : null,
      c ? { label: c.builtin ? 'Als Kopie bearbeiten' : 'Bearbeiten', icon: 'edit', hint: 'Doppelklick', onClick: () => (c.builtin ? collectionDlg(c, { copy: true }) : collectionDlg(c)) } : null,
      { label: 'Neu', icon: 'plus', sub: [
        { label: 'Dynamische Sammlung …', icon: 'coll', onClick: () => collectionDlg(null, { parentId: newIn(c), type: 'dynamic' }) },
        { label: 'Statische Sammlung …', icon: 'stat', onClick: () => collectionDlg(null, { parentId: newIn(c), type: 'static' }) },
        { label: 'Ordner …', icon: 'folder', onClick: () => collectionDlg(null, { parentId: newIn(c), type: 'folder' }) }] },
      c ? { label: 'Duplizieren', icon: 'coll', onClick: () => duplicate(c) } : null,
      '-',
      c && c.type !== 'folder' ? { label: 'Sammlung scannen', icon: 'refresh', sub: () => scanSubItems(() => memberIdsOf(c.id)) } : null,
      c && c.type !== 'folder' ? { label: 'Heartbeat', icon: 'net', onClick: async () => heartbeatIds(await memberIdsOf(c.id)) } : null,
      c && c.type !== 'folder' ? { label: 'Bereitstellen …', icon: 'send', onClick: () => deployDlg({ collectionId: c.id }) } : null,
      c && c.type !== 'folder' ? { label: 'Tools', icon: 'terminal', sub: () => toolSubItems(membersT) } : null,
      c && c.type !== 'folder' ? { label: 'Bericht ausführen', icon: 'report', sub: () => reportSubItems(c.id) } : null,
      '-',
      c ? { label: 'Ausschneiden', icon: 'x', hint: 'Strg+X', disabled: !!c.builtin, onClick: () => { S.collClip = { mode: 'cut', id: c.id, name: c.name }; toast(`„${c.name}“ ausgeschnitten – Ziel wählen und einfügen`); drawTree(); } } : null,
      c ? { label: 'Kopieren', icon: 'coll', hint: 'Strg+C', onClick: () => { S.collClip = { mode: 'copy', id: c.id, name: c.name }; toast(`„${c.name}“ kopiert`); } } : null,
      { label: clip ? `Einfügen („${clip.name}“)` : 'Einfügen', icon: 'plus', hint: 'Strg+V', disabled: !clip, onClick: () => paste(c) },
      '-',
      c ? { label: 'Umbenennen', icon: 'edit', hint: 'F2', disabled: !!c.builtin, onClick: () => rename(c) } : null,
      c ? { label: 'Löschen', icon: 'trash', hint: 'Entf', disabled: !!c.builtin, onClick: () => remove(c) } : null,
    ]);
  };

  // Ziehen & Ablegen: Sammlungen verschieben (Strg = kopieren), Computer auf statische Sammlungen ziehen
  const dropOn = async (target, e) => {
    const raw = e.dataTransfer.getData('application/x-pk-computers');
    if (raw || S.dragComputers) {
      const ids = raw ? JSON.parse(raw) : S.dragComputers;
      if (!target || target.type !== 'static') { toast('Computer lassen sich nur auf statische Sammlungen ziehen.', 'err'); return; }
      if (await run(() => POST(`/api/collections/${target.id}/members`, { add: ids }), `${ids.length} Computer zu „${target.name}“ hinzugefügt`) !== undefined) { S.collections = null; route(); }
      return;
    }
    const src = Number(e.dataTransfer.getData('application/x-pk-collection') || S.dragColl || 0);
    if (!src || (target && src === target.id)) return;
    const srcC = cols.find((x) => x.id === src);
    const copy = e.ctrlKey || e.altKey || (srcC && srcC.builtin);
    if (target && target.builtin) { toast('In Standard- und AD-Sammlungen kann nichts abgelegt werden.', 'err'); return; }
    if (copy) { const r = await run(() => POST(`/api/collections/${src}/duplicate`, { parent_id: target ? target.id : null }), 'Kopiert'); if (r) route(); }
    else if (await run(() => POST(`/api/collections/${src}/move`, { parent_id: target ? target.id : null }), 'Verschoben') !== undefined) route();
  };
  const dragTarget = (el, target) => {
    el.addEventListener('dragover', (e) => {
      const comps = S.dragComputers || [...(e.dataTransfer.types || [])].includes('application/x-pk-computers');
      if (comps ? (target && target.type === 'static') : (!target || !target.builtin)) {
        e.preventDefault(); e.dataTransfer.dropEffect = comps || e.ctrlKey ? 'copy' : 'move'; el.classList.add('dropto');
      }
    });
    el.addEventListener('dragleave', () => el.classList.remove('dropto'));
    el.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); el.classList.remove('dropto'); dropOn(target, e); });
  };

  const renderNode = (c, depth) => {
    const kids = byParent[c.id] || [];
    const isOpen = openState[c.id] !== undefined ? openState[c.id] : (depth === 0 || c.id === cid || collDescendants(cols, c.id).includes(cid));
    const tw = h('span.twisty', { onclick: (e) => { e.stopPropagation(); openState[c.id] = !isOpen; saveOpen(); drawTree(); } }, kids.length ? (isOpen ? '▾' : '▸') : '');
    const it = h('div.tree-item', {
      class: (c.id === cid ? 'active ' : '') + (c.id === selId && c.id !== cid ? 'focus ' : '') + (S.collClip && S.collClip.mode === 'cut' && S.collClip.id === c.id ? 'cut ' : '')
        + (c.builtin === 2 ? 'ad ' : ''),
      // Standard- und AD-Bereich sind fest: nur auf- und zuklappen, nicht ziehen (Kopie über Rechtsklick → Kopieren)
      style: { paddingLeft: `${8 + depth * 14}px` }, draggable: c.builtin ? 'false' : 'true', title: c.description || '',
      onclick: () => { selId = c.id; if (c.type !== 'folder') go(`#/computers?c=${c.id}`); else { openState[c.id] = !isOpen; saveOpen(); drawTree(); } },
    }, tw, h('span.ic-s', icon(COLL_ICON(c))), h('span.lbl', { class: c.builtin === 1 ? '' : 'raw' }, c.name),
    c.count !== null && c.count !== undefined ? h('span.cnt', num(c.count)) : null);
    it.addEventListener('dblclick', () => (c.builtin ? collectionDlg(c, { copy: true }) : collectionDlg(c)));
    it.addEventListener('contextmenu', (e) => { e.preventDefault(); e.stopPropagation(); treeMenu(c, e); });
    if (!c.builtin) it.addEventListener('dragstart', (e) => { S.dragColl = c.id; try { e.dataTransfer.setData('application/x-pk-collection', String(c.id)); } catch (x) { /* egal */ } e.dataTransfer.effectAllowed = 'copyMove'; });
    it.addEventListener('dragend', () => { S.dragColl = null; });
    dragTarget(it, c);
    return [it, kids.length && isOpen ? kids.map((k) => renderNode(k, depth + 1)) : null];
  };
  function drawTree() {
    const f = filterIn.value.trim().toLowerCase();
    if (f) {
      const hits = cols.filter((c) => c.name.toLowerCase().includes(f));
      treeBody.replaceChildren(...hits.map((c) => { const [it] = renderNode({ ...c }, 0); it.querySelector('.lbl').textContent = collPath(cols, c.id); return it; }),
        hits.length ? '' : h('div.muted.small', { style: { padding: '8px 12px' } }, 'Keine Treffer'));
      return;
    }
    treeBody.replaceChildren(...(byParent[0] || []).map((c) => h('div', renderNode(c, 0))));
  }
  filterIn.addEventListener('input', drawTree);
  drawTree();
  treeBody.addEventListener('contextmenu', (e) => { if (e.target === treeBody) { e.preventDefault(); treeMenu(null, e); } });
  dragTarget(treeBody, null);
  treeBody.addEventListener('keydown', (e) => {
    const c = cols.find((x) => x.id === selId);
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'c' && c) { S.collClip = { mode: 'copy', id: c.id, name: c.name }; toast(`„${c.name}“ kopiert`); }
    else if ((e.ctrlKey || e.metaKey) && k === 'x' && c && !c.builtin) { S.collClip = { mode: 'cut', id: c.id, name: c.name }; toast(`„${c.name}“ ausgeschnitten`); drawTree(); }
    else if ((e.ctrlKey || e.metaKey) && k === 'v') paste(c);
    else if (e.key === 'F2' && c) rename(c);
    else if (e.key === 'Delete' && c) remove(c);
    else return;
    e.preventDefault();
  });

  // Computerliste ---------------------------------------------------------------------------
  const rows = await GET('/api/computers' + (cid ? `?collection=${cid}` : ''));
  const t = table([
    { key: 'online', label: '', chooserLabel: 'Status (Punkt)', width: '24px', nosort: true, render: (r) => (r.addr_problem
      // Antwortet unter der Adresse ein anderes Gerät (veralteter DNS-Eintrag, kein Domänenmitglied …): eigener Hinweis
      ? h('span.dot.warn', { title: `${tr('Adresse nicht bestätigt')}: ${tr(r.addr_detail || '')}` })
      : h('span.dot', { class: r.online ? 'on' : 'off', title: r.online ? (r.winrm ? 'Online (WinRM ok)' : 'Online, WinRM nicht erreichbar') : 'Offline' })) },
    { key: 'name', label: 'Name', render: (r) => h('span', h('a', { href: `#/computer/${r.id}` }, r.name), r.scan_allowed ? null : h('span.badge', { style: { marginLeft: '6px' }, title: 'Automatisches Scannen gesperrt' }, 'kein Scan')), value: (r) => r.name, title: (r) => r.dns_name },
    { key: 'online_txt', label: 'Online', value: (r) => (r.online ? 'Ja' : 'Nein'), hidden: true },
    { key: 'scan_status', label: 'Scanstatus', render: scanBadge, value: (r) => (r.scanning ? 'Scan läuft' : r.scan_status || 'Neu') },
    { key: 'os', label: 'Betriebssystem' },
    { key: 'os_display_version', label: 'Version' },
    { key: 'os_build', label: 'Build' },
    { key: 'uptime', label: 'Betriebszeit', value: (r) => (r.online ? uptime(r.last_boot) : ''), sort: (r) => (r.online && r.last_boot ? r.last_boot : ''), hidden: true },
    { key: 'current_user', label: 'Benutzer' },
    { key: 'manufacturer', label: 'Hersteller', hidden: true },
    { key: 'model', label: 'Modell' },
    { key: 'chassis', label: 'Gehäuse', hidden: true },
    { key: 'serial', label: 'Seriennummer', hidden: true },
    { key: 'cpu', label: 'Prozessor', hidden: true, cls: 'trunc' },
    { key: 'ram_gb', label: 'RAM (GB)', hidden: true, cls: 'right' },
    { key: 'os_arch', label: 'Architektur', hidden: true },
    { key: 'winget', label: 'winget', hidden: true, value: (r) => r.winget || (r.last_scan ? 'fehlt' : '') },
    { key: 'ip_addresses', label: 'IP', cls: 'trunc' },
    { key: 'resolved_ip', label: 'DNS-Adresse', cls: 'mono', hidden: true, title: (r) => (r.addr_problem ? tr(r.addr_detail || "") : undefined) },
    { key: 'mac_addresses', label: 'MAC', hidden: true, cls: 'trunc' },
    { key: 'description', label: 'Beschreibung', hidden: true, cls: 'trunc' },
    { key: 'ou', label: 'OU', value: (r) => ouPath(r.dn), hidden: true, cls: 'trunc' },
    { key: 'outdated', label: 'Veraltet', render: (r) => (r.outdated ? h('span.badge.warn', r.outdated) : ''), value: (r) => r.outdated || '' },
    { key: 'last_scan', label: 'Letzter Scan', render: (r) => h('span', { title: fmtDate(r.last_scan) }, relTime(r.last_scan)), value: (r) => fmtDate(r.last_scan), sort: (r) => r.last_scan || '' },
    { key: 'last_seen', label: 'Zuletzt gesehen', value: (r) => fmtDate(r.last_seen), sort: (r) => r.last_seen || '', hidden: true },
    { key: 'ad_last_logon', label: 'Letzte AD-Anmeldung', value: (r) => fmtDate(r.ad_last_logon), sort: (r) => r.ad_last_logon || '', hidden: true },
    { key: 'created_at', label: 'Hinzugefügt', value: (r) => fmtDate(r.created_at), sort: (r) => r.created_at || '', hidden: true },
  ], rows, { key: 'computers', selectable: true, sort: 'name', noun: 'Computer', dragRows: true, onRow: (r) => go(`#/computer/${r.id}`),
    onSelect: (sx) => upd(sx), onContext: (r, e, selRows) => rowMenu(e, selRows), onDelete: (selRows) => delComputers(selRows.map((x) => x.id)) });

  const targetsOf = (selRows) => selRows.map((r) => ({ id: r.id, name: r.name, online: r.online }));
  async function delComputers(ids) {
    if (!(await confirmDlg(`${ids.length} Computer aus POCKET KNIFE löschen? (Computer, die noch im AD sind, werden beim nächsten Abgleich wieder angelegt.)`, 'Löschen', true))) return;
    let ok = 0;
    for (const id of ids) if (await run(() => DEL(`/api/computers/${id}`)) !== undefined) ok += 1;
    if (ok) toast(ok === ids.length ? (ok === 1 ? '1 Computer gelöscht' : `${ok} Computer gelöscht`) : `${ok} von ${ids.length} Computern gelöscht`, ok === ids.length ? 'ok' : 'err');
    route();
  }
  async function setScanAllowed(ids, allowed) {
    if (await run(() => POST('/api/computers/scan_allowed', { ids, allowed }), allowed ? 'Scannen erlaubt' : 'Automatisches Scannen gesperrt') !== undefined) route();
  }
  function rowMenu(e, selRows) {
    const ids = selRows.map((r) => r.id);
    const one = selRows.length === 1 ? selRows[0] : null;
    const statics = cols.filter((c) => c.type === 'static');
    const allAllowed = selRows.every((r) => r.scan_allowed);
    const ck = clickedKey(e);
    contextMenu(e.clientX, e.clientY, [
      one ? { label: 'Computerdetails anzeigen', icon: 'pc', hint: 'Enter', onClick: () => go(`#/computer/${one.id}`) } : null,
      '-',
      { label: 'Scannen', icon: 'refresh', sub: () => scanSubItems(async () => ids) },
      { label: 'Heartbeat', icon: 'net', onClick: () => heartbeatIds(ids) },
      '-',
      { label: 'Bereitstellen …', icon: 'send', onClick: () => deployDlg({ computerIds: ids }) },
      { label: 'Tools', icon: 'terminal', sub: () => toolSubItems(async () => targetsOf(selRows)) },
      { label: 'Remotedesktop im Browser', icon: 'monitor', disabled: selRows.length > 6, onClick: () => openRemote(targetsOf(selRows), 'rdp') },
      '-',
      { label: 'Zur Sammlung hinzufügen', icon: 'stat', sub: [
        ...statics.map((c) => ({ label: collPath(cols, c.id), icon: 'stat', onClick: async () => {
          if (await run(() => POST(`/api/collections/${c.id}/members`, { add: ids }), `${ids.length} Computer zu „${c.name}“ hinzugefügt`) !== undefined) { S.collections = null; route(); }
        } })),
        statics.length ? '-' : null,
        { label: 'Neue statische Sammlung …', icon: 'plus', onClick: () => addToStaticDlg(ids, true) }] },
      LIST_COLL[ck] ? dynCollMenu('computer', ck, selRows, null, { rename: { [ck]: LIST_COLL[ck] } }) : null,
      current && current.type === 'static' ? { label: `Aus „${current.name}“ entfernen`, icon: 'x', onClick: async () => {
        if (!(await askRemove(`${ids.length} Computer aus „${current.name}“ entfernen? Die Computer selbst bleiben erhalten.`))) return;
        if (await run(() => POST(`/api/collections/${cid}/members`, { remove: ids }), 'Entfernt') !== undefined) { S.collections = null; route(); }
      } } : null,
      '-',
      { label: 'Namen kopieren', icon: 'coll', hint: 'Strg+C', onClick: () => copyText(selRows.map((r) => r.name).join('\r\n'), `${selRows.length} Name(n) kopiert`) },
      { label: 'Als CSV speichern', icon: 'download', onClick: () => downloadText(`computer_${localDate()}.csv`, tableCsv(t, selRows)) },
      '-',
      { label: allAllowed ? '☑ Scannen erlaubt' : '☐ Scannen erlaubt', icon: 'refresh', onClick: () => setScanAllowed(ids, !allAllowed) },
      '-',
      { label: 'Löschen', icon: 'trash', hint: 'Entf', onClick: () => delComputers(ids) },
      { label: 'Alle auswählen', icon: 'list', hint: 'Strg+A', onClick: () => t.selectAll() },
    ]);
  }
  t.el.querySelector('.tbl-scroll').addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c' && t.selected().length) { e.preventDefault(); copyText(t.selected().map((r) => r.name).join('\r\n'), `${t.selected().length} Name(n) kopiert`); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'h' && t.selected().length) { e.preventDefault(); heartbeatIds(t.selectedIds()); }
  });

  const bScan = btn('Scannen ▾', (e) => scanMenu(e.currentTarget, t.selectedIds()), { icon: 'refresh', title: 'Mit Scanprofil scannen' });
  const bDeploy = btn('Bereitstellen …', () => deployDlg({ computerIds: t.selectedIds() }), { icon: 'send', cls: 'primary' });
  const bTools = btn('Tools ▾', (e) => toolsMenu(e.currentTarget, targetsOf(t.selected())), { icon: 'terminal' });
  const bCsv = btn('CSV', () => downloadText(`computer_${localDate()}.csv`, tableCsv(t, t.filtered())), { icon: 'download', cls: 'ghost', title: 'Gefilterte Liste als CSV speichern' });
  const editColl = current && !current.builtin ? btn('Sammlung bearbeiten', () => collectionDlg(current), { icon: 'edit', cls: 'ghost' }) : null;
  const addMembers = current && current.type === 'static' ? btn('Mitglieder …', () => collectionDlg(current), { icon: 'stat', cls: 'ghost', title: 'Computer zu dieser statischen Sammlung hinzufügen oder entfernen' }) : null;
  t.actions.append(bScan, bDeploy, bTools, addMembers || '', editColl || '', bCsv);
  function upd(sx) { [bScan, bDeploy, bTools].forEach((b) => (b.disabled = !sx.length)); }
  upd([]);
  const hint = h('div.tbl-hint.muted.small', 'Rechtsklick für weitere Aktionen · Strg/Umschalt + Klick für Mehrfachauswahl · Doppelklick öffnet · Computer auf eine statische Sammlung ziehen fügt sie hinzu');

  setContent(h('div', { style: { display: 'flex', flex: '1', minWidth: '0' } }, tree, splitter(tree, 'collections'), h('div.pane', t.el, hint)), true);
  every(20000, async () => {
    if (parseHash().parts[0] !== 'computers' || document.querySelector('.cmenu')) return;
    try { t.setRows(await GET('/api/computers' + (cid ? `?collection=${cid}` : ''))); } catch (e) { /* still */ }
  });
}

async function scanIds(ids, profileId) {
  if (!ids.length) return;
  await run(() => POST('/api/computers/scan', { ids, profile_id: profileId || null }),
    (r) => `${r.queued} Scan(s) gestartet` + (r.busy ? ` · ${r.busy} läuft bereits` : ''));
}

function addComputerDlg() {
  const name = h('input', { placeholder: 'PC-0815 oder pc-0815.firma.local' });
  modal('Computer hinzufügen', h('div.form', field('Computername / FQDN', name,
    'Nur nötig für Computer, die nicht automatisch über das AD gefunden werden.')), [
    { label: 'Abbrechen' },
    { label: 'Hinzufügen', cls: 'primary', onClick: async () => { await POST('/api/computers', { name: name.value }); toast('Hinzugefügt, Scan gestartet', 'ok'); route(); } },
  ]);
}

async function addToStaticDlg(ids, forceNew = false) {
  const cols = (await collections(true)).filter((c) => c.type === 'static');
  const pick = sel([['', '— neue statische Sammlung —'], ...cols.map((c) => [c.id, c.name])], forceNew || !cols[0] ? '' : cols[0].id);
  const newName = h('input', { placeholder: 'Name der neuen Sammlung' });
  modal(`${ids.length} Computer zur Sammlung hinzufügen`, h('div.form', field('Sammlung', pick), field('Neuer Name', newName, 'Nur bei neuer Sammlung.')), [
    { label: 'Abbrechen' },
    {
      label: 'Hinzufügen', cls: 'primary', onClick: async () => {
        let id = pick.value;
        if (!id) { if (!newName.value.trim()) throw new Error('Namen angeben'); id = (await POST('/api/collections', { name: newName.value.trim(), type: 'static' })).id; }
        await POST(`/api/collections/${id}/members`, { add: ids });
        toast('Hinzugefügt', 'ok');
        S.collections = null;
        route();
      },
    },
  ]);
}

function collDescendants(cols, id) {
  const out = []; const todo = [id];
  while (todo.length) { const cur = todo.pop(); cols.filter((x) => x.parent_id === cur).forEach((x) => { out.push(x.id); todo.push(x.id); }); }
  return out;
}
function collPath(cols, id) {
  const by = new Map(cols.map((x) => [x.id, x]));
  const parts = []; let cur = by.get(id); let n = 0;
  while (cur && n++ < 30) { parts.unshift(cur.name); cur = by.get(cur.parent_id); }
  return parts.join(' / ');
}

async function collSchema(reload = false) {
  if (!S.schema || reload) S.schema = await GET('/api/collections/schema');
  return S.schema;
}

const COLL_TEMPLATES = [
  ['Software installiert', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'software', column: 'name', op: 'contains', value: '' }] })],
  ['Software fehlt', () => ({ type: 'group', match: 'none', items: [{ type: 'cond', table: 'software', column: 'name', op: 'contains', value: '' }] })],
  ['Software älter als Version …', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'software', column: 'name', op: 'contains', value: '' }, { type: 'cond', table: 'software', column: 'version', op: 'v_lt', value: '' }] })],
  ['Veraltete Software (winget)', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'software', column: 'outdated', op: 'is_true' }] })],
  ['Windows-Release (z. B. 23H2)', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'computer', column: 'os_display_version', op: 'equals', value: '' }] })],
  ['Wenig Speicherplatz auf C:', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'disks', column: 'drive', op: 'equals', value: 'C:' }, { type: 'cond', table: 'disks', column: 'free_gb', op: 'lt', value: '20' }] })],
  ['Nicht gescannt seit … Tagen', () => ({ type: 'group', match: 'any', items: [{ type: 'cond', table: 'computer', column: 'last_scan', op: 'older_days', value: '7' }, { type: 'cond', table: 'computer', column: 'last_scan', op: 'empty' }] })],
  ['Dienst läuft nicht', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'services', column: 'name', op: 'equals', value: '' }, { type: 'cond', table: 'services', column: 'state', op: 'not_equals', value: 'Running' }] })],
  ['Mitglied einer AD-Gruppe', () => ({ type: 'group', match: 'all', items: [{ type: 'cond', table: 'ad_groups', column: 'name', op: 'equals', value: '' }] })],
];

function filterEditor(root, schema, cols, selfId, onChange, newCond, emptyText) {
  const tables = [];
  for (const t of schema.tables) tables.push({ ...t, ui: t.alias ? '__cf' : t.key });
  const tdef = (ui) => tables.find((t) => t.ui === ui) || tables[0];
  const uiKey = (it) => (it.table === 'computer' && String(it.column || '').startsWith('cf_') ? '__cf' : (it.table || 'computer'));
  const changed = () => { if (onChange) onChange(); };
  const box = h('div.feditor');

  function condRow(it, parent) {
    let t = tdef(uiKey(it));
    if (!it.table || !tables.some((x) => x.ui === uiKey(it))) it.table = t.alias ? 'computer' : t.key;
    let col = t.columns.find((c) => c.key === it.column);
    if (!col && t.columns.length && !t.free_columns) { col = t.columns[0]; it.column = col.key; }
    const ops = () => {
      const o = schema.ops[(col || { type: 'any' }).type] || schema.ops.text;
      // Größenspalte mit älterer Bedingung (z. B. „enthält“): Operator behalten statt stillschweigend ändern
      return (col && col.type === 'bytes' && it.op && !o.includes(it.op) && (schema.ops.any || []).includes(it.op)) ? [...o, it.op] : o;
    };
    if (!ops().includes(it.op)) it.op = ops()[0];
    const row = h('div.fcond');
    const tSel = sel(tables.map((x) => [x.ui, x.label]), t.ui, {
      onchange: (e) => { const nt = tdef(e.target.value); it.table = nt.alias ? 'computer' : nt.key; it.column = nt.columns[0] ? nt.columns[0].key : ''; it.op = ''; it.value = ''; redraw(); changed(); },
    });
    let cSel;
    if (t.free_columns) {
      cSel = combo(h('input', { value: it.column || '', placeholder: 'Spaltenname', oninput: (e) => { it.column = e.target.value; changed(); },
        // Nach der Auswahl neu zeichnen: eine Größenspalte bekommt ≥/≤ und Einheiten statt Text-Operatoren
        onchange: (e) => { const nc = t.columns.find((c) => c.key === e.target.value); if ((nc && nc.type) !== (col && col.type)) { it.op = ''; it.value = ''; setTimeout(() => { redraw(); changed(); }, 0); } } }),
      t.columns.map((c) => [c.key, c.type === 'bytes' ? 'Größe' : '']));
    } else {
      cSel = sel(t.columns.map((c) => [c.key, c.label]), it.column, {
        onchange: (e) => { it.column = e.target.value; it.op = ''; it.value = ''; redraw(); changed(); },
      });
    }
    const oSel = sel(ops().map((o) => [o, schema.op_labels[o] || o]), it.op, { onchange: (e) => { it.op = e.target.value; redraw(); changed(); } });
    let vIn;
    const type = (col || { type: 'any' }).type;
    if (schema.no_value.includes(it.op)) {
      vIn = h('span.muted.small', '');
    } else if (type === 'collection') {
      vIn = sel(cols.filter((x) => x.type !== 'folder' && x.id !== selfId).map((x) => [x.id, x.name]), it.value, { onchange: (e) => { it.value = e.target.value; changed(); } });
      if (!it.value && vIn.value) it.value = vIn.value;
    } else if (type === 'date' && (it.op === 'before' || it.op === 'after')) {
      vIn = h('input', { type: 'date', value: it.value || '', onchange: (e) => { it.value = e.target.value; changed(); } });
    } else if (type === 'bytes' && ['gt', 'gte', 'lt', 'lte', 'eq', 'ne'].includes(it.op)) {
      // Größenangaben: mit Einheit eingeben (500 MB, 10 GB, 1,5 TB – 1 GB = 1024³ Bytes), Vorschläge lesbar
      let sizes = null;
      const input = h('input', { value: it.value || '', placeholder: 'z. B. 10 GB', title: 'Mit Einheit: B, KB, MB, GB, TB (1 GB = 1024³ Bytes). Ohne Einheit: Bytes.',
        oninput: (e) => { it.value = e.target.value; changed(); } });
      vIn = h('span.fval-in', combo(input, async () => (sizes = sizes || await GET(`/api/collections/values?table=${encodeURIComponent(it.table)}&column=${encodeURIComponent(it.column)}`)), { width: '140px' }),
        h('span.muted.small', { style: { marginLeft: '6px' } }, 'Größe'));
    } else if (type === 'date' || type === 'number' || ['gt', 'lt'].includes(it.op)) {
      vIn = h('span.row', h('input', { type: 'number', step: 'any', value: it.value ?? '', style: { width: '110px' }, oninput: (e) => { it.value = e.target.value; changed(); } }),
        type === 'date' ? h('span.muted', 'Tage') : null);
    } else {
      const input = h('input', {
        value: it.value || '', placeholder: it.op === 'regex' ? 'regulärer Ausdruck' : it.op === 'wildcard' ? 'z. B. *Office*' : 'Wert',
        oninput: (e) => { it.value = e.target.value; changed(); },
      });
      // Beim Öffnen alle vorhandenen Werte, beim Tippen filtert der Server
      vIn = h('span.fval-in', combo(input, (q) => (it.column ? GET(`/api/collections/values?table=${encodeURIComponent(it.table)}&column=${encodeURIComponent(it.column)}&q=${encodeURIComponent(q || '')}&limit=200`) : []), { remote: true, width: '100%' }));
    }
    append(row, [tSel, cSel, oSel, vIn, h('button.ghost', { type: 'button', title: 'Bedingung entfernen', onclick: async () => { if (!(await askRemove(it.value !== '' && it.value !== undefined && it.value !== null ? `Bedingung „${it.value}“ entfernen?` : 'Diese Bedingung entfernen?'))) return; parent.items.splice(parent.items.indexOf(it), 1); redraw(); changed(); } }, icon('x'))]);
    return row;
  }

  function groupBox(g, parent, depth) {
    const mSel = sel(Object.entries(schema.match_labels).map(([k, l]) => [k, l]), g.match, { onchange: (e) => { g.match = e.target.value; redraw(); changed(); } });
    const desc = { all: 'der folgenden Bedingungen treffen zu', any: 'der folgenden Bedingungen trifft zu', not_all: 'der folgenden Bedingungen treffen zu', none: 'der folgenden Bedingungen trifft zu' }[g.match];
    const body = h('div.fgroup-b');
    for (const it of g.items) body.appendChild(it.type === 'group' ? groupBox(it, g, depth + 1) : condRow(it, g));
    if (!g.items.length) body.appendChild(h('div.muted.small', depth ? 'Leere Gruppe' : (emptyText || 'Noch keine Bedingung – ohne Bedingung enthält die Sammlung alle Computer.')));
    return h('div.fgroup', { class: `m-${g.match}` },
      h('div.fgroup-h', mSel, h('span.muted', desc), h('div.spacer'),
        btn('Bedingung', () => { g.items.push(newCond ? newCond() : { type: 'cond', table: 'software', column: 'name', op: 'contains', value: '' }); redraw(); changed(); }, { icon: 'plus', cls: 'small' }),
        btn('Gruppe', () => { g.items.push({ type: 'group', match: 'all', items: [] }); redraw(); changed(); }, { icon: 'plus', cls: 'small' }),
        parent ? h('button.ghost', { type: 'button', title: 'Gruppe entfernen', onclick: async () => { if ((g.items || []).length && !(await askRemove(`Diese Gruppe mit ${g.items.length} Bedingung(en) entfernen?`))) return; parent.items.splice(parent.items.indexOf(g), 1); redraw(); changed(); } }, icon('trash')) : null),
      body);
  }

  function redraw() { box.replaceChildren(groupBox(root, null, 0)); }
  redraw();
  return { el: box, redraw };
}

async function collectionDlg(c, opt = {}) {
  const [schema, cols] = await Promise.all([collSchema(true), collections()]);
  const isNew = !c || opt.copy || opt.preset;
  const src = c || { name: '', type: opt.type || 'dynamic', filters: { type: 'group', match: 'all', items: [] }, parent_id: opt.parentId || null, description: '' };
  c = { ...src, name: opt.copy ? `Kopie von ${src.name}` : src.name };
  if (opt.copy) c.builtin = 0;
  const tree = JSON.parse(JSON.stringify(c.filters && c.filters.type === 'group' ? c.filters : { type: 'group', match: 'all', items: [] }));
  const name = h('input', { value: c.name });
  const type = sel([['dynamic', 'Dynamisch (Filter)'], ['static', 'Statisch (manuell)'], ['folder', 'Ordner']], c.type);
  const banned = new Set(c.id && !opt.copy ? [c.id, ...collDescendants(cols, c.id)] : []);
  const parent = sel([['', '— oberste Ebene —'], ...cols.filter((x) => !x.builtin && !banned.has(x.id)).map((x) => [x.id, collPath(cols, x.id)])],
    opt.copy && c.builtin ? '' : c.parent_id);
  const desc = h('input', { value: c.description || '' });
  const count = h('b');
  const sample = h('div.fpreview');
  let timer = null;
  const preview = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const r = await POST('/api/collections/preview', { filters: tree });
        count.textContent = `${num(r.count)} Computer`;
        count.className = '';
        sample.replaceChildren(...r.sample.map((x) => h('span.chip', h('span.dot', { class: x.online ? 'on' : 'off' }), ' ', x.name)),
          ...(r.count > r.sample.length ? [h('span.muted', ` … und ${r.count - r.sample.length} weitere`)] : []));
      } catch (e) {
        count.textContent = e.message;
        count.className = 'err-text';
        sample.replaceChildren();
      }
    }, 500);
  };
  const ed = filterEditor(tree, schema, cols, c.id, preview);
  const tpl = sel([['', 'Vorlage einfügen …'], ...COLL_TEMPLATES.map(([l], i) => [i, l])], '', {
    onchange: (e) => { if (e.target.value === '') return; tree.items.push(COLL_TEMPLATES[Number(e.target.value)][1]()); e.target.value = ''; ed.redraw(); preview(); },
  });
  const dyn = h('div.full',
    h('div.row', { style: { margin: '6px 0 8px' } }, h('h3', 'Filter'), h('div.spacer'), tpl),
    ed.el,
    h('p.muted.small', 'Tipp: Bedingungen zur selben Tabelle in einer „Alle“-Gruppe gelten für denselben Eintrag (z. B. Anwendung „Firefox“ UND Version kleiner als 130). ',
      'Für „hat etwas nicht“ eine Gruppe „Keine“ verwenden, z. B. Keine: Anwendungen · Name enthält Firefox = Computer ohne Firefox.'),
    h('div.fprev-box', h('div.row', h('span.muted', 'Ergebnis:'), count), sample));
  let memberIds = c.id && c.type === 'static' && !opt.copy ? (await GET(`/api/computers?collection=${c.id}`)).map((x) => x.id) : [];
  let createdId = null;
  const picker = await computerPicker(memberIds);
  const stat = h('div.full', h('h3', { style: { margin: '6px 0 8px' } }, 'Mitglieder'),
    h('p.muted.small', { style: { marginTop: 0 } }, 'Computer anhaken, die zu dieser Sammlung gehören. Alternativ in der Computerliste markieren → Rechtsklick → „Zur Sammlung hinzufügen“.'), picker.el);
  const toggle = () => { dyn.style.display = type.value === 'dynamic' ? '' : 'none'; stat.style.display = type.value === 'static' ? '' : 'none'; };
  type.addEventListener('change', toggle);
  toggle();
  preview();
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    buttons.unshift({
      label: 'Löschen', cls: 'danger', onClick: async () => {
        if (!(await confirmDlg(`Sammlung „${c.name}“ löschen?`, 'Löschen', true))) return false;
        await DEL(`/api/collections/${c.id}`); S.collections = null; toast('Gelöscht', 'ok'); go('#/computers');
      },
    });
  }
  buttons.push({
    label: 'Speichern', cls: 'primary', onClick: async () => {
      const body = { name: name.value, type: type.value, parent_id: parent.value ? Number(parent.value) : null, filters: tree, match: tree.match, description: desc.value };
      // Schon angelegt (z. B. erster Versuch scheiterte erst bei den Mitgliedern)? Dann aktualisieren statt doppelt anlegen
      let id;
      if (createdId) { await PUT(`/api/collections/${createdId}`, body); id = createdId; }
      else if (isNew) { id = (await POST('/api/collections', body)).id; createdId = id; }
      else { await PUT(`/api/collections/${c.id}`, body); id = c.id; }
      if (body.type === 'static') {
        const want = new Set(picker.selected());
        const had = new Set(memberIds);
        await POST(`/api/collections/${id}/members`, { add: [...want].filter((x) => !had.has(x)), remove: [...had].filter((x) => !want.has(x)) });
        memberIds = [...want];
      }
      S.collections = null;
      toast('Gespeichert', 'ok');
      if (opt.onSaved) opt.onSaved(id, body.name);
      else go(`#/computers?c=${id}`);
    },
  });
  modal(isNew ? (opt.copy ? 'Sammlung kopieren' : 'Neue Sammlung') : `Sammlung bearbeiten: ${c.name}`,
    h('div.form', field('Name', name), field('Typ', type), field('Ablegen in', parent, 'Unter einer Sammlung (nicht Ordner) enthält eine dynamische Sammlung nur Computer der übergeordneten Sammlung.'),
      field('Beschreibung', desc), dyn, stat), buttons, { wide: true, sticky: true, help: "collections" });
}

// ---------------------------------------------------------------------------
// Ansicht: Computerdetails
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Dynamische Sammlung direkt aus Inventarwerten (Rechtsklick → „Dynamische Sammlung erstellen“)
// ---------------------------------------------------------------------------
// Kategorie der Computeransicht -> [Filtertabelle, Hauptspalte, {Zeilenschlüssel: Filterspalte}]
const INV_COLL = {
  software: ['software', 'name'], ad_groups: ['ad_groups', 'name'], cpus: ['cpus', 'name'], displays: ['displays', 'model'],
  environment: ['environment', 'name'], devices: ['devices', 'name'], hotfixes: ['hotfixes', 'hotfix_id'],
  local_groups: ['local_groups', 'name'], local_users: ['local_users', 'name'], memory: ['memory', 'part', { capacity: 'capacity_gb' }],
  nics: ['nics', 'name', { speed: 'speed_mbit' }], printers: ['printers', 'name'], processes: ['processes', 'name'],
  product_keys: ['product_keys', 'product'], services: ['services', 'name'], shares: ['shares', 'name'],
  features: ['features', 'name'], tasks: ['tasks', 'name'], disks: ['disks', 'drive'], disks_physical: ['disks_physical', 'model', { size: 'size_gb' }],
  admins: ['admins', 'name'],
};
// Spalten der Computerliste -> Filterspalte der Tabelle „Computer“
const LIST_COLL = { os: 'os', os_display_version: 'os_display_version', os_build: 'os_build', current_user: 'current_user',
  manufacturer: 'manufacturer', model: 'model', chassis: 'chassis', cpu: 'cpu', os_arch: 'os_arch', description: 'description',
  scan_status: 'scan_status', winget: 'winget_version', ram_gb: 'ram_gb' };

function collCond(table, column, type, value) {
  const v = value === null || value === undefined ? '' : String(value).trim();
  if (type === 'bool') return { type: 'cond', table, column, op: v === 'true' || v === '1' || v === 'True' || v === 'Ja' ? 'is_true' : 'is_false', value: '' };
  if (v === '') return { type: 'cond', table, column, op: 'empty', value: '' };
  const op = type === 'version' ? 'v_eq' : type === 'number' ? 'eq' : 'equals';
  return { type: 'cond', table, column, op, value: v };
}

function collGroup(match, items) { return { type: 'group', match, items }; }

function shortVal(v, n = 40) { const s = String(v ?? '').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }

// Neue dynamische Sammlung mit vorbelegtem Filter im normalen Dialog öffnen (Name und Filter lassen sich vor dem Speichern anpassen)
function newDynColl(name, filters) {
  collectionDlg({ name: tr(name).slice(0, 120), type: 'dynamic', filters, parent_id: null, description: '' }, { preset: true });
}

// Untermenü für eine Zeile einer Inventartabelle.
// table = Filtertabelle, primary = Hauptspalte, rows = markierte Zeilen, clicked = Spalte unter dem Mauszeiger
function dynCollMenu(table, primary, rows, clicked, opt = {}) {
  return {
    label: 'Dynamische Sammlung erstellen', icon: 'coll', sub: async () => {
      const schema = await collSchema();
      const tdef = schema.tables.find((x) => x.key === table && !x.alias);
      if (!tdef) return [{ label: 'Für diese Tabelle nicht verfügbar', disabled: true }];
      const ren = opt.rename || {};
      const colOf = (k) => tdef.columns.find((c) => c.key === (ren[k] || k)) || (tdef.free_columns || table.startsWith('custom:') ? { key: k, label: k, type: 'any' } : null);
      const conv = { capacity_gb: (v) => Math.round(v / 107374182.4) / 10, size_gb: (v) => Math.round(v / 107374182.4) / 10, speed_mbit: (v) => Math.round(v / 1e6) };
      const valOf = (r, k) => { const v = opt.value ? opt.value(r, k) : r[k]; const f = conv[ren[k]]; return f && v !== '' && v !== null && !isNaN(Number(v)) ? f(Number(v)) : v; };
      const pc = colOf(primary);
      if (!pc) return [{ label: 'Für diese Tabelle nicht verfügbar', disabled: true }];
      const tl = table === 'computer' ? pc.label : tdef.label;
      const cond = (r, k) => { const c = colOf(k); return collCond(table, c.key, c.type, valOf(r, k)); };
      // Spalten mit Byte-Werten: lesbar anzeigen und als Schwelle (≥/≤) statt exaktem Bytewert anbieten
      const isBytes = (k, r) => opt.bytes && opt.bytes.has(k) && r && r[k] !== '' && r[k] !== null && !isNaN(Number(r[k]));
      const sizeVal = (n, up) => {
        const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; let v = Number(n);
        while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
        const d = i === 0 ? 1 : 10; const x = (up ? Math.ceil(v * d) : Math.floor(v * d)) / d;
        return `${String(x).replace('.', ',')} ${u[i]}`;
      };
      const sizeCond = (r, k, op) => ({ type: 'cond', table, column: colOf(k).key, op, value: sizeVal(r[k], op === 'lte') });
      const items = [];
      if (rows.length === 1 && table === 'software') {
        // Software: zuerst „installiert“ / „nicht installiert“ – Name ohne Präfix, damit er nicht umgeschrieben werden muss
        const r = rows[0];
        const pv = shortVal(valOf(r, primary));
        const pat = uninstallPattern(String(r.name || ''));
        const anyVer = pat && pat !== r.name ? { type: 'cond', table, column: 'name', op: 'wildcard', value: pat } : null;
        const base = anyVer ? pat.replace(/\*$/, '').trim() : String(r.name || '').trim();
        const has = anyVer || cond(r, primary);
        items.push({ label: anyVer ? `„${shortVal(base)}“ installiert (jede Version)` : `„${shortVal(base)}“ installiert`,
          onClick: () => newDynColl(`${base} installiert`, collGroup('all', [has])) });
        items.push({ label: `„${shortVal(base)}“ nicht installiert`, onClick: () => newDynColl(`${base} nicht installiert`, collGroup('none', [has])) });
        items.push('-');
        if (anyVer) {
          items.push({ label: `Computer mit genau „${pv}“`, onClick: () => newDynColl(`${r.name} installiert`, collGroup('all', [cond(r, primary)])) });
        }
        if (r.version) {
          items.push({ label: `Computer mit „${shortVal(base)}“ in Version ${shortVal(r.version, 20)}`, onClick: () => newDynColl(`${base} ${r.version}`,
            collGroup('all', [has, { type: 'cond', table, column: 'version', op: 'v_eq', value: String(r.version) }])) });
          items.push({ label: `Computer mit „${shortVal(base)}“ älter als ${shortVal(r.version, 20)}`, onClick: () => newDynColl(`${base} älter als ${r.version}`,
            collGroup('all', [has, { type: 'cond', table, column: 'version', op: 'v_lt', value: String(r.version) }])) });
        }
        if (r.publisher && clicked !== 'publisher') {
          const pub = shortVal(r.publisher);
          items.push({ label: `Computer mit Software von „${pub}“`, onClick: () => newDynColl(`Software von ${pub}`, collGroup('all', [cond(r, 'publisher')])) });
        }
        // angeklickte Spalte (Architektur, Installationsort, winget-ID …) – allein oder zusammen mit dem Programm
        const cc = clicked && !['name', 'version'].includes(clicked) ? colOf(clicked) : null;
        if (cc && cc.type !== 'date' && String(valOf(r, clicked) ?? '').trim() !== '') {
          const cv = shortVal(valOf(r, clicked));
          items.push({ label: `Computer mit ${cc.label} = „${cv}“`, onClick: () => newDynColl(`${cc.label}: ${cv}`, collGroup('all', [cond(r, clicked)])) });
          items.push({ label: `Computer mit „${shortVal(base)}“ und ${cc.label} = „${cv}“`, onClick: () => newDynColl(`${base} – ${cv}`, collGroup('all', [has, cond(r, clicked)])) });
        }
        return items;
      }
      if (rows.length > 1 && table === 'software') {
        const vals = [...new Map(rows.map((r) => [String(valOf(r, primary) ?? ''), r])).values()];
        const names = vals.map((r) => shortVal(valOf(r, primary), 25));
        const nm = names.slice(0, 3).join(', ') + (names.length > 3 ? ' …' : '');
        items.push({ label: `Alle ${vals.length} installiert`, onClick: () => newDynColl(`${nm} installiert`, collGroup('all', vals.map((r) => collGroup('all', [cond(r, primary)])))) });
        items.push({ label: `Mindestens eines der ${vals.length} installiert`, onClick: () => newDynColl(`${nm} – eines installiert`, collGroup('any', vals.map((r) => cond(r, primary)))) });
        items.push({ label: `Keines der ${vals.length} installiert`, onClick: () => newDynColl(`${nm} nicht installiert`, collGroup('none', vals.map((r) => cond(r, primary)))) });
        return items;
      }
      if (rows.length === 1) {
        const r = rows[0];
        const pv = shortVal(valOf(r, primary));
        items.push({ label: `Computer mit ${pc.label} = „${pv}“`, onClick: () => newDynColl(`${tl}: ${pv}`, collGroup('all', [cond(r, primary)])) });
        const cc = clicked && clicked !== primary ? colOf(clicked) : null;
        if (cc && isBytes(clicked, r)) {
          for (const [op, sym] of [['gte', '≥'], ['lte', '≤']]) {
            const c1 = sizeCond(r, clicked, op);
            items.push({ label: `Computer mit ${cc.label} ${sym} ${c1.value}`, onClick: () => newDynColl(`${tl}: ${cc.label} ${sym} ${c1.value}`, collGroup('all', [c1])) });
          }
          const c2 = sizeCond(r, clicked, 'gte');
          items.push({ label: `Computer mit „${pv}“ und ${cc.label} ≥ ${c2.value}`, onClick: () => newDynColl(`${tl}: ${pv} – ${cc.label} ≥ ${c2.value}`, collGroup('all', [cond(r, primary), c2])) });
        } else if (cc && cc.type !== 'date' && String(valOf(r, clicked) ?? '').trim() !== '') {
          const cv = shortVal(valOf(r, clicked));
          items.push({ label: `Computer mit ${cc.label} = „${cv}“`, onClick: () => newDynColl(`${tl}: ${cc.label} ${cv}`, collGroup('all', [cond(r, clicked)])) });
          items.push({ label: `Computer mit „${pv}“ und ${cc.label} = „${cv}“`, onClick: () => newDynColl(`${tl}: ${pv} – ${cv}`, collGroup('all', [cond(r, primary), cond(r, clicked)])) });
        }
        // Software: Name ohne Versionsnummer als Muster, damit auch andere Versionen (z. B. „7-Zip 23.01 …“) gefunden werden
        const pat = table === 'software' ? uninstallPattern(String(r.name || '')) : '';
        const anyVer = pat && pat !== r.name ? { type: 'cond', table, column: 'name', op: 'wildcard', value: pat } : null;
        if (anyVer) items.unshift({ label: `Computer mit „${shortVal(pat)}“ (jede Version)`, onClick: () => newDynColl(`${tl}: ${pat.replace(/\*$/, '')}`, collGroup('all', [anyVer])) });
        if (table === 'software' && r.version) {
          const nm = anyVer ? pat.replace(/\*$/, '') : pv;
          items.push({ label: `Computer mit „${nm}“ in Version ${shortVal(r.version, 20)}`, onClick: () => newDynColl(`${nm} ${r.version}`,
            collGroup('all', [anyVer || cond(r, 'name'), { type: 'cond', table, column: 'version', op: 'v_eq', value: String(r.version) }])) });
          items.push({ label: `Computer mit „${nm}“ älter als ${shortVal(r.version, 20)}`, onClick: () => newDynColl(`${nm} älter als ${r.version}`,
            collGroup('all', [anyVer || cond(r, 'name'), { type: 'cond', table, column: 'version', op: 'v_lt', value: String(r.version) }])) });
        }
        if (table === 'software' && r.publisher && clicked !== 'publisher') {
          const pub = shortVal(r.publisher);
          items.push({ label: `Computer mit Software von „${pub}“`, onClick: () => newDynColl(`Hersteller: ${pub}`, collGroup('all', [cond(r, 'publisher')])) });
        }
        items.push('-');
        items.push(table === 'computer'
          ? { label: `Computer mit ${pc.label} ≠ „${pv}“`, onClick: () => newDynColl(`${pc.label}: nicht ${pv}`, collGroup('none', [cond(r, primary)])) }
          : { label: `Computer ohne „${anyVer ? pat.replace(/\*$/, '') : pv}“`, onClick: () => newDynColl(`Ohne ${anyVer ? pat.replace(/\*$/, '') : pv}`, collGroup('none', [anyVer || cond(r, primary)])) });
      } else {
        const vals = [...new Map(rows.map((r) => [String(valOf(r, primary) ?? ''), r])).values()];
        const names = vals.map((r) => shortVal(valOf(r, primary), 25));
        const nm = names.slice(0, 3).join(', ') + (names.length > 3 ? ' …' : '');
        items.push({ label: `Computer mit mindestens einem der ${vals.length} Einträge`, onClick: () => newDynColl(`${tl}: ${nm}`, collGroup('any', vals.map((r) => cond(r, primary)))) });
        items.push({ label: `Computer mit allen ${vals.length} Einträgen`, onClick: () => newDynColl(`${tl}: ${nm} (alle)`, collGroup('all', vals.map((r) => collGroup('all', [cond(r, primary)])))) });
        items.push('-');
        items.push({ label: `Computer ohne diese ${vals.length} Einträge`, onClick: () => newDynColl(`Ohne ${nm}`, collGroup('none', vals.map((r) => cond(r, primary)))) });
      }
      return items;
    },
  };
}

// Spalte unter dem Mauszeiger (Tabellenzellen tragen data-k)
const clickedKey = (e) => { const td = e && e.target && e.target.closest ? e.target.closest('td[data-k]') : null; return td ? td.dataset.k : null; };

// Einzelwert auf der Computer-Übersicht: Rechtsklick → Sammlung „Computer mit <Feld> = Wert“
function collValue(el, column, value, label) {
  if (value === null || value === undefined || value === '') return el;
  el.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    contextMenu(e.clientX, e.clientY, [
      { label: 'Dynamische Sammlung erstellen', icon: 'coll', sub: async () => {
        const schema = await collSchema();
        const c = schema.tables.find((x) => x.key === 'computer' && !x.alias).columns.find((x) => x.key === column);
        if (!c) return [{ label: 'Nicht verfügbar', disabled: true }];
        const v = shortVal(value);
        return [
          { label: `Computer mit ${c.label} = „${v}“`, onClick: () => newDynColl(`${label || c.label}: ${v}`, collGroup('all', [collCond('computer', column, c.type, value)])) },
          { label: `Computer mit ${c.label} ≠ „${v}“`, onClick: () => newDynColl(`${label || c.label}: nicht ${v}`, collGroup('none', [collCond('computer', column, c.type, value)])) },
        ];
      } },
      { label: 'Wert kopieren', icon: 'coll', onClick: () => copyText(String(value)) },
    ]);
  });
  el.classList.add('has-ctx');
  return el;
}

const PC_CATS = [
  // Gleiche Einträge und Reihenfolge: Schlüssel, Bezeichnung, Icon, Zähler-Schlüssel
  ['computer', 'Computer', 'pc'],
  ['ad_groups', 'Active-Directory-Gruppen', 'users', 'ad_groups'],
  ['software', 'Anwendungen', 'apps', 'software'],
  ['collections', 'Sammlungen', 'coll'],
  ['cpus', 'CPU', 'cpu', 'cpus'],
  ['custom_fields', 'Benutzerdefinierte Felder', 'edit'],
  ['deployments', 'Bereitstellungen', 'send', 'deployments'],
  ['disks', 'Laufwerke', 'drive', 'disks'],
  ['displays', 'Bildschirme', 'monitor', 'displays'],
  ['environment', 'Umgebungsvariablen', 'env', 'environment'],
  ['files', 'Dateien & Ordner', 'folder'],
  ['devices', 'Hardware', 'chip', 'devices'],
  ['hotfixes', 'Hotfixes', 'shield', 'hotfixes'],
  ['local_groups', 'Lokale Gruppen', 'users', 'local_groups'],
  ['local_users', 'Lokale Benutzer', 'user', 'local_users'],
  ['memory', 'Speichermodule', 'mem', 'memory'],
  ['nics', 'Netzwerkkarten', 'net', 'nics'],
  ['powershell', 'PowerShell', 'terminal'],
  ['printers', 'Drucker (lokal)', 'print', 'printers'],
  ['processes', 'Prozesse', 'list', 'processes'],
  ['product_keys', 'Produktschlüssel', 'key', 'product_keys'],
  ['registry', 'Registry', 'db'],
  ['scans', 'Scans', 'refresh', 'scans'],
  ['services', 'Dienste', 'gear', 'services'],
  ['shares', 'Freigaben', 'share', 'shares'],
  ['storage', 'Speicherplatz', 'db'],
  ['features', 'Windows-Features', 'apps', 'features'],
  ['tasks', 'Geplante Aufgaben', 'calendar', 'tasks'],
  ['wmi', 'WMI', 'db'],
];

// PowerShell liefert bei genau einem Element ein Objekt statt einer Liste – immer als Liste behandeln
const arr = (x) => (Array.isArray(x) ? x : x === null || x === undefined || x === '' ? [] : [x]);

const MEM_FORM = { 8: 'DIMM', 12: 'SODIMM', 13: 'SRIMM', 0: '' };
const MEM_TYPE = { 20: 'DDR', 21: 'DDR2', 24: 'DDR3', 26: 'DDR4', 29: 'LPDDR3', 30: 'LPDDR4', 34: 'DDR5', 35: 'LPDDR5' };
const yesNo = (v) => (v === true || v === 1 || v === 'True' ? 'Ja' : v === false || v === 0 || v === 'False' ? 'Nein' : '');
const R = {
  bytes: (k) => (r) => fmtBytes(r[k]),
  date: (k) => (r) => fmtDate(r[k]),
  bool: (k) => (r) => yesNo(r[k]),
};

// Spalten je Kategorie: [Schlüssel, Bezeichnung, Renderer?, CSS-Klasse?]
const PC_COLS = {
  ad_groups: [['name', 'Gruppe'], ['dn', 'Distinguished Name', null, 'trunc']],
  cpus: [['name', 'Prozessor'], ['manufacturer', 'Hersteller'], ['cores', 'Kerne'], ['logical', 'Threads'],
    ['max_mhz', 'Max. MHz'], ['current_mhz', 'Akt. MHz'], ['l2_kb', 'L2 (KB)'], ['l3_kb', 'L3 (KB)'], ['socket', 'Sockel'],
    ['virtualization', 'Virtualisierung', R.bool('virtualization')], ['processor_id', 'Prozessor-ID', null, 'mono']],
  memory: [['slot', 'Steckplatz'], ['bank', 'Bank'], ['capacity', 'Größe', R.bytes('capacity')], ['speed', 'MHz'],
    ['type', 'Typ', (r) => MEM_TYPE[r.type] || ''], ['form_factor', 'Bauform', (r) => MEM_FORM[r.form_factor] || ''],
    ['manufacturer', 'Hersteller'], ['part', 'Teilenummer'], ['serial', 'Seriennummer']],
  displays: [['manufacturer', 'Hersteller'], ['model', 'Modell'], ['serial', 'Seriennummer'], ['product_code', 'Produktcode'],
    ['year', 'Baujahr'], ['week', 'KW'], ['active', 'Aktiv', R.bool('active')]],
  environment: [['name', 'Variable'], ['value', 'Wert', null, 'wrap']],
  devices: [['name', 'Gerät'], ['class', 'Klasse'], ['manufacturer', 'Hersteller'],
    ['status', 'Status', (r) => (r.problem_text ? h('span.badge.err', r.problem_text) : r.status || '')],
    ['device_id', 'Geräte-ID', null, 'trunc']],
  local_groups: [['name', 'Gruppe'], ['description', 'Beschreibung', null, 'trunc'], ['count', 'Mitglieder'], ['members', 'Mitglieder', null, 'wrap']],
  local_users: [['name', 'Benutzer'], ['full_name', 'Name'], ['enabled', 'Aktiv', (r) => (r.enabled === false || r.enabled === 'False' ? h('span.badge', 'deaktiviert') : h('span.badge.ok', 'aktiv'))],
    ['last_logon', 'Letzte Anmeldung', R.date('last_logon')], ['password_last_set', 'Passwort gesetzt', R.date('password_last_set')],
    ['password_expires', 'Passwort läuft ab', R.date('password_expires')], ['description', 'Beschreibung', null, 'trunc']],
  nics: [['name', 'Adapter'], ['connection', 'Verbindung'], ['status', 'Status', (r) => h('span.badge', { class: r.status === 'Verbunden' ? 'ok' : '' }, r.status || '')],
    ['ip', 'IP-Adresse'], ['subnet', 'Subnetz'], ['gateway', 'Gateway'], ['dns', 'DNS'], ['dhcp', 'DHCP', R.bool('dhcp')],
    ['dhcp_server', 'DHCP-Server'], ['mac', 'MAC', null, 'mono'], ['speed', 'Geschwindigkeit', (r) => (r.speed ? `${Math.round(r.speed / 1e6)} Mbit/s` : '')]],
  printers: [['name', 'Drucker'], ['driver', 'Treiber'], ['port', 'Anschluss'], ['default', 'Standard', R.bool('default')],
    ['network', 'Netzwerk', R.bool('network')], ['shared', 'Freigegeben', R.bool('shared')], ['share_name', 'Freigabename'], ['location', 'Standort']],
  processes: [['name', 'Prozess'], ['pid', 'PID'], ['user', 'Benutzer'], ['memory', 'Arbeitsspeicher', R.bytes('memory')],
    ['cpu_s', 'CPU (s)'], ['start', 'Gestartet', R.date('start')], ['company', 'Firma'], ['path', 'Pfad', null, 'trunc']],
  product_keys: [['product', 'Produkt', null, 'wrap'], ['key', 'Schlüssel', null, 'mono'], ['channel', 'Kanal'],
    ['status', 'Status', (r) => (r.status ? h('span.badge', { class: r.status === 'Lizenziert' ? 'ok' : 'warn' }, r.status) : '')]],
  shares: [['name', 'Freigabe'], ['path', 'Pfad'], ['description', 'Beschreibung'], ['type', 'Typ'], ['admin', 'Administrativ', R.bool('admin')]],
  features: [['caption', 'Feature'], ['name', 'Name', null, 'mono'],
    ['state', 'Status', (r) => h('span.badge', { class: ['Aktiviert', 'Installiert'].includes(r.state) ? 'ok' : '' }, r.state || '')], ['kind', 'Art']],
  tasks: [['name', 'Aufgabe'], ['path', 'Ordner'], ['state', 'Status'], ['run_as', 'Ausführen als'], ['last_run', 'Letzte Ausführung', R.date('last_run')],
    ['next_run', 'Nächste Ausführung', R.date('next_run')], ['last_result', 'Ergebnis'], ['action', 'Aktion', null, 'trunc'], ['author', 'Autor']],
  hotfixes: [['hotfix_id', 'KB'], ['description', 'Art'], ['installed_on', 'Installiert', (r) => fmtDate(r.installed_on, false)], ['installed_by', 'Von']],
  services: [['display_name', 'Anzeigename'], ['name', 'Name', null, 'mono'],
    ['state', 'Status', (r) => h('span.badge', { class: r.state === 'Running' ? 'ok' : '' }, r.state === 'Running' ? 'läuft' : r.state === 'Stopped' ? 'beendet' : r.state)],
    ['start_mode', 'Starttyp'], ['account', 'Konto'], ['path', 'Pfad', null, 'trunc'], ['description', 'Beschreibung', null, 'trunc']],
  scans: [['ts', 'Zeitpunkt', R.date('ts')], ['status', 'Status', (r) => h('span.badge', { class: r.status === 'OK' ? 'ok' : 'err' }, r.status)],
    ['duration_ms', 'Dauer', (r) => (r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1).replace('.', ',')} s` : '')],
    ['profile_name', 'Profil'], ['user', 'Ausgelöst von'], ['error', 'Fehler', null, 'wrap'], ['warnings', 'Hinweise', null, 'wrap']],
};

function colsFrom(def) {
  return def.map(([key, label, render, cls]) => ({ key, label, render: render || undefined, cls: cls || '' }));
}

function uptime(boot) {
  if (!boot) return '';
  const d = new Date(boot.replace(' ', 'T'));
  let m = Math.floor((Date.now() - d.getTime()) / 60000);
  if (isNaN(m) || m < 0) return '';
  const days = Math.floor(m / 1440); m -= days * 1440;
  const hrs = Math.floor(m / 60); m -= hrs * 60;
  return [days ? `${days} ${days === 1 ? 'Tag' : 'Tage'}` : '', hrs ? `${hrs} Std.` : '', `${m} Min.`].filter(Boolean).join(' ');
}

function ouPath(dn) {
  if (!dn) return '';
  const parts = dn.split(/,(?=(?:OU|CN|DC)=)/i).slice(1).filter((p) => /^(OU|CN)=/i.test(p)).map((p) => p.replace(/^(OU|CN)=/i, ''));
  return parts.reverse().join('/');
}

function osShort(name) {
  if (!name) return '';
  const m = /Windows (Server \d{4}( R2)?|\d+)/i.exec(name);
  return m ? m[1] : name;
}

async function viewComputer(parts, params) {
  const id = Number(parts[0]);
  const c = await GET(`/api/computers/${id}`);
  let cat = params.get('cat') || params.get('tab') || 'computer';
  if (!PC_CATS.some((x) => x[0] === cat)) cat = 'computer';
  const e = c.extra || {};
  const n = c.counts || {};
  const more = btn('Tools ▾', (e) => toolsMenu(e.currentTarget, [{ id, name: c.name, online: c.online }]), { icon: 'terminal' });
  $('#topbar').replaceChildren(
    h('div.pc-title', h('div.pc-icon', icon('pc'), h('span.dot', { class: c.addr_problem ? 'warn' : (c.online ? 'on' : 'off') })),
      h('div', h('h1', c.name), h('small.muted', `${c.dns_name || ''}${c.resolved_ip ? ` (${c.resolved_ip})` : ''} · ${c.addr_problem ? tr('Adresse nicht bestätigt') : (c.online ? 'online' : 'offline')} · letzter Scan ${relTime(c.last_scan)}`))),
    h('div.spacer'),
    h('div.row',
      btn(c.scanning ? `Scan läuft (${c.scan_profile || '…'})` : 'Scannen ▾', (ev) => scanMenu(ev.currentTarget, [id]), { icon: 'refresh', disabled: c.scanning, title: 'Mit Scanprofil scannen' }),
      btn('Bereitstellen …', () => deployDlg({ computerIds: [id] }), { icon: 'send', cls: 'primary' }),
      btn('Verbindung testen', async () => {
        const r = await run(() => POST(`/api/computers/${id}/test`));
        if (r) showOutput('Verbindungstest', Object.entries(r).map(([k, v]) => `${k}: ${v}`).join('\n'));
      }, { icon: 'plug' }),
      btn('Remotedesktop', () => openRemote([{ id, name: c.name }], 'rdp'), { icon: 'monitor', title: 'RDP-Sitzung im Browser (VNC unter Tools)' }), more));

  const link = (k) => () => go(`#/computer/${id}?cat=${k}`);
  const nav = h('nav.pc-nav', PC_CATS.map(([k, label, ic, ck]) => {
    let badge = null;
    if (k === 'software' && n.software_outdated) badge = h('span.badge.warn', n.software_outdated);
    else if (k === 'devices' && n.device_problems) badge = h('span.badge.err', n.device_problems);
    else if (ck && n[ck]) badge = h('span.cnt', num(n[ck]));
    return h('a', { href: `#/computer/${id}?cat=${k}`, class: k === cat ? 'active' : '' }, h('span.ic', { 'data-ic': ic }, icon(ic)), h('span.lbl', label), badge);
  }));
  const main = h('div.pc-main');
  if (c.addr_problem) {
    // Unter der Adresse antwortet nicht dieser Computer: deutlich zeigen, warum er als offline gilt
    main.appendChild(h('div.card.guard', { style: { marginBottom: '12px' } }, h('div.card-b', h('div.row', { style: { gap: '10px', flexWrap: 'wrap' } },
      h('span.badge.warn', 'Adresse nicht bestätigt'), h('span', c.addr_detail || ''), h('div.spacer'),
      btn('Jetzt erneut prüfen', async () => {
        if (await run(() => POST('/api/computers/heartbeat', { ids: [id] }), 'Geprüft') !== undefined) route();
      }, { cls: 'small', icon: 'refresh' })),
      h('p.muted.small', { style: { margin: '6px 0 0' } }, 'Scans und Bereitstellungen laufen nicht, solange die Adresse nicht bestätigt ist. Meist ist der DNS-Eintrag veraltet: Computer einschalten (er registriert sich neu) oder den Eintrag im DNS der Domäne korrigieren; dauerhaft hilft die DNS-Bereinigung (Scavenging) auf den Domänencontrollern.'))));
  }
  setContent(h('div.pc', nav, main), true);
  const scrollTo = nav.querySelector('a.active');
  if (scrollTo) scrollTo.scrollIntoView({ block: 'nearest' });

  const title = (PC_CATS.find((x) => x[0] === cat) || ['', cat])[1];
  if (cat === 'computer') {
    main.appendChild(pcOverview(c, link));
  } else {
    main.appendChild(h('h2.pc-h', title));
    const box = h('div.pc-body', h('div.muted', 'Lade …'));
    main.appendChild(box);
    try { box.replaceChildren(await pcCategory(c, cat)); } catch (x) { box.replaceChildren(h('div.empty', 'Fehler: ' + x.message)); }
  }
  if (c.scanning) every(4000, async () => { const x = await GET(`/api/computers/${id}`); if (!x.scanning) route(); });
}

function liveBtn(c, what) {
  return btn('Live vom Computer laden', async () => {
    if (!c.online) toast('Computer ist laut Heartbeat offline – Versuch läuft trotzdem …');
    const r = await run(() => POST(`/api/computers/${c.id}/live/${what}`), (x) => `${plural(x.count, 'Eintrag', 'Einträge')} aktualisiert`);
    if (r) route();
  }, { icon: 'refresh' });
}

function serviceButtons(c, r) {
  const act = async (action, extra = {}) => {
    const labels = { start: 'starten', stop: 'beenden', restart: 'neu starten', startmode: 'Starttyp ändern' };
    if (action !== 'start' && action !== 'startmode' && !(await confirmDlg(`Dienst „${r.display_name || r.name}“ auf ${c.name} ${labels[action]}?`, 'Ausführen', action === 'stop'))) return;
    const res = await run(() => POST(`/api/computers/${c.id}/service`, { name: r.name, action, ...extra }), (x) => `${r.display_name || r.name}: ${x.state === 'Running' ? 'läuft' : x.state}`);
    if (res) route();
  };
  const mode = sel([['', 'Starttyp …'], ['auto', 'Automatisch'], ['manual', 'Manuell'], ['disabled', 'Deaktiviert']], '', {
    onchange: (e) => { const v = e.target.value; e.target.value = ''; if (v) act('startmode', { start_mode: v }); },
  });
  mode.classList.add('small-select');
  const running = r.state === 'Running';
  return h('div.row', { style: { flexWrap: 'nowrap' } },
    btn('Start', () => act('start'), { cls: 'small', disabled: running }),
    btn('Stopp', () => act('stop'), { cls: 'small danger', disabled: !running }),
    btn('Neustart', () => act('restart'), { cls: 'small', disabled: !running }), mode);
}

async function uninstallDlg(rows) {
  if (!rows.length) { toast('Keine Software ausgewählt', 'err'); return; }
  if (rows.length > 60) {
    // Lieber ehrlich begrenzen als stillschweigend nur einen Teil deinstallieren
    toast(`Bitte höchstens 60 Installationen auf einmal deinstallieren (ausgewählt: ${rows.length}). Für mehr ein Deinstallationspaket mit Zeitplan verwenden.`, 'err');
    return;
  }
  const infos = await Promise.all(rows.map((r) => GET(`/api/software/${r.id}/uninstall`).then((x) => ({ ...x, id: r.id }))));
  const single = infos.length === 1;
  const RUNAS = [['system', 'SYSTEM (Standard)'], ['user', 'Angemeldeter Benutzer (für Benutzer-Installationen)']];
  const cleanup = h('input', { type: 'checkbox' });
  const cleanupRow = h('label.chk', cleanup, 'Registry-Eintrag entfernen, wenn das Programm nicht mehr vorhanden ist (verwaister Eintrag)');
  let body; let cmd = null; let runAs = null; let rem = null; let pat = null;
  if (single) {
    const i = infos[0];
    cmd = h('textarea.mono', { rows: 3, value: i.command });
    runAs = sel(RUNAS, i.run_as);
    rem = h('input', { type: 'checkbox' });
    pat = h('input', { value: i.pattern, style: { maxWidth: '260px' } });
    body = h('div',
      h('div.form', field('Software', `${i.name} ${i.version || ''}`), field('Computer', i.computer),
        field('Erkannte Methode', h('span', h('span.badge', { class: i.review ? 'warn' : 'ok' }, i.method))),
        field('Deinstallationsbefehl', h('span.mono.small', i.uninstall_string || '–')),
        field('Stille Deinstallation', h('span.mono.small', i.quiet_uninstall || '–')),
        field('Registry', h('span.mono.small', i.hive ? `${i.hive}\\${i.reg_path}` : '–')),
        h('label', 'Wird ausgeführt'), cmd,
        i.review ? h('div.hint', h('b', 'Bitte prüfen: '), 'Für diesen Installer ist kein sicherer Silent-Schalter bekannt. Ohne passenden Schalter wartet die Deinstallation auf eine Eingabe, die niemand sieht, und läuft in das Zeitlimit.') : null,
        field('Ausführen als', runAs, i.user_sid ? 'Benutzer-Installation: läuft im Kontext des Benutzers, dem sie gehört – der Benutzer muss dafür angemeldet sein.' : 'SYSTEM genügt für Installationen für alle Benutzer.'),
        field('', cleanupRow),
        isOU() ? null : field('Merken', h('div.row', h('label.chk', rem, 'Befehl für Software'), pat, h('span.muted.small', 'merken')),
          'Gilt künftig für alle Computer. Platzhalter: * im Namen; im Befehl {UninstallString}, {ProductCode}, {InstallLocation}.')),
      h('p.muted.small', 'Die Deinstallation läuft wie eine Bereitstellung. Danach wird der Computer automatisch neu gescannt.'));
  } else {
    body = h('div', h('p', `${infos.length} Installationen werden still deinstalliert:`),
      h('table', h('thead', h('tr', h('th', 'Computer'), h('th', 'Software'), h('th', 'Methode'), h('th', 'Als'), h('th', 'Befehl'))),
        h('tbody', infos.map((i) => h('tr', h('td', i.computer), h('td', i.name), h('td', h('span.badge', { class: i.review ? 'warn' : 'ok' }, i.method)),
          h('td', i.run_as === 'user' ? 'Benutzer' : 'SYSTEM'), h('td.trunc.mono', { title: i.command }, i.command || '– kein Befehl –'))))),
      infos.some((i) => i.review) ? h('p.hint', 'Gelb markierte Einträge: Silent-Schalter bitte prüfen (einzeln deinstallieren, um den Befehl anzupassen und zu merken).') : null,
      h('div', { style: { marginTop: '8px' } }, cleanupRow));
  }
  modal(single ? 'Software deinstallieren' : `${infos.length} Installationen deinstallieren`, body, [
    { label: 'Abbrechen' },
    {
      label: 'Deinstallieren', cls: 'primary', icon: 'trash', onClick: async () => {
        const items = infos.filter((i) => cleanup.checked || (single ? cmd.value.trim() : i.command)).map((i) => ({
          software_id: i.id, command: single ? cmd.value : undefined, run_as: single ? runAs.value : undefined, cleanup: cleanup.checked,
          remember: single && rem.checked ? pat.value : undefined,
        }));
        if (!items.length) throw new Error('Kein Deinstallationsbefehl vorhanden.');
        const r = await POST('/api/uninstall', { items });
        toast(`Deinstallation gestartet (${r.deployments.length} Bereitstellung${r.deployments.length > 1 ? 'en' : ''})`, 'ok');
        go(r.deployments.length === 1 ? `#/deployment/${r.deployments[0]}` : '#/deployments');
      },
    },
  ], { wide: true });
}

async function secretsDlg(c, what) {
  const titles = { laps: 'LAPS-Kennwort', bitlocker: 'BitLocker-Wiederherstellungsschlüssel (AD)', bitlocker_client: 'BitLocker-Wiederherstellungsschlüssel (vom Computer)' };
  const r = await run(() => POST(`/api/computers/${c.id}/secrets/${what}`));
  if (!r) return;
  const secret = (val) => {
    const span = h('span.secret.mono.raw', '••••••••••••');
    let shown = false;
    const toggle = btn('Anzeigen', () => { shown = !shown; span.textContent = shown ? val : '••••••••••••'; toggle.lastChild.textContent = shown ? 'Verbergen' : 'Anzeigen'; }, { cls: 'small' });
    const copy = btn('Kopieren', async () => {
      try { await navigator.clipboard.writeText(val); toast('In die Zwischenablage kopiert', 'ok'); } catch (e) { span.textContent = val; toast('Kopieren nicht möglich – bitte markieren', 'err'); }
    }, { cls: 'small' });
    return h('span.row', span, toggle, copy);
  };
  let body;
  if (what === 'laps') {
    body = r.found && r.password
      ? h('div.form', field('Art', r.kind), field('Konto', r.account || ''), field('Kennwort', secret(r.password)),
        field('Gesetzt', fmtDate(r.updated)), field('Läuft ab', fmtDate(r.expires)))
      : h('div', r.kind ? h('p', h('b', r.kind)) : null, r.expires ? h('p', `Läuft ab: ${fmtDate(r.expires)}`) : null, h('p.muted', r.hint || 'Kein Kennwort gefunden.'));
  } else {
    const keys = r.keys || [];
    body = h('div', keys.length ? h('table', h('thead', h('tr', h('th', what === 'bitlocker' ? 'Erstellt' : 'Laufwerk'), h('th', 'Schlüssel-ID'), h('th', 'Wiederherstellungskennwort'))),
      h('tbody', keys.map((k) => h('tr', h('td', what === 'bitlocker' ? fmtDate(k.created) : k.volume), h('td.mono.small', k.id),
        h('td', k.password ? secret(k.password) : h('span.muted', 'nicht lesbar'))))))
      : null, r.hint ? h('p.muted', r.hint) : (!keys.length ? h('p.muted', 'Keine Schlüssel gefunden.') : null));
  }
  modal(`${titles[what]} – ${c.name}`, h('div', body, h('p.muted.small', { style: { marginTop: '12px' } }, 'Der Abruf wird im Protokoll (Kategorie „Kennwörter/Schlüssel“) mit Ihrem Benutzernamen festgehalten.')),
    [{ label: 'Schließen' }], { wide: true });
}

function fRow(label, value, onClick, coll) {
  const v = value instanceof Node ? value : (value === null || value === undefined ? '' : String(value));
  const fv = h('div.fval', { title: typeof v === 'string' ? v : undefined }, v);
  if (coll) collValue(fv, coll[0], coll[1]);
  return [onClick ? h('a.flabel', { href: '#', onclick: (ev) => { ev.preventDefault(); onClick(); } }, label) : h('span.flabel', label), fv];
}
function fSection(title, rows) {
  return h('section.fsec', h('h3.fsec-t', title), h('div.fgrid', rows));
}

function pcOverview(c, link) {
  const e = c.extra || {};
  const cpus = arr(c.cpus);
  const cpu = cpus[0];
  const cpuText = cpu ? `${cpu.cores}-Core ${cpu.max_mhz ? (cpu.max_mhz / 1000).toFixed(1).replace('.', ',') + ' GHz ' : ''}${cpu.name}`
    + (cpus.length > 1 ? ` (${cpus.length} Prozessoren)` : '') : (c.cpu || '');
  const mods = c.counts.memory;
  const ram = c.ram_bytes ? fmtBytes(c.ram_bytes) + (mods ? ` (${mods} Modul${mods > 1 ? 'e' : ''}${e.memory_slots ? ` in ${e.memory_slots} Steckplätzen` : ''})` : '') : '';
  const mac = (c.mac_addresses || '').split(',')[0];
  const ip = (c.ip_addresses || '').split(',')[0];
  const bl = arr(e.bitlocker).map((b) => `${b.mount} ${b.protection === 'On' ? 'geschützt' : 'ungeschützt'} (${b.percent ?? '?'}%)`).join(', ');
  const av = arr(e.antivirus).map((a) => `${a.name}${a.enabled ? '' : ' (aus)'}${a.up_to_date === false ? ' (veraltet)' : ''}`).join(', ');
  const fw = arr(e.firewall).map((f) => `${f.profile}: ${f.enabled ? 'ein' : 'AUS'}`).join(' · ');
  const last = c.last_scan_info || {};
  const online = h('span', h('span.dot', { class: c.online ? 'on' : 'off' }), ' ', c.online ? 'Ja' : 'Nein');
  const bool = (v) => (v === null || v === undefined ? '' : v ? 'Ja' : 'Nein');
  const warnBool = (v) => (v ? h('span.badge.warn', 'Ja') : v === false ? 'Nein' : '');

  // Angemeldeter Benutzer: Stand des letzten Scans, bei Online-Computern sofort live nachgefragt
  const userBox = h('span', c.current_user || (c.online ? h('span.muted', 'wird abgefragt …') : h('span.muted', 'niemand (laut letztem Scan)')));
  const userDn = h('span', e.user_display_name || '');
  if (c.online) {
    POST(`/api/computers/${c.id}/loggedon`).then((r) => {
      if (!r.live) { if (!c.current_user) userBox.replaceChildren(h('span.muted', 'nicht abfragbar')); return; }
      userBox.replaceChildren(r.user || h('span.muted', 'niemand angemeldet'));
      userDn.replaceChildren(r.display_name || '');
    }).catch(() => { if (!c.current_user) userBox.replaceChildren(''); });
  }
  const left = h('div.fcol',
    fSection('Allgemein', [
      fRow('Name', c.name), fRow('Beschreibung', c.description), fRow('Hostname', c.dns_name),
      fRow('Hinzugefügt', fmtDate(c.created_at)), fRow('Online', online), fRow('Betriebszeit', uptime(c.last_boot)),
      fRow('Startzeit', fmtDate(c.last_boot)), fRow('Angemeldeter Benutzer', userBox), fRow('↳ Anzeigename', userDn),
      fRow('IP-Adresse', ip, link('nics')), fRow('MAC-Adresse', mac, link('nics')), fRow('Zeitzone', e.timezone)]),
    fSection('Betriebssystem', [
      fRow('O/S', osShort(c.os_name || c.ad_os)), fRow('Name', c.os_name || c.ad_os, null, ['os', c.os_name || c.ad_os]),
      fRow('Version', c.os_version && c.os_build ? c.os_version.split('.').slice(0, 2).join('.') + '.' + c.os_build : c.os_version),
      fRow('SP / Release', c.os_display_version, null, ['os_display_version', c.os_display_version]), fRow('Installiert', fmtDate(c.os_install_date)), fRow('Seriennummer', e.os_serial),
      fRow('Systemlaufwerk', (e.system_drive || '').replace(':', ''), link('disks')), fRow('PowerShell-Version', e.ps_version, null, ['ps_version', e.ps_version]),
      fRow('SMBv1-Server aktiviert', warnBool(e.smb1_server)), fRow('SMBv1-Client aktiviert', warnBool(e.smb1_client)),
      fRow('IE-Version', e.ie_version), fRow('Architektur', c.os_arch, null, ['os_arch', c.os_arch]), fRow('.NET-Versionen', e.dotnet),
      fRow('BIOS-Modus', e.bios_mode, null, ['bios_mode', e.bios_mode]), fRow('Secure Boot', bool(e.secure_boot)),
      fRow('Letztes Windows-Update', fmtDate(e.last_wu_install), link('hotfixes')),
      fRow('Neustart erforderlich', e.pending_reboot ? h('span.badge.warn', 'Ja') : (c.last_scan ? 'Nein' : ''))]));

  const right = h('div.fcol',
    fSection('Active Directory', [
      fRow('Übergeordneter Pfad', ouPath(c.dn)), fRow('Beschreibung', c.description), fRow('Domäne', e.domain, null, ['domain', e.domain]),
      fRow('Domänencontroller', e.domain_controller), fRow('Standort', c.ad_location, null, ['location', c.ad_location]), fRow('Letzte Anmeldung', fmtDate(c.ad_last_logon)),
      fRow('Erstellt', fmtDate(c.ad_created)), fRow('Aktiviert', c.ad_enabled ? 'Ja' : h('span.badge.err', 'Nein')),
      fRow('Gruppen', c.counts.ad_groups ? `${c.counts.ad_groups} Gruppen` : '', link('ad_groups'))]),
    fSection('System', [
      fRow('Hersteller', c.manufacturer, null, ['manufacturer', c.manufacturer]), fRow('Modell', c.model, null, ['model', c.model]), fRow('Arbeitsspeicher', ram, link('memory')),
      fRow('Prozessor', cpuText, link('cpus'), ['cpu', c.cpu]), fRow('Gehäuse', e.chassis_name, null, ['chassis', e.chassis_name]), fRow('Seriennummer', c.serial),
      fRow('BIOS-Version', c.bios_version, null, ['bios_version', c.bios_version]), fRow('BIOS-Hersteller', e.bios_manufacturer, null, ['bios_manufacturer', e.bios_manufacturer]), fRow('BIOS-Datum', fmtDate(c.bios_date, false)),
      fRow('BIOS-Asset-Tag', e.asset_tag), fRow('Familie', e.family, null, ['family', e.family]), fRow('Version', e.product_version), fRow('SKU', e.sku, null, ['sku', e.sku]),
      fRow('UUID', e.uuid), fRow('TPM', e.tpm ? `Version ${(e.tpm.version || '').split(',')[0]}` : '', null, e.tpm ? ['tpm_version', e.tpm.version] : null)]),
    fSection('Sicherheit', [
      fRow('Defender', e.defender ? (e.defender.enabled ? 'aktiv' : h('span.badge.err', 'inaktiv')) : ''),
      fRow('Echtzeitschutz', e.defender ? (e.defender.realtime ? 'ein' : h('span.badge.err', 'aus')) : ''),
      fRow('Signatur', e.defender ? `${e.defender.signature || ''} (${e.defender.signature_age ?? '?'} Tage alt)` : ''),
      fRow('Antivirus', av), fRow('Firewall', fw), fRow('BitLocker', bl)]),
    h('section.fsec', h('h3.fsec-t', 'Kennwörter & Schlüssel'), h('div.row',
      btn('LAPS-Kennwort', () => secretsDlg(c, 'laps'), { icon: 'key', cls: 'small' }),
      btn('BitLocker-Schlüssel (AD)', () => secretsDlg(c, 'bitlocker'), { icon: 'shield', cls: 'small' }),
      btn('BitLocker vom Computer lesen', () => secretsDlg(c, 'bitlocker_client'), { cls: 'small', disabled: !c.online }))),
    fSection('Scan', [
      fRow('Scannen erlaubt', 'Ja'),
      fRow('Erfolgreicher Scan', c.last_scan ? `${relTime(c.last_scan)} (${fmtDate(c.last_scan)})` : 'nie', link('scans')),
      fRow('Letzter Versuch', fmtDate(c.last_scan_attempt)), fRow('Status', scanBadge(c)),
      fRow('Dauer', last.duration_ms ? `${(last.duration_ms / 1000).toFixed(1).replace('.', ',')} s` : ''),
      fRow('Scan-Benutzer', c.scan_user), fRow('Fehler', c.scan_error),
      fRow('Hinweise', last.warnings ? h('span.badge.warn', { title: last.warnings }, 'Teilweise unvollständig') : '', link('scans'))]));
  return h('div.fcols', left, right);
}

async function pcCategory(c, cat) {
  const id = c.id;
  const data = await GET(`/api/computers/${id}/cat/${cat}`);
  const tbl = (cols, rows, opt = {}) => {
    const ic = opt.coll || INV_COLL[cat];
    if (ic && !opt.onContext) {
      opt = { selectable: true, ...opt, onContext: (r, e, sx) => contextMenu(e.clientX, e.clientY, [
        dynCollMenu(ic[0], ic[1], sx, clickedKey(e), { rename: ic[2], bytes: ic[3] }), '-',
        { label: 'Wert kopieren', icon: 'coll', disabled: !clickedKey(e), onClick: () => copyText(String(r[clickedKey(e)] ?? '')) },
      ]) };
    }
    const t = table(cols, rows, { id: (r) => r, noun: 'Einträge', ...opt });
    return t;
  };
  const wrapT = (t) => h('div.pc-table', t.el);
  if (cat === 'software') {
    const outdated = data.filter((s) => s.outdated);
    const instDate = (r) => {
      const d = /^(\d{4})(\d{2})(\d{2})$/.exec(r.install_date || '');
      const date = d ? `${d[1]}-${d[2]}-${d[3]}` : '';
      if (r.modified && (!date || r.modified.startsWith(date))) return r.modified;
      return date || r.modified || '';
    };
    const t = tbl([
      { key: 'act', label: 'Aktion', nosort: true, noFilter: true, render: (r) => h('div.row', { style: { flexWrap: 'nowrap' } },
        r.outdated && r.winget_id ? btn('Aktualisieren', () => updateOutdated(r.winget_id, [id]), { cls: 'small' }) : null,
        btn('Deinstallieren', () => uninstallDlg([{ ...r, computer: c.name }]), { cls: 'small danger' })) },
      { key: 'name', label: 'Name' }, { key: 'version', label: 'Version' }, { key: 'publisher', label: 'Hersteller' },
      { key: 'installed', label: 'Installiert (Datum & Zeit)', value: instDate, render: (r) => fmtDate(instDate(r)) },
      { key: 'uninstall_string', label: 'Deinstallation', cls: 'trunc mono', title: (r) => r.uninstall_string },
      { key: 'quiet_uninstall', label: 'Stille Deinstallation', cls: 'trunc mono', title: (r) => r.quiet_uninstall },
      { key: 'hive', label: 'Registry-Hive' }, { key: 'reg_path', label: 'Registry-Pfad', cls: 'trunc mono', title: (r) => `${r.hive || ''}\\${r.reg_path || ''}` },
      { key: 'product_code', label: 'Schlüssel', cls: 'trunc mono', title: (r) => r.product_code },
      { key: 'install_location', label: 'Installationsort', cls: 'trunc', title: (r) => r.install_location },
      { key: 'size_kb', label: 'Größe', render: (r) => (r.size_kb ? fmtBytes(r.size_kb * 1024) : '') },
      { key: 'arch', label: 'Arch.' }, { key: 'scope', label: 'Bereich' },
      { key: 'winget_id', label: 'winget-ID', cls: 'mono' },
      { key: 'winget_latest', label: 'Aktuell', render: (r) => (r.outdated ? h('span.badge.warn', r.winget_latest) : (r.winget_latest || '')) },
    ], data, { sort: 'name', noun: 'Programme', selectable: true, id: (r) => r.id,
      onContext: (r, e, selRows) => contextMenu(e.clientX, e.clientY, [
        r.outdated && r.winget_id && selRows.length === 1 ? { label: 'Aktualisieren', icon: 'refresh', onClick: () => updateOutdated(r.winget_id, [id]) } : null,
        { label: selRows.length > 1 ? `${selRows.length} Programme deinstallieren …` : 'Jetzt deinstallieren …', icon: 'trash', onClick: () => uninstallDlg(selRows.map((x) => ({ ...x, computer: c.name }))) },
        selRows.length === 1 ? { label: 'Deinstallationspaket erstellen …', icon: 'box', onClick: () => uninstallPackageFrom(r) } : null,
        selRows.length === 1 && r.winget_id ? { label: 'Paket aus winget anlegen …', icon: 'box', onClick: () => packageDlg(null, { winget_id: r.winget_id, name: r.name }) } : null,
        '-',
        dynCollMenu('software', 'name', selRows, clickedKey(e)),
        { label: 'In der Softwareübersicht zeigen', icon: 'list', onClick: () => go(`#/software?q=${encodeURIComponent(r.name)}`) },
        { label: 'Namen kopieren', icon: 'coll', onClick: () => copyText(selRows.map((x) => x.name).join('\r\n')) },
      ]) });
    const bUn = btn('Auswahl deinstallieren', () => uninstallDlg(t.selected().map((r) => ({ ...r, computer: c.name }))), { cls: 'danger', icon: 'trash' });
    const bPkg = btn('Deinstallationspaket …', () => { const sx = t.selected(); if (sx.length !== 1) { toast('Bitte genau ein Programm auswählen', 'err'); return; } uninstallPackageFrom(sx[0]); },
      { icon: 'box', title: 'Aus dem gewählten Programm ein wiederverwendbares Deinstallationspaket erstellen (für andere Computer, Sammlungen oder Zeitpläne)' });
    t.actions.append(bUn, bPkg);
    if (outdated.length) {
      t.actions.append(btn(`Alle veralteten aktualisieren (${outdated.length})`, async () => {
        const ids = [...new Set(outdated.filter((s) => s.winget_id).map((s) => s.winget_id))];
        if (!(await confirmDlg(`${ids.length === 1 ? '1 Programm' : `${ids.length} Programme`} auf ${c.name} per winget aktualisieren?`, 'Aktualisieren'))) return;
        let ok = 0;
        for (const w of ids) if (await run(() => POST('/api/deployments', { winget_id: w, action: 'upgrade', computer_ids: [id] })) !== undefined) ok += 1;
        if (!ok) return;
        toast(ok === ids.length ? 'Bereitstellungen gestartet' : `${ok} von ${ids.length} Bereitstellungen gestartet`, ok === ids.length ? 'ok' : 'err');
        go(`#/computer/${id}?cat=deployments`);
      }, { cls: 'primary' }));
    }
    return wrapT(t);
  }
  if (cat === 'services') {
    const t = tbl([{ key: 'act', label: 'Aktion', nosort: true, noFilter: true, render: (r) => serviceButtons(c, r) }].concat(colsFrom(PC_COLS.services)),
      data, { sort: 'display_name', noun: 'Dienste' });
    t.actions.append(liveBtn(c, 'services'));
    return wrapT(t);
  }
  if (cat === 'processes') {
    const t = tbl([{ key: 'act', label: 'Aktion', nosort: true, noFilter: true, render: (r) => btn('Beenden', async () => {
      if (!(await confirmDlg(`Prozess „${r.name}“ (PID ${r.pid}) auf ${c.name} beenden?${r.user ? ' Benutzer: ' + r.user : ''}`, 'Beenden', true))) return;
      if (await run(() => POST(`/api/computers/${id}/process/kill`, { pid: r.pid, name: r.name }), `${r.name} beendet`) !== undefined) route();
    }, { cls: 'small danger' }) }].concat(colsFrom(PC_COLS.processes)), data, { sort: 'name', noun: 'Prozesse', empty: 'Keine Daten – „Live vom Computer laden“ klicken.' });
    t.actions.append(liveBtn(c, 'processes'));
    return wrapT(t);
  }
  if (cat === 'storage') {
    const open = (sid) => go(`#/diskusage/${id}${sid ? '?id=' + sid : ''}`);
    const scans = (data.scans || []);
    const t = tbl([
      { key: 'path', label: 'Pfad', render: (r) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); open(r.id); } }, r.path) },
      { key: 'total', label: 'Größe', cls: 'right', render: (r) => fmtBytes(r.total) },
      { key: 'ts', label: 'Analysiert', render: (r) => fmtDate(r.ts) },
      { key: 'created_by', label: 'Durch', render: (r) => r.created_by || '' },
    ], scans, { fill: false, noun: 'Analysen', sort: 'ts', dir: -1, key: null, empty: 'Noch keine Speicherplatz-Analyse. Der Scanner „Speicherplatz“ (Scanprofile) oder das Tool „Speicherplatz analysieren“ füllen diese Liste.' });
    return h('div.grid', h('div.row', h('h3', { style: { margin: 0 } }, 'Speicherplatz-Analysen'), h('div.spacer'),
      btn('Jetzt analysieren …', () => open(''), { cls: 'small primary', icon: 'db' })),
    h('p.muted.small', { style: { margin: '2px 0 0' } }, 'Als Scanner im Scanprofil „Speicherplatz“ (optional, nicht im Standardscan) oder einmalig über das Tool. Klick auf einen Pfad öffnet das Diagramm.'),
    wrapT(t));
  }
  if (cat === 'disks') {
    const pct = (r) => (r.size ? Math.round(100 - (100 * r.free) / r.size) : 0);
    const vol = tbl([{ key: 'drive', label: 'Laufwerk' }, { key: 'label', label: 'Bezeichnung' }, { key: 'fs', label: 'Dateisystem' },
      { key: 'size', label: 'Größe', render: R.bytes('size') }, { key: 'free', label: 'Frei', render: R.bytes('free') },
      { key: 'pct', label: 'Belegung', value: pct, render: (r) => h('div.row', h('div.progress', { style: { width: '120px' } },
        h('span', { class: pct(r) > 90 ? 'p-err' : 'p-run', style: { width: `${pct(r)}%` } })), `${pct(r)} %`) }],
    data.volumes, { tools: false, fill: false, coll: ['disks', 'drive'] });
    const phy = tbl([{ key: 'model', label: 'Modell' }, { key: 'media', label: 'Typ' }, { key: 'bus', label: 'Bus' },
      { key: 'interface', label: 'Schnittstelle' }, { key: 'size', label: 'Größe', render: R.bytes('size') },
      { key: 'health', label: 'Zustand', render: (r) => h('span.badge', { class: /Healthy|OK/i.test(r.health || '') ? 'ok' : 'warn' }, r.health || '') },
      { key: 'serial', label: 'Seriennummer', cls: 'mono' }, { key: 'firmware', label: 'Firmware' }, { key: 'partitions', label: 'Partitionen' }],
    data.physical, { tools: false, fill: false, coll: ['disks_physical', 'model', { size: 'size_gb' }], empty: 'Keine Daten (erst nach einem Scan mit Version 1.4)' });
    return h('div.grid', h('div.row', h('h3', { style: { margin: 0 } }, 'Volumes'), h('div.spacer'),
      btn('Speicherplatz analysieren …', () => go(`#/diskusage/${id}`), { cls: 'small', icon: 'db', title: 'Ordnergrößen als Diagramm – wo liegen die Speicherfresser?' })),
    wrapT(vol), h('h3', 'Physische Datenträger'), wrapT(phy));
  }
  if (cat === 'custom_fields') {
    // Eigene Angaben je Computer – werden nie vom Computer gelesen, nur hier eingetragen. Notizen (freier Text) gibt es
    // immer; Felder wie „Inventarnummer“ oder „Raum“ legt ein Hauptadmin einmal an (gelten dann für alle Computer und
    // lassen sich in Sammlungen und eigenen Berichten verwenden).
    const inputs = {};
    const notes = h('textarea', { rows: 5, style: { width: '100%' }, placeholder: 'Freier Text, z. B. Standort, Ansprechpartner, Besonderheiten …', value: c.notes || '' });
    const form = h('div.form', field('Notizen', notes, 'Nur in POCKET KNIFE gespeichert – wird nicht auf den Computer übertragen.'),
      ...data.map((f) => field(f.name, inputs[f.id] = f.type === 'bool'
        ? h('input', { type: 'checkbox', checked: ['1', 'true', 'ja'].includes(String(f.value || '').toLowerCase()) })
        : h('input', { value: f.value || '', type: f.type === 'number' ? 'number' : f.type === 'date' ? 'date' : 'text' }))));
    const save = btn('Speichern', async () => {
      const values = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.type === 'checkbox' ? (el.checked ? '1' : '') : el.value]));
      await run(async () => { await PUT(`/api/computers/${id}`, { notes: notes.value }); if (data.length) await PUT(`/api/computers/${id}/fields`, { values }); c.notes = notes.value; }, 'Gespeichert');
    }, { cls: 'primary' });
    let add = null;
    if (!isOU()) {
      // Neues Feld gleich hier anlegen (gilt für alle Computer) – ohne Umweg über die Einstellungen
      const fName = h('input', { placeholder: 'Neues Feld, z. B. Inventarnummer', style: { maxWidth: '260px' } });
      const fType = sel([['text', 'Text'], ['number', 'Zahl'], ['date', 'Datum']], 'text');
      add = h('div', { style: { marginTop: '16px' } }, h('div.muted.small', { style: { marginBottom: '6px' } },
        data.length ? 'Weiteres Feld für alle Computer anlegen (verwalten unter Einstellungen → Inventar & Felder):'
          : 'Noch keine eigenen Felder. Felder gelten für alle Computer – z. B. Inventarnummer, Raum, Kostenstelle, Garantie bis:'),
      h('div.row', fName, fType, btn('Feld anlegen', async () => {
        if (await run(() => POST('/api/fields', { name: fName.value, type: fType.value }), 'Feld angelegt') === undefined) return;
        S.schema = null; route();
      }, { cls: 'small' })));
    }
    return h('div.card', h('div.card-b', form, h('div.row.end', { style: { marginTop: '12px' } }, save), add));
  }
  if (cat === 'collections') {
    const t = tbl([{ key: 'name', label: 'Sammlung' }, { key: 'type', label: 'Typ', value: (r) => (r.type === 'static' ? 'Statisch' : 'Dynamisch') },
      { key: 'builtin', label: 'Standard', render: R.bool('builtin') }], data, { onRow: (r) => go(`#/computers?c=${r.id}`), noun: 'Sammlungen' });
    return wrapT(t);
  }
  if (cat === 'deployments') {
    return wrapT(tbl([{ key: 'deployment_id', label: '#' }, { key: 'name', label: 'Bereitstellung' },
      { key: 'status', label: 'Status', render: (r) => depBadge(r.status) }, { key: 'exit_code', label: 'Exitcode' },
      { key: 'message', label: 'Meldung', cls: 'wrap' }, { key: 'finished_at', label: 'Beendet', render: R.date('finished_at') }],
    data, { onRow: (r) => go(`#/deployment/${r.deployment_id}`), noun: 'Ziele' }));
  }
  if (['registry', 'files', 'wmi', 'powershell'].includes(cat)) {
    if (!data.length) {
      return h('div.empty', 'Für diese Kategorie ist noch kein eigener Scanner eingerichtet. ',
        h('a', { href: '#/scanners' }, 'Scanprofile → Eigene Scanner'), ' – einem Scanprofil zuordnen und den Computer damit scannen.');
    }
    const block = (sc) => {
      const ccols = customCols(sc.rows, sc.extra);
      const keys = ccols.map((c) => c.key);
      const t = tbl(ccols, sc.rows,
        { fill: false, coll: keys.length ? [`custom:${sc.id}`, keys[0], null, new Set(ccols.filter((c) => c.bytes).map((c) => c.key))] : null, empty: sc.updated ? 'Der Scanner hat beim letzten Scan nichts zurückgegeben.' : 'Noch nicht gescannt.' });
      return h('div', h('div.row', h('h3', { style: { margin: 0 } }, sc.name), h('span.mono.muted.small', sc.target.length > 80 ? sc.target.slice(0, 80) + '…' : sc.target), h('div.spacer'),
        h('span.muted.small', sc.updated ? `Stand: ${fmtDate(sc.updated)}` : 'noch nicht gescannt'),
        sc.profile_id ? btn(`Jetzt scannen („${sc.profile}“)`, async () => { await scanIds([id], sc.profile_id); }, { cls: 'small', icon: 'refresh' }) : null), wrapT(t));
    };
    if (data.length === 1) return h('div.grid', block(data[0]));
    // Viele Scanner: Auswahl per Dropdown (gemerkt je Kategorie), „Alle“ zeigt sie untereinander
    const lsKey = `pk-custom-sel-${cat}`;
    const sorted = [...data].sort((a, b) => a.name.localeCompare(b.name, 'de'));
    let cur = localStorageGet(lsKey);
    if (cur !== 'all' && !sorted.some((sc) => String(sc.id) === cur)) cur = String(sorted[0].id);
    const pick = h('select', { style: { minWidth: '260px' } },
      h('option', { value: 'all' }, `Alle Scanner (${sorted.length})`),
      sorted.map((sc) => h('option', { value: String(sc.id) }, `${sc.name} – ${sc.updated ? (sc.rows.length === 1 ? '1 Eintrag' : `${sc.rows.length} Einträge`) : 'noch nicht gescannt'}`)));
    pick.value = cur;
    const body = h('div.grid');
    const show = () => {
      cur = pick.value;
      try { localStorage.setItem(lsKey, cur); } catch (e) { /* egal */ }
      body.replaceChildren(...(cur === 'all' ? sorted : sorted.filter((sc) => String(sc.id) === cur)).map(block));
      idx.textContent = cur === 'all' ? '' : `${sorted.findIndex((sc) => String(sc.id) === cur) + 1} / ${sorted.length}`;
    };
    const step = (d) => {
      const i = sorted.findIndex((sc) => String(sc.id) === pick.value);
      pick.value = String(sorted[((i < 0 ? 0 : i + d) + sorted.length) % sorted.length].id); show();
    };
    const idx = h('span.muted.small');
    pick.addEventListener('change', show);
    const bar = h('div.row', { style: { gap: '8px', alignItems: 'center', marginBottom: '10px' } }, h('b', 'Scanner'), pick,
      btn('‹', () => step(-1), { cls: 'small', title: 'Vorheriger Scanner' }),
      btn('›', () => step(1), { cls: 'small', title: 'Nächster Scanner' }), idx);
    show();
    return h('div', bar, body);
  }
  if (cat === 'scans') {
    const pt = tbl([
      { key: 'name', label: 'Scanprofil', render: (r) => h('span', r.name, r.is_default ? h('span.badge.accent', { style: { marginLeft: '6px' } }, 'Standard') : null) },
      { key: 'member', label: 'Automatisch', render: (r) => (r.member ? h('span.badge.ok', 'ja') : h('span.muted', 'nur manuell')) },
      { key: 'last_scan', label: 'Letzter Scan', render: (r) => h('span', { title: fmtDate(r.last_scan) }, relTime(r.last_scan)) },
      { key: 'last_attempt', label: 'Letzter Versuch', render: (r) => fmtDate(r.last_attempt) },
      { key: 'status', label: 'Status', render: profStatus },
      { key: 'act', label: '', nosort: true, render: (r) => btn('Jetzt', async () => { await scanIds([id], r.id); }, { cls: 'small', icon: 'refresh' }) },
    ], data.profiles, { fill: false, search: false, noun: 'Profile', id: (r) => r.id });
    const ht = tbl(colsFrom(PC_COLS.scans), data.history, { sort: 'ts', dir: -1, noun: 'Scans', empty: 'Noch keine Scans.' });
    return h('div.grid', h('div', h('h3', 'Scanprofile'), wrapT(pt)), h('div', h('h3', 'Verlauf'), wrapT(ht)));
  }
  const def = PC_COLS[cat];
  if (!def) return h('div.empty', 'Keine Ansicht für diese Kategorie.');
  let rows = data;
  const opt = { noun: 'Einträge', empty: 'Keine Daten – ggf. Computer neu scannen.' };
  if (cat === 'scans') { opt.sort = 'ts'; opt.dir = -1; }
  const t = tbl(colsFrom(def), rows, opt);
  if (cat === 'tasks') {
    const all = rows;
    const cb = h('input', { type: 'checkbox', checked: true, onchange: () => t.setRows(cb.checked ? all.filter((r) => !r.microsoft) : all) });
    t.actions.append(h('label.chk', cb, 'Microsoft-Aufgaben ausblenden'));
    t.setRows(all.filter((r) => !r.microsoft));
  }
  if (cat === 'features') {
    const all = rows;
    const cb = h('input', { type: 'checkbox', checked: true, onchange: () => t.setRows(cb.checked ? all.filter((r) => ['Aktiviert', 'Installiert'].includes(r.state)) : all) });
    t.actions.append(h('label.chk', cb, 'nur aktivierte'));
    t.setRows(all.filter((r) => ['Aktiviert', 'Installiert'].includes(r.state)));
  }
  if (cat === 'devices') {
    const all = rows;
    const cb = h('input', { type: 'checkbox', checked: false, onchange: () => t.setRows(cb.checked ? all.filter((r) => r.problem_text) : all) });
    t.actions.append(h('label.chk', cb, 'nur Geräte mit Problemen'));
  }
  return wrapT(t);
}

function levelBadge(r) {
  return h('span.badge', { class: { ERROR: 'err', WARN: 'warn', INFO: 'info' }[r.level] || '' }, r.level);
}

// ---------------------------------------------------------------------------
// Ansicht: Software
// ---------------------------------------------------------------------------
async function viewSoftware(_p, params) {
  const cols = await collections();
  const cid = params.get('c') || '';
  const onlyOut = params.get('outdated') === '1';
  setTop('Software', 'Installierte Programme und Abgleich mit dem winget-Repository');
  let rows = await GET('/api/software' + (cid ? `?collection=${cid}` : ''));
  if (onlyOut) rows = rows.filter((r) => r.outdated > 0);
  const t = table([
    { key: 'name', label: 'Name', render: (r) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); softwareDlg(r); } }, r.name) },
    { key: 'publisher', label: 'Hersteller' },
    { key: 'installs', label: 'Installationen', cls: 'right' },
    { key: 'versions', label: 'Versionen', cls: 'right' },
    { key: 'winget_id', label: 'winget-ID', cls: 'mono', render: (r) => (r.ignored ? h('span.badge', 'ignoriert') : r.winget_id || '') },
    { key: 'winget_latest', label: 'Aktuell' },
    { key: 'outdated', label: 'Veraltet', render: (r) => (r.excluded ? h('span.badge', { title: 'Steht auf der Ausnahmeliste – wird nicht auf Aktualität geprüft' }, 'ausgenommen')
      : r.outdated ? h('span.badge.warn', `${r.outdated} PCs`) : (r.winget_id ? h('span.badge.ok', 'aktuell') : '')), value: (r) => (r.excluded ? 'ausgenommen' : r.outdated || '') },
    { key: 'act', label: '', nosort: true, noFilter: true, render: (r) => (r.outdated && r.winget_id ? btn('Aktualisieren', () => updateOutdated(r.winget_id), { cls: 'small' }) : '') },
  ], rows, { id: (r) => r.name, q: params.get('q') || '', sort: onlyOut ? 'outdated' : 'name', dir: onlyOut ? -1 : 1, noun: 'Programme', onRow: (r) => softwareDlg(r),
    selectable: true, onSelect: (sx) => updBtn(sx),
    onContext: (r, e, selRows) => contextMenu(e.clientX, e.clientY, [
      selRows.length === 1 ? { label: 'Details …', icon: 'list', onClick: () => softwareDlg(r) } : null,
      { label: `Aktualisieren (${selRows.filter((x) => x.outdated && x.winget_id).length} Programm(e))`, icon: 'refresh', disabled: !selRows.some((x) => x.outdated && x.winget_id), onClick: () => updateSelected(selRows) },
      selRows.length === 1 ? { label: 'Deinstallationspaket erstellen …', icon: 'trash', onClick: () => uninstallPackageFrom(r) } : null,
      selRows.length === 1 && r.winget_id ? { label: 'Paket aus winget anlegen …', icon: 'box', onClick: () => packageDlg(null, { winget_id: r.winget_id, name: r.name }) } : null,
      dynCollMenu('software', 'name', selRows, clickedKey(e)),
      '-',
      selRows.length === 1
        ? { label: 'Von der Aktualitätsprüfung ausnehmen …', icon: 'x', disabled: !!r.excluded, onClick: () => exclusionAddDlg({ field: 'name', pattern: r.name }) }
        : { label: `${selRows.length} Programme von der Aktualitätsprüfung ausnehmen`, icon: 'x', onClick: () => exclusionAddMany(selRows.map((x) => x.name)) },
      { label: 'Namen kopieren', icon: 'coll', onClick: () => copyText(selRows.map((x) => x.name).join('\r\n')) },
    ]) });
  async function updateSelected(selRows) {
    const todo = selRows.filter((x) => x.outdated && x.winget_id);
    if (!todo.length) { toast('In der Auswahl ist keine veraltete Software mit winget-Zuordnung.', 'err'); return; }
    const n = todo.reduce((a, x) => a + x.outdated, 0);
    if (!(await confirmDlg(`${todo.length} Programm(e) auf ${n} Installation(en) per winget aktualisieren?`, 'Aktualisieren'))) return;
    const r = await run(() => POST('/api/software/update', { names: todo.map((x) => x.name) }));
    if (!r) return;
    toast(`${r.deployments.length} Bereitstellung(en) für ${r.computers} Computer gestartet` + (r.skipped.length ? ` – ohne veraltete Installation: ${r.skipped.length}` : ''), 'ok');
    go(r.deployments.length === 1 ? `#/deployment/${r.deployments[0]}` : '#/deployments');
  }
  const bUpd = btn('Auswahl aktualisieren', () => updateSelected(t.selected()), { icon: 'refresh', cls: 'primary', title: 'Veraltete Installationen der ausgewählten Programme per winget aktualisieren' });
  function updBtn(sx) { bUpd.disabled = !sx.some((x) => x.outdated && x.winget_id); }
  updBtn([]);
  t.actions.append(bUpd);
  const csel = sel(collectionOptions(cols), cid, { onchange: (e) => go(`#/software?c=${e.target.value}${onlyOut ? '&outdated=1' : ''}`) });
  const ochk = h('label.chk', h('input', { type: 'checkbox', checked: onlyOut, onchange: (e) => go(`#/software?c=${cid}${e.target.checked ? '&outdated=1' : ''}`) }), 'nur veraltete');
  t.actions.append(csel, ochk, btn('Ausnahmen …', () => exclusionsDlg(), { cls: 'small', icon: 'list', title: 'Programme, die nicht auf Aktualität geprüft werden' }));
  setContent(h('div', { style: { height: '100%', display: 'flex' } }, h('div.pane.card', t.el)));
}

// --- Ausnahmen der Aktualitätsprüfung (eigene Liste) ----------------------------------------
const EXCL_HINT = 'Platzhalter: * (beliebig viele Zeichen) und ? (ein Zeichen), Groß-/Kleinschreibung egal. Beispiele: „Microsoft Visual C++*“, „*Runtime*“, Hersteller „Datev*“, winget-ID „Oracle.JavaRuntimeEnvironment“.';

async function exclusionAddDlg(preset = {}, after) {
  const meta = await GET('/api/software/exclusions');
  const fSel = sel(meta.fields.map((f) => [f.key, f.label]), preset.field || 'name');
  const pattern = h('input', { value: preset.pattern || '', placeholder: 'z. B. Microsoft Visual C++*' });
  const note = h('input', { value: preset.note || '', placeholder: 'optional, z. B. „Fachanwendung, Version vom Hersteller vorgegeben“' });
  const prev = h('div', h('p.muted.small', 'Muster eingeben – die Vorschau zeigt, welche Programme betroffen sind.'));
  let timer = null;
  const preview = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!pattern.value.trim()) { prev.replaceChildren(h('p.muted.small', 'Muster eingeben – die Vorschau zeigt, welche Programme betroffen sind.')); return; }
      try {
        const r = await POST('/api/software/exclusions/preview', { field: fSel.value, pattern: pattern.value });
        prev.replaceChildren(h('p.muted.small', r.total ? `Betrifft ${plural(r.total, fSel.value === 'name' ? 'Programm' : 'Wert', fSel.value === 'name' ? 'Programme' : 'Werte')}${r.total > r.hits.length ? ` (die ersten ${r.hits.length} angezeigt)` : ''}:` : 'Trifft derzeit auf kein installiertes Programm zu.'),
          r.hits.length ? h('div.chips', { style: { maxHeight: '180px', overflow: 'auto' } }, r.hits.map((x) => h('span.badge', { title: plural(x.computers, 'Computer', 'Computer') }, x.value))) : '');
      } catch (e) { prev.replaceChildren(h('p', h('span.badge.err', 'Fehler'), ' ', e.message)); }
    }, 250);
  };
  pattern.addEventListener('input', preview); fSel.addEventListener('change', preview);
  preview();
  modal('Von der Aktualitätsprüfung ausnehmen', h('div', h('div.form', field('Feld', fSel), field('Muster', pattern, EXCL_HINT), field('Notiz', note)), prev), [
    { label: 'Abbrechen' },
    { label: 'Ausnehmen', cls: 'primary', onClick: async () => {
      await POST('/api/software/exclusions', { field: fSel.value, pattern: pattern.value, note: note.value });
      toast('Ausnahme gespeichert – Abgleich läuft', 'ok');
      if (after) after(); else { const hh = location.hash; setTimeout(() => { if (location.hash === hh) route(); }, 1500); }
    } },
  ], { wide: true });
}

async function exclusionAddMany(names) {
  if (!(await confirmDlg(`${plural(names.length, 'Programm', 'Programme')} (exakter Name) von der Aktualitätsprüfung ausnehmen?`, 'Ausnehmen'))) return;
  let ok = 0;
  for (const n of names) {
    try { await POST('/api/software/exclusions', { field: 'name', pattern: n }); ok += 1; } catch (e) { /* schon vorhanden */ }
  }
  toast(`${plural(ok, 'Ausnahme', 'Ausnahmen')} gespeichert – Abgleich läuft`, 'ok');
  const hh = location.hash; setTimeout(() => { if (location.hash === hh) route(); }, 1500);
}

async function exclusionsDlg() {
  const body = h('div', h('p.muted', 'Wird geladen …'));
  let changed = false;
  async function draw() {
    const d = await GET('/api/software/exclusions');
    const t = table([
      { key: 'field_label', label: 'Feld' },
      { key: 'pattern', label: 'Muster', cls: 'mono' },
      { key: 'matches', label: 'Treffer', cls: 'right', render: (r) => (r.matches ? num(r.matches) : h('span.muted', '0')) },
      { key: 'note', label: 'Notiz', cls: 'trunc' },
      { key: 'created_at', label: 'Angelegt', render: (r) => h('span', { title: r.created_by || '' }, fmtDate(r.created_at)) },
      { key: 'act', label: '', nosort: true, noFilter: true, render: (r) => btn('', async () => {
        if (!(await askRemove(`Ausnahme „${r.pattern}“ entfernen? Die Programme werden danach wieder auf Aktualität geprüft.`))) return;
        if (await run(() => DEL(`/api/software/exclusions/${r.id}`), 'Entfernt – Abgleich läuft') !== undefined) { changed = true; draw(); }
      }, { icon: 'trash', cls: 'small ghost', title: 'Entfernen' }) },
    ], d.rules, { noun: 'Ausnahmen', fill: false, search: d.rules.length > 8, key: null, empty: 'Noch keine Ausnahmen. Mit „Neue Ausnahme“ oder per Rechtsklick auf ein Programm anlegen.' });
    t.actions.append(btn('Neue Ausnahme', () => exclusionAddDlg({}, () => { changed = true; draw(); }), { icon: 'plus', cls: 'small primary' }));
    body.replaceChildren(h('p.muted.small', { style: { marginTop: 0 } }, 'Programme auf dieser Liste gelten nie als „veraltet“: keine Anzeige unter „Veraltet“, nicht in der Sammlung „Veraltete Software“ und keine winget-Updates durch Zeitpläne. Die winget-Zuordnung bleibt sichtbar; gezielt bereitstellen geht weiterhin.'),
      t.el,
      d.ignored.length ? h('div', { style: { marginTop: '14px' } }, h('h3', 'Einzeln ignorierte Programme'),
        h('p.muted.small', 'Bei diesen Programmen wurde in den Details „Ignorieren“ gesetzt (ohne winget-Zuordnung).'),
        h('div.chips', d.ignored.map((x) => h('span.chip', x.name, h('button', { type: 'button', title: 'Nicht mehr ignorieren', onclick: async () => {
          if (!(await askRemove(`„${x.name}“ nicht mehr ignorieren?`))) return;
          if (await run(() => POST('/api/software/override', { name: x.name, remove: true }), 'Entfernt – Abgleich läuft') !== undefined) { changed = true; draw(); }
        } }, '×'))))) : '');
  }
  modal('Ausnahmen der Aktualitätsprüfung', body, [{ label: 'Schließen' }], { wide: true, help: 'software',
    onClose: () => { if (changed && parseHash().parts[0] === 'software') setTimeout(route, 1500); } });
  draw();
}

async function softwareDlg(sw) {
  const sw0 = sw;
  const d = await GET('/api/software/detail?name=' + encodeURIComponent(sw.name));
  const ov = d.override || {};
  const wid = h('input', { value: ov.winget_id || sw.winget_id || '', placeholder: 'z.B. Mozilla.Firefox' });
  const ign = h('input', { type: 'checkbox', checked: !!ov.ignore });
  const t = table([
    { key: 'name', label: 'Computer', render: (r) => h('a', { href: `#/computer/${r.id}` }, r.name) },
    { key: 'version', label: 'Version' }, { key: 'arch', label: 'Arch.' }, { key: 'scope', label: 'Bereich' },
    { key: 'outdated', label: 'Status', render: (r) => (r.outdated ? h('span.badge.warn', 'veraltet') : r.winget_id ? h('span.badge.ok', 'aktuell') : '') },
    { key: 'online', label: 'Online', render: (r) => h('span.dot', { class: r.online ? 'on' : 'off' }) },
  ], d.computers, { selectable: true, noun: 'Computer', fill: false });
  t.el.querySelector('.tbl-scroll').style.maxHeight = '320px';
  const outIds = d.computers.filter((c) => c.outdated).map((c) => c.id);
  modal(sw.name, h('div',
    h('div.form', field('winget-ID (Zuordnung)', h('div.row', wid, btn('Suchen', () => wingetPicker((id) => (wid.value = id)), { cls: 'small' })),
      'Automatisch ermittelt. Hier manuell festlegen, falls die Zuordnung falsch ist.'),
    field('Ignorieren', h('label.chk', ign, 'keine winget-Zuordnung (nicht auf Aktualität prüfen)')),
    field('Ausnahmeliste', d.exclusion
      ? h('span', h('span.badge', 'ausgenommen'), ` durch ${({ name: 'Name', publisher: 'Hersteller', winget_id: 'winget-ID' })[d.exclusion.field] || d.exclusion.field} „${d.exclusion.pattern}“ `,
        btn('Liste öffnen …', () => exclusionsDlg(), { cls: 'small ghost' }))
      : btn('Von der Aktualitätsprüfung ausnehmen …', () => exclusionAddDlg({ field: 'name', pattern: sw.name }), { cls: 'small' }),
    'Eigene Liste von Programmen, die nie als „veraltet“ gelten (mit Platzhaltern, z. B. „Microsoft Visual C++*“).')),
    h('div.row', { style: { margin: '14px 0 8px' } }, h('h3', 'Installationen'), h('div.spacer'),
      btn('Auswahl aktualisieren', async () => {
        const ids = t.selectedIds();
        if (!ids.length) { toast('Bitte Computer auswählen', 'err'); return; }
        if (!wid.value) { toast('Keine winget-Zuordnung – bitte winget-ID festlegen.', 'err'); return; }
        const r = await run(() => POST('/api/software/update', { names: [sw0.name], computer_ids: ids }));
        if (r && r.deployments.length) go(`#/deployment/${r.deployments[0]}`);
        else if (r) toast('Keine passenden Installationen (winget wird auf dem Betriebssystem evtl. nicht unterstützt).', 'err');
      }, { cls: 'small', icon: 'refresh' }),
      btn('Auswahl bereitstellen …', () => deployDlg({ computerIds: t.selectedIds(), wingetId: wid.value }), { cls: 'small' }),
      btn('Deinstallationspaket …', () => uninstallPackageFrom(sw0), { cls: 'small', icon: 'box', title: 'Wiederverwendbares Paket, das dieses Programm auf beliebigen Computern entfernt' }),
      btn('Auswahl deinstallieren …', async () => {
        const ids = t.selectedIds();
        if (!ids.length) { toast('Bitte Computer auswählen', 'err'); return; }
        const rows = [];
        for (const cid of ids) {
          const sw = (await GET(`/api/computers/${cid}/cat/software`)).filter((x) => x.name === sw0.name);
          const cn = (d.computers.find((x) => x.id === cid) || {}).name;
          sw.forEach((x) => rows.push({ ...x, computer: cn }));
        }
        uninstallDlg(rows);
      }, { cls: 'small danger' }),
      outIds.length && sw.winget_id ? btn(`Veraltete aktualisieren (${outIds.length})`, () => updateOutdated(sw.winget_id, outIds), { cls: 'small primary' }) : null),
    t.el), [
    ov.name ? { label: 'Zuordnung entfernen', onClick: async () => { await POST('/api/software/override', { name: sw.name, remove: true }); toast('Entfernt – Abgleich läuft', 'ok'); const hh = location.hash; setTimeout(() => { if (location.hash === hh) route(); }, 2000); } } : null,
    { label: 'Schließen' },
    { label: 'Zuordnung speichern', cls: 'primary', onClick: async () => { await POST('/api/software/override', { name: sw.name, winget_id: wid.value.trim(), ignore: ign.checked }); toast('Gespeichert – Abgleich läuft', 'ok'); const hh = location.hash; setTimeout(() => { if (location.hash === hh) route(); }, 2000); } },
  ].filter(Boolean), { wide: true });
}

function wingetPicker(onPick) {
  const q = h('input', { type: 'search', placeholder: 'Name oder ID, z.B. firefox' });
  const list = h('div.list-pick');
  let timer = null;
  const m = modal('winget-Paket suchen', h('div', q, h('div', { style: { height: '10px' } }), list), [{ label: 'Abbrechen' }]);
  q.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const r = await run(() => GET('/api/winget/search?q=' + encodeURIComponent(q.value)));
      list.replaceChildren(...(r || []).map((p) => h('div', { onclick: () => { onPick(p.id, p); m.close(); } },
        h('b', p.name), ' ', h('span.mono.muted', p.id), h('span.muted', ` · ${p.latest || ''}`))));
      if (r && !r.length) list.replaceChildren(h('div.muted', 'Nichts gefunden (ist der winget-Index geladen?)'));
    }, 250);
  });
}

// ---------------------------------------------------------------------------
// Ansicht: Pakete
// ---------------------------------------------------------------------------
const PKG_TYPES = { winget: 'winget', custom: 'Eigenes Paket' };
const INST_TYPES = [['msi', 'MSI (msiexec /i)'], ['exe', 'EXE-Installer'], ['msp', 'MSP-Patch'], ['msu', 'MSU-Update (wusa)'],
  ['cmd', 'Batch (.cmd/.bat)'], ['ps1', 'PowerShell-Datei (.ps1)'], ['script', 'PowerShell-Skript (inline, ohne Dateien)']];

// ---------------------------------------------------------------------------
// Pakete: Liste, Editor mit Schritten und Bedingungen
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// .reg-Dateien (Windows Registry Editor 5.00 / REGEDIT4) in Registry-Einträge umwandeln
// ---------------------------------------------------------------------------
function decodeRegFile(buf) {
  const b = new Uint8Array(buf);
  if (b[0] === 0xff && b[1] === 0xfe) return new TextDecoder('utf-16le').decode(b.subarray(2));
  if (b[0] === 0xfe && b[1] === 0xff) return new TextDecoder('utf-16be').decode(b.subarray(2));
  if (b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) return new TextDecoder('utf-8').decode(b.subarray(3));
  // Ohne BOM: UTF-16LE erkennen (jedes zweite Byte 0), sonst UTF-8 bzw. Windows-1252
  if (b.length > 4 && b[1] === 0 && b[3] === 0) return new TextDecoder('utf-16le').decode(b);
  try { return new TextDecoder('utf-8', { fatal: true }).decode(b); } catch (e) { return new TextDecoder('windows-1252').decode(b); }
}

function parseRegText(text) {
  const entries = [];
  const warn = [];
  const HIVE = { HKEY_LOCAL_MACHINE: 'HKLM', HKEY_CURRENT_USER: 'HKCU', HKEY_USERS: 'HKU', HKEY_CLASSES_ROOT: 'HKCR', HKEY_CURRENT_CONFIG: 'HKCC',
    HKLM: 'HKLM', HKCU: 'HKCU', HKU: 'HKU', HKCR: 'HKCR', HKCC: 'HKCC' };
  const normKey = (k) => { const [h0, ...rest] = k.split('\\'); return (HIVE[h0.toUpperCase()] || h0) + (rest.length ? '\\' + rest.join('\\') : ''); };
  // Fortsetzungszeilen (… \ am Zeilenende) zusammenfügen
  const lines = [];
  let buf = '';
  for (const raw of text.replace(/\r/g, '').split('\n')) {
    const l = buf ? raw.trim() : raw;
    if (/\\$/.test(l) && !/^\s*\[/.test(l) && /=\s*hex/i.test(buf + l)) { buf += l.slice(0, -1); continue; }
    lines.push(buf + l); buf = '';
  }
  if (buf) lines.push(buf);
  const bytes = (s) => s.split(',').map((x) => x.trim()).filter(Boolean).map((x) => parseInt(x, 16));
  const utf16 = (bs) => { let out = ''; for (let i = 0; i + 1 < bs.length; i += 2) out += String.fromCharCode(bs[i] | (bs[i + 1] << 8)); return out; };
  const unq = (s) => s.replace(/\\(["\\])/g, '$1');
  let key = null; let keyHasValues = false;
  const flushKey = () => { if (key && !keyHasValues) entries.push({ action: 'create_key', path: key }); };
  for (const l0 of lines) {
    const l = l0.trim();
    if (!l || l.startsWith(';') || /^Windows Registry Editor/i.test(l) || /^REGEDIT4$/i.test(l)) continue;
    let m = /^\[(-?)([^\]]+)\]$/.exec(l);
    if (m) {
      flushKey();
      if (m[1]) { entries.push({ action: 'delete_key', path: normKey(m[2].trim()) }); key = null; keyHasValues = true; } else { key = normKey(m[2].trim()); keyHasValues = false; }
      continue;
    }
    m = /^(@|"((?:[^"\\]|\\.)*)")\s*=\s*(.*)$/.exec(l);
    if (!m || !key) { warn.push(`Nicht erkannt: ${l.slice(0, 80)}`); continue; }
    keyHasValues = true;
    const name = m[1] === '@' ? '' : unq(m[2]);
    const v = m[3].trim();
    let e = null;
    if (v === '-') e = { action: 'delete_value', path: key, name };
    else if (/^"/.test(v)) e = { action: 'set', path: key, name, kind: 'REG_SZ', data: unq(v.replace(/^"|"$/g, '')) };
    else if (/^dword:/i.test(v)) e = { action: 'set', path: key, name, kind: 'REG_DWORD', data: String(parseInt(v.slice(6), 16)) };
    else {
      const hm = /^hex(?:\(([0-9a-f]+)\))?:(.*)$/i.exec(v);
      if (!hm) { warn.push(`Werttyp nicht unterstützt: ${name || '(Standard)'} = ${v.slice(0, 40)}`); continue; }
      const t = (hm[1] || '3').toLowerCase();
      const bs = bytes(hm[2]);
      if (t === '2') e = { action: 'set', path: key, name, kind: 'REG_EXPAND_SZ', data: utf16(bs).replace(/\0+$/, '') };
      else if (t === '7') e = { action: 'set', path: key, name, kind: 'REG_MULTI_SZ', data: utf16(bs).replace(/\0+$/, '').split('\0').join('\n') };
      else if (t === 'b') { let n = 0n; bs.slice(0, 8).forEach((x, i) => { n += BigInt(x) << BigInt(8 * i); }); e = { action: 'set', path: key, name, kind: 'REG_QWORD', data: n.toString() }; }
      else if (t === '4') { let n = 0; bs.slice(0, 4).forEach((x, i) => { n += x * 2 ** (8 * i); }); e = { action: 'set', path: key, name, kind: 'REG_DWORD', data: String(n) }; }
      else if (t === '3' || t === '0') e = { action: 'set', path: key, name, kind: 'REG_BINARY', data: bs.map((x) => x.toString(16).padStart(2, '0')).join(',') };
      else { warn.push(`Werttyp hex(${t}) nicht unterstützt: ${name || '(Standard)'}`); continue; }
    }
    entries.push(e);
  }
  flushKey();
  return { entries, warn };
}

function regImportDlg(onDone) {
  const area = h('textarea.mono', { rows: 14, spellcheck: false, placeholder: 'Windows Registry Editor Version 5.00\n\n[HKEY_LOCAL_MACHINE\\SOFTWARE\\Firma\\App]\n"Server"="srv01"\n"Port"=dword:00001f90' });
  const fp = filePick('.reg,.txt', '.reg-Datei wählen …');
  const info = h('div.muted.small');
  fp.el.querySelector('input').addEventListener('change', async () => {
    const f = fp.file();
    if (f) area.value = decodeRegFile(await f.arrayBuffer());
    area.dispatchEvent(new Event('input'));
  });
  area.addEventListener('input', () => {
    const r = parseRegText(area.value);
    info.textContent = area.value.trim() ? `${plural(r.entries.length, 'Eintrag', 'Einträge')} erkannt${r.warn.length ? ` · ${r.warn.length} Hinweis(e): ${r.warn.slice(0, 3).join(' | ')}` : ''}` : '';
  });
  modal('Registry-Einträge importieren', h('div.form.one',
    h('p.muted.small', { style: { margin: 0 } }, 'Inhalt einer .reg-Datei einfügen oder die Datei wählen (z. B. aus regedit → Exportieren). Schlüssel, Werte (String, DWORD, QWORD, Binär, erweiterbar, mehrteilig) und Löschungen ([-Schlüssel], "Wert"=-) werden übernommen.'),
    fp.el, area, info), [{ label: 'Abbrechen' }, { label: 'Übernehmen', cls: 'primary', onClick: () => {
    const r = parseRegText(area.value);
    if (!r.entries.length) { toast('Keine Einträge erkannt', 'err'); return false; }
    onDone(r.entries);
    toast(`${plural(r.entries.length, 'Eintrag', 'Einträge')} übernommen`, 'ok');
  } }], { wide: true });
}

const STEP_ICON = { install: 'box', registry: 'db', uninstall: 'trash', command: 'terminal', powershell: 'terminal', nested: 'coll', filecopy: 'folder', scan: 'refresh',
  reboot: 'power', sleep: 'clock', message: 'user', logoff: 'x' };
let PKG_META = null;
async function pkgMeta(force) { if (!PKG_META || force) PKG_META = await GET('/api/packages/meta'); return PKG_META; }

async function viewPackages(_p, params) {
  const [rows, meta] = await Promise.all([GET('/api/packages'), pkgMeta(true)]);
  let t = null;
  const bDeploy = btn('Bereitstellen …', () => deployDlg({ packageIds: t ? t.selectedIds() : [] }), { icon: 'send', title: 'Die ausgewählten Pakete bereitstellen (mehrere: je Computer nacheinander)' });
  const bExport = btn('Exportieren', () => {
    const ids = t && t.selectedIds().length ? t.selectedIds() : t.filteredRows().map((r) => r.id);
    exportPackages(ids);
  }, { icon: 'download', title: 'Ausgewählte Pakete (ohne Auswahl: alle angezeigten) als Datei speichern – z. B. für eine andere POCKET-KNIFE-Installation' });
  setTop('Pakete', 'Pakete aus Schritten – winget, Dateien aus dem Repository, Befehle, Skripte',
    bDeploy, bExport, mayBuild() ? btn('Importieren …', () => importPackagesDlg(meta), { icon: 'up', title: 'Pakete aus einer Export-Datei übernehmen' }) : null,
    mayBuild() ? btn('Neues Paket', () => packageDlg(null), { icon: 'plus', cls: 'primary' }) : null);
  const tenantSel = sel([['', 'Alle Mandanten'], ['0', 'Ohne Mandant (global)'], ...meta.tenants.map((t) => [t.id, t.name])], params.get('tenant') || '',
    { onchange: (e) => go(`#/packages${e.target.value ? '?tenant=' + e.target.value : ''}`) });
  const tf = params.get('tenant');
  const shown = tf === null || tf === '' ? rows : rows.filter((r) => String(r.tenant_id || 0) === tf);
  const ITYPE = { msi: 'MSI', exe: 'EXE', msp: 'MSP', msu: 'MSU', cmd: 'CMD', ps1: 'PS1', script: 'PowerShell' };
  const kind = (r) => (r.type === 'winget' ? 'winget' : ITYPE[r.install_type] || (r.step_types.includes('uninstall') ? 'Deinstallation' : r.step_types.includes('powershell') ? 'PowerShell' : r.step_types.includes('command') ? 'Befehl' : 'Schritte'));
  t = table([
    { key: 'name', label: 'Name', render: (r) => h('span', h('a', { href: '#', onclick: (e) => { e.preventDefault(); packageDlg(r.id); } }, r.name),
      r.tenant_name ? h('span.badge', { style: { marginLeft: '6px' } }, r.tenant_name) : null), value: (r) => r.name, title: (r) => r.catalog_name || r.description || '' },
    { key: 'version', label: 'Version', value: (r) => (r.type === 'winget' ? (r.winget_version || (r.latest ? `${r.latest} (neueste)` : 'neueste')) : r.version || ''),
      title: (r) => (r.type === 'winget' && !r.winget_version ? 'Ohne feste Version wird immer die aktuelle Katalogversion installiert.' : '') },
    { key: 'latest', label: 'Katalog', value: (r) => r.latest || '', title: () => 'Neueste Version im winget-Katalog' },
    { key: 'kind', label: 'Typ', render: (r) => h('span.badge', { class: r.type === 'winget' ? 'info' : 'accent' }, kind(r)), value: kind },
    { key: 'step_count', label: 'Schritte', cls: 'right' },
    { key: 'run_as_label', label: 'Ausführen als' },
    { key: 'integrity', label: 'Zustand', cls: 'center', value: (r) => integText(r.integrity), render: (r) => integBadge(r.integrity),
      sort: (r) => ({ broken: 0, warn: 1, unknown: 2, ok: 3 })[(r.integrity || {}).state] ?? 4,
      title: () => 'Repository-Dateien des Pakets: ✓ unverändert, ! Abweichung (Lauf geht weiter), ✕ Bruch (Bereitstellungen werden unterbrochen), ? nicht prüfbar' },
    { key: 'notify', label: 'Benachrichtigung', cls: 'center', value: (r) => (r.notify_effective ? tr('ja') : ''), sort: (r) => (r.notify_effective ? 1 : 0),
      title: (r) => (r.notify_effective ? tr(r.notify_mode === 'on' ? 'Windows-Benachrichtigung am Client: immer (im Paket eingestellt)' : 'Windows-Benachrichtigung am Client: ja (wie in den Einstellungen)')
        : tr(r.notify_mode === 'off' ? 'Keine Benachrichtigung (im Paket ausgeschaltet)' : 'Keine Benachrichtigung (in den Einstellungen aus oder das Paket installiert nichts)')),
      render: (r) => (r.notify_effective ? h('span', { style: { color: 'var(--ok)', fontWeight: 700, fontSize: '15px' } }, '✓') : '') },
    { key: 'src', label: 'Quelle', cls: 'mono trunc', value: (r) => (r.type === 'winget' ? r.winget_id : r.install_file ? `${r.folder ? r.folder + '\\' : ''}${r.install_file}` : '') },
    { key: 'conditions_text', label: 'Bedingungen', cls: 'trunc', value: (r) => r.conditions_text.join(' · ') || 'alle', hidden: true },
    { key: 'tenant_name', label: 'Mandant', value: (r) => r.tenant_name || '', hidden: true },
    { key: 'installs', label: 'Installiert', cls: 'right', value: (r) => (r.type === 'winget' ? r.installs : ''), sort: (r) => r.installs || 0 },
    { key: 'outdated_installs', label: 'Veraltet', render: (r) => (r.outdated_installs ? h('a.badge.warn', { href: `#/software?outdated=1&q=${encodeURIComponent(r.winget_id || '')}` }, r.outdated_installs) : ''), value: (r) => r.outdated_installs || '', sort: (r) => r.outdated_installs || 0 },
    { key: 'updated_at', label: 'Geändert', value: (r) => fmtDate(r.updated_at), sort: (r) => r.updated_at || '' },
    { key: 'act', label: '', nosort: true, noFilter: true, render: (r) => btn('Bereitstellen', () => deployDlg({ packageId: r.id }), { cls: 'small' }) },
  ], shown, { key: 'packages23', sort: 'name', noun: 'Pakete', selectable: true, onRow: (r) => packageDlg(r.id), empty: 'Noch keine Pakete – „Neues Paket“ anlegen.',
    onSelect: (sx) => { bDeploy.lastChild.textContent = tr(sx.length > 1 ? `${sx.length} Pakete bereitstellen …` : 'Bereitstellen …'); bDeploy.classList.toggle('primary', sx.length > 0); },
    onContext: (r, e, selRows) => contextMenu(e.clientX, e.clientY, selRows && selRows.length > 1 ? [
      { label: `${selRows.length} Pakete bereitstellen …`, icon: 'send', onClick: () => deployDlg({ packageIds: selRows.map((x) => x.id) }) },
      { label: 'Zeitplan anlegen …', icon: 'clock', onClick: () => scheduleDlg(null, { packages: selRows.map((x) => x.id) }) },
      { label: `${selRows.length} Pakete exportieren`, icon: 'download', onClick: () => exportPackages(selRows.map((x) => x.id)) },
    ] : [
      { label: 'Bearbeiten', icon: 'edit', hint: 'Doppelklick', onClick: () => packageDlg(r.id) },
      { label: 'Bereitstellen …', icon: 'send', onClick: () => deployDlg({ packageId: r.id }) },
      { label: 'Zeitplan anlegen …', icon: 'clock', onClick: () => scheduleDlg(null, { packages: [r.id] }) },
      { label: 'Duplizieren', icon: 'coll', onClick: async () => { if (await run(() => POST(`/api/packages/${r.id}/duplicate`), 'Paket dupliziert')) route(); } },
      { label: 'Exportieren', icon: 'download', onClick: () => exportPackages([r.id]) },
      r.type === 'winget' && r.outdated_installs ? { label: `Veraltete Installationen anzeigen (${r.outdated_installs})`, icon: 'list', onClick: () => go(`#/software?outdated=1&q=${encodeURIComponent(r.winget_id)}`) } : null,
      '-',
      { label: 'Löschen', icon: 'trash', onClick: async () => {
        if (!(await confirmDlg(`Paket „${r.name}“ löschen?`, 'Löschen', true))) return;
        if (await run(() => DEL(`/api/packages/${r.id}`), 'Gelöscht') !== undefined) route();
      } },
    ]) });
  if (meta.tenants.length) t.actions.append(tenantSel);
  setContent(h('div', { style: { height: '100%', display: 'flex' } }, h('div.pane.card', t.el)));
  // Nur die Zeilen auffrischen – Filter, Auswahl und Scrollposition bleiben erhalten
  every(60000, async () => {
    if (parseHash().parts[0] !== 'packages') return;
    const fresh = await GET('/api/packages');
    t.setRows(tf === null || tf === '' ? fresh : fresh.filter((r) => String(r.tenant_id || 0) === tf));
  });
}

// --- Pakete exportieren / importieren ------------------------------------------------------
function saveTextFile(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const a = h('a', { href: url, download: name, style: { display: 'none' } });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

async function exportPackages(ids) {
  if (!ids.length) { toast(tr('Keine Pakete gewählt.'), 'err'); return; }
  const r = await run(() => POST('/api/packages/export', { ids }));
  if (!r) return;
  const save = () => saveTextFile(r.filename, JSON.stringify(r.data, null, 1));
  const n = r.data.packages.length;
  if (!r.warnings.length && !r.extra.length) { save(); toast(fmtT('{0} Paket(e) exportiert', n), 'ok'); return; }
  modal(tr('Pakete exportieren'), h('div',
    h('p', fmtT('{0} Paket(e) werden exportiert.', n)),
    r.extra.length ? h('p.muted', fmtT('Mit dabei, weil sie verschachtelt verwendet werden: {0}', r.extra.join(', '))) : null,
    r.warnings.length ? h('div', h('b', tr('Bitte prüfen:')), h('ul', r.warnings.map((w) => h('li.raw', w))),
      h('p.muted.small', tr('Repository-Pfade in Paketen sind immer relativ und passen nach dem Import automatisch zum dort eingestellten Repository. Nur Pfade, die fest in Befehlen oder Skripten stehen, bleiben unverändert.'))) : null,
    h('p.muted.small', tr('Skripte und Befehle stehen in der Datei so, wie sie im Paket stehen – Kennwörter gehören nicht in Skripte.'))),
  [{ label: 'Abbrechen' }, { label: 'Datei speichern', cls: 'primary', onClick: () => save() }], { wide: true });
}

function importPackagesDlg(meta) {
  let data = null;
  const file = h('input', { type: 'file', accept: '.json,application/json' });
  const tenants = meta.tenants || [];
  const tenant = sel([...(isOU() ? [] : [['', 'Zentral (ohne Mandant)']]), ...tenants.map((x) => [x.id, x.name])], isOU() && tenants[0] ? tenants[0].id : '');
  const conflict = sel([['skip', 'Überspringen (vorhandenes Paket bleibt, wird für Verschachtelungen verwendet)'], ['copy', 'Zusätzlich anlegen als „… (Import)“']], 'skip');
  const out = h('div', h('p.muted', tr('Eine Datei *.pkpackages.json wählen (Pakete → Exportieren).')));
  let last = null, gen = 0, done = false;
  const STATUS = { new: ['ok', 'neu'], copy: ['info', 'als Kopie'], skip: ['', 'übersprungen'], error: ['err', 'Fehler'] };
  const showResult = (r, head) => {
    const c = r.count;
    out.replaceChildren(
      h('p', head || fmtT('{0} neu, {1} als Kopie, {2} übersprungen, {3} mit Fehler', c.new, c.copy, c.skip, c.error)),
      h('div', { style: { maxHeight: '45vh', overflow: 'auto' } },
        h('table.tbl', { style: { width: '100%', tableLayout: 'fixed' } },
          h('thead', h('tr', h('th', { style: { width: '42%' } }, 'Paket'), h('th', { style: { width: '110px' } }, 'Ergebnis'), h('th', 'Hinweis'))),
          h('tbody', r.items.map((x) => h('tr', h('td.wrap.raw', x.status === 'copy' ? `${x.name} → ${x.new_name}` : x.name),
            h('td', h('span.badge', { class: STATUS[x.status][0] }, tr(STATUS[x.status][1]))),
            h('td.wrap.raw', [x.message, ...(x.warnings || [])].filter(Boolean).join(' · '))))))),
      c.error ? h('p.muted.small', tr('Pakete mit Fehler (und Pakete, die sie verschachtelt verwenden) werden ausgelassen.')) : null);
  };
  async function check() {
    if (!data || done) return;
    const my = ++gen;
    last = null;  // „Importieren“ erst, wenn die Prüfung zur aktuellen Einstellung da ist
    out.replaceChildren(h('div.muted', tr('Wird geprüft …')));
    const r = await run(() => POST('/api/packages/import', { data, tenant_id: tenant.value || null, conflict: conflict.value, dry_run: true }));
    if (my !== gen) return;  // überholt (Mandant, Regel oder Datei inzwischen geändert)
    if (!r) { out.replaceChildren(); return; }
    last = r;
    showResult(r);
  }
  file.addEventListener('change', async () => {
    const f = file.files[0];
    last = null; data = null; gen++;
    if (!f) return;
    if (f.size > 19 * 1024 * 1024) { toast(tr('Datei zu groß (höchstens 19 MB).'), 'err'); return; }
    try { data = JSON.parse(await f.text()); } catch (e) { data = null; out.replaceChildren(h('div.empty', tr('Die Datei ist keine gültige Export-Datei (kein JSON).'))); return; }
    check();
  });
  tenant.addEventListener('change', check);
  conflict.addEventListener('change', check);
  modal(tr('Pakete importieren'), h('div.form',
    field('Datei', file),
    field('Mandant', tenant, isOU() ? 'Importierte Pakete gehören Ihrem Mandanten.' : 'Wem die importierten Pakete gehören. Dateien aus dem Repository müssen bei einem Mandanten in dessen Repository-Ordner liegen.'),
    field('Gibt es schon', conflict, 'Gleicher Name wie ein vorhandenes Paket.'),
    h('div.full', out)),
  [{ label: 'Schließen' }, { label: 'Importieren', cls: 'primary', close: false, onClick: async () => {
    if (done) return;
    if (!last) { toast(tr(data ? 'Die Prüfung läuft noch – bitte einen Moment.' : 'Nichts zu importieren – zuerst eine Datei wählen.'), 'err'); return; }
    if (!(last.count.new + last.count.copy)) { toast(tr('Nichts zu importieren – alle Pakete gibt es schon oder sie haben Fehler.'), 'err'); return; }
    const r = await run(() => POST('/api/packages/import', { data, tenant_id: tenant.value || null, conflict: conflict.value, dry_run: false }));
    if (!r) return;
    const c = r.count;
    done = true;  // kein zweiter Import derselben Datei aus diesem Dialog
    [file, tenant, conflict].forEach((x) => { x.disabled = true; });
    const ib = out.closest('.modal') && out.closest('.modal').querySelector('.modal-f button.primary');
    if (ib) ib.style.display = 'none';  // fertig – nur noch „Schließen“
    showResult(r, fmtT('{0} Paket(e) importiert, {1} übersprungen, {2} mit Fehler', c.new + c.copy, c.skip, c.error));
    route();  // Paketliste dahinter auffrischen – der Dialog bleibt mit dem Ergebnis offen
    toast(fmtT('{0} Paket(e) importiert, {1} übersprungen, {2} mit Fehler', c.new + c.copy, c.skip, c.error), c.error ? 'err' : 'ok');
  } }], { wide: true });
}

// --- Bedingungs-Editor (Paket und Schritt) -------------------------------------------------
function conditionsEditor(c, meta, opt = {}) {
  c.os = c.os || meta.default_conditions.os; c.arch = c.arch || meta.default_conditions.arch;
  c.files = c.files || []; c.registry = c.registry || []; c.collections = c.collections || [];
  const box = h('div.cond');
  const checkRow = (items, list) => h('div.chk-row', items.map(([k, l]) => h('label.chk',
    h('input', { type: 'checkbox', checked: list.includes(k), onchange: (e) => { const i = list.indexOf(k); if (e.target.checked && i < 0) list.push(k); if (!e.target.checked && i >= 0) list.splice(i, 1); } }), l)));
  const listEditor = (arr, make, add, empty) => {
    const wrap = h('div');
    const draw = () => wrap.replaceChildren(...arr.map((x, i) => h('div.cond-line', ...make(x),
      h('button.ghost.small', { type: 'button', title: 'Entfernen', onclick: async () => { if (!(await askRemove('Diese Bedingung entfernen?'))) return; arr.splice(i, 1); draw(); } }, icon('trash')))),
    h('button.ghost.small', { type: 'button', onclick: () => { arr.push(add()); draw(); } }, icon('plus'), ' ', empty));
    draw();
    return wrap;
  };
  const bind = (x, k) => (e) => { x[k] = e.target.value; };
  box.append(h('div.form',
    field('Betriebssystem', checkRow(meta.os, c.os), 'Standardmäßig ist alles ausgewählt – bei Bedarf abwählen.'),
    field('Architektur', checkRow(meta.arch, c.arch)),
    field('PowerShell', sel(meta.ps_versions.map((v) => [v, v ? `mindestens ${v}` : 'beliebige Version']), c.ps_min || '', { onchange: (e) => { c.ps_min = e.target.value; } })),
    field('Angemeldet', sel(Object.entries(meta.logon), c.logged_on || 'always', { onchange: (e) => { c.logged_on = e.target.value; } })),
    field('Datei', listEditor(c.files, (x) => [h('input', { value: x.path, placeholder: '%ProgramFiles%\\Firma\\app.exe', oninput: bind(x, 'path') }),
      sel(Object.entries(meta.file_modes), x.mode, { onchange: bind(x, 'mode') }), h('input', { value: x.version || '', placeholder: 'Version', style: { maxWidth: '110px' }, oninput: bind(x, 'version') })],
    () => ({ path: '', mode: 'exists', version: '' }), 'Datei-Bedingung')),
    field('Registry', listEditor(c.registry, (x) => [h('input', { value: x.path, placeholder: 'HKLM:\\SOFTWARE\\Firma', oninput: bind(x, 'path') }),
      h('input', { value: x.value || '', placeholder: 'Wertname (leer = Schlüssel)', style: { maxWidth: '170px' }, oninput: bind(x, 'value') }),
      sel(Object.entries(meta.reg_modes), x.mode, { onchange: bind(x, 'mode') }), h('input', { value: x.data || '', placeholder: 'Daten', style: { maxWidth: '120px' }, oninput: bind(x, 'data') })],
    () => ({ path: '', value: '', mode: 'exists', data: '' }), 'Registry-Bedingung')),
    field('Sammlung', listEditor(c.collections, (x) => [sel([['member', 'ist Mitglied von'], ['not_member', 'ist nicht Mitglied von']], x.mode, { onchange: bind(x, 'mode') }),
      sel(collChoiceOptions(meta.collections), x.id, { 'data-notr': '1', onchange: (e) => { x.id = Number(e.target.value); } })],
    () => ({ id: (meta.collections[0] || {}).id, mode: 'member' }), 'Sammlungs-Bedingung'), 'Wird vor der Ausführung auf dem Server geprüft.')));
  if (opt.hint) box.prepend(h('p.muted.small', { style: { marginTop: 0 } }, opt.hint));
  return box;
}

// --- Editor ------------------------------------------------------------------------------
// Name ohne Versionsnummer als Muster („7-Zip 24.08 (x64 edition)“ → „7-Zip*“). Als Version zählt nur eine Zahl mit Punkt
// (24.08, 3.12.1, v8.6) – „Microsoft 365 Apps“, „Office 16 …“ oder „Windows 10 Update Assistant“ bleiben unverändert,
// sonst würde aus ihnen ein viel zu breites Muster wie „Microsoft*“.
function uninstallPattern(name) {
  const n = String(name || '');
  const base = n.replace(/\s+v?\d+\.\d[\d.]*\b.*$/i, '').replace(/[\s\-–—:,(]+$/, '').trim();
  return base && base !== n && base.length >= 2 ? base + '*' : n;
}
function uninstallPackageFrom(sw) {
  packageDlg(null, { uninstall: { name: sw.name, pattern: uninstallPattern(sw.name), version: '' } });
}

// --- Bruchprüfung (2.26): Zustand der Repository-Dateien eines Pakets ---------------------------------------
const INTEG = {
  ok: ['ok', '✓', 'Repository-Dateien unverändert'],
  warn: ['warn', '!', 'Abweichung im Repository – Lauf geht weiter'],
  broken: ['err', '✕', 'Bruch im Repository – Bereitstellungen werden unterbrochen'],
  unknown: ['', '?', 'Nicht prüfbar (Repository nicht erreichbar)'],
};
function integBadge(ig) {
  if (!ig || !ig.state || !INTEG[ig.state]) return '';
  const [cls, sym, text] = INTEG[ig.state];
  const first = (ig.issues || []).filter((x) => x.level !== 'unknown').slice(0, 3).map((x) => `${x.path ? x.path + ': ' : ''}${tr(x.note || x.kind)}`);
  return h('span.badge', { class: cls, title: [tr(text), ...first].join('\n') + (ig.at ? `\n${tr('Geprüft')}: ${fmtDate(ig.at)}` : '') }, sym);
}
function integText(ig) { return ig && INTEG[ig.state] ? tr({ ok: 'OK', warn: 'Abweichung', broken: 'Bruch', unknown: 'nicht prüfbar' }[ig.state]) : ''; }

function integrityBox(p, editable, onChange) {
  const box = h('div.integ', { style: { marginTop: '16px' } });
  const fmtSig = (x) => (x && (x.size !== undefined && x.size !== null) ? `${fmtBytes(x.size)}${x.mtime ? ', ' + fmtDate(x.mtime) : ''}${x.sha256 ? ', SHA-256 ' + x.sha256.slice(0, 12) + '…' : ''}` : '');
  function draw(ig) {
    const st = ig && INTEG[ig.state];
    const rows = (ig && ig.issues || []).filter((x) => x.level !== 'unknown' || x.note);
    box.replaceChildren(
      h('h3', { style: { margin: '0 0 6px' } }, 'Repository-Dateien'),
      !ig || !ig.state ? h('div.muted.small', 'Dieses Paket benutzt keine Dateien aus dem Repository (oder wurde noch nicht geprüft).')
        : h('div', h('div.row', { style: { gap: '8px', alignItems: 'center' } }, integBadge(ig), h('b', tr(st[2]))),
          ig.at ? h('div.muted.small', fmtT('Geprüft: {0}', fmtDate(ig.at))) : null,
          rows.length ? h('table.tbl', { style: { width: '100%', marginTop: '8px', tableLayout: 'fixed' } },
            h('thead', h('tr', h('th', { style: { width: '38%' } }, 'Datei'), h('th', { style: { width: '24%' } }, 'Befund'), h('th', 'Bisher → jetzt'))),
            h('tbody', rows.map((x) => h('tr', h('td.wrap.raw', x.path || '—', x.main ? h('span.badge', { style: { marginLeft: '6px' } }, tr('Installationsdatei')) : null),
              h('td.wrap', h('span.badge', { class: x.level === 'broken' ? 'err' : x.level === 'warn' ? 'warn' : '' }, tr(x.note || x.kind))),
              h('td.wrap.small.raw', [fmtSig(x.old), fmtSig(x.new)].filter(Boolean).join(' → ')))))) : null,
          (ig.total || 0) > rows.length ? h('div.muted.small', fmtT('… und {0} weitere', ig.total - rows.length)) : null),
      h('div.row', { style: { gap: '8px', marginTop: '10px' } },
        btn('Jetzt prüfen', async () => { const r = await run(() => POST(`/api/packages/${p.id}/integrity/check`)); if (r) { draw(r); onChange && onChange(r); } }, { cls: 'small', icon: 'refresh' }),
        // nur wenn es eigene (sichtbare) Abweichungen gibt – zentrale Dateien bestätigt ein Hauptadmin
        editable && ig && (ig.state === 'broken' || ig.state === 'warn') && rows.some((x) => x.path) ? btn('Neuen Stand übernehmen', async () => {
          if (!(await confirmDlg(tr('Den aktuellen Inhalt der Repository-Dateien als richtig bestätigen? Danach laufen Bereitstellungen wieder mit genau diesen Dateien. Fehlende Dateien lassen sich so nicht beheben – dafür das Paket anpassen.'), 'Übernehmen'))) return;
          const r = await run(() => POST(`/api/packages/${p.id}/integrity/accept`));
          if (!r) return;
          draw(r.integrity); onChange && onChange(r.integrity);
          if (!r.count) toast(tr('Nichts zu übernehmen – die Dateien entsprechen schon dem bestätigten Stand (oder es fehlen welche: dann das Paket anpassen).'), 'err');
          else toast(r.version_hint ? fmtT('Neuer Stand übernommen. Die MSI hat jetzt Version {0} – bitte die Version im Paket anpassen.', r.version_hint) : tr('Neuer Stand übernommen'), r.version_hint ? 'err' : 'ok');
        }, { cls: 'small primary' }) : null,
        // Reparatur per Fingerabdruck (2.27): fehlende Dateien wurden evtl. nur verschoben
        editable && ig && rows.some((x) => x.kind === 'missing' && x.path) ? btn('Datei suchen …', () => repairDlg(p, async () => {
          const r = await run(() => POST(`/api/packages/${p.id}/integrity/check`)); if (r) { draw(r); onChange && onChange(r); }
        }), { cls: 'small', icon: 'folder', title: tr('Fehlende Dateien per Fingerabdruck (SHA-256) im Repository suchen und die Pfade übernehmen') }) : null),
      h('p.muted.small', { style: { margin: '8px 0 0' } }, 'POCKET KNIFE merkt sich Größe, Datum und Fingerabdruck der Dateien. Fehlt eine Datei oder ändert sich die Installationsdatei, werden Bereitstellungen unterbrochen, bis jemand den neuen Stand übernimmt.'));
  }
  draw(p.integrity);
  return box;
}

async function packageDlg(pidOrPkg, preset = {}) {
  const meta = await pkgMeta(true);
  let p;
  if (typeof pidOrPkg === 'number') p = await GET(`/api/packages/${pidOrPkg}`);
  else if (pidOrPkg && pidOrPkg.id) p = await GET(`/api/packages/${pidOrPkg.id}`);
  const isNew = !p;
  const newStep = (type, extra = {}) => ({ id: Math.random().toString(16).slice(2, 10), type, enabled: true, run_as: 'inherit', on_error: 'stop', timeout_min: 0,
    title: '', conditions: JSON.parse(JSON.stringify(meta.default_conditions)),
    ...({ install: { source: 'file', folder: '', install_file: '', install_type: '', arguments: '', copy_folder: true, detect_name: '', detect_version: '', success_codes: '0,3010,1641',
      winget_id: '', winget_action: 'install', winget_version: '', winget_scope: 'machine', extra_args: '' },
    uninstall: { match_name: '', match_version: '', command: '', cleanup: false, all_matches: true, missing_ok: true },
    command: { command: '', success_codes: '0' }, powershell: { script: '', success_codes: '0' }, nested: { package_id: (meta.packages.find((x) => !p || x.id !== p.id) || {}).id },
    filecopy: { source: '', target: '%ProgramData%\\', overwrite: true }, registry: { view: '64', success_codes: '0', entries: [{ action: 'set', path: 'HKLM\\SOFTWARE\\', name: '', kind: 'REG_SZ', data: '' }] }, scan: { profile_id: null }, reboot: { delay_sec: 60, wait_minutes: 20, message: '' },
    sleep: { seconds: 30 }, message: { text: '', timeout_sec: 0 }, logoff: { only_disconnected: false } })[type], ...extra });
  p = p ? JSON.parse(JSON.stringify(p)) : { name: '', version: '', description: '', run_as: 'deploy', timeout_min: 60, reboot: 'never', rescan: 1, tenant_id: preset.tenant_id || null,
    conditions: JSON.parse(JSON.stringify(meta.default_conditions)),
    steps: { pre: [], main: [preset.uninstall ? newStep('uninstall', { match_name: preset.uninstall.pattern, match_version: preset.uninstall.version || '' })
      : newStep('install', preset.winget_id ? { source: 'winget', winget_id: preset.winget_id } : {})], post: [] } };
  if (preset.uninstall && isNew) { p.name = `Deinstallation: ${preset.uninstall.name}`; p.description = `Entfernt „${preset.uninstall.name}“ von den Zielcomputern.`; }
  if (preset.winget_id && isNew) p.name = preset.name || preset.winget_id;
  let selKey = 'props'; // immer mit Name/Eigenschaften beginnen – die Schritte stehen links und sind einen Klick entfernt
  const tree = h('div.pkg-tree');
  const panel = h('div.pkg-panel');
  const PH = meta.phases;
  const stepLabel = (st) => {
    if (st.title) return st.title;
    const t = st.type;
    if (t === 'install') return st.source === 'winget' ? `winget ${st.winget_action || 'install'} ${st.winget_id || '…'}` : `Installieren: ${st.install_file || '…'}`;
    if (t === 'uninstall') return 'Deinstallieren: ' + (st.match_name || '…') + (st.match_version ? ' ' + st.match_version : '');
    if (t === 'command') return 'Befehl: ' + ((st.command || '').split('\n')[0] || '…');
    if (t === 'powershell') return 'PowerShell: ' + ((st.script || '').split('\n')[0] || '…');
    if (t === 'nested') return 'Paket: ' + ((meta.packages.find((x) => x.id === Number(st.package_id)) || {}).name || '…');
    if (t === 'filecopy') return `Dateikopie → ${st.target || '…'}`;
    if (t === 'registry') { const e = (st.entries || [])[0] || {}; return `Registry: ${e.path || '…'}${e.name ? '\\' + e.name : ''}${(st.entries || []).length > 1 ? ` (+${st.entries.length - 1})` : ''}`; }
    if (t === 'scan') return `Scannen (${(meta.profiles.find((x) => x.id === Number(st.profile_id)) || { name: 'Standard' }).name})`;
    if (t === 'sleep') return `Warten (${st.seconds} s)`;
    if (t === 'message') return 'Nachricht: ' + (st.text || '…');
    return meta.step_types[t];
  };
  const condSummary = (c) => {
    const n = [];
    if (c.os && c.os.length !== meta.os.length) n.push('BS');
    if (c.arch && c.arch.length !== meta.arch.length) n.push('Arch');
    if (c.ps_min) n.push('PS');
    if (c.logged_on && c.logged_on !== 'always') n.push('Anmeldung');
    if ((c.files || []).length) n.push('Datei');
    if ((c.registry || []).length) n.push('Registry');
    if ((c.collections || []).length) n.push('Sammlung');
    return n;
  };
  let dragFrom = null;
  function drawTree() {
    const item = (key, label, ic, extra = []) => h('div.pkg-item', { class: JSON.stringify(selKey) === JSON.stringify(key) ? 'on' : '', onclick: () => { selKey = key; drawTree(); drawPanel(); } },
      h('span.ic-s', icon(ic)), h('span.lbl', label), ...extra);
    const parts = [item('props', 'Paket-Eigenschaften', 'box'), item('conds', 'Bedingungen', 'rule', condSummary(p.conditions).length ? [h('span.badge.info', condSummary(p.conditions).join(', '))] : [])];
    for (const [ph, phLabel] of PH) {
      const head = h('div.pkg-phase', h('span', phLabel), h('button.ghost.small', { type: 'button', title: `Schritt zu „${phLabel}“ hinzufügen`, onclick: (e) => addMenu(e, ph) }, icon('plus')));
      head.addEventListener('dragover', (e) => { if (dragFrom) { e.preventDefault(); head.classList.add('dropto'); } });
      head.addEventListener('dragleave', () => head.classList.remove('dropto'));
      head.addEventListener('drop', (e) => { e.preventDefault(); head.classList.remove('dropto'); moveStep(dragFrom, ph, 0); });
      parts.push(head);
      p.steps[ph].forEach((st, i) => {
        const key = { phase: ph, idx: i };
        const el = item(key, stepLabel(st), STEP_ICON[st.type] || 'box', [
          condSummary(st.conditions || {}).length ? h('span.badge.info', { title: 'Schritt hat Bedingungen' }, '⚑') : null,
          st.run_as && st.run_as !== 'inherit' ? h('span.badge', { title: meta.step_run_as[st.run_as] }, { system: 'SYSTEM', user: 'Benutzer', deploy: 'Dienstkonto' }[st.run_as]) : null,
          st.on_error === 'continue' ? h('span.badge', { title: 'Bei Fehler weitermachen' }, '↷') : null,
          h('span.pkg-acts', h('button.ghost.small', { type: 'button', title: 'Nach oben', onclick: (e) => { e.stopPropagation(); moveStep(key, ph, i - 1); } }, '↑'),
            h('button.ghost.small', { type: 'button', title: 'Nach unten', onclick: (e) => { e.stopPropagation(); moveStep(key, ph, i + 2); } }, '↓'),
            h('button.ghost.small', { type: 'button', title: 'Duplizieren', onclick: (e) => { e.stopPropagation(); const c = JSON.parse(JSON.stringify(st)); c.id = Math.random().toString(16).slice(2, 10); p.steps[ph].splice(i + 1, 0, c); selKey = { phase: ph, idx: i + 1 }; drawTree(); drawPanel(); } }, '⧉'),
            h('button.ghost.small', { type: 'button', title: 'Entfernen', onclick: async (e) => { e.stopPropagation(); if (!(await askRemove(`Schritt „${stepLabel(st)}“ entfernen?`))) return; p.steps[ph].splice(i, 1); selKey = 'props'; drawTree(); drawPanel(); } }, icon('trash')))]);
        el.prepend(h('span.pkg-n', String(i + 1)));
        if (!st.enabled) el.classList.add('off');
        el.draggable = true;
        el.addEventListener('dragstart', (e) => { dragFrom = key; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', 'step'); } catch (x) { /* egal */ } });
        el.addEventListener('dragend', () => { dragFrom = null; });
        el.addEventListener('dragover', (e) => { if (dragFrom) { e.preventDefault(); el.classList.add('dropto'); } });
        el.addEventListener('dragleave', () => el.classList.remove('dropto'));
        el.addEventListener('drop', (e) => { e.preventDefault(); el.classList.remove('dropto'); moveStep(dragFrom, ph, i); });
        el.addEventListener('contextmenu', (e) => { e.preventDefault(); selKey = key; drawTree(); drawPanel();
          contextMenu(e.clientX, e.clientY, [
            { label: st.enabled ? 'Deaktivieren' : 'Aktivieren', icon: 'x', onClick: () => { st.enabled = !st.enabled; drawTree(); drawPanel(); } },
            { label: 'Duplizieren', icon: 'coll', onClick: () => { const c = JSON.parse(JSON.stringify(st)); c.id = Math.random().toString(16).slice(2, 10); p.steps[ph].splice(i + 1, 0, c); drawTree(); } },
            { label: 'Verschieben nach', icon: 'folder', sub: PH.filter(([k]) => k !== ph).map(([k, l]) => ({ label: l, onClick: () => moveStep(key, k, p.steps[k].length) })) },
            '-', { label: 'Entfernen', icon: 'trash', onClick: async () => { if (!(await askRemove(`Schritt „${stepLabel(st)}“ entfernen?`))) return; p.steps[ph].splice(i, 1); selKey = 'props'; drawTree(); drawPanel(); } }]); });
        parts.push(el);
      });
      if (!p.steps[ph].length) parts.push(h('div.pkg-empty.muted.small', ph === 'main' ? 'Noch keine Schritte – über + hinzufügen' : ph === 'pre' ? 'z. B. Programm beenden, alte Version entfernen' : 'läuft immer – auch nach einem Fehler'));
    }
    parts.push(h('div', { style: { padding: '8px' } }, btn('Neuer Schritt', (e) => addMenu(e, 'main'), { icon: 'plus', cls: 'small' })));
    tree.replaceChildren(...parts);
  }
  function moveStep(from, ph, to) {
    if (!from) return;
    const [st] = p.steps[from.phase].splice(from.idx, 1);
    if (from.phase === ph && to > from.idx) to -= 1;
    to = Math.max(0, Math.min(p.steps[ph].length, to));
    p.steps[ph].splice(to, 0, st);
    selKey = { phase: ph, idx: to };
    dragFrom = null;
    drawTree(); drawPanel();
  }
  function addMenu(e, ph) {
    const r = e.currentTarget.getBoundingClientRect();
    contextMenu(r.left, r.bottom + 2, Object.entries(meta.step_types).map(([k, l]) => ({ label: l, icon: STEP_ICON[k], onClick: () => {
      p.steps[ph].push(newStep(k)); selKey = { phase: ph, idx: p.steps[ph].length - 1 }; drawTree(); drawPanel();
    } })));
  }

  // Rechte Seite -----------------------------------------------------------------------
  const inp = (o, k, attrs = {}) => h('input', { value: o[k] ?? '', oninput: (e) => { o[k] = attrs.type === 'number' ? Number(e.target.value) : e.target.value; changed(); }, ...attrs });
  const ta = (o, k, rows, attrs = {}) => h('textarea.mono', { rows, value: o[k] || '', oninput: (e) => { o[k] = e.target.value; changed(); }, spellcheck: false, ...attrs });
  const chk = (o, k, label) => h('label.chk', h('input', { type: 'checkbox', checked: !!o[k], onchange: (e) => { o[k] = e.target.checked; changed(); } }), label);
  const sl = (o, k, opts, num = false) => sel(opts, o[k] ?? '', { onchange: (e) => { o[k] = num ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value; changed(); } });
  let pvTimer = null; let pvBox = null; let curStep = null;
  function changed() {
    drawTreeSoon();
    if (curStep && pvBox) { clearTimeout(pvTimer); pvTimer = setTimeout(updPreview, 300); }
  }
  let treeTimer = null;
  function drawTreeSoon() { clearTimeout(treeTimer); treeTimer = setTimeout(drawTree, 250); }
  async function updPreview() {
    if (!curStep || !pvBox) return;
    try {
      const r = await POST('/api/packages/preview', { step: curStep, run_as: p.run_as, version: p.version });
      pvBox.replaceChildren(r.error ? h('div.muted', r.error) : h('pre.cmd-preview.mono', r.command), ...(r.notes || []).map((n) => h('div.muted.small', '• ' + n)));
    } catch (e) { pvBox.replaceChildren(h('div.muted', e.message)); }
  }
  async function msiAutofill(st, folder, file) {
    try {
      const m = await GET('/api/repository/msi?path=' + encodeURIComponent((folder ? folder + '\\' : '') + file));
      const filled = [];
      if (!p.name) { p.name = m.name; filled.push('Name'); }
      if (!p.version) { p.version = m.version; filled.push('Version'); }
      if (!p.description && m.manufacturer) { p.description = `${m.manufacturer}${m.product_code ? ' · ' + m.product_code : ''}`; filled.push('Beschreibung'); }
      if (!st.detect_name && m.name) { st.detect_name = (m.name.replace(/\s+v?\d[\d.]*.*$/, '') || m.name) + '*'; st.detect_version = m.version; filled.push('Erkennung'); }
      st.product_code = m.product_code;
      if (m.arch === 'x64') { st.conditions.arch = st.conditions.arch.filter((a) => a !== 'x86'); filled.push('Architektur ohne x86'); }
      if (m.arch === 'arm64') { st.conditions.arch = ['arm64']; filled.push('Architektur nur ARM64'); }
      st._msi = m;
      toast(`MSI erkannt: ${m.name} ${m.version}${filled.length ? ' – übernommen: ' + filled.join(', ') : ''}`, 'ok');
    } catch (e) { toast(`MSI-Eigenschaften nicht lesbar: ${e.message}`, 'err'); }
    drawTree(); drawPanel();
  }
  function stepDetails(st) {
    const t = st.type;
    if (t === 'install') {
      const seg = h('div.seg', [['file', 'Datei aus dem Repository'], ['winget', 'winget-Katalog']].map(([k, l]) => h('button', { type: 'button', class: st.source === k ? 'on' : '',
        onclick: () => { st.source = k; drawTree(); drawPanel(); } }, l)));
      if (st.source === 'winget') {
        const latest = h('span.muted.small');
        const wInp = inp(st, 'winget_id', { placeholder: 'z. B. Mozilla.Firefox' });
        const showLatest = async () => { if (!st.winget_id) { latest.textContent = ''; return; } try { const r = await GET('/api/winget/search?q=' + encodeURIComponent(st.winget_id)); const hit = (r || []).find((x) => x.id.toLowerCase() === st.winget_id.toLowerCase()); latest.textContent = hit ? `aktuell im Katalog: ${hit.latest}` : 'nicht im Katalog gefunden'; } catch (e) { latest.textContent = ''; } };
        wInp.addEventListener('change', showLatest); setTimeout(showLatest, 0);
        return h('div.form', field('Quelle', seg),
          field('winget-ID', h('div.row', wInp, btn('Suchen', () => wingetPicker((id, pk) => { st.winget_id = id; if (!p.name) p.name = pk.name; drawTree(); drawPanel(); }), { cls: 'small' })), latest),
          field('Aktion', sl(st, 'winget_action', [['install', 'Installieren'], ['upgrade', 'Aktualisieren'], ['uninstall', 'Deinstallieren']])),
          field('Version', inp(st, 'winget_version', { placeholder: 'leer = immer die neueste' })),
          field('Bereich', sl(st, 'winget_scope', [['machine', 'Computer (empfohlen)'], ['', 'Standard des Pakets'], ['user', 'Benutzer']])),
          field('Zusätzliche Argumente', inp(st, 'extra_args', { placeholder: 'z. B. --override "/S"' })));
      }
      const fileInp = inp(st, 'install_file', { placeholder: 'z. B. 7z2408-x64.msi' });
      fileInp.addEventListener('change', () => { if (!st.install_type || st.install_type === '') { st.install_type = ''; } });
      const msiInfo = st._msi ? h('div.muted.small', `MSI: ${st._msi.name} ${st._msi.version} · ${st._msi.manufacturer} · ${st._msi.arch} · ${st._msi.product_code}`) : null;
      return h('div.form', field('Quelle', seg),
        field('Paketordner', h('div.row', inp(st, 'folder', { placeholder: 'z. B. 7zip\\24.08' }), btn('Durchsuchen …', () => repoBrowser((folder, file) => {
          st.folder = folder; if (file) { st.install_file = file; st.install_type = ''; if (/\.(msi|msp)$/i.test(file)) { msiAutofill(st, folder, file); return; } } drawTree(); drawPanel();
        }, st.folder || tenantFolder()), { cls: 'small' })), 'Relativ zum Repository. Bei Auswahl einer MSI werden Name, Version, Hersteller und Produktcode automatisch gelesen.'),
        field('Installationsdatei', fileInp, msiInfo),
        field('Dateityp', sl(st, 'install_type', [['', 'automatisch (nach Endung)'], ['msi', 'MSI (msiexec /i)'], ['msp', 'MSP-Patch (msiexec /p)'], ['msu', 'MSU-Update (wusa)'], ['exe', 'EXE-Installer'], ['cmd', 'Batch (.cmd/.bat)'], ['ps1', 'PowerShell-Datei (.ps1)'], ['reg', 'Registry-Datei (.reg → reg import)']])),
        field('Parameter', inp(st, 'arguments', { placeholder: 'z. B. /S oder ALLUSERS=1' }), 'Bei MSI/MSP/MSU setzt POCKET KNIFE /qn bzw. /quiet und /norestart selbst – doppelte Angaben werden entfernt.'),
        field('Kopieren', chk(st, 'copy_folder', 'ganzen Paketordner kopieren (sonst nur die Installationsdatei)')),
        field('Erkennung', h('div.row', inp(st, 'detect_name', { placeholder: 'Anzeigename, z. B. 7-Zip*' }), inp(st, 'detect_version', { placeholder: 'Version (leer = Paketversion)', style: { maxWidth: '180px' } })),
          'Optional: Ist die Software in dieser Version oder neuer installiert, wird der Schritt übersprungen.'),
        field('Erfolgs-Exitcodes', inp(st, 'success_codes'), '3010/1641 = Neustart erforderlich.'));
    }
    if (t === 'uninstall') {
      return h('div.form',
        field('Software (Anzeigename)', inp(st, 'match_name', { placeholder: 'z. B. Zotero* oder Mozilla Firefox*' }), 'Wie unter „Programme und Features“; * und ? sind Platzhalter. Gesucht wird beim Ausführen live auf dem Computer.'),
        field('Version', inp(st, 'match_version', { placeholder: 'leer = jede Version, z. B. 6.* ' })),
        field('Eigener Befehl', inp(st, 'command', { placeholder: 'leer = automatisch erkennen (MSI, Hersteller-Silent-Befehl, bekannte Uninstaller, winget)' }),
          'Platzhalter: {ProductCode}, {UninstallString}, {QuietUninstallString}, {InstallLocation} – z. B. "{InstallLocation}\\uninstall\\helper.exe" /S'),
        field('Mehrere Treffer', chk(st, 'all_matches', 'alle passenden Einträge deinstallieren (sonst nur den ersten)')),
        field('Registry', chk(st, 'cleanup', 'verwaisten Eintrag in „Programme und Features“ danach entfernen')),
        field('Nicht installiert', chk(st, 'missing_ok', 'als erfolgreich werten (übersprungen)')));
    }
    if (t === 'command') return h('div.form', h('label', 'Befehl'), h('div', ta(st, 'command', 10, { placeholder: 'Mehrzeilig wie eine .cmd-Datei, z. B.\ntaskkill /im 7zFM.exe /f\nreg add "HKLM\\SOFTWARE\\Firma" /v Wert /d 1 /f' })), field('Erfolgs-Exitcodes', inp(st, 'success_codes')));
    if (t === 'powershell') return h('div.form', h('label', 'PowerShell'), h('div', ta(st, 'script', 14, { placeholder: '# exit 1 meldet einen Fehler (Exitcode), ohne exit gilt 0\nGet-Process 7zFM -ErrorAction SilentlyContinue | Stop-Process -Force' })), field('Erfolgs-Exitcodes', inp(st, 'success_codes')));
    if (t === 'nested') {
      return h('div.form', field('Paket', sl(st, 'package_id', meta.packages.filter((x) => !p.id || x.id !== p.id).map((x) => [x.id, x.name]), true),
        'Die Schritte dieses Pakets laufen an dieser Stelle – mit dessen eigenen Bedingungen und „Ausführen als“.'));
    }
    if (t === 'filecopy') {
      return h('div.form', field('Quelle', h('div.row', inp(st, 'source', { placeholder: 'Ordner oder Datei im Repository' }),
        btn('Durchsuchen …', () => repoBrowser((folder, file) => { st.source = file ? (folder ? folder + '\\' : '') + file : folder; drawPanel(); drawTree(); }, tenantFolder()), { cls: 'small' }))),
      field('Ziel', inp(st, 'target', { placeholder: '%ProgramData%\\Firma' }), 'Absoluter Pfad auf dem Client; Umgebungsvariablen wie %ProgramData% sind erlaubt.'),
      field('Vorhandene Dateien', chk(st, 'overwrite', 'überschreiben')));
    }
    if (t === 'registry') {
      const ACT = [['set', 'Wert setzen'], ['create_key', 'Schlüssel anlegen'], ['delete_value', 'Wert löschen'], ['delete_key', 'Schlüssel löschen']];
      const KINDS = [['REG_SZ', 'REG_SZ (Text)'], ['REG_EXPAND_SZ', 'REG_EXPAND_SZ (mit %Variablen%)'], ['REG_DWORD', 'REG_DWORD (32 Bit)'], ['REG_QWORD', 'REG_QWORD (64 Bit)'],
        ['REG_MULTI_SZ', 'REG_MULTI_SZ (mehrzeilig)'], ['REG_BINARY', 'REG_BINARY (Hex)']];
      const list = h('div.reg-list');
      const draw = () => {
        list.replaceChildren(...st.entries.map((e, i) => {
          const needsName = e.action === 'set' || e.action === 'delete_value';
          const dataEl = e.action !== 'set' ? null : e.kind === 'REG_MULTI_SZ' ? ta(e, 'data', 3, { placeholder: 'eine Zeile je Eintrag' })
            : inp(e, 'data', { placeholder: { REG_DWORD: 'z. B. 1 oder 0x1f90', REG_QWORD: 'Zahl', REG_BINARY: 'z. B. 01,ff,0a' }[e.kind] || 'Wert' });
          const actSel = sl(e, 'action', ACT); actSel.addEventListener('change', () => { if (e.action === 'set' && !e.kind) e.kind = 'REG_SZ'; draw(); });
          const kindSel = e.action === 'set' ? sl(e, 'kind', KINDS) : null;
          if (kindSel) kindSel.addEventListener('change', draw);
          return h('div.reg-row',
            h('div.reg-top', h('span.muted.small', `${i + 1}.`), actSel,
              inp(e, 'path', { placeholder: 'HKLM\\SOFTWARE\\Firma\\App', class: 'mono', style: { flex: 1 } }),
              btn('', async () => { if ((e.name || e.data) && !(await askRemove(`Eintrag ${i + 1} (${e.path}${e.name ? '\\' + e.name : ''}) entfernen?`))) return; st.entries.splice(i, 1); draw(); changed(); }, { icon: 'x', cls: 'small ghost', title: 'Eintrag entfernen', disabled: st.entries.length < 2 })),
            needsName ? h('div.reg-val', inp(e, 'name', { placeholder: 'Wertname (leer = Standardwert)', class: 'mono' }), kindSel, dataEl) : null);
        }));
      };
      draw();
      return h('div.form',
        field('Ansicht', sl(st, 'view', [['64', '64-Bit (Standard)'], ['32', '32-Bit (WOW6432Node)']]), 'Bei 32-Bit-Programmen auf 64-Bit-Windows landen deren Einstellungen unter WOW6432Node.'),
        h('div', { style: { gridColumn: '1 / -1' } }, h('div.muted.small', { style: { margin: '4px 0 6px' } }, 'Einträge (werden der Reihe nach ausgeführt)'), list, h('div.row', { style: { marginTop: '6px' } },
          btn('Eintrag', () => { const last = st.entries[st.entries.length - 1] || {}; st.entries.push({ action: 'set', path: last.path || 'HKLM\\SOFTWARE\\', name: '', kind: 'REG_SZ', data: '' }); draw(); changed(); }, { icon: 'plus', cls: 'small' }),
          btn('Aus .reg importieren …', () => regImportDlg((ents) => {
            const blank = st.entries.length === 1 && !st.entries[0].name && !st.entries[0].data && /^HKLM\\SOFTWARE\\?$/.test(st.entries[0].path || '');
            st.entries = (blank ? [] : st.entries).concat(ents); draw(); drawTree(); changed();
          }), { icon: 'file', cls: 'small' }))),
        h('p.muted.small', { style: { gridColumn: '1 / -1', margin: '4px 0 0' } }, 'Pfade mit HKLM\\, HKCU\\, HKU\\, HKCR\\ oder HKCC\\ (auch ausgeschrieben, z. B. HKEY_LOCAL_MACHINE\\…). Beim Setzen werden fehlende Schlüssel angelegt. HKCU-Einträge gelten für das ausführende Konto – für Benutzereinstellungen „Ausführen als: Angemeldeter Benutzer“. Eine ganze .reg-Datei lässt sich alternativ mit „Installieren“ (Dateityp Registry-Datei) importieren.'),
        field('Erfolgs-Exitcodes', inp(st, 'success_codes'), '0 = alle Einträge erfolgreich, 1 = mindestens einer fehlgeschlagen.'));
    }
    if (t === 'scan') return h('div.form', field('Scanprofil', sl(st, 'profile_id', [['', 'Standardprofil'], ...meta.profiles.map((x) => [x.id, x.name])], true)));
    if (t === 'reboot') {
      return h('div.form', field('Vorwarnung (Sek.)', inp(st, 'delay_sec', { type: 'number', min: 0, style: { maxWidth: '120px' } })),
        field('Warten bis erreichbar (Min.)', inp(st, 'wait_minutes', { type: 'number', min: 2, style: { maxWidth: '120px' } }), 'Danach geht es mit dem nächsten Schritt weiter.'),
        field('Meldung', inp(st, 'message', { placeholder: 'leer = Standardtext (Einstellungen → Meldung bei Neustart)' })));
    }
    if (t === 'sleep') return h('div.form', field('Sekunden', inp(st, 'seconds', { type: 'number', min: 1, style: { maxWidth: '120px' } })));
    if (t === 'message') return h('div.form', field('Text', ta(st, 'text', 4)), field('Anzeigen (Sek.)', inp(st, 'timeout_sec', { type: 'number', min: 0, style: { maxWidth: '120px' } }), '0 = bis der Benutzer sie schließt.'));
    if (t === 'logoff') return h('div.form', field('Sitzungen', chk(st, 'only_disconnected', 'nur getrennte Sitzungen abmelden')));
    return h('div');
  }
  function stepOptions(st) {
    return h('div.form',
      field('Titel', inp(st, 'title', { placeholder: 'leer = automatisch' })),
      field('Aktiv', chk(st, 'enabled', 'Schritt ausführen')),
      field('Ausführen als', sl(st, 'run_as', Object.entries(meta.step_run_as)), `„Wie Paket“ = ${meta.run_as[p.run_as]}.`),
      field('Bei Fehler', sl(st, 'on_error', [['stop', 'Paket anhalten (Abschluss-Schritte laufen trotzdem)'], ['continue', 'Mit dem nächsten Schritt weitermachen']])),
      field('Zeitlimit (Min.)', inp(st, 'timeout_min', { type: 'number', min: 0, style: { maxWidth: '120px' } }), '0 = Zeitlimit des Pakets.'));
  }
  function drawPanel() {
    curStep = null; pvBox = null;
    if (selKey === 'props') {
      // Bruch oder Abweichung: der Bereich „Repository-Dateien“ steht ganz oben, sonst am Ende
      const igBad = p.id && p.integrity && ['broken', 'warn'].includes(p.integrity.state);
      const igBox = p.id ? integrityBox(p, p.editable !== false, (r) => { p.integrity = r; }) : null;
      if (igBad) igBox.style.marginTop = '0';
      panel.replaceChildren(igBad ? igBox : null, h('h3', igBad ? { style: { marginTop: '18px' } } : {}, 'Paket-Eigenschaften'), h('div.form',
        field('Name', inp(p, 'name')), field('Version', inp(p, 'version')), field('Beschreibung', h('textarea', { rows: 3, value: p.description || '', style: { fontFamily: 'inherit', fontSize: '13px', minHeight: '60px' }, oninput: (e) => { p.description = e.target.value; } })),
        meta.tenants.length ? field('Mandant', sl(p, 'tenant_id', [['', '— keiner (global) —'], ...meta.tenants.map((x) => [x.id, x.name])], true)) : null,
        field('Ausführen als', sl(p, 'run_as', Object.entries(meta.run_as)), `Standard ist das Dienstkonto (${meta.service_login || 'nicht eingerichtet'}). „Lokales System“ und „Angemeldeter Benutzer“ bei Bedarf; einzelne Schritte können abweichen.`),
        field('Zeitlimit (Min.)', inp(p, 'timeout_min', { type: 'number', min: 1, style: { maxWidth: '120px' } }), 'Je Schritt.'),
        field('Neustart am Ende', sl(p, 'reboot', [['never', 'Nie'], ['if_required', 'Wenn erforderlich (3010/1641)'], ['always', 'Immer']])),
        field('Benachrichtigung', sl(p, 'notify_user', [['', 'wie in den Einstellungen'], ['on', 'immer'], ['off', 'nie']]),
          'Windows-Benachrichtigung für den angemeldeten Benutzer nach erfolgreicher Installation/Aktualisierung („… wurde installiert.“). „Immer“ meldet auch reine Skript-Pakete.'),
        field('Danach scannen', sel([['', 'Anwendungen (Standard)'], ...meta.profiles.filter((x) => x.name !== 'Anwendungen').map((x) => [x.id, x.name]), ['none', 'Nicht scannen']],
          !p.rescan ? 'none' : (p.rescan_profile_id || ''), { onchange: (e) => { p.rescan = e.target.value === 'none' ? 0 : 1; p.rescan_profile_id = e.target.value && e.target.value !== 'none' ? Number(e.target.value) : null; } }),
        'Nach jeder Installation oder Deinstallation wird der Computer mit diesem Scanprofil neu inventarisiert.')),
        igBad ? null : igBox);
      return;
    }
    if (selKey === 'conds') {
      panel.replaceChildren(h('h3', 'Bedingungen des Pakets'), conditionsEditor(p.conditions, meta, { hint: 'Sind sie nicht erfüllt, wird das Paket auf dem Computer übersprungen (Status „Übersprungen“).' }));
      return;
    }
    const st = p.steps[selKey.phase][selKey.idx];
    if (!st) { selKey = 'props'; drawPanel(); return; }
    curStep = st;
    pvBox = h('div.pv-box', h('div.muted', 'Wird berechnet …'));
    const tabs = tabsBox([
      ['Details', h('div', stepDetails(st), h('h3', { style: { margin: '14px 0 6px' } }, 'Wird ausgeführt'), pvBox)],
      ['Bedingungen', conditionsEditor(st.conditions, meta, { hint: 'Zusätzlich zu den Bedingungen des Pakets. Nicht erfüllt = Schritt wird übersprungen.' })],
      ['Optionen', stepOptions(st)],
    ]);
    // Bezeichnung des Schritts (erscheint links in der Liste und im Bereitstellungsprotokoll); leer = automatisch
    const auto = stepLabel({ ...st, title: '' });
    const titleIn = h('input', { value: st.title || '', placeholder: auto, maxlength: 200, style: { flex: '1', minWidth: '220px' },
      oninput: (e) => { st.title = e.target.value; drawTree(); } });
    panel.replaceChildren(h('div.row', h('h3', { style: { margin: 0 } }, `${dict(meta.phases)[selKey.phase]} · Schritt ${selKey.idx + 1}: ${meta.step_types[st.type]}`)),
      h('div.row.step-title', { style: { gap: '8px', alignItems: 'center', margin: '10px 0 4px' } }, h('label', { style: { minWidth: '110px' } }, 'Bezeichnung'), titleIn),
      h('div.muted.small', { style: { margin: '0 0 8px 118px' } }, 'Eigener Name für diesen Schritt – erscheint in der Liste links und im Protokoll. Leer = automatisch.'), tabs);
    updPreview();
  }
  const dict = (pairs) => Object.fromEntries(pairs);
  function tenantFolder() { return ((meta.tenants.find((x) => x.id === Number(p.tenant_id)) || {}).repo_folder) || ''; }
  drawTree(); drawPanel();
  const body = () => ({ ...p, steps: Object.fromEntries(PH.map(([k]) => [k, p.steps[k].map((s) => { const c = { ...s }; delete c._msi; delete c.package; return c; })])) });
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Paket „${p.name}“ löschen?`, 'Löschen', true))) return false; await DEL(`/api/packages/${p.id}`); toast('Gelöscht', 'ok'); route(); } });
    buttons.splice(1, 0, { label: 'Speichern & bereitstellen …', icon: 'send', onClick: async () => {
      if (await saveWithDup((x) => PUT(`/api/packages/${p.id}`, { ...body(), ...x })) === false) return false;
      route(); deployDlg({ packageId: p.id });
    } });
  }
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    // Duplikatsperre (2.27): gleiche Software in einem anderen Bereich → Hinweis; Hauptadmins dürfen übersteuern
    const r = await saveWithDup((x) => (isNew ? POST('/api/packages', { ...body(), ...x }) : PUT(`/api/packages/${p.id}`, { ...body(), ...x })));
    if (r === false) return false;
    if (isNew) p.id = r.id;
    toast('Paket gespeichert', 'ok'); PKG_META = null; route();
  } });
  if (!isNew && isOU() && p.editable === false) {
    // OU-Admin und zentrales Paket: ansehen und auf eigene Computer verteilen – ändern nur als eigene Kopie
    buttons.length = 0;
    buttons.push({ label: 'Schließen' });
    if (mayBuild()) buttons.push({ label: 'Als eigenes Paket kopieren', icon: 'plus', onClick: async () => {
      const r = await POST(`/api/packages/${p.id}/duplicate`); toast('Kopie angelegt', 'ok'); PKG_META = null; route(); packageDlg(r.id);
    } });
    buttons.push({ label: 'Bereitstellen …', icon: 'send', cls: 'primary', onClick: () => { deployDlg({ packageId: p.id }); } });
  }
  modal(isNew ? 'Neues Paket' : `Paket: ${p.name}`, h('div.pkg-editor', tree, panel), buttons, { wide: true, sticky: true, xwide: true, help: 'packages' });
  if (isNew) setTimeout(() => { const n = panel.querySelector('input'); if (n) n.focus(); }, 60);
}

// ---------------------------------------------------------------------------
// Zeitpläne
// ---------------------------------------------------------------------------
const SCHED_STATUS = { success: ['Erfolgreich', 'ok'], failed: ['Fehlgeschlagen', 'err'], queued: ['Wartend', 'info'], running: ['Läuft', 'info'],
  skipped: ['Übersprungen', ''], offline: ['Offline', ''], cancelled: ['Abgebrochen', ''] };
const schedBadge = (r) => (r.pending ? h('span.badge.info', 'wartet auf online') : r.status ? h('span.badge', { class: (SCHED_STATUS[r.status] || ['', ''])[1] }, (SCHED_STATUS[r.status] || [r.status])[0]) : h('span.muted', '—'));

async function viewSchedules() {
  setTop('Zeitpläne', 'Automatisch verteilen und aktualisieren – Pakete oder winget-Updates, mit Auslösern, Heartbeat und Zeitfenster',
    mayBuild() ? btn('Neuer Zeitplan ▾', (e) => { const r = e.currentTarget.getBoundingClientRect(); contextMenu(r.left, r.bottom + 2, [
      { label: 'Pakete verteilen …', icon: 'box', onClick: () => scheduleDlg(null, { kind: 'packages' }) },
      { label: 'winget-Updates …', icon: 'refresh', onClick: () => scheduleDlg(null, { kind: 'winget' }) }]); }, { icon: 'plus', cls: 'primary' }) : null);
  const rows = await GET('/api/schedules');
  const t = table([
    { key: 'name', label: 'Name', render: (r) => h('span', h('a', { href: '#', onclick: (e) => { e.preventDefault(); scheduleDlg(r.id); } }, r.name),
      r.enabled ? null : h('span.badge', { style: { marginLeft: '6px' } }, 'deaktiviert')), value: (r) => r.name },
    { key: 'kind', label: 'Art', value: (r) => (r.kind === 'winget' ? 'winget-Updates' : 'Pakete'),
      render: (r) => h('span.badge', { class: r.kind === 'winget' ? 'info' : 'accent' }, r.kind === 'winget' ? 'winget-Updates' : 'Pakete') },
    { key: 'package_names', label: 'Was', value: (r) => r.package_names.join(' → '), cls: 'trunc' },
    { key: 'triggers_text', label: 'Auslöser', value: (r) => r.triggers_text.join(' · ') || '— nur manuell —', cls: 'wrap' },
    { key: 'window_text', label: 'Zeitfenster', render: (r) => (r.window_text ? h('span', { title: r.in_window ? 'Zeitfenster ist gerade offen' : 'außerhalb des Zeitfensters' },
      h('span.dot', { class: r.in_window ? 'on' : 'off' }), ' ', r.window_text, r.deferred ? h('span.badge.info', { style: { marginLeft: '4px' } }, 'wartet') : null) : h('span.muted', 'immer')) , value: (r) => r.window_text || '' },
    { key: 'members', label: 'Ziele', cls: 'right' },
    { key: 'next_run', label: 'Nächster Lauf', value: (r) => fmtDate(r.next_run), sort: (r) => r.next_run || 'z' },
    { key: 'last_run', label: 'Letzter Lauf', render: (r) => h('span', { title: fmtDate(r.last_run) }, relTime(r.last_run)), sort: (r) => r.last_run || '' },
    { key: 'state', label: 'Zielverlauf', value: (r) => r.failed * 1000 + r.ok, render: (r) => h('span.row', { style: { gap: '4px' } },
      r.ok ? h('span.badge.ok', `${r.ok} OK`) : null, r.failed ? h('span.badge.err', `${r.failed} Fehler`) : null, r.pending ? h('span.badge.info', `${r.pending} warten`) : null) },
  ], rows, { key: 'schedules', sort: 'name', noun: 'Zeitpläne', onRow: (r) => scheduleDlg(r.id), empty: 'Noch keine Zeitpläne.',
    onContext: (r, e) => contextMenu(e.clientX, e.clientY, [
      { label: 'Bearbeiten', icon: 'edit', onClick: () => scheduleDlg(r.id) },
      { label: 'Jetzt ausführen', icon: 'send', onClick: () => runSchedule(r) },
      { label: 'Vorschau', icon: 'list', onClick: () => schedulePreview(r) },
      { label: 'Duplizieren', icon: 'coll', onClick: async () => { if (await run(() => POST(`/api/schedules/${r.id}/duplicate`), 'Kopie angelegt (deaktiviert)')) route(); } },
      '-',
      { label: 'Löschen', icon: 'trash', onClick: async () => { if (await confirmDlg(`Zeitplan „${r.name}“ löschen?`, 'Löschen', true)) { await run(() => DEL(`/api/schedules/${r.id}`), 'Gelöscht'); route(); } } },
    ]) });
  setContent(h('div', { style: { height: '100%', display: 'flex' } }, h('div.pane.card', t.el)));
}

async function schedulePreview(s) {
  const p = await run(() => GET(`/api/schedules/${s.id}/preview`));
  if (!p) return;
  if (p.kind === 'winget') {
    showOutput(`Vorschau: ${s.name}`, p.items.length ? p.items.map((x) => `${x.winget_id}\n   ${x.computers.join(', ')}${x.offline.length ? `\n   (offline: ${x.offline.join(', ')})` : ''}`).join('\n\n')
      : 'Derzeit keine veraltete Software mit winget-Zuordnung auf den Zielen.');
  } else showOutput(`Vorschau: ${s.name}`, `${p.members} Ziel(e), davon ${p.online} online.`);
}

async function runSchedule(s, ids = null, force = false) {
  const r = await run(() => POST(`/api/schedules/${s.id}/run`, { ids, force }));
  if (!r) return;
  if (r.count > 1) { toast(`${r.count} Bereitstellungen gestartet`, 'ok'); go('#/deployments'); }
  else if (r.deployment_id) { toast('Bereitstellung gestartet', 'ok'); go(`#/deployment/${r.deployment_id}`); }
  else if (r.blocked) toast(r.blocked, 'err');
  else toast(s.kind === 'winget' ? 'Nichts zu tun – keine veraltete Software auf den Online-Zielen (offline Ziele sind vorgemerkt).'
    : 'Nichts zu tun – alle Ziele sind bereits versorgt, offline (vorgemerkt), in der Wartezeit oder laufen noch. „Erneut versorgen“ erzwingt es.');
}

async function scheduleDlg(id, preset = {}) {
  const [meta] = await Promise.all([pkgMeta(true)]);
  let s = id ? await GET(`/api/schedules/${id}`) : null;
  const isNew = !s;
  const kind0 = preset.kind || 'packages';
  s = s ? JSON.parse(JSON.stringify(s)) : { name: '', description: '', enabled: 1, kind: kind0,
    triggers: kind0 === 'winget' ? [{ type: 'interval', every: 24, unit: 'h', enabled: true, start: '' }, { type: 'heartbeat', enabled: true }] : [],
    targets: { computers: [], collections: [] }, packages: preset.packages || [],
    winget: { mode: 'all', ids: '', exclude: '', scope: '' },
    window: { enabled: kind0 === 'winget', days: [1, 2, 3, 4, 5], start: '07:00', end: '18:00' },
    options: { stop_on_fail: true, offline: 'wait', skip_successful: true, max_attempts: kind0 === 'winget' ? 0 : 0,
      retry_wait_hours: kind0 === 'winget' ? 24 : 0, renew_after_hours: 0, retry_failed: true }, tenant_id: null };
  if (isNew && s.triggers[0] && s.triggers[0].type === 'interval') {
    const d = new Date(); const pad = (n) => String(n).padStart(2, '0');
    s.triggers[0].start = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:00`;
  }
  if (isNew && preset.packages && preset.packages.length) s.name = (meta.packages.find((x) => x.id === preset.packages[0]) || {}).name || '';
  const name = h('input', { value: s.name, placeholder: s.kind === 'winget' ? 'z. B. Software aktuell halten' : 'z. B. Office-Grundausstattung' });
  const desc = h('textarea', { rows: 2, value: s.description || '' });
  const en = h('input', { type: 'checkbox', checked: !!s.enabled });
  const tenant = meta.tenants.length ? sel([['', '— keiner —'], ...meta.tenants.map((x) => [x.id, x.name])], s.tenant_id || '') : null;
  const kindSeg = h('div.seg');
  // Pakete in Reihenfolge
  const pkBox = h('div');
  const addPk = sel([['', '+ Paket hinzufügen …'], ...meta.packages.map((x) => [x.id, x.name])], '', { onchange: (e) => { if (e.target.value) { s.packages.push(Number(e.target.value)); e.target.value = ''; drawPk(); } } });
  function drawPk() {
    pkBox.replaceChildren(...(s.packages.length ? s.packages.map((pid, i) => h('div.cond-line', h('span.pkg-n', String(i + 1)), h('b', (meta.packages.find((x) => x.id === pid) || { name: `#${pid}` }).name), h('div.spacer'),
      h('button.ghost.small', { type: 'button', onclick: () => { if (i) { s.packages.splice(i - 1, 0, s.packages.splice(i, 1)[0]); drawPk(); } } }, '↑'),
      h('button.ghost.small', { type: 'button', onclick: () => { if (i < s.packages.length - 1) { s.packages.splice(i + 1, 0, s.packages.splice(i, 1)[0]); drawPk(); } } }, '↓'),
      h('button.ghost.small', { type: 'button', title: 'Entfernen', onclick: async () => { if (!(await askRemove(`Paket „${(meta.packages.find((x) => x.id === pid) || { name: `#${pid}` }).name}“ aus dem Zeitplan entfernen?`))) return; s.packages.splice(i, 1); drawPk(); } }, icon('trash')))) : [h('p.muted', 'Noch keine Pakete.')]), addPk);
  }
  drawPk();
  // winget
  const w = s.winget;
  const wMode = sel([['all', 'Alle veralteten Programme'], ['specific', 'Nur bestimmte winget-IDs']], w.mode, { onchange: (e) => { w.mode = e.target.value; idsRow.forEach((x) => x && (x.style.display = w.mode === 'specific' ? '' : 'none')); } });
  const wIds = h('textarea', { rows: 3, value: w.ids || '', placeholder: 'z. B. Mozilla.Firefox, 7zip.7zip, Notepad++.Notepad++', oninput: (e) => { w.ids = e.target.value; } });
  const wEx = h('textarea', { rows: 2, value: w.exclude || '', placeholder: 'z. B. Oracle.JavaRuntimeEnvironment', oninput: (e) => { w.exclude = e.target.value; } });
  const wScope = sel([['', 'Standard des Pakets'], ['machine', 'Computer (--scope machine)'], ['user', 'Benutzer (--scope user)']], w.scope || '', { onchange: (e) => { w.scope = e.target.value; } });
  const idsRow = field('winget-IDs', wIds, 'Durch Komma oder Zeilenumbruch getrennt.');
  idsRow.forEach((x) => x && (x.style.display = w.mode === 'specific' ? '' : 'none'));
  const wingetBox = h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Aktualisiert auf den Zielen alle Programme, für die das Inventar eine neuere Version im winget-Katalog kennt (sichere Zuordnung). Je Programm entsteht eine Bereitstellung „winget upgrade“; nach dem Update wird neu inventarisiert. Computer vor Windows 10 1809 / Server 2019 werden ausgelassen.'),
    h('div.form', field('Programme', wMode), idsRow, field('Ausnahmen', wEx, 'Diese winget-IDs nie automatisch aktualisieren.'), field('Bereich', wScope)));
  // Ziele
  const colSet = new Set(s.targets.collections);
  const colBox = await collPicker(colSet);
  const picker = await computerPicker(s.targets.computers);
  // Zeitfenster
  const win = s.window;
  const winBody = h('div.row', { style: { flexWrap: 'wrap', gap: '6px', marginTop: '6px' } },
    ...DAYS.map(([n, l]) => h('label.chk', h('input', { type: 'checkbox', checked: win.days.includes(n), onchange: (e) => { win.days = win.days.filter((x) => x !== n); if (e.target.checked) win.days.push(n); win.days.sort(); } }), l)),
    h('span.muted', 'von'), h('input', { type: 'time', value: win.start, onchange: (e) => { win.start = e.target.value; } }),
    h('span.muted', 'bis'), h('input', { type: 'time', value: win.end, onchange: (e) => { win.end = e.target.value; } }));
  const winEn = h('input', { type: 'checkbox', checked: !!win.enabled, onchange: (e) => { win.enabled = e.target.checked; winBody.style.opacity = win.enabled ? 1 : 0.45; } });
  winBody.style.opacity = win.enabled ? 1 : 0.45;
  const winBox = h('div.card', { style: { marginTop: '12px' } }, h('div.card-b',
    h('label.chk', winEn, h('b', 'Nur im Zeitfenster ausführen')), winBody,
    h('p.muted.small', { style: { margin: '6px 0 0' } }, 'Fällige Läufe außerhalb des Fensters werden zu Beginn des Fensters nachgeholt; Computer, die außerhalb online kommen (Heartbeat), werden vorgemerkt. Über Mitternacht möglich (z. B. 22:00–05:00). „Jetzt ausführen“ ignoriert das Fenster.')));
  // Optionen
  const o = s.options;
  if (o.retry_failed === undefined) o.retry_failed = true;  // Zeitpläne aus älteren Versionen: Standard „nachholen“
  const oc = (k, label) => h('label.chk', { style: { display: 'flex' } }, h('input', { type: 'checkbox', checked: !!o[k], onchange: (e) => { o[k] = e.target.checked; } }), label);
  const num = (k, max = 8760) => h('input', { type: 'number', min: 0, max, value: o[k] || 0, style: { maxWidth: '90px' }, onchange: (e) => { o[k] = Number(e.target.value); } });
  const offSel = sel([['wait', 'vormerken und versorgen, sobald sie online sind (Heartbeat)'], ['skip', 'überspringen']], o.offline, { onchange: (e) => { o.offline = e.target.value; } });
  const trig = triggerEditor(s.triggers, { noCatchUp: true, only: ['once', 'interval', 'daily', 'weekly', 'monthly', 'heartbeat'], empty: 'Keine Auslöser – der Zeitplan läuft nur, wenn er manuell gestartet wird.' });
  const histBox = h('div');
  if (!isNew) {
    const ht = table([
      { key: 'name', label: 'Computer', render: (r) => h('a', { href: `#/computer/${r.id}` }, r.name) },
      { key: 'online', label: 'Online', render: (r) => h('span.dot', { class: r.online ? 'on' : 'off' }) },
      { key: 'status', label: 'Status', render: schedBadge, value: (r) => (r.pending ? 'wartet' : r.status || '') },
      { key: 'last_run', label: 'Letzter Lauf', value: (r) => fmtDate(r.last_run), sort: (r) => r.last_run || '' },
      { key: 'last_success', label: 'Letzter Erfolg', value: (r) => fmtDate(r.last_success), sort: (r) => r.last_success || '' },
      { key: 'attempts', label: 'Fehlversuche', cls: 'right' },
      { key: 'message', label: 'Meldung', cls: 'wrap' },
      { key: 'dep', label: '', nosort: true, render: (r) => (r.deployment_id ? h('a', { href: `#/deployment/${r.deployment_id}` }, `#${r.deployment_id}`) : '') },
    ], s.history, { sort: 'name', noun: 'Ziele', selectable: true, fill: false, key: null, empty: 'Noch keine Läufe.' });
    ht.actions.append(btn('Auswahl erneut versorgen', () => { const ids = ht.selectedIds(); if (!ids.length) { toast('Bitte Computer auswählen', 'err'); return; } runSchedule(s, ids, true); }, { cls: 'small', icon: 'send' }),
      btn('Verlauf der Auswahl zurücksetzen', async () => { const ids = ht.selectedIds(); if (!ids.length) { toast('Bitte Computer auswählen', 'err'); return; } if (!(await confirmDlg(`Zielverlauf für ${ids.length} Computer zurücksetzen? Sie gelten danach als noch nicht versorgt.`, 'Zurücksetzen', true))) return; await run(() => POST(`/api/schedules/${s.id}/reset`, { ids }), 'Zurückgesetzt'); }, { cls: 'small ghost' }));
    histBox.append(ht.el);
  }
  const whatTab = h('div');
  const pkgOpts = h('div.form', field('Bei Fehler', oc('stop_on_fail', 'weitere Pakete für diesen Computer nicht mehr bereitstellen')),
    field('Erfolgreiche Ziele', oc('skip_successful', 'nicht erneut versorgen')),
    field('Erneut versorgen nach (Std.)', num('renew_after_hours'), '0 = nie. Sonst bekommen erfolgreiche Ziele das Paket nach dieser Zeit erneut – praktisch mit dynamischen Sammlungen wie „Software fehlt oder ist veraltet“.'),
    field('Max. Fehlversuche', num('max_attempts', 100), '0 = unbegrenzt. Danach wird der Computer nicht mehr versucht, bis der Verlauf zurückgesetzt wird.'));
  const optTab = h('div');
  const drawKind = () => {
    kindSeg.replaceChildren(...[['packages', 'Pakete verteilen'], ['winget', 'winget-Updates']].map(([k, l]) => h('button', { type: 'button', class: s.kind === k ? 'on' : '',
      onclick: () => { s.kind = k; drawKind(); } }, l)));
    whatTab.replaceChildren(s.kind === 'winget' ? wingetBox : h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Werden je Computer nacheinander in dieser Reihenfolge bereitgestellt.'), pkBox));
    optTab.replaceChildren(h('div.form',
      field('Offline Computer', offSel),
      field('Wartezeit nach Fehlschlag (Std.)', num('retry_wait_hours'), s.kind === 'winget' ? 'Ein fehlgeschlagenes Update desselben Programms wird auf diesem Computer frühestens nach dieser Zeit erneut versucht.' : '0 = beim nächsten Lauf erneut versuchen.'),
      field('Fehlgeschlagene nachholen', oc('retry_failed', 'fehlgeschlagene Computer automatisch erneut versorgen, ohne auf den nächsten Termin zu warten'),
        s.kind === 'winget' ? 'Sobald der Computer erreichbar ist und die Wartezeit nach Fehlschlag (mindestens 1 Stunde) um ist, höchstens 3 Mal, im Zeitfenster. Konnte ein Lauf gar nicht starten, wird er alle 15 Minuten wiederholt.'
          : 'Sobald der Computer erreichbar ist und die Wartezeit nach Fehlschlag (mindestens 1 Stunde) um ist, höchstens „Max. Fehlversuche“ Mal (bei 0: 3 Mal), im Zeitfenster. Konnte ein Lauf gar nicht starten, wird er alle 15 Minuten wiederholt.')),
    ...(s.kind === 'packages' ? [pkgOpts] : []));
    if (tabsEl) tabsEl.setLabel(2, s.kind === 'winget' ? 'Programme' : 'Pakete');
  };
  let tabsEl = null;
  const tabs = [
    ['Allgemein', h('div.form', field('Art', kindSeg, 'Pakete verteilen = gespeicherte Pakete an die Ziele; winget-Updates = veraltete Software automatisch aktualisieren.'),
      field('Name', name), field('Beschreibung', desc), tenant ? field('Mandant', tenant) : null, field('Aktiv', h('label.chk', en, 'Auslöser ausführen')))],
    ['Auslöser', h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Wann der Zeitplan läuft. „Heartbeat“ versorgt Ziele, sobald sie erreichbar sind und noch nicht versorgt wurden – also direkt nach dem Einschalten oder Wake-on-LAN, bei einem neuen Zeitplan oder neuen Mitgliedern einer Sammlung beim nächsten Heartbeat (Standard alle 5 Minuten, manuell sofort). Bereits versuchte Ziele wiederholt er nicht ständig. Ohne Heartbeat-Auslöser werden beim Einschalten nur vorgemerkte Computer versorgt.'), trig, winBox)],
    ['Pakete', whatTab],
    ['Ziele', h('div', h('h3', 'Sammlungen'), h('p.muted.small', 'Mitglieder werden bei jedem Lauf neu ermittelt – mit dynamischen Sammlungen pflegt sich der Zeitplan selbst.'), colBox,
      h('h3', { style: { marginTop: '12px' } }, 'Einzelne Computer'), picker.el)],
    ['Optionen', optTab],
  ];
  if (!isNew) tabs.push(['Zielverlauf', histBox]);
  const buttons = [{ label: 'Abbrechen' }];
  const body = () => ({ name: name.value, description: desc.value, enabled: en.checked, kind: s.kind, tenant_id: tenant ? tenant.value || null : null,
    triggers: s.triggers.map((t) => ({ ...t, at: t.at && t.at.replace('T', ' '), start: t.start && t.start.replace('T', ' '), end: t.end ? t.end.replace('T', ' ') : null })),
    targets: { computers: picker.selected(), collections: [...colSet] }, packages: s.packages, winget: w, window: win, options: o });
  if (!isNew) {
    buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Zeitplan „${s.name}“ löschen?`, 'Löschen', true))) return false; await DEL(`/api/schedules/${s.id}`); toast('Gelöscht', 'ok'); route(); } });
    // Beide Knöpfe speichern vorher – deshalb so beschriftet, und die Liste wird danach aktualisiert
    buttons.splice(1, 0, { label: 'Speichern & Vorschau', icon: 'list', close: false, onClick: async () => { await PUT(`/api/schedules/${s.id}`, body()); S.dirtyList = true; await schedulePreview({ ...s, ...body() }); } },
      { label: 'Speichern & ausführen', icon: 'send', onClick: async () => { await PUT(`/api/schedules/${s.id}`, body()); await runSchedule({ ...s, kind: s.kind }); route(); } });
  }
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    if (isNew) await POST('/api/schedules', body()); else await PUT(`/api/schedules/${s.id}`, body());
    toast('Zeitplan gespeichert', 'ok'); if (parseHash().parts[0] === 'schedules') route(); else go('#/schedules');
  } });
  tabsEl = tabsBox(tabs);
  drawKind();
  modal(isNew ? (s.kind === 'winget' ? 'Neuer Zeitplan: winget-Updates' : 'Neuer Zeitplan') : `Zeitplan: ${s.name}`, tabsEl, buttons,
    { wide: true, sticky: true, help: 'schedules', onClose: () => { if (S.dirtyList) { S.dirtyList = false; route(); } } });
}

// --- Einstellungen: Mandanten ----------------------------------------------------------------
function tenantsCard() {
  const body = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Mandanten'), btn('Neuer Mandant', () => tenantDlg(null), { cls: 'small', icon: 'plus' })), body);
  async function tenantDlg(t) {
    const cols = await collections(true);
    t = t || { name: '', description: '', collection_id: null, repo_folder: '', admin_group: '', allow_packages: 0 };
    const name = h('input', { value: t.name });
    const desc = h('input', { value: t.description || '' });
    const col = sel([['', '— alle Computer —'], ...collectionOptions(cols, false).filter((x) => x[0])], t.collection_id || '');
    const folder = h('input', { value: t.repo_folder || '', placeholder: 'leer = automatisch „Mandanten\\<Name>“' });
    const quota = h('input', { type: 'number', min: 0, value: t.quota_mb ?? '', placeholder: 'leer = Standard aus den Einstellungen', style: { width: '260px' } });
    const grp = h('input', { value: t.admin_group || '', placeholder: 'z. B. PK-OU-Hamburg (leer = keine OU-Admins)', style: { flex: 1 } });
    const allow = h('input', { type: 'checkbox', checked: !!t.allow_packages });
    const allowRow = h('label.chk', allow, tr('dürfen eigene Pakete und Zeitpläne für diesen Mandanten anlegen'));
    const syncAllow = () => { const on = !!grp.value.trim(); allow.disabled = !on; allowRow.style.opacity = on ? '' : '.5'; };
    grp.addEventListener('input', syncAllow); syncAllow();
    const pick = btn('Wählen …', async () => {
      const chosen = await adPicker('group');
      if (chosen && chosen.length) { grp.value = chosen[0].sam || chosen[0].name; syncAllow(); }
    }, { cls: 'small' });
    modal(t.id ? `Mandant: ${t.name}` : 'Neuer Mandant', h('div.form', field('Name', name), field('Beschreibung', desc),
      field('Computer', col, 'Pakete dieses Mandanten werden nur auf Computer dieser Sammlung verteilt (andere werden ignoriert). Für eine OU: dynamische Sammlung mit Filter „Distinguished Name / OU“ – oder die automatisch angelegte Sammlung der OU.'),
      field('Repository-Ordner', folder, 'Pakete dieses Mandanten dürfen nur Dateien aus diesem Ordner verwenden (wird beim Speichern geprüft). OU-Admins sehen und bearbeiten im Repository nur diesen Ordner. Leer lassen: bei „eigene Pakete“ legt POCKET KNIFE „Mandanten\\<Name>“ selbst an.'),
      field('Speicherkontingent (MB)', quota, 'Wie viel die OU-Admins dieses Mandanten im Repository hochladen dürfen. 0 = unbegrenzt.'),
      field('OU-Admins', h('div', h('div.row', grp, pick), h('div', { style: { marginTop: '8px' } }, allowRow)),
        'AD-Gruppe, deren Mitglieder sich anmelden dürfen – aber nur für die Computer dieses Mandanten. Sie gehören NICHT in die Gruppe der Hauptadmins. Sie sehen nur ihre Computer, deren Software, Bereitstellungen und Protokoll; zentrale Pakete können sie verteilen, aber nicht ändern. Einstellungen, Scanprofile, Kiosk-Verwaltung und Berichte bleiben den Hauptadmins vorbehalten.')), [
      { label: 'Abbrechen' },
      { label: 'Speichern', cls: 'primary', onClick: async () => {
        const b = { name: name.value, description: desc.value, collection_id: col.value || null, repo_folder: folder.value,
          admin_group: grp.value.trim(), allow_packages: allow.checked && !!grp.value.trim(), quota_mb: quota.value === '' ? null : Number(quota.value) };
        if (t.id) await PUT(`/api/tenants/${t.id}`, b); else await POST('/api/tenants', b);
        toast('Gespeichert', 'ok'); PKG_META = null; draw();
      } }]);
  }
  async function draw() {
    let rows;
    try { rows = await GET('/api/tenants'); } catch (e) { body.replaceChildren(h('p.muted', e.message)); return; }
    const t = table([
      { key: 'name', label: 'Name', render: (r) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); tenantDlg(r); } }, r.name) },
      { key: 'collection', label: 'Computer', value: (r) => r.collection || 'alle' },
      { key: 'repo_folder', label: 'Repository-Ordner', render: (r) => h('span.raw.small', r.repo_folder || '—') },
      { key: 'admin_group', label: 'OU-Admins', value: (r) => (r.admin_group ? r.admin_group + (r.allow_packages ? ' · ' + tr('eigene Pakete') : '')
        + (r.admin_group_sid ? '' : ' · ' + tr('bitte einmal neu speichern (Gruppe wird dann eindeutig festgelegt)')) : '—') },
      { key: 'packages', label: 'Pakete', cls: 'right', render: (r) => h('a', { href: `#/packages?tenant=${r.id}` }, String(r.packages)) },
      { key: 'schedules', label: 'Zeitpläne', cls: 'right' },
      { key: 'x', label: '', nosort: true, render: (r) => btn('Löschen', async () => {
        if (await confirmDlg(`Mandant „${r.name}“ löschen?`, 'Löschen', true)) { if (await run(() => DEL(`/api/tenants/${r.id}`), 'Gelöscht') !== undefined) draw(); }
      }, { cls: 'small danger' }) },
    ], rows, { fill: false, search: false, columns: false, key: null, noun: 'Mandanten', empty: 'Keine Mandanten. Mandanten trennen Pakete und Zeitpläne z. B. nach Kunden oder Standorten.' });
    body.replaceChildren(t.el, h('p.muted.small', 'Pakete und Zeitpläne lassen sich einem Mandanten zuordnen und in der Paketliste danach filtern. Mit einer OU-Admin-Gruppe betreuen deren Mitglieder nur die Computer dieses Mandanten.'));
  }
  draw();
  return card;
}

// --- Bereitstellung: Ergebnis je Schritt -----------------------------------------------------
const STEP_STATUS = { success: ['OK', 'ok'], failed: ['Fehler', 'err'], skipped: ['Übersprungen', ''], notrun: ['Nicht ausgeführt', ''], running: ['Läuft', 'info'] };
// Schritt erneut ausführen: nur diesen Schritt oder ab diesem Schritt (neue Bereitstellung mit gekürztem Paket)
async function retryStep(did, sid, title, mode, targetIds, names) {
  const who = names.length === 1 ? names[0] : `${names.length} Computern`;
  const what = mode === 'step' ? `Nur den Schritt „${title}“` : `Den Schritt „${title}“ und alle folgenden Schritte (inkl. Abschluss)`;
  if (!(await confirmDlg(`${what} auf ${who} erneut ausführen?\n\nEs wird eine neue Bereitstellung mit diesem Ausschnitt des Pakets erstellt; Paket-Bedingungen und „Ausführen als“ bleiben gleich.`, 'Wiederholen'))) return;
  const r = await run(() => POST(`/api/deployments/${did}/retry`, { step_id: sid, mode, target_ids: targetIds }), 'Wiederholung gestartet');
  if (r) { closeModals(); go(`#/deployment/${r.id}`); }
}
function retryMenu(e, did, sid, title, targetIds, names) {
  const b = e.currentTarget.getBoundingClientRect();
  contextMenu(b.left, b.bottom + 2, [
    { label: 'Nur diesen Schritt wiederholen', icon: 'refresh', onClick: () => retryStep(did, sid, title, 'step', targetIds, names) },
    { label: 'Ab diesem Schritt wiederholen', icon: 'play', onClick: () => retryStep(did, sid, title, 'from', targetIds, names) },
  ]);
}

function targetDetails(r, did = null, canRetry = false) {
  let steps = [];
  try { steps = JSON.parse(r.steps || '[]'); } catch (e) { steps = []; }
  const top = (x) => (x.depth ?? Math.floor(((x.title || '').length - (x.title || '').trimStart().length) / 2)) === 0;
  const retryable = canRetry && did && r.computer_id && steps.some((x) => x.status === 'failed' && top(x) && x.sid);
  const tb = steps.length ? h('table', h('thead', h('tr', h('th', '#'), h('th', 'Abschnitt'), h('th', 'Schritt'), h('th', 'Status'), h('th', 'Exitcode'), h('th', 'Meldung'), retryable ? h('th', '') : null)),
    h('tbody', steps.map((x) => h('tr', h('td', x.n), h('td.muted', { pre: 'Vorbereitung', main: 'Schritte', post: 'Abschluss' }[x.phase] || x.phase),
      h('td', { style: { whiteSpace: 'pre' } }, x.title), h('td', h('span.badge', { class: (STEP_STATUS[x.status] || ['', ''])[1] }, (STEP_STATUS[x.status] || [x.status])[0])),
      h('td.mono', x.exit ?? ''), h('td.wrap', x.message),
      retryable ? h('td', x.status === 'failed' && top(x) && x.sid ? btn('Wiederholen ▾', (e) => retryMenu(e, did, x.sid, x.title.trim(), [r.id], [r.computer_name]),
        { cls: 'small', icon: 'refresh', title: 'Diesen Schritt auf diesem Computer erneut ausführen' }) : '') : null)))) : null;
  modal(`${r.computer_name}: ${r.message || ''}`.slice(0, 120), h('div', tb, h('h3', { style: { margin: '14px 0 6px' } }, 'Ausgabe'),
    h('pre.out', r.output || '(keine Ausgabe)')), [{ label: 'Schließen' }], { wide: true });
}

function repoBrowser(onPick, start = '', opt = {}) {
  let path = start || '';
  const list = h('div.list-pick', { style: { maxHeight: '360px' } });
  const crumb = h('div.mono.muted', { style: { marginBottom: '8px' } });
  // Suche: Datei- oder Ordnername ab dem aktuellen Ordner in allen Unterordnern (mit * und ? als Muster)
  const q = h('input', { type: 'search', placeholder: tr('Im Repository suchen – Datei- oder Ordnername, z. B. 7z*.msi'), style: { width: '100%', marginBottom: '8px' } });
  const note = h('div.muted.small', { style: { margin: '-4px 0 8px' } });
  let qt = null, qn = 0;
  q.addEventListener('input', () => {
    clearTimeout(qt);
    const v = q.value.trim();
    if (v.replace(/[*?]/g, '').length < 2) { qn++; note.textContent = ''; load(); return; }
    qt = setTimeout(() => search(v), 350);
  });
  // opt.folderOnly: nur einen Ordner wählen (Dateien sind dann nicht anklickbar); opt.onCancel: geschlossen ohne Wahl
  let picked = false;
  const pickFile = (folder, name) => { if (opt.folderOnly) return; picked = true; onPick(folder, name); m.close(); };
  const m = modal(opt.title || 'Repository durchsuchen', h('div', q, note, crumb, list), [
    { label: 'Abbrechen' },
    { label: 'Diesen Ordner wählen', cls: 'primary', onClick: () => { picked = true; onPick(path, null); } },
  ], { wide: true, onClose: () => { if (!picked && opt.onCancel) opt.onCancel(); } });
  const join = (a, b) => (a ? `${a}\\${b}` : b);
  async function search(v, refresh = false) {
    const my = ++qn;
    ln++;  // eine noch laufende Ordnerliste darf die Treffer nicht überschreiben
    note.textContent = tr(refresh ? 'Repository wird neu eingelesen – diese Suche läuft direkt auf der Freigabe …' : 'Suche läuft …');
    let r;
    // Immer im ganzen Repository (OU-Admins: im Ordner ihres Mandanten) – nicht nur im gerade geöffneten Ordner
    try { r = await GET(`/api/repository/search?q=${encodeURIComponent(v)}${refresh ? '&refresh=1' : ''}`); } catch (e) {
      if (my === qn) { note.textContent = e.message; list.replaceChildren(); }
      return;
    }
    if (my !== qn) return;  // eine neuere Suche läuft schon
    crumb.textContent = tr('Suche im ganzen Repository');
    const res = r.items.length
      ? fmtT(r.truncated ? '{0} Treffer (Suche abgebrochen – bitte genauer suchen)' : '{0} Treffer', r.items.length)
      : (r.truncated ? tr('Keine Treffer in der Zeit – bitte genauer suchen.') : tr('Keine Treffer.'));
    // Stand des Index (Arbeitsspeicher des Servers): neue Dateien erscheinen spätestens nach 5 Minuten
    const ix = r.index || {};
    const stand = r.cached ? (r.age < 60 ? tr('Stand: gerade eben') : fmtT('Stand: vor {0} Min.', Math.round(r.age / 60)))
      : (ix.building ? tr('direkt gesucht – Index wird gerade eingelesen') : tr('direkt gesucht'));
    note.replaceChildren(res + ' · ' + stand + (isOU() ? '' : ' · '),
      isOU() ? '' : h('a', { href: '#', onclick: (e) => { e.preventDefault(); search(v, true); } }, tr('Neu einlesen')));
    list.replaceChildren(...r.items.map((it) => h('div', {
      title: join(it.folder, it.name),
      onclick: () => {
        if (it.dir) { path = join(it.folder, it.name); q.value = ''; note.textContent = ''; load(); }
        else pickFile(it.folder, it.name);
      },
    }, icon(it.dir ? 'folder' : 'file'), ' ', h('span.raw', it.name), h('span.muted.small.raw', `   ${it.folder || '\\'}`),
    it.dir ? '' : h('span.muted.small', `  ${fmtBytes(it.size)}`))));
  }
  let ln = 0;
  // OU-Admin: oberhalb des Mandanten-Ordners gibt es nichts – dort kein „..“
  const ouRoots = isOU() ? (S.me.tenants || []).map((t) => (t.repo_folder || '').toLowerCase()).filter(Boolean) : [];
  async function load() {
    const my = ++ln;
    qn++;  // laufende Suche verwerfen
    let r;
    try { r = await GET('/api/repository?path=' + encodeURIComponent(path)); } catch (e) {
      if (my !== ln) return;
      if (path) { path = ''; load(); return; }
      toast(e.message, 'err');
    }
    if (my !== ln) return;
    if (!r) { list.replaceChildren(h('div.muted', 'Repository nicht erreichbar – Pfad unter Einstellungen prüfen.')); return; }
    path = r.path || path;  // OU-Admin: der Server beginnt im Ordner des Mandanten
    crumb.textContent = (r.root || '') + (path ? '\\' + path : '');
    const items = [];
    if (path && !ouRoots.includes(path.toLowerCase())) items.push(h('div', { onclick: () => { path = path.split('\\').slice(0, -1).join('\\'); load(); } }, icon('up'), ' ..'));
    for (const it of r.items) {
      items.push(h('div', {
        onclick: () => {
          if (it.dir) { path = path ? `${path}\\${it.name}` : it.name; load(); }
          else pickFile(path, it.name);
        },
      }, icon(it.dir ? 'folder' : 'file'), ' ', it.name, it.dir ? '' : h('span.muted.small', `  ${fmtBytes(it.size)}`)));
    }
    list.replaceChildren(...(items.length ? items : [h('div.muted', 'Leerer Ordner')]));
  }
  load();
  setTimeout(() => q.focus(), 50);
}

// ---------------------------------------------------------------------------
// Bereitstellen-Dialog
// ---------------------------------------------------------------------------
async function deployDlg(opt = {}) {
  const [pkgs, cols] = await Promise.all([GET('/api/packages'), collections()]);
  const mode = sel([['package', 'Gespeichertes Paket'], ['winget', 'winget (direkt)']], opt.wingetId ? 'winget' : 'package');
  const stop = h('input', { type: 'checkbox', checked: true });
  const stopRow = field('Bei Fehler', h('label.chk', stop, 'die weiteren Pakete für diesen Computer auslassen'), 'Mehrere Pakete laufen als eine Bereitstellung – je Computer nacheinander in der gezeigten Reihenfolge (nie zwei Installationen gleichzeitig).');
  const pkgPick = packagePicker(pkgs, opt.packageIds || (opt.packageId ? [opt.packageId] : []), (n) => stopRow.forEach((e) => e && (e.style.display = n > 1 && mode.value === 'package' ? '' : 'none')));
  const wid = h('input', { value: opt.wingetId || '', placeholder: 'z.B. Notepad++.Notepad++' });
  const wact = sel([['upgrade', 'Aktualisieren'], ['install', 'Installieren'], ['uninstall', 'Deinstallieren']], opt.wingetId ? 'upgrade' : 'install');
  const pkgRow = field('Pakete', pkgs.length ? pkgPick.el : h('span.muted', 'Keine Pakete vorhanden'));
  const wRow = [field('winget-ID', h('div.row', wid, btn('Suchen', () => wingetPicker((id) => (wid.value = id)), { cls: 'small' }))), field('Aktion', wact)];
  const hasIds = opt.computerIds && opt.computerIds.length;
  const tmode = sel([['computers', 'Computer auswählen'], ['collection', 'Sammlung']], hasIds || !opt.collectionId ? 'computers' : 'collection');
  const coll = sel(collectionOptions(cols, false), opt.collectionId || '');
  const picker = await computerPicker(opt.computerIds || []);
  const collRow = field('Sammlung', coll, 'Alle Computer, die beim Start Mitglied der Sammlung sind.');
  const pickRow = h('div.full', picker.el);
  const when = h('input', { type: 'datetime-local' });
  const toggle = () => {
    pkgRow.forEach((e) => e && (e.style.display = mode.value === 'package' ? '' : 'none'));
    stopRow.forEach((e) => e && (e.style.display = mode.value === 'package' && pkgPick.selected().length > 1 ? '' : 'none'));
    wRow.flat().forEach((e) => e && (e.style.display = mode.value === 'winget' ? '' : 'none'));
    collRow.forEach((e) => e && (e.style.display = tmode.value === 'collection' ? '' : 'none'));
    pickRow.style.display = tmode.value === 'computers' ? '' : 'none';
  };
  mode.addEventListener('change', toggle);
  tmode.addEventListener('change', toggle);
  const form = h('div.form', field('Quelle', mode), pkgRow, stopRow, wRow, field('Ziel', tmode), collRow, pickRow, field('Zeitpunkt', when, 'Leer = sofort starten.'));
  toggle();
  modal('Software bereitstellen', form, [
    { label: 'Abbrechen' },
    {
      label: 'Bereitstellen', cls: 'primary', icon: 'send', onClick: async () => {
        const body = { scheduled_at: when.value || null };
        if (tmode.value === 'computers') {
          body.computer_ids = picker.selected();
          if (!body.computer_ids.length) throw new Error('Bitte mindestens einen Computer auswählen.');
        } else body.collection_id = Number(coll.value);
        if (mode.value === 'package') {
          body.package_ids = pkgPick.selected();
          if (!body.package_ids.length) throw new Error('Bitte mindestens ein Paket auswählen.');
          body.stop_on_fail = stop.checked;
        } else { body.winget_id = wid.value.trim(); body.action = wact.value; }
        const r = await POST('/api/deployments', body);
        toast((body.scheduled_at ? 'Bereitstellung geplant' : 'Bereitstellung gestartet') + (r.ignored ? ` – ${r.ignored} Computer gehören nicht zum Mandanten und wurden ausgelassen` : ''), 'ok');
        go(`#/deployment/${r.id}`);
      },
    },
  ], { wide: true, sticky: true });
}

// Pakete auswählen (mehrere): Suche, Häkchen, gewählte Pakete als Chips in Ausführungsreihenfolge (ziehen zum Umsortieren)
function packagePicker(pkgs, preselected = [], onChange = null) {
  const order = preselected.map(Number).filter((id) => pkgs.some((p) => p.id === id));
  const q = h('input', { type: 'search', placeholder: 'Paket suchen …' });
  const list = h('div.pick-list');
  const chips = h('div.pick-chips');
  const info = h('span.muted.small');
  const label = (p) => `${p.name}${p.version ? ' ' + p.version : ''}`;
  let dragId = null;
  function drawChips() {
    chips.replaceChildren(...(order.length ? order.map((id, i) => {
      const p = pkgs.find((x) => x.id === id);
      const c = h('span.chip', { draggable: 'true', title: 'Ziehen zum Umsortieren' }, h('b', `${i + 1}.`), h('span.raw', label(p)),
        h('button', { type: 'button', title: 'Entfernen', onclick: () => { order.splice(order.indexOf(id), 1); draw(); } }, '×'));
      c.addEventListener('dragstart', (e) => { dragId = id; e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', String(id)); } catch (x) { /* egal */ } });
      c.addEventListener('dragover', (e) => { if (dragId !== null && dragId !== id) e.preventDefault(); });
      c.addEventListener('drop', (e) => { e.preventDefault(); if (dragId === null || dragId === id) return; order.splice(order.indexOf(dragId), 1); order.splice(order.indexOf(id), 0, dragId); dragId = null; draw(); });
      return c;
    }) : [h('span.muted.small', 'Noch kein Paket ausgewählt')]));
    info.textContent = fmtT('{0} ausgewählt', order.length);
    if (onChange) onChange(order.length);
  }
  function draw() {
    const f = q.value.trim().toLowerCase();
    const rows = pkgs.filter((p) => !f || [p.name, p.version, p.winget_id, p.tenant_name, p.description].some((v) => String(v || '').toLowerCase().includes(f)));
    list.replaceChildren(...rows.map((p) => h('label', { class: order.includes(p.id) ? 'on' : '' },
      h('input', { type: 'checkbox', checked: order.includes(p.id), onchange: (e) => { if (e.target.checked) { if (!order.includes(p.id)) order.push(p.id); } else order.splice(order.indexOf(p.id), 1); draw(); } }),
      h('b.raw', p.name), h('span.muted.small', [p.version, p.type === 'winget' ? 'winget' : '', p.tenant_name].filter(Boolean).join(' · ')))));
    if (!rows.length) list.replaceChildren(h('div.muted', { style: { padding: '10px' } }, 'Keine Treffer'));
    drawChips();
  }
  q.addEventListener('input', draw);
  draw();
  return {
    el: h('div.pick', h('div.pick-tools', q, btn('Keine', () => { order.length = 0; draw(); }, { cls: 'small' }), info), chips, list),
    selected: () => [...order],
  };
}

async function computerPicker(preselected = [], opt = {}) {
  const all = await GET('/api/computers');
  const selIds = new Set(preselected.map(Number));
  const q = h('input', { type: 'search', placeholder: 'Computer suchen (Name, IP, Benutzer, Modell) …' });
  const onlyOnline = h('input', { type: 'checkbox' });
  const info = h('span.muted.small');
  const list = h('div.pick-list');
  const chips = h('div.pick-chips');
  const shown = () => {
    const t = q.value.trim().toLowerCase();
    return all.filter((c) => (!onlyOnline.checked || c.online) &&
      (!t || [c.name, c.dns_name, c.ip_addresses, c.current_user, c.model, c.os].some((v) => String(v || '').toLowerCase().includes(t))));
  };
  function drawChips() {
    const chosen = all.filter((c) => selIds.has(c.id));
    chips.replaceChildren(...(chosen.length ? chosen.map((c) => h('span.chip', h('span.dot', { class: c.online ? 'on' : 'off' }), c.name,
      h('button', { type: 'button', title: 'Entfernen', onclick: () => { selIds.delete(c.id); draw(); } }, '×'))) : [h('span.muted.small', 'Noch kein Computer ausgewählt')]));
    info.textContent = `${selIds.size} ausgewählt`;
  }
  function draw() {
    const rows = shown().slice(0, 400);
    list.replaceChildren(...rows.map((c) => h('label', { class: selIds.has(c.id) ? 'on' : '' },
      h('input', { type: 'checkbox', checked: selIds.has(c.id), onchange: (e) => { if (e.target.checked) selIds.add(c.id); else selIds.delete(c.id); draw(); } }),
      h('span.dot', { class: c.online ? 'on' : 'off' }), h('b', c.name), h('span.muted.small', [c.os, c.current_user, (c.ip_addresses || '').split(',')[0]].filter(Boolean).join(' · ')))));
    if (!rows.length) list.replaceChildren(h('div.muted', { style: { padding: '10px' } }, 'Keine Treffer'));
    drawChips();
  }
  q.addEventListener('input', draw);
  onlyOnline.addEventListener('change', draw);
  const allBtn = btn('Alle Treffer', () => { shown().forEach((c) => selIds.add(c.id)); draw(); }, { cls: 'small' });
  const noneBtn = btn('Keine', () => { selIds.clear(); draw(); }, { cls: 'small' });
  draw();
  if (!opt.noFocus) setTimeout(() => q.focus(), 50);
  return {
    el: h('div.pick', h('div.pick-tools', q, h('label.chk', onlyOnline, 'nur online'), allBtn, noneBtn, info), chips, list),
    selected: () => [...selIds],
  };
}

// ---------------------------------------------------------------------------
// Ansicht: Bereitstellungen
// ---------------------------------------------------------------------------
// Ziel einer Bereitstellung: Sammlung/Zeitplan-Ziele (falls bekannt) und die ersten Computernamen
function depTargetNames(r) {
  const names = (r.target_names || '').split(', ').filter(Boolean);
  const more = (r.total || 0) - names.length;
  return names.join(', ') + (more > 0 ? ` +${more}` : '');
}
function depTargetText(r) { return [r.target_label, depTargetNames(r)].filter(Boolean).join(' · '); }

async function viewDeployments() {
  setTop('Bereitstellungen', 'Verlauf und laufende Softwareverteilungen', btn('Neue Bereitstellung', () => deployDlg({}), { icon: 'send', cls: 'primary' }));
  const load = () => GET('/api/deployments');
  const t = table([
    { key: 'id', label: '#', cls: 'right' },
    { key: 'name', label: 'Name' },
    { key: 'target', label: 'Ziel', cls: 'trunc', value: depTargetText, title: depTargetText,
      render: (r) => h('span', r.target_label ? h('span.badge', { style: { marginRight: '6px' } }, r.target_label) : null, depTargetNames(r)) },
    { key: 'source', label: 'Quelle', value: (r) => SOURCE[r.source] || r.source },
    { key: 'status', label: 'Status', render: (r) => depBadge(r.status) },
    { key: 'prog', label: 'Fortschritt', nosort: true, noFilter: true, render: (r) => h('div.row', progress(r.ok, r.failed, r.total), h('span.small.muted', `${r.ok}/${r.total}`)) },
    { key: 'created_by', label: 'Von' },
    { key: 'created_at', label: 'Erstellt', render: (r) => fmtDate(r.created_at) },
    { key: 'scheduled_at', label: 'Geplant', render: (r) => fmtDate(r.scheduled_at) },
    { key: 'finished_at', label: 'Beendet', render: (r) => fmtDate(r.finished_at) },
  ], await load(), { sort: 'id', dir: -1, noun: 'Bereitstellungen', onRow: (r) => go(`#/deployment/${r.id}`) });
  setContent(h('div', { style: { height: '100%', display: 'flex' } }, h('div.pane.card', t.el)));
  every(5000, async () => { if (parseHash().parts[0] === 'deployments') t.setRows(await load()); });
}

// Bereitstellung mit frei gewählten Schritten wiederholen
const STEP_COUNT_LABEL = { success: ['OK', 'ok'], failed: ['Fehler', 'err'], skipped: ['übersprungen', ''], notrun: ['nicht ausgeführt', ''] };
async function rerunDlg(did, d) {
  const ov = await GET(`/api/deployments/${did}/steps`);
  const PHN = { pre: 'Vorbereitung', main: 'Schritte', post: 'Abschluss' };
  const checks = {};
  const anyFailed = ov.steps.some((x) => x.counts.failed);
  const rows = [];
  for (const ph of ['pre', 'main', 'post']) {
    const list = ov.steps.filter((x) => x.phase === ph);
    if (!list.length) continue;
    rows.push(h('div.muted.small', { style: { margin: '10px 0 4px', textTransform: 'uppercase', letterSpacing: '.05em' } }, tr(PHN[ph])));
    for (const st of list) {
      // Vorauswahl: fehlgeschlagene und danach nicht mehr ausgeführte Schritte (gibt es keine Fehler, alle eingeschalteten)
      const cb = checks[st.sid] = h('input', { type: 'checkbox', checked: anyFailed ? !!(st.counts.failed || st.counts.notrun) : st.enabled });
      const badges = Object.entries(st.counts).map(([k, n]) => h('span.badge', { class: (STEP_COUNT_LABEL[k] || ['', ''])[1], style: { marginLeft: '6px' } }, `${n} ${tr((STEP_COUNT_LABEL[k] || [k])[0])}`));
      rows.push(h('label.chk', { style: { display: 'flex', alignItems: 'center', padding: '3px 0' } }, cb, h('span.raw', { style: { marginLeft: '6px' } }, st.title),
        st.enabled ? null : h('span.muted.small', { style: { marginLeft: '6px' } }, tr('(im Paket ausgeschaltet)')), ...badges));
    }
  }
  const allBtns = h('div.row', { style: { gap: '6px', marginBottom: '4px' } },
    btn('Alle', () => { Object.values(checks).forEach((c) => (c.checked = true)); rbAuto(); }, { cls: 'small' }),
    btn('Keine', () => { Object.values(checks).forEach((c) => (c.checked = false)); rbAuto(); }, { cls: 'small' }),
    anyFailed ? btn('Nur fehlgeschlagene', () => { ov.steps.forEach((x) => (checks[x.sid].checked = !!x.counts.failed)); rbAuto(); }, { cls: 'small' }) : null);
  let rbAuto = () => {};
  const failedT = ov.targets.filter((t) => t.status !== 'success');
  const tsel = sel([['failed', fmtT('Nicht erfolgreiche Ziele ({0})', failedT.length)], ['all', fmtT('Alle Ziele ({0})', ov.targets.length)],
    ['selected', 'Ziele auswählen …']], failedT.length ? 'failed' : 'all');
  const tchecks = {};
  const tbox = h('div.list-pick', { style: { maxHeight: '180px', display: 'none', marginTop: '6px' } },
    ...ov.targets.map((t) => h('label.chk', { style: { display: 'flex', padding: '2px 0' } }, tchecks[t.tid] = h('input', { type: 'checkbox', checked: t.status !== 'success' }),
      h('span.raw', { style: { marginLeft: '6px' } }, t.computer), h('span', { style: { marginLeft: '6px' } }, depBadge(t.status)))));
  tsel.addEventListener('change', () => { tbox.style.display = tsel.value === 'selected' ? '' : 'none'; });
  // Neustart: Standard „nie“, außer es ist ein Installations- oder Neustart-Schritt gewählt (dann wie im Paket)
  const pkgReboot = (d.package && d.package.reboot) || 'never';
  const rb = sel([['package', fmtT('wie im Paket ({0})', tr({ never: 'nie', if_required: 'bei Bedarf', always: 'immer' }[pkgReboot] || 'nie'))], ['never', 'nie']], 'never');
  rbAuto = () => { const any = ov.steps.some((x) => checks[x.sid].checked && ['install', 'reboot', 'nested'].includes(x.type)); rb.value = any ? 'package' : 'never'; };
  Object.values(checks).forEach((c) => c.addEventListener('change', rbAuto)); rbAuto();
  modal('Schritte wählen und wiederholen', h('div',
    h('p.muted.small', { style: { marginTop: 0 } }, 'Es entsteht eine neue Bereitstellung nur mit den gewählten Schritten – in der Reihenfolge des Pakets, mit dessen Bedingungen und „Ausführen als“. Abschluss-Schritte laufen nur, wenn sie gewählt sind.'),
    allBtns, h('div', rows), h('div.form', { style: { marginTop: '12px' } }, field('Ziele', h('div', tsel, tbox)),
      pkgReboot !== 'never' ? field('Neustart am Ende', rb, 'Werden nur einzelne Schritte wiederholt (z. B. Registry), ist meist kein Neustart nötig.') : null)), [
    { label: 'Abbrechen' },
    { label: 'Wiederholen', cls: 'primary', icon: 'refresh', onClick: async () => {
      const step_ids = ov.steps.filter((x) => checks[x.sid].checked).map((x) => x.sid);
      if (!step_ids.length) { toast(tr('Bitte mindestens einen Schritt wählen.'), 'err'); return false; }
      const target_ids = tsel.value === 'selected' ? Object.entries(tchecks).filter(([, c]) => c.checked).map(([k]) => Number(k)) : [];
      if (tsel.value === 'selected' && !target_ids.length) { toast(tr('Bitte mindestens ein Ziel wählen.'), 'err'); return false; }
      const r = await run(() => POST(`/api/deployments/${did}/rerun`, { step_ids, target: tsel.value, target_ids, reboot: rb.value }), 'Wiederholung gestartet');
      if (!r) return false;
      go(`#/deployment/${r.id}`);
    } }], { wide: true });
}

async function viewDeployment(parts) {
  const id = Number(parts[0]);
  const d = await GET(`/api/deployments/${id}`);
  const p = d.package || {};
  const active = ['running', 'queued', 'scheduled'].includes(d.status);
  setTop(`Bereitstellung #${d.id}: ${d.name}`, `${SOURCE[d.source] || d.source} · ${d.created_by || ''} · ${fmtDate(d.created_at)}`,
    active ? btn('Abbrechen', async () => { if (await confirmDlg('Bereitstellung abbrechen? Bereits laufende Installationen werden beendet.', 'Abbrechen', true)) { await run(() => POST(`/api/deployments/${id}/cancel`), 'Abgebrochen'); route(); } }, { cls: 'danger' }) : null,
    !active ? btn('Fehlgeschlagene erneut', async () => {
      if (!(await confirmDlg('Die fehlgeschlagenen Ziele erneut bereitstellen?', 'Erneut bereitstellen'))) return;
      const r = await run(() => POST(`/api/deployments/${id}/redeploy`, { failed_only: true }), 'Gestartet'); if (r) go(`#/deployment/${r.id}`);
    }) : null,
    !active && p.steps && Object.values(p.steps).some((v) => v && v.length) ? btn('Schritte wählen …', () => rerunDlg(id, d), { icon: 'refresh', title: 'Ausgewählte Schritte auf allen, den fehlgeschlagenen oder ausgewählten Zielen wiederholen' }) : null,
    !active ? btn('Alle erneut', async () => {
      if (!(await confirmDlg(`Bereitstellung für alle ${d.targets.length === 1 ? 'Ziele (1)' : `${d.targets.length} Ziele`} erneut starten? Bereits erfolgreiche Computer werden noch einmal versorgt.`, 'Erneut bereitstellen'))) return;
      const r = await run(() => POST(`/api/deployments/${id}/redeploy`, { failed_only: false }), 'Gestartet'); if (r) go(`#/deployment/${r.id}`);
    }) : null,
    !active ? btn('Löschen', async () => { if (await confirmDlg('Bereitstellung aus dem Verlauf löschen?', 'Löschen', true)) { await run(() => DEL(`/api/deployments/${id}`), 'Gelöscht'); go('#/deployments'); } }, { cls: 'danger', icon: 'trash' }) : null);
  const ok = d.targets.filter((t) => t.status === 'success').length;
  const failed = d.targets.filter((t) => t.status === 'failed').length;
  const allSteps = p.steps ? ['pre', 'main', 'post'].flatMap((k) => (p.steps[k] || []).map((x) => x.title || x.type)) : [];
  const src = p.steps ? `${allSteps.length} Schritt(e)${p.run_as ? ' · ' + ({ deploy: 'Dienstkonto', system: 'Lokales System', user: 'Angemeldeter Benutzer' })[p.run_as] : ''}`
    : p.type === 'winget' ? `winget ${p.winget_action} ${p.winget_id}${p.winget_version ? ' ' + p.winget_version : ''}`
      : p.install_type === 'script' ? 'PowerShell-Skript' : `${p.folder ? `${p.folder}\\` : ''}${p.install_file || ''} ${p.arguments || ''}`;
  const info = h('div.card', h('div.card-b', h('div.grid.two',
    kv([['Status', depBadge(d.status)], ['Paket', d.package_id ? h('a', { href: '#', onclick: (e) => { e.preventDefault(); packageDlg(d.package_id); } }, `${p.name || ''} ${p.version || ''}`) : `${p.name || ''} ${p.version || ''}`],
      ['Ausführung', h('span.mono', src)], d.schedule_id ? ['Zeitplan', h('a', { href: '#', onclick: (e) => { e.preventDefault(); scheduleDlg(d.schedule_id); } }, 'öffnen')] : null, ['Neustart', { never: 'nie', if_required: 'bei Bedarf', always: 'immer' }[p.reboot] || 'nie']]),
    kv([d.target_label ? ['Ziel', d.target_label] : null, ['Ziele', `${d.targets.length} (${ok} erfolgreich, ${failed} fehlgeschlagen)`], ['Fortschritt', progress(ok, failed, d.targets.length)],
      ['Geplant', fmtDate(d.scheduled_at)], ['Gestartet', fmtDate(d.started_at)], ['Beendet', fmtDate(d.finished_at)]]))));
  const t = table([
    { key: 'computer_name', label: 'Computer', render: (r) => (r.computer_id ? h('a', { href: `#/computer/${r.computer_id}` }, r.computer_name) : r.computer_name) },
    { key: 'status', label: 'Status', render: (r) => depBadge(r.status) },
    { key: 'exit_code', label: 'Exitcode', cls: 'mono' },
    { key: 'message', label: 'Meldung', cls: 'wrap' },
    { key: 'started_at', label: 'Start', render: (r) => fmtDate(r.started_at) },
    { key: 'finished_at', label: 'Ende', render: (r) => fmtDate(r.finished_at) },
    { key: 'out', label: '', nosort: true, noFilter: true, render: (r) => (r.finished_at || r.output ? btn('Details', () => targetDetails(r, id, !active), { cls: 'small' }) : '') },
  ], d.targets, { sort: 'computer_name', noun: 'Ziele', onRow: (r) => targetDetails(r, id, !active) });
  // Fehlgeschlagene Schritte zusammengefasst – einzeln wiederholen
  let fsCard = null;
  if (!active && failed) {
    let fs = [];
    try { fs = await GET(`/api/deployments/${id}/failed_steps`); } catch (e) { fs = []; }
    if (fs.length) {
      const PHL = { pre: 'Vorbereitung', main: 'Schritte', post: 'Abschluss' };
      fsCard = h('div.card', h('div.card-h', h('h2', 'Fehlgeschlagene Schritte')), h('div.card-b',
        h('p.muted.small', { style: { marginTop: 0 } }, 'Einzelne Schritte erneut ausführen, ohne das ganze Paket zu wiederholen – „Nur diesen Schritt“ oder „Ab diesem Schritt“ (inkl. der danach nicht mehr ausgeführten Schritte und des Abschlusses). Für einen einzelnen Computer: Details öffnen.'),
        h('table.fs-table', h('tbody', fs.map((f) => h('tr',
          h('td.muted', PHL[f.phase] || f.phase), h('td', h('b', f.title)),
          h('td', h('span.badge.err', `${f.targets.length} Computer`), ' ', h('span.muted.small', { title: f.targets.map((x) => `${x.computer}: ${x.message}`).join('\n') },
            f.targets.slice(0, 4).map((x) => x.computer).join(', ') + (f.targets.length > 4 ? ' …' : ''))),
          h('td', { style: { textAlign: 'right', whiteSpace: 'nowrap' } },
            btn('Nur diesen Schritt', () => retryStep(id, f.sid, f.title, 'step', f.targets.map((x) => x.tid), f.targets.map((x) => x.computer)), { cls: 'small', icon: 'refresh' }), ' ',
            btn('Ab diesem Schritt', () => retryStep(id, f.sid, f.title, 'from', f.targets.map((x) => x.tid), f.targets.map((x) => x.computer)), { cls: 'small', icon: 'play' }))))))));
    }
  }
  setContent(h('div.grid', info, fsCard, h('div.card', { style: { display: 'flex', height: 'calc(100vh - 290px)', minHeight: '300px' } }, h('div.pane', t.el))));
  if (active) every(4000, async () => { const x = await GET(`/api/deployments/${id}`); t.setRows(x.targets); if (x.status !== d.status) route(); });
}

// ---------------------------------------------------------------------------
// Wochentage (Zeitfenster der Zeitpläne)
// ---------------------------------------------------------------------------
const DAYS = [[1, 'Mo'], [2, 'Di'], [3, 'Mi'], [4, 'Do'], [5, 'Fr'], [6, 'Sa'], [7, 'So']];

// ---------------------------------------------------------------------------
// Ansicht: Berichte
// ---------------------------------------------------------------------------
async function viewReports(parts, params) {
  const [defs, cols] = await Promise.all([GET('/api/reports'), collections()]);
  const std = defs.find((d) => !d.custom) || defs[0];
  const key = parts[0] || std.key;
  const isMail = key === 'mail';
  const def = defs.find((d) => d.key === key) || std;
  setTop('Berichte', isMail ? 'E-Mail-Versand' : def.title);
  const own = defs.filter((d) => d.custom);
  const item = (d) => h('div.tree-item', { class: !isMail && d.key === def.key ? 'active' : '', onclick: () => go(`#/reports/${d.key}`) }, icon(d.custom ? 'edit' : 'report'), h('span.lbl', { class: d.custom ? 'raw' : '' }, d.title));
  const list = h('div.tree', h('div.head', h('h3', 'Berichte')),
    h('div.tree-item', { class: isMail ? 'active' : '', onclick: () => go('#/reports/mail') }, icon('mail'), h('span.lbl', 'E-Mail-Versand')), h('div.tree-sep'),
    h('div.tree-cap', 'Eigene Berichte'), own.map(item),
    h('div.tree-item.muted', { onclick: () => customReportDlg(null, cols) }, icon('plus'), h('span.lbl', 'Neuer Bericht …')), h('div.tree-sep'),
    h('div.tree-cap', 'Vorgegeben'), defs.filter((d) => !d.custom).map(item));
  if (isMail) {
    const pane = await mailSubsPane(defs, cols);
    setContent(h('div', { style: { display: 'flex', flex: '1', minWidth: '0' } }, list, splitter(list, 'reports'), pane), true);
    return;
  }
  const inputs = {};
  const form = h('div.row');
  for (const p of def.params) {
    const v = params.get(p.name) ?? p.default;
    inputs[p.name] = p.type === 'collection' ? sel(collectionOptions(cols), v) : h('input', { type: p.type === 'number' ? 'number' : 'text', value: v, style: { width: p.type === 'number' ? '90px' : '200px' } });
    form.append(h('label.muted', p.label), inputs[p.name]);
  }
  const qs = () => new URLSearchParams(Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value]))).toString();
  form.append(btn('Ausführen', () => go(`#/reports/${def.key}?${qs()}`), { cls: 'primary', icon: 'play' }),
    btn('CSV', () => downloadUrl(`/api/reports/${def.key}/csv?${qs()}`), { icon: 'download' }),
    btn('Drucken', () => window.print(), { icon: 'print' }),
    btn('Per E-Mail …', () => mailSubDlg(null, defs, cols, { key: def.key, params: Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value])) }),
      { icon: 'mail', title: 'Diesen Bericht regelmäßig per E-Mail senden' }),
    def.custom ? h('span.row', { style: { gap: '6px', marginLeft: '6px', paddingLeft: '10px', borderLeft: '1px solid var(--line)' } },
      btn('Bearbeiten', async () => customReportDlg(await GET(`/api/customreports/${def.id}`), cols), { icon: 'edit' }),
      btn('Duplizieren', async () => { const r = await POST(`/api/customreports/${def.id}/duplicate`); toast('Kopie angelegt', 'ok'); go(`#/reports/${r.key}`); }),
      btn('Benachrichtigung …', () => customReportAlert(def), { icon: 'bell', title: 'Mail, sobald der Bericht Zeilen liefert' })) : '');  // nie null: DOM-append schreibt sonst „null“
  const res = await GET(`/api/reports/${def.key}?${qs()}`);
  const t = table(res.columns.map((c) => ({ key: c.key, label: c.label, cls: c.type === 'number' || c.type === 'bytes' ? 'right' : '',
    sort: c.type === 'bytes' || c.type === 'number' ? (r) => (r[c.key] === null || r[c.key] === '' ? -1 : Number(r[c.key])) : undefined,
    render: c.type === 'bytes' ? bytesCell(c.key) : c.type === 'date' || (!c.type && /(_at|^ts$|last_|installed_on|finished)/.test(c.key)) ? (r) => fmtDate(r[c.key]) : c.key === 'online' && !c.type ? (r) => (r.online ? 'ja' : 'nein') : undefined })),
  res.rows, { id: (r) => JSON.stringify(r), noun: 'Zeilen', pageSize: 5000 });
  const head = h('div.toolbar.no-print', h('div.muted', def.desc), h('div', { style: { height: '8px' } }), form);
  const printHead = h('div.print-only', h('h1', `POCKET KNIFE – ${res.title}`), h('p', `Erstellt: ${fmtDate(res.generated)} · ${plural(res.rows.length, 'Zeile', 'Zeilen')}`));
  setContent(h('div', { style: { display: 'flex', flex: '1', minWidth: '0' } }, list, splitter(list, 'reports'), h('div.pane', head, printHead, t.el)), true);
}

// ---------------------------------------------------------------------------
// E-Mail: Berichtsversand (Abonnements) – Teil der Berichte-Ansicht
// ---------------------------------------------------------------------------
const MAIL_TRIGGERS = ['once', 'interval', 'daily', 'weekly', 'monthly'];

function mailNotReady(d) {
  if (d.configured) return null;
  return h('div.card.guard', { style: { marginBottom: '10px' } }, h('div.card-b', h('div.row',
    h('span.badge.warn', 'E-Mail nicht eingerichtet'),
    h('span', 'Versand und Benachrichtigungen laufen erst, wenn unter Einstellungen → E-Mail ein SMTP-Server eingetragen und der Versand aktiviert ist.'),
    h('div.spacer'), btn('Einstellungen öffnen', () => go('#/settings?s=mail'), { cls: 'small', icon: 'gear' }))));
}

function mailStatus(s) {
  if (!s) return '';
  if (/^FEHLER/.test(s)) return h('span.badge.err', { title: s }, 'Fehler');
  if (/^Übersprungen/.test(s)) return h('span.badge', { title: s }, 'übersprungen');
  return h('span.muted.small', { title: s }, s);
}

async function mailSubsPane(defs, cols) {
  const d = await GET('/api/mail/reports');
  const t = table([
    { key: 'name', label: 'Name', render: (r) => h('span', h('b', r.name), r.enabled ? '' : h('span.badge', { style: { marginLeft: '6px' } }, 'inaktiv')) },
    { key: 'reports', label: 'Berichte', cls: 'wrap', value: (r) => r.report_titles.join(', ') },
    { key: 'triggers', label: 'Wann', cls: 'wrap', value: (r) => r.trigger_texts.join('; ') || 'nur manuell',
      render: (r) => (r.trigger_texts.length ? h('div', r.trigger_texts.map((x) => h('div', x))) : h('span.muted', 'nur manuell')) },
    { key: 'recipients', label: 'Empfänger', cls: 'wrap', value: (r) => r.recipients || (d.default_to ? `Standard (${d.default_to})` : '—') },
    { key: 'next', label: 'Nächster Versand', render: (r) => fmtDate(r.next) },
    { key: 'last_run', label: 'Zuletzt', render: (r) => fmtDate(r.last_run) },
    { key: 'last_status', label: 'Ergebnis', render: (r) => mailStatus(r.last_status) },
  ], d.subscriptions, { sort: 'name', noun: 'Versände', onRow: (r) => mailSubDlg(r, defs, cols), clickOpens: true,
    empty: 'Noch kein Berichtsversand. Mit „Neuer Versand“ einen oder mehrere Berichte zusammenstellen und einen Zeitplan festlegen.' });
  t.actions.append(btn('Neuer Versand', () => mailSubDlg(null, defs, cols), { cls: 'primary small', icon: 'plus' }));
  return h('div.pane', h('div.toolbar', h('div.muted', 'Berichte regelmäßig per E-Mail verschicken – als Tabelle im Mailtext und als CSV-Anhang. Mehrere Berichte lassen sich in einer Mail bündeln.')),
    mailNotReady(d), t.el);
}

function mailSubDlg(s, defs, cols, preset) {
  const isNew = !s || !s.id;
  s = s ? JSON.parse(JSON.stringify(s)) : { name: '', description: '', enabled: true, reports: [], recipients: '', triggers: [], options: { csv: true, skip_empty: false, row_limit: 200 } };
  if (preset) s.reports.push(preset);
  if (isNew && !s.triggers.length) s.triggers.push({ type: 'weekly', enabled: true, days: [0], every: 1, time: '07:00' });
  if (isNew && !s.name && s.reports.length) s.name = (defs.find((x) => x.key === s.reports[0].key) || {}).title || '';
  const name = h('input', { value: s.name, placeholder: 'z. B. Wochenbericht IT' });
  const desc = h('textarea', { rows: 2, value: s.description || '', placeholder: 'Optionaler Text am Anfang der Mail' });
  const en = h('input', { type: 'checkbox', checked: !!s.enabled });
  const rec = h('input', { value: s.recipients || '', placeholder: 'leer = Standard-Empfänger aus Einstellungen → E-Mail' });
  const csv = h('input', { type: 'checkbox', checked: s.options.csv !== false });
  const skip = h('input', { type: 'checkbox', checked: !!s.options.skip_empty });
  const limit = h('input', { type: 'number', min: 0, max: 2000, value: s.options.row_limit ?? 200, style: { maxWidth: '100px' } });
  // Berichte mit Parametern
  const repBox = h('div');
  const addSel = sel([['', '— Bericht hinzufügen —'], ...defs.map((x) => [x.key, x.title])], '');
  addSel.addEventListener('change', () => {
    const def = defs.find((x) => x.key === addSel.value);
    if (def) {
      s.reports.push({ key: def.key, params: Object.fromEntries(def.params.map((p) => [p.name, p.default ?? ''])) });
      if (!name.value) name.value = def.title;
      drawReps();
    }
    addSel.value = '';
  });
  function drawReps() {
    repBox.replaceChildren(...(s.reports.length ? s.reports.map((r, i) => {
      const def = defs.find((x) => x.key === r.key);
      if (!def) return null;
      r.params = r.params || {};
      const ps = def.params.map((p) => {
        const v = r.params[p.name] ?? p.default ?? '';
        const el = p.type === 'collection' ? sel(collectionOptions(cols), v, { style: { width: '200px' } }) : h('input', { type: p.type === 'number' ? 'number' : 'text', value: v, style: { width: p.type === 'number' ? '90px' : '200px' } });
        el.addEventListener('change', () => { r.params[p.name] = el.value; });
        return h('label.row', { style: { gap: '6px' } }, h('span.muted.small', p.label), el);
      });
      const move = (dir) => { const j = i + dir; if (j < 0 || j >= s.reports.length) return; [s.reports[i], s.reports[j]] = [s.reports[j], s.reports[i]]; drawReps(); };
      return h('div.card.mail-rep', h('div.card-b',
        h('div.mail-rep-h', icon('report'), h('b', def.title), h('span.muted.small', def.desc), h('div.spacer'),
          btn('↑', () => move(-1), { cls: 'small ghost', title: 'Nach oben', disabled: i === 0 }), btn('↓', () => move(1), { cls: 'small ghost', title: 'Nach unten', disabled: i === s.reports.length - 1 }),
          btn('', () => { s.reports.splice(i, 1); drawReps(); }, { icon: 'trash', cls: 'small ghost', title: 'Bericht entfernen' })),
        ps.length ? h('div.mail-rep-p', ...ps) : null));
    }).filter(Boolean) : [h('p.muted', 'Noch kein Bericht gewählt.')]), h('div.row', { style: { marginTop: '6px' } }, addSel));
  }
  drawReps();
  const trig = triggerEditor(s.triggers, { noCatchUp: true, only: MAIL_TRIGGERS, empty: 'Keine Auslöser – der Bericht wird nur mit „Jetzt senden“ verschickt.' });
  const body = () => ({ name: name.value, description: desc.value, enabled: en.checked, recipients: rec.value, reports: s.reports,
    options: { csv: csv.checked, skip_empty: skip.checked, row_limit: Number(limit.value || 0) },
    triggers: s.triggers.map((t) => ({ ...t, at: t.at && t.at.replace('T', ' '), start: t.start && t.start.replace('T', ' '), end: t.end ? t.end.replace('T', ' ') : null })) });
  const tabs = [
    ['Berichte', h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Alle gewählten Berichte kommen in eine Mail – in dieser Reihenfolge.'), repBox)],
    ['Zeitplan', h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Wann die Mail verschickt wird. Zeitangaben beziehen sich auf die Serverzeit des Containers (TZ in der .env).'), trig)],
    ['Empfänger & Inhalt', h('div.form', field('Name', name, 'Erscheint als Betreff der Mail.'), field('Einleitung', desc), field('Empfänger', rec, 'Mehrere Adressen mit Komma oder Semikolon trennen.'),
      field('Mailtext', h('div.row', { style: { gap: '6px' } }, 'bis zu', limit, 'Zeilen je Bericht als Tabelle'), '0 = nur Anhang. Größere Berichte stehen vollständig im CSV-Anhang.'),
      field('Anhang', h('label.chk', csv, 'jeden Bericht als CSV-Datei anhängen (Excel-kompatibel)')),
      field('Leere Berichte', h('label.chk', skip, 'nicht senden, wenn alle Berichte leer sind'), 'Praktisch z. B. für „Wenig Speicherplatz“: Mail nur, wenn es etwas zu tun gibt.'),
      field('Aktiv', h('label.chk', en, 'automatisch nach Zeitplan senden')))],
  ];
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Berichtsversand „${s.name}“ löschen?`, 'Löschen', true))) return false; await DEL(`/api/mail/reports/${s.id}`); toast('Gelöscht', 'ok'); route(); } });
  }
  buttons.unshift({ label: 'Vorschau', icon: 'file', onClick: async () => { await mailPreview(body()); return false; } });
  if (!isNew) {
    buttons.unshift({ label: 'Jetzt senden …', icon: 'send', onClick: async () => { await mailSendNow(s); return false; } });
  }
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    if (isNew) await POST('/api/mail/reports', body()); else await PUT(`/api/mail/reports/${s.id}`, body());
    toast('Berichtsversand gespeichert', 'ok'); route();
  } });
  modal(isNew ? 'Neuer Berichtsversand' : `Berichtsversand: ${s.name}`, tabsBox(tabs), buttons, { wide: true, sticky: true, help: 'mail' });
}

async function mailPreview(body) {
  const r = await POST('/api/mail/reports/preview', body);
  const frame = h('iframe.mail-frame', { sandbox: '', src: r.url, title: 'Vorschau der E-Mail' });
  modal(`Vorschau: ${r.subject}`, h('div', h('p.muted.small', { style: { marginTop: 0 } }, `${plural(r.rows, 'Zeile', 'Zeilen')} insgesamt`,
    r.attachments.length ? ` · Anhänge: ${r.attachments.map((a) => `${a.name} (${fmtBytes(a.size)})`).join(', ')}` : ' · ohne Anhang'), frame),
  [{ label: 'Schließen' }], { xwide: true });
}

async function mailSendNow(s) {
  const to = h('input', { placeholder: s.recipients || 'Standard-Empfänger', style: { width: '100%' } });
  modal(`„${s.name}“ jetzt senden`, h('div.form', field('An', to, 'Leer = gespeicherte Empfänger. Eine Adresse eintragen, um z. B. nur an sich selbst zu testen. Gesendet wird der gespeicherte Stand.')), [
    { label: 'Abbrechen' },
    { label: 'Senden', cls: 'primary', icon: 'send', onClick: async () => {
      const r = await POST(`/api/mail/reports/${s.id}/send`, { to: to.value });
      toast(r.skipped ? 'Nicht gesendet – alle Berichte leer' : `Gesendet an ${(r.to || []).join(', ')} – Server: ${r.reply || ''}`, 'ok');
      route();
    } }]);
}

// ---------------------------------------------------------------------------
// Einstellungen → E-Mail: SMTP-Server und Benachrichtigungen
// ---------------------------------------------------------------------------
function proxyCard() {
  const box = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Proxyserver')), box);
  GET('/api/system/proxy').then((m) => {
    const url = h('input', { value: m.url, placeholder: 'leer = kein Proxy, z. B. http://proxy.firma.local:8080' });
    const usr = h('input', { value: m.user, placeholder: 'leer = ohne Anmeldung', autocomplete: 'off' });
    const pwPh = (set) => (set ? '•••••••• (gespeichert – leer lassen zum Behalten)' : 'Kennwort');
    const pw = h('input', { type: 'password', placeholder: pwPh(m.password_set), autocomplete: 'new-password' });
    const clients = h('input', { type: 'checkbox', checked: m.clients });
    const out = h('div.small');
    const body = () => ({ url: url.value, user: usr.value, password: pw.value, clients: clients.checked });
    const save = async (msg) => {
      const r = await run(() => PUT('/api/system/proxy', body()), msg);
      if (r) { pw.value = ''; pw.placeholder = pwPh(r.password_set); url.value = r.url; clients.checked = r.clients; }
      return r;
    };
    const testBtn = btn('Speichern & testen', async () => {
      if (!(await save())) return;
      const r = await run(() => POST('/api/system/proxy/test'));
      if (!r) return;
      out.replaceChildren(h('div', { style: { marginTop: '6px' } },
        h('span.badge.' + (r.ok ? 'ok' : 'err'), r.ok ? 'erreichbar' : 'fehlgeschlagen'), ' ',
        h('span.raw', r.url), h('span.muted', ` · ${r.via ? fmtT('über {0}', r.via) : tr('direkt (ohne Proxy)')} · ${r.ms} ms`),
        r.error ? h('div.err.raw', r.error) : null, r.hint ? h('div', r.hint) : null));
    }, { cls: 'small' });
    box.replaceChildren(h('p.muted.small', { style: { marginTop: 0 } }, 'Optional, für Netze ohne direkten Internetzugang. POCKET KNIFE lädt darüber den winget-Katalog; auf Wunsch laden auch die Clients ihre winget-Installationen darüber. Das Kennwort wird verschlüsselt gespeichert.'),
      h('div.form',
        field('Proxy-Adresse', url, 'Mit http:// – auch für HTTPS-Ziele. Leer = kein Proxy.'),
        field('Benutzer', usr, 'Nur für POCKET KNIFE selbst (Basic-Anmeldung am Proxy), z. B. FIRMA\\proxy-user.'),
        field('Kennwort', pw),
        field('Clients', h('label.chk', clients, 'auch winget auf den Clients über diesen Proxy laden'),
          'Setzt bei der nächsten winget-Bereitstellung die winget-Einstellung „DefaultProxy“ auf dem Client (und entfernt sie wieder, wenn Sie den Haken herausnehmen). Benutzer und Kennwort gehen nie an die Clients – verlangt der Proxy eine Anmeldung, melden sie sich mit ihrem Computerkonto an (Windows-Anmeldung am Proxy).'),
        field('Test', h('div', testBtn, out), 'Speichert die Eingaben und lädt die ersten Bytes des winget-Katalogs.')),
      h('div.row.end', btn('Speichern', () => save('Proxy-Einstellungen gespeichert'), { cls: 'primary' })));
  }).catch((e) => box.replaceChildren(h('p.muted', e.message)));
  return card;
}

function mailCard() {
  const box = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'SMTP-Server')), box);
  GET('/api/mail/settings').then((m) => {
    const enabled = h('input', { type: 'checkbox', checked: m.enabled });
    const host = h('input', { value: m.host, placeholder: 'z. B. mail.firma.local' });
    const sec = sel(Object.entries(m.securities), m.security);
    const port = h('input', { type: 'number', min: 1, max: 65535, value: m.port, style: { maxWidth: '110px' } });
    const verify = h('input', { type: 'checkbox', checked: m.verify });
    sec.addEventListener('change', () => { port.value = { starttls: 587, ssl: 465, none: 25 }[sec.value]; verifyRow.forEach((x) => x && (x.style.display = sec.value === 'none' ? 'none' : '')); });
    const usr = h('input', { value: m.user, placeholder: 'leer = ohne Anmeldung (Relay)', autocomplete: 'off' });
    const pw = h('input', { type: 'password', placeholder: m.password_set ? '•••••••• (gespeichert – leer lassen zum Behalten)' : 'Kennwort', autocomplete: 'new-password' });
    const from = h('input', { value: m.from, placeholder: 'z. B. pocketknife@firma.de' });
    const fromName = h('input', { value: m.from_name, placeholder: 'POCKET KNIFE' });
    const to = h('input', { value: m.to, placeholder: 'z. B. it@firma.de, admin@firma.de' });
    const prefix = h('input', { value: m.subject_prefix, style: { maxWidth: '220px' } });
    const testTo = h('input', { placeholder: 'Testempfänger (leer = Standard-Empfänger)', style: { flex: 1 } });
    const verifyRow = field('Zertifikat', h('label.chk', verify, 'Zertifikat des Servers prüfen'), 'Nur bei internen Servern mit selbst signiertem Zertifikat abschalten.');
    verifyRow.forEach((x) => x && (x.style.display = m.security === 'none' ? 'none' : ''));
    const body = () => ({ enabled: enabled.checked, host: host.value, port: port.value, security: sec.value, verify: verify.checked, user: usr.value,
      password: pw.value, from: from.value, from_name: fromName.value, to: to.value, subject_prefix: prefix.value });
    const saveBtn = btn('Speichern', async () => {
      const r = await run(() => PUT('/api/mail/settings', body()), 'E-Mail-Einstellungen gespeichert');
      if (r) { pw.value = ''; pw.placeholder = r.password_set ? '•••••••• (gespeichert – leer lassen zum Behalten)' : 'Kennwort'; }
    }, { cls: 'primary' });
    const testBtn = btn('Testmail senden', async () => {
      // Erst speichern, damit genau diese Werte getestet werden
      const saved = await run(() => PUT('/api/mail/settings', { ...body(), enabled: enabled.checked }));
      if (!saved) return;
      pw.value = '';
      const r = await run(() => POST('/api/mail/test', { to: testTo.value }));
      if (!r) return;
      lastLine.textContent = `Zuletzt: ${new Date().toLocaleString('de-DE')} – Testmail übergeben`;
      mailTestResult(r);
    }, { icon: 'send' });
    const lastLine = h('div.muted.small', m.last ? `Zuletzt gesendet: ${m.last}` : 'Noch keine Mail gesendet.');
    box.replaceChildren(h('p.muted.small', { style: { marginTop: 0 } }, 'Für den Berichtsversand (Berichte → E-Mail-Versand) und die Benachrichtigungen. Das Kennwort wird verschlüsselt gespeichert.'),
      h('div.form', field('Versand', h('label.chk', enabled, 'E-Mails versenden')), field('SMTP-Server', host), field('Verschlüsselung', h('div.row', sec, h('span.muted', 'Port'), port)), verifyRow,
        field('Benutzer', usr), field('Kennwort', pw), field('Absender', from), field('Absendername', fromName),
        field('Standard-Empfänger', to, 'Gilt für Berichte und Benachrichtigungen ohne eigene Empfänger. Mehrere mit Komma trennen.'),
        field('Betreff-Präfix', prefix), field('Test', h('div.row', testTo, testBtn), 'Speichert die Eingaben und schickt eine Testnachricht.')),
      lastLine, h('div.row.end', saveBtn));
  }).catch((e) => box.replaceChildren(h('p.muted', e.message)));
  return card;
}

// Ergebnis der Testmail: was der Server geantwortet hat – und woran es liegen kann, wenn trotzdem nichts ankommt
function mailTestResult(r) {
  const pre = h('pre.out', { style: { whiteSpace: 'pre-wrap', maxHeight: '260px', overflow: 'auto', margin: '6px 0 12px' } }, (r.transcript || []).join('\n'));
  const tips = [
    ['Spam/Quarantäne', 'Junk-Ordner des Empfängers und die Quarantäne des Mailservers bzw. Spamfilters prüfen (Exchange Online: Microsoft Defender → Quarantäne).'],
    ['Nachverfolgung', `Mit der Antwort oben (Queue-ID) oder der Message-ID ${r.message_id} im Protokoll des Mailservers suchen – Exchange: Nachrichtenablaufverfolgung (Get-MessageTrackingLog / Message trace), Postfix: /var/log/mail.log.`],
    ['Absender', `Gibt es ${r.from} wirklich und darf das Konto/der Connector mit dieser Adresse senden? Unzustellbarkeitsberichte gehen an diese Adresse – existiert sie nicht, sieht sie niemand.`],
    ['Externe Empfänger', 'Bei Adressen außerhalb der eigenen Domäne: Ist Relay nach außen für diesen Server/Connector erlaubt? Passen SPF/DKIM/DMARC der Absenderdomäne zu diesem Mailserver?'],
    ['Interner Relay', `Anonyme Relays (ohne Anmeldung) nehmen oft alles an und verwerfen später. POCKET KNIFE meldet sich als „${r.helo}“ – ggf. diesen Namen bzw. die Container-/Host-IP am Connector freigeben.`],
  ];
  modal('Testmail vom Server angenommen', h('div',
    h('p', { style: { marginTop: 0 } }, h('span.badge.ok', 'angenommen'), ' ', `Der Server hat die Mail für ${r.to.join(', ')} angenommen: `, h('b.mono', r.reply || '')),
    r.refused && r.refused.length ? h('p', h('span.badge.err', 'abgelehnt'), ' ', r.refused.join(', ')) : null,
    (r.warnings || []).length ? h('div.card.guard', { style: { margin: '6px 0 10px' } }, h('div.card-b',
      h('b', 'Hinweise zu den Einstellungen'), h('ul', { style: { margin: '6px 0 0', paddingLeft: '18px' } }, r.warnings.map((w) => h('li', w))))) : null,
    h('p.muted.small', 'Ab hier liegt die Zustellung beim Mailserver. Kommt nichts an, helfen meist diese Punkte:'),
    h('dl.kv', tips.flatMap(([k, v]) => [h('dt', k), h('dd', v)])),
    h('h3', { style: { marginBottom: '0' } }, 'SMTP-Dialog'), pre), [{ label: 'Schließen' }], { wide: true, help: 'smtp' });
}

function alertsCard() {
  const box = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const checkBtn = btn('Jetzt prüfen', async () => {
    const r = await run(() => POST('/api/mail/alerts/check'));
    if (r) toast(r.errors.length ? r.errors[0] : (r.sent ? `${plural(r.sent, 'Benachrichtigung', 'Benachrichtigungen')} gesendet` : 'Nichts Neues zu melden'), r.errors.length ? 'err' : 'ok');
    load();
  }, { cls: 'small', icon: 'refresh' });
  const card = h('div.card', h('div.card-h', h('h2', 'Benachrichtigungen'), checkBtn), box);
  async function load() {
    const [d, cols] = await Promise.all([GET('/api/mail/alerts'), collections()]);
    box.replaceChildren(...[h('p.muted.small', { style: { marginTop: 0 } }, 'Mails bei bestimmten Ereignissen – jede Art einzeln einschaltbar. Jeder Fall wird nur einmal gemeldet (geprüft wird jede Minute); auf Wunsch mit Erinnerung und Entwarnung.'),
      d.configured ? null : h('p', h('span.badge.warn', 'E-Mail-Versand ist noch nicht aktiv'), ' – oben SMTP-Server eintragen und „E-Mails versenden“ einschalten.'),
      h('div.alert-list', d.alerts.map((a) => h('div.alert-item', { class: a.enabled ? 'on' : '' },
        h('div.row', { style: { gap: '10px', alignItems: 'center' } },
          h('span.dot', { class: a.enabled ? 'on' : 'off' }), h('div', { style: { flex: 1, minWidth: 0 } }, h('b', a.title), h('div.muted.small', a.desc)),
          a.enabled && a.active ? h('span.badge.warn', { title: 'Aktuell gemeldete, noch offene Fälle' }, `${a.active} offen`) : null,
          a.last_status ? h('span.muted.small', { title: a.last_status, style: { maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, /^FEHLER/.test(a.last_status) ? '⚠ ' + a.last_status : `Zuletzt ${fmtDate(a.last_sent)}`) : null,
          btn(a.enabled ? 'Bearbeiten' : 'Einrichten …', () => alertDlg(a, cols, load), { cls: 'small' })))))].filter(Boolean));
  }
  load().catch((e) => box.replaceChildren(h('p.muted', e.message)));
  return card;
}

function alertDlg(a, cols, onSaved) {
  const en = h('input', { type: 'checkbox', checked: a.enabled || !a.since });
  const rec = h('input', { value: a.recipients || '', placeholder: 'leer = Standard-Empfänger' });
  const rep = h('input', { type: 'number', min: 0, max: 8760, value: a.repeat_hours || 0, style: { maxWidth: '100px' } });
  const res = h('input', { type: 'checkbox', checked: a.resolved !== false });
  const inputs = {};
  const pRows = (a.param_defs || []).map((p) => {
    const v = a.params[p.name] ?? p.default ?? '';
    const el = p.type === 'collection' ? sel(collectionOptions(cols), v) : p.type === 'bool' ? h('input', { type: 'checkbox', checked: String(v) === '1' })
      : h('input', { type: p.type === 'number' ? 'number' : 'text', value: v, style: { maxWidth: p.type === 'number' ? '110px' : '220px' } });
    inputs[p.name] = el;
    return field(p.label, p.type === 'bool' ? h('label.chk', el, 'ja') : el, p.hint);
  });
  const prev = h('div');
  const collect = () => { const p = {}; for (const [k, el] of Object.entries(inputs)) p[k] = el.type === 'checkbox' ? (el.checked ? '1' : '0') : el.value; return p; };
  const prevBtn = btn('Was würde jetzt gemeldet?', async () => {
    const r = await run(() => POST(`/api/mail/alerts/${a.key}/preview`, { params: collect() }));
    if (!r) return;
    const t = table(r.columns || [], r.rows || [], { fill: false, search: false, columns: false, key: null, empty: 'Aktuell nichts zu melden.' });
    t.el.querySelector('.tbl-scroll').style.maxHeight = '220px';
    prev.replaceChildren(h('p.muted.small', r.current === 1 ? 'Aktuell trifft 1 Fall zu.' : `Aktuell treffen ${r.current} Fälle zu.`), t.el);
  }, { cls: 'small', icon: 'file' });
  const resolvable = a.resolvable;
  const form = h('div.form', h('p.muted.full', { style: { margin: 0 } }, a.desc), field('Aktiv', h('label.chk', en, 'Benachrichtigung senden')), ...pRows,
    field('Empfänger', rec, 'Mehrere Adressen mit Komma trennen.'),
    resolvable ? field('Erinnerung', h('div.row', { style: { gap: '6px' } }, 'nach', rep, 'Stunden erneut melden, solange offen'), '0 = nur einmal melden.') : null,
    resolvable ? field('Entwarnung', h('label.chk', res, 'melden, wenn der Zustand behoben ist')) : null,
    field('Vorschau', h('div', prevBtn), 'Prüft mit den Eingaben oben, ohne zu speichern oder zu senden.'), h('div.full', prev));
  modal(`Benachrichtigung: ${a.title}`, form, [{ label: 'Abbrechen' }, { label: 'Speichern', cls: 'primary', onClick: async () => {
    await PUT(`/api/mail/alerts/${a.key}`, { enabled: en.checked, recipients: rec.value, repeat_hours: rep.value, resolved: res.checked, params: collect() });
    toast(en.checked ? `„${a.title}“ aktiv` : `„${a.title}“ ausgeschaltet`, 'ok');
    onSaved();
  } }], { wide: true, help: 'alerts' });
}

// ---------------------------------------------------------------------------
// Eigene Berichte: Datenquelle, Spalten, Filter, Zusammenfassen – Vorschau und Speichern
// ---------------------------------------------------------------------------
const CR_TEMPLATES = [
  ['Leer (Computerliste)', () => ({ source: 'computer', columns: [{ t: 'computer', c: 'name' }] })],
  ['Hardware-Übersicht', () => ({ source: 'computer', columns: ['name', 'manufacturer', 'model', 'serial', 'cpu', 'ram_gb', 'os', 'os_display_version'].map((c) => ({ t: 'computer', c })) })],
  ['Software je Computer', () => ({ source: 'software', columns: [{ t: 'computer', c: 'name' }, { t: 'software', c: 'name' }, { t: 'software', c: 'version' }, { t: 'software', c: 'publisher' }],
    rows: { type: 'group', match: 'all', items: [{ type: 'cond', table: 'software', column: 'name', op: 'contains', value: '' }] } })],
  ['Software zählen (Name + Version)', () => ({ source: 'software', columns: [{ t: 'software', c: 'name' }, { t: 'software', c: 'version' }], group: true, sort: { i: 'count', dir: 'desc' } })],
  ['Laufwerke unter 15 % frei', () => ({ source: 'disks', columns: [{ t: 'computer', c: 'name' }, { t: 'disks', c: 'drive' }, { t: 'disks', c: 'size_gb' }, { t: 'disks', c: 'free_gb' }, { t: 'disks', c: 'free_pct' }],
    rows: { type: 'group', match: 'all', items: [{ type: 'cond', table: 'disks', column: 'free_pct', op: 'lt', value: '15' }] }, sort: { i: 4, dir: 'asc' } })],
  ['Windows 10 noch im Einsatz', () => ({ source: 'computer', columns: ['name', 'os', 'os_display_version', 'model', 'current_user', 'last_boot'].map((c) => ({ t: 'computer', c })),
    filter: { type: 'group', match: 'all', items: [{ type: 'cond', table: 'computer', column: 'os', op: 'contains', value: 'Windows 10' }] } })],
  ['Lange nicht neu gestartet', () => ({ source: 'computer', columns: ['name', 'days_since_boot', 'last_boot', 'current_user', 'pending_reboot'].map((c) => ({ t: 'computer', c })),
    filter: { type: 'group', match: 'all', items: [{ type: 'cond', table: 'computer', column: 'days_since_boot', op: 'gt', value: '30' }] }, sort: { i: 1, dir: 'desc' } })],
  ['Defender-Echtzeitschutz aus', () => ({ source: 'computer', columns: ['name', 'defender_enabled', 'defender_realtime', 'signature_age', 'last_scan'].map((c) => ({ t: 'computer', c })),
    filter: { type: 'group', match: 'all', items: [{ type: 'cond', table: 'computer', column: 'defender_realtime', op: 'is_false' }] } })],
];

function crNormalize(d) {
  const g = () => ({ type: 'group', match: 'all', items: [] });
  return { source: d.source || 'computer', columns: d.columns || [], filter: d.filter || g(), rows: d.rows || g(), collection: d.collection || '',
    group: !!d.group, sort: d.sort || { i: 0, dir: 'asc' }, limit: d.limit || 5000 };
}

async function customReportDlg(r, cols) {
  const isNew = !r;
  const schema = await collSchema(true);
  r = r ? JSON.parse(JSON.stringify(r)) : { name: '', description: '', definition: CR_TEMPLATES[1][1]() };
  let d = crNormalize(r.definition);
  const sources = schema.tables.filter((t) => !t.alias);
  const tableOf = (k) => sources.find((t) => t.key === k);
  const compCols = () => [...(tableOf('computer') || { columns: [] }).columns, ...schema.tables.filter((t) => t.alias).flatMap((t) => t.columns)].filter((c) => c.type !== 'collection');
  const srcCols = () => (d.source === 'computer' ? [] : (tableOf(d.source) || { columns: [] }).columns);
  const colLabel = (c) => ((c.t === 'computer' ? compCols() : srcCols()).find((x) => x.key === c.c) || { label: c.c }).label;

  const name = h('input', { value: r.name, placeholder: 'z. B. Rechner ohne BitLocker' });
  const desc = h('textarea', { rows: 2, value: r.description || '', placeholder: 'Wofür ist der Bericht? (erscheint in der Berichteliste)' });
  const srcSel = sel(sources.map((t) => [t.key, t.key === 'computer' ? 'Computer – eine Zeile je Computer' : `${t.label} – eine Zeile je Eintrag`]), d.source);
  const tplSel = sel([['', '— Vorlage übernehmen —'], ...CR_TEMPLATES.map((t, i) => [String(i), t[0]])], '');
  const collSel = sel(collectionOptions(cols), d.collection);
  const group = h('input', { type: 'checkbox', checked: d.group });
  const limit = h('input', { type: 'number', min: 1, max: 100000, value: d.limit, style: { maxWidth: '110px' } });
  const sortSel = h('select'); const dirSel = sel([['asc', 'aufsteigend'], ['desc', 'absteigend']], d.sort.dir);
  const colBox = h('div'); const rowBox = h('div'); const compBox = h('div'); const prevBox = h('div', h('p.muted', 'Mit „Vorschau“ den Bericht mit den aktuellen Einstellungen ausführen.'));

  function drawSort() {
    const opts = d.columns.map((c, i) => [String(i), c.label || colLabel(c)]);
    if (group.checked) opts.push(['count', d.source === 'computer' ? 'Anzahl' : 'Anzahl Computer']);
    sortSel.replaceChildren(...opts.map(([v, l]) => h('option', { value: v, selected: String(d.sort.i) === v }, l)));
    if (!opts.some(([v]) => v === String(d.sort.i))) { d.sort.i = 0; sortSel.value = '0'; }
  }
  sortSel.addEventListener('change', () => { d.sort.i = sortSel.value === 'count' ? 'count' : Number(sortSel.value); });
  dirSel.addEventListener('change', () => { d.sort.dir = dirSel.value; });
  group.addEventListener('change', drawSort);

  function drawCols() {
    const addSel = h('select', h('option', { value: '' }, '— Spalte hinzufügen —'),
      h('optgroup', { label: 'Computer' }, compCols().map((c) => h('option', { value: `computer|${c.key}` }, c.label))),
      d.source !== 'computer' ? h('optgroup', { label: (tableOf(d.source) || {}).label || d.source }, srcCols().map((c) => h('option', { value: `${d.source}|${c.key}` }, c.label))) : null);
    addSel.addEventListener('change', () => {
      if (!addSel.value) return;
      const [t, c] = addSel.value.split('|');
      d.columns.push({ t, c });
      drawCols();
    });
    const free = (tableOf(d.source) || {}).free_columns ? h('span.row', { style: { gap: '6px' } }, h('span.muted.small', 'oder Spalte des Scanners:'),
      (() => { const inp = h('input', { placeholder: 'Spaltenname', style: { width: '160px' } }); return [inp, btn('Hinzufügen', () => { if (inp.value.trim()) { d.columns.push({ t: d.source, c: inp.value.trim() }); drawCols(); } }, { cls: 'small' })]; })()) : null;
    colBox.replaceChildren(
      h('p.muted.small', { style: { marginTop: 0 } }, 'Spalten in dieser Reihenfolge. Die Bezeichnung lässt sich überschreiben (leer = Standard).'),
      ...(d.columns.length ? d.columns.map((c, i) => h('div.cr-col',
        h('span.badge', { class: c.t === 'computer' ? '' : 'info' }, c.t === 'computer' ? 'Computer' : ((tableOf(d.source) || {}).label || c.t)),
        h('b', colLabel(c)),
        h('input', { value: c.label || '', placeholder: 'Bezeichnung (optional)', oninput: (e) => { c.label = e.target.value; drawSort(); } }),
        h('div.spacer'),
        btn('↑', () => { if (i) { [d.columns[i - 1], d.columns[i]] = [d.columns[i], d.columns[i - 1]]; drawCols(); } }, { cls: 'small ghost', disabled: !i, title: 'Nach oben' }),
        btn('↓', () => { if (i < d.columns.length - 1) { [d.columns[i + 1], d.columns[i]] = [d.columns[i], d.columns[i + 1]]; drawCols(); } }, { cls: 'small ghost', disabled: i === d.columns.length - 1, title: 'Nach unten' }),
        btn('', () => { d.columns.splice(i, 1); drawCols(); }, { icon: 'x', cls: 'small ghost', title: 'Spalte entfernen' }))) : [h('p.muted', 'Noch keine Spalte.')]),
      h('div.row', { style: { gap: '10px', marginTop: '8px', flexWrap: 'wrap' } }, addSel, free));
    drawSort();
  }

  function drawFilters() {
    const src = tableOf(d.source);
    rowBox.replaceChildren();
    if (d.source !== 'computer' && src) {
      const first = src.columns[0] ? src.columns[0].key : '';
      const fe = filterEditor(d.rows, { ...schema, tables: [src] }, cols, null, null,
        () => ({ type: 'cond', table: d.source, column: first, op: '', value: '' }), `Noch keine Bedingung – alle ${src.label}.`);
      rowBox.append(h('h3', `Welche ${src.label}`), h('p.muted.small', { style: { marginTop: 0 } }, `Bedingungen an die einzelne Zeile – z. B. nur bestimmte ${src.label}. Ohne Bedingung: alle.`), fe.el);
    }
    const fc = filterEditor(d.filter, schema, cols, null, null, null, 'Noch keine Bedingung – der Bericht gilt für alle Computer.');
    compBox.replaceChildren(h('h3', 'Welche Computer'), h('p.muted.small', { style: { marginTop: 0 } }, 'Wie bei dynamischen Sammlungen – z. B. nur Computer mit Windows 11 oder mit einer bestimmten Software. Ohne Bedingung: alle Computer.'),
      h('div.row', { style: { gap: '8px', marginBottom: '8px' } }, h('span.muted', 'Nur Sammlung'), collSel), fc.el);
  }

  srcSel.addEventListener('change', () => {
    const prev = d.source;
    d.source = srcSel.value;
    d.columns = d.columns.filter((c) => c.t === 'computer');
    if (!d.columns.length) d.columns.push({ t: 'computer', c: 'name' });
    const st = tableOf(d.source);
    if (prev !== d.source && st && d.source !== 'computer' && st.columns[0]) d.columns.push({ t: d.source, c: st.columns[0].key });
    d.rows = { type: 'group', match: 'all', items: [] };
    drawCols(); drawFilters();
  });
  tplSel.addEventListener('change', () => {
    const tpl = CR_TEMPLATES[Number(tplSel.value)];
    if (!tpl) return;
    d = crNormalize(tpl[1]());
    if (!name.value) name.value = tpl[0].startsWith('Leer') ? '' : tpl[0];
    srcSel.value = d.source; group.checked = d.group; limit.value = d.limit; dirSel.value = d.sort.dir; collSel.value = '';
    drawCols(); drawFilters(); tplSel.value = '';
  });

  const body = () => ({ name: name.value, description: desc.value,
    definition: { ...d, collection: collSel.value, group: group.checked, limit: Number(limit.value || 5000), sort: { i: d.sort.i, dir: dirSel.value } } });

  async function preview() {
    prevBox.replaceChildren(h('p.muted', 'Wird ausgeführt …'));
    try {
      const res = await POST('/api/customreports/preview', body());
      const t = table(res.columns.map((c) => ({ key: c.key, label: c.label, cls: c.type === 'number' || c.type === 'bytes' ? 'right' : '',
        render: c.type === 'bytes' ? bytesCell(c.key) : c.type === 'date' ? (x) => fmtDate(x[c.key]) : undefined })),
        res.rows, { key: null, fill: false, columns: false, search: false, noun: 'Zeilen', id: (x) => JSON.stringify(x), empty: 'Keine Zeilen – Filter prüfen.' });
      t.el.querySelector('.tbl-scroll').style.maxHeight = '340px';
      prevBox.replaceChildren(h('p.muted.small', `${plural(res.rows.length, 'Zeile', 'Zeilen')}${res.truncated ? ' (Vorschau: erste 200)' : ''} · ${res.ms} ms`), t.el);
    } catch (e) { prevBox.replaceChildren(h('p', h('span.badge.err', 'Fehler'), ' ', e.message)); }
  }

  drawCols(); drawFilters();
  const tabs = tabsBox([
    ['Daten', h('div.form', isNew ? field('Vorlage', tplSel, 'Startpunkt wählen und dann anpassen.') : null, field('Name', name), field('Beschreibung', desc),
      field('Datenquelle', srcSel, 'Bestimmt, wofür eine Zeile steht: ein Computer, eine installierte Anwendung, ein Laufwerk, ein Drucker … Spalten des Computers lassen sich immer dazunehmen.'))],
    ['Spalten', h('div', colBox)],
    ['Filter', h('div', rowBox, h('div', { style: { height: '12px' } }), compBox)],
    ['Ausgabe', h('div.form', field('Zusammenfassen', h('label.chk', group, 'gleiche Zeilen zusammenfassen und zählen'), 'Z. B. Quelle „Anwendungen“, Spalten Name + Version → wie viele Computer haben welche Version.'),
      field('Sortieren nach', h('div.row', { style: { gap: '6px' } }, sortSel, dirSel)), field('Höchstens', h('div.row', { style: { gap: '6px' } }, limit, h('span.muted', 'Zeilen')), 'Schutz vor sehr großen Ergebnissen (bis 100 000).'))],
    ['Vorschau', prevBox],
  ]);
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => {
      const u = r.usage || { subscriptions: [] };
      const extra = (u.subscriptions.length ? ` Er wird auch aus ${plural(u.subscriptions.length, 'Berichtsversand', 'Berichtsversänden')} entfernt (${u.subscriptions.join(', ')}).` : '') + (u.alert ? ' Die zugehörige Benachrichtigung wird gelöscht.' : '');
      if (!(await confirmDlg(`Eigenen Bericht „${r.name}“ löschen?${extra}`, 'Löschen', true))) return false;
      await DEL(`/api/customreports/${r.id}`); toast('Gelöscht', 'ok'); go('#/reports');
    } });
  }
  buttons.unshift({ label: 'Vorschau', icon: 'play', onClick: async () => { tabs.show(4); await preview(); return false; } });
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    const saved = isNew ? await POST('/api/customreports', body()) : await PUT(`/api/customreports/${r.id}`, body());
    toast('Bericht gespeichert', 'ok');
    if (isNew) go(`#/reports/${saved.key}`); else route();
  } });
  modal(isNew ? 'Neuer eigener Bericht' : `Eigener Bericht: ${r.name}`, tabs, buttons, { xwide: true, sticky: true, help: 'customreports' });
}

async function customReportAlert(def) {
  const [d, cols] = await Promise.all([GET('/api/mail/alerts'), collections()]);
  const a = d.alerts.find((x) => x.key === `report:${def.id}`);
  if (!a) { toast('Benachrichtigung nicht gefunden', 'err'); return; }
  if (!d.configured) toast('Hinweis: E-Mail ist noch nicht eingerichtet (Einstellungen → E-Mail).', 'err');
  alertDlg(a, cols, () => route());
}

// ---------------------------------------------------------------------------
// Ansicht: Protokoll
// ---------------------------------------------------------------------------
async function viewLogs(_p, params) {
  setTop('Protokoll', 'Anmeldungen, Scans, Bereitstellungen und Systemereignisse');
  const level = sel([['', 'Alle Stufen'], ['INFO', 'Info'], ['WARN', 'Warnung'], ['ERROR', 'Fehler']], params.get('level') || '');
  const cat = sel([['', 'Alle Kategorien'], ['auth', 'Anmeldung'], ['ad', 'Active Directory'], ['ldap', 'LDAP'], ['scan', 'Scan'], ['deploy', 'Bereitstellung'],
    ['rule', 'Regeln'], ['winget', 'winget'], ['report', 'Berichte'], ['mail', 'E-Mail'], ['kiosk', 'Kiosk'], ['repo', 'Repository'], ['secret', 'Kennwörter/Schlüssel'], ['system', 'System']], params.get('category') || '');
  const q = h('input', { type: 'search', placeholder: 'Suche im Server …', value: params.get('q') || '' });
  const qs = () => new URLSearchParams({ level: level.value, category: cat.value, q: q.value }).toString();
  const apply = () => go(`#/logs?${qs()}`);
  [level, cat].forEach((e) => e.addEventListener('change', apply));
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') apply(); });
  const rows = await GET(`/api/logs?${qs()}`);
  const t = table([
    { key: 'ts', label: 'Zeit', render: (r) => fmtDate(r.ts) },
    { key: 'level', label: 'Stufe', render: levelBadge },
    { key: 'category', label: 'Kategorie' },
    { key: 'computer', label: 'Computer', render: (r) => (r.computer_id ? h('a', { href: `#/computer/${r.computer_id}` }, r.computer || r.computer_id) : '') },
    { key: 'username', label: 'Benutzer' },
    { key: 'message', label: 'Meldung', cls: 'wrap' },
  ], rows, { sort: 'ts', dir: -1, noun: 'Einträge', placeholder: 'In Ergebnissen filtern …' });
  t.actions.append(level, cat, q, btn('CSV', () => downloadUrl(`/api/logs/csv?${qs()}`), { icon: 'download' }));
  setContent(h('div', { style: { height: '100%', display: 'flex' } }, h('div.pane.card', t.el)));
  every(15000, async () => { if (parseHash().parts[0] === 'logs') t.setRows(await GET(`/api/logs?${qs()}`)); });
}

// ---------------------------------------------------------------------------
// Ansicht: Software-Kiosk (Verwaltung) – Anfragen freigeben, Katalog pflegen, Zugang einstellen
// ---------------------------------------------------------------------------
// Auswahl aus dem AD als aufklappbarer Baum – OUs (mode 'ou') oder Gruppen (mode 'group', mit Suche).
// Liefert die gewählten Einträge [{dn, name, sam, kind}] oder null bei „Abbrechen“.
function adPicker(mode) {
  return new Promise((resolve) => {
    const chosen = new Map();
    const count = h('span.muted.small');
    const upd = () => { count.textContent = chosen.size ? fmtT('{0} ausgewählt', chosen.size) : ''; };
    const treeBox = h('div.adtree');
    const resBox = h('div.adtree', { style: { display: 'none' } });
    const check = (it) => h('input', { type: 'checkbox', checked: chosen.has(it.dn), onclick: (e) => e.stopPropagation(),
      onchange: (e) => { if (e.target.checked) chosen.set(it.dn, it); else chosen.delete(it.dn); upd(); } });
    const node = (it, depth) => {
      const isGroup = it.kind === 'group';
      const selectable = mode === 'group' ? isGroup : !isGroup;
      const kids = h('div', { style: { display: 'none' } });
      const tw = h('span.adtw', isGroup ? '' : '▸');
      const cb = selectable ? check(it) : null;
      let open = false;
      const toggle = async () => {
        if (isGroup) { if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); } return; }
        open = !open; tw.textContent = open ? '▾' : '▸'; kids.style.display = open ? '' : 'none';
        if (open && !kids.dataset.loaded) { kids.dataset.loaded = '1'; await load(it.dn, kids, depth + 1); }
      };
      const row = h('div.adtree-row', { style: { paddingLeft: `${depth * 18 + 6}px` }, title: it.dn, onclick: toggle },
        tw, cb, icon(isGroup ? 'users' : 'folder'), h('span.raw', it.name),
        isGroup && it.scope ? h('span.muted.small', ` · ${it.scope}${it.security === false ? ', Verteiler' : ''}`) : null,
        it.desc ? h('span.muted.small.raw.adtree-d', it.desc) : null);
      return h('div', row, kids);
    };
    async function load(dn, box, depth) {
      box.replaceChildren(h('div.muted.small', { style: { padding: `4px ${depth * 18 + 30}px` } }, 'Lade …'));
      try {
        const r = await GET(`/api/ad/browse?groups=${mode === 'group' ? 1 : 0}&dn=${encodeURIComponent(dn || '')}`);
        box.replaceChildren(...(r.items.length ? r.items.map((it) => node(it, depth))
          : [h('div.muted.small', { style: { padding: `4px ${depth * 18 + 30}px` } }, mode === 'group' ? 'Keine Gruppen oder Unterordner.' : 'Keine Unter-OUs.')]));
        return r;
      } catch (e) { box.replaceChildren(h('div.err', { style: { padding: '6px' } }, e.message)); return null; }
    }
    (async () => {
      const r0 = await GET('/api/ad/browse?groups=0&dn=').catch(() => null);
      const root = { dn: (r0 && r0.dn) || '', name: (r0 && r0.path) || 'Domäne', kind: 'ou' };
      const el = node(root, 0);
      treeBox.replaceChildren(el);
      el.firstChild.click();  // Domäne aufklappen
    })();
    let qt = null;
    const q = mode === 'group' ? h('input', { type: 'search', placeholder: 'Gruppe suchen (Name oder Beschreibung) …', style: { width: '100%', marginBottom: '8px' },
      oninput: () => {
        clearTimeout(qt);
        qt = setTimeout(async () => {
          const v = q.value.trim();
          treeBox.style.display = v.length >= 2 ? 'none' : ''; resBox.style.display = v.length >= 2 ? '' : 'none';
          if (v.length < 2) return;
          try {
            const res = await GET(`/api/ad/groups?q=${encodeURIComponent(v)}`);
            resBox.replaceChildren(...(res.length ? res.map((it) => h('div.adtree-row', { title: it.dn, onclick: (e) => { const c = e.currentTarget.querySelector('input'); c.checked = !c.checked; c.dispatchEvent(new Event('change')); } },
              check(it), icon('users'), h('span.raw', it.name), h('span.muted.small.raw.adtree-d', it.path))) : [h('div.muted.small', { style: { padding: '6px' } }, 'Keine Gruppe gefunden.')]));
          } catch (e) { resBox.replaceChildren(h('div.err', e.message)); }
        }, 300);
      } }) : null;
    let ok = false;
    modal(mode === 'group' ? 'Gruppen aus dem AD wählen' : 'OUs aus dem AD wählen', h('div', q, treeBox, resBox,
      h('p.muted.small', { style: { marginBottom: 0 } }, mode === 'group' ? 'Aufklappen mit ▸, Häkchen bei den Gruppen setzen – auch aus mehreren OUs.' : 'Aufklappen mit ▸, Häkchen bei den OUs setzen. Eine OU gilt immer mit allen Unter-OUs.')),
    [{ label: 'Abbrechen' }, { label: 'Übernehmen', cls: 'primary', onClick: () => { ok = true; } }], { wide: true, onClose: () => resolve(ok ? [...chosen.values()] : null) });
    const foot = $('#modal-root .backdrop:last-child .modal-f');
    if (foot) foot.prepend(count);
  });
}
function fmtT(s, ...a) { return tr(s.replace(/\{(\d+)\}/g, (_m, i) => (a[i] === undefined ? '' : a[i]))); }

const KIOSK_BADGE = { pending: 'warn', scheduled: 'warn', queued: 'info', running: 'info', success: 'ok', skipped: 'ok',
  failed: 'err', failed_start: 'err', rejected: 'err', withdrawn: '', cancelled: '' };
const KIOSK_ACTION = { install: 'Installieren', uninstall: 'Deinstallieren', reboot: 'Neustart' };

async function viewKiosk(_p, params) {
  const d = await GET('/api/kiosk/admin');
  setTop('Software-Kiosk', 'Nutzer installieren freigegebene Software selbst – nur auf dem Rechner, an dem sie angemeldet sind',
    btn('Kiosk öffnen', () => window.open('/kiosk', '_blank', 'noopener'), { icon: 'up', title: 'Die Seite für Nutzer in einem neuen Tab' }));
  const pend = d.requests.filter((r) => r.status === 'pending').length;
  const decide = async (r, approve) => {
    let note = '';
    if (!approve) {
      const inp = h('input', { placeholder: 'optional, sieht der Nutzer', style: { width: '100%' } });
      let ok = false;
      await new Promise((res) => modal('Anfrage ablehnen', h('div', h('p', `„${r.package_name}“ für ${r.computer_name} (${r.display_name || r.username}) ablehnen?`),
        h('label', 'Begründung'), inp), [{ label: 'Abbrechen' }, { label: 'Ablehnen', cls: 'primary', onClick: () => { ok = true; note = inp.value; } }], { onClose: res }));
      if (!ok) return;
    }
    if (await run(() => POST(`/api/kiosk/admin/requests/${r.id}/${approve ? 'approve' : 'reject'}`, { note }), approve ? 'Freigegeben – Bereitstellung gestartet' : 'Abgelehnt')) route();
  };
  const reqT = table([
    { key: 'created_at', label: 'Angefragt', value: (r) => fmtDate(r.created_at), sort: (r) => r.created_at },
    { key: 'user', label: 'Benutzer', value: (r) => `${r.display_name || r.username}${r.display_name && r.display_name !== r.username ? ` (${r.username})` : ''}` },
    { key: 'computer_name', label: 'Computer', render: (r) => (r.computer_id ? h('a.raw', { href: `#/computer/${r.computer_id}` }, r.computer_name) : r.computer_name) },
    { key: 'package_name', label: 'Software' },
    { key: 'action', label: 'Aktion', value: (r) => KIOSK_ACTION[r.action] || r.action },
    { key: 'status', label: 'Status', value: (r) => r.text, render: (r) => h('span', h('span.badge', { class: KIOSK_BADGE[r.status] || '' }, r.text),
      r.message && r.status !== 'pending' ? h('div.muted.small', { title: r.message }, r.message.slice(0, 90)) : null) },
    { key: 'decided', label: 'Entschieden', value: (r) => (r.decided_by ? `${r.decided_by} · ${fmtDate(r.decided_at)}` : '') },
    { key: 'dep', label: '', nosort: true, render: (r) => (r.status === 'pending'
      ? h('span.row', { style: { gap: '4px' } }, btn('Freigeben', () => decide(r, true), { cls: 'small primary' }), btn('Ablehnen', () => decide(r, false), { cls: 'small' }))
      : (r.deployment_id ? h('a', { href: `#/deployment/${r.deployment_id}` }, `#${r.deployment_id}`) : '')) },
  ], d.requests, { key: 'kiosk-requests', noun: 'Anfragen', empty: 'Noch keine Anfragen.', fill: false });

  const cats = [...new Set(d.items.map((i) => i.category).filter(Boolean))].sort();
  const itemDlg = (it) => {
    const isNew = !it;
    const x = it ? { ...it } : { package_id: '', enabled: 1, approval: 0, category: '', description: '', uninstall_package_id: '', collection_id: '' };
    const inCat = new Set(d.items.map((i) => i.package_id));
    const pkgSel = sel([['', '— Paket wählen —'], ...d.packages.filter((p) => !inCat.has(p.id) || p.id === x.package_id).map((p) => [p.id, p.version ? `${p.name} (${p.version})` : p.name])],
      x.package_id, { disabled: !isNew });
    const en = h('input', { type: 'checkbox', checked: !!x.enabled });
    const appr = h('input', { type: 'checkbox', checked: !!x.approval });
    const cat = h('input', { value: x.category || '', placeholder: 'z. B. Office, Grafik, Werkzeuge' });
    combo(cat, cats);
    const desc = h('textarea', { rows: 3, placeholder: 'Kurze Beschreibung für Nutzer (leer = Paketbeschreibung)', value: x.description || '' });
    const un = sel([['', '— keine (Deinstallieren nicht anbieten) —'], ...d.packages.map((p) => [p.id, p.name])], x.uninstall_package_id || '');
    const col = sel([['', tr('Alle Computer')], ...collChoiceOptions(d.collections.filter((c) => c.id > 0))], x.collection_id || '', { 'data-notr': '1' });
    const grp = h('textarea', { rows: 3, style: { width: '100%' }, placeholder: 'leer = alle Kiosk-Nutzer\neine Gruppe je Zeile – Name oder DN, z. B. Grafik-Team', value: x.groups || '' });
    const pickGroups = async () => {
      const chosen = await adPicker('group');
      if (!chosen || !chosen.length) return;
      const have = grp.value.split('\n').map((v) => v.trim()).filter(Boolean);
      const low = new Set(have.map((v) => v.toLowerCase()));
      for (const g of chosen) { const v = g.sam || g.name; if (!low.has(v.toLowerCase())) { have.push(v); low.add(v.toLowerCase()); } }
      grp.value = have.join('\n');
    };
    // Symbol: Vorschau aus der gewählten Datei (als data:-URL – die Seite erlaubt keine blob:-Bilder)
    let iconData = null; let iconFile = null; let iconClear = false;
    const prev = h('div.kiosk-ic');
    const drawPrev = () => {
      const src = iconData || (!iconClear && x.icon);
      prev.replaceChildren(src ? h('img', { src, alt: '' }) : h('span', ((x.name || pkgSel.selectedOptions[0]?.textContent || '?').replace(/[^A-Za-zÄÖÜäöü0-9 ]/g, '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?')));
    };
    const fileIn = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp,image/x-icon,.ico', onchange: (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (f.size > 512 * 1024) { toast('Bild zu groß (höchstens 512 KB)', 'err'); e.target.value = ''; return; }
      if (/svg/i.test(f.type) || /\.svg$/i.test(f.name)) { toast('SVG wird aus Sicherheitsgründen nicht angenommen – bitte als PNG speichern.', 'err'); e.target.value = ''; return; }
      iconFile = f; iconClear = false;
      const rd = new FileReader(); rd.onload = () => { iconData = rd.result; drawPrev(); }; rd.readAsDataURL(f);
    } });
    pkgSel.addEventListener('change', drawPrev);
    drawPrev();
    const buttons = [{ label: 'Abbrechen' }];
    if (!isNew) buttons.unshift({ label: 'Aus dem Kiosk entfernen', cls: 'danger', onClick: async () => {
      if (!(await confirmDlg(`„${x.name}“ aus dem Kiosk entfernen? Bisherige Anfragen bleiben im Verlauf.`, 'Entfernen', true))) return false;
      await DEL(`/api/kiosk/admin/items/${x.package_id}`); toast('Entfernt', 'ok'); go('#/kiosk?tab=catalog'); return true;
    } });
    buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
      const pid = Number(pkgSel.value);
      if (!pid) { toast('Bitte ein Paket wählen', 'err'); return false; }
      await PUT(`/api/kiosk/admin/items/${pid}`, { enabled: en.checked, approval: appr.checked, category: cat.value, description: desc.value,
        uninstall_package_id: un.value ? Number(un.value) : null, collection_id: col.value ? Number(col.value) : null, groups: grp.value });
      if (iconFile) await api(`/api/kiosk/admin/items/${pid}/icon`, { method: 'PUT', body: iconFile, raw: true });
      else if (iconClear && x.icon) await DEL(`/api/kiosk/admin/items/${pid}/icon`);
      toast('Gespeichert', 'ok'); go('#/kiosk?tab=catalog'); return true;
    } });
    modal(isNew ? 'Software zum Kiosk hinzufügen' : `Kiosk: ${x.name}`, h('div.form',
      field('Paket', pkgSel, 'Ein vorhandenes Paket (Pakete). Nutzer sehen Name, Version und die Beschreibung.'),
      field('Angeboten', h('label.chk', en, 'im Kiosk anzeigen')),
      field('Freigabe', h('label.chk', appr, 'erst nach Freigabe durch einen Administrator installieren'), 'Sonst startet die Installation sofort. Bei Freigabe kommt eine Mail (Kiosk-Einstellungen).'),
      field('Symbol', h('div.row', { style: { gap: '10px', alignItems: 'center' } }, prev, h('label.file-pick', fileIn, h('span', 'Bild wählen …')),
        btn('Entfernen', () => { iconFile = null; iconData = null; iconClear = true; fileIn.value = ''; drawPrev(); }, { cls: 'small ghost' })),
        'PNG, JPEG, GIF, WebP oder ICO, höchstens 512 KB – am besten quadratisch, z. B. 128 × 128 Pixel. Ohne Symbol zeigt der Kiosk die Anfangsbuchstaben.'),
      field('Kategorie', cat),
      field('Beschreibung', desc),
      field('Deinstallieren', un, 'Paket, das die Software wieder entfernt (z. B. per Rechtsklick in „Software“ → Deinstallationspaket erstellen). Ohne Paket gibt es im Kiosk keinen Knopf „Deinstallieren“.'),
      field('Nur für', col, 'Nur Computer in dieser Sammlung sehen die Software – z. B. eine Lizenz nur für eine Abteilung.'),
      field('Nur für Gruppen', h('div', grp, h('div.row', { style: { marginTop: '6px' } }, btn('Aus dem AD wählen …', pickGroups, { cls: 'small', icon: 'users' }))),
        'Nur Mitglieder dieser AD-Gruppen (auch verschachtelt) sehen und bestellen die Software. Leer = alle, die den Kiosk benutzen dürfen. Gilt zusätzlich zu „Nur für“ (Computer).')), buttons, { wide: true });
  };
  const itemT = table([
    { key: 'name', label: 'Software', render: (r) => h('span.row', { style: { gap: '8px' } }, r.icon ? h('img.kiosk-ic-s', { src: r.icon, alt: '' }) : h('span.kiosk-ic-s'),
      h('span', h('b.raw', r.name), r.version ? h('span.muted', ` ${r.version}`) : null)) },
    { key: 'category', label: 'Kategorie' },
    { key: 'approval', label: 'Freigabe', value: (r) => (r.approval ? 'nötig' : 'sofort'), render: (r) => h('span.badge', { class: r.approval ? 'warn' : 'ok' }, r.approval ? 'nötig' : 'sofort') },
    { key: 'uninstall_name', label: 'Deinstallieren', value: (r) => r.uninstall_name || '–' },
    { key: 'collection_name', label: 'Nur für', value: (r) => r.collection_name || 'Alle Computer' },
    { key: 'groups', label: 'Nur für Gruppen', value: (r) => (r.groups || '').split('\n').filter(Boolean).join(', '),
      render: (r) => (r.groups ? h('span.raw', r.groups.split('\n').filter(Boolean).join(', ')) : h('span.muted', 'alle')) },
    { key: 'requests', label: 'Anfragen', cls: 'right', value: (r) => r.requests, render: (r) => h('span', String(r.requests), r.pending ? h('span.badge.warn', { style: { marginLeft: '6px' } }, `${r.pending} offen`) : null) },
    { key: 'enabled', label: 'Angeboten', value: (r) => (r.enabled ? 'Ja' : 'Nein') },
  ], d.items, { key: 'kiosk-items', noun: 'Einträge', empty: 'Noch keine Software im Kiosk – „Software hinzufügen“.', onRow: (r) => itemDlg(r), fill: false });
  itemT.actions.append(btn('Software hinzufügen', () => itemDlg(null), { cls: 'primary small', icon: 'plus' }));

  const s = d.settings;
  const kEn = h('input', { type: 'checkbox', checked: !!Number(s.kiosk_enabled) });
  const kTitle = h('input', { value: s.kiosk_title || '', style: { maxWidth: '320px' } });
  const kGroups = h('textarea', { rows: 4, style: { width: '100%' }, placeholder: 'eine Gruppe je Zeile – Name oder DN, z. B.\nDomänen-Benutzer\nCN=Kiosk-Nutzer,OU=Gruppen,DC=firma,DC=local', value: s.kiosk_groups || '' });
  const kOus = h('textarea', { rows: 4, style: { width: '100%' }, placeholder: 'eine OU je Zeile (mit allen Unter-OUs), z. B.\nOU=Mitarbeiter,DC=firma,DC=local', value: s.kiosk_ous || '' });
  const kNotify = h('input', { type: 'checkbox', checked: !!Number(s.kiosk_notify) });
  const kTo = h('input', { value: s.kiosk_notify_to || '', placeholder: 'leer = Standard-Empfänger (E-Mail-Einstellungen)' });
  const kMax = h('input', { type: 'number', min: 1, max: 500, value: s.kiosk_max_per_day, style: { maxWidth: '100px' } });
  const url = d.url && !d.url.startsWith('/') ? d.url : `${location.origin}/kiosk`;
  // Anmeldung mit Windows (Kerberos): Schalter wird mit „Speichern“ übernommen, die Keytab sofort beim Hochladen
  const kSso = h('input', { type: 'checkbox', checked: !!Number(s.kiosk_sso) });
  const ssoState = h('div');
  const ssoOut = h('div');
  const kt = filePick('.keytab,application/octet-stream', 'Keytab-Datei wählen …');
  const drawSso = (si) => {
    const rows = si.present ? [h('div', h('b', 'Keytab hinterlegt'), si.uploaded_at ? h('span.muted', ` (${fmtDate(si.uploaded_at)})`) : null),
      ...si.entries.map((e) => h('div.small', h('code.raw', e.principal), h('span.muted', ` · kvno ${e.kvno} · `),
        h('span', { class: e.strong ? '' : 'err' }, e.enctype_name)))]
      : [h('div.muted', 'Noch keine Keytab hinterlegt.')];
    if (si.present && si.hosts.length) rows.push(h('div.small', 'Nutzer rufen den Kiosk unter ', h('b.raw', `${location.protocol}//${si.hosts[0]}${location.port ? `:${location.port}` : ''}/kiosk`), ' auf.'));
    if (Number(s.kiosk_sso) && !si.present) rows.push(h('div.small.err', 'Eingeschaltet, aber ohne Keytab – der Kiosk bietet die Windows-Anmeldung erst an, wenn eine Keytab hinterlegt ist.'));
    ssoState.replaceChildren(...rows);
  };
  drawSso(d.sso || { present: false, entries: [], hosts: [] });
  const ssoTest = async () => {
    const r = await run(() => POST('/api/kiosk/admin/sso/test'));
    if (!r) return;
    ssoOut.replaceChildren(h('table', { style: { marginTop: '8px' } }, h('tbody', r.steps.map((st) => h('tr',
      h('td', st.ok === true ? h('span.badge.ok', 'OK') : (st.ok === false ? h('span.badge.err', 'Fehler') : h('span.badge', 'Hinweis'))),
      h('td.wrap.raw', st.text))))));
  };
  const ssoPanel = h('div.sso-panel', ssoState,
    h('div.row', { style: { marginTop: '8px', flexWrap: 'wrap' } }, kt.el,
      btn('Hochladen', async () => {
        const f = kt.file();
        if (!f) { toast(tr('Bitte zuerst die Keytab-Datei wählen.'), 'err'); return; }
        const si = await run(() => api('/api/kiosk/admin/sso/keytab', { method: 'PUT', body: f, raw: true }), 'Keytab gespeichert (verschlüsselt)');
        if (si) { kt.clear(); drawSso(si); ssoOut.replaceChildren(); }
      }, { cls: 'small', icon: 'up' }),
      btn('Testen', ssoTest, { cls: 'small' }),
      btn('Entfernen', async () => {
        if (!await confirmDlg('Keytab entfernen? Die Windows-Anmeldung am Kiosk funktioniert danach nicht mehr – Nutzer melden sich mit Benutzername und Passwort an.', 'Entfernen', true)) return;
        const si = await run(() => DEL('/api/kiosk/admin/sso/keytab'), 'Keytab entfernt');
        if (si) { drawSso(si); ssoOut.replaceChildren(); }
      }, { cls: 'small ghost' }),
      btn('Anleitung', () => go('#/help?s=kiosk-sso'), { cls: 'small ghost', icon: 'help' })),
    ssoOut);
  // Auswahl aus dem AD-Baum in das Textfeld übernehmen (Gruppen als Kontoname, OUs als DN; ohne Doppelte)
  const pickInto = async (ta, mode) => {
    const sel2 = await adPicker(mode);
    if (!sel2 || !sel2.length) return;
    const have = ta.value.split('\n').map((x) => x.trim()).filter(Boolean);
    const low = new Set(have.map((x) => x.toLowerCase()));
    for (const it of sel2) {
      const v = mode === 'group' ? (it.sam || it.name) : it.dn;
      if (!low.has(v.toLowerCase())) { have.push(v); low.add(v.toLowerCase()); }
    }
    ta.value = have.join('\n');
    toast(fmtT('{0} übernommen – bitte noch „Speichern“.', sel2.length), 'ok');
  };
  const setBox = h('div.card', h('div.card-b', h('div.form',
    field('Kiosk', h('label.chk', kEn, 'eingeschaltet'), h('span', 'Adresse für Nutzer: ', h('a.raw', { href: '/kiosk', target: '_blank', rel: 'noopener' }, url), ' – z. B. als Verknüpfung per GPO auf den Desktop.')),
    field('Titel', kTitle),
    field('Gruppen', h('div', kGroups, h('div.row', { style: { marginTop: '6px' } }, btn('Aus dem AD wählen …', () => pickInto(kGroups, 'group'), { cls: 'small', icon: 'users' }))),
      'Mitglieder dieser AD-Gruppen (auch verschachtelt) dürfen den Kiosk benutzen.'),
    field('OUs', h('div', kOus, h('div.row', { style: { marginTop: '6px' } }, btn('Aus dem AD wählen …', () => pickInto(kOus, 'ou'), { cls: 'small', icon: 'folder' }))),
      'Benutzerkonten in diesen OUs (einschließlich Unter-OUs) dürfen den Kiosk benutzen. Gruppen und OUs zählen zusammen: eins von beiden genügt.'),
    field('Windows-Anmeldung', h('div', h('label.chk', kSso, 'Anmeldung mit Windows anbieten (Kerberos – ohne Benutzername und Passwort)'), ssoPanel),
      'Standard: aus. Eingeschaltet meldet der Kiosk Nutzer an Domänen-PCs automatisch mit ihrem Windows-Konto an; das Formular bleibt als Rückfall. Braucht einen SPN, eine Keytab und eine Browser-Freigabe per GPO – siehe „Anleitung“. Gilt nur für den Kiosk, nie für die Verwaltung.'),
    field('Mail bei Freigabe', h('label.chk', kNotify, 'Mail schicken, wenn eine Anfrage auf Freigabe wartet'), kTo),
    field('Höchstens', h('span.row', kMax, h('span.muted', 'Anfragen je Nutzer in 24 Stunden'))),
    field('', h('p.muted.small', { style: { margin: 0 } }, 'Nutzer sehen nur die Software im Katalog und nur den Computer, an dem sie gerade angemeldet sind – das prüft POCKET KNIFE live per WinRM. Auf die Verwaltung haben Kiosk-Nutzer keinen Zugriff. Mitglieder der Zugriffsgruppe können den Kiosk für ihren eigenen Rechner ebenfalls nutzen.')),
    field('', h('div.row', btn('Speichern', async () => {
      if (await run(() => PUT('/api/settings', { kiosk_enabled: kEn.checked ? 1 : 0, kiosk_title: kTitle.value, kiosk_groups: kGroups.value, kiosk_ous: kOus.value,
        kiosk_notify: kNotify.checked ? 1 : 0, kiosk_notify_to: kTo.value, kiosk_max_per_day: kMax.value, kiosk_sso: kSso.checked ? 1 : 0 }), 'Gespeichert')) go('#/kiosk?tab=settings');
    }, { cls: 'primary' }))))));
  const tabs = tabsBox([
    [pend ? `Anfragen (${pend} offen)` : 'Anfragen', h('div.pc-table', reqT.el)],
    [`Katalog (${d.items.length})`, h('div.pc-table', itemT.el)],
    ['Einstellungen', setBox],
  ]);
  const head = !d.enabled ? h('div.card.guard', { style: { marginBottom: '12px' } }, h('div.card-b', h('b', 'Der Kiosk ist ausgeschaltet. '),
    'Unter „Einstellungen“ einschalten und festlegen, wer ihn benutzen darf.')) : null;
  setContent(h('div', head, tabs));
  const want = params.get('tab');
  if (want === 'catalog') tabs.show(1); else if (want === 'settings' || (!d.enabled && !d.items.length)) tabs.show(2);
  every(20000, async () => { if (parseHash().parts[0] === 'kiosk' && !document.querySelector('#modal-root .backdrop')) {
    const n = await GET('/api/kiosk/admin'); reqT.setRows(n.requests);
  } });
}

// ---------------------------------------------------------------------------
// Ansicht: Einstellungen
// ---------------------------------------------------------------------------
// --- Einstellungen: Konten (verschlüsselt in der Datenbank) -------------------------------------
function accountsCard() {
  const body = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Dienstkonto & Repository-Konto')), body);
  const srcTxt = { ui: 'in POCKET KNIFE hinterlegt', env: 'aus der .env', service: 'wie Dienstkonto', '': 'nicht eingerichtet' };
  async function draw() {
    let a;
    try { a = await GET('/api/system/accounts'); } catch (e) { body.replaceChildren(h('p.muted', e.message)); return; }
    const block = (which, title, info, help) => {
      const u = h('input', { value: info.source === 'ui' || info.source === 'env' ? info.user : '', placeholder: which === 'service' ? 'svc-pocketknife' : 'leer = Dienstkonto verwenden', autocomplete: 'off' });
      const p = h('input', { type: 'password', placeholder: info.source === 'ui' ? '•••••••• (unverändert lassen)' : 'Kennwort', autocomplete: 'new-password' });
      const saveB = btn('Prüfen & speichern', async () => {
        try {
          await PUT('/api/system/accounts', { which, user: u.value, password: p.value });
          toast(which === 'service' ? 'Anmeldung erfolgreich – Dienstkonto gespeichert' : 'Repository-Konto gespeichert', 'ok'); draw();
        } catch (e) {
          if (!/nicht erreichbar/.test(e.message)) { toast(e.message, 'err'); return; }
          if (!(await confirmDlg(`${e.message}\n\nOhne Prüfung speichern? Ein falsches Kennwort kann zur Kontosperre führen.`, 'Ohne Prüfung speichern', true))) return;
          if (await run(() => PUT('/api/system/accounts', { which, user: u.value, password: p.value, skip_test: true }), 'Dienstkonto gespeichert (ungeprüft)')) draw();
        }
      }, { cls: 'small primary' });
      const resetB = info.source === 'ui' ? btn('Zurücksetzen', async () => {
        if (!(await confirmDlg(`Hinterlegtes ${title} löschen? Danach gilt ${which === 'service' ? 'wieder die .env' : 'wieder das Dienstkonto bzw. die .env'}.`, 'Zurücksetzen', true))) return;
        if (await run(() => PUT('/api/system/accounts', { which, reset: true }), 'Zurückgesetzt')) draw();
      }, { cls: 'small ghost' }) : null;
      return h('div', { style: { marginBottom: '14px' } },
        h('div.row', { style: { marginBottom: '6px' } }, h('b', title), h('span.badge', { class: info.source === 'ui' ? 'ok' : info.source ? '' : 'err' }, srcTxt[info.source] || info.source),
          info.login ? h('span.muted.small.mono', info.login) : null),
        h('div.acct-row', h('div.form.one', field('Benutzer', u)), h('div.form.one', field('Kennwort', p)), h('div.row', saveB, resetB || '')),
        h('p.muted.small', { style: { margin: '4px 0 0' } }, help));
    };
    body.replaceChildren(
      block('service', 'Dienstkonto', a.service, 'Konto mit lokalen Administratorrechten auf den Clients (LDAPS, WinRM, SMB). Vor dem Speichern wird die Anmeldung per LDAPS geprüft, damit ein Tippfehler nicht zur Kontosperre führt. Angabe als „benutzer“, „DOMÄNE\\benutzer“ oder „benutzer@domäne“.'),
      block('repo', 'Repository-Konto', a.repo, 'Optional: eigenes Konto nur für den Lesezugriff auf das Software-Repository (SMB). Leer = Dienstkonto.'),
      h('p.muted.small', { style: { margin: 0 } }, 'Kennwörter werden verschlüsselt in der Datenbank gespeichert (Schlüssel: /data/pocketknife.key). Bei einer Sicherung beide Dateien sichern. Ein in POCKET KNIFE hinterlegtes Konto hat Vorrang vor der .env – dort können SERVICE_PASSWORD und REPO_PASSWORD dann entfernt werden.'));
  }
  draw();
  return card;
}

// --- Einstellungen: Zertifikat der Weboberfläche -------------------------------------------------
function filePick(accept, label) {
  const input = h('input', { type: 'file', accept });
  const txt = h('span', label);
  input.addEventListener('change', () => { txt.textContent = input.files[0] ? input.files[0].name : tr(label); });
  return { el: h('label.file-pick', input, txt), file: () => input.files[0] || null, clear: () => { input.value = ''; txt.textContent = tr(label); } };
}
function readB64(f) {
  return new Promise((res, rej) => {
    if (!f) { res(''); return; }
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(',', 2)[1] || '');
    r.onerror = () => rej(new Error('Datei nicht lesbar'));
    r.readAsDataURL(f);
  });
}
function certRows(d) {
  const days = d.days_left;
  return [
    field('Ausgestellt für', h('span', h('b', d.cn), d.names.length ? h('span.muted', ` · ${d.names.join(', ')}`) : null)),
    field('Aussteller', h('span', d.self_signed ? h('span.badge.warn', 'selbstsigniert') : d.issuer_cn)),
    field('Gültig', h('span', `${fmtDate(d.not_before, false)} – ${fmtDate(d.not_after, false)} `,
      h('span.badge', { class: days < 0 ? 'err' : days < 30 ? 'warn' : 'ok' }, days < 0 ? 'abgelaufen' : `noch ${days} Tage`))),
    field('Schlüssel', h('span', d.key)),
    field('Fingerabdruck (SHA-256)', h('span.mono.small', { style: { wordBreak: 'break-all' } }, d.sha256.match(/.{2}/g).join(':'))),
  ];
}
function tlsCard() {
  const body = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Zertifikat der Weboberfläche'), helpButton('certificate')), body);
  const SRC = { self: 'selbstsigniert (automatisch erzeugt)', custom: 'eigenes Zertifikat', csr: 'eigenes Zertifikat (aus Anforderung)' };
  const done = (r, msg) => {
    toast(r.live ? `${tr(msg)} – ${tr('ab sofort aktiv. Seite neu laden, um es im Browser zu sehen.')}` : `${tr(msg)} – ${tr('aktiv nach dem nächsten Start des Containers.')}`, 'ok');
    draw();
  };
  async function draw() {
    let i;
    try { i = await GET('/api/tls'); } catch (e) { body.replaceChildren(h('p.muted', e.message)); return; }
    const parts = [];
    if (!i.https) {
      parts.push(h('div.help-note', 'HTTPS ist abgeschaltet (PK_HTTPS=false). Das Zertifikat wird erst verwendet, wenn PK_HTTPS=true gesetzt und der Container neu gestartet wird.'));
    }
    // Aktuelles Zertifikat
    if (i.cert) {
      parts.push(h('div.row', { style: { marginBottom: '8px' } }, h('b', 'Aktuell'), h('span.badge', { class: i.source === 'self' ? '' : 'ok' }, SRC[i.source] || i.source),
        i.installed ? h('span.muted.small', `seit ${fmtDate(i.installed)}`) : null, h('div.spacer'),
        h('a.btn.small', { href: '/api/tls/cert', download: '' }, icon('download'), 'Zertifikat herunterladen')));
      parts.push(h('div.form', certRows(i.cert),
        i.chain.length ? field('Zertifikatskette', h('span', i.chain.map((c) => c.cn).join(' → '))) : null,
        i.hostname_ok ? null : field('Hinweis', h('span.badge.warn', { style: { whiteSpace: 'normal' } }, `PK_PUBLIC_HOSTNAME „${i.hostname}“ ist nicht im Zertifikat enthalten`))));
    } else if (i.error) parts.push(h('p.err-text', i.error));

    // Eigenes Zertifikat hochladen
    const mode = sel([['pfx', 'PFX / PKCS#12 (.pfx, .p12) – Zertifikat mit Schlüssel'], ['pem', 'PEM / CER (.crt, .cer, .pem) + Schlüsseldatei']], 'pfx');
    const pfx = filePick('.pfx,.p12', 'PFX-Datei wählen …');
    const pfxPw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'Kennwort der PFX-Datei' });
    const crt = filePick('.crt,.cer,.pem,.der,.p7b', 'Zertifikat wählen …');
    const chainF = filePick('.crt,.cer,.pem,.p7b', 'optional: Zwischenzertifikat(e) …');
    const key = filePick('.key,.pem', i.pending ? 'Schlüssel (leer = aus der Anforderung)' : 'Schlüsseldatei wählen …');
    const keyPw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'nur bei verschlüsseltem Schlüssel' });
    const pfxBox = h('div.form', field('PFX-Datei', pfx.el), field('Kennwort', pfxPw));
    const pemBox = h('div.form', field('Zertifikat', crt.el, 'Serverzertifikat; eine PEM-Datei darf die komplette Kette enthalten.'),
      field('Zwischenzertifikate', chainF.el, 'Nur falls die Kette nicht schon in der Zertifikatsdatei steckt.'),
      field('Privater Schlüssel', key.el), field('Kennwort', keyPw));
    const result = h('div');
    const toggle = () => { pfxBox.style.display = mode.value === 'pfx' ? '' : 'none'; pemBox.style.display = mode.value === 'pem' ? '' : 'none'; result.replaceChildren(); };
    mode.addEventListener('change', toggle);
    toggle();
    const payload = async () => {
      if (mode.value === 'pfx') {
        if (!pfx.file()) throw new Error('PFX-Datei wählen');
        return { kind: 'pfx', data: await readB64(pfx.file()), password: pfxPw.value };
      }
      if (!crt.file()) throw new Error('Zertifikat wählen');
      const [a, b] = await Promise.all([crt.file().arrayBuffer(), chainF.file() ? chainF.file().arrayBuffer() : Promise.resolve(new ArrayBuffer(0))]);
      // Zertifikat und Kette zu einer Datei zusammenfügen (PEM hintereinander; DER-Kette separat nicht möglich)
      const isPem = (buf) => new TextDecoder().decode(buf.slice(0, 64)).includes('-----BEGIN');
      let all = new Uint8Array(a);
      if (b.byteLength) {
        if (!isPem(a) || !isPem(b)) throw new Error('Zwischenzertifikate bitte im PEM-Format (Base64) – oder Zertifikat und Kette als PFX hochladen.');
        const nl = new TextEncoder().encode('\n');
        all = new Uint8Array(a.byteLength + 1 + b.byteLength); all.set(new Uint8Array(a)); all.set(nl, a.byteLength); all.set(new Uint8Array(b), a.byteLength + 1);
      }
      let bin = ''; all.forEach((x) => { bin += String.fromCharCode(x); });
      return { kind: 'pem', data: btoa(bin), key: await readB64(key.file()), key_password: keyPw.value };
    };
    const checkB = btn('Prüfen', async () => {
      try {
        const r = await POST('/api/tls/check', await payload());
        result.replaceChildren(h('div.fprev-box', h('div.row', h('span.badge.ok', 'Zertifikat und Schlüssel passen zusammen')),
          h('div.form', { style: { marginTop: '8px' } }, certRows(r.cert), r.chain.length ? field('Zertifikatskette', h('span', r.chain.map((c) => c.cn).join(' → '))) : null),
          ...r.warnings.map((w) => h('div.help-note', w))));
      } catch (e) { result.replaceChildren(h('p.err-text', e.message)); }
    }, { cls: 'small' });
    const installB = btn('Installieren', async () => {
      let p;
      try { p = await payload(); } catch (e) { toast(e.message, 'err'); return; }
      if (!(await confirmDlg('Das Zertifikat der Weboberfläche ersetzen? Das bisherige wird gesichert und lässt sich wiederherstellen.', 'Installieren'))) return;
      const r = await run(() => POST('/api/tls/install', p));
      if (r) done(r, 'Zertifikat installiert');
    }, { cls: 'small primary', icon: 'shield' });
    parts.push(h('h3', { style: { margin: '18px 0 8px' } }, 'Eigenes Zertifikat hochladen'),
      h('p.muted.small', { style: { marginTop: 0 } }, 'Zum Beispiel ein Zertifikat der eigenen Zertifizierungsstelle (Active Directory-Zertifikatdienste) oder ein gekauftes bzw. Let’s-Encrypt-Zertifikat. Es wird geprüft (Schlüssel passt, gültig, Serverauthentifizierung, Hostname) und ohne Neustart aktiviert. Kein Reverse Proxy nötig.'),
      h('div.form', field('Format', mode)), pfxBox, pemBox, h('div.row', { style: { margin: '8px 0' } }, checkB, installB), result);

    // Zertifikatsanforderung
    parts.push(h('h3', { style: { margin: '18px 0 8px' } }, 'Zertifikatsanforderung (CSR) erstellen'));
    if (i.pending) {
      const ta = h('textarea.mono', { rows: 8, readonly: true, style: { width: '100%', fontSize: '11px' } });
      ta.value = i.pending.csr;
      parts.push(h('p.muted.small', { style: { marginTop: 0 } }, `Offene Anforderung vom ${fmtDate(i.pending.created)} für ${i.pending.names.join(', ') || i.pending.subject}. `,
        'Bei der Zertifizierungsstelle einreichen (z. B. certreq -submit -attrib "CertificateTemplate:WebServer" pocketknife.csr) und das ausgestellte Zertifikat oben im Format „PEM / CER“ hochladen – der Schlüssel wird automatisch aus der Anforderung genommen.'),
        ta, h('div.row', { style: { marginTop: '6px' } },
          btn('CSR herunterladen', () => downloadText(`${i.hostname}.csr`, i.pending.csr, 'application/pkcs10'), { cls: 'small', icon: 'download' }),
          btn('In Zwischenablage', () => copyText(i.pending.csr), { cls: 'small' }),
          btn('Verwerfen', async () => {
            if (!(await confirmDlg('Offene Anforderung samt Schlüssel verwerfen? Ein dafür ausgestelltes Zertifikat lässt sich danach nicht mehr verwenden.', 'Verwerfen', true))) return;
            if (await run(() => DEL('/api/tls/csr'), 'Verworfen')) draw();
          }, { cls: 'small ghost' })));
    } else {
      const cn = h('input', { value: i.hostname });
      const names = h('input', { value: i.default_names.join(', ') });
      const org = h('input', { placeholder: 'optional, z. B. Firma GmbH' });
      const ou = h('input', { placeholder: 'optional' });
      const country = h('input', { placeholder: 'DE', maxlength: 2, style: { maxWidth: '80px' } });
      const kt = sel([['rsa3072', 'RSA 3072 Bit (empfohlen)'], ['rsa2048', 'RSA 2048 Bit'], ['ec256', 'ECDSA P-256']], 'rsa3072');
      parts.push(h('p.muted.small', { style: { marginTop: 0 } }, 'Der private Schlüssel wird hier erzeugt und verlässt POCKET KNIFE nicht. Die Anforderung bei der eigenen Zertifizierungsstelle einreichen und das ausgestellte Zertifikat anschließend oben hochladen.'),
        h('div.form', field('Allgemeiner Name (CN)', cn), field('Alternative Namen (SAN)', names, 'DNS-Namen und IP-Adressen, durch Komma getrennt – genau die Adressen, unter denen POCKET KNIFE aufgerufen wird.'),
          field('Organisation', org), field('Abteilung', ou), field('Land', country), field('Schlüssel', kt)),
        h('div.row', { style: { marginTop: '8px' } }, btn('Anforderung erstellen', async () => {
          const r = await run(() => POST('/api/tls/csr', { cn: cn.value, names: names.value, org: org.value, ou: ou.value, country: country.value, key_type: kt.value }), 'Anforderung erstellt');
          if (r) draw();
        }, { cls: 'small', icon: 'file' })));
    }

    // Sicherungen / selbstsigniert
    const bsel = i.backups.length ? sel(i.backups.map((b) => [b, b.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})$/, '$3.$2.$1 $4:$5:$6')]), i.backups[0]) : null;
    parts.push(h('h3', { style: { margin: '18px 0 8px' } }, 'Zurücksetzen'),
      h('div.row',
        btn('Neues selbstsigniertes Zertifikat', async () => {
          if (!(await confirmDlg(`Ein neues selbstsigniertes Zertifikat für ${i.default_names.join(', ')} erzeugen und das aktuelle ersetzen? Browser zeigen danach wieder eine Warnung.`, 'Erzeugen', true))) return;
          const r = await run(() => POST('/api/tls/selfsigned')); if (r) done(r, 'Selbstsigniertes Zertifikat erzeugt');
        }, { cls: 'small' }),
        bsel ? h('span.muted.small', 'Sicherung:') : null, bsel,
        bsel ? btn('Wiederherstellen', async () => {
          if (!(await confirmDlg('Das gesicherte Zertifikat wieder aktivieren?', 'Wiederherstellen'))) return;
          const r = await run(() => POST('/api/tls/restore', { name: bsel.value })); if (r) done(r, 'Zertifikat wiederhergestellt');
        }, { cls: 'small' }) : null),
      h('p.muted.small', 'Dateien liegen unter /data/tls (server.crt mit Kette, server.key). Vor jedem Wechsel wird das bisherige Zertifikat in /data/tls/backup gesichert (die letzten fünf).'));
    body.replaceChildren(...parts);
  }
  draw();
  return card;
}

// --- Einstellungen: Active Directory ----------------------------------------------------------
function adCard() {
  const body = h('div.card-b', h('p.muted', 'Wird geladen …'));
  const card = h('div.card', h('div.card-h', h('h2', 'Active Directory'),
    btn('Jetzt abgleichen', async () => { await run(() => POST('/api/system/run/ad'), 'AD-Abgleich gestartet'); setTimeout(draw, 4000); }, { cls: 'small', icon: 'refresh' })), body);
  async function draw() {
    let a;
    try { a = await GET('/api/ad/settings'); } catch (e) { body.replaceChildren(h('p.muted', e.message)); return; }
    let rules = a.rules.map((r) => ({ ...r }));
    const paths = { ...a.rules_paths };
    const chk = (k) => h('input', { type: 'checkbox', checked: !!Number(a[k]) });
    const inp = { ad_auto_sync: chk('ad_auto_sync'), ad_sync_disabled: chk('ad_sync_disabled'), ad_ou_collections: chk('ad_ou_collections'), ad_group_collections: chk('ad_group_collections') };
    // Intervall in Tagen / Stunden / Minuten
    const mins = Number(a.ad_sync_minutes) || 60;
    const unit = mins % 1440 === 0 ? 1440 : mins % 60 === 0 ? 60 : 1;
    const ivN = h('input', { type: 'number', min: 1, value: mins / unit, style: { maxWidth: '90px' } });
    const ivU = sel([[1, 'Minuten'], [60, 'Stunden'], [1440, 'Tage']], unit);
    const mode = sel([[0, 'Nur als „nicht im AD“ markieren'], [1, 'Aus POCKET KNIFE löschen']], Number(a.ad_remove_missing) ? 1 : 0);
    const list = h('div.rule-list');
    const drawRules = () => list.replaceChildren(...(rules.length ? rules : []).map((r, i) => h('div.rule',
      h('span.badge', { class: r.include ? 'inc' : 'exc' }, r.include ? 'Einschließen' : 'Ausschließen'),
      h('span.dn', { title: r.dn }, paths[r.dn] || r.dn),
      h('button.ghost.small', { title: 'Umschalten', onclick: () => { r.include = !r.include; drawRules(); } }, r.include ? 'ausschließen' : 'einschließen'),
      h('button.ghost.small', { title: 'Entfernen', onclick: async () => { if (!(await askRemove(`Regel für „${paths[r.dn] || r.dn}“ entfernen?`))) return; rules.splice(i, 1); drawRules(); } }, icon('trash')))),
    rules.length ? '' : h('div.muted.small', { style: { padding: '8px' } }, 'Keine Regeln – bitte mindestens einen Container einschließen.'));
    drawRules();
    const pick = async () => {
      let cont;
      try { cont = await GET('/api/ad/containers'); } catch (e) { toast(e.message, 'err'); return; }
      const q = h('input', { type: 'search', placeholder: 'Filtern …' });
      const box = h('div.rule-list', { style: { maxHeight: '50vh' } });
      const chosen = new Set();
      const drawC = () => {
        const f = q.value.toLowerCase();
        box.replaceChildren(...cont.filter((c) => !f || c.path.toLowerCase().includes(f)).map((c) => h('label.rule',
          h('input', { type: 'checkbox', checked: chosen.has(c.dn), onchange: (e) => (e.target.checked ? chosen.add(c.dn) : chosen.delete(c.dn)) }),
          h('span.dn', { title: c.dn, style: { paddingLeft: `${(c.path.split('/').length - 1) * 12}px` } }, c.path),
          h('span.muted.small', c.count ? `${c.count} PCs` : ''))));
      };
      q.addEventListener('input', drawC); drawC();
      const add = (include) => {
        cont.filter((c) => chosen.has(c.dn)).forEach((c) => {
          paths[c.dn] = c.path;
          const ex = rules.find((r) => r.dn.toLowerCase() === c.dn.toLowerCase());
          if (ex) ex.include = include; else rules.push({ dn: c.dn, include });
        });
        drawRules();
      };
      modal('Container auswählen', h('div', q, h('div', { style: { height: '8px' } }), box,
        h('p.muted.small', 'Die nächstgelegene Regel gewinnt: Ein ausgeschlossener Container innerhalb eines eingeschlossenen wird übersprungen – und umgekehrt.')),
      [{ label: 'Abbrechen' }, { label: 'Ausschließen', onClick: () => add(false) }, { label: 'Einschließen', cls: 'primary', onClick: () => add(true) }]);
    };
    const saveB = btn('Speichern', async () => {
      const body2 = { rules, ad_sync_minutes: Math.round(Number(ivN.value) * Number(ivU.value)), ad_remove_missing: Number(mode.value) };
      for (const [k, el] of Object.entries(inp)) body2[k] = el.checked ? 1 : 0;
      if (await run(() => PUT('/api/ad/settings', body2), 'AD-Einstellungen gespeichert – wirksam beim nächsten Abgleich')) { S.collections = null; draw(); }
    }, { cls: 'small primary' });
    const st = a.last_status || '';
    body.replaceChildren(h('div.form',
      field('Letzter Abgleich', h('div', h('div', fmtDate(a.last_sync) || 'noch nie'), st ? h('div.status-line', { class: /^Fehler/.test(st) ? 'err' : 'ok' }, st) : null)),
      field('Automatisch abgleichen', h('div.row.inline-set', h('label.chk', inp.ad_auto_sync, 'alle'), ivN, ivU)),
      field('Fehlende Computer', mode, 'Was mit Computern passiert, die nicht mehr im AD (oder in keinem eingeschlossenen Container) sind.'),
      field('Deaktivierte Computer', h('label.chk', inp.ad_sync_disabled, 'deaktivierte Computerkonten übernehmen')),
      field('AD-Sammlungen', h('div', h('label.chk', { style: { display: 'flex' } }, inp.ad_ou_collections, `OU-Struktur als Sammlungen anlegen (unter „${a.domain}“)`),
        h('label.chk', { style: { display: 'flex' } }, inp.ad_group_collections, 'AD-Gruppen mit Computern als Sammlungen anlegen')),
      'AD-Sammlungen werden bei jedem Abgleich automatisch aktualisiert und sind schreibgeschützt; „Duplizieren“ erzeugt eine bearbeitbare Kopie.'),
      field('Container', h('div', list, h('div.row', { style: { marginTop: '6px' } }, btn('Container hinzufügen …', pick, { cls: 'small', icon: 'plus' }))),
        `Basis-DN: ${a.base_dn}. Es werden nur Windows-Computer übernommen.`)),
    h('div.row.end', { style: { marginTop: '10px' } }, saveB));
  }
  draw();
  return card;
}


async function viewSettings() {
  setTop('Einstellungen', 'Verbindung, Zeitpläne, Repository und winget');
  const [s, sys, scs, flds] = await Promise.all([GET('/api/settings'), GET('/api/system'), GET('/api/scanners'), GET('/api/fields')]);
  const inputs = {};
  const n = (k, attrs = {}) => (inputs[k] = h('input', { type: 'number', min: 0, value: s[k], style: { maxWidth: '140px' }, ...attrs }));
  const t = (k, attrs = {}) => (inputs[k] = h('input', { value: s[k] ?? '', ...attrs }));
  const c = (k) => (inputs[k] = h('input', { type: 'checkbox', checked: !!Number(s[k]) }));
  // Neuer Repository-Server bzw. neue Freigabe: Kennwort des Zugriffskontos zur Bestätigung (siehe Hilfe)
  const repoPw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: 'nur bei neuem Server/neuer Freigabe', style: { maxWidth: '320px' } });
  const repoPwRow = field('Kennwort bestätigen', repoPw, 'Wer Server oder Freigabe des Repositorys ändert, bestätigt das mit dem Kennwort des Zugriffskontos – die gespeicherte Anmeldung geht so nie versehentlich an einen anderen Server.');
  const repoChanged = () => { const show = inputs.repo_path && inputs.repo_path.value.trim() !== String(s.repo_path || '').trim();
    repoPwRow.forEach((el) => { if (el) el.style.display = show ? '' : 'none'; }); };
  repoPwRow.forEach((el) => { if (el) el.style.display = 'none'; });
  const withRepoPw = (body) => { if (repoPw.value) body.repo_password_confirm = repoPw.value; return body; };
  const repoSaved = (r) => { if (r) { s.repo_path = inputs.repo_path.value.trim(); repoPw.value = ''; repoChanged(); } return r; };
  const save = btn('Speichern', async () => {
    const body = {};
    for (const [k, el] of Object.entries(inputs)) body[k] = el.type === 'checkbox' ? (el.checked ? 1 : 0) : el.value;
    repoSaved(await run(() => PUT('/api/settings', withRepoPw(body)), (r) => (r.changed.length ? 'Gespeichert' : 'Keine Änderungen')));
  }, { cls: 'primary' });

  const conn = h('div.card', h('div.card-h', h('h2', 'Domäne'), btn('LDAPS testen', async () => {
    const r = await run(() => POST('/api/system/ldaptest'));
    if (r) (r.ok ? toast('LDAPS-Verbindung erfolgreich', 'ok') : toast(r.error, 'err'));
  }, { cls: 'small' })), h('div.card-b', kv([
    ['Domäne', sys.domain], ['NetBIOS', sys.netbios], ['LDAP-Server', `${sys.ldap_servers.join(', ')} : ${sys.ldap_port} (LDAPS)`],
    ['Basis-DN', sys.base_dn], ['Zugriffsgruppe', sys.access_group], ['Zertifikat (SHA-256)', h('span.mono', sys.ldap_fingerprint || 'noch nicht verbunden')],
    ['WinRM', `${sys.winrm.https ? 'HTTPS' : 'HTTP'} Port ${sys.winrm.port}, Anmeldung ${sys.winrm.auth}`],
    ['Datenverzeichnis', `${sys.data_dir} (Datenbank ${fmtBytes(sys.db_size)})`], ['Version', sys.version]]),
  sys.ldap_pending && sys.ldap_pending.host ? h('div.card.guard', { style: { margin: '10px 0' } }, h('div.card-b',
    h('div.row', h('span.badge.err', 'LDAPS-Zertifikat geändert'), h('b', sys.ldap_pending.host)),
    h('p.muted.small', `Neu: ${sys.ldap_pending.fingerprint} · bisher: ${sys.ldap_pending.old} · gesehen ${fmtDate(sys.ldap_pending.seen)}. `,
      'Vergleichen Sie den Fingerabdruck mit dem Zertifikat auf dem Domänencontroller (z. B. nach einer Zertifikatserneuerung), bevor Sie ihm vertrauen.'),
    btn('Neuem Zertifikat vertrauen', async () => {
      if (!(await confirmDlg(`Dem neuen Zertifikat von ${sys.ldap_pending.host} vertrauen?`, 'Vertrauen'))) return;
      await run(() => POST('/api/system/ldap/trust'), 'Zertifikat übernommen'); route();
    }, { cls: 'primary small' }))) : null,
  h('div.form', { style: { marginTop: '8px' } }, field('LDAPS-Zertifikat', h('label.chk', c('ldap_cert_strict'), 'strikt prüfen: bei geändertem Zertifikat Verbindung ablehnen, bis es bestätigt wird'),
    sys.ldap_ca ? 'Es wird gegen die CA aus PK_LDAP_CA_FILE geprüft.' : 'Standard: Das Zertifikat jedes Domänencontrollers wird beim ersten Kontakt vertraut und vor dem Senden eines Kennworts verglichen; eine Änderung wird protokolliert. Mit PK_LDAP_CA_FILE (CA der Domäne als PEM) erfolgt eine vollständige Prüfung.'),
  field('Abmelden bei Inaktivität (Min.)', n('session_idle_minutes', { min: 5, max: 720 }), 'Ohne Maus- oder Tastatureingabe wird die Sitzung nach dieser Zeit beendet (auch offene Fernsitzungen); kurz vorher erscheint ein Hinweis. Automatische Aktualisierungen zählen nicht als Aktivität. 5–720 Minuten, Standard 30.'),
  field('', h('div.row', btn('Speichern', async () => {
    if (await run(() => PUT('/api/settings', { ldap_cert_strict: inputs.ldap_cert_strict.checked ? 1 : 0, session_idle_minutes: inputs.session_idle_minutes.value }), 'Gespeichert') && S.me) S.me.idle_minutes = Number(inputs.session_idle_minutes.value);
  }, { cls: 'small' })))),
  h('p.muted.small', 'Domäne und Server kommen aus der .env. Das Dienstkonto wird unten, die AD-Container unter „Active Directory“ gepflegt.')));

  const sched = h('div.card', h('div.card-h', h('h2', 'Zeitpläne & Leistung')), h('div.card-b', h('div.form',
    field('Heartbeat alle (Min.)', n('heartbeat_minutes'), 'Prüft, welche Computer online sind. Wann gescannt wird, legen die Scanprofile fest.'),
    field('Parallele Scans', n('scan_concurrency', { min: 1 }), 'Wirksam nach Neustart des Containers.'),
    field('Parallele Installationen', n('deploy_concurrency', { min: 1 }), 'Wirksam nach Neustart des Containers.'),
    field('Zeitlimit Installation (Min.)', n('deploy_timeout_minutes', { min: 1 })),
    field('Nach Bereitstellungen scannen', h('label.chk', c('rescan_after_deploy'), 'nach jeder Installation/Deinstallation neu inventarisieren'), 'Standard ist das Profil „Anwendungen“; im Paket unter „Danach scannen“ änderbar.'),
    field('Protokoll aufbewahren (Tage)', n('log_retention_days'), '0 = nie automatisch löschen.'),
    field('Scan-Protokoll aufbewahren (Tage)', n('scan_log_retention_days'), 'Einträge unter „Scans“ und Scan-Meldungen im Protokoll werden nach so vielen Tagen automatisch gelöscht; 0 = nie. Die gescannten Daten bleiben erhalten.'),
    field('Bereitstellungen aufbewahren (Tage)', n('deployment_retention_days')),
    field('Softwareänderungen aufbewahren (Tage)', n('software_changes_retention_days'), 'Verlauf „installiert/aktualisiert/entfernt“ je Computer (Bericht Softwareänderungen).'),
    field('Datenbankwartung um (Stunde)', n('db_maintenance_hour', { min: 0, max: 23 }), 'Täglich zu dieser Stunde: Aufräumen, Statistiken, und bei mehr als 20 % freiem Platz VACUUM (verkleinert die Datei).'),
    field('Grenze „wenig Speicher“ (GB)', n('low_disk_gb')),
    field('Speicherplatz-Scanner: Tiefe', n('du_scan_depth', { min: 1, max: 8 }), 'Wie tief der optionale Scanner „Speicherplatz“ die Ordner aufschlüsselt (1–8). Tiefer = mehr Details, aber langsamer und größer. Nachträglich lässt sich in der Ansicht gezielt tiefer analysieren.'))));

  // Windows-Benachrichtigung („Toast“) für angemeldete Benutzer nach erfolgreicher Installation/Aktualisierung
  const toastHost = h('input', { placeholder: 'Computername, z. B. PC001', style: { maxWidth: '220px' } });
  const toastOut = h('div.small');
  inputs.toast_language = sel([['de', 'Deutsch'], ['en', 'English']], s.toast_language || 'de');
  const toastCard = h('div.card', h('div.card-h', h('h2', 'Benachrichtigung am Client')), h('div.card-b',
    h('p.muted.small', { style: { marginTop: 0 } }, 'Nach einer erfolgreichen Installation oder Aktualisierung sieht der angemeldete Benutzer eine Windows-Benachrichtigung, z. B. „7-Zip wurde aktualisiert. Installierte Version: 26.00“. Ohne angemeldeten Benutzer oder wenn alles schon aktuell war, erscheint nichts. Deinstallationen melden sich nicht.'),
    h('div.form',
      field('Benachrichtigen', h('label.chk', c('toast_enabled'), 'nach erfolgreicher Installation/Aktualisierung'), 'Je Paket abweichend einstellbar (Paket-Eigenschaften → Benachrichtigung).'),
      field('Absender', t('toast_sender', { placeholder: 'Softwareverteilung', maxlength: 60 }), 'Steht oben in der Meldung, z. B. „IT-Abteilung“.'),
      field('Sprache', inputs.toast_language),
      field('Kompatibilität', h('label.chk', c('toast_compat'), 'Absender „Windows PowerShell“ verwenden'), 'Nur einschalten, wenn auf einem Client trotz Test keine Meldung erscheint – dieser Absender ist in Windows immer registriert, zeigt aber „Windows PowerShell“ statt des eigenen Namens.'),
      field('Test', h('div', h('div.row', toastHost, btn('Test-Benachrichtigung senden', async () => {
        const r = await run(() => POST('/api/system/toast/test', { host: toastHost.value.trim() }));
        if (r) toastOut.replaceChildren(h('div', { style: { marginTop: '6px' } }, h('span.badge.' + (r.ok ? 'ok' : 'err'), r.ok ? 'gesendet' : 'nicht gezeigt'), ' ', h('span.raw', r.message)));
      }, { cls: 'small', icon: 'send' })), toastOut), 'Erst speichern – der Test nutzt die gespeicherten Einstellungen.'))));

  // Text der Windows-Meldung bei Neustart/Herunterfahren (shutdown.exe /c)
  const RB = [
    ['restart', 'Neustart (Computer-Menü)', '{absender}: Neustart in {sekunden} Sekunden.', { sekunden: 60, minuten: 1 }],
    ['shutdown', 'Herunterfahren (Computer-Menü)', '{absender}: Herunterfahren in {sekunden} Sekunden.', { sekunden: 60, minuten: 1 }],
    ['deploy', 'Neustart nach Bereitstellung', '{absender}: Neustart nach Softwareverteilung in {minuten} Minuten.', { sekunden: 120, minuten: 2 }],
    ['step', 'Paketschritt „Neustart“', '{absender}: Neustart für die Softwareverteilung', { sekunden: 60, minuten: 1 }],
    ['kiosk', 'Neustart aus dem Kiosk', '{absender}: Neustart in {minuten} Minuten (angefordert von {benutzer} im Software-Kiosk). Bitte Dateien speichern.', { sekunden: 120, minuten: 2, benutzer: 'Max Mustermann' }],
  ];
  const rbPrev = {};
  const rbUpdate = () => {
    const snd = (inputs.reboot_sender.value || '').trim() || 'POCKET KNIFE';
    for (const [k, , def, ex] of RB) {
      const tpl = (inputs['reboot_msg_' + k].value || '').trim() || def;
      const out = tpl.replace(/\{(absender|sekunden|minuten|benutzer)\}/g, (_m, n) => (n === 'absender' ? snd : String(ex[n] ?? '')));
      rbPrev[k].textContent = out;
    }
  };
  const rbFields = RB.map(([k, label, def]) => {
    rbPrev[k] = h('span.raw');
    const inp = t('reboot_msg_' + k, { placeholder: def, maxlength: 400, oninput: () => rbUpdate() });
    return field(label, h('div', inp, h('div.muted.small', { style: { marginTop: '4px' } }, tr('Beispiel: '), rbPrev[k])));
  });
  const rebootCard = h('div.card', h('div.card-h', h('h2', 'Meldung bei Neustart und Herunterfahren')), h('div.card-b',
    h('p.muted.small', { style: { marginTop: 0 } }, 'Diesen Text sieht der angemeldete Benutzer in der Windows-Meldung, bevor der Computer neu startet oder herunterfährt. Leeres Feld = Standardtext. Platzhalter: {absender}, {sekunden}, {minuten}, {benutzer} (nur Kiosk). Ein eigener Text im Paketschritt „Neustart“ hat Vorrang.'),
    h('div.form',
      field('Absender', t('reboot_sender', { placeholder: 'POCKET KNIFE', maxlength: 60, oninput: () => rbUpdate() }), 'Ersetzt {absender}, z. B. „Ihre IT-Abteilung“.'),
      ...rbFields)));
  rbUpdate();

  // Namensauflösung und „Antwortet wirklich dieser Computer?“
  const dnsInfo = h('span.muted.small');
  const dnsOut = h('div');
  const dnsName = h('input', { placeholder: 'z. B. PC-0001', style: { maxWidth: '220px' } });
  const dnsCard = h('div.card', h('div.card-h', h('h2', 'Namensauflösung & Erreichbarkeit'),
    btn('DNS-Cache leeren', async () => {
      const r = await run(() => POST('/api/system/dns/flush'));
      if (r) { toast(fmtT('{0} gespeicherte Auflösungen verworfen – der nächste Heartbeat fragt neu.', r.flushed), 'ok'); refreshDns(); }
    }, { cls: 'small', icon: 'refresh', title: 'Gespeicherte Namensauflösungen von POCKET KNIFE sofort verwerfen' })),
  h('div.card-b', h('div.form',
    field('DNS-Cache (Min.)', h('span.row', n('dns_cache_minutes', { min: 0, max: 1440 }), dnsInfo),
      'So lange gilt eine aufgelöste Adresse, danach wird neu gefragt („Flush“). 0 = bei jedem Heartbeat neu auflösen.'),
    field('DNS direkt fragen', h('label.chk', c('dns_direct'), 'die DNS-Server der Domäne direkt fragen (an Zwischenspeichern auf dem Docker-Host vorbei)'),
      'Hilft, wenn der Docker-Host alte Antworten zwischenspeichert. Antwortet keiner der Server, wird wie bisher über das System aufgelöst.'),
    field('DNS-Server', t('dns_servers', { placeholder: 'leer = die Domänencontroller (PK_LDAP_SERVERS)' }), 'Namen oder IP-Adressen, durch Komma getrennt – nur für „DNS direkt fragen“.'),
    field('Identität prüfen', h('label.chk', c('identity_check'), 'nur „online“, wenn unter der Adresse wirklich dieser Computer antwortet'),
      'Bei neuer Adresse (und spätestens nach der unten eingestellten Zeit) fragt POCKET KNIFE den Computer über WinRM nach Name und Domäne. Antwortet ein anderes Gerät, ein Gerät außerhalb der Domäne oder eines ohne WinRM unter einer zuvor anderen Adresse, gilt er als offline – Scans und Bereitstellungen laufen dann nicht auf dem falschen Gerät. Siehe Sammlung „Falsche Adresse“.'),
    field('Erneut bestätigen nach (Std.)', n('identity_recheck_hours', { min: 1, max: 168 }), 'Auch bei gleicher Adresse wird die Identität nach dieser Zeit erneut geprüft.'),
    field('Auflösung testen', h('div', h('div.row', dnsName, btn('Auflösen', async () => {
      const r = await run(() => POST('/api/system/dns/test', { name: dnsName.value.trim() }));
      if (!r) return;
      dnsOut.replaceChildren(h('div.small', { style: { marginTop: '6px' } }, h('b.raw', r.name), ' → ', h('b.raw', r.ip || tr('nicht gefunden')),
        r.direct ? h('span.muted', fmtT(' (direkt bei {0})', r.servers.join(', ') || '–')) : h('span.muted', ` (${tr('System')})`),
        r.direct && r.system_ip !== r.ip ? h('div.err', fmtT('Das System liefert eine andere Adresse: {0}', r.system_ip || '–')) : null,
        h('span.muted', ` · ${r.ms} ms`)));
    }, { cls: 'small' })), dnsOut)))));
  const refreshDns = () => GET('/api/system/dns').then((d) => dnsInfo.replaceChildren(fmtT('{0} Einträge gespeichert', d.entries))).catch(() => {});
  refreshDns();

  const repo = h('div.card', h('div.card-h', h('h2', 'Software-Repository')), h('div.card-b', h('div.form',
    field('Repository-Pfad', t('repo_path', { placeholder: '\\\\fileserver\\software$  oder  /repo', oninput: () => repoChanged() }),
      'UNC-Pfad (Zugriff per SMB mit dem Repository-/Dienstkonto) oder ein im Container eingebundener Ordner.'),
    field('Zugriffskonto', h('span', sys.repo_user, h('span.muted.small', ' – änderbar unter „Dienstkonto & Repository-Konto“'))),
    repoPwRow,
    field('', h('div.row', btn('Speichern & Verbindung testen', async () => {
      if (!repoSaved(await run(() => PUT('/api/settings', withRepoPw({ repo_path: inputs.repo_path.value }))))) { repoPw.focus(); return; }
      const r = await run(() => POST('/api/repository/test'));
      if (r) repoTestDlg(r);
    }, { cls: 'small' }), btn('Durchsuchen', () => repoBrowser(() => {}), { cls: 'small' }))))));

  const w = sys.winget;
  const file = h('input', { type: 'file', accept: '.msix', onchange: (e) => { const sp = e.target.nextSibling; if (sp) sp.textContent = e.target.files[0] ? e.target.files[0].name : 'source2.msix wählen …'; } });
  const wg = h('div.card', h('div.card-h', h('h2', 'winget')), h('div.card-b',
    kv([['Index', w.loaded ? `${num(w.count)} Pakete (Format ${w.schema})` : (w.error || 'nicht geladen')], ['Stand', fmtDate(w.updated)],
      ['Status', w.refreshing ? 'wird aktualisiert …' : 'bereit']]),
    h('div.form', { style: { marginTop: '12px' } },
      field('Automatisch aktualisieren', h('label.chk', c('winget_auto_refresh'), 'Index regelmäßig von cdn.winget.microsoft.com laden')),
      field('Intervall (Std.)', n('winget_refresh_hours', { min: 1 })),
      field('Standard-Bereich', inputs.winget_scope = sel([['machine', 'Computer (--scope machine)'], ['', 'Paketstandard'], ['user', 'Benutzer']], s.winget_scope)),
      field('Fehlendes winget', h('label.chk', c('winget_bootstrap'), 'automatisch nachinstallieren (z. B. Windows Server 2019/2022/2025)'),
        'Vor jedem winget-Schritt wird geprüft, ob winget vorhanden ist. Fehlt es, wird der App-Installer samt Abhängigkeiten installiert. Auf Systemen vor Windows 10 1809 / Server 2019 wird der Schritt übersprungen statt fehlzuschlagen.'),
      field('Offline-Quelle', t('winget_bootstrap_folder', { placeholder: 'z. B. _winget (Ordner im Repository)' }),
        'Optional: Ordner im Repository mit Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle, der *_License1.xml und DesktopAppInstaller_Dependencies.zip aus dem GitHub-Release von microsoft/winget-cli. Leer = der Client lädt die Dateien selbst von github.com.')),
    h('div.form', { style: { marginTop: '12px' } },
      field('Katalog', h('div.row',
        btn('Index jetzt laden', () => run(() => POST('/api/winget/refresh'), 'Download gestartet'), { icon: 'refresh', cls: 'small' }),
        btn('Abgleich neu berechnen', () => run(() => POST('/api/system/run/rematch'), 'Abgleich gestartet'), { cls: 'small' }))),
      field('Offline-Index', h('div.row', h('label.file-pick', file, h('span', 'source2.msix wählen …')),
        btn('Hochladen', async () => {
          if (!file.files[0]) { toast('Datei wählen', 'err'); return; }
          await run(() => api('/api/winget/upload', { method: 'POST', body: file.files[0], raw: true }), 'Index importiert');
          route();
        }, { cls: 'small' })), 'Für Netze ohne Internet: source2.msix von einem anderen Rechner laden (https://cdn.winget.microsoft.com/cache/source2.msix) und hier hochladen.'))));

  const hostIn = h('input', { placeholder: 'pc-0815.firma.local' });
  // Diagnosepaket: ZIP zur Fehlersuche – wird nur heruntergeladen, nie verschickt
  const diagDays = sel([['0', 'Seit dem letzten Update'], ['1', '1 Tag'], ['3', '3 Tage'], ['7', '7 Tage'], ['14', '14 Tage'], ['30', '30 Tage']], '7');
  const diagAnon = h('input', { type: 'checkbox', checked: true });
  const diagAlias = h('input', { placeholder: 'z. B. PC-017', style: { maxWidth: '160px' } });
  const diagOut = h('div.small');
  const diagCard = h('div.card', h('div.card-h', h('h2', 'Diagnosepaket')), h('div.card-b',
    h('p.muted.small', { style: { marginTop: 0 } }, 'Eine ZIP-Datei zur Fehlersuche: Version und Umgebung, Einstellungen (ohne Kennwörter, Keytab und Schlüssel), Protokoll des Zeitraums, Warnungen und Fehler mit technischen Details, Statistik zu Scans und Bereitstellungen sowie fehlgeschlagene Ziele mit Schrittergebnissen. Es wird nichts automatisch verschickt – Sie laden die Datei herunter und geben sie weiter, wenn Sie möchten.'),
    h('div.form',
      field('Zeitraum', diagDays, sys.version_since ? `Version ${sys.version} läuft seit ${fmtDate(sys.version_since)}. Jede Zeile im Paket trägt die Version, unter der sie entstand – Meldungen älterer Versionen sind so von aktuellen getrennt.` : null),
      field('Anonymisieren', h('label.chk', diagAnon, 'Namen und Adressen durch Platzhalter ersetzen (empfohlen)'),
        'Rechner-, Benutzer-, Server- und Gruppennamen, Domäne, AD-Pfade, IP-, MAC- und Mail-Adressen werden zu PC-001, USER-001, SRV-001, 10.99.x.y … – in jedem Paket gleich. Merkmale bleiben sichtbar: -LANG (länger als 15 Zeichen), -UML (Umlaut), -SZ (Sonderzeichen). Paket-, Software- und Sammlungsnamen bleiben lesbar.'),
      field('', h('div.row', btn('Diagnosepaket herunterladen', () => downloadUrl(`/api/system/diagnostics?days=${diagDays.value}&anon=${diagAnon.checked ? 1 : 0}`),
        { cls: 'primary', icon: 'download' }))),
      field('Platzhalter nachschlagen', h('div', h('div.row', diagAlias, btn('Nachschlagen', async () => {
        const r = await run(() => GET(`/api/system/diagnostics/lookup?alias=${encodeURIComponent(diagAlias.value.trim())}`));
        if (!r) return;
        diagOut.replaceChildren(...(r.items.length ? r.items.map((x) => h('div', h('b.raw', x.alias), ' = ', h('span.raw', x.real), h('span.muted', ` (${x.kind})`)))
          : [h('span.muted', 'Kein solcher Platzhalter.')]));
      }, { cls: 'small' }), btn('Zuordnung herunterladen (CSV)', () => downloadUrl('/api/system/diagnostics/mapping'),
        { cls: 'small ghost', icon: 'download', title: 'Alle Platzhalter mit echten Namen – bleibt bei Ihnen, nicht mitschicken' })), diagOut),
        'Steht in einer Rückmeldung z. B. „PC-017 bricht ab“, finden Sie hier den echten Rechner. Die Zuordnung verlässt den Server nur über diesen Knopf.'))));

  const maint = h('div.card', h('div.card-h', h('h2', 'Wartung')), h('div.card-b', h('div.form',
    field('Scheduler', h('span', sys.scheduler.started ? `läuft seit ${fmtDate(sys.scheduler.started)}` : 'nicht gestartet')),
    field('Letzter AD-Abgleich', h('span', fmtDate(sys.scheduler.ad_sync) || '—')),
    field('Letzter Heartbeat', h('span', fmtDate(sys.scheduler.heartbeat) || '—')),
    field('Laufende Scans', h('span', String(sys.scanning))),
    field('Datenbank', h('div.row', { style: { flexWrap: 'wrap', gap: '6px' } },
      h('span', `${fmtBytes(sys.db.size)} · davon ${sys.db.free_pct} % frei${sys.db.wal > 1048576 ? ` · WAL ${fmtBytes(sys.db.wal)}` : ''}`),
      btn('Jetzt aufräumen', async () => {
        if (!(await confirmDlg('Datenbank jetzt aufräumen und verkleinern (VACUUM)? Bei großen Datenbanken kann POCKET KNIFE dabei einige Sekunden bis Minuten langsamer reagieren.', 'Aufräumen'))) return;
        if (await run(() => POST('/api/system/run/dbmaint'), 'Wartung gestartet – Ergebnis im Protokoll')) setTimeout(route, 5000);
      }, { cls: 'small', icon: 'refresh' })),
      sys.db_maintenance_last ? `Letzte Wartung: ${sys.db_maintenance_last}` : 'Noch keine Wartung gelaufen – sie läuft täglich automatisch (siehe Zeitpläne & Leistung).'),
    field('Hintergrundaufgaben', h('span', sys.background.join(', ') || '—')),
    field('Jetzt ausführen', h('div.row',
      btn('AD abgleichen', () => run(() => POST('/api/system/run/ad'), 'AD-Abgleich gestartet'), { cls: 'small', icon: 'refresh' }),
      btn('Heartbeat', () => run(() => POST('/api/system/run/heartbeat'), 'Heartbeat gestartet'), { cls: 'small', icon: 'net' }))),
    field('Verbindung testen', h('div.row.inline-set', hostIn, btn('WinRM & SMB testen', async () => {
      const r = await run(() => POST('/api/system/hosttest', { host: hostIn.value }));
      if (r) showOutput('Verbindungstest', Object.entries(r).map(([k, v]) => `${k}: ${v}`).join('\n'));
    }, { cls: 'small' })), 'Prüft WinRM, Administratorrechte und die Freigabe ADMIN$ für einen Computernamen.'),
    field('Darstellung', sel([['', 'Wie System'], ['light', 'Hell'], ['dark', 'Dunkel']], localStorageGet('pk-theme'), { onchange: (e) => setTheme(e.target.value), style: { maxWidth: '200px' } })),
    field('Sprache / Language', sel([['de', 'Deutsch'], ['en', 'English']], PK_LANG, { onchange: (e) => setLang(e.target.value), style: { maxWidth: '200px' } }),
      'Gilt für diesen Browser.'))));

  const scCard = h('div.card', h('div.card-h', h('h2', 'Scanprofile & eigene Scanner')), h('div.card-b',
    h('p.muted', { style: { marginTop: 0 } }, `${scs.length} eigene Scanner eingerichtet. Scan-Zeitpläne und die Auswahl der Scanner je Computer-Sammlung stehen in der Navigation unter „Scanprofile“.`),
    btn('Zu den Scanprofilen', () => go('#/scanners'), { cls: 'small' })));
  const fName = h('input', { placeholder: 'z. B. Inventarnummer' });
  const fType = sel([['text', 'Text'], ['number', 'Zahl'], ['date', 'Datum']], 'text');
  const fCard = h('div.card', h('div.card-h', h('h2', 'Benutzerdefinierte Felder')), h('div.card-b',
    flds.length ? h('table', h('tbody', flds.map((f) => h('tr', h('td', h('b', f.name)), h('td.muted', { text: 'Text', number: 'Zahl', date: 'Datum' }[f.type] || f.type),
      h('td.muted', plural(f.used, 'Wert', 'Werte')), h('td.right', btn('Löschen', async () => {
        if (await confirmDlg(`Feld „${f.name}“ samt ${f.used} Werten löschen?`, 'Löschen', true)) { await run(() => DEL(`/api/fields/${f.id}`), 'Gelöscht'); route(); }
      }, { cls: 'small danger' })))))) : h('p.muted', 'Felder für eigene Angaben je Computer, z. B. Inventarnummer, Raum, Kostenstelle, Garantie bis. Sie sind auch in Sammlungsfiltern nutzbar.'),
    h('div.row', { style: { marginTop: '10px' } }, fName, fType, btn('Hinzufügen', async () => {
      if (await run(() => POST('/api/fields', { name: fName.value, type: fType.value }), 'Feld angelegt') === undefined) return; // Eingabe behalten
      S.schema = null; route();
    }, { cls: 'small primary' }))));
  // Sicherung: Einstellungen (Speichern-Knopf des Bereichs) und Liste der Sicherungen
  const bkList = h('div', h('p.muted', 'Wird geladen …'));
  const bkCard = h('div.card', h('div.card-h', h('h2', 'Automatische Sicherung'),
    btn('Jetzt sichern', async () => { const r = await run(() => POST('/api/backups'), (x) => `Sicherung erstellt (${fmtBytes(x.size)}, ${x.seconds} s)`); if (r) drawBk(); }, { cls: 'small primary', icon: 'download' })),
  h('div.card-b', h('div.form',
    field('Automatisch sichern', h('label.chk', c('backup_enabled'), 'täglich sichern und vor jedem Update (beim ersten Start einer neuen Version)')),
    field('Uhrzeit (Stunde)', n('backup_hour', { min: 0, max: 23 }), 'Standard 2 Uhr – vor der Datenbankwartung.'),
    field('Versionen aufheben', n('backup_keep', { min: 1, max: 365 }), 'So viele Sicherungen bleiben erhalten (Standard 10); ältere werden automatisch gelöscht – auch manuelle und „vor Update“.')),
  h('p.muted.small', 'Gesichert werden die Datenbank (konsistent im laufenden Betrieb), der Schlüssel pocketknife.key und das HTTPS-Zertifikat. Die Sicherung enthält damit alles, um gespeicherte Kennwörter wieder zu entschlüsseln – den Sicherungsordner bitte entsprechend schützen.'),
  bkList));
  async function drawBk() {
    let r;
    try { r = await GET('/api/backups'); } catch (e) { bkList.replaceChildren(h('p.err-text', e.message)); return; }
    if (!r.dir) { bkList.replaceChildren(h('p.err-text', r.error || 'Kein Sicherungsordner')); return; }
    const t = table([
      { key: 'created', label: 'Erstellt', render: (x) => fmtDate(x.created) },
      { key: 'label', label: 'Art', value: (x) => (x.label === 'auto' ? 'automatisch' : x.label.startsWith('vor-') ? `vor Update auf ${x.label.slice(4)}` : 'manuell') },
      { key: 'size', label: 'Größe', render: (x) => fmtBytes(x.size), cls: 'right' },
      { key: 'name', label: 'Datei', cls: 'mono small' },
      { key: 'x', label: '', nosort: true, noFilter: true, render: (x) => h('div.row', { style: { flexWrap: 'nowrap' } },
        h('a.btn.small', { href: `/api/backups/${encodeURIComponent(x.name)}`, download: x.name }, icon('download'), 'Herunterladen'),
        btn('Löschen', async () => {
          if (!(await confirmDlg(`Sicherung „${x.name}“ löschen?`, 'Löschen', true))) return;
          if (await run(() => DEL(`/api/backups/${encodeURIComponent(x.name)}`), 'Gelöscht') !== undefined) drawBk();
        }, { cls: 'small danger' })) },
    ], r.items, { fill: false, search: false, key: null, sort: 'created', dir: -1, noun: 'Sicherungen', empty: 'Noch keine Sicherung vorhanden.' });
    bkList.replaceChildren(
      h('div.row', { style: { margin: '10px 0 6px', flexWrap: 'wrap' } }, h('b', 'Ablage:'), h('span.mono', r.dir), h('span.muted', `· ${fmtBytes(r.free)} frei`),
        r.same_disk ? h('span.badge.warn', { title: 'Fällt der Datenträger aus, sind Datenbank und Sicherungen weg. In der .env PK_BACKUP_PATH auf ein anderes Laufwerk oder eine Netzwerkfreigabe setzen.' }, 'gleicher Datenträger wie die Datenbank') : null),
      r.last ? h('p.muted.small', { style: { margin: '0 0 6px' } }, `Letzte Sicherung: ${r.last}`) : null,
      t.el,
      h('p.muted.small', 'Wiederherstellen: Container stoppen (docker compose down), Inhalt der ZIP-Datei in das Datenverzeichnis entpacken (vorher pocketknife.db-wal und pocketknife.db-shm löschen), Container starten.'));
  }
  drawBk();
  const rdBody = h('div.card-b', h('p.muted', 'Wird geladen …'));
  // Zentrales VNC-Kennwort: eigenes Speichern (unabhängig vom Speichern-Knopf des Bereichs)
  const vncBox = (isSet) => {
    const pw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: isSet ? '•••••••• (gesetzt – zum Ändern neu eingeben)' : 'leer = kein zentrales Kennwort', style: { maxWidth: '320px' } });
    const state = h('span.badge', { class: isSet ? 'ok' : '' }, isSet ? 'gesetzt' : 'nicht gesetzt');
    const box = h('div.row', { style: { flexWrap: 'wrap', gap: '6px' } }, pw,
      btn('Speichern', async () => {
        if (!pw.value) { toast('Bitte ein Kennwort eingeben', 'err'); return; }
        const r = await run(() => PUT('/api/remote/vnc_password', { password: pw.value }), 'VNC-Kennwort gespeichert');
        if (r) box.replaceWith(vncBox(true));
      }, { cls: 'small primary' }),
      isSet ? btn('Entfernen', async () => {
        if (!(await confirmDlg('Zentrales VNC-Kennwort entfernen?', 'Entfernen', true))) return;
        const r = await run(() => PUT('/api/remote/vnc_password', { clear: true }), 'VNC-Kennwort entfernt');
        if (r) box.replaceWith(vncBox(false));
      }, { cls: 'small ghost' }) : null, state);
    return box;
  };
  const rdCard = h('div.card', h('div.card-h', h('h2', 'Fernsitzungen (RDP/VNC im Browser)')), rdBody);
  (async () => {
    let st; let ses = [];
    try { [st, ses] = await Promise.all([GET('/api/remote/status'), GET('/api/remote/sessions')]); } catch (e) { rdBody.replaceChildren(h('p.muted', e.message)); return; }
    inputs.remote_keyboard = sel(Object.entries(st.layouts), s.remote_keyboard);
    const sesT = table([
      { key: 'computer', label: 'Computer' }, { key: 'protocol', label: 'Art', render: (r) => r.protocol.toUpperCase() },
      { key: 'user', label: 'Bediener' }, { key: 'login', label: 'Angemeldet als' },
      { key: 'started', label: 'Seit', render: (r) => fmtDate(r.started) },
      { key: 'x', label: '', nosort: true, render: (r) => btn('Beenden', async () => {
        if (await confirmDlg(`Sitzung von ${r.user} zu ${r.computer} beenden?`, 'Beenden', true)) { await run(() => DEL(`/api/remote/sessions/${r.id}`), 'Beendet'); route(); }
      }, { cls: 'small danger' }) },
    ], ses, { fill: false, search: false, noun: 'aktive Sitzungen', empty: 'Keine aktiven Sitzungen.' });
    rdBody.replaceChildren(h('div.form',
      field('Guacamole-Dienst', st.guacd.ok ? h('span.badge.ok', `erreichbar (${st.guacd.host}:${st.guacd.port})`)
        : h('span.badge.err', { title: st.guacd.error }, `nicht erreichbar (${st.guacd.host}:${st.guacd.port})`)),
      field('Eigene Anmeldung', h('label.chk', c('remote_sso'), 'RDP mit den Anmeldedaten des POCKET KNIFE-Benutzers'),
        'Das Kennwort wird bei der Anmeldung verschlüsselt nur im Arbeitsspeicher gehalten (nie in der Datenbank) und beim Abmelden, Sitzungsende oder Neustart verworfen. Wirkt ab der nächsten Anmeldung.'),
      field('Tastaturlayout', inputs.remote_keyboard, 'Standard für neue Sitzungen; im Verbindungsdialog änderbar.'),
      field('Zentrales VNC-Kennwort', vncBox(st.vnc_password_set),
        'Optional. Ist ein Kennwort hinterlegt, bietet der VNC-Dialog „Zentrales VNC-Kennwort“ an und wählt es vor. Es wird verschlüsselt gespeichert und nie angezeigt. Klassisches VNC wertet nur die ersten 8 Zeichen aus.')),
    h('h3', { style: { margin: '14px 0 6px' } }, 'Aktive Sitzungen'), sesT.el);
  })();
  // 2.26 Bruchprüfung: Vollprüfung der Fingerabdrücke (Schnellprüfung alle 5 Minuten und vor jedem Lauf läuft immer)
  const integCard = h('div.card', h('div.card-h', h('h2', 'Bruchprüfung der Pakete')), h('div.card-b',
    h('p.muted.small', { style: { marginTop: 0 } }, 'POCKET KNIFE merkt sich Größe, Datum und Fingerabdruck (SHA-256) der Repository-Dateien, die Pakete benutzen. Alle 5 Minuten und vor jeder Bereitstellung wird geprüft, ob sie noch so da sind – fehlt eine Datei oder hat sich die Installationsdatei geändert, wird der Lauf unterbrochen, bis jemand im Paket „Neuen Stand übernehmen“ wählt.'),
    h('div.form',
      field('Vollprüfung', h('label.chk', c('integrity_full_enabled'), 'jede Datei einmal pro Woche komplett lesen (Fingerabdruck)'),
        'Findet auch Dateien, die mit gleicher Größe und gleichem Datum ausgetauscht wurden. Liest jede benutzte Datei einmal über das Netz – Last auf dem Dateiserver etwa wie ein einmaliges Kopieren der Paketordner. Endet das Zeitfenster, geht es in der nächsten Nacht weiter.'),
      field('Zeitfenster', h('div.row', { style: { gap: '6px', alignItems: 'center' } }, n('integrity_full_start', { min: 0, max: 23, style: { maxWidth: '80px' } }),
        h('span', tr('bis')), n('integrity_full_end', { min: 0, max: 24, style: { maxWidth: '80px' } }), h('span', tr('Uhr'))), 'Stunden, z. B. 1 bis 6 Uhr. Über Mitternacht geht auch (22 bis 5).'),
      field('Höchstens (MB/s)', n('integrity_full_mbps', { min: 5, max: 1000 }), 'Drossel, damit der Dateiserver nachts nicht ausgelastet wird. Gilt auch für das Nachrechnen geänderter Dateien.'))));
  // 2.27 Repository-Ansicht: Grenzen für das Hochladen
  const upCard = h('div.card', h('div.card-h', h('h2', 'Hochladen ins Repository')), h('div.card-b',
    h('p.muted.small', { style: { marginTop: 0 } }, 'In der Ansicht „Repository“ laden Hauptadmins und OU-Admins Dateien hoch – geschrieben wird mit dem Repository-Konto, das dafür Schreibrechte auf der Freigabe braucht. Die Daten laufen über diesen Server zum Dateiserver.'),
    h('div.form',
      field('Größte Datei (MB)', n('repo_upload_max_mb', { min: 1, max: 102400 }), 'Gilt für alle.'),
      field('Kontingent je Mandant (MB)', n('repo_tenant_quota_mb', { min: 0 }), 'Standard für Mandanten ohne eigenes Kontingent; 0 = unbegrenzt. Gilt für Uploads von OU-Admins.'),
      field('Dateitypen für OU-Admins', t('repo_upload_types', { style: { width: '100%' } }), 'Endungen ohne Punkt, durch Komma getrennt. Hauptadmins dürfen alles hochladen.'))));
  const exclInfo = h('p.muted', 'Wird geladen …');
  GET('/api/software/exclusions').then((d) => exclInfo.replaceChildren(d.rules.length
    ? `${plural(d.rules.length, 'Ausnahme', 'Ausnahmen')}: ${d.rules.slice(0, 6).map((r) => r.pattern).join(', ')}${d.rules.length > 6 ? ' …' : ''}`
    : 'Noch keine Ausnahmen.')).catch(() => {});
  const exclCard = h('div.card', h('div.card-h', h('h2', 'Ausnahmen der Aktualitätsprüfung'), btn('Liste bearbeiten …', () => exclusionsDlg(), { cls: 'small', icon: 'list' })),
    h('div.card-b', h('p.muted.small', { style: { marginTop: 0 } }, 'Eigene Liste von Programmen, die nie als „veraltet“ gelten – z. B. Laufzeitbibliotheken oder Fachanwendungen mit vorgegebener Version. Muster mit * und ?, nach Name, Hersteller oder winget-ID.'), exclInfo));
  // Bereiche wie in einer Systemsteuerung: links die Liste, rechts die Einstellungen des Bereichs
  const SECTIONS = [
    ['conn', 'Verbindung & Konten', 'net', [conn, accountsCard(), authCard(sys)], false],
    ['ad', 'Active Directory', 'ad', [adCard()], false],
    ['sched', 'Zeitpläne & Leistung', 'clock', [sched, toastCard, rebootCard], true],
    ['dns', 'Namensauflösung', 'net', [dnsCard], true],
    ['software', 'Repository & winget', 'box', [repo, upCard, integCard, wg, proxyCard(), exclCard], true],
    ['remote', 'Fernsitzungen', 'monitor', [rdCard], true],
    ['tls', 'Zertifikat (HTTPS)', 'shield', [tlsCard()], false],
    ['backup', 'Sicherung', 'download', [bkCard], true],
    ['mail', 'E-Mail & Benachrichtigungen', 'mail', [mailCard(), alertsCard()], false],
    ['inventory', 'Inventar & Felder', 'db', [scCard, fCard], false],
    ['tenants', 'Mandanten', 'user', [tenantsCard()], false],
    ['maint', 'Wartung', 'gear', [maint, updatesCard(), diagCard], false],
  ];
  const cur = (() => { const k = parseHash().params.get('s'); return SECTIONS.some((x) => x[0] === k) ? k : 'conn'; })();
  const panes = {};
  const navItems = {};
  const showSec = (k) => {
    for (const [key] of SECTIONS) {
      panes[key].style.display = key === k ? '' : 'none';
      navItems[key].classList.toggle('on', key === k);
    }
    try { history.replaceState(null, '', `#/settings?s=${k}`); } catch (e) { /* egal */ }
  };
  const nav = h('nav.set-nav', SECTIONS.map(([k, label, ic]) => (navItems[k] = h('a', { href: '#', onclick: (e) => { e.preventDefault(); showSec(k); } }, icon(ic), h('span', label)))));
  const body = h('div.set-body', SECTIONS.map(([k, , , cards, hasSave]) => (panes[k] = h('div.set-pane', ...cards,
    hasSave ? h('div.row.end', btn('Speichern', () => save.click(), { cls: 'primary' })) : null))));
  setTop('Einstellungen', 'Verbindung, Active Directory, Zeitpläne, Repository und winget');
  setContent(h('div', guardBanner(sys.guard), h('div.set-layout', nav, body)));
  showSec(cur);
}

function scannerDlg(sc, meta) {
  const isNew = !sc;
  const inProf = new Set(meta ? meta.profiles.filter((p) => sc && !p.builtin_key && p.scanners.includes(sc.id)).map((p) => p.id) : []);
  sc = sc || { name: '', type: 'powershell', target: '', extra: {}, enabled: 1 };
  const ownProfiles = meta ? meta.profiles.filter((p) => !p.builtin_key) : [];
  const fixedLine = h('div.muted.small');
  const profBox = meta ? h('div', fixedLine, ownProfiles.map((p) => h('label.chk', { style: { display: 'flex', padding: '2px 0' } },
    h('input', { type: 'checkbox', checked: inProf.has(p.id), onchange: (e) => (e.target.checked ? inProf.add(p.id) : inProf.delete(p.id)) }), p.name))) : null;
  const name = h('input', { value: sc.name });
  const type = sel([['registry', 'Registry'], ['files', 'Dateien & Ordner'], ['wmi', 'WMI'], ['powershell', 'PowerShell']], sc.type);
  const target = h('textarea', { rows: 4, value: sc.target });
  const ns = h('input', { value: (sc.extra || {}).namespace || 'root\\cimv2' });
  const rec = h('input', { type: 'checkbox', checked: !!(sc.extra || {}).recurse });
  const en = h('input', { type: 'checkbox', checked: !!sc.enabled });
  const bMode = sel([['auto', 'automatisch erkennen'], ['list', 'nur diese Spalten'], ['off', 'nie umrechnen']], (sc.extra || {}).bytes_mode || 'auto');
  const bCols = h('input', { value: (sc.extra || {}).bytes_cols || '', placeholder: 'z. B. Size, FreeSpace', style: { flex: 1 } });
  const bSync = () => { bCols.style.display = bMode.value === 'list' ? '' : 'none'; };
  bMode.addEventListener('change', bSync); bSync();
  const bytesOpt = () => ({ bytes_mode: bMode.value, bytes_cols: bCols.value });
  const hint = h('div.hint');
  const nsRow = field('WMI-Namespace', ns);
  const recRow = field('Unterordner', h('label.chk', rec, 'rekursiv durchsuchen'));
  const HINTS = {
    registry: ['Registry-Pfad', 'z. B. HKLM:\\SOFTWARE\\Firma\\Programm – alle Werte des Schlüssels werden gelesen. Platzhalter * sind erlaubt.'],
    files: ['Pfad', 'z. B. C:\\Program Files\\Firma\\*.exe oder %SYSTEMDRIVE%\\Users\\*\\ntuser.dat – liefert Größe, Datum und Dateiversion (max. 500 Einträge). Umgebungsvariablen (%ProgramData%, $env:SystemDrive) werden auf dem Client aufgelöst. * = beliebiger Name auf einer Ebene, ** = beliebig viele Ordnerebenen (durchsucht alles darunter – langsamer).'],
    wmi: ['WQL-Abfrage', 'z. B. SELECT * FROM Win32_Battery'],
    powershell: ['PowerShell-Code', 'Läuft beim Scan als Administrator auf dem Client. Jedes ausgegebene Objekt wird eine Zeile, seine Eigenschaften die Spalten (max. 500). Texte, Write-Host, Hashtables und Format-Table werden ebenfalls übernommen, Fehler erscheinen in der Spalte „fehler“. Beispiel: Get-ItemProperty HKLM:\\SOFTWARE\\Firma\\App | Select-Object Version, Pfad'],
  };
  const tLabel = h('label');
  const toggle = () => {
    tLabel.textContent = HINTS[type.value][0];
    hint.textContent = HINTS[type.value][1];
    nsRow.forEach((x) => x && (x.style.display = type.value === 'wmi' ? '' : 'none'));
    const fp = meta && meta.profiles.find((p) => p.builtin_key === type.value);
    fixedLine.textContent = fp ? `Immer enthalten im festen Profil „${fp.name}“.${ownProfiles.length ? ' Zusätzlich in eigenen Profilen:' : ''}` : '';
    recRow.forEach((x) => x && (x.style.display = ['registry', 'files'].includes(type.value) ? '' : 'none'));
  };
  type.addEventListener('change', toggle);
  // Sofort testen: Scanner auf einem Computer ausführen und Ergebnis zeigen
  const testPc = h('select', h('option', { value: '' }, '— Computer wählen —'));
  GET('/api/computers').then((list) => list.filter((c) => c.online).concat(list.filter((c) => !c.online))
    .forEach((c) => testPc.appendChild(h('option', { value: c.id }, c.name + (c.online ? '' : ' (offline)'))))).catch(() => {});
  const testOut = h('div');
  const testBtn = btn('Testen', async () => {
    if (!testPc.value) { toast('Bitte einen Computer wählen', 'err'); return; }
    testBtn.disabled = true; testOut.replaceChildren(h('p.muted', 'Wird auf dem Computer ausgeführt …'));
    try {
      const r = await POST('/api/scanners/test', { id: sc.id, computer_id: Number(testPc.value), name: name.value || 'Test', type: type.value, target: target.value, namespace: ns.value, recurse: rec.checked });
      const t = table(customCols(r.rows, bytesOpt()), r.rows, { fill: false, search: false, columns: false, key: null, empty: 'Keine Ausgabe – der Code hat nichts zurückgegeben.' });
      t.el.querySelector('.tbl-scroll').style.maxHeight = '260px';
      testOut.replaceChildren(h('p.muted.small', `${plural(r.rows.length, 'Zeile', 'Zeilen')} von ${r.computer} in ${(r.ms / 1000).toLocaleString('de-DE', { maximumFractionDigits: 1 })} s` + (r.saved ? ' – beim Computer eingetragen.' : ' – nach dem Speichern wird das Ergebnis beim Scannen eingetragen.')), t.el);
    } catch (e) { testOut.replaceChildren(h('p', h('span.badge.err', 'Fehler'), ' ', e.message)); }
    finally { testBtn.disabled = false; }
  }, { cls: 'small', icon: 'refresh' });
  const form = h('div.form', field('Name', name), field('Typ', type), tLabel, target, hint, nsRow, recRow, field('Größenangaben', h('div.row', bMode, bCols), 'Werte in Bytes werden als KB/MB/GB/TB angezeigt (Tooltip: genauer Wert). Automatisch: Spalten wie Size, Length, FreeSpace, Größe mit reinen Zahlen. Gespeichert und exportiert (CSV) wird weiterhin der Bytewert.'),
    field('Aktiv', h('label.chk', en, 'beim nächsten Scan ausführen')), profBox ? field('In Scanprofilen', profBox) : null,
    field('Testen', h('div.row', testPc, testBtn), 'Führt den Scanner sofort auf dem gewählten Computer aus.'), h('div.full', testOut));
  toggle();
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Scanner „${sc.name}“ und seine Ergebnisse löschen?`, 'Löschen', true))) return false; await DEL(`/api/scanners/${sc.id}`); toast('Gelöscht', 'ok'); route(); } });
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    const body = { name: name.value, type: type.value, target: target.value, namespace: ns.value, recurse: rec.checked, enabled: en.checked, ...bytesOpt() };
    if (meta) body.profiles = [...inProf];
    if (isNew) await POST('/api/scanners', body); else await PUT(`/api/scanners/${sc.id}`, body);
    toast(`Gespeichert – läuft mit dem Profil „${({ registry: 'Registry', files: 'Dateien & Ordner', wmi: 'WMI', powershell: 'PowerShell' })[type.value]}“`, 'ok'); route();
  } });
  modal(isNew ? 'Neuer Scanner' : `Scanner: ${sc.name}`, form, buttons, { wide: true, help: 'customscanners' });
}

// Anmeldeverfahren WinRM/SMB: NTLM, Negotiate oder nur Kerberos – mit Schritt-für-Schritt-Test
function authCard(sys) {
  const k = sys.kerberos;
  const envLabel = (k.modes.find((m) => m.key === sys.winrm.env_auth) || { label: sys.winrm.env_auth }).label;
  const modeSel = sel([['', `wie .env (PK_WINRM_AUTH: ${envLabel})`], ...k.modes.map((m) => [m.key, m.label])], sys.winrm.set_mode || '');
  const pc = h('select', h('option', { value: '' }, '— Computer wählen —'));
  GET('/api/computers').then((list) => list.filter((c) => c.online).concat(list.filter((c) => !c.online))
    .forEach((c) => pc.appendChild(h('option', { value: c.id }, c.name + (c.online ? '' : ' (offline)'))))).catch(() => {});
  const tMode = sel([['kerberos', 'nur Kerberos (ohne NTLM-Rückfall)'], ['negotiate', 'Negotiate']], 'kerberos');
  const out = h('div');
  const testBtn = btn('Testen', async () => {
    testBtn.disabled = true; out.replaceChildren(h('p.muted', 'Wird geprüft …'));
    try {
      const r = await POST('/api/system/kerberos/test', { computer_id: pc.value ? Number(pc.value) : null, mode: tMode.value });
      out.replaceChildren(h('div.row', { style: { gap: '8px', margin: '8px 0' } }, r.ok ? h('span.badge.ok', 'Kerberos funktioniert') : h('span.badge.err', 'Kerberos noch nicht möglich'),
        r.auth_type ? h('span.muted.small', `Der Client meldet: Anmeldung per ${r.auth_type}`) : null),
      h('table', h('tbody', r.steps.map((x) => h('tr', h('td', x.ok ? h('span.badge.ok', 'OK') : h('span.badge.err', 'Fehler')),
        h('td', h('b', x.step)), h('td.wrap', x.detail, x.hint ? h('div.hint', x.hint) : null), h('td.right.muted', `${x.ms} ms`))))),
      ...(!pc.value ? [h('p.muted.small', 'Ohne Computer werden nur Bibliotheken, Konfiguration, KDC und das Dienstkonto geprüft.')] : []));
    } catch (e) { out.replaceChildren(h('p', h('span.badge.err', 'Fehler'), ' ', e.message)); }
    finally { testBtn.disabled = false; }
  }, { cls: 'small', icon: 'refresh' });
  const save = btn('Speichern', async () => {
    if (modeSel.value === 'kerberos' && !(await confirmDlg('Auf „Nur Kerberos“ umstellen? Computer, die nur per IP-Adresse oder ohne gültigen DNS-Namen bekannt sind, sind danach nicht mehr erreichbar. Vorher mit „Testen“ prüfen.', 'Umstellen'))) return;
    await run(() => PUT('/api/settings', { auth_mode: modeSel.value }), 'Anmeldeverfahren gespeichert');
    route();
  }, { cls: 'primary small' });
  return h('div.card', h('div.card-h', h('h2', 'Anmeldeverfahren (NTLM / Kerberos)')), h('div.card-b',
    kv([['Aktiv', h('b', (k.modes.find((m) => m.key === sys.winrm.auth) || { label: sys.winrm.auth }).label)],
      ['Kerberos-Bibliotheken', k.available ? `vorhanden (gssapi ${k.version})` : h('span.badge.warn', 'fehlen – Image neu bauen')],
      ['Realm', k.realm || '—'], ['Dienstkonto als', h('span.mono', k.principal || '—')],
      ['KDC', k.kdc.length ? k.kdc.join(', ') + ' (PK_KDC)' : 'automatisch über DNS'], ['krb5.conf', h('span.mono', k.conf)]]),
    h('div.form', { style: { marginTop: '10px' } }, field('Verfahren für WinRM & SMB', h('div.row', modeSel, save),
      'NTLM ist von Microsoft abgekündigt. Empfohlener Weg: erst „Testen“, dann „Negotiate“ (Kerberos mit NTLM-Rückfall) und nach einer Weile ohne Auffälligkeiten „Nur Kerberos“. LDAPS ist nicht betroffen (einfache Anmeldung über TLS).'),
    field('Test', h('div.row', pc, tMode, testBtn), 'Holt ein Ticket für das Dienstkonto, Diensttickets für den Computer und meldet sich per WinRM und SMB mit dem gewählten Verfahren an – unabhängig von der aktiven Einstellung. Der Client zeigt, ob wirklich Kerberos verwendet wurde.'),
    h('div.full', out))));
}

function repoTestDlg(r) {
  const rows = r.steps.map((x) => h('tr', h('td', x.ok ? h('span.badge.ok', 'OK') : h('span.badge.err', 'Fehler')),
    h('td', h('b', x.step)), h('td.wrap', x.detail), h('td.right.muted', `${x.ms} ms`)));
  modal(`Repository-Test: ${r.ok ? 'erfolgreich' : 'fehlgeschlagen'}`, h('div',
    h('p.mono.muted', r.path), h('table', h('tbody', rows)),
    r.info && r.info.encryption === false ? h('p.muted.small', 'Hinweis: Die Verbindung ist signiert, aber nicht verschlüsselt. Für Installationsdateien unkritisch; SMB-Verschlüsselung kann auf der Freigabe aktiviert werden.') : null),
  [{ label: 'Schließen' }], { wide: true });
}

function guardBanner(g) {
  if (!g || !g.paused) return null;
  return h('div.card.guard', h('div.card-b',
    h('div.row', h('span.badge.err', 'Dienstkonto-Schutz aktiv'), h('b', g.reason)),
    h('p.muted', `Seit ${fmtDate(g.since)} (${g.source}) sind alle automatischen Scans, Bereitstellungen und AD-Abgleiche pausiert, `,
      'damit keine weiteren Fehlanmeldungen das Konto (erneut) sperren. Beheben Sie die Ursache im AD (Konto entsperren, Passwort in der .env prüfen) ',
      `und klicken Sie dann auf „Prüfen & fortsetzen“. Ohne Eingriff wird es um ${fmtDate(g.until)} erneut versucht.`),
    btn('Prüfen & fortsetzen', async () => {
      const r = await run(() => POST('/api/system/guard/reset'));
      if (r) { toast(r.ok ? 'Dienstkonto in Ordnung – Zugriffe laufen wieder' : 'Dienstkonto weiterhin abgelehnt', r.ok ? 'ok' : 'err'); route(); }
    }, { cls: 'primary' })));
}

function localStorageGet(k) { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } }
function setTheme(v) {
  try { if (v) localStorage.setItem('pk-theme', v); else localStorage.removeItem('pk-theme'); } catch (e) { /* egal */ }
  if (v) document.documentElement.setAttribute('data-theme', v); else document.documentElement.removeAttribute('data-theme');
  document.querySelectorAll('.theme-btn').forEach((b) => b._upd && b._upd());
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------
const BUILTIN_TOOLS = [
  { key: 'ping', name: 'Ping & Ports', icon: 'net' },
  { key: 'rdpweb', name: 'Remotedesktop im Browser', icon: 'monitor' },
  { key: 'vncweb', name: 'VNC im Browser', icon: 'monitor' },
  { key: 'wake', name: 'Wake-on-LAN', icon: 'play' },
  { key: 'restart', name: 'Neustart (60 s)', icon: 'refresh', confirm: true, action: true },
  { key: 'shutdown', name: 'Herunterfahren (60 s)', icon: 'x', confirm: true, action: true },
  { key: 'abort', name: 'Neustart/Herunterfahren abbrechen', icon: 'x', action: true },
  { key: 'gpupdate', name: 'gpupdate /force', icon: 'refresh', action: true },
  { key: 'winget', name: 'winget prüfen / nachinstallieren', icon: 'box' },
  { key: 'scan', name: 'Scannen mit Profil …', icon: 'refresh' },
  { key: 'diskusage', name: 'Speicherplatz analysieren …', icon: 'db' },
  { key: 'files', name: 'Dateien (Explorer) …', icon: 'folder', view: 'files' },
  { key: 'eventlog', name: 'Ereignisanzeige …', icon: 'log', view: 'eventlog' },
];
let openMenu = null;
function closeMenu() { if (openMenu) { openMenu.remove(); openMenu = null; document.removeEventListener('mousedown', menuOutside, true); document.removeEventListener('keydown', menuKey, true); } }
function menuKey(e) { if (e.key === 'Escape' && openMenu) { e.stopPropagation(); e.preventDefault(); closeMenu(); } }
function menuOutside(e) { if (openMenu && !openMenu.contains(e.target)) closeMenu(); }

async function toolsMenu(anchor, targets) {
  if (openMenu) { closeMenu(); return; }
  if (!targets.length) { toast('Bitte zuerst Computer auswählen', 'err'); return; }
  if (S.menuLoading) return; // Doppelklick: nicht zwei Menüs öffnen
  S.menuLoading = true;
  let data;
  try { data = await GET('/api/tools'); } finally { S.menuLoading = false; }
  const q = h('input', { type: 'search', placeholder: 'Tool suchen …' });
  const list = h('div.tmenu-list');
  const item = (label, ic, onClick, sub) => h('button.tmenu-item', { type: 'button', onclick: () => { closeMenu(); onClick(); } },
    h('span.ic', { 'data-ic': ic }, icon(ic)), h('span.lbl', label), sub ? h('span.muted.small', sub) : null);
  const folders = {};
  for (const t of data.tools) (folders[t.folder || ''] = folders[t.folder || ''] || []).push(t);
  const open = new Set();
  function draw() {
    const f = q.value.trim().toLowerCase();
    const els = [];
    const bi = BUILTIN_TOOLS.filter((b) => !f || b.name.toLowerCase().includes(f));
    bi.forEach((b) => els.push(item(b.name, b.icon, () => runBuiltin(b, targets))));
    if (bi.length) els.push(h('div.tmenu-sep'));
    for (const name of Object.keys(folders).sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a.localeCompare(b, 'de')))) {
      const tools = folders[name].filter((t) => !f || t.name.toLowerCase().includes(f) || name.toLowerCase().includes(f));
      if (!tools.length) continue;
      const isOpen = f || open.has(name) || name === '';
      if (name) {
        els.push(h('button.tmenu-folder', { type: 'button', onclick: () => { if (open.has(name)) open.delete(name); else open.add(name); draw(); } },
          h('span.ic', { 'data-ic': 'folder' }, icon('folder')), h('span.lbl', name), h('span.muted', isOpen ? '▾' : '▸')));
      }
      if (isOpen) {
        tools.forEach((t) => els.push(h('div', { class: name ? 'tmenu-indent' : '' },
          item(t.name, t.type === 'url' ? 'net' : t.type === 'cmd' ? 'terminal' : 'terminal', () => runTool(t, targets), t.run_as === 'system' ? 'SYSTEM' : ''))));
      }
    }
    els.push(h('div.tmenu-sep'), h('button.tmenu-item', { type: 'button', onclick: () => { closeMenu(); go('#/tools'); } },
      h('span.ic', { 'data-ic': 'gear' }, icon('gear')), h('span.lbl', 'Tools verwalten …')));
    list.replaceChildren(...els);
  }
  q.addEventListener('input', draw);
  draw();
  const r = anchor.getBoundingClientRect();
  const panel = h('div.tmenu', h('div.tmenu-head', q, h('span.muted.small', targets.length === 1 ? targets[0].name : `${targets.length} Computer`)), list);
  panel.style.top = `${r.bottom + 4}px`;
  panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 360))}px`;
  document.body.appendChild(panel);
  openMenu = panel;
  setTimeout(() => { document.addEventListener('mousedown', menuOutside, true); document.addEventListener('keydown', menuKey, true); q.focus(); }, 0);
}

// Fernsitzung in eigenem Tab öffnen (Verbindungsdialog erscheint dort)
function openRemote(targets, proto) {
  if (targets.length > 6) { toast('Bitte höchstens 6 Computer gleichzeitig öffnen', 'err'); return; }
  targets.forEach((t) => {
    const w = window.open(`/remote#c=${t.id}&p=${proto}`, `pk-${proto}-${t.id}`);
    if (!w) toast('Der Browser hat das neue Fenster blockiert – Pop-ups für POCKET KNIFE erlauben.', 'err');
  });
}

async function runBuiltin(b, targets) {
  if (b.view) {
    if (targets.length > 1) toast(`${b.name} läuft je Computer – geöffnet für ${targets[0].name}`, 'ok');
    go(`#/${b.view}/${targets[0].id}`);
    return;
  }
  if (b.key === 'rdpweb' || b.key === 'vncweb') { openRemote(targets, b.key === 'vncweb' ? 'vnc' : 'rdp'); return; }
  if (b.key === 'scan') { await scanProfileDlg(targets.map((t) => t.id)); return; }
  if (b.key === 'diskusage') {
    if (targets.length > 1) toast('Die Speicherplatz-Analyse läuft je Computer – geöffnet für ' + targets[0].name, 'ok');
    go(`#/diskusage/${targets[0].id}`);
    return;
  }
  if (b.key === 'winget') {
    const r = await run(() => POST('/api/deployments', { winget_bootstrap: 1, computer_ids: targets.map((t) => t.id) }), 'winget-Prüfung gestartet');
    if (r) go(`#/deployment/${r.id}`);
    return;
  }
  if (b.confirm && !(await confirmDlg(`„${b.name}“ auf ${targets.length === 1 ? targets[0].name : targets.length + ' Computern'} ausführen?`, 'Ausführen', true))) return;
  const exec = b.action
    ? (t) => POST(`/api/computers/${t.id}/action`, { action: b.key }).then((r) => ({ exit: 0, output: r.output || 'Ausgeführt.' }))
    : (t) => POST(`/api/tools/builtin/${b.key}`, { computer_id: t.id });
  runDialog(b.name, targets, exec);
}

async function runTool(t, targets) {
  if (t.type === 'url') {
    for (const c of targets.slice(0, 10)) {
      const r = await run(() => POST(`/api/tools/${t.id}/run`, { computer_id: c.id }));
      if (r && r.url) window.open(r.url, '_blank', 'noopener');
    }
    return;
  }
  if (t.confirm && !(await confirmDlg(`Tool „${t.name}“ auf ${targets.length === 1 ? targets[0].name : targets.length + ' Computern'} ausführen?`, 'Ausführen', true))) return;
  // Läuft als Job auf dem Server weiter, auch wenn dieses Fenster geschlossen wird
  const r = await run(() => POST(`/api/tools/${t.id}/jobs`, { ids: targets.map((x) => x.id) }));
  if (r) jobDialog(t.name + (t.run_as === 'system' ? ' (als SYSTEM)' : ''), r.job);
}

function jobDialog(title, jid) {
  const body = h('div');
  let closed = false;
  let last = null;
  const badge = (r) => h('span.badge', { class: { OK: 'ok', Fehler: 'err', läuft: 'info' }[r.status] || '' },
    r.status === 'Fehler' && r.res && r.res.exit ? `Exitcode ${r.res.exit}` : r.status);
  const secs = (r) => (r.res && r.res.seconds !== undefined ? h('span.muted.small', `${String(r.res.seconds).replace('.', ',')} s`) : null);
  const openSet = new Set();
  function draw(j) {
    last = j;
    const rows = j.targets;
    const done = rows.filter((r) => r.res).length;
    if (rows.length === 1) {
      const r = rows[0];
      body.replaceChildren(h('div.row', { style: { marginBottom: '8px' } }, h('b', r.name), badge(r), secs(r)),
        h('pre.out', r.res ? (r.res.output || '(keine Ausgabe)') : 'Wird ausgeführt …'));
      return;
    }
    body.replaceChildren(h('div.row', { style: { marginBottom: '8px' } }, h('span', `${done} von ${rows.length} fertig`),
      progress(rows.filter((r) => r.status === 'OK').length, rows.filter((r) => r.status === 'Fehler').length, rows.length),
      h('span.muted.small', 'Läuft auf dem Server weiter, auch wenn Sie dieses Fenster schließen.')),
    h('div.run-list', rows.map((r) => {
      const d = h('details', { open: openSet.has(r.id) || (rows.length <= 3 && !!r.res) },
        h('summary', h('b', r.name), badge(r), secs(r)),
        h('pre.out', r.res ? (r.res.output || '(keine Ausgabe)') : (r.status === 'läuft' ? 'Wird ausgeführt …' : 'Wartet …')));
      d.addEventListener('toggle', () => { if (d.open) openSet.add(r.id); else openSet.delete(r.id); });
      return d;
    })));
  }
  const copyAll = () => {
    const txt = ((last && last.targets) || []).map((r) => `=== ${r.name} (${r.status}) ===\n${(r.res && r.res.output) || ''}`).join('\n\n');
    navigator.clipboard.writeText(txt).then(() => toast('Ausgabe kopiert', 'ok'), () => toast('Kopieren nicht möglich', 'err'));
  };
  body.append(h('div.muted', 'Wird gestartet …'));
  modal(title, body, [{ label: 'Ausgabe kopieren', onClick: () => { copyAll(); return false; } }, { label: 'Schließen' }],
    { wide: true, onClose: () => { closed = true; } });
  const poll = async () => {
    if (closed) return;
    try {
      const j = await GET(`/api/tooljobs/${jid}`);
      draw(j);
      if (j.targets.some((r) => !r.res)) setTimeout(poll, 1500);
    } catch (e) { body.replaceChildren(h('div.empty', e.message)); }
  };
  poll();
}

function runDialog(title, targets, exec) {
  const rows = targets.map((c) => ({ c, status: 'wartet', res: null }));
  const body = h('div');
  const single = rows.length === 1;
  let closed = false;
  function draw() {
    if (single) {
      const r = rows[0];
      body.replaceChildren(h('div.row', { style: { marginBottom: '8px' } }, h('b.raw', r.c.name), statusBadge(r),
        r.res && r.res.seconds !== undefined ? h('span.muted.small', `${String(r.res.seconds).replace('.', ',')} s`) : null),
      h('pre.out', r.status === 'läuft' || r.status === 'wartet' ? 'Wird ausgeführt …' : (r.res && r.res.output) || '(keine Ausgabe)'));
      return;
    }
    const done = rows.filter((r) => r.res).length;
    body.replaceChildren(h('div.row', { style: { marginBottom: '8px' } }, h('span', `${done} von ${rows.length} fertig`), progress(rows.filter((r) => r.status === 'OK').length, rows.filter((r) => r.status === 'Fehler').length, rows.length)),
      h('div.run-list', rows.map((r) => h('details', { open: rows.length <= 3 && !!r.res },
        h('summary', h('b.raw', r.c.name), statusBadge(r), r.res && r.res.seconds !== undefined ? h('span.muted.small', `${String(r.res.seconds).replace('.', ',')} s`) : null),
        h('pre.out', (r.res && r.res.output) || (r.status === 'läuft' ? 'Wird ausgeführt …' : 'Wartet …'))))));
  }
  function statusBadge(r) {
    const cls = { OK: 'ok', Fehler: 'err', läuft: 'info' }[r.status] || '';
    return h('span.badge', { class: cls }, r.status === 'Fehler' && r.res && r.res.exit ? `Exitcode ${r.res.exit}` : r.status);
  }
  const copyAll = () => {
    const txt = rows.map((r) => `=== ${r.c.name} (${r.status}) ===\n${(r.res && r.res.output) || ''}`).join('\n\n');
    navigator.clipboard.writeText(txt).then(() => toast('Ausgabe kopiert', 'ok'), () => toast('Kopieren nicht möglich', 'err'));
  };
  modal(title, body, [{ label: 'Ausgabe kopieren', onClick: () => { copyAll(); return false; } }, { label: 'Schließen' }],
    { wide: true, onClose: () => { closed = true; } });
  draw();
  let idx = 0;
  const worker = async () => {
    while (idx < rows.length && !closed) {
      const r = rows[idx++];
      r.status = 'läuft'; draw();
      try {
        r.res = await exec(r.c);
        r.status = r.res.exit ? 'Fehler' : 'OK';
        if (r.res.exit && r.res.output === undefined) r.res.output = '';
      } catch (e) { r.res = { output: e.message, exit: 1 }; r.status = 'Fehler'; }
      if (!closed) draw();
    }
  };
  for (let i = 0; i < Math.min(4, rows.length); i++) worker();
}

// --- Verwaltung -----------------------------------------------------------------
async function viewTools() {
  setTop('Tools', 'Befehle für einzelne oder mehrere Computer – aufrufbar über „Tools ▾“ oder per Rechtsklick auf Computer',
    btn('Neuer Ordner', async () => {
      const n = (prompt('Name des neuen Ordners') || '').trim();
      if (n) { toolDlg(null, n.toUpperCase()); }
    }, { icon: 'folder' }),
    isOU() ? null : btn('Neues Tool', () => toolDlg(null), { icon: 'plus', cls: 'primary' }));
  const data = await GET('/api/tools');
  const q = h('input', { type: 'search', placeholder: 'Filtern …' });
  const box = h('div.ttree');
  let closed = new Set();
  try { closed = new Set(JSON.parse(localStorage.getItem('pk-tools-closed') || '[]')); } catch (e) { /* egal */ }
  const saveClosed = () => { try { localStorage.setItem('pk-tools-closed', JSON.stringify([...closed])); } catch (e) { /* egal */ } };
  const typeIcon = (t) => (t.type === 'url' ? 'net' : 'terminal');
  const row = (ic, name, type, runas, desc, onClick, cls = '') => h('div.trow', { class: cls, onclick: onClick, ondblclick: onClick },
    h('span.tname', h('span.ic', { 'data-ic': ic }, icon(ic)), h('span', name)), h('span.muted', type), h('span.muted', runas), h('span.muted.trunc', desc));
  function draw() {
    const f = q.value.trim().toLowerCase();
    const els = [h('div.trow.thead', h('span', 'Name'), h('span', 'Typ'), h('span', 'Ausführung'), h('span', 'Beschreibung'))];
    for (const b of BUILTIN_TOOLS) {
      if (f && !b.name.toLowerCase().includes(f)) continue;
      els.push(row(b.icon, b.name, 'eingebaut', b.key.endsWith('web') || b.view ? 'im Browser' : b.action ? 'Dienstkonto' : 'POCKET KNIFE', BUILTIN_DESC[b.key] || '', null, 'builtin'));
    }
    els.push(h('div.tsep'));
    const folders = {};
    data.tools.forEach((t) => (folders[t.folder || ''] = folders[t.folder || ''] || []).push(t));
    const names = Object.keys(folders).sort((x, y) => (x === '' ? 1 : y === '' ? -1 : x.localeCompare(y, 'de')));
    for (const fn of names) {
      const list = folders[fn].filter((t) => !f || t.name.toLowerCase().includes(f) || fn.toLowerCase().includes(f) || (t.description || '').toLowerCase().includes(f));
      if (!list.length) continue;
      const open = f || !closed.has(fn);
      if (fn) {
        els.push(h('div.trow.tfolder', { onclick: () => { if (closed.has(fn)) closed.delete(fn); else closed.add(fn); saveClosed(); draw(); } },
          h('span.tname', h('span.caret', open ? '▾' : '▸'), h('span.ic', { 'data-ic': 'folder' }, icon('folder')), h('b', fn), h('span.muted.small', ` (${list.length})`)), h('span'), h('span'), h('span')));
      }
      if (open) {
        list.forEach((t) => els.push(row(typeIcon(t), t.name, data.types[t.type], t.type === 'url' ? 'im Browser' : data.run_as[t.run_as],
          t.description || t.script.split('\n')[0], () => toolDlg(t), fn ? 'tchild' : '')));
      }
    }
    box.replaceChildren(...els);
  }
  q.addEventListener('input', draw);
  draw();
  const help = h('p.muted.small', { style: { margin: '8px 2px 0' } }, 'Variablen in Befehlen: ', ['$(Computer)', '$(Host)', '$(IP)', '$(MAC)', '$(User)', '$(Domain)'].map((v) => h('code', { style: { marginRight: '8px' } }, v)),
    '– werden vor dem Ausführen durch die Werte des jeweiligen Computers ersetzt. Klick auf ein eigenes Tool öffnet es zum Bearbeiten.');
  setContent(h('div.grid', h('div.card', h('div.card-b', h('div.row', { style: { marginBottom: '8px' } }, q), box, help))));
}
const BUILTIN_DESC = {
  ping: 'DNS, ICMP und Ports (WinRM, SMB, RDP, RPC) prüfen', rdpweb: 'RDP-Sitzung im Browser-Tab', vncweb: 'VNC-Sitzung im Browser-Tab',
  wake: 'Magic Packet über einen Computer im selben Subnetz', restart: 'Neustart mit 60 s Vorwarnung',
  shutdown: 'Herunterfahren mit 60 s Vorwarnung', abort: 'geplanten Neustart abbrechen', gpupdate: 'Gruppenrichtlinien neu anwenden', scan: 'Scan mit wählbarem Scanprofil',
  diskusage: 'Ordnergrößen als Ringdiagramm, mit Zoom in die Ebenen',
  files: 'Dateien auf dem Client durchsuchen, kopieren, verschieben, löschen, herunter- und hochladen',
  eventlog: 'Windows-Ereignisprotokolle im Browser ansehen',
};

async function toolDlg(tool, presetFolder) {
  const data = await GET('/api/tools');
  const isNew = !tool;
  tool = tool || { name: '', folder: presetFolder || '', type: 'powershell', script: '', run_as: 'service', confirm: 0, description: '' };
  const name = h('input', { value: tool.name });
  const folders = [...new Set(data.tools.map((x) => x.folder).filter(Boolean))].sort();
  const folder = h('input', { value: tool.folder || '', placeholder: 'z. B. BITLOCKER' });
  const type = sel(Object.entries(data.types), tool.type);
  const runAs = sel(Object.entries(data.run_as), tool.run_as);
  const script = h('textarea.mono', { rows: 12, value: tool.script, spellcheck: 'false' });
  const desc = h('input', { value: tool.description || '' });
  const confirm = h('input', { type: 'checkbox', checked: !!tool.confirm });
  const hint = h('div.hint');
  const runRow = field('Ausführen als', runAs, 'Dienstkonto: direkt über WinRM, schnell. SYSTEM: über eine geplante Aufgabe, z. B. für SFC oder Dinge, die als Computerkonto laufen müssen.');
  const toggle = () => {
    const url = type.value === 'url';
    runRow.forEach((e) => e && (e.style.display = url ? 'none' : ''));
    script.rows = url ? 2 : 12;
    hint.textContent = url ? 'Wird im Browser geöffnet, z. B. vnc://$(IP), https://$(Host):8443 oder http://$(IP):9100'
      : type.value === 'cmd' ? 'Befehle wie in einer .cmd-Datei, mehrzeilig möglich, z. B. ipconfig /all'
        : 'PowerShell-Code. Ausgaben (auch Format-Table) werden angezeigt. Fehler setzen den Status auf „Fehler“.';
  };
  type.addEventListener('change', toggle);
  const form = h('div.form', field('Name', name), field('Ordner', combo(folder, folders, { width: '100%' })),
    field('Typ', type), runRow, h('label', 'Befehl / URL'), script, hint,
    field('Beschreibung', desc), field('Rückfrage', h('label.chk', confirm, 'vor dem Ausführen bestätigen (für ändernde Aktionen)')),
    h('div.full.muted.small', 'Variablen: $(Computer) $(Host) $(IP) $(MAC) $(User) $(Domain)'));
  toggle();
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Tool „${tool.name}“ löschen?`, 'Löschen', true))) return false; await DEL(`/api/tools/${tool.id}`); toast('Gelöscht', 'ok'); route(); } });
    buttons.splice(1, 0, { label: 'Als Kopie speichern', onClick: async () => { await POST('/api/tools', { ...collect(), name: name.value + ' (Kopie)' }); toast('Kopie angelegt', 'ok'); route(); } });
  }
  function collect() {
    return { name: name.value, folder: folder.value, type: type.value, run_as: runAs.value, script: script.value, description: desc.value, confirm: confirm.checked };
  }
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => { if (isNew) await POST('/api/tools', collect()); else await PUT(`/api/tools/${tool.id}`, collect()); toast('Gespeichert', 'ok'); route(); } });
  modal(isNew ? 'Neues Tool' : `Tool: ${tool.name}`, form, buttons, { wide: true, sticky: true });
}

// --- Scanprofile -----------------------------------
const TRIG_UNITS = [['m', 'Minuten'], ['h', 'Stunden'], ['d', 'Tage']];
const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

function profStatus(r) {
  if (r.pending) return h('span.badge.info', 'ausstehend');
  if (r.status === 'OK') return h('span.badge.ok', 'OK');
  if (r.status === 'Fehler') return h('span.badge.err', 'Fehler');
  if (r.status === 'Offline') return h('span.badge', 'offline');
  return h('span.muted', '–');
}

async function viewScanners() {
  setTop('Scanprofile', 'Standard- und eigene Scanner zu Profilen bündeln, auf Sammlungen oder einzelne Computer anwenden und zeitlich steuern',
    btn('Neues Scanprofil', async () => profileDlg(null, await GET('/api/scanprofiles')), { icon: 'plus', cls: 'primary' }));
  const meta = await GET('/api/scanprofiles');
  const colName = (id) => (meta.collections.find((c) => c.id === id) || { name: `#${id}` }).name;
  const t = table([
    { key: 'name', label: 'Name', render: (r) => h('span', h('a', { href: '#', onclick: (e) => { e.preventDefault(); profileDlg(r, meta); } }, r.name),
      r.is_default ? h('span.badge.accent', { style: { marginLeft: '6px' } }, 'Standard') : null, r.enabled ? null : h('span.badge', { style: { marginLeft: '6px' } }, 'deaktiviert')),
      sort: (r) => (r.is_default ? '0' : r.builtin_key ? '1' : '2') + r.name },
    { key: 'kind', label: 'Art', value: (r) => (r.builtin_key ? 'Fest' : 'Eigen') },
    { key: 'scanners_n', label: 'Scanner', value: (r) => r.modules.length + r.scanners.length + (r.scanner_types || []).length,
      title: (r) => r.modules.map((m) => tr((meta.modules.find((x) => x.key === m) || { label: m }).label)).concat(r.scanners.map((s) => (meta.scanners.find((x) => x.id === s) || { name: `#${s}` }).name)).join(', ') },
    { key: 'collections', label: 'Ziele', value: (r) => [...r.collections.map(colName), ...((r.computers || []).length ? [`${r.computers.length} einzelne Computer`] : [])].join(', ') || '— nur manuell —', cls: 'trunc' },
    { key: 'triggers_text', label: 'Auslöser', value: (r) => r.triggers_text.join(' · ') || '— keine —', cls: 'wrap' },
    { key: 'members', label: 'Computer', cls: 'right' },
    { key: 'last_scan', label: 'Letzter Scan', render: (r) => h('span', { title: fmtDate(r.last_scan) }, relTime(r.last_scan)) },
    { key: 'state', label: 'Status', value: (r) => r.failed * 1000 + r.pending, render: (r) => h('span.row', { style: { gap: '4px' } },
      r.pending ? h('span.badge.info', `${r.pending} ausstehend`) : null, r.failed ? h('span.badge.err', `${r.failed} Fehler`) : null,
      !r.pending && !r.failed && r.scanned ? h('span.badge.ok', `${r.scanned} OK`) : null) },
  ], meta.profiles, { noun: 'Profile', sort: 'name', onRow: (r) => profileDlg(r, meta), empty: 'Keine Scanprofile.', fill: false });

  const sc = table([
    { key: 'name', label: 'Name', render: (r) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); scannerDlg(r, meta); } }, r.name) },
    { key: 'type_label', label: 'Typ', render: (r) => h('span.badge.info', r.type_label) },
    { key: 'target', label: 'Ziel / Abfrage', cls: 'trunc mono', title: (r) => r.target },
    { key: 'in', label: 'In Profilen', value: (r) => meta.profiles.filter((p) => p.scanners.includes(r.id) || (p.builtin_key && (p.scanner_types || []).includes(r.type))).map((p) => p.name).join(', ') || '—' },
    { key: 'enabled', label: 'Aktiv', render: (r) => (r.enabled ? h('span.badge.ok', 'aktiv') : h('span.badge', 'aus')) },
  ], meta.scanners, { sort: 'name', noun: 'eigene Scanner', onRow: (r) => scannerDlg(r, meta), empty: 'Noch keine eigenen Scanner.', fill: false });
  sc.actions.append(btn('Neuer eigener Scanner', () => scannerDlg(null, meta), { icon: 'plus', cls: 'small' }));

  const std = h('div.chips', meta.modules.map((m) => h('span.badge', m.label)));
  setContent(h('div.grid',
    h('div.card', h('div.card-h', h('h2', 'Scanprofile')), h('div.card-b', t.el,
      h('p.muted.small', 'Die festen Profile können nicht gelöscht werden; Auslöser und Ziele sind einstellbar. Das Standardprofil wird für „Scannen“ ohne Profilauswahl und für neue Computer verwendet. Ein Computer wird von einem Profil automatisch gescannt, wenn er zu seinen Zielen gehört (Mitglied einer gewählten Sammlung oder einzeln gewählt) und ein Auslöser zutrifft. Neue Profile zielen zunächst auf „Alle Computer“.'))),
    h('div.card', h('div.card-h', h('h2', 'Eigene Scanner')), h('div.card-b', sc.el,
      h('p.muted.small', 'Registry, Dateien & Ordner, WMI und PowerShell. Jeder eigene Scanner läuft im festen Profil seines Typs (z. B. „PowerShell“) und zusätzlich in eigenen Profilen, denen er zugeordnet ist. Das Ergebnis steht beim Computer in der gleichnamigen Kategorie.'))),
    h('div.card', h('div.card-h', h('h2', 'Standard-Scanner')), h('div.card-b', std))));
}

function tabsBox(defs) {
  const bar = h('div.tabs', { style: { padding: '0', marginBottom: '12px' } });
  const body = h('div');
  const show = (i) => {
    bar.querySelectorAll('button').forEach((b, j) => b.classList.toggle('active', i === j));
    body.replaceChildren(defs[i][1]);
  };
  defs.forEach(([label], i) => bar.appendChild(h('button', { type: 'button', onclick: () => show(i) }, label)));
  show(0);
  const el = h('div', bar, body);
  el.setLabel = (i, label) => { if (bar.children[i]) bar.children[i].textContent = tr(label); };
  el.show = show;
  return el;
}

function triggerEditor(list, opts = {}) {
  const box = h('div');
  const ins = (attrs) => h('input', { style: { maxWidth: '90px' }, ...attrs });
  function rowFor(t, idx) {
    const set = (k) => (e) => { t[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; };
    const parts = [];
    const typ = t.type;
    if (typ === 'once') parts.push('am', h('input', { type: 'datetime-local', value: (t.at || '').replace(' ', 'T'), onchange: set('at') }));
    if (typ === 'interval') {
      parts.push('alle', ins({ type: 'number', min: 1, value: t.every || 1, onchange: set('every') }),
        sel(TRIG_UNITS, t.unit || 'h', { onchange: set('unit') }), 'ab', h('input', { type: 'datetime-local', value: (t.start || '').replace(' ', 'T'), onchange: set('start') }));
    }
    if (typ === 'daily') parts.push('alle', ins({ type: 'number', min: 1, value: t.every || 1, onchange: set('every') }), 'Tag(e) um', h('input', { type: 'time', value: t.time || '08:00', onchange: set('time') }));
    if (typ === 'weekly') {
      t.days = t.days || [0];
      parts.push('alle', ins({ type: 'number', min: 1, value: t.every || 1, onchange: set('every') }), 'Woche(n) am',
        ...WEEKDAYS.map((d, i) => h('label.chk', h('input', { type: 'checkbox', checked: t.days.includes(i), onchange: (e) => { t.days = t.days.filter((x) => x !== i); if (e.target.checked) t.days.push(i); } }), d)),
        'um', h('input', { type: 'time', value: t.time || '08:00', onchange: set('time') }));
    }
    if (typ === 'monthly') {
      parts.push('an Tag(en)', h('input', { placeholder: 'z. B. 1, 15, letzter', value: (t.days || [1]).map((d) => (d === 'last' ? 'letzter' : d)).join(', '),
        onchange: (e) => { t.days = e.target.value.split(/[,;\s]+/).filter(Boolean).map((x) => (/^l/i.test(x) ? 'last' : Number(x))); } }),
      'um', h('input', { type: 'time', value: t.time || '08:00', onchange: set('time') }));
    }
    if (typ === 'scan_age') {
      const win = h('span.row', { style: { gap: '6px', display: t.only_at ? '' : 'none' } }, 'von', h('input', { type: 'time', value: t.from || '18:00', onchange: set('from') }),
        'bis', h('input', { type: 'time', value: t.to || '06:00', onchange: set('to') }));
      parts.push('wenn letzter Scan älter als', ins({ type: 'number', min: 0, value: t.days ?? 1, onchange: set('days') }), 'T',
        ins({ type: 'number', min: 0, max: 23, value: t.hours ?? 0, onchange: set('hours') }), 'Std',
        ins({ type: 'number', min: 0, max: 59, value: t.minutes ?? 0, onchange: set('minutes') }), 'Min',
        h('label.chk', h('input', { type: 'checkbox', checked: !!t.only_at, onchange: (e) => { t.only_at = e.target.checked; win.style.display = e.target.checked ? '' : 'none'; if (e.target.checked) { t.from = t.from || '18:00'; t.to = t.to || '06:00'; } } }), 'nur zu bestimmten Zeiten'), win);
    }
    if (typ === 'heartbeat') parts.push(h('span.muted', 'sobald der Computer beim Heartbeat (wieder) online erkannt wird'));
    const extra = [];
    if (!['scan_age', 'heartbeat'].includes(typ) && !opts.noCatchUp) {
      extra.push(h('label.chk', { title: 'Computer, die zum Zeitpunkt offline sind, werden gescannt, sobald sie online kommen.' },
        h('input', { type: 'checkbox', checked: !!t.catch_up, onchange: set('catch_up') }), 'offline Computer nachholen'));
    }
    extra.push(h('label.chk', h('input', { type: 'checkbox', checked: t.enabled !== false, onchange: set('enabled') }), 'aktiv'),
      h('span.muted.small', 'Ende:'), h('input', { type: 'datetime-local', value: (t.end || '').replace(' ', 'T'), onchange: set('end'), style: { maxWidth: '200px' } }));
    return h('div.card', { style: { marginBottom: '8px' } }, h('div.card-b',
      h('div.row', { style: { flexWrap: 'wrap', gap: '6px', alignItems: 'center' } }, h('b', { style: { minWidth: '100px' } }, TRIG_LABELS[typ]), ...parts,
        h('div.spacer'), btn('', async () => { if (!(await askRemove(`Auslöser „${TRIG_LABELS[typ] || typ}“ entfernen?`))) return; list.splice(idx, 1); draw(); }, { icon: 'trash', cls: 'small ghost', title: 'Auslöser entfernen' })),
      h('div.row', { style: { gap: '8px', marginTop: '6px', flexWrap: 'wrap' } }, ...extra)));
  }
  const TRIG_ICONS = { once: '1', interval: '↻', daily: 'T', weekly: '7', monthly: '31', scan_age: '⌕', heartbeat: '♥' };
  const addTrig = (typ) => {
    const now = new Date(Date.now() + 3600e3);
    const pad = (n) => String(n).padStart(2, '0');
    const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:00`;
    const defs = { once: { at: stamp }, interval: { every: 4, unit: 'h', start: stamp }, daily: { time: '08:00', every: 1 },
      weekly: { time: '08:00', days: [0], every: 1 }, monthly: { time: '08:00', days: [1] }, scan_age: { days: 7, hours: 0, minutes: 0 }, heartbeat: {} };
    list.push({ type: typ, enabled: true, ...defs[typ] });
    draw();
  };
  const toolbar = h('div.trig-bar', Object.entries(TRIG_LABELS).map(([k, l]) => h('button.trig-btn', { type: 'button', title: `Auslöser „${l}“ hinzufügen`,
    disabled: !allowed(k), onclick: () => addTrig(k) }, h('span.trig-ic', { 'data-t': k }, TRIG_ICONS[k]), h('span', l))));
  function allowed(k) { return !(opts.only && !opts.only.includes(k)); }
  function draw() {
    box.replaceChildren(toolbar, ...(list.length ? list.map(rowFor) : [h('p.muted', opts.empty || 'Keine Auslöser – das Profil läuft nur, wenn es manuell gestartet wird.')]));
  }
  draw();
  return box;
}
const TRIG_LABELS = { once: 'Einmalig', interval: 'Intervall', daily: 'Täglich', weekly: 'Wöchentlich', monthly: 'Monatlich', scan_age: 'Scan-Alter', heartbeat: 'Heartbeat' };

async function profileDlg(p, meta) {
  const isNew = !p;
  // Neue Profile wirken wie die festen zunächst auf „Alle Computer“ (id 0)
  p = p ? JSON.parse(JSON.stringify(p)) : { name: '', description: '', modules: meta.modules.map((m) => m.key), scanners: [], collections: [0], computers: [], triggers: [], enabled: 1 };
  const fixed = !!p.builtin_key;
  const name = h('input', { value: p.name, disabled: fixed });
  const desc = h('textarea', { rows: 3, value: p.description || '', disabled: fixed });
  const en = h('input', { type: 'checkbox', checked: !!p.enabled });
  const mods = new Set(p.modules);
  const scs = new Set(p.scanners);
  const stypes = new Set(p.scanner_types || []);
  const cols = new Set(p.collections);
  const checks = (items, set, keyOf, labelOf, subOf) => h('div', { style: { columns: '2 260px' } }, items.map((x) => h('label.chk', { style: { display: 'flex', breakInside: 'avoid', padding: '2px 0' } },
    h('input', { type: 'checkbox', checked: set.has(keyOf(x)), onchange: (e) => (e.target.checked ? set.add(keyOf(x)) : set.delete(keyOf(x))) }), labelOf(x), subOf ? h('span.muted.small', subOf(x)) : null)));
  const allBtn = (items, set, keyOf, boxFn) => h('div.row', { style: { marginBottom: '6px' } },
    btn('Alle', () => { items.forEach((x) => set.add(keyOf(x))); boxFn(); }, { cls: 'small ghost' }),
    btn('Keine', () => { set.clear(); boxFn(); }, { cls: 'small ghost' }));
  const stdBox = h('div'); const ownBox = h('div');
  const drawStd = () => stdBox.replaceChildren(checks(meta.modules, mods, (m) => m.key, (m) => m.label));
  const drawOwn = () => ownBox.replaceChildren(meta.scanners.length ? checks(meta.scanners, scs, (s) => s.id, (s) => s.name, (s) => ` (${s.type_label})${s.enabled ? '' : ' – deaktiviert'}`)
    : h('p.muted', 'Noch keine eigenen Scanner. Sie werden auf der Seite „Scanprofile“ unter „Eigene Scanner“ angelegt.'));
  drawStd(); drawOwn();
  const colBox = await collPicker(cols);
  const picker = await computerPicker(p.computers || [], { noFocus: true });
  const trig = triggerEditor(p.triggers);
  const compBox = h('div', h('p.muted', 'Wird geladen …'));
  if (!isNew) {
    GET(`/api/scanprofiles/${p.id}`).then((d) => {
      const t = table([
        { key: 'name', label: 'Computer', render: (r) => h('a', { href: `#/computer/${r.id}` }, r.name) },
        { key: 'online', label: 'Online', render: (r) => h('span.dot', { class: r.online ? 'on' : 'off' }) },
        { key: 'last_scan', label: 'Letzter Scan', render: (r) => h('span', { title: fmtDate(r.last_scan) }, relTime(r.last_scan)) },
        { key: 'last_attempt', label: 'Letzter Versuch', render: (r) => fmtDate(r.last_attempt) },
        { key: 'status', label: 'Status', render: profStatus },
      ], d.members, { sort: 'name', noun: 'Computer', selectable: true, fill: false, empty: 'Keine Ziel-Computer – unter „Ziele“ Sammlungen oder einzelne Computer wählen.' });
      t.actions.append(btn('Ausgewählte jetzt scannen', async () => {
        const ids = t.selectedIds();
        if (!ids.length) { toast('Bitte Computer auswählen', 'err'); return; }
        await run(() => POST(`/api/scanprofiles/${p.id}/scan`, { ids }), (r) => `${r.queued} Scan(s) gestartet` + (r.busy ? `, ${r.busy} belegt` : ''));
      }, { cls: 'small', icon: 'refresh' }));
      compBox.replaceChildren(t.el);
    }).catch((e) => compBox.replaceChildren(h('p.muted', e.message)));
  }
  const tabs = [
    ['Details', h('div.form', field('Name', name), field('Beschreibung', desc), field('Aktiv', h('label.chk', en, 'automatische Auslöser ausführen')))],
    ['Scanner', fixed ? h('div',
      h('p', h('span.badge.accent', 'Festes Profil'), ' Fest vorgegeben – die Scanner lassen sich nicht ändern. Für eine eigene Zusammenstellung „Duplizieren“.'),
      h('div.chips', [...p.modules.map((m) => h('span.badge', (meta.modules.find((x) => x.key === m) || { label: m }).label)),
        ...(p.scanner_types || []).map((t) => h('span.badge.info', `alle eigenen Scanner „${({ registry: 'Registry', files: 'Dateien & Ordner', wmi: 'WMI', powershell: 'PowerShell' })[t]}“`))]),
      (p.scanner_types || []).length ? h('p.muted.small', `Eigene Scanner dieses Typs: ${meta.scanners.filter((s) => p.scanner_types.includes(s.type)).map((s) => s.name).join(', ') || 'noch keine'} – anlegen unter „Eigene Scanner“.`) : null)
      : h('div', h('h3', 'Standard-Scanner'), allBtn(meta.modules, mods, (m) => m.key, drawStd), stdBox,
      h('h3', { style: { marginTop: '14px' } }, 'Eigene Scanner nach Typ'),
      h('p.muted.small', { style: { margin: '0 0 6px' } }, 'Alle aktiven eigenen Scanner dieses Typs – auch solche, die später angelegt werden.'),
      checks([['registry', 'Registry'], ['files', 'Dateien & Ordner'], ['wmi', 'WMI'], ['powershell', 'PowerShell']], stypes, (x) => x[0], (x) => x[1]),
      h('h3', { style: { marginTop: '14px' } }, 'Einzelne eigene Scanner'), meta.scanners.length ? allBtn(meta.scanners, scs, (s) => s.id, drawOwn) : null, ownBox)],
    ['Auslöser', h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Wann das Profil die Computer seiner Sammlungen scannt. Zeitangaben beziehen sich auf die Serverzeit des Containers (TZ in der .env).'), trig)],
    ['Ziele', h('div', h('p.muted.small', { style: { marginTop: 0 } }, 'Auf welche Computer die Auslöser wirken: Mitglieder der gewählten Sammlungen plus einzeln gewählte Computer. Ohne Ziel läuft das Profil nur manuell (Scannen → Profil wählen).'),
      h('h3', 'Sammlungen'), colBox, h('h3', { style: { marginTop: '12px' } }, 'Einzelne Computer'), picker.el)],
  ];
  if (!isNew) tabs.push(['Computer', compBox]);
  const buttons = [{ label: 'Abbrechen' }];
  if (!isNew) {
    if (!p.is_default && fixed) {
      buttons.unshift({ label: 'Als Standard', onClick: async () => { await POST(`/api/scanprofiles/${p.id}/default`); toast(`„${p.name}“ ist jetzt das Standardprofil`, 'ok'); route(); } });
    }
    if (!p.is_default && !fixed) {
      buttons.unshift({ label: 'Löschen', cls: 'danger', onClick: async () => { if (!(await confirmDlg(`Scanprofil „${p.name}“ löschen?`, 'Löschen', true))) return false; await DEL(`/api/scanprofiles/${p.id}`); toast('Gelöscht', 'ok'); route(); } });
      buttons.unshift({ label: 'Als Standard', onClick: async () => { await POST(`/api/scanprofiles/${p.id}/default`); toast(`„${p.name}“ ist jetzt das Standardprofil`, 'ok'); route(); } });
    }
    buttons.unshift({ label: 'Duplizieren', onClick: async () => { await POST(`/api/scanprofiles/${p.id}/duplicate`); toast('Kopie angelegt', 'ok'); route(); } });
    buttons.unshift({ label: 'Jetzt ausführen', icon: 'refresh', onClick: async () => {
      if (!(await confirmDlg(`Profil „${p.name}“ jetzt auf allen Online-Computern seiner Sammlungen ausführen?`, 'Scannen'))) return false;
      await run(() => POST(`/api/scanprofiles/${p.id}/scan`, {}), (r) => `${r.queued} Scan(s) gestartet` + (r.busy ? `, ${r.busy} belegt` : ''));
      return false;
    } });
  }
  buttons.push({ label: 'Speichern', cls: 'primary', onClick: async () => {
    const body = { name: name.value, description: desc.value, enabled: en.checked, modules: [...mods], scanners: [...scs], scanner_types: [...stypes], collections: [...cols], computers: picker.selected(),
      triggers: p.triggers.map((t) => ({ ...t, at: t.at && t.at.replace('T', ' '), start: t.start && t.start.replace('T', ' '), end: t.end ? t.end.replace('T', ' ') : null })) };
    if (isNew) await POST('/api/scanprofiles', body); else await PUT(`/api/scanprofiles/${p.id}`, body);
    toast('Scanprofil gespeichert', 'ok'); route();
  } });
  modal(isNew ? 'Neues Scanprofil' : `Scanprofil: ${p.name}`, tabsBox(tabs), buttons, { wide: true, sticky: true, help: 'profiles' });
}

// Scannen mit Profilauswahl (Menü am Knopf)
async function scanMenu(anchor, ids) {
  if (openMenu) { closeMenu(); return; }
  if (!ids.length) { toast('Bitte zuerst Computer auswählen', 'err'); return; }
  if (S.menuLoading) return; // Doppelklick: nicht zwei Menüs öffnen
  S.menuLoading = true;
  let meta;
  try { meta = await GET('/api/scanprofiles'); } finally { S.menuLoading = false; }
  const list = h('div.tmenu-list', meta.profiles.map((p) => h('button.tmenu-item', { type: 'button', onclick: () => { closeMenu(); scanIds(ids, p.id); } },
    h('span.ic', { 'data-ic': 'refresh' }, icon('refresh')), h('span.lbl', p.name), p.is_default ? h('span.muted.small', 'Standard') : h('span.muted.small', `${p.modules.length + p.scanners.length} Scanner`))),
  h('div.tmenu-sep'), h('button.tmenu-item', { type: 'button', onclick: () => { closeMenu(); go('#/scanners'); } }, h('span.ic', { 'data-ic': 'gear' }, icon('gear')), h('span.lbl', 'Scanprofile verwalten …')));
  const r = anchor.getBoundingClientRect();
  const panel = h('div.tmenu', h('div.tmenu-head', h('b', 'Scannen mit Profil'), h('div.spacer'), h('span.muted.small', ids.length === 1 ? '1 Computer' : `${ids.length} Computer`)), list);
  panel.style.top = `${r.bottom + 4}px`;
  panel.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 360))}px`;
  document.body.appendChild(panel);
  openMenu = panel;
  setTimeout(() => { document.addEventListener('mousedown', menuOutside, true); document.addEventListener('keydown', menuKey, true); }, 0);
}

async function scanProfileDlg(ids) {
  const meta = await GET('/api/scanprofiles');
  const s = sel(meta.profiles.map((p) => [p.id, p.name + (p.is_default ? ' (Standard)' : '')]), (meta.profiles.find((p) => p.is_default) || {}).id);
  modal('Scannen mit Profil', h('div.form', field('Scanprofil', s), field('Computer', h('span', String(ids.length)))), [
    { label: 'Abbrechen' }, { label: 'Scannen', cls: 'primary', onClick: () => scanIds(ids, Number(s.value)) }]);
}

// ---------------------------------------------------------------------------
// Hilfe: Handbuch (Markdown) mit Inhaltsverzeichnis, Suche und Sprungzielen
// ---------------------------------------------------------------------------
const HELP_FOR = { dashboard: 'overview', computers: 'computers', computer: 'computerview', software: 'software', packages: 'packages',
  deployments: 'deployments', deployment: 'deployments', schedules: 'schedules', rules: 'schedules', scanners: 'scanning', tools: 'tools',
  reports: 'reports', logs: 'reports', settings: 'settings', kiosk: 'kiosk', repository: 'repository' };
const HELP_PDF = { de: 'POCKET-KNIFE-Handbuch.pdf', en: 'POCKET-KNIFE-Manual.pdf' };
let HELP_CACHE = null;

// Elemente ohne Übersetzung bauen (der Hilfetext ist bereits in der gewählten Sprache)
function rawEl(tag, cls, ...kids) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  for (const k of kids.flat()) {
    if (k === null || k === undefined || k === '') continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

function mdInline(text) {
  // **fett**, *kursiv*, `code`, [Text](Link), ![Bild](Pfad)
  const out = [];
  const re = /(!?\[([^\]]*)\]\(([^)\s]+)\))|(`([^`]+)`)|(\*\*([^*]+)\*\*)|(\*([^*\s][^*]*)\*)/g;
  let last = 0; let m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      if (m[1][0] === '!') {
        const img = rawEl('img', 'help-img');
        img.src = '/static/help/' + m[3].replace(/^\/+/, '');
        img.alt = m[2]; img.loading = 'lazy';
        out.push(rawEl('figure', 'help-fig', img, m[2] ? rawEl('figcaption', '', m[2]) : null));
      } else {
        const a = rawEl('a', '', m[2]);
        if (/^#/.test(m[3])) a.href = '#/help?s=' + m[3].slice(1); else { a.href = m[3]; a.rel = 'noopener noreferrer'; a.target = '_blank'; }
        out.push(a);
      }
    } else if (m[4]) out.push(rawEl('code', '', m[5]));
    else if (m[6]) out.push(rawEl('b', '', mdInline(m[7])));
    else if (m[8]) out.push(rawEl('i', '', mdInline(m[9])));
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function parseHelp(md) {
  const lines = md.replace(/\r/g, '').split('\n');
  const doc = { title: '', subtitle: '', sections: [] };
  let sec = null; let i = 0;
  const startSec = (level, title, id) => {
    sec = { level, title, id: id || title.toLowerCase().replace(/[^a-z0-9äöüß]+/g, '-'), nodes: [], text: title };
    doc.sections.push(sec);
  };
  while (i < lines.length) {
    const l = lines[i];
    if (/^% /.test(l)) { if (!doc.title) doc.title = l.slice(2); else doc.subtitle = l.slice(2); i++; continue; }
    const hm = /^(#{1,3})\s+(.+?)(?:\s+\{#([\w-]+)\})?\s*$/.exec(l);
    if (hm) { startSec(hm[1].length, hm[2], hm[3]); i++; continue; }
    if (!l.trim()) { i++; continue; }
    if (!sec) startSec(1, doc.title || 'Hilfe');
    if (/^```/.test(l)) {
      const buf = []; i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      sec.nodes.push(rawEl('pre', 'help-code', buf.join('\n'))); sec.text += ' ' + buf.join(' ');
      continue;
    }
    if (/^\|/.test(l)) {
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) { rows.push(lines[i]); i++; }
      const cells = (r) => r.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(rows[0]);
      const body = rows.slice(2).map(cells);
      const tbl = rawEl('table', 'help-table',
        head.some(Boolean) ? rawEl('thead', '', rawEl('tr', '', head.map((c) => rawEl('th', '', mdInline(c))))) : null,
        rawEl('tbody', '', body.map((r) => rawEl('tr', '', r.map((c) => rawEl('td', '', mdInline(c)))))));
      sec.nodes.push(rawEl('div', 'help-table-wrap', tbl)); sec.text += ' ' + rows.join(' ');
      continue;
    }
    if (/^>\s?/.test(l)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
      sec.nodes.push(rawEl('div', 'help-note', mdInline(buf.join(' ')))); sec.text += ' ' + buf.join(' ');
      continue;
    }
    if (/^(\s*)([-*]|\d+\.)\s+/.test(l)) {
      // Listen mit einer Unterebene (eingerückte Punkte) und eingerückten Codeblöcken innerhalb eines Punkts
      const isItem = (x) => /^(\s*)([-*]|\d+\.)\s+/.test(x);
      const ind = (x) => /^(\s*)/.exec(x)[1].length;
      const isFence = (x) => /^\s*```/.test(x);
      const base = ind(l);
      const list = rawEl(/^\s*\d+\./.test(l) ? 'ol' : 'ul', 'help-list');
      let li = null; let sub = null;
      const itemText = (lvl) => {
        let item = lines[i].replace(/^(\s*)([-*]|\d+\.)\s+/, ''); i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && ind(lines[i]) > lvl && !isItem(lines[i]) && !isFence(lines[i])) item += ' ' + lines[i++].trim();
        sec.text += ' ' + item;
        return item;
      };
      while (i < lines.length) {
        const x = lines[i];
        if (isItem(x) && ind(x) <= base) {
          li = rawEl('li', '', mdInline(itemText(base))); list.appendChild(li); sub = null;
        } else if (li && isItem(x)) {
          if (!sub) { sub = rawEl(/^\s*\d+\./.test(x) ? 'ol' : 'ul', 'help-list'); li.appendChild(sub); }
          sub.appendChild(rawEl('li', '', mdInline(itemText(ind(x)))));
        } else if (li && isFence(x) && ind(x) > base) {
          const pad = ind(x); const buf = []; i++;
          while (i < lines.length && !isFence(lines[i])) { buf.push(lines[i].slice(Math.min(pad, ind(lines[i])))); i++; }
          i++;
          li.appendChild(rawEl('pre', 'help-code', buf.join('\n'))); sec.text += ' ' + buf.join(' ');
        } else if (li && /^\s{2,}\S/.test(x) && ind(x) > base) {
          li.appendChild(rawEl('div', '', mdInline(x.trim()))); sec.text += ' ' + x.trim(); i++;
        } else break;
      }
      sec.nodes.push(list);
      continue;
    }
    if (/^!\[/.test(l)) { sec.nodes.push(...mdInline(l)); i++; continue; }
    if (!l.trim()) { i++; continue; }
    const buf = [l];
    i++;
    while (i < lines.length && lines[i].trim() && !/^(#|```|\||>|\s*([-*]|\d+\.)\s|!\[)/.test(lines[i])) buf.push(lines[i++]);
    sec.nodes.push(rawEl('p', '', mdInline(buf.join(' ')))); sec.text += ' ' + buf.join(' ');
  }
  return doc;
}

async function loadHelp() {
  if (HELP_CACHE && HELP_CACHE.lang === PK_LANG) return HELP_CACHE.doc;
  const r = await fetch(`/static/help/${PK_LANG === 'en' ? 'en' : 'de'}.md`, { credentials: 'same-origin' });
  const doc = parseHelp(await r.text());
  HELP_CACHE = { lang: PK_LANG, doc };
  return doc;
}

function helpButton(id) {
  const b = h('button.ghost.small.help-btn', { type: 'button', title: 'Hilfe zu dieser Seite', onclick: () => go(`#/help?s=${id}`) }, icon('help'));
  return b;
}

async function viewHelp(_parts, params) {
  const doc = await loadHelp();
  const pdf = HELP_PDF[PK_LANG === 'en' ? 'en' : 'de'];
  setTop('Hilfe', doc.subtitle || doc.title,
    h('a.btn', { href: `/static/help/${pdf}`, download: pdf, title: 'Das komplette Handbuch als PDF' }, icon('download'), 'Handbuch als PDF'));
  const q = h('input', { type: 'search', placeholder: 'Hilfe durchsuchen …' });
  const toc = h('nav.help-toc');
  const body = rawEl('div', 'help-body');
  const secEls = {};
  for (const s of doc.sections) {
    const hd = rawEl(s.level === 1 ? 'h2' : s.level === 2 ? 'h3' : 'h4', 'help-h', s.title);
    hd.id = 'help-' + s.id;
    const el = rawEl('section', 'help-sec lvl' + s.level, hd, s.nodes.map((n) => n.cloneNode(true)));
    secEls[s.id] = el;
    body.appendChild(el);
  }
  const links = {};
  const drawToc = (filter) => {
    const f = (filter || '').trim().toLowerCase();
    toc.replaceChildren(...doc.sections.filter((s) => s.level <= 2).map((s) => {
      const hit = !f || s.text.toLowerCase().includes(f) || doc.sections.some((x) => x.level > s.level && x.text.toLowerCase().includes(f) && sectionParent(x) === s);
      const a = rawEl('a', 'lvl' + s.level + (hit ? '' : ' dim'), s.title);
      a.href = '#'; a.onclick = (e) => { e.preventDefault(); show(s.id); };
      links[s.id] = a;
      return a;
    }));
  };
  const sectionParent = (x) => { let p = null; for (const s of doc.sections) { if (s === x) return p; if (s.level < x.level) p = s; } return p; };
  const show = (id) => {
    const el = secEls[id];
    if (!el) return;
    el.scrollIntoView({ block: 'start' });
    Object.values(links).forEach((a) => a.classList.remove('on'));
    if (links[id]) links[id].classList.add('on');
    try { history.replaceState(null, '', `#/help?s=${id}`); } catch (e) { /* egal */ }
  };
  q.addEventListener('input', () => {
    const f = q.value.trim().toLowerCase();
    drawToc(f);
    for (const s of doc.sections) secEls[s.id].classList.toggle('dim', !!f && !s.text.toLowerCase().includes(f));
    if (f) { const first = doc.sections.find((s) => s.text.toLowerCase().includes(f)); if (first) secEls[first.id].scrollIntoView({ block: 'start' }); }
  });
  drawToc('');
  setContent(h('div.help-layout', h('div.help-side', q, toc), body));
  const start = params.get('s');
  if (start) setTimeout(() => show(start), 50);
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Ansicht: Dateien auf dem Client (Browser-Dateimanager über die Freigaben C$, D$ …)
// ---------------------------------------------------------------------------
let FILE_CLIP = null; // { cid, action: 'copy'|'move', src, names }

async function viewFiles(parts, params) {
  const cid = Number(parts[0]);
  const c = await GET(`/api/computers/${cid}`);
  setTop(`Dateien: ${c.name}`, 'Zugriff auf die Laufwerke über die administrativen Freigaben – kopieren, verschieben, löschen, herunter- und hochladen.',
    btn('Zum Computer', () => go(`#/computer/${cid}?cat=disks`), { icon: 'pc' }));
  const crumbs = h('div.fm-crumbs');
  const tools = h('div.row', { style: { gap: '6px', flexWrap: 'wrap' } });
  const listBox = h('div.fx-list');
  const info = h('span.muted.small');
  // EIN Suchfeld links über der Liste (2.25.7): Enter bzw. „Suchen“ durchsucht den geöffneten Ordner samt allen
  // Unterordnern auf dem Client. Ein Filter nur für den aktuellen Ordner gibt es hier nicht mehr – vorher standen ein
  // Filterfeld (links, sah nach Suche aus) und das Suchfeld (rechts) nebeneinander.
  let searching = false, busy = false;
  const deepBtn = (get) => btn('Suchen', () => doSearch(get()), { cls: 'small primary', icon: 'folder',
    title: tr('Den geöffneten Ordner und alle Unterordner auf dem Computer durchsuchen (Enter im Feld)') });
  setContent(h('div.fx-page', h('div.card.fx-card',
    h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '8px' } }, tools, h('div.spacer'), info),
    crumbs, listBox)));
  const fxTableOpts = (v) => ({
    placeholder: tr('In diesem Ordner und allen Unterordnern suchen, z. B. setup oder *.log'), q: v || '', liveFilter: false,
    searchStyle: { minWidth: '420px' },
    onSearchEnter: (t) => doSearch(t),
    searchAfter: (get) => deepBtn(get),
  });
  let cur = params.get('path') || '';
  let focusName = params.get('focus') || '';
  let redirects = 0;
  let curNames = new Set(); // Namen im aktuellen Ordner – Hochladen überschreibt nicht ungefragt
  let selected = new Set();

  let loadGen = 0;
  const navGen = S.nav;
  async function load(path) {
    // Nur die zuletzt angeforderte Liste zählt: kommt eine ältere Antwort später an, darf sie weder angezeigt
    // werden noch „cur“ bestimmen – sonst wirken Löschen/Umbenennen/Einfügen im falschen Ordner
    const gen = ++loadGen;
    const want = path || '';
    selected = new Set();
    searching = false;
    listBox.replaceChildren(h('div.muted', { style: { padding: '12px' } }, 'Lädt …'));
    let d;
    try { d = await GET(`/api/computers/${cid}/files${want ? '?path=' + encodeURIComponent(want) : ''}`); }
    catch (e) {
      if (gen !== loadGen || navGen !== S.nav) return;
      cur = want; listBox.replaceChildren(h('div.empty', e.message)); drawCrumbs(); drawTools(); return;
    }
    if (gen !== loadGen || navGen !== S.nav) return; // überholt oder Ansicht verlassen
    cur = want;
    try { history.replaceState(null, '', `#/files/${cid}${cur ? '?path=' + encodeURIComponent(cur) : ''}`); } catch (e) { /* egal */ }
    if (!cur) { drawDrives(d.drives); drawCrumbs(); drawTools(); return; }
    if (d.redirect) {
      // Verknüpfung (z. B. „All Users“ → C:\ProgramData): wie im Windows-Explorer zum Ziel wechseln
      if ((redirects = (redirects || 0) + 1) > 5) { redirects = 0; listBox.replaceChildren(h('div.empty', 'Verknüpfungen zeigen im Kreis.')); return; }
      toast(`Verknüpfung: ${d.path} → ${d.redirect}`, 'ok');
      load(d.redirect);
      return;
    }
    redirects = 0;
    curNames = new Set((d.items || d.entries || []).map((x) => String(x.name || '').toLowerCase()));
    drawList(d);
    drawCrumbs();
    drawTools();
  }

  async function doSearch(term) {
    const v = String(term || '').trim();
    if (busy) return;  // ein Klick = eine Suche auf dem Client
    if (!cur) { toast(tr('Zuerst ein Laufwerk oder einen Ordner öffnen – gesucht wird ab dort.'), 'err'); return; }
    if (v.replace(/[*?]/g, '').length < 2) { toast(tr('Bitte mindestens 2 Zeichen eingeben.'), 'err'); return; }
    const gen = ++loadGen;
    const base = cur;
    searching = true;
    selected = new Set();
    info.textContent = '';
    listBox.replaceChildren(h('div.muted', { style: { padding: '12px' } }, fmtT('{0} durchsucht {1} und alle Unterordner nach „{2}“ … (bei einem ganzen Laufwerk kann das etwas dauern)', c.name, base, v)));
    let r;
    busy = true;
    try { r = await GET(`/api/computers/${cid}/files/search?path=${encodeURIComponent(base)}&q=${encodeURIComponent(v)}`); }
    catch (e) {
      if (gen !== loadGen || navGen !== S.nav) return;
      listBox.replaceChildren(h('div.empty', e.message, ' ', btn('Zurück zum Ordner', () => load(base), { cls: 'small ghost' }))); return;
    } finally { busy = false; }
    if (gen !== loadGen || navGen !== S.nav) return;  // überholt (anderer Ordner geöffnet, neue Suche)
    drawResults(r, v, base);
  }

  function drawResults(r, v, base) {
    const join = (f, n) => f.replace(/\\+$/, '') + '\\' + n;
    const n = r.items.length;
    info.textContent = (r.truncated ? fmtT('{0} Treffer (abgebrochen – bitte genauer suchen)', n) : fmtT('{0} Treffer', n))
      + (r.skipped ? ' · ' + fmtT('{0} Ordner ohne Zugriff übersprungen', r.skipped) : '')
      + (r.errors ? ' · ' + fmtT('{0} Einträge nicht lesbar (kein Zugriff oder Pfad zu lang)', r.errors) : '')
      + (r.via === 'smb' ? ' · ' + tr('WinRM nicht erreichbar – über die Freigabe gesucht (langsamer, enger begrenzt)') : '');
    const openHit = (x) => {
      if (x.dir) load(join(x.folder, x.name));
      else { focusName = x.name; load(x.folder); }
    };
    const head = h('div.row', { style: { gap: '8px', alignItems: 'center', padding: '6px 2px 10px' } },
      h('span.muted', fmtT('Suchergebnis für „{0}“ in {1}', v, base)), h('div.spacer'),
      btn('Suche beenden', () => load(base), { cls: 'small ghost', icon: 'x' }));
    if (!n) {
      listBox.replaceChildren(head, h('div.empty', r.truncated ? tr('Keine Treffer in der Zeit – bitte genauer suchen oder in einem Unterordner beginnen.') : tr('Keine Treffer.')));
      return;
    }
    const t = table([
      { key: 'name', label: 'Name', render: (x) => h('span.row', { style: { gap: '6px', flexWrap: 'nowrap' } },
        h('span.ic', { 'data-ic': x.dir ? 'folder' : 'file', style: { color: x.dir ? 'var(--accent)' : 'var(--muted)' } }, icon(x.dir ? 'folder' : 'file')),
        h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); openHit(x); } }, x.name),
        x.reparse ? h('span.badge', { title: 'Verknüpfung (Junction/Symlink)' }, '↗') : null) },
      { key: 'folder', label: 'Ordner', render: (x) => h('span.muted.small.raw', x.folder), sort: (x) => x.folder.toLowerCase() },
      { key: 'size', label: 'Größe', cls: 'right', render: (x) => (x.dir || x.reparse ? '' : fmtBytes(x.size)), sort: (x) => (x.dir ? -1 : x.size) },
      { key: 'modified', label: 'Geändert', render: (x) => fmtDate(x.modified), sort: (x) => x.modified || '' },
    ], r.items, { key: null, noun: 'Treffer', sort: null, id: (x) => join(x.folder, x.name), ...fxTableOpts(v),
      onRow: (x) => openHit(x),
      onContext: (x, e) => {
        contextMenu(e.clientX, e.clientY, [
          { label: x.dir ? 'Öffnen' : 'Im Ordner zeigen', icon: 'folder', onClick: () => openHit(x) },
          !x.dir && !x.denied && !x.reparse ? { label: 'Herunterladen', icon: 'download', onClick: () => download(x, x.folder) } : null,
          x.dir ? null : { label: 'Ordner öffnen', icon: 'folder', onClick: () => load(x.folder) },
          '-',
          { label: 'Pfad kopieren', onClick: () => copyText(join(x.folder, x.name)) },
        ]);
      } });
    listBox.replaceChildren(head, t.el);
  }

  function drawDrives(drives) {
    info.textContent = '';
    if (!drives || !drives.length) {
      listBox.replaceChildren(h('div.empty', 'Keine Laufwerke bekannt – den Computer zuerst scannen (Laufwerke). Anschließend hier ein Laufwerk wählen oder oben einen Pfad eingeben.'));
      return;
    }
    listBox.replaceChildren(h('div.fm-drives', drives.map((dr) => h('button.fm-drive', { type: 'button', onclick: () => load(`${dr.drive}\\`) },
      h('span.ic', { 'data-ic': 'db' }, icon('db')), h('b', dr.drive), dr.label ? h('span.muted.raw', dr.label) : null,
      dr.size ? h('span.muted.small', `${fmtBytes(dr.size - dr.free)} von ${fmtBytes(dr.size)} belegt`) : null))));
  }

  function parentOf(path) {
    const p = path.replace(/\\+$/, '');
    const i = p.lastIndexOf('\\');
    if (i < 3) return p.length <= 3 ? '' : p.slice(0, 3); // auf Laufwerkswurzel bzw. Laufwerksliste
    return p.slice(0, i);
  }

  function drawCrumbs() {
    const parts2 = [];
    parts2.push(h('a', { href: '#', onclick: (e) => { e.preventDefault(); load(''); } }, 'Laufwerke'));
    if (cur) {
      const segs = cur.replace(/\\+$/, '').split('\\');
      let acc = '';
      segs.forEach((s, i) => {
        acc = i === 0 ? s + '\\' : acc.replace(/\\$/, '') + '\\' + s;
        const p = acc;
        parts2.push(h('span.muted', ' \\ '), i === segs.length - 1 ? h('b.raw', s || p) : h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); load(p); } }, s || p));
      });
    }
    crumbs.replaceChildren(...parts2);
  }

  function drawTools() {
    const up = parentOf(cur);
    const els = [
      btn('▲ Übergeordnet', () => load(up), { cls: 'small ghost', disabled: !cur }),
      btn('Aktualisieren', () => load(cur), { cls: 'small ghost', icon: 'refresh' }),
    ];
    if (cur) {
      els.push(btn('Neuer Ordner', async () => {
        const n = (prompt('Name des neuen Ordners') || '').trim();
        if (!n) return;
        if (await run(() => POST(`/api/computers/${cid}/files/mkdir`, { path: cur, name: n }), 'Ordner angelegt') !== undefined) load(cur);
      }, { cls: 'small', icon: 'folder' }));
      els.push(uploadBtn());
      if (FILE_CLIP && FILE_CLIP.cid === cid && FILE_CLIP.names.length) {
        els.push(btn(`Einfügen (${FILE_CLIP.names.length}${FILE_CLIP.action === 'move' ? ', verschieben' : ''})`, () => paste(), { cls: 'small primary', icon: 'send' }));
      }
    }
    tools.replaceChildren(...els);
  }

  function uploadBtn() {
    const inp = h('input', { type: 'file', style: { display: 'none' }, multiple: true, onchange: (e) => doUpload([...e.target.files]) });
    const b = btn('Hochladen', () => inp.click(), { cls: 'small', icon: 'send' });
    return h('span', b, inp);
  }

  async function doUpload(files) {
    if (!files.length) return;
    const clash = [...files].filter((f) => curNames.has(f.name.toLowerCase())).map((f) => f.name);
    if (clash.length && !(await confirmDlg(fmtT('{0} gibt es in diesem Ordner schon: {1}. Überschreiben?',
      clash.length === 1 ? tr('Die Datei') : fmtT('{0} Dateien', clash.length), clash.slice(0, 5).join(', ') + (clash.length > 5 ? ' …' : '')), 'Überschreiben', true))) return;
    let ok = 0;
    for (const f of files) {
      try {
        await api(`/api/computers/${cid}/files/upload?path=${encodeURIComponent(cur)}&name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f, raw: true });
        ok += 1;
      } catch (e) { toast(`${f.name}: ${e.message}`, 'err'); }
    }
    if (ok) { toast(`${ok} ${ok === 1 ? 'Datei' : 'Dateien'} hochgeladen`, 'ok'); load(cur); }
  }

  async function paste() {
    const r = await run(() => POST(`/api/computers/${cid}/files/transfer`, { action: FILE_CLIP.action, src: FILE_CLIP.src, items: FILE_CLIP.names, dst: cur }));
    if (r === undefined) return;
    if (r.errors && r.errors.length) toast(`${r.ok} übertragen, ${r.errors.length} Fehler: ${r.errors[0]}`, 'err');
    else toast(`${r.ok} ${r.ok === 1 ? 'Eintrag' : 'Einträge'} ${FILE_CLIP.action === 'move' ? 'verschoben' : 'kopiert'}`, 'ok');
    if (FILE_CLIP.action === 'move') FILE_CLIP = null;
    load(cur);
  }

  function clip(action, names) {
    FILE_CLIP = { cid, action, src: cur, names };
    toast(`${names.length} ${names.length === 1 ? 'Eintrag' : 'Einträge'} ${action === 'move' ? 'ausgeschnitten' : 'kopiert'} – im Zielordner „Einfügen“`, 'ok');
    drawTools();
  }

  async function doDelete(items) {
    const names = items.map((x) => x.name);
    if (!(await confirmDlg(`${names.length === 1 ? `„${names[0]}“` : names.length + ' Einträge'} auf ${c.name} endgültig löschen? Ordner werden mit ihrem Inhalt gelöscht. Das lässt sich nicht rückgängig machen.`, 'Löschen', true))) return;
    const r = await run(() => POST(`/api/computers/${cid}/files/delete`, { items: items.map((x) => ({ path: cur, name: x.name, dir: x.dir })) }));
    if (r === undefined) return;
    if (r.errors && r.errors.length) toast(`${r.ok} gelöscht, ${r.errors.length} Fehler: ${r.errors[0]}`, 'err');
    else toast(`${r.ok} ${r.ok === 1 ? 'Eintrag' : 'Einträge'} gelöscht`, 'ok');
    load(cur);
  }

  async function doRename(r) {
    const n = (prompt('Neuer Name', r.name) || '').trim();
    if (!n || n === r.name) return;
    if (await run(() => POST(`/api/computers/${cid}/files/rename`, { path: cur, name: r.name, new: n }), 'Umbenannt') !== undefined) load(cur);
  }

  async function download(r, folder = cur) {
    const url = `/api/computers/${cid}/files/download?path=${encodeURIComponent(folder)}&name=${encodeURIComponent(r.name)}`;
    // Erst prüfen (gesperrt, zu groß, Verknüpfung …) – sonst landet ein Fehler als Textseite im Browser
    try { await GET(url + '&check=1'); } catch (e) { toast(e.message, 'err'); return; }
    window.location.href = url;
  }

  function drawList(d) {
    info.textContent = `${d.items.length} ${d.items.length === 1 ? 'Eintrag' : 'Einträge'}`;
    const open = (r) => { if (r.dir) load(cur.replace(/\\+$/, '') + '\\' + r.name); else if (!r.denied) download(r); };
    const t = table([
      { key: 'name', label: 'Name', render: (r) => h('span.row', { style: { gap: '6px', flexWrap: 'nowrap' } },
        h('span.ic', { 'data-ic': r.dir ? 'folder' : 'file', style: { color: r.dir ? 'var(--accent)' : 'var(--muted)' } }, icon(r.dir ? 'folder' : 'file')),
        r.denied ? h('span.raw', r.name) : h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); open(r); } }, r.name),
        r.reparse ? h('span.badge', { title: r.dir ? 'Verknüpfung (Junction/Symlink) – Öffnen springt zum Ziel' : 'Verknüpfung (Symlink)' }, '↗') : null,
        r.hidden ? h('span.badge', { title: 'Versteckt' }, 'versteckt') : null,
        r.denied ? h('span.badge.err', 'kein Zugriff') : null) },
      { key: 'size', label: 'Größe', cls: 'right', render: (r) => (r.dir || r.reparse ? '' : fmtBytes(r.size)), sort: (r) => (r.dir ? -1 : r.size) },
      { key: 'modified', label: 'Geändert', render: (r) => fmtDate(r.modified), sort: (r) => r.modified || '' },
    ], d.items, { key: null, noun: 'Einträge', sort: 'name', selectable: true, id: (r) => r.name, ...fxTableOpts(''),
      // Nichts im Ordner gefunden → gleich die Suche in den Unterordnern anbieten
      empty: 'Leerer Ordner',
      onRow: (r) => open(r),
      onSelect: (sx) => { selected = new Set(sx.map((x) => x.name)); },
      onContext: (r, e, selRows) => {
        const many = selRows.length > 1;
        contextMenu(e.clientX, e.clientY, [
          !many && r.dir ? { label: 'Öffnen', icon: 'folder', onClick: () => open(r) } : null,
          !many && !r.dir && !r.denied ? { label: 'Herunterladen', icon: 'download', onClick: () => download(r) } : null,
          { label: `Kopieren${many ? ` (${selRows.length})` : ''}`, icon: 'coll', onClick: () => clip('copy', selRows.map((x) => x.name)) },
          { label: `Ausschneiden${many ? ` (${selRows.length})` : ''}`, icon: 'send', onClick: () => clip('move', selRows.map((x) => x.name)) },
          !many ? { label: 'Umbenennen', icon: 'gear', onClick: () => doRename(r) } : null,
          '-',
          { label: `Löschen${many ? ` (${selRows.length})` : ''}`, icon: 'trash', cls: 'danger', onClick: () => doDelete(selRows) },
          '-',
          { label: 'Pfad kopieren', onClick: () => copyText(cur.replace(/\\+$/, '') + '\\' + r.name) },
        ]);
      },
      onDelete: (selRows) => doDelete(selRows) });
    listBox.replaceChildren(t.el);
    if (focusName) {
      const hit = d.items.find((x) => x.name.toLowerCase() === focusName.toLowerCase());
      if (hit) t.select([hit.name]);
      focusName = '';
    }
  }

  load(cur);
}

// ---------------------------------------------------------------------------
// Ansicht: Ereignisanzeige (Windows-Ereignisprotokolle live über WinRM)
// ---------------------------------------------------------------------------
const EV_LEVEL_BADGE = { 1: ['err', 'Kritisch'], 2: ['err', 'Fehler'], 3: ['warn', 'Warnung'], 4: ['', 'Info'], 0: ['', 'Info'], 5: ['', 'Ausführlich'] };

async function viewEventLog(parts, params) {
  const cid = Number(parts[0]);
  const c = await GET(`/api/computers/${cid}`);
  setTop(`Ereignisanzeige: ${c.name}`, 'Windows-Ereignisprotokolle live über WinRM – ohne etwas auf dem Client zu installieren.',
    btn('Zum Computer', () => go(`#/computer/${cid}?cat=computer`), { icon: 'pc' }));
  const logSel = sel([['System', 'System'], ['Application', 'Anwendung'], ['Security', 'Sicherheit'], ['Setup', 'Setup'], ['Microsoft-Windows-Windows Defender/Operational', 'Defender']], params.get('log') || 'System');
  const lvlSel = sel([['0', 'Alle Stufen'], ['3', 'Warnung und schlimmer'], ['2', 'Fehler und Kritisch'], ['1', 'nur Kritisch']], '3');
  const cntSel = sel([['100', '100 Einträge'], ['250', '250'], ['500', '500']], '100');
  const q = h('input', { type: 'search', placeholder: 'Suchen (Text, Ereignis-ID, Quelle) …', style: { minWidth: '220px' } });
  const out = h('div.fx-list', h('div.muted', { style: { padding: '12px' } }, 'Protokoll wählen und „Laden“.'));
  const status = h('span.muted.small');
  const loadBtn = btn('Laden', () => load(), { cls: 'primary', icon: 'refresh' });
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') load(); });
  setContent(h('div.fx-page', h('div.card.fx-card',
    h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '8px' } },
      h('b', 'Protokoll'), logSel, lvlSel, cntSel, q, loadBtn, status),
    out)));

  let evGen = 0;
  async function load() {
    const gen = ++evGen;
    if (!c.online) toast(`${c.name} ist laut Heartbeat offline – der Versuch kann fehlschlagen.`, 'err');
    status.textContent = 'Wird vom Computer gelesen …';
    try { history.replaceState(null, '', `#/eventlog/${cid}?log=${encodeURIComponent(logSel.value)}`); } catch (e) { /* egal */ }
    let d;
    try { d = await GET(`/api/computers/${cid}/eventlog?log=${encodeURIComponent(logSel.value)}&level=${lvlSel.value}&count=${cntSel.value}&q=${encodeURIComponent(q.value.trim())}`); }
    catch (e) { if (gen !== evGen) return; status.textContent = ''; out.replaceChildren(h('div.empty', e.message)); return; }
    if (gen !== evGen) return; // eine neuere Abfrage läuft
    status.textContent = '';
    const t = table([
      { key: 'level', label: 'Stufe', render: (r) => { const b = EV_LEVEL_BADGE[r.level] || ['', r.levelName]; return h('span.badge', { class: b[0] }, r.levelName || b[1]); }, sort: (r) => r.level },
      { key: 'time', label: 'Zeitpunkt', render: (r) => fmtDate(r.time), sort: (r) => r.time || '' },
      { key: 'id', label: 'ID', cls: 'right' },
      { key: 'provider', label: 'Quelle', cls: 'trunc' },
      { key: 'message', label: 'Meldung', cls: 'wrap', render: (r) => h('span.raw', (r.message || '').slice(0, 300)) },
    ], d.rows, { key: null, noun: 'Ereignisse', sort: 'time', dir: -1, id: (r) => r, pageSize: 500,
      onRow: (r) => modal(`Ereignis ${r.id} – ${r.provider}`, h('div',
        kv([['Zeitpunkt', fmtDate(r.time)], ['Stufe', r.levelName], ['Quelle', r.provider], ['Ereignis-ID', String(r.id)]]),
        h('pre.out', { style: { marginTop: '10px', whiteSpace: 'pre-wrap' } }, r.message || '(keine Meldung)')), [{ label: 'Schließen' }], { wide: true }) });
    out.replaceChildren(t.el);
  }
  load();
}

// ---------------------------------------------------------------------------
// Ansicht: Speicherplatz-Analyse (Ringdiagramm mit Zoom in die Ordnerebenen)
// ---------------------------------------------------------------------------
async function viewDiskUsage(parts, params) {
  const cid = Number(parts[0]);
  const [c, meta] = await Promise.all([GET(`/api/computers/${cid}`), GET(`/api/computers/${cid}/diskusage`)]);
  setTop(`Speicherplatz: ${c.name}`, 'Wo liegen die Speicherfresser? Klick auf einen Ring oder eine Zeile zoomt in den Ordner.',
    btn('Zum Computer', () => go(`#/computer/${cid}?cat=disks`), { icon: 'pc' }));
  const drives = (meta.drives || []).filter((d) => d.drive);
  const pathIn = h('input', { value: params.get('path') || (drives[0] ? `${drives[0].drive}\\` : 'C:\\'), placeholder: 'C:\\  oder  D:\\Daten' });
  const dl = combo(pathIn, [...drives.map((d) => [`${d.drive}\\`, `${fmtBytes(d.size - d.free)} belegt von ${fmtBytes(d.size)}`]),
    '%SystemDrive%\\Users', '%SystemDrive%\\Windows', '%ProgramData%'], { width: '260px' });
  const depth = sel([['3', '3 Ebenen'], ['4', '4 Ebenen'], ['5', '5 Ebenen'], ['6', '6 Ebenen']], '4', { title: 'Wie tief die Ordner im Ergebnis aufgeschlüsselt werden. Tiefer gelegene Ordner lassen sich per Klick nachscannen.' });
  const hist = sel([['', meta.scans.length ? '— gespeicherte Analysen —' : '— noch keine Analyse —'],
    ...meta.scans.map((s) => [s.id, `${s.path} · ${fmtDate(s.ts)} · ${fmtBytes(s.total)}`])], params.get('id') || '');
  hist.addEventListener('change', () => { if (hist.value) go(`#/diskusage/${cid}?id=${hist.value}`); });
  const status = h('span.muted.small');
  const scanBtn = btn('Analysieren', () => startScan(pathIn.value), { cls: 'primary', icon: 'refresh' });
  const body = h('div.du-body');
  const bar = h('div.card', h('div.card-b', h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
    h('b', 'Pfad'), dl, depth, scanBtn, status, h('div.spacer'), hist)));
  setContent(h('div.grid', bar, body));

  async function startScan(path) {
    if (!c.online) toast(`${c.name} ist laut Heartbeat offline – der Versuch kann fehlschlagen.`, 'err');
    const r = await run(() => POST(`/api/computers/${cid}/diskusage`, { path, depth: Number(depth.value) }));
    if (!r) return;
    scanBtn.disabled = true;
    const navGen = S.nav;
    const poll = async () => {
      if (navGen !== S.nav) return; // weg navigiert – der Scan läuft auf dem Server weiter und erscheint in der Liste
      let j;
      try { j = await GET(`/api/diskusage/jobs/${r.job}`); } catch (e) { status.textContent = e.message; scanBtn.disabled = false; return; }
      if (j.status === 'running') {
        status.textContent = `Läuft auf ${c.name} … ${j.elapsed} s (große Laufwerke brauchen ein bis zwei Minuten)`;
        setTimeout(poll, 2000);
        return;
      }
      scanBtn.disabled = false;
      if (j.status === 'error') { status.textContent = ''; toast(j.error, 'err'); return; }
      status.textContent = '';
      go(`#/diskusage/${cid}?id=${j.result_id}`);
    };
    poll();
  }

  const id = params.get('id') || (meta.scans[0] && !params.get('path') ? meta.scans[0].id : '');
  if (!id) {
    body.replaceChildren(h('div.card', h('div.card-b.empty', 'Pfad wählen und „Analysieren“ – das Ergebnis wird gespeichert und bleibt hier abrufbar.')));
    return;
  }
  hist.value = String(id);
  const res = await GET(`/api/diskusage/${id}`);
  pathIn.value = res.path || pathIn.value;
  duRender(body, res, (p) => { pathIn.value = p; startScan(p); }, cid);
}

// Kategorien in fester Reihenfolge (Farbe folgt dem Eintrag in der aktuellen Ansicht), Rest/Dateien neutral
const DU_SLOTS = 8;
function duRender(body, res, rescan, cid) {
  const root = res.tree;
  const sep = root.n.includes('/') && !root.n.includes('\\') ? '/' : '\\';
  const join = (a, b) => (a.endsWith(sep) ? a + b : a + sep + b);
  let stack = [root];
  const tip = h('div.du-tip');
  const chartBox = h('div.du-chart');
  const side = h('div.du-side');
  const crumbs = h('div.du-crumbs');
  const m = res.meta || {};
  const info = h('div.muted.small', [
    m.drive && m.drive.total ? `Laufwerk ${fmtBytes(m.drive.total - m.drive.free)} belegt von ${fmtBytes(m.drive.total)} (${fmtBytes(m.drive.free)} frei)` : '',
    `Analysiert: ${fmtBytes(root.s)} in ${num(root.fc)} Dateien und ${num(root.dc)} Ordnern`,
    `${fmtDate(res.ts)}${res.seconds !== undefined && res.seconds !== null ? ` · ${res.seconds} s` : ''}`,
    m.errors ? `${plural(m.errors, 'Eintrag', 'Einträge')} nicht lesbar (z. B. System Volume Information)` : '',
  ].filter(Boolean).join(' · '));
  body.replaceChildren(h('div.card', h('div.card-b', crumbs, info, h('div.du-wrap', chartBox, side))));

  const pathOf = (st) => st.slice(1).reduce((p, n) => join(p, n.n), root.n);
  // Im Explorer (Dateien) öffnen – nur lokale Laufwerkspfade (C:\…) sind über die Freigaben erreichbar
  const canExplore = (p) => !!cid && /^[A-Za-z]:(\\|$)/.test(p);
  const explore = (dir, name) => go(`#/files/${cid}?path=${encodeURIComponent(/^[A-Za-z]:$/.test(dir) ? dir + '\\' : dir)}${name ? '&focus=' + encodeURIComponent(name) : ''}`);
  const kidsOf = (n) => (n.k || []).filter((k) => k.s > 0);
  function colorOf(k, i) {
    if (k.o || k.f) return k.f && !k.o ? 'var(--du-file)' : 'var(--du-other)';
    return i < DU_SLOTS ? `var(--du-${i + 1})` : 'var(--du-other)';
  }
  function open(n) {
    if (n.f || n.o) return;
    if (n.k && n.k.length) { stack.push(n); draw(); return; }
    if (n.m) {
      const p = pathOf([...stack, n]);
      confirmDlg(`„${p}“ wurde nur als Summe erfasst (unterhalb der gewählten Tiefe). Jetzt genauer analysieren?`, 'Analysieren').then((ok) => { if (ok) rescan(p); });
    }
  }
  function up(i) { stack = stack.slice(0, i + 1); draw(); }

  function arc(r0, r1, a0, a1) {
    const big = a1 - a0 > Math.PI ? 1 : 0;
    const p = (r, a) => `${(r * Math.sin(a)).toFixed(2)} ${(-r * Math.cos(a)).toFixed(2)}`;
    if (a1 - a0 >= Math.PI * 2 - 1e-6) a1 = a0 + Math.PI * 2 - 1e-4;
    return `M${p(r1, a0)}A${r1} ${r1} 0 ${big} 1 ${p(r1, a1)}L${p(r0, a1)}A${r0} ${r0} 0 ${big} 0 ${p(r0, a0)}Z`;
  }
  function svgEl(tag, attrs) { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; }
  function showTip(ev, n, parentSize, path) {
    const pct = parentSize ? (100 * n.s) / parentSize : 0;
    tip.replaceChildren(...[h('b', n.n), h('div', `${fmtBytes(n.s)} · ${pct.toLocaleString('de-DE', { maximumFractionDigits: 1 })} %`),
      n.f ? null : h('div.muted', `${plural(n.fc || 0, 'Datei', 'Dateien')}, ${plural(n.dc || 0, 'Ordner', 'Ordner')}`),
      n.x ? h('div.muted', 'teilweise nicht lesbar') : null, path ? h('div.muted.mono', path) : null,
      n.k && n.k.length ? h('div.muted', 'Klick: hineinzoomen') : n.m ? h('div.muted', 'Klick: genauer analysieren') : null].filter(Boolean));
    tip.style.display = 'block';
    const r = chartBox.getBoundingClientRect();
    tip.style.left = `${ev.clientX - r.left + 14}px`; tip.style.top = `${ev.clientY - r.top + 14}px`;
  }
  const hideTip = () => { tip.style.display = 'none'; };

  function draw() {
    const v = stack[stack.length - 1];
    const vpath = pathOf(stack);
    crumbs.replaceChildren(...[...stack.flatMap((n, i) => [i ? h('span.muted', ' › ') : null,
      i < stack.length - 1 ? h('a', { class: i ? 'raw' : '', href: '#', onclick: (e) => { e.preventDefault(); up(i); } }, i ? n.n : root.n) : h('b', { class: i ? 'raw' : '' }, i ? n.n : root.n)]),
    h('div.spacer'),
    stack.length > 1 ? btn('Eine Ebene zurück', () => up(stack.length - 2), { cls: 'small ghost' }) : null,
    canExplore(vpath) ? btn('Im Explorer öffnen', () => explore(vpath), { cls: 'small ghost', icon: 'folder', title: 'Diesen Ordner in „Dateien“ öffnen – löschen, verschieben, herunterladen' }) : null,
    btn('Diesen Ordner neu analysieren', () => rescan(vpath), { cls: 'small ghost', icon: 'refresh' }),
    btn('Pfad kopieren', () => copyText(vpath), { cls: 'small ghost' })].filter(Boolean));
    // Ringdiagramm: Mitte = aktueller Ordner, innerer Ring = Inhalt, äußerer Ring = eine Ebene tiefer
    const S0 = 330; const R0 = 70; const R1 = 118; const R2 = 160;
    const svg = svgEl('svg', { viewBox: `${-S0 / 2} ${-S0 / 2} ${S0} ${S0}`, class: 'du-svg', role: 'img', 'aria-label': `Belegung von ${vpath}` });
    const total = v.s || 1;
    let a = 0;
    kidsOf(v).forEach((k, i) => {
      const a1 = a + (2 * Math.PI * k.s) / total;
      const col = colorOf(k, i);
      if (a1 - a > 0.002) {
        const seg = svgEl('path', { d: arc(R0, R1, a, a1), fill: col, class: 'du-seg' + (k.k || k.m ? ' du-click' : '') });
        seg.addEventListener('mousemove', (ev) => showTip(ev, k, total, join(vpath, k.n)));
        seg.addEventListener('mouseleave', hideTip);
        seg.addEventListener('click', () => { hideTip(); open(k); });
        svg.appendChild(seg);
        // äußerer Ring: Inhalt des Unterordners im selben Farbton, heller
        let b = a;
        for (const g of kidsOf(k)) {
          const b1 = b + (2 * Math.PI * g.s) / total;
          if (b1 - b > 0.004) {
            const s2 = svgEl('path', { d: arc(R1 + 2, R2, b, b1), fill: col, 'fill-opacity': g.o || g.f ? '0.28' : '0.55', class: 'du-seg' + (g.k || g.m ? ' du-click' : '') });
            s2.addEventListener('mousemove', (ev) => showTip(ev, g, total, join(join(vpath, k.n), g.n)));
            s2.addEventListener('mouseleave', hideTip);
            s2.addEventListener('click', () => { hideTip(); if (g.k && g.k.length) { stack.push(k); stack.push(g); draw(); } else if (g.m) { stack.push(k); open(g); stack.pop(); } });
            svg.appendChild(s2);
          }
          b = b1;
        }
        // Beschriftung nur für große Stücke
        const rr = (R0 + R1) / 2;
        const mid = (a + a1) / 2;
        // waagerechter Platz im Ringstück: oben/unten entlang des Bogens, links/rechts nur die Ringbreite
        const avail = Math.abs(Math.sin(mid)) > 0.5 ? (R1 - R0 - 4) : Math.min((a1 - a) * rr * 0.8, 90);
        const room = Math.floor(Math.min(avail, 90) / 5.4); // Platz für so viele Zeichen
        if (a1 - a > 0.3 && room >= 4) {
          const tx = svgEl('text', { x: (rr * Math.sin(mid)).toFixed(1), y: (-rr * Math.cos(mid)).toFixed(1), class: 'du-lbl', 'text-anchor': 'middle', 'dominant-baseline': 'middle' });
          tx.textContent = k.n.length > room ? k.n.slice(0, room - 1) + '…' : k.n;
          svg.appendChild(tx);
        }
      }
      a = a1;
    });
    const center = svgEl('circle', { r: R0 - 3, class: 'du-center' + (stack.length > 1 ? ' du-click' : '') });
    center.addEventListener('click', () => { if (stack.length > 1) up(stack.length - 2); });
    svg.appendChild(center);
    const t1 = svgEl('text', { y: -8, class: 'du-ctitle', 'text-anchor': 'middle' }); t1.textContent = v === root ? root.n : (v.n.length > 16 ? v.n.slice(0, 15) + '…' : v.n);
    const t2 = svgEl('text', { y: 14, class: 'du-csize', 'text-anchor': 'middle' }); t2.textContent = fmtBytes(v.s);
    svg.append(t1, t2);
    if (stack.length > 1) { const t3 = svgEl('text', { y: 32, class: 'du-cback', 'text-anchor': 'middle' }); t3.textContent = tr('↑ zurück'); svg.appendChild(t3); }
    chartBox.replaceChildren(svg, tip);

    // Liste daneben: genaue Werte, sortiert, mit Balken (klickbar wie das Diagramm)
    const rows = kidsOf(v).map((k, i) => ({ ...k, _i: i, _pct: (100 * k.s) / total }));
    const t = table([
      { key: 'n', label: 'Name', render: (r) => h('span.row', { style: { gap: '6px', flexWrap: 'nowrap' } },
        h('span.du-key', { style: { background: colorOf(r, r._i) } }), icon(r.f ? 'file' : 'folder'),
        r.k || r.m ? h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); open(r); } }, r.n) : h('span.raw', r.n),
        r.x ? h('span.badge', { title: 'Teilweise nicht lesbar (Zugriff verweigert)' }, 'teilweise') : null, r.m && !r.k ? h('span.muted.small', ' ▸ genauer') : null) },
      { key: 's', label: 'Größe', cls: 'right', render: (r) => fmtBytes(r.s) },
      { key: '_pct', label: 'Anteil', render: (r) => h('div.row', { style: { gap: '6px', flexWrap: 'nowrap' } },
        h('div.du-bar', h('span', { style: { width: `${Math.max(0.5, r._pct)}%`, background: colorOf(r, r._i) } })),
        h('span.muted.small', `${r._pct.toLocaleString('de-DE', { maximumFractionDigits: 1 })} %`)) },
      { key: 'fc', label: 'Dateien', cls: 'right', render: (r) => (r.f || r.o ? '' : num(r.fc || 0)) },
      { key: '_x', label: '', nosort: true, render: (r) => (r.o || !canExplore(vpath) ? '' : h('button.small.ghost.du-ex', { type: 'button', title: r.f ? 'Im Explorer zeigen' : 'Im Explorer öffnen',
        onclick: (e) => { e.stopPropagation(); if (r.f) explore(vpath, r.n); else explore(join(vpath, r.n)); } }, icon('folder'))) },
    ], rows, { key: null, noun: 'Einträge', sort: 's', dir: -1, fill: false, search: false, columns: false, id: (r) => r.n,
      onRow: (r) => open(r),
      onContext: (r, e) => contextMenu(e.clientX, e.clientY, [
        r.k || r.m ? { label: 'Hineinzoomen', icon: 'list', disabled: !(r.k && r.k.length), onClick: () => open(r) } : null,
        !r.f && !r.o ? { label: 'Diesen Ordner genauer analysieren', icon: 'refresh', onClick: () => rescan(join(vpath, r.n)) } : null,
        !r.o && canExplore(vpath) ? { label: r.f ? 'Im Explorer zeigen' : 'Im Explorer öffnen', icon: 'folder', onClick: () => (r.f ? explore(vpath, r.n) : explore(join(vpath, r.n))) } : null,
        !r.o ? { label: 'Pfad kopieren', icon: 'coll', onClick: () => copyText(join(vpath, r.n)) } : null,
      ]) });
    side.replaceChildren(t.el);
  }
  draw();
}

// ---------------------------------------------------------------------------
// Ansicht: Repository (2.27) – Installationsdateien über POCKET KNIFE verwalten: Ordner, Hochladen (in Stücken, mit
// Fortsetzen), Umbenennen, Verschieben (Pfade in Paketen werden angepasst), Löschen (nur, was kein Paket benutzt).
// Geschrieben wird immer mit dem Repository-/Dienstkonto auf dem Server – der Browser hat keinen Zugriff auf die Freigabe.
// ---------------------------------------------------------------------------
let REPO_CLIP = null;  // { items: [voller Pfad …] } – „Ausschneiden“, dann im Zielordner „Einfügen“
const repoJoin = (a, b) => (a ? `${a}\\${b}` : b);
const repoParent = (p) => { const i = String(p || '').lastIndexOf('\\'); return i < 0 ? '' : p.slice(0, i); };
const repoBase = (p) => { const s = String(p || ''); const i = s.lastIndexOf('\\'); return i < 0 ? s : s.slice(i + 1); };
const unixDate = (t) => { if (!t) return ''; const d = new Date(t * 1000); const z = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; };

// Fehler „wird noch benutzt“ (409): Liste der Pakete zeigen statt eines langen Toasts
function repoBlockedDlg(e, title = 'Nicht möglich') {
  const used = e && e.info && e.info.used_by;
  if (!used || !used.length) { toast(e.message, 'err'); return; }
  modal(title, h('div', h('p', tr('Diese Dateien bzw. Ordner werden noch benutzt von:')),
    h('ul', { style: { maxHeight: '300px', overflow: 'auto' } }, used.slice(0, 50).map((u) => h('li', u))),
    used.length > 50 ? h('p.muted.small', fmtT('… und {0} weitere', used.length - 50)) : null,
    h('p.muted.small', tr('Erst die Pakete anpassen (andere Dateien wählen) oder löschen – dann lässt sich das hier löschen bzw. überschreiben.'))),
  [{ label: 'OK', cls: 'primary' }]);
}

// Vorschau eines Umbaus (Verschieben/Umbenennen/Assistent): betroffene Pakete mit altem und neuem Pfad
function repoPlanView(pv) {
  const els = [];
  if (pv.problems && pv.problems.length) {
    els.push(h('div.alert.err', h('b', tr('Geht so nicht:')), h('ul', pv.problems.map((x) => h('li', x)))));
  }
  if (pv.create_dest) els.push(h('p', fmtT('Neuer Ordner wird angelegt: {0}', pv.create_dest)));
  if (pv.pairs && pv.pairs.length) {
    els.push(h('h3', { style: { margin: '8px 0 4px' } }, pv.pairs.length === 1 ? tr('1 Eintrag') : fmtT('{0} Einträge', pv.pairs.length)));
    els.push(h('div.mono.small', { style: { maxHeight: '140px', overflow: 'auto' } }, pv.pairs.slice(0, 200).map((x) => h('div.raw', `${x.old}  →  ${x.new}`))));
  }
  if (pv.packages && pv.packages.length) {
    els.push(h('h3', { style: { margin: '12px 0 4px' } }, fmtT('{0} Paket(e) werden angepasst', pv.packages.length)));
    els.push(h('table.tbl', { style: { width: '100%', tableLayout: 'fixed' } },
      h('thead', h('tr', h('th', { style: { width: '30%' } }, 'Paket'), h('th', 'Pfad bisher → neu'))),
      h('tbody', pv.packages.slice(0, 300).map((p) => h('tr', h('td.wrap', h('span.raw', p.name), p.tenant && p.tenant !== 'zentral' ? h('span.muted.small', ` (${p.tenant})`) : null),
        h('td.wrap.small.mono', (p.changes || []).map((c) => h('div.raw', `${c.old} → ${c.new}`))))))));
  }
  if (pv.tenants && pv.tenants.length) els.push(h('p', tr('Mandantenordner: '), pv.tenants.map((t) => `${t.name}: ${t.old} → ${t.new}`).join(' · ')));
  if (pv.deployments && pv.deployments.length) els.push(h('p', fmtT('{0} geplante Bereitstellung(en) werden angepasst.', pv.deployments.length)));
  if (pv.settings && pv.settings.length) els.push(h('p', tr('Einstellung winget-Offline-Kopie wird angepasst.')));
  const db = (pv.packages || []).length + (pv.tenants || []).length + (pv.deployments || []).length + (pv.settings || []).length;
  if (!(pv.problems || []).length) {
    els.push(h('p.muted.small', { style: { marginTop: '10px' } }, db
      ? tr('Vor dem Ausführen legt POCKET KNIFE automatisch eine Sicherung an. Geht unterwegs etwas schief, wird alles zurückgedreht (Dateien und Pakete).')
      : tr('Kein Paket benutzt diese Einträge – es werden nur die Dateien verschoben.')));
  }
  return h('div', els);
}

// Umbau ausführen: erst Vorschau (dry_run), bei betroffenen Paketen bestätigen lassen, dann ausführen
async function repoRunPlan(preview, execute, opt = {}) {
  let pv;
  try { pv = await preview(); } catch (e) { if (e.status === 409) repoBlockedDlg(e); else toast(e.message, 'err'); return null; }
  if (pv.problems && pv.problems.length) {
    modal(opt.title || 'Verschieben nicht möglich', repoPlanView(pv), [{ label: 'OK', cls: 'primary' }], { wide: true });
    return null;
  }
  const touches = (pv.packages || []).length + (pv.tenants || []).length + (pv.deployments || []).length + (pv.settings || []).length;
  if (touches || opt.confirm) {
    const ok = await new Promise((res) => modal(opt.title || 'Vorschau', repoPlanView(pv), [
      { label: 'Abbrechen', onClick: () => res(false) },
      { label: opt.okLabel || 'Ausführen', cls: 'primary', onClick: () => res(true) }], { wide: true, onClose: () => res(false) }));
    if (!ok) return null;
  }
  let r;
  try { r = await execute(); } catch (e) { if (e.status === 409) repoBlockedDlg(e); else toast(e.message, 'err'); return null; }
  if (r.problems && r.problems.length) { modal(opt.title || 'Nicht ausgeführt', repoPlanView(r), [{ label: 'OK' }], { wide: true }); return null; }
  toast(r.packages && r.packages.length ? fmtT('Erledigt – {0} Paket(e) angepasst', r.packages.length) + (r.backup ? ' · ' + fmtT('Sicherung: {0}', r.backup) : '') : tr('Erledigt'), 'ok');
  return r;
}

// Dateien aus einem Ziehen vom Desktop einsammeln (auch ganze Ordner)
async function droppedFiles(dt) {
  const out = [];
  const items = [...(dt.items || [])].map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null)).filter(Boolean);
  if (!items.length) return [...(dt.files || [])].map((f) => ({ file: f, rel: f.name }));
  const readAll = (reader) => new Promise((res) => {
    const acc = [];
    const next = () => reader.readEntries((ents) => { if (!ents.length) res(acc); else { acc.push(...ents); next(); } }, () => res(acc));
    next();
  });
  async function walk(ent, prefix) {
    if (ent.isFile) {
      const f = await new Promise((res) => ent.file(res, () => res(null)));
      if (f) out.push({ file: f, rel: prefix + f.name });
    } else if (ent.isDirectory) {
      out.push({ dir: prefix + ent.name });
      for (const c of await readAll(ent.createReader())) await walk(c, prefix + ent.name + '/');
    }
  }
  for (const e of items) await walk(e, '');
  return out;
}

// Upload-Warteschlange: eine Datei nach der anderen, in Stücken; Abbruch/Netzfehler → „Fortsetzen“ an derselben Stelle
function repoUploader(getInfo, onChange) {
  const box = h('div.up-box', { style: { display: 'none' } });
  const list = h('div.up-list');
  const head = h('div.row', { style: { gap: '8px', alignItems: 'center' } });
  box.append(head, list);
  const q = [];
  let running = false;
  let overwriteAll = null;  // null = fragen, true = alle überschreiben, false = alle überspringen
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function drawHead() {
    const open = q.filter((x) => !['done', 'skipped', 'cancelled'].includes(x.state)).length;
    head.replaceChildren(h('b', open ? fmtT('Hochladen: {0} offen', open) : tr('Hochladen abgeschlossen')),
      h('span.muted.small', tr('Die Dateien laufen über den POCKET-KNIFE-Server (Docker-Host) zum Dateiserver – große Dateien belasten beide Netzwege.')),
      h('div.spacer'),
      open ? '' : btn('Liste leeren', () => { q.length = 0; list.replaceChildren(); box.style.display = 'none'; }, { cls: 'small ghost' }));
  }
  function row(it) {
    const bar = h('div.up-bar', h('div', { style: { width: '0%' } }));
    const st = h('span.small');
    const act = h('span.row', { style: { gap: '4px' } });
    it.el = h('div.up-row', h('span.raw.up-name', { title: repoJoin(it.folder, it.name) }, it.name), h('span.muted.small.raw', it.folder || '\\'), bar, st, act);
    it.draw = () => {
      const pct = it.size ? Math.floor((it.sent / it.size) * 100) : (it.state === 'done' ? 100 : 0);
      bar.firstChild.style.width = `${pct}%`;
      bar.className = 'up-bar ' + (it.state === 'error' || it.state === 'paused' ? 'err' : it.state === 'done' ? 'ok' : '');
      const txt = { wait: tr('wartet'), run: `${pct}% · ${fmtBytes(it.sent)} / ${fmtBytes(it.size)}` + (it.speed ? ` · ${fmtBytes(it.speed)}/s` : ''),
        done: tr('fertig'), skipped: tr('übersprungen'), cancelled: tr('abgebrochen'), paused: `${tr('unterbrochen')} (${pct}%) – ${it.msg || ''}`,
        error: it.msg || tr('Fehler') }[it.state] || '';
      st.textContent = txt;
      st.className = 'small ' + (it.state === 'error' || it.state === 'paused' ? 'err-text' : it.state === 'done' ? 'ok-text' : 'muted');
      act.replaceChildren(...[
        it.state === 'paused' ? btn('Fortsetzen', () => { it.state = 'wait'; it.draw(); pump(); }, { cls: 'small' }) : null,
        ['wait', 'run', 'paused'].includes(it.state) ? btn('Abbrechen', () => cancel(it), { cls: 'small ghost' }) : null].filter(Boolean));
    };
    it.draw();
    list.appendChild(it.el);
  }
  async function cancel(it) {
    it.cancel = true;
    if (it.state !== 'run') { it.state = 'cancelled'; it.draw(); drawHead(); }
    if (it.token) { try { await DEL(`/api/repository/upload/${it.token}`); } catch (e) { /* egal */ } }
  }
  function add(files, folder) {
    const info = getInfo() || {};
    const types = (info.types || '').split(',').map((x) => x.trim().toLowerCase()).filter(Boolean);
    for (const f of files) {
      const it = { file: f.file, name: f.file.name, folder: f.folder !== undefined ? f.folder : folder, size: f.file.size, sent: 0, state: 'wait' };
      const ext = it.name.includes('.') ? it.name.split('.').pop().toLowerCase() : '';
      if (info.max_mb && it.size > info.max_mb * 1048576) { it.state = 'error'; it.msg = fmtT('zu groß (höchstens {0} MB)', info.max_mb); }
      else if (types.length && !types.includes(ext)) { it.state = 'error'; it.msg = tr('Dateityp nicht freigegeben'); }
      q.push(it); row(it);
    }
    box.style.display = '';
    drawHead();
    pump();
  }
  async function askOverwrite(it) {
    if (overwriteAll !== null) return overwriteAll;
    return new Promise((res) => modal(tr('Datei gibt es schon'), h('p', fmtT('„{0}“ gibt es in {1} schon. Überschreiben?', it.name, it.folder || '\\')), [
      { label: 'Überspringen', onClick: () => res(false) },
      { label: 'Alle überspringen', onClick: () => { overwriteAll = false; res(false); } },
      { label: 'Alle überschreiben', cls: 'danger', onClick: () => { overwriteAll = true; res(true); } },
      { label: 'Überschreiben', cls: 'danger', onClick: () => res(true) }], { onClose: () => res(false) }));
  }
  async function one(it) {
    it.state = 'run'; it.draw();
    let s;
    try { s = await POST('/api/repository/upload', { folder: it.folder, name: it.name, size: it.size, overwrite: !!it.overwrite }); }
    catch (e) {
      if (e.info && e.info.exists && !it.overwrite) {
        if (await askOverwrite(it)) { it.overwrite = true; return one(it); }
        it.state = 'skipped'; it.draw(); return;
      }
      if (e.status === 409 && e.info && e.info.used_by) { it.state = 'error'; it.msg = tr('wird von einem Paket benutzt – nicht überschrieben'); it.draw(); repoBlockedDlg(e); return; }
      it.state = 'error'; it.msg = e.message; it.draw(); return;
    }
    it.token = s.token; it.sent = s.received || 0;
    const chunk = s.chunk || 8 * 1048576;
    let t0 = Date.now(), b0 = it.sent, fails = 0;
    while (it.sent < it.size) {
      if (it.cancel) { it.state = 'cancelled'; it.draw(); return; }
      const end = Math.min(it.size, it.sent + chunk);
      try {
        const r = await api(`/api/repository/upload/${it.token}?offset=${it.sent}`, { method: 'PUT', body: it.file.slice(it.sent, end), raw: true });
        it.sent = r.received; fails = 0;
        const dt = (Date.now() - t0) / 1000; if (dt > 1) it.speed = (it.sent - b0) / dt;
        it.draw();
      } catch (e) {
        if (e.info && e.info.received !== undefined) { it.sent = e.info.received; continue; }  // Server hat einen anderen Stand
        if (e.info && e.info.restart) { it.token = null; it.sent = 0; return one(it); }
        if (e.status === 404 || e.status === 403 || e.status === 400) { it.state = 'error'; it.msg = e.message; it.draw(); return; }
        fails += 1;
        if (fails > 4) { it.state = 'paused'; it.msg = e.message; it.draw(); return; }
        await sleep(1500 * fails);
        t0 = Date.now(); b0 = it.sent;
      }
    }
    let fin;
    try { fin = await POST(`/api/repository/upload/${it.token}/finish`, {}); }
    catch (e) {
      if (e.info && e.info.restart && !it.restarted) { it.restarted = true; it.token = null; it.sent = 0; return one(it); }  // Zwischendatei kaputt: einmal neu
      const d = e.info && e.info.duplicate;
      if (d && e.info.can_override && e.info.token) {
        const go = await new Promise((res) => modal(tr('Gibt es schon'), h('div', h('p', e.message),
          h('ul', d.map((x) => h('li', h('a', { href: '#', onclick: (ev) => { ev.preventDefault(); packageDlg(x.id); } }, x.name), h('span.muted.small', ` (${x.scope})`))))), [
          { label: 'Abbrechen', onClick: () => res(false) },
          { label: 'Trotzdem hochladen', cls: 'danger', onClick: () => res(true) }], { onClose: () => res(false) }));
        if (go) {
          try { fin = await POST(`/api/repository/upload/${it.token}/finish`, { allow_duplicate: true }); }
          catch (e2) { it.state = 'error'; it.msg = e2.message; it.draw(); return; }
        } else { await cancel(it); it.state = 'cancelled'; it.draw(); return; }
      } else {
        if (d) modal(tr('Gibt es schon'), h('div', h('p', e.message), h('ul', d.map((x) => h('li', h('span.raw', x.name), h('span.muted.small', ` (${x.scope})`))))), [{ label: 'OK', cls: 'primary' }]);
        it.state = 'error'; it.msg = d ? tr('gleiche Software gibt es schon') : e.message; it.draw(); return;
      }
    }
    it.state = 'done'; it.sha = fin.sha256; it.draw();
    if (fin.in_package_folder) toast(fmtT('„{0}“ liegt im Paketordner von {1} – das Paket zeigt „neu im Paketordner“, bis jemand den neuen Stand übernimmt.', it.name, fin.in_package_folder), 'err');
    if (fin.same_file_at && fin.same_file_at.length) toast(fmtT('Hinweis: dieselbe Datei liegt schon unter {0}', fin.same_file_at.join(', ')), 'ok');
  }
  async function pump() {
    if (running) return;
    running = true;
    try {
      for (;;) {
        const it = q.find((x) => x.state === 'wait');
        if (!it) break;
        try { await one(it); } catch (e) { it.state = 'error'; it.msg = e.message; it.draw(); }
        drawHead();
        if (onChange) onChange();
      }
    } finally { running = false; overwriteAll = null; drawHead(); }
  }
  return { el: box, add, busy: () => running };
}

// Ordner wählen (für „Verschieben nach …“) – derselbe Browser wie im Paket-Editor
function repoPickFolder(start) {
  return new Promise((res) => repoBrowser((folder) => res(folder), start, { folderOnly: true, title: 'Zielordner wählen', onCancel: () => res(null) }));
}

async function viewRepository(_parts, params) {
  const info = await GET('/api/repository/info').catch((e) => ({ error: e.message }));
  const admin = !isOU();
  setTop('Repository', 'Installationsdateien für Pakete – hochladen, Ordner anlegen, umbenennen, verschieben, löschen. Pfade in Paketen werden beim Verschieben automatisch angepasst.',
    admin ? btn('Repository umstrukturieren …', () => repoRestructureDlg(() => load(cur)), { icon: 'folder', title: tr('Viele Ordner auf einmal in einen neuen Hauptordner verschieben – mit Vorschau, Sicherung und Zurückdrehen bei einem Fehler') }) : null);
  const crumbs = h('div.fm-crumbs');
  const tools = h('div.row', { style: { gap: '6px', flexWrap: 'wrap' } });
  const listBox = h('div.fx-list');
  const status = h('span.muted.small');
  const limits = h('div.muted.small', { style: { margin: '2px 0 6px' } });
  let cur = params.get('path') || '';
  let rows = [];
  let tbl = null;
  const roots = info.roots || null;  // OU-Admin: nur die eigenen Mandantenordner
  const isRoot = (p) => (roots ? roots.some((r) => r.toLowerCase() === String(p || '').toLowerCase()) : !p);
  const inScope = (p) => !roots || roots.some((r) => { const a = String(p || '').toLowerCase(); const b = r.toLowerCase(); return a === b || a.startsWith(b + '\\'); });
  if (!cur && roots && roots.length) cur = roots[0];
  const up = repoUploader(() => info, () => load(cur, true));
  // Wer etwas zieht: interne Einträge (Verschieben) oder Dateien vom Desktop (Hochladen)
  let dragItems = null;
  const isFileDrag = (e) => [...((e.dataTransfer && e.dataTransfer.types) || [])].includes('Files');

  if (info.error) { setContent(h('div.empty', info.error)); return; }
  const lim = [];
  if (info.max_mb) lim.push(fmtT('Dateien bis {0} MB', info.max_mb));
  (info.tenants || []).forEach((t) => lim.push(t.quota_mb ? fmtT('{0}: {1} von {2} MB belegt', t.name, t.used_mb ?? '?', t.quota_mb) : fmtT('{0}: Ordner {1}', t.name, t.folder)));
  limits.replaceChildren(lim.join(' · '), info.types ? h('span', ' · ', h('span', { style: { textDecoration: 'underline dotted', cursor: 'help' },
    title: info.types.split(',').join(', ') }, tr('erlaubte Dateitypen'))) : '');

  setContent(h('div.fx-page', h('div.card.fx-card',
    h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '4px' } }, tools, h('div.spacer'), status),
    limits, crumbs, up.el, listBox)));

  // Ablegen vom Desktop auf die Liste = in den geöffneten Ordner hochladen
  listBox.addEventListener('dragover', (e) => { if (isFileDrag(e)) { e.preventDefault(); listBox.classList.add('dropto'); } });
  listBox.addEventListener('dragleave', (e) => { if (!listBox.contains(e.relatedTarget)) listBox.classList.remove('dropto'); });
  listBox.addEventListener('drop', async (e) => {
    listBox.classList.remove('dropto');
    if (!isFileDrag(e)) return;
    e.preventDefault();
    uploadEntries(await droppedFiles(e.dataTransfer), cur);
  });

  async function uploadEntries(entries, folder) {
    // entries: {file, rel: „A/B/x.msi“} bzw. {dir: „A/B“} – erst die Ordner anlegen, dann die Dateien hochladen
    const dirs = new Set(entries.filter((x) => x.dir).map((x) => x.dir.replace(/\//g, '\\')));
    const files = entries.filter((x) => x.file).map((x) => {
      const relDir = repoParent(String(x.rel || x.file.name).replace(/\//g, '\\'));
      if (relDir) dirs.add(relDir);
      return { file: x.file, folder: repoJoin(folder, relDir) };
    });
    if (dirs.size) {
      try { await POST('/api/repository/mkdir', { folder, dirs: [...dirs] }); }
      catch (e) { toast(e.message, 'err'); return; }
    }
    if (files.length) up.add(files, folder);
    else if (dirs.size) load(cur, true);
  }

  let loadGen = 0;
  const navGen = S.nav;
  async function load(path, keepSel = false) {
    const gen = ++loadGen;
    const want = path || '';
    if (!keepSel) listBox.replaceChildren(h('div.muted', { style: { padding: '12px' } }, 'Lädt …'));
    let d;
    try { d = await GET(`/api/repository?used=1&path=${encodeURIComponent(want)}`); }
    catch (e) { if (gen !== loadGen || navGen !== S.nav) return; listBox.replaceChildren(h('div.empty', e.message)); return; }
    if (gen !== loadGen || navGen !== S.nav) return;
    cur = d.path || '';
    try { history.replaceState(null, '', `#/repository${cur ? '?path=' + encodeURIComponent(cur) : ''}`); } catch (e) { /* egal */ }
    rows = d.items.map((x) => ({ ...x, path: repoJoin(cur, x.name) }));
    drawCrumbs(d.root);
    drawTools();
    drawList(keepSel);
  }

  function crumbDrop(el, target) {
    el.addEventListener('dragover', (e) => { if ((dragItems && inScope(target)) || isFileDrag(e)) { e.preventDefault(); el.classList.add('dropto'); } });
    el.addEventListener('dragleave', () => el.classList.remove('dropto'));
    el.addEventListener('drop', async (e) => {
      el.classList.remove('dropto');
      e.preventDefault(); e.stopPropagation();
      if (isFileDrag(e)) { uploadEntries(await droppedFiles(e.dataTransfer), target); return; }
      if (dragItems) { const it = dragItems; dragItems = null; moveTo(it, target); }
    });
    return el;
  }
  function drawCrumbs(root) {
    const segs = cur ? cur.split('\\') : [];
    const els = [];
    let start = 0;
    if (roots) {
      // OU-Admin: Pfad beginnt bei seinem Mandantenordner
      const r = roots.find((x) => cur.toLowerCase() === x.toLowerCase() || cur.toLowerCase().startsWith(x.toLowerCase() + '\\')) || roots[0] || '';
      start = r ? r.split('\\').length : 0;
      els.push(crumbDrop(h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); load(r); } }, r || '\\'), r));
      if (roots.length > 1) els.push(h('span.muted.small', ` (${tr('weitere')}: `), ...roots.filter((x) => x !== r).map((x) => h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); load(x); } }, x + ' ')), h('span.muted.small', ')'));
    } else {
      els.push(crumbDrop(h('a', { href: '#', title: root || '', onclick: (e) => { e.preventDefault(); load(''); } }, 'Repository'), ''));
    }
    let acc = segs.slice(0, start).join('\\');
    segs.slice(start).forEach((s, i, arr) => {
      acc = repoJoin(acc, s);
      const p = acc;
      els.push(h('span.muted', ' \\ '));
      els.push(i === arr.length - 1 ? h('b.raw', s) : crumbDrop(h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); load(p); } }, s), p));
    });
    crumbs.replaceChildren(...els);
  }

  function drawTools() {
    const fileIn = h('input', { type: 'file', multiple: true, style: { display: 'none' }, onchange: (e) => { up.add([...e.target.files].map((f) => ({ file: f })), cur); e.target.value = ''; } });
    const dirIn = h('input', { type: 'file', multiple: true, webkitdirectory: true, style: { display: 'none' }, onchange: (e) => {
      const entries = [...e.target.files].map((f) => ({ file: f, rel: f.webkitRelativePath || f.name }));
      e.target.value = '';
      uploadEntries(entries, cur);
    } });
    tools.replaceChildren(...[
      btn('▲ Übergeordnet', () => load(repoParent(cur)), { cls: 'small ghost', disabled: isRoot(cur) }),
      btn('Aktualisieren', () => load(cur), { cls: 'small ghost', icon: 'refresh' }),
      btn('Neuer Ordner', () => newFolder(), { cls: 'small', icon: 'folder' }),
      h('span', btn('Dateien hochladen', () => fileIn.click(), { cls: 'small primary', icon: 'up', title: tr('Oder Dateien bzw. Ordner einfach vom Desktop in die Liste ziehen') }), fileIn),
      h('span', btn('Ordner hochladen', () => dirIn.click(), { cls: 'small', icon: 'up' }), dirIn),
      REPO_CLIP && REPO_CLIP.items.length ? btn(fmtT('Einfügen ({0}, verschieben)', REPO_CLIP.items.length), () => { const it = REPO_CLIP.items; moveTo(it, cur, () => { REPO_CLIP = null; }); }, { cls: 'small primary', icon: 'send' }) : null,
      REPO_CLIP && REPO_CLIP.items.length ? btn('✕', () => { REPO_CLIP = null; drawTools(); }, { cls: 'small ghost', title: tr('Ausschneiden verwerfen') }) : null].filter(Boolean));
  }

  async function newFolder() {
    const n = (prompt(tr('Name des neuen Ordners')) || '').trim();
    if (!n) return;
    if (await run(() => POST('/api/repository/mkdir', { folder: cur, name: n }), 'Ordner angelegt') !== undefined) load(cur, true);
  }

  async function moveTo(items, dest, after) {
    items = items.filter((p) => repoParent(p).toLowerCase() !== String(dest).toLowerCase() && p.toLowerCase() !== String(dest).toLowerCase());
    if (!items.length) return;
    if (items.some((p) => String(dest).toLowerCase().startsWith(p.toLowerCase() + '\\'))) { toast(tr('Ein Ordner lässt sich nicht in sich selbst verschieben.'), 'err'); return; }
    const r = await repoRunPlan(() => POST('/api/repository/move', { items, dest, dry_run: true }),
      () => POST('/api/repository/move', { items, dest, dry_run: false }), { title: fmtT('Verschieben nach {0}', dest || '\\') });
    if (r) { if (after) after(); load(cur, true); }
  }

  async function doRename(r) {
    const n = (prompt(tr('Neuer Name'), r.name) || '').trim();
    if (!n || n === r.name) return;
    const res = await repoRunPlan(() => POST('/api/repository/rename', { path: r.path, name: n, dry_run: true }),
      () => POST('/api/repository/rename', { path: r.path, name: n }), { title: fmtT('Umbenennen: {0}', r.name) });
    if (res) load(cur, true);
  }

  async function doDelete(sel) {
    const used = sel.filter((x) => (x.used_by || []).length);
    if (used.length) { repoBlockedDlg({ message: '', info: { used_by: [...new Set(used.flatMap((x) => x.used_by))] } }, 'Löschen nicht möglich'); return; }
    const names = sel.map((x) => x.name);
    if (!(await confirmDlg(fmtT('{0} im Repository endgültig löschen? Ordner werden mit ihrem Inhalt gelöscht. Das lässt sich nicht rückgängig machen.',
      names.length === 1 ? `„${names[0]}“` : fmtT('{0} Einträge', names.length)), 'Löschen', true))) return;
    try { await POST('/api/repository/delete', { items: sel.map((x) => x.path) }); toast(tr('Gelöscht'), 'ok'); }
    catch (e) { if (e.status === 409) repoBlockedDlg(e, 'Löschen nicht möglich'); else toast(e.message, 'err'); }
    load(cur, true);
  }

  async function showUsage(r) {
    const u = await run(() => GET(`/api/repository/usage?path=${encodeURIComponent(r.path)}`));
    if (!u) return;
    modal(fmtT('Wer benutzt „{0}“?', r.name), u.used_by.length ? h('ul', u.used_by.map((x) => h('li', x))) : h('p', tr('Nichts – kein Paket, keine geplante Bereitstellung und kein Mandant benutzt diesen Eintrag.')), [{ label: 'OK', cls: 'primary' }]);
  }

  function drawList(keepSel) {
    const prev = keepSel && tbl ? new Set(tbl.selectedIds()) : null;
    status.textContent = fmtT('{0} Einträge', rows.length);
    const open = (r) => { if (r.dir && !r.link) load(r.path); };
    tbl = table([
      { key: 'name', label: 'Name', render: (r) => h('span.row', { style: { gap: '6px', flexWrap: 'nowrap' } },
        h('span.ic', { style: { color: r.dir ? 'var(--accent)' : 'var(--muted)' } }, icon(r.dir ? 'folder' : 'file')),
        r.dir && !r.link ? h('a.raw', { href: '#', onclick: (e) => { e.preventDefault(); open(r); } }, r.name) : h('span.raw', r.name),
        r.link ? h('span.badge', { title: tr('Verknüpfung (Junction/Symlink) – POCKET KNIFE folgt ihr nicht und schreibt dort nicht') }, '↗') : null,
        (r.used_by || []).length ? h('span.badge.info', { title: r.used_by.map((x) => tr(x)).join('\n') }, tr('benutzt')) : null) },
      { key: 'size', label: 'Größe', cls: 'right', render: (r) => (r.dir ? '' : fmtBytes(r.size)), sort: (r) => (r.dir ? -1 : r.size) },
      { key: 'mtime', label: 'Geändert', render: (r) => fmtDate(unixDate(r.mtime)), sort: (r) => r.mtime || 0 },
      { key: 'used', label: 'Benutzt von', value: (r) => (r.used_by || []).join(', '), render: (r) => h('span.muted.small', (r.used_by || []).slice(0, 2).map((x) => tr(x)).join(', ') + ((r.used_by || []).length > 2 ? ` +${r.used_by.length - 2}` : '')) },
    ], rows, { key: 'repo-view', noun: 'Einträge', sort: 'name', selectable: true, id: (r) => r.path, placeholder: tr('In diesem Ordner filtern …'),
      empty: tr('Leerer Ordner – Dateien hierher ziehen oder „Dateien hochladen“.'),
      onRow: (r) => open(r),
      dragData: (sel, e) => { dragItems = sel.map((x) => x.path); try { e.dataTransfer.setData('text/plain', dragItems.join('\n')); } catch (x) { /* egal */ } e.dataTransfer.effectAllowed = 'move'; },
      dragEnd: () => { setTimeout(() => { dragItems = null; }, 0); },
      canDrop: (r, e) => r.dir && !r.link && ((dragItems && !dragItems.includes(r.path)) || isFileDrag(e)),
      onDrop: async (r, e) => {
        if (isFileDrag(e)) { uploadEntries(await droppedFiles(e.dataTransfer), r.path); return; }
        const it = dragItems; dragItems = null; if (it) moveTo(it, r.path);
      },
      onContext: (r, e, sel) => {
        const many = sel.length > 1;
        contextMenu(e.clientX, e.clientY, [
          !many && r.dir && !r.link ? { label: 'Öffnen', icon: 'folder', onClick: () => open(r) } : null,
          { label: `Verschieben nach …${many ? ` (${sel.length})` : ''}`, icon: 'send', onClick: async () => {
            const dest = await repoPickFolder(cur);
            if (dest !== undefined && dest !== null) moveTo(sel.map((x) => x.path), dest);
          } },
          { label: `Ausschneiden${many ? ` (${sel.length})` : ''}`, icon: 'send', onClick: () => { REPO_CLIP = { items: sel.map((x) => x.path) }; toast(tr('Ausgeschnitten – im Zielordner „Einfügen“'), 'ok'); drawTools(); } },
          !many ? { label: 'Umbenennen', icon: 'edit', onClick: () => doRename(r) } : null,
          { label: 'Neuer Ordner', icon: 'folder', onClick: () => newFolder() },
          !many ? { label: 'Wer benutzt das?', icon: 'box', onClick: () => showUsage(r) } : null,
          '-',
          { label: `Löschen${many ? ` (${sel.length})` : ''}`, icon: 'trash', cls: 'danger', onClick: () => doDelete(sel) },
          '-',
          { label: 'Pfad kopieren', onClick: () => copyText(r.path) },
        ]);
      },
      onDelete: (sel) => doDelete(sel) });
    listBox.replaceChildren(tbl.el);
    if (prev && prev.size) tbl.select([...prev].filter((p) => rows.some((x) => x.path === p)));
  }

  load(cur);
}

// Assistent „Repository umstrukturieren“ (Hauptadmin): Ordner auf einmal in einen neuen Hauptordner verschieben
async function repoRestructureDlg(after) {
  let d;
  try { d = await GET('/api/repository?used=1&path='); } catch (e) { toast(e.message, 'err'); return; }
  const dest = h('input', { value: 'Zentral', style: { flex: 1 } });
  const pick = btn('Wählen …', async () => { const f = await repoPickFolder(''); if (f !== undefined && f !== null) { dest.value = f; drawList(); } }, { cls: 'small' });
  const chosen = new Set();
  const list = h('div.list-pick', { style: { maxHeight: '300px' } });
  const isTenantBase = (n) => n.toLowerCase() === 'mandanten';
  function drawList() {
    const dk = dest.value.trim().replace(/^\\+|\\+$/g, '').toLowerCase();
    list.replaceChildren(...d.items.filter((x) => x.name.toLowerCase() !== dk.split('\\')[0] || dk.includes('\\')).map((x) => h('label.chk', { style: { display: 'flex', gap: '6px' } },
      h('input', { type: 'checkbox', checked: chosen.has(x.name), onchange: (e) => { if (e.target.checked) chosen.add(x.name); else chosen.delete(x.name); } }),
      icon(x.dir ? 'folder' : 'file'), h('span.raw', x.name), (x.used_by || []).length ? h('span.muted.small', ` – ${fmtT('benutzt von {0}', x.used_by.length)}`) : null,
      isTenantBase(x.name) ? h('span.muted.small', ` – ${tr('Mandantenordner (meist nicht mitverschieben)')}`) : null)));
  }
  dest.addEventListener('input', drawList);
  drawList();
  const all = btn('Alle markieren', () => { d.items.forEach((x) => { if (!isTenantBase(x.name) && x.name.toLowerCase() !== dest.value.trim().toLowerCase()) chosen.add(x.name); }); drawList(); }, { cls: 'small ghost' });
  const none = btn('Keine', () => { chosen.clear(); drawList(); }, { cls: 'small ghost' });
  modal('Repository umstrukturieren', h('div.form',
    h('p.muted', tr('Empfohlen: ein Ordner für die zentralen Pakete (z. B. „Zentral“) und „Mandanten“ mit je einem Unterordner pro Mandant. Die Pfade in allen Paketen werden angepasst; vorher wird automatisch gesichert, bei einem Fehler wird alles zurückgedreht.')),
    field('1. Neuer Hauptordner', h('div.row', { style: { gap: '6px' } }, dest, pick), tr('Gibt es ihn noch nicht, wird er angelegt.')),
    field('2. Was soll hinein?', h('div', h('div.row', { style: { gap: '6px', marginBottom: '6px' } }, all, none), list))), [
    { label: 'Abbrechen' },
    { label: '3. Vorschau …', cls: 'primary', close: false, onClick: async (close) => {
      const target = dest.value.trim().replace(/^\\+|\\+$/g, '');
      const items = [...chosen].filter((n) => n.toLowerCase() !== target.toLowerCase());
      if (!items.length) { toast(tr('Bitte mindestens einen Ordner markieren.'), 'err'); return; }
      const r = await repoRunPlan(() => POST('/api/repository/move', { items, dest: target, dry_run: true, create_dest: true }),
        () => POST('/api/repository/move', { items, dest: target, dry_run: false, create_dest: true }),
        { title: tr('Repository umstrukturieren – Vorschau'), okLabel: tr('4. Ausführen'), confirm: true });
      if (r) { close(); if (after) after(); }
    } }], { wide: true });
}

// Reparatur per Fingerabdruck: fehlende Dateien eines Pakets im Repository wiederfinden und Pfade übernehmen
async function repairDlg(p, onDone) {
  const r = await run(() => POST(`/api/packages/${p.id}/repair/suggest`));
  if (!r) return;
  if (!r.items.length) {
    modal('Datei suchen', h('div', h('p', r.missing ? tr('Keine passende Datei im Repository gefunden (gleiche Größe und gleicher Fingerabdruck).') : tr('Es fehlt nichts.')),
      r.not_found && r.not_found.length ? h('ul', r.not_found.map((x) => h('li.raw', x))) : null,
      h('p.muted.small', tr('Liegt die Datei woanders, das Paket von Hand anpassen. Neue Dateien sucht der Index nach spätestens 5 Minuten mit.'))), [{ label: 'OK', cls: 'primary' }]);
    return;
  }
  const chk = r.items.map(() => true);
  modal('Datei suchen – gefunden', h('div',
    h('p', tr('Diese Dateien bzw. Ordner wurden an einem anderen Ort gefunden. Übernehmen passt die Pfade in allen Paketen an, die sie benutzen.')),
    h('table.tbl', { style: { width: '100%', tableLayout: 'fixed' } }, h('thead', h('tr', h('th', { style: { width: '32px' } }, ''), h('th', 'Bisher'), h('th', 'Gefunden'), h('th', { style: { width: '28%' } }, 'Woran erkannt'))),
      h('tbody', r.items.map((x, i) => h('tr', h('td', h('input', { type: 'checkbox', checked: true, onchange: (e) => { chk[i] = e.target.checked; } })),
        h('td.wrap.small.raw', x.old), h('td.wrap.small.raw', x.new), h('td.wrap.small', x.how))))),
    r.not_found && r.not_found.length ? h('p.muted.small', tr('Nicht gefunden: '), r.not_found.join(', ')) : null), [
    { label: 'Abbrechen' },
    { label: 'Pfade übernehmen', cls: 'primary', onClick: async () => {
      const items = r.items.filter((_x, i) => chk[i]).map((x) => ({ old: x.old, new: x.new }));
      if (!items.length) return false;
      const res = await POST(`/api/packages/${p.id}/repair/apply`, { items });
      toast(fmtT('Repariert – {0} Paket(e) angepasst', res.packages.length), 'ok');
      if (onDone) onDone();
    } }], { wide: true });
}

// Speichern mit Duplikatsperre: Hauptadmins dürfen nach Rückfrage übersteuern
async function saveWithDup(send) {
  try { return await send({}); } catch (e) {
    const d = e.info && e.info.duplicate;
    if (!d) throw e;
    if (!e.info.can_override) {
      modal(tr('Gibt es schon'), h('div', h('p', e.message), h('ul', d.map((x) => h('li', h('span.raw', x.name), h('span.muted.small', ` (${x.scope})`))))), [{ label: 'OK', cls: 'primary' }]);
      return false;
    }
    const go = await new Promise((res) => modal(tr('Gibt es schon'), h('div', h('p', e.message), h('ul', d.map((x) => h('li', h('span.raw', x.name), h('span.muted.small', ` (${x.scope})`))))), [
      { label: 'Abbrechen', onClick: () => res(false) }, { label: 'Trotzdem speichern', cls: 'danger', onClick: () => res(true) }], { onClose: () => res(false) }));
    if (!go) return false;
    return send({ allow_duplicate: true });
  }
}

const VIEWS = {
  dashboard: viewDashboard, computers: viewComputers, computer: viewComputer, software: viewSoftware,
  help: viewHelp, packages: viewPackages, schedules: viewSchedules, deployments: viewDeployments, deployment: viewDeployment, rules: () => go('#/schedules'),
  reports: viewReports, logs: viewLogs, settings: viewSettings, tools: viewTools, scanners: viewScanners, diskusage: viewDiskUsage,
  files: viewFiles, eventlog: viewEventLog, kiosk: viewKiosk, repository: viewRepository,
};

// ---------------------------------------------------------------------------
// Abmeldung bei Inaktivität: Eingaben (Maus, Tastatur, auch in Fernsitzungen) werden höchstens einmal pro Minute
// gemeldet. Automatische Aktualisierungen zählen nicht. Kurz vor Ablauf erscheint ein Hinweis; der Server
// entscheidet (mehrere Tabs teilen sich dieselbe Sitzung).
// ---------------------------------------------------------------------------
const IDLE = { last: Date.now(), sent: 0, warn: null, checking: false };
function idleActivity() {
  IDLE.last = Date.now();
  if (IDLE.warn) { IDLE.warn.remove(); IDLE.warn = null; }
  if (S.me && Date.now() - IDLE.sent > 60000) {
    IDLE.sent = Date.now();
    api('/api/session/active', { method: 'POST', body: {} }).catch(() => {});
  }
}
['pointerdown', 'keydown', 'wheel', 'touchstart', 'mousemove'].forEach((ev) => window.addEventListener(ev, idleActivity, { capture: true, passive: true }));
setInterval(async () => {
  if (!S.me || IDLE.checking) return;
  const limit = (S.me.idle_minutes || 30) * 60000;
  if (Date.now() - IDLE.last < limit - 120000) return;  // in diesem Tab kürzlich aktiv – nichts zu prüfen
  IDLE.checking = true;
  try {
    const r = await api('/api/session/idle');  // 401 → Anmeldung mit Hinweis „wegen Inaktivität abgemeldet“
    if (r.remaining <= 100 && S.me) {
      const txt = `Abmeldung wegen Inaktivität in ${Math.max(0, Math.round(r.remaining))} s – zum Weiterarbeiten klicken oder eine Taste drücken.`;
      if (!IDLE.warn) {
        IDLE.warn = h('div.toast.warn', { style: { position: 'fixed', right: '16px', bottom: '16px', zIndex: 9999, maxWidth: '420px' } }, txt);
        document.body.appendChild(IDLE.warn);
      } else IDLE.warn.textContent = txt;
    }
  } catch (e) { /* abgemeldet oder Netzwerkfehler */ } finally { IDLE.checking = false; }
}, 15000);

window.addEventListener('hashchange', route);
(async function init() {
  setTheme(localStorageGet('pk-theme'));
  try {
    S.me = await api('/api/me');
    renderShell();
    route();
  } catch (e) {
    if (!S.me) renderLogin();
  }
}());
