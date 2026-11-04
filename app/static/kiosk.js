/* POCKET KNIFE – software kiosk for users (no external libraries).
   Users only see the released software and only the computer they are currently logged in on. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const tr = (s) => (typeof window.tr === 'function' ? window.tr(s) : s);
function h(tag, attrs, ...kids) {
  const m = /^([a-z0-9]*)((?:[.#][\w-]+)*)$/i.exec(tag);
  const el = document.createElement((m && m[1]) || 'div');
  if (m && m[2]) for (const part of m[2].match(/[.#][\w-]+/g)) { if (part[0] === '.') el.classList.add(part.slice(1)); else el.id = part.slice(1); }
  if (attrs === null || attrs === undefined || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) kids.unshift(attrs);
  else {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === 'class') String(v).split(/\s+/).filter(Boolean).forEach((c) => el.classList.add(c));
      else if (k === 'style') Object.assign(el.style, v);
      else if (k === 'value' || k === 'checked' || k === 'disabled') el[k] = v;
      else if (k === 'placeholder' || k === 'title' || k === 'aria-label') el.setAttribute(k, tr(String(v)));
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  // Never translate data (software names, descriptions, computers): class .raw
  const raw = el.classList.contains('raw');
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false || k === '') continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(typeof k === 'string' && !raw ? tr(k) : String(k)));
  }
  return el;
}
function fmt(s, ...a) { return tr(s).replace(/\{(\d+)\}/g, (_m, i) => (a[i] === undefined ? '' : a[i])); }
function toast(msg, kind = '') {
  const t = h('div.toast', { class: kind }, msg);
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'err' ? 8000 : 4000);
}
function fmtDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || '');
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : (s || '');
}
function setTheme(v) {
  try { if (v) localStorage.setItem('pk-theme', v); else localStorage.removeItem('pk-theme'); } catch (e) { /* ignore */ }
  if (v) document.documentElement.setAttribute('data-theme', v); else document.documentElement.removeAttribute('data-theme');
}
function themeDark() {
  const v = document.documentElement.getAttribute('data-theme');
  return v ? v === 'dark' : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
}
try { setTheme(localStorage.getItem('pk-theme') || ''); } catch (e) { /* ignore */ }

const S = { me: null, cid: null, cat: 'Alle', q: '', data: null, reqs: [], timer: null, busy: false };

