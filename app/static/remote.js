/* POCKET KNIFE – RDP/VNC im Browser (Apache Guacamole, lokal gebündelt) */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs, ...kids) {
  const [name, ...cls] = tag.split('.');
  const el = document.createElement(name);
  if (cls.length) el.className = cls.join(' ');
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = (el.className ? el.className + ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'value' || (k in el && typeof v !== 'string')) el[k] = v;
    else if (k === 'placeholder' || k === 'title' || k === 'aria-label') el.setAttribute(k, tr(String(v)));
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (k) => { if (k === null || k === undefined || k === false) return; if (Array.isArray(k)) k.forEach(add); else el.append(k instanceof Node ? k : (typeof k === 'string' ? tr(k) : String(k))); };
  kids.forEach(add);
  return el;
}
const btn = (label, onclick, cls = '', title) => h('button', { type: 'button', class: cls, onclick, title }, label);
function toast(msg, kind = '') {
  const t = h('div.toast', { class: kind }, msg);
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'err' ? 7000 : 3000);
}
async function api(path, opt = {}) {
  const o = { method: opt.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (o.method !== 'GET') o.headers['X-PK-Request'] = '1';
  if (opt.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opt.body); }
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || `Fehler ${r.status}`);
  return data;
}
function prefs() { try { return JSON.parse(localStorage.getItem('pk-remote') || '{}'); } catch (e) { return {}; } }
function savePrefs(p) { try { localStorage.setItem('pk-remote', JSON.stringify(p)); } catch (e) { /* egal */ } }
try { const th = localStorage.getItem('pk-theme'); if (th) document.documentElement.setAttribute('data-theme', th); } catch (e) { /* egal */ }

// Guacamole-Statuscodes → verständliche Meldungen
const STATUS = {
  256: 'Diese Funktion wird vom Ziel nicht unterstützt.', 512: 'Interner Fehler im Guacamole-Dienst.',
  513: 'Der Guacamole-Dienst ist ausgelastet.', 514: 'Der Computer antwortet nicht (Zeitüberschreitung).',
  515: 'Fehler auf dem Computer bzw. Verbindung abgelehnt (RDP aktiviert? Firewall Port offen? NLA/Sicherheit?).',
  516: 'Die Ressource existiert nicht.', 517: 'Die Ressource wird bereits verwendet.', 518: 'Die Ressource wurde geschlossen.',
  519: 'Der Computer ist nicht erreichbar (Name/DNS, ausgeschaltet oder Port blockiert).',
  520: 'Der Remotedienst auf dem Computer ist nicht verfügbar.', 521: 'Die Sitzung wurde durch eine andere Verbindung übernommen oder beendet.',
  522: 'Die Sitzung wurde auf dem Computer beendet.', 768: 'Ungültige Anfrage.',
  769: 'Anmeldung abgelehnt – Benutzername oder Kennwort falsch, oder das Konto darf sich nicht per Remotedesktop anmelden.',
  771: 'Zugriff verweigert.', 776: 'Zeitüberschreitung.', 781: 'Zu viele Daten.', 783: 'Datentyp nicht unterstützt.',
  797: 'Zu viele Verbindungen.',
};

const P = new URLSearchParams(location.hash.slice(1));
const CID = Number(P.get('c') || 0);
const PROTO = P.get('p') === 'vnc' ? 'vnc' : 'rdp';
const LABEL = PROTO === 'vnc' ? 'VNC' : 'Remotedesktop';
const S = { client: null, keyboard: null, computer: null, status: null, opts: null, state: 0, fit: true, sink: null, clip: '', ended: false };

const bar = $('#rd-bar'); const stage = $('#rd-stage'); const overlay = $('#rd-overlay');
const stateEl = h('span.state.muted');

function overlayCard(title, body, buttons = [], spin = false) {
  overlay.replaceChildren(h('div.rd-card', h('div.rd-h', h('h2', title)), h('div.rd-b', spin ? h('div.rd-spin') : null, body),
    buttons.length ? h('div.rd-f', buttons) : null));
}
function clearOverlay() { overlay.replaceChildren(); focusStage(); }
function focusStage() { if (!overlay.childNodes.length) stage.focus({ preventScroll: true }); }

