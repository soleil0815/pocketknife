/* POCKET KNIFE – language switch German/English (no external libraries).
   The UI is written in German; for English all displayed texts are translated via the
   dictionary PK_I18N_EN (fixed texts and templates with placeholders {0}, {1} ...). */
(function () {
  'use strict';
  const LS = 'pk-lang';
  let lang = 'de';
  try { lang = localStorage.getItem(LS) || ''; } catch (e) { lang = ''; }
  if (!lang) lang = (navigator.language || 'de').toLowerCase().startsWith('de') ? 'de' : 'en';
  if (lang !== 'de' && lang !== 'en') lang = 'de';
  const D = (typeof PK_I18N_EN !== 'undefined' && PK_I18N_EN) || { exact: {}, tpl: [] };
  const exact = D.exact || {};
  const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const buckets = new Map();
  for (const [src, dst] of D.tpl || []) {
    const parts = src.split(/\{(\d+)\}/);
    let re = '^';
    const order = [];
    let longest = '';
    const L = 'A-Za-zÄÖÜäöüß';
    for (let i = 0; i < parts.length; i++) {
      if (i % 2) {
        // Placeholder directly attached to a word: only substitute whole words (otherwise "{0} Computer{1}" would match "Computerübersicht")
        const before = /[A-Za-zÄÖÜäöüß]$/.test(parts[i - 1] || '') ? `(?![${L}])` : '';
        const after = /^[A-Za-zÄÖÜäöüß]/.test(parts[i + 1] || '') ? `(?<![${L}])` : '';
        re += `(${before}[\\s\\S]+?${after})`; order.push(Number(parts[i]));
      } else {
        re += esc(parts[i]);
        if (parts[i].length > longest.length) longest = parts[i];
      }
    }
    const t = { re: new RegExp(re + '$'), pre: parts.length > 1 && parts[parts.length - 1].length > 1 ? new RegExp(re) : null, order, dst, frag: longest };
    const key = parts[0].length >= 3 ? parts[0].slice(0, 3) : '';
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(t);
  }
  const cache = new Map();
  const has = (k) => Object.prototype.hasOwnProperty.call(exact, k);
  const JOIN = { von: 'of', ausgewählt: 'selected', und: 'and', oder: 'or', bis: 'to', ab: 'from', je: 'per', bei: 'at' };
  function fill(t, m) {
    const vals = {};
    t.order.forEach((n, i) => { vals[n] = m[i + 1]; });
    return t.dst.replace(/\{(\d+)\}/g, (_, n) => (vals[n] === undefined ? '' : tr(vals[n])));
  }
  function core(s, depth) {
    if (has(s)) return exact[s];
    if (s.includes(' / ') && depth < 3) return s.split(' / ').map((x) => tr(x)).join(' / ');
    const cands = (buckets.get(s.slice(0, 3)) || []).concat(buckets.get('') || []);
    for (const t of cands) {
      if (t.frag && !s.includes(t.frag)) continue;
      const m = t.re.exec(s);
      if (m) return fill(t, m);
    }
    if (depth > 2) return null;
    // Multi-line texts (outputs, preview) line by line
    if (s.includes('\n') && s.length < 60000) {
      let changed = false;
      const out = s.split('\n').map((l) => { const t = tr(l); if (t !== l) changed = true; return t; }).join('\n');
      return changed ? out : null;
    }
    // Composite texts: "A · B" or template + remainder
    if (s.includes(' · ')) return s.split(' · ').map((x) => tr(x)).join(' · ');
    const pm = /^([^:]{3,60}:)(\s+)([\s\S]+)$/.exec(s);
    if (pm && has(pm[1])) return exact[pm[1]] + pm[2] + tr(pm[3]);
    for (const t of cands) {
      if (!t.pre || (t.frag && !s.includes(t.frag))) continue;
      const m = t.pre.exec(s);
      if (m && m[0].length < s.length && !/^[A-Za-zÄÖÜäöüß0-9]/.test(s.slice(m[0].length))) {
        const rest = s.slice(m[0].length);
        const r = core(rest, depth + 1);
        return fill(t, m) + (r === null ? rest : r);
      }
    }
    // Numbers + text: "91 Einträge", "3 von 10 Computer"
    if (/\d/.test(s) && s.length < 160) {
      let changed = false;
      const out = s.split(/(\d[\d.,:]*)/).map((part) => {
        if (!part || /^\d/.test(part)) return part;
        const m = /^(\s*)([\s\S]*?)(\s*)$/.exec(part);
        const k = m[2];
        if (!k) return part;
        if (has(k)) { changed = true; return m[1] + exact[k] + m[3]; }
        if (JOIN[k]) { changed = true; return m[1] + JOIN[k] + m[3]; }
        return part;
      }).join('');
      if (changed) return out;
    }
    return null;
  }
  function tr(s) {
    if (lang !== 'en' || typeof s !== 'string' || s.length < 2 || !/[A-Za-zÄÖÜäöüß]/.test(s)) return s;
    if (cache.has(s)) return cache.get(s);
    const m = /^([\s•→✓✕–-]*)([\s\S]*?)(\s*)$/.exec(s);
    let out = core(m[2], 0);
    out = out === null || out === undefined ? s : m[1] + out + m[3];
    if (cache.size > 20000) cache.clear();
    cache.set(s, out);
    return out;
  }
  // Translate texts set via textContent afterwards (h() translates directly)
  if (lang === 'en' && typeof MutationObserver !== 'undefined') {
    const SKIP = '.help-body, .help-toc, pre.out, input, textarea, .raw, [data-notr]';
    const fixText = (n) => { if (n.data) { const t = tr(n.data); if (t !== n.data) n.data = t; } };
    const fix = (n) => {
      // Do not translate single text nodes (textContent = ...) in data areas either (e.g. displayed passwords)
      if (n.nodeType === 3) { if (!(n.parentElement && n.parentElement.closest(SKIP))) fixText(n); return; }
      if (n.nodeType !== 1 || n.closest(SKIP)) return;
      const w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT,
        { acceptNode: (t) => (t.parentElement && t.parentElement.closest(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
      let t; while ((t = w.nextNode())) fixText(t);
    };
    const obs = new MutationObserver((list) => {
      for (const m of list) {
        if (m.type === 'characterData') fix(m.target);
        else for (const n of m.addedNodes) fix(n);
      }
    });
    const start = () => obs.observe(document.body, { childList: true, characterData: true, subtree: true });
    if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
  }
  window.PK_LANG = lang;
  window.tr = tr;
  window.setLang = (l) => { try { localStorage.setItem(LS, l); } catch (e) { /* ignore */ } location.reload(); };
  document.documentElement.lang = lang;
}());