async function api(path, opt = {}) {
  const o = { method: opt.method || 'GET', headers: {}, credentials: 'same-origin' };
  if (o.method !== 'GET') o.headers['X-PK-Request'] = '1';
  if (opt.body !== undefined) { o.body = JSON.stringify(opt.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (r.status === 401 && !path.startsWith('/api/login')) {
    const was = S.me; S.me = null;
    if (was) ssoOff(); // do not automatically sign in again right after a logout due to inactivity
    if (was) renderLogin(data && typeof data.detail === 'string' && data.detail !== 'Nicht angemeldet' ? data.detail : '');
    throw new Error(tr('Bitte neu anmelden.'));
  }
  if (!r.ok) throw new Error(data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `Fehler ${r.status}`);
  return data;
}
const GET = (p) => api(p);
const POST = (p, b) => api(p, { method: 'POST', body: b || {} });

function modal(title, body, buttons) {
  return new Promise((res) => {
    const bg = h('div.k-modal-bg');
    const close = (v) => { bg.remove(); document.removeEventListener('keydown', key); res(v); };
    const key = (e) => { if (e.key === 'Escape') close(null); };
    bg.appendChild(h('div.k-modal', h('div.mh', title), h('div.mb', body),
      h('div.mf', buttons.map(([label, val, cls]) => h('button', { type: 'button', class: cls || '', onclick: () => close(val) }, label)))));
    bg.addEventListener('mousedown', (e) => { if (e.target === bg) close(null); });
    document.addEventListener('keydown', key);
    $('#modal-root').appendChild(bg);
    const b = bg.querySelector('button.primary'); if (b) b.focus();
  });
}

// --- Sign-in with Windows (Kerberos) ------------------------------------------------------------
// Automatically only once per browser tab; after "Abmelden", inactivity or an error only again via the button.
function ssoOff() { try { sessionStorage.setItem('pk-sso', 'off'); } catch (e) { /* ignore */ } }
function ssoAuto() { try { return sessionStorage.getItem('pk-sso') !== 'off'; } catch (e) { return true; } }
function ssoHostOk(info) {
  const hs = info.sso_hosts || [];
  return !hs.length || hs.includes(location.hostname.toLowerCase());
}
function ssoUrl(info) {
  const host = (info.sso_hosts || [])[0];
  return host ? `${location.protocol}//${host}${location.port ? `:${location.port}` : ''}/kiosk` : '';
}
async function trySSO() {
  // The browser answers the 401 response ("WWW-Authenticate: Negotiate") itself with its Kerberos ticket
  let r;
  try { r = await fetch('/api/kiosk/sso', { method: 'POST', credentials: 'same-origin', headers: { 'X-PK-Request': '1' } }); } catch (e) { return tr('Server nicht erreichbar.'); }
  if (r.ok) return '';
  let d = {}; try { d = await r.json(); } catch (e) { /* ignore */ }
  if (r.status === 401) return tr('Die Anmeldung mit Windows hat nicht geklappt: Der Browser hat kein Kerberos-Ticket geschickt. Bitte mit Benutzername und Passwort anmelden – oder die IT bitten, den Browser für den Kiosk freizugeben.');
  return d.detail || fmt('Fehler {0}', r.status);
}
async function ssoLogin(info, box) {
  box.replaceChildren(h('div.k-sso-wait', h('span.spin'), h('span', 'Anmeldung mit Windows …')));
  const err = await trySSO();
  if (!err) {
    try { await start(); return true; } catch (e) { ssoOff(); renderLogin(e.message || String(e), info); return false; }
  }
  ssoOff();
  renderLogin(err, info);
  return false;
}

// --- Sign-in ---------------------------------------------------------------------------------
async function renderLogin(msg = '', known = null) {
  stopTimer();
  let info = known || { enabled: true, title: 'Software-Kiosk' };
  if (!known) { try { info = await GET('/api/kiosk/info'); } catch (e) { /* ignore */ } }
  document.title = `${tr(info.title)} – POCKET KNIFE`;
  const user = h('input', { autocomplete: 'username', required: true, placeholder: 'benutzer oder DOMÄNE\\benutzer' });
  const pass = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const err = h('div.err', msg);
  const submit = h('button.primary', { type: 'submit' }, 'Anmelden');
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault(); submit.disabled = true; err.textContent = '';
    try {
      await api('/api/login', { method: 'POST', body: { username: user.value, password: pass.value, kiosk: true } });
      await start();
    } catch (x) { err.textContent = x.message; pass.value = ''; pass.focus(); } finally { submit.disabled = false; }
  } }, h('label', 'Benutzername'), user, h('label', 'Passwort'), pass, submit, err);
  const body = h('div');
  let ssoBox = null;
  if (info.enabled && info.sso) {
    if (ssoHostOk(info)) {
      ssoBox = h('div.k-sso', h('button.primary.wide', { type: 'button', onclick: () => ssoLogin(info, body) }, 'Mit Windows anmelden'),
        h('div.k-or', h('span', 'oder mit Benutzername und Passwort')));
    } else {
      const u = ssoUrl(info);
      ssoBox = h('div.k-sso', h('p.muted.small', 'Anmeldung mit Windows ist nur über diese Adresse möglich: ', h('a.raw', { href: u }, u)),
        h('div.k-or', h('span', 'oder mit Benutzername und Passwort')));
    }
  }
  body.replaceChildren(...(info.enabled ? [ssoBox, form].filter(Boolean) : [h('p', 'Der Software-Kiosk ist derzeit nicht eingeschaltet. Bitte wenden Sie sich an die IT.')]));
  $('#app').replaceChildren(h('div.k-login', h('div.box',
    h('div.lg', h('img', { src: '/static/logo.svg', alt: '' }), h('div', h('b', info.title), h('span.muted', 'Software selbst installieren – mit Ihrem Windows-Konto'))),
    body,
    h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: '6px', marginTop: '14px' } }, langSwitch()))));
  if (info.enabled && info.sso && ssoHostOk(info) && ssoAuto() && !msg) {
    ssoOff(); // only one automatic attempt per tab – if it fails, the form stays
    if (await ssoLogin(info, body)) { try { sessionStorage.removeItem('pk-sso'); } catch (e) { /* ignore */ } }
    return;
  }
  if (info.enabled) setTimeout(() => user.focus(), 30);
}
function langSwitch() {
  const lang = window.PK_LANG || 'de';
  return h('div.lang-switch', [['de', 'DE'], ['en', 'EN']].map(([k, l]) => h('button', { type: 'button', class: lang === k ? 'on' : '',
    onclick: () => { if (lang !== k && window.setLang) window.setLang(k); } }, l)));
}
async function logout() {
  ssoOff(); // after logging out do not immediately sign in with Windows automatically again
  try { await POST('/api/logout'); } catch (e) { /* ignore */ }
  S.me = null; renderLogin();
}