function drawBar() {
  const c = S.computer || {};
  const connected = S.state === 3;
  bar.replaceChildren(...[
    h('img', { src: '/static/logo.svg', alt: '', style: { width: '20px', height: '20px' } }),
    h('span.name.raw', c.name || '…'), h('span.badge', LABEL), stateEl,
    h('div.spacer'),
    connected && PROTO === 'rdp' ? btn('Strg+Alt+Entf', () => sendCombo([0xffe3, 0xffe9, 0xffff]), 'opt', 'Sendet Strg+Alt+Entf an den Computer') : null,
    connected ? btn('Windows-Taste', () => sendCombo([0xffeb]), 'opt') : null,
    connected ? btn('Zwischenablage', clipboardDlg, 'opt', 'Text mit dem Computer austauschen') : null,
    connected ? btn(S.fit ? 'Originalgröße' : 'An Fenster anpassen', () => { S.fit = !S.fit; rescale(); drawBar(); }, 'opt') : null,
    connected ? btn('Vollbild', toggleFullscreen, 'opt') : null,
    connected ? btn('Trennen', () => disconnect(), 'danger') : null].filter(Boolean));
  document.body.classList.toggle('connected', connected);
}

function setState(text, cls = '') { stateEl.textContent = text; stateEl.className = 'state ' + (cls || 'muted'); }

// --- Verbindungsdialog --------------------------------------------------------
async function start() {
  if (!CID) { overlayCard('Fernsitzung', h('p', 'Kein Computer angegeben. Bitte die Fernsitzung aus POCKET KNIFE heraus öffnen.')); return; }
  drawBar();
  try {
    [S.computer, S.status] = await Promise.all([api(`/api/computers/${CID}`), api('/api/remote/status')]);
  } catch (e) {
    overlayCard('Fernsitzung', h('p.rd-msg', e.message.includes('angemeldet') || e.message.includes('abgelaufen')
      ? 'Sie sind bei POCKET KNIFE nicht (mehr) angemeldet. Bitte im Hauptfenster anmelden und die Sitzung erneut öffnen.' : e.message),
    [btn('Zu POCKET KNIFE', () => { location.href = '/'; }, 'primary')]);
    return;
  }
  document.title = `${S.computer.name} – ${LABEL} – POCKET KNIFE`;
  drawBar();
  setState(S.computer.online ? 'online' : 'offline (laut letztem Heartbeat)', S.computer.online ? '' : 'warn');
  optionsDlg();
}