// --- Main page -------------------------------------------------------------------------------
async function start() {
  S.me = await GET('/api/kiosk/me');
  IDLE.last = Date.now(); IDLE.sent = Date.now();
  if (!S.me.computers.some((c) => c.id === S.cid)) S.cid = S.me.computers.length ? S.me.computers[0].id : null;
  document.title = `${tr(S.me.title)} – POCKET KNIFE`;
  await refresh(true);
}
function stopTimer() { if (S.timer) clearTimeout(S.timer); S.timer = null; }

async function refresh(first = false) {
  stopTimer();
  if (!S.me) return;
  try {
    S.reqs = await GET('/api/kiosk/requests');
    S.data = S.cid ? await GET(`/api/kiosk/catalog?computer_id=${S.cid}`) : null;
  } catch (e) {
    if (!S.me) return;
    if (first) toast(e.message, 'err');
    if (/nicht \(mehr\) angemeldet/.test(e.message)) { S.me = await GET('/api/kiosk/me').catch(() => S.me); S.cid = (S.me.computers[0] || {}).id || null; }
  }
  draw();
  const busy = (S.data && S.data.items.some((i) => i.request && i.request.open)) || S.reqs.some((r) => r.open);
  S.timer = setTimeout(() => refresh(), busy ? 5000 : 30000);
}