function optionsDlg(errorText) {
  const st = S.status;
  const pr = prefs();
  const own = st.own_login;
  const authSel = h('div.rd-radio');
  const fields = {};
  const mk = (k, attrs = {}) => (fields[k] = h('input', attrs));
  const authMode = { v: PROTO === 'rdp' ? (own ? 'own' : 'manual') : (st.vnc_password_set ? 'central' : 'prompt') };
  const credBox = h('div.form', { style: { marginTop: '8px' } });
  const radio = (v, label, disabled, sub) => h('label.chk', { style: { opacity: disabled ? 0.5 : 1 } },
    h('input', { type: 'radio', name: 'auth', checked: authMode.v === v, disabled, onchange: () => { authMode.v = v; drawCred(); } }), label, sub ? h('span.muted.small', ' ', sub) : null);
  function drawCred() {
    credBox.replaceChildren();
    if (PROTO === 'rdp' && authMode.v === 'manual') {
      credBox.append(h('label', 'Benutzer'), mk('username', { value: pr.username || '', placeholder: 'DOMÄNE\\benutzer, benutzer@domäne oder .\\Administrator', autocomplete: 'username' }),
        h('label', 'Kennwort'), mk('password', { type: 'password', autocomplete: 'current-password' }));
    }
    if (PROTO === 'vnc' && authMode.v === 'manual') {
      credBox.append(h('label', 'Kennwort'), mk('password', { type: 'password', autocomplete: 'off', placeholder: 'VNC-Kennwort (falls gesetzt)' }),
        h('label', 'Benutzer'), mk('username', { value: pr.vnc_user || '', placeholder: 'nur bei Servern mit Benutzeranmeldung' }));
    }
    const first = credBox.querySelector('input');
    if (first) setTimeout(() => (first.value ? (fields.password || first) : first).focus(), 30);
  }
  if (PROTO === 'rdp') {
    authSel.append(
      radio('own', 'Mit meiner POCKET KNIFE-Anmeldung', !own, own ? `(${own})` : st.sso_enabled ? '– nicht verfügbar, bitte neu anmelden' : '– in den Einstellungen ausgeschaltet'),
      radio('manual', 'Andere Anmeldedaten eingeben'),
      radio('prompt', 'Erst auf dem Computer anmelden', false, '(Windows-Anmeldebildschirm; nur ohne NLA)'));
  } else {
    authSel.append(radio('prompt', 'Ohne Kennwort / Nachfrage vom Server'),
      st.vnc_password_set ? radio('central', 'Zentrales VNC-Kennwort', false, '(aus den Einstellungen)') : null,
      radio('manual', 'Mit Kennwort'));
  }
  drawCred();
  const sizeSel = h('select', {}, [['window', 'Fenstergröße (passt sich an)'], ['1920x1080', '1920 × 1080'], ['1680x1050', '1680 × 1050'], ['1600x900', '1600 × 900'],
    ['1366x768', '1366 × 768'], ['1280x1024', '1280 × 1024'], ['1280x800', '1280 × 800']].map(([v, l]) => h('option', { value: v, selected: (pr.size || 'window') === v }, l)));
  const kb = h('select', {}, Object.entries(st.layouts).map(([v, l]) => h('option', { value: v, selected: (pr.keyboard || st.keyboard) === v }, l)));
  const depth = h('select', {}, Object.entries(st.color_depths).map(([v, l]) => h('option', { value: v, selected: String(pr.color_depth || '') === v }, l)));
  const port = h('input', { type: 'number', min: 1, max: 65535, value: (PROTO === 'vnc' ? pr.vnc_port : pr.rdp_port) || st.ports[PROTO], style: { maxWidth: '120px' } });
  const cb = (k, def, label) => { fields[k] = h('input', { type: 'checkbox', checked: pr[k] ?? def }); return h('label.chk', fields[k], label); };
  const opts = h('div.form',
    h('label', 'Auflösung'), sizeSel,
    PROTO === 'rdp' ? [h('label', 'Tastatur (Server)'), kb] : null,
    h('label', 'Farbtiefe'), depth,
    h('label', 'Port'), port,
    h('label', 'Optionen'), h('div.rd-radio',
      cb('clipboard', true, 'Zwischenablage (Text) erlauben'),
      PROTO === 'rdp' ? cb('audio', true, 'Ton übertragen') : null,
      PROTO === 'rdp' ? cb('console', false, 'Konsolensitzung (/admin)') : null,
      PROTO === 'rdp' ? cb('wallpaper', false, 'Hintergrundbild und Effekte') : null,
      PROTO === 'vnc' ? cb('read_only', false, 'Nur ansehen (keine Eingaben)') : null,
      cb('autohide', true, 'Leiste im Vollbild ausblenden')));
  const guacWarn = st.guacd.ok ? null : h('p.rd-msg', { style: { color: 'var(--err, #c00)' } },
    `Der Guacamole-Dienst (guacd, ${st.guacd.host}:${st.guacd.port}) ist nicht erreichbar: ${st.guacd.error}\nLäuft der Container „pocketknife-guacd“? (docker compose up -d)`);
  const go = async () => {
    const o = { protocol: PROTO, computer_id: CID, auth: authMode.v, port: port.value, keyboard: kb.value, color_depth: depth.value };
    for (const k of ['clipboard', 'audio', 'console', 'wallpaper', 'read_only', 'autohide']) if (fields[k]) o[k] = fields[k].checked;
    if (fields.username) o.username = fields.username.value.trim();
    if (fields.password) o.password = fields.password.value;
    if (PROTO === 'rdp' && o.auth === 'manual' && !o.username) { toast('Benutzername fehlt', 'err'); return; }
    const np = { ...pr, size: sizeSel.value, keyboard: kb.value, color_depth: depth.value, clipboard: o.clipboard, audio: o.audio,
      console: o.console, wallpaper: o.wallpaper, read_only: o.read_only, autohide: o.autohide };
    if (PROTO === 'rdp') { np.rdp_port = port.value; if (o.auth === 'manual') np.username = o.username; } else { np.vnc_port = port.value; if (o.username) np.vnc_user = o.username; }
    savePrefs(np);
    S.opts = { ...o, size: sizeSel.value };
    delete S.opts.password;
    await connect(o, sizeSel.value);
  };
  const body = h('div', guacWarn, errorText ? h('p.rd-msg', { style: { color: 'var(--err, #c00)', marginTop: 0 } }, errorText) : null,
    h('p.muted', { style: { marginTop: 0 } }, `${S.computer.dns_name || S.computer.name}${S.computer.ip_addresses ? ' · ' + S.computer.ip_addresses : ''}`),
    h('h3', { style: { margin: '6px 0' } }, 'Anmeldung'), authSel, credBox,
    h('details', { open: !!errorText && false }, h('summary', { style: { margin: '14px 0 8px', cursor: 'pointer' } }, 'Weitere Einstellungen'), opts));
  body.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); go(); } });
  overlayCard(`${LABEL}: ${S.computer.name}`, body, [btn('Schließen', () => window.close()), btn('Verbinden', go, 'primary')]);
}

// --- Verbindung ---------------------------------------------------------------
function targetSize(size) {
  if (size && size !== 'window') { const [w, hh] = size.split('x').map(Number); return [w, hh]; }
  return [Math.max(640, stage.clientWidth || window.innerWidth), Math.max(480, stage.clientHeight || (window.innerHeight - 40))];
}

let connectGen = 0;
async function connect(o, size) {
  if (S.connecting) return; // Enter + Klick: nicht zwei Sitzungen gleichzeitig aufbauen
  S.connecting = true;
  const gen = ++connectGen;
  // „Abbrechen“ während der Anmeldung: diese Verbindung verwerfen, auch wenn die Antwort noch kommt
  overlayCard(`Verbinde mit ${S.computer.name} …`, h('p.muted', { style: { textAlign: 'center' } }, 'Verbindung wird aufgebaut.'),
    [btn('Abbrechen', () => { connectGen++; S.connecting = false; disconnect(true); })], true);
  let tok;
  try { tok = await api('/api/remote/connect', { method: 'POST', body: o }); } catch (e) { S.connecting = false; if (gen === connectGen) optionsDlg(e.message); return; }
  S.connecting = false;
  if (gen !== connectGen) return; // abgebrochen
  if (S.client) {
    // Vorherige Sitzung sauber trennen – ihr spätes „getrennt“ darf die neue nicht überdecken
    const old = S.client;
    old.onstatechange = null; old.onerror = null;
    try { old.disconnect(); } catch (e) { /* egal */ }
  }
  if (typeof Guacamole === 'undefined') { optionsDlg('Die Guacamole-Bibliothek wurde nicht geladen.'); return; }
  const [w, hh] = targetSize(size);
  const dpi = Math.round(96 * Math.min(2, window.devicePixelRatio || 1));
  let tz = '';
  try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { /* egal */ }
  const tunnel = new Guacamole.WebSocketTunnel('/api/remote/tunnel');
  const client = new Guacamole.Client(tunnel);
  S.client = client; S.ended = false; S.lastError = null;
  const display = client.getDisplay();
  const el = display.getElement();
  el.classList.add('rd-display');
  stage.replaceChildren(el);
  stage.tabIndex = 0;
  S.fit = size === 'window' ? true : (prefs().fit ?? true);
  document.body.classList.toggle('autohide', !!o.autohide);

  display.onresize = () => rescale();
  client.onstatechange = (st) => {
    S.state = st;
    const txt = { 1: 'verbinde …', 2: 'warte auf Computer …', 3: 'verbunden', 4: 'trenne …', 5: 'getrennt' }[st];
    if (txt) setState(txt, st === 3 ? 'ok' : '');
    if (st === 3) { clearOverlay(); rescale(); }
    if (st === 5) ended();
    drawBar();
  };
  client.onerror = (status) => { S.lastError = status; };
  tunnel.onerror = (status) => { S.lastError = S.lastError || status; };
  client.onclipboard = (stream, mimetype) => {
    if (!/^text\//.test(mimetype)) return;
    const reader = new Guacamole.StringReader(stream);
    let data = '';
    reader.ontext = (t) => { data += t; };
    reader.onend = () => {
      S.clip = data;
      if (navigator.clipboard && navigator.clipboard.writeText && document.hasFocus()) navigator.clipboard.writeText(data).catch(() => {});
    };
  };
  client.onrequired = (params) => requiredDlg(params);
  client.onname = (name) => { if (name) document.title = `${name} – ${LABEL} – POCKET KNIFE`; };

  // Maus (Desktop) und Touch (Tablet)
  const mouse = new Guacamole.Mouse(el);
  mouse.onEach(['mousedown', 'mouseup', 'mousemove'], (e) => { if (S.state === 3 && !o.read_only) client.sendMouseState(e.state, true); });
  mouse.on('mousedown', () => focusStage());
  try {
    const touch = new Guacamole.Mouse.Touchscreen(el);
    touch.onEach(['mousedown', 'mouseup', 'mousemove'], (e) => { if (S.state === 3 && !o.read_only) client.sendMouseState(e.state, true); });
  } catch (e) { /* ohne Touch */ }
  el.addEventListener('contextmenu', (e) => e.preventDefault());

  // Tastatur: nur, wenn kein Eingabefeld der Oberfläche den Fokus hat (eine Instanz für alle Verbindungen)
  const kb = S.keyboard || new Guacamole.Keyboard(document);
  const typing = () => { const a = document.activeElement; return overlay.childNodes.length || (a && ['INPUT', 'TEXTAREA', 'SELECT'].includes(a.tagName)); };
  kb.onkeydown = (sym) => { if (typing() || S.state !== 3 || o.read_only) return true; client.sendKeyEvent(1, sym); return false; };
  kb.onkeyup = (sym) => { if (typing() || S.state !== 3 || o.read_only) return; client.sendKeyEvent(0, sym); };
  if (!S.keyboard) window.addEventListener('blur', () => { try { S.keyboard.reset(); } catch (e) { /* egal */ } });
  S.keyboard = kb;

  const q = new URLSearchParams({ token: tok.token, width: w, height: hh, dpi, tz });
  client.connect(q.toString());
}

function ended() {
  if (S.ended) return;
  S.ended = true;
  const err = S.lastError;
  const reconnect = btn('Erneut verbinden', () => optionsDlg(), 'primary');
  if (err && err.code) {
    const msg = STATUS[err.code] || err.message || `Fehlercode ${err.code}`;
    optionsDlg(`Verbindung fehlgeschlagen bzw. beendet: ${msg}` + (err.message && STATUS[err.code] && !/^Aborted|^Error/.test(err.message) ? `\n(${err.message})` : ''));
    return;
  }
  overlayCard('Sitzung beendet', h('p', `Die ${LABEL}-Sitzung zu ${S.computer.name} wurde beendet.`), [btn('Schließen', () => window.close()), reconnect]);
}

function disconnect(cancel) {
  if (S.client) { try { S.client.disconnect(); } catch (e) { /* egal */ } }
  if (cancel) optionsDlg();
}

// Server fragt fehlende Anmeldedaten nach (Guacamole „required“, z. B. bei NLA oder VNC-Kennwort)
function requiredDlg(params) {
  const inputs = {};
  const LBL = { username: 'Benutzer', password: 'Kennwort', domain: 'Domäne' };
  const form = h('div.form', params.map((p) => [h('label', LBL[p] || p), inputs[p] = h('input', { type: p === 'password' ? 'password' : 'text',
    value: p === 'domain' ? (S.status && S.status.own_login && S.status.own_login.includes('\\') ? S.status.own_login.split('\\')[0] : '') : '' })]));
  const send = () => {
    for (const p of params) {
      const stream = S.client.createArgumentValueStream('text/plain', p);
      const wr = new Guacamole.StringWriter(stream);
      wr.sendText(inputs[p].value);
      wr.sendEnd();
    }
    overlayCard(`Verbinde mit ${S.computer.name} …`, h('p.muted', { style: { textAlign: 'center' } }, 'Anmeldung wird geprüft.'), [], true);
  };
  form.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
  overlayCard('Anmeldung erforderlich', h('div', h('p.muted', { style: { marginTop: 0 } }, `${S.computer.name} verlangt Anmeldedaten.`), form),
    [btn('Abbrechen', () => disconnect(true)), btn('Anmelden', send, 'primary')]);
  setTimeout(() => { const f = form.querySelector('input'); if (f) f.focus(); }, 30);
}

function sendCombo(keys) {
  if (!S.client || S.state !== 3) return;
  keys.forEach((k) => S.client.sendKeyEvent(1, k));
  [...keys].reverse().forEach((k) => S.client.sendKeyEvent(0, k));
  focusStage();
}

function clipboardDlg() {
  const ta = h('textarea', { value: S.clip || '' });
  const sendText = (text) => {
    const stream = S.client.createClipboardStream('text/plain');
    const wr = new Guacamole.StringWriter(stream);
    wr.sendText(text);
    wr.sendEnd();
    S.clip = text;
  };
  overlayCard('Zwischenablage', h('div.rd-clip',
    h('p.muted', { style: { marginTop: 0 } }, 'Text, der auf dem Computer kopiert wird, erscheint hier. Eigenen Text einfügen und „An Computer senden“ – danach dort mit Strg+V einfügen.'), ta),
  [btn('Schließen', clearOverlay),
    navigator.clipboard && navigator.clipboard.readText ? btn('Lokale Zwischenablage senden', async () => {
      try { const t = await navigator.clipboard.readText(); sendText(t); toast('Gesendet', 'ok'); clearOverlay(); } catch (e) { toast('Kein Zugriff auf die lokale Zwischenablage – bitte Text einfügen.', 'err'); }
    }) : null,
    btn('An Computer senden', () => { sendText(ta.value); toast('Gesendet', 'ok'); clearOverlay(); }, 'primary')]);
  setTimeout(() => ta.focus(), 30);
}

function rescale() {
  if (!S.client) return;
  const d = S.client.getDisplay();
  const dw = d.getWidth(); const dh = d.getHeight();
  if (!dw || !dh) return;
  const fs = !!document.fullscreenElement && document.body.classList.contains('autohide');
  const aw = stage.clientWidth; const ah = stage.clientHeight || (window.innerHeight - (fs ? 0 : 40));
  const scale = S.fit ? Math.min(aw / dw, ah / dh, 4) : 1;
  d.scale(scale);
  stage.classList.toggle('scroll', !S.fit);
}

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (S.client && S.state === 3 && S.opts && S.opts.size === 'window') {
      const [w, hh] = targetSize('window');
      S.client.sendSize(w, hh);
    }
    rescale();
  }, 250);
});
document.addEventListener('fullscreenchange', () => { document.body.classList.toggle('fs', !!document.fullscreenElement); setTimeout(rescale, 100); });