function initials(n) { return (n || '?').replace(/[^A-Za-zÄÖÜäöü0-9 ]/g, '').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?'; }

function topBar() {
  const comps = S.me.computers;
  const pick = comps.length > 1 ? h('select', { onchange: (e) => { S.cid = Number(e.target.value); refresh(true); } },
    comps.map((c) => h('option.raw', { value: c.id, selected: c.id === S.cid }, c.name))) : null;
  const theme = h('button', { type: 'button', title: themeDark() ? 'Helle Darstellung' : 'Dunkle Darstellung',
    onclick: () => { setTheme(themeDark() ? 'light' : 'dark'); draw(); } }, themeDark() ? '☀' : '☾');
  return h('div.k-top', h('img', { src: '/static/logo.svg', alt: '' }),
    h('div.k-brand', h('b', S.me.title), h('small', 'POCKET KNIFE')),
    h('div.spacer'),
    pick,
    h('div.k-user', h('b.raw', S.me.display_name || S.me.username), comps.length === 1 ? h('span.raw', fmt('Computer: {0}', comps[0].name)) : null),
    theme, langSwitch(), h('button', { type: 'button', onclick: logout }, 'Abmelden'));
}

const ST_DOT = { success: 'ok', failed: 'err', failed_start: 'err', rejected: 'err', pending: 'warn', scheduled: 'warn', queued: 'run', running: 'run', skipped: 'ok' };

function mine(r) { return !!(r && S.me && String(r.username || '').toLowerCase() === String(S.me.username || '').toLowerCase()); }
function card(it) {
  const r = it.request;
  const st = [];
  // "installiert" comes from the server: result of the last request as long as it is newer than the last scan, otherwise the inventory
  if (r && r.open) st.push(h('span.k-dot', { class: ST_DOT[r.status] || '' }), h('span', r.action === 'uninstall' ? fmt('Deinstallation: {0}', tr(r.text)) : tr(r.text)));
  else if (it.installed) st.push(h('span.k-dot.ok'), h('span', it.installed_version ? fmt('Installiert ({0})', it.installed_version) : 'Installiert'));
  else if (r && ['failed', 'failed_start'].includes(r.status)) st.push(h('span.k-dot.err'), h('span', { title: r.message }, 'Letzter Versuch fehlgeschlagen'));
  else if (r && r.status === 'rejected') st.push(h('span.k-dot.err'), h('span', { title: r.message || '' }, r.message ? fmt('Abgelehnt: {0}', r.message) : 'Abgelehnt'));
  else if (it.approval) st.push(h('span.muted', 'Installation nach Freigabe durch die IT'));
  const act = [];
  if (r && r.open) {
    // Withdraw only for own requests (on shared PCs the requests of others are visible too)
    if (r.status === 'pending' && mine(r)) act.push(h('button', { type: 'button', onclick: () => withdraw(r) }, 'Anfrage zurückziehen'));
  } else if (it.installed) {
    if (it.can_uninstall) act.push(h('button', { type: 'button', onclick: () => order(it, 'uninstall') }, 'Deinstallieren'));
    act.push(h('button.ghost', { type: 'button', onclick: () => order(it, 'install'), title: 'Erneut installieren bzw. aktualisieren' }, 'Neu installieren'));
  } else {
    act.push(h('button.primary', { type: 'button', onclick: () => order(it, 'install') }, it.approval ? 'Anfordern' : 'Installieren'));
  }
  return h('div.k-card', h('div.k-h', it.icon ? h('div.k-ic.img', h('img', { src: it.icon, alt: '' })) : h('div.k-ic', initials(it.name)),
    h('div', h('div.k-n.raw', it.name), h('div.k-v', it.version ? fmt('Version {0}', it.version) : '', it.version && it.category ? ' · ' : '', it.category ? h('span.raw', it.category) : null))),
  h('div.k-d.raw', it.description || ''), h('div.k-st', st), h('div.k-act', act));
}

function rebootBanner() {
  const rb = S.data && S.data.reboot;
  if (!rb) return null;
  if (rb.planned) {
    return h('div.k-banner.info', h('div.k-bt', h('b', 'Neustart geplant'),
      rb.planned.scheduled_at ? fmt('Ihr Computer startet am {0} neu. Sie erhalten vorher einen Hinweis.', fmtDate(rb.planned.scheduled_at)) : 'Ihr Computer startet in Kürze neu. Bitte Dateien speichern.'),
    rb.planned.status === 'scheduled' && mine(rb.planned) ? h('button', { type: 'button', onclick: () => withdraw(rb.planned) }, 'Neustart absagen') : null);
  }
  if (!rb.needed) return null;
  const now = new Date();
  const later = ['12:00', '18:00', '20:00'].filter((t) => { const [hh, mm] = t.split(':').map(Number); const d = new Date(); d.setHours(hh, mm, 0, 0); return d - now > 10 * 60000; });
  return h('div.k-banner', h('div.k-bt', h('b', 'Neustart erforderlich'), 'Damit die Installation abgeschlossen wird, muss Ihr Computer neu starten. Wann passt es Ihnen?'),
    h('button.primary', { type: 'button', onclick: () => reboot('now') }, 'Jetzt (in 2 Minuten)'),
    h('button', { type: 'button', onclick: () => reboot('1h') }, 'In 1 Stunde'),
    later.map((t) => h('button', { type: 'button', onclick: () => reboot(t) }, fmt('Heute {0} Uhr', t))));
}

function draw() {
  if (!S.me) return;
  const comp = S.me.computers.find((c) => c.id === S.cid);
  const wrap = h('div.k-wrap');
  wrap.append(h('h1.k-hello.raw', fmt('Hallo {0}', (S.me.display_name || S.me.username).split(/[ ,]/)[0])),
    h('p.k-sub.raw', comp ? fmt('Software für {0} – installieren, ohne die IT anrufen zu müssen.', comp.name) : ''));
  if (!S.me.enabled) {
    wrap.append(h('div.k-empty', 'Der Software-Kiosk ist derzeit nicht eingeschaltet.'));
  } else if (!comp) {
    wrap.append(h('div.k-empty', h('p', S.me.hint || 'Kein Computer gefunden.'),
      h('button.primary', { type: 'button', onclick: async () => { try { S.me = await GET('/api/kiosk/me'); S.cid = (S.me.computers[0] || {}).id || null; refresh(true); } catch (e) { toast(e.message, 'err'); } } }, 'Erneut suchen')));
  } else if (S.data) {
    const rb = rebootBanner(); if (rb) wrap.append(rb);
    const cats = ['Alle', ...[...new Set(S.data.items.map((i) => i.category).filter(Boolean))].sort((a, b) => a.localeCompare(b))];
    if (!cats.includes(S.cat)) S.cat = 'Alle';
    const search = h('input', { type: 'search', placeholder: 'Software suchen …', value: S.q,
      oninput: (e) => { S.q = e.target.value; const pos = e.target.selectionStart; drawGrid(); setTimeout(() => { const el = $('.k-tools input'); if (el) { el.focus(); el.setSelectionRange(pos, pos); } }, 0); } });
    wrap.append(h('div.k-tools', search, cats.length > 2 ? cats.map((c) => h('button.k-chip', { type: 'button', class: `${S.cat === c ? 'on' : ''} ${c === 'Alle' ? '' : 'raw'}`, onclick: () => { S.cat = c; draw(); } }, c)) : null));
    const grid = h('div.k-grid#kgrid'); wrap.append(grid);
    const items = S.data.items.filter((i) => (S.cat === 'Alle' || i.category === S.cat) && (!S.q || `${i.name} ${i.description} ${i.category}`.toLowerCase().includes(S.q.toLowerCase())));
    if (!S.data.items.length) grid.replaceWith(h('div.k-empty', 'Für Ihren Computer ist noch keine Software freigegeben.'));
    else if (!items.length) grid.replaceWith(h('div.k-empty', 'Keine Software gefunden.'));
    else grid.append(...items.map(card));
  }
  if (S.reqs.length) {
    wrap.append(h('div.k-sec', 'Meine Anfragen'), h('div.k-reqwrap', h('table.k-req', h('thead', h('tr', h('th', 'Datum'), h('th', 'Software'), h('th', 'Computer'), h('th', 'Status'), h('th', ''))),
      h('tbody', S.reqs.slice(0, 20).map((r) => h('tr',
        h('td', fmtDate(r.created_at)),
        h('td', r.action === 'reboot' ? h('span', 'Neustart') : h('span.raw', r.package_name), r.action === 'uninstall' ? h('span.muted', ' (Deinstallation)') : (r.action === 'reboot' ? h('span.muted', r.scheduled_at ? ` – ${fmtDate(r.scheduled_at)}` : '') : null)),
        h('td.raw', r.computer_name),
        h('td', h('span.k-dot', { class: ST_DOT[r.status] || '', style: { marginRight: '6px' } }), tr(r.text), r.message && !r.open && r.status !== 'success' ? h('div.muted.small.raw', r.message) : null),
        h('td', r.status === 'pending' || (r.status === 'scheduled' && r.action === 'reboot') ? h('button.small', { type: 'button', onclick: () => withdraw(r) }, 'Zurückziehen') : null)))))));
  }
  $('#app').replaceChildren(topBar(), wrap);
}
function drawGrid() { draw(); }

async function order(it, action) {
  const comp = S.me.computers.find((c) => c.id === S.cid);
  const txt = action === 'uninstall'
    ? fmt('„{0}“ von {1} deinstallieren?', it.name, comp.name)
    : (it.approval ? fmt('„{0}“ für {1} anfordern? Die IT wird benachrichtigt und gibt die Installation frei.', it.name, comp.name)
      : fmt('„{0}“ auf {1} installieren? Die Installation läuft im Hintergrund – Sie können weiterarbeiten. Bitte das Programm vorher schließen, falls es schon geöffnet ist.', it.name, comp.name));
  const ok = await modal(action === 'uninstall' ? 'Deinstallieren' : (it.approval ? 'Anfordern' : 'Installieren'), h('p', { style: { margin: 0 } }, txt),
    [['Abbrechen', null], [action === 'uninstall' ? 'Deinstallieren' : (it.approval ? 'Anfordern' : 'Installieren'), true, 'primary']]);
  if (!ok || S.busy) return;
  S.busy = true; // no second job while the first is still being sent
  try {
    const r = await POST('/api/kiosk/request', { computer_id: S.cid, package_id: it.package_id, action });
    toast(r.status === 'pending' ? tr('Angefragt – Sie sehen hier, sobald die IT freigibt.') : tr('Gestartet – das kann einige Minuten dauern.'), 'ok');
  } catch (e) { toast(e.message, 'err'); } finally { S.busy = false; }
  refresh();
}
async function withdraw(r) {
  try { await POST(`/api/kiosk/requests/${r.id}/withdraw`); toast(tr('Zurückgezogen'), 'ok'); } catch (e) { toast(e.message, 'err'); }
  refresh();
}
async function reboot(when) {
  const txt = when === 'now' ? tr('Computer in 2 Minuten neu starten? Bitte vorher alle Dateien speichern.') : (when === '1h' ? tr('Neustart in einer Stunde planen?') : fmt('Neustart heute um {0} Uhr planen?', when));
  if (!(await modal('Neustart', h('p', { style: { margin: 0 } }, txt), [['Abbrechen', null], ['Neustart planen', true, 'primary']])) || S.busy) return;
  S.busy = true;
  try { await POST('/api/kiosk/reboot', { computer_id: S.cid, when }); toast(tr('Neustart geplant'), 'ok'); } catch (e) { toast(e.message, 'err'); } finally { S.busy = false; }
  refresh();
}

// --- Logout on inactivity (as in the administration) ------------------------------------------
const IDLE = { last: Date.now(), sent: 0, warn: null, checking: false };
function idleActivity() {
  IDLE.last = Date.now();
  if (IDLE.warn) { IDLE.warn.remove(); IDLE.warn = null; }
  if (S.me && Date.now() - IDLE.sent > 60000) { IDLE.sent = Date.now(); POST('/api/session/active').catch(() => {}); }
}
['pointerdown', 'keydown', 'wheel', 'touchstart', 'mousemove'].forEach((ev) => window.addEventListener(ev, idleActivity, { capture: true, passive: true }));
setInterval(async () => {
  if (!S.me || IDLE.checking) return;
  const limit = (S.me.idle_minutes || 30) * 60000;
  if (Date.now() - IDLE.last < limit - 120000) return;
  IDLE.checking = true;
  try {
    const r = await GET('/api/session/idle');
    if (r.remaining <= 100 && S.me) {
      const txt = fmt('Abmeldung wegen Inaktivität in {0} s – zum Weiterarbeiten klicken oder eine Taste drücken.', Math.max(0, Math.round(r.remaining)));
      if (!IDLE.warn) { IDLE.warn = h('div.toast.warn', { style: { position: 'fixed', right: '16px', bottom: '16px', zIndex: 9999, maxWidth: '420px' } }, txt); document.body.appendChild(IDLE.warn); } else IDLE.warn.textContent = txt;
    }
  } catch (e) { /* signed out */ } finally { IDLE.checking = false; }
}, 15000);

(async function init() {
  try { await start(); } catch (e) { if (!S.me) renderLogin(); }
}());