function toggleFullscreen() {
  if (document.fullscreenElement) { document.exitFullscreen(); return; }
  document.documentElement.requestFullscreen().catch(() => toast('Vollbild nicht möglich', 'err'));
  try { if (navigator.keyboard && navigator.keyboard.lock) navigator.keyboard.lock(); } catch (e) { /* nicht unterstützt */ }
}

// Leiste bei ausgeblendetem Modus über den oberen Rand einblenden
const hot = h('div');
hot.id = 'rd-hot';
document.body.appendChild(hot);
hot.addEventListener('mouseenter', () => { bar.classList.add('show'); setTimeout(() => bar.classList.remove('show'), 2500); });

// Eingaben in der Fernsitzung zählen als Aktivität – sonst meldete die Abmeldung bei Inaktivität den Bediener mitten
// in der Arbeit ab (der Haupt-Tab sieht diese Eingaben nicht) und beendete damit die Sitzung
let pkActiveSent = 0;
['pointerdown', 'keydown', 'wheel', 'touchstart', 'mousemove'].forEach((ev) => window.addEventListener(ev, () => {
  if (Date.now() - pkActiveSent > 60000) { pkActiveSent = Date.now(); fetch('/api/session/active', { method: 'POST', credentials: 'same-origin', headers: { 'X-PK-Request': '1' } }).catch(() => {}); }
}, { capture: true, passive: true }));
window.addEventListener('beforeunload', (e) => { if (S.state === 3) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('unload', () => { if (S.client) try { S.client.disconnect(); } catch (e) { /* egal */ } });

start();
