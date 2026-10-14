/* Bar Oratorio – interfaccia web (vanilla JS, nessuna dipendenza esterna) */
'use strict';

/* ---------------- utilità ---------------- */
const WD = ['L', 'M', 'M', 'G', 'V', 'S', 'D'];
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function fmt(s) { if (!s) return '—'; const p = String(s).slice(0, 10).split('-'); return p[2] + '/' + p[1] + '/' + p[0]; }
function fmtS(s) { if (!s) return '—'; const p = String(s).slice(0, 10).split('-'); return p[2] + '/' + p[1]; }
function fmtTs(s) {
  if (!s) return '';
  const d = String(s).slice(0, 10), t = String(s).slice(11, 16);
  if (d === S.today) return 'oggi ' + t;
  const y = new Date(S.today); y.setDate(y.getDate() - 1);
  if (d === y.toISOString().slice(0, 10)) return 'ieri ' + t;
  return fmtS(d) + ' ' + t;
}
function daysLeft(s) { return Math.round((new Date(String(s).slice(0, 10)) - new Date(S.today)) / 86400000); }
function addDays(s, n) { const d = new Date(s); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); }
function lifeText(lf) { return lf >= 300 ? 'un anno' : lf >= 60 ? Math.round(lf / 30) + ' mesi' : lf + ' giorni'; }
function initials(name) { const w = String(name || '?').replace(/[^\p{L}\p{N} ]/gu, '').split(' ').filter(Boolean); return ((w[0] || '?')[0] + ((w[1] || '')[0] || (w[0] || '')[1] || '')).toUpperCase(); }
function store(k, v) { try { if (v === undefined) return localStorage.getItem('bar.' + k); localStorage.setItem('bar.' + k, v); } catch (e) { return null; } return null; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* ---------------- API ---------------- */
class ApiError extends Error { constructor(status, detail) { super(typeof detail === 'string' ? detail : (detail && detail.message) || 'Errore'); this.status = status; this.detail = detail; } }
async function api(method, path, body) {
  const opt = { method, headers: { 'X-Bar': '1' }, credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch('api/' + path, opt); } catch (e) { throw new ApiError(0, 'Connessione assente: riprova tra poco'); }
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401 && path !== 'login') { S.user = null; S.boot = null; render(); throw new ApiError(401, 'Accesso scaduto: rientra'); }
  if (!r.ok) throw new ApiError(r.status, data && data.detail !== undefined ? data.detail : 'Errore ' + r.status);
  return data;
}
const get = (p) => api('GET', p);
const post = (p, b) => api('POST', p, b === undefined ? {} : b);
const put = (p, b) => api('PUT', p, b);
const del = (p) => api('DELETE', p);

/* ---------------- stato ---------------- */
const S = {
  boot: null, user: null, today: new Date().toISOString().slice(0, 10), screen: 'home', toast: null, sheet: null,
  wide: window.matchMedia('(min-width: 900px)').matches,
  login: { step: 'user', username: store('user') || '', pin: '', pass: '', device: store('device') || guessDevice(), err: '', busy: false },
  data: {}, loading: false,
  draft: { lines: [], note: '', updated_at: null }, draftSaved: null,
  out: {}, reason: 'venduto', sub: null, q: '', filter: 'tutti', done: null,
  prop: { step: 0, code: null, name: '', cat: null, note: '', photo_url: null },
  lotFilter: 'attivi', lotQ: '', lotEst: false, selCart: null, selLine: null, lineVal: null, lineReason: null, lineExp: '',
  hz: 'fine_mese', ptab: 'prev', mesiPid: null,
  selProd: null, pe: null, prodTab: 'prodotti', photoResults: null, catEdit: null,
  selUser: null, newUser: { display_name: '', username: '', role: 'operatore', pin: '' }, pinNew: '', pw: { current: '', new: '' },
  corr: { pid: null, val: null, reason: null, target: 'nuovo' },
  invCounts: {}, invTarget: {}, prodLots: {},
  shopTab: 'lista', newItem: { text: '', qty: '' }, addProd: { pid: '', qty: '' },
  logCat: '', logUser: '', logQ: '',
  edit: null, pickFor: null, unknown: null, st: null,
};
function guessDevice() { const ua = navigator.userAgent; return /iPhone|Android|Mobile/.test(ua) ? 'Telefono' : /iPad|Tablet/.test(ua) ? 'Tablet' : 'PC'; }
const isStaff = () => S.user && S.user.role !== 'operatore';
const isAdmin = () => S.user && S.user.role === 'admin';
const pc = () => S.wide && isStaff();
const roleLabel = (r) => ({ admin: 'Amministratore', coordinatore: 'Coordinatore', operatore: 'Operatore' }[r]);
const products = () => (S.boot ? S.boot.products : []);
const activeProducts = () => products().filter((p) => p.status === 'attivo');
const P = (id) => products().find((p) => p.id === Number(id));
const categories = () => (S.boot ? S.boot.categories : []);
const counts = () => (S.boot ? S.boot.counts : {});
const warnDays = () => Number((S.boot && S.boot.settings.expiry_warning_days) || 14);
const editMinutes = () => Number((S.boot && S.boot.settings.edit_minutes) || 30);
const catLife = (p) => (p.tracks ? (p.shelf_life_days || p.cat_life || 180) : null);
const shortName = (n) => String(n || '').replace(' lattina 33cl', '').replace(' 50cl', '');

let toastTimer = null;
function toast(msg) { S.toast = msg; clearTimeout(toastTimer); toastTimer = setTimeout(() => { S.toast = null; render(); }, 2600); render(); }
function fail(e) { toast(e && e.message ? e.message : 'Errore'); }
function setPath(path, val) { const ks = path.split('.'); let o = S; ks.slice(0, -1).forEach((k) => { o = o[k]; }); o[ks[ks.length - 1]] = val; }

/* ---------------- icone ---------------- */
const ICONS = {
  in: '<path d="M12 3v10"/><path d="m8 9 4 4 4-4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  out: '<path d="M12 13V3"/><path d="m8 7 4-4 4 4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
  list: '<path d="M3 7h18"/><path d="M3 12h18"/><path d="M3 17h12"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 8v8M10 8v8M13 8v8M16 8v8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  cart: '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.7 12.2a2 2 0 0 0 2 1.6h7.7a2 2 0 0 0 2-1.5L21 8H6"/>',
  home: '<path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
  clip: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4h6v3H9z"/><path d="M9 12h6M9 16h4"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16Z"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8Z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  inbox: '<path d="M4 13h4l2 3h4l2-3h4"/><path d="M5 5h14l2 8v6H3v-6Z"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 8v4l3 2"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  device: '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
  pc: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  logout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="m10 16-4-4 4-4M6 12h10"/>',
  cup: '<path d="M17 8h1a4 4 0 0 1 0 8h-1"/><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/><path d="M6 2v2M10 2v2M14 2v2"/>',
  camera: '<path d="M4 7h3l2-3h6l2 3h3a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13" r="4"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  shield: '<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6Z"/><path d="M9 12l2 2 4-4"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 20a2 2 0 0 0 4 0"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
};
function ic(name, size, color) {
  const s = size || 22;
  return '<svg width="' + s + '" height="' + s + '" viewBox="0 0 24 24" fill="none" stroke="' + (color || 'currentColor') + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (ICONS[name] || '') + '</svg>';
}
function thumb(p, size) {
  const z = size || 48;
  if (p && p.photo) return '<div class="thumb" style="width:' + z + 'px;height:' + z + 'px;background:#fff"><img src="photos/' + esc(p.photo) + '" alt="" loading="lazy"></div>';
  const col = (p && p.cat_color) || '#DCE2DF';
  return '<div class="thumb" style="width:' + z + 'px;height:' + z + 'px;background:' + esc(col) + ';color:#2B3632;font-size:' + Math.round(z / 2.8) + 'px">' + esc(initials(p && p.name)) + '</div>';
}
function pill(txt, kind) { return '<span class="pill ' + kind + '">' + esc(txt) + '</span>'; }
function A(action, extra) { return 'data-a="' + action + '"' + (extra ? ' ' + Object.keys(extra).map((k) => 'data-' + k + '="' + esc(extra[k]) + '"').join(' ') : ''); }
function seg(items, cur, action, key) { return '<div class="seg">' + items.map((i) => '<button class="' + (String(cur) === String(i[0]) ? 'on' : '') + '" ' + A(action, { [key]: i[0] }) + '>' + i[1] + '</button>').join('') + '</div>'; }
function stockState(p) { if (p.stock <= 0) return ['Finito', 'bad']; if (p.stock <= p.min_stock) return ['Poco: da riordinare', 'warn']; return ['OK', 'ok']; }
function loading() { return '<div class="loading">Caricamento…</div>'; }

/* ---------------- scansione barcode (fotocamera, pistola USB/Bluetooth, inserimento a mano) ---------------- */
const Scanner = {
  stream: null, timer: null, reader: null, onCode: null,
  async open(onCode) {
    this.onCode = onCode;
    const el = document.getElementById('scanner');
    el.classList.add('on');
    document.getElementById('scan-manual').value = '';
    const msg = document.getElementById('scan-msg');
    msg.textContent = 'Avvio della fotocamera…';
    const video = document.getElementById('scan-video');
    try {
      if ('BarcodeDetector' in window) {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        const det = new window.BarcodeDetector({ formats: formats.filter((f) => /ean|upc|code|itf/.test(f)) });
        this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        video.srcObject = this.stream;
        await video.play();
        msg.textContent = 'Tieni fermo il codice dentro il riquadro.';
        this.timer = setInterval(async () => {
          try { const r = await det.detect(video); if (r && r[0]) this.found(r[0].rawValue); } catch (e) { /* frame non pronto */ }
        }, 250);
      } else {
        await this.loadZxing();
        this.reader = new window.ZXing.BrowserMultiFormatReader();
        msg.textContent = 'Tieni fermo il codice dentro il riquadro.';
        await this.reader.decodeFromVideoDevice(undefined, 'scan-video', (res) => { if (res) this.found(res.getText()); });
      }
    } catch (e) {
      this.stopCamera();
      msg.textContent = (location.protocol !== 'https:' && location.hostname !== 'localhost')
        ? 'La fotocamera funziona solo dal link https: scrivi il codice qui sotto o usa la pistola barcode.'
        : 'Fotocamera non disponibile: scrivi il codice qui sotto o usa la pistola barcode.';
    }
  },
  loadZxing() {
    if (window.ZXing) return Promise.resolve();
    return new Promise((ok, ko) => { const s = document.createElement('script'); s.src = 'static/vendor/zxing.min.js'; s.onload = ok; s.onerror = ko; document.head.appendChild(s); });
  },
  found(code) { const cb = this.onCode; this.close(); if (navigator.vibrate) navigator.vibrate(80); if (cb) cb(String(code).trim()); },
  stopCamera() {
    clearInterval(this.timer); this.timer = null;
    if (this.reader) { try { this.reader.reset(); } catch (e) { /* già fermo */ } this.reader = null; }
    if (this.stream) { this.stream.getTracks().forEach((t) => t.stop()); this.stream = null; }
  },
  close() {
    this.stopCamera();
    document.getElementById('scanner').classList.remove('on');
  },
};
document.getElementById('scan-close').addEventListener('click', () => Scanner.close());
document.getElementById('scan-go').addEventListener('click', () => { const v = document.getElementById('scan-manual').value.trim(); if (v) Scanner.found(v); });
document.getElementById('scan-manual').addEventListener('keydown', (e) => { if (e.key === 'Enter') { const v = e.target.value.trim(); if (v) Scanner.found(v); } });
/* pistola barcode: digita velocemente e chiude con Invio, quando nessun campo è attivo */
let wedge = '', wedgeT = 0;
document.addEventListener('keydown', (e) => {
  const tag = (document.activeElement && document.activeElement.tagName) || '';
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || !S.user) return;
  const t = Date.now();
  if (t - wedgeT > 80) wedge = '';
  wedgeT = t;
  if (e.key === 'Enter') { if (wedge.length >= 6) { const c = wedge; wedge = ''; handleCode(c); } return; }
  if (e.key.length === 1 && /[0-9A-Za-z\-.]/.test(e.key)) wedge += e.key;
});

/* ---------------- foto dal telefono (ridotta prima dell'invio) ---------------- */
function pickPhoto() {
  return new Promise((resolve) => {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*'; inp.capture = 'environment';
    inp.onchange = () => {
      const f = inp.files && inp.files[0]; if (!f) return resolve(null);
      const img = new Image();
      img.onload = () => {
        const m = 700, k = Math.min(1, m / Math.max(img.width, img.height));
        const c = document.createElement('canvas'); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL('image/jpeg', 0.82)); URL.revokeObjectURL(img.src);
      };
      img.src = URL.createObjectURL(f);
    };
    inp.click();
  });
}
function qrSvg(text) {
  try { const q = window.qrcode(0, 'M'); q.addData(text); q.make(); return q.createSvgTag(4, 0); } catch (e) { return ''; }
}
/* =====================================================================
   ACCESSO
   ===================================================================== */
function loginView() {
  const L = S.login;
  let body;
  if (L.step === 'user') {
    body = `
      <label class="field">Nome utente
        <input id="lg-user" class="input" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" value="${esc(L.username)}" data-bind="login.username" placeholder="es. marco">
      </label>
      ${L.err ? `<div class="info amber">${esc(L.err)}</div>` : ''}
      <button class="btn big ${L.username.trim() ? 'green' : 'off'}" ${A('loginNext')}>Avanti</button>`;
  } else if (L.step === 'pin') {
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'C', '0', '←'];
    body = `
      <div class="row"><div class="grow"><div class="title">Ciao ${esc(L.username)}</div><div class="muted small">Inserisci il tuo PIN</div></div>
        <button class="btn sm ghost" ${A('loginBack')}>Non sono io</button></div>
      <div class="pin-dots">${[0, 1, 2, 3, 4, 5, 6, 7].slice(0, Math.max(4, L.pin.length)).map((i) => `<i class="${i < L.pin.length ? 'f' : ''}"></i>`).join('')}</div>
      ${L.err ? `<div class="info amber">${esc(L.err)}</div>` : ''}
      <div class="keypad">${keys.map((k) => `<button class="${k === 'C' || k === '←' ? 'fn' : ''}" ${A('pinKey', { k })} aria-label="${k === 'C' ? 'Cancella' : k === '←' ? 'Cancella una cifra' : 'Cifra ' + k}">${k}</button>`).join('')}</div>
      ${deviceField()}
      <button class="btn big ${L.pin.length >= 4 && !L.busy ? 'green' : 'off'}" ${A('loginDo')}>Entra</button>`;
  } else {
    body = `
      <div class="row"><div class="grow"><div class="title">Amministratore</div><div class="muted small">Accesso con nome utente e password</div></div>
        <button class="btn sm ghost" ${A('loginBack')}>Cambia utente</button></div>
      <label class="field">Password
        <input id="lg-pass" class="input" type="password" autocomplete="current-password" value="${esc(L.pass)}" data-bind="login.pass">
      </label>
      ${L.err ? `<div class="info amber">${esc(L.err)}</div>` : ''}
      ${deviceField()}
      <button class="btn big ${L.busy ? 'off' : 'green'}" ${A('loginDo')}>Entra</button>`;
  }
  return `<div class="scroll" style="padding:28px 24px;gap:16px">
    <div style="max-width:440px;width:100%;margin:${S.wide ? '40px' : '0'} auto;display:flex;flex-direction:column;gap:16px">
      <div class="row" style="gap:12px">
        <div class="thumb" style="width:52px;height:52px;background:var(--green);border-radius:14px">${ic('cup', 30, '#fff')}</div>
        <div><div style="font-size:23px;font-weight:700">Bar Oratorio</div><div class="muted">Magazzino del bar</div></div>
      </div>
      ${body}
      <div class="muted small" style="text-align:center;line-height:1.4">Dopo 5 tentativi sbagliati l'utente si blocca per 15 minuti.<br>PIN dimenticato? Chiedi all'amministratore.</div>
    </div></div>`;
}
function deviceField() {
  return `<label class="field">Nome di questo dispositivo
      <input id="lg-dev" class="input" value="${esc(S.login.device)}" data-bind="login.device">
      <span class="muted xsmall" style="font-weight:400">Resterai collegato qui finché non esci. L'amministratore vede i dispositivi collegati a ogni utente.</span>
    </label>`;
}

/* =====================================================================
   SCHERMATE OPERATIVE (operatore; le usano anche coordinatore e admin)
   ===================================================================== */
const qMatch = (p) => !S.q.trim() || p.name.toLowerCase().includes(S.q.trim().toLowerCase()) || (p.barcodes || []).includes(S.q.trim());

function opHome() {
  const c = counts();
  return `<div class="scroll">
    <div><div style="font-size:26px;font-weight:700">Ciao ${esc(S.user.display_name.split(' ')[0])}</div><div class="muted" style="font-size:18px">Cosa devi fare?</div></div>
    <button class="tile green" ${A('go', { s: 'carico' })}><div class="ic">${ic('in', 36, '#fff')}</div><div><div class="t1">Arriva merce</div><div class="t2">${c.my_draft ? `<b>Hai un carrello in bozza:</b> ${c.my_draft} prodotti. Tocca per continuare` : 'Carico: metti i prodotti nel carrello e conferma tutto insieme'}</div></div></button>
    <button class="tile orange" ${A('go', { s: 'scarico' })}><div class="ic">${ic('out', 36, '#fff')}</div><div><div class="t1">Esce merce</div><div class="t2">Venduto al bar, scaduto, rotto, uso interno</div></div></button>
    <button class="tile blue" ${A('go', { s: 'giacenze' })}><div class="ic">${ic('list', 34, '#fff')}</div><div><div class="t1">Quanto c'è</div><div class="t2">Giacenze e scadenze</div></div></button>
    <div class="grid2">
      <button class="mtile" ${A('go', { s: 'spesa' })}><span class="row" style="gap:8px">${ic('cart', 22)}<span class="t1">Lista spesa</span></span><span class="t2">${c.shop_open ? c.shop_open + ' cose da prendere' : 'nessuna lista aperta'}</span></button>
      <button class="mtile" ${A('go', { s: 'miei' })}><span class="row" style="gap:8px">${ic('history', 22)}<span class="t1">I miei movimenti</span></span><span class="t2">${c.my_editable ? c.my_editable + ' ancora modificabili' : 'carichi e scarichi fatti'}</span></button>
    </div>
    ${c.expiring ? `<button class="alert amber" ${A('goScad')}>${ic('clock', 26, '#8A5A00')}<span><b>${c.expiring === 1 ? '1 riga di lotto scade' : c.expiring + ' righe di lotto scadono'} a breve.</b> ${c.expiring === 1 ? 'Vendila per prima' : 'Vendile per prime'}.</span></button>` : ''}
    ${S.boot.open_inventory ? `<button class="alert blue" ${A('go', { s: 'inventario' })}><span class="n">${ic('clip', 18, '#fff')}</span><span><b>Inventario in corso</b><br>aiuta a contare i prodotti</span></button>` : ''}
    <button class="btn ghost" style="border-style:dashed;border-color:#8FA39B;min-height:56px" ${A('go', { s: 'proponi' })}>${ic('plus')} Prodotto nuovo? Proponilo</button>
  </div>`;
}

/* ---------- carico (carrello in bozza, salvato sul server) ---------- */
function caricoView() {
  const dr = S.draft;
  const lines = dr.lines.map((c, i) => {
    const p = P(c.product_id) || { name: '?', stock: 0 };
    const lf = catLife(p);
    return `<div class="card">
      <div class="row">${thumb(p, 52)}<div class="grow"><div class="title">${esc(p.name)}</div><div class="muted small">${esc(p.category || '')} · ora in magazzino ${p.stock}</div></div>
        <button class="iconbtn" ${A('cartDel', { i })} aria-label="Togli dal carrello">${ic('x', 20)}</button></div>
      <div class="stepper"><span class="b" style="width:60px">Pezzi</span>
        <button style="background:var(--soft)" ${A('cartQty', { i, d: -1 })} aria-label="Un pezzo in meno">−</button>
        <input id="cq-${i}" class="val" style="font:inherit;font-size:26px;font-weight:700;text-align:center;width:100%" inputmode="numeric" value="${c.qty}" data-bind="draftQty" data-i="${i}" aria-label="Pezzi">
        <button style="background:var(--green);color:#fff" ${A('cartQty', { i, d: 1 })} aria-label="Un pezzo in più">+</button></div>
      <div class="row" style="padding-left:70px;gap:8px">${[6, 12, 24].map((n) => `<button class="btn sm ghost grow" ${A('cartQty', { i, d: n })}>+${n}</button>`).join('')}</div>
      ${lf ? `<label class="row b" style="font-size:15px"><span style="width:60px;white-space:nowrap">Scade il</span>
          <input id="exp-${i}" type="date" class="input" style="height:48px" value="${esc(c.expiry)}" data-bind="draftExp" data-i="${i}"></label>
        ${!c.expiry ? `<div class="info amber small" style="padding:8px 10px">Senza data la stimo io (circa ${lifeText(lf)}) e la segno come stimata.</div>` : ''}`
        : '<div class="muted small">Questo prodotto non ha scadenza.</div>'}
    </div>`;
  }).join('');
  const pieces = dr.lines.reduce((a, c) => a + Number(c.qty || 0), 0);
  return `<div class="scroll">
      ${dr.lines.length ? `<div class="row small muted" style="gap:8px">${ic('check', 18, '#1F6B52')}<span class="grow">${S.draftSaved === 'saving' ? 'Salvataggio…' : S.draftSaved === 'error' ? '<span class="err">Bozza non salvata: controlla la connessione</span>' : 'Bozza salvata ' + esc(fmtTs(dr.updated_at)) + ': puoi chiudere l\'app e riprenderla da qualsiasi dispositivo.'}</span><button class="btn xs ghost" ${A('sheet', { k: 'dropDraft' })}>Svuota</button></div>` : ''}
      <div class="grid2">
        <button class="btn dark" style="min-height:64px" ${A('scan')}>${ic('scan', 24)} Scansiona</button>
        <button class="btn outline" style="min-height:64px" ${A('sheet', { k: 'pick' })}>${ic('search')} Cerca nome</button>
      </div>
      ${dr.lines.length ? lines + `<label class="field">Nota (facoltativa)<input id="dr-note" class="input" value="${esc(dr.note || '')}" data-bind="draftNote" placeholder="es. spesa per la festa di domenica"></label>`
        : `<div class="empty">${ic('cart', 48, '#8FA39B')}<div class="title" style="font-size:19px">Il carrello è vuoto</div><div class="muted" style="line-height:1.4">Scansiona i prodotti arrivati oppure cercali per nome. Alla fine conferma tutto insieme: il carrello diventa un lotto con la data di oggi.</div></div>`}
    </div>
    <div class="foot"><button class="btn big ${dr.lines.length ? 'green' : 'off'}" ${A('confirmCarico')}>${dr.lines.length ? `Conferma carico · ${dr.lines.length} prodotti, ${pieces} pz` : 'Aggiungi prodotti al carrello'}</button></div>`;
}

/* ---------- scarico ---------- */
function scaricoView() {
  const outIds = Object.keys(S.out).map(Number);
  const pieces = outIds.reduce((a, id) => a + S.out[id], 0);
  const ready = pieces > 0 && (S.reason === 'venduto' || S.sub);
  const tiles = activeProducts().filter(qMatch).map((p) => {
    const n = S.out[p.id] || 0, s = p.stock;
    return `<button class="ptile ${n ? 'sel' : ''} ${s <= 0 ? 'empty' : ''}" ${A('tapOut', { id: p.id })}>
      ${n ? `<span class="badge">${n}</span>` : ''}${thumb(p, 54)}
      <span class="nm">${esc(shortName(p.name))}</span>
      <span class="xsmall ${s <= 0 ? 'b' : 'muted'}" style="${s <= 0 ? 'color:var(--red)' : ''}">${s <= 0 ? 'Finito' : 'ci sono ' + (s - n)}</span></button>`;
  }).join('');
  const label = pieces === 0 ? 'Tocca i prodotti che escono' : S.reason === 'altro' && !S.sub ? 'Scegli il motivo' : 'Conferma uscita · ' + pieces + (pieces === 1 ? ' pezzo' : ' pezzi');
  return `<div class="scroll">
      <div class="b">Perché esce?</div>
      <div class="grid2">
        <button class="btn ${S.reason === 'venduto' ? 'orange' : 'ghost'}" ${A('reason', { r: 'venduto' })}>Venduto al bar</button>
        <button class="btn ${S.reason === 'altro' ? 'orange' : 'ghost'}" ${A('reason', { r: 'altro' })}>Altro motivo</button>
      </div>
      ${S.reason === 'altro' ? `<div class="chips">${['Scaduto', 'Rotto', 'Uso interno', 'Omaggio', 'Altro'].map((r) => `<button class="chip ${S.sub === r ? 'on' : ''}" ${A('sub', { r })}>${r}</button>`).join('')}</div>` : ''}
      <div class="row">
        <button class="btn dark" ${A('scan')}>${ic('scan')} Scansiona</button>
        <input id="q-out" class="input grow" placeholder="Cerca…" aria-label="Cerca prodotto" value="${esc(S.q)}" data-bind="q">
      </div>
      <div class="muted small">Tocca un prodotto una volta per ogni pezzo. Esce prima dal lotto che scade prima.</div>
      <div class="grid3">${tiles || '<div class="muted">Nessun prodotto.</div>'}</div>
    </div>
    <div class="foot">
      ${outIds.length ? `<div class="chips">${outIds.map((id) => `<button class="chip" style="background:#FBE3D6;border-color:#FBE3D6;color:var(--orange-ink);min-height:36px;font-size:14px" ${A('outDec', { id })} aria-label="Togli un pezzo">${S.out[id]} × ${esc(shortName((P(id) || {}).name))} &nbsp;−</button>`).join('')}</div>` : ''}
      <button class="btn big ${ready ? 'orange' : 'off'}" ${A('confirmScarico')}>${label}</button>
    </div>`;
}

/* ---------- giacenze (versione operatore) ---------- */
function giacenzeOp() {
  const w = warnDays();
  const rows = activeProducts().filter(qMatch).filter((p) => {
    if (S.filter === 'poco') return p.stock <= p.min_stock;
    if (S.filter === 'scad') return p.next_expiry && daysLeft(p.next_expiry) <= w;
    return true;
  }).map((p) => {
    const st = stockState(p);
    let expTag = '';
    if (p.next_expiry && daysLeft(p.next_expiry) < 0) expTag = pill('Scaduto il ' + fmtS(p.next_expiry), 'bad');
    else if (p.next_expiry && daysLeft(p.next_expiry) <= w) expTag = pill('Scade il ' + fmtS(p.next_expiry) + ' (' + daysLeft(p.next_expiry) + ' gg)', 'est');
    else if (p.next_expiry && p.next_expiry_estimated) expTag = pill('Scadenza stimata', 'est');
    return `<div class="card" style="flex-direction:row;align-items:center;gap:12px;padding:12px 14px">${thumb(p, 48)}
      <div class="grow"><div class="title">${esc(p.name)}</div><div class="chips" style="gap:6px;margin-top:4px">${pill(st[0], st[1])}${expTag}</div></div>
      <div style="text-align:right"><div style="font-size:26px;font-weight:700;line-height:1">${p.stock}</div><div class="muted xsmall">pezzi</div></div></div>`;
  }).join('');
  const pend = products().filter((p) => p.status === 'proposto').map((p) => `<div class="card" style="flex-direction:row;align-items:center;gap:12px;background:#E9ECEB;color:var(--muted)">
      <div class="thumb" style="width:48px;height:48px;background:#C9D2CE">${ic('lock', 22, '#4A5752')}</div>
      <div><div class="title" style="color:#34413C">${esc(p.name)}</div><div class="small">Proposto da ${esc((p.proposed_by_name || '').split(' ')[0])} · in attesa di approvazione. Non si può ancora caricare né scaricare.</div></div></div>`).join('');
  return `<div class="scroll">
    <input id="q-g" class="input" placeholder="Cerca un prodotto…" aria-label="Cerca prodotto" value="${esc(S.q)}" data-bind="q">
    <div class="chips">${[['tutti', 'Tutti'], ['poco', 'Da riordinare'], ['scad', 'In scadenza']].map((f) => `<button class="chip ${S.filter === f[0] ? 'on' : ''}" ${A('filter', { f: f[0] })}>${f[1]}</button>`).join('')}</div>
    ${rows || '<div class="empty muted">Nessun prodotto.</div>'}
    ${S.filter === 'tutti' && !S.q ? pend : ''}
  </div>`;
}

/* ---------- lista della spesa (operatore): legge, spunta, chiude ---------- */
const shopLabel = (it) => it.product || it.text;
function spesaOp() {
  const sh = S.data.shop;
  if (!sh) return loading();
  const items = sh.items, done = items.filter((i) => i.checked).length;
  if (!items.length) return `<div class="scroll"><div class="empty">${ic('cart', 48, '#8FA39B')}<div class="title" style="font-size:19px">Nessuna lista aperta</div><div class="muted">Quando il coordinatore prepara la spesa, la trovi qui.</div></div></div>`;
  return `<div class="scroll">
      <div class="muted" style="line-height:1.4">Spunta quello che metti nel carrello del negozio. Quando hai finito, chiudi la lista. La merce poi si carica a mano con «Arriva merce».</div>
      <div class="list">${items.map((it) => `<button class="li" style="border:none;background:none;width:100%;text-align:left;cursor:pointer;min-height:60px" ${A('shopTick', { id: it.id })}>
        <span class="thumb" style="width:34px;height:34px;border-radius:10px;border:3px solid ${it.checked ? 'var(--green)' : 'var(--field)'};background:${it.checked ? 'var(--green)' : '#fff'}">${it.checked ? ic('check', 20, '#fff') : ''}</span>
        <span class="grow" style="${it.checked ? 'text-decoration:line-through;color:var(--muted)' : ''}"><span class="title">${esc(shopLabel(it))}</span>${it.product_id ? '' : '<br><span class="xsmall muted">voce libera</span>'}</span>
        <b style="font-size:18px">${esc(it.qty_text || '')}</b></button>`).join('')}</div>
    </div>
    <div class="foot"><div class="muted small" style="text-align:center">${done} di ${items.length} presi</div><button class="btn big dark" ${A('sheet', { k: 'closeList' })}>Chiudi la lista</button></div>`;
}

/* ---------- i miei movimenti: vedi e modifica entro N minuti, mai eliminare ---------- */
const KIND = { carico: ['Carico', 'ok'], vendita: ['Venduto', 'blue'], scarico: ['Scarico', 'warn'], correzione: ['Correzione', 'grey'], inventario: ['Inventario', 'grey'] };
const linesText = (lines) => (lines || []).map((l) => shortName(l.name) + ' ' + (l.qty > 0 ? '+' : '') + l.qty).join(', ');
function mieiView() {
  const m = S.data.mine;
  if (!m) return loading();
  return `<div class="scroll">
    <div class="muted" style="line-height:1.4">Per <b>${m.edit_minutes} minuti</b> puoi correggere un tuo carico o una tua uscita: quantità, scadenze, righe in più o in meno, nota. Non puoi eliminarlo tutto. Dopo, chiedi al coordinatore.</div>
    ${m.items.map((d) => `<div class="card">
        <div class="row">${pill(KIND[d.type][0], KIND[d.type][1])}<span class="title grow">${d.type === 'carico' && d.cart ? 'Lotto ' + esc(d.cart) : esc(d.reason || '')}</span><span class="muted small">${esc(fmtTs(d.ts))}</span></div>
        <div class="small">${esc(linesText(d.lines))}</div>
        ${d.edited_at ? `<div class="xsmall muted">Modificato ${esc(fmtTs(d.edited_at))}</div>` : ''}
        ${d.editable ? `<button class="btn ghost" ${A('openEdit', { id: d.id })}>${ic('edit', 18)} Modifica · ancora ${d.minutes_left} min</button>` : `<div class="row small muted" style="gap:6px">${ic('lock', 16)} Non più modificabile</div>`}
      </div>`).join('') || '<div class="empty muted">Non hai ancora fatto movimenti.</div>'}
  </div>`;
}

/* ---------- proponi un prodotto ---------- */
function proponiView() {
  const pr = S.prop;
  const cats = categories().map((c) => `<button class="chip ${pr.cat === c.id ? 'on' : ''}" ${A('propCat', { c: c.id })}>${esc(c.name)}</button>`).join('');
  return `<div class="scroll">
    <div class="info blue">Il coordinatore controlla la proposta. Finché non la approva il prodotto <b>non si può caricare né scaricare</b>: tienilo da parte.</div>
    ${pr.step === 0 ? `<div class="b">Ha il codice a barre?</div>
      <button class="btn big dark" ${A('propScan')}>${ic('scan', 24)} Sì, scansiono il codice</button>
      <button class="btn ghost" ${A('propNoCode')}>No, non ce l'ha (es. panini)</button>` : ''}
    ${pr.step > 0 ? `<div class="card" style="flex-direction:row;align-items:center;gap:14px">
        ${pr.photo_url ? `<div class="thumb" style="width:84px;height:84px;background:#fff"><img src="${esc(pr.photo_url)}" alt=""></div>` : `<div class="thumb" style="width:84px;height:84px;background:var(--soft);color:var(--muted)">${ic('camera', 30)}</div>`}
        <div style="display:flex;flex-direction:column;gap:4px">${pr.code ? `<span class="muted small">Codice ${esc(pr.code)}</span>` : '<span class="muted small">Senza barcode</span>'}
          ${pr.photo_url ? '<span class="b small" style="color:var(--green)">Foto trovata online</span>' : '<span class="muted xsmall">La foto la cerco online dopo l\'invio</span>'}</div></div>
      <label class="field">Nome del prodotto<input id="pr-name" class="input" value="${esc(pr.name)}" data-bind="prop.name" placeholder="es. Panino cotto e formaggio"></label>
      <div class="field">Che tipo di prodotto è?<div class="chips" style="font-weight:400">${cats}</div></div>
      <label class="field">Nota per il coordinatore (facoltativa)<input id="pr-note" class="input" value="${esc(pr.note)}" data-bind="prop.note" placeholder="es. arrivato col carico di oggi"></label>` : ''}
  </div>
  ${pr.step > 0 ? `<div class="foot"><button class="btn big ${pr.name.trim() && pr.cat ? 'blue' : 'off'}" ${A('sendProp')}>Invia proposta</button></div>` : ''}`;
}

/* ---------- esito ---------- */
function doneView() {
  const d = S.done;
  const col = d.kind === 'carico' ? 'var(--green)' : d.kind === 'scarico' ? 'var(--orange)' : 'var(--blue)';
  return `<div class="scroll" style="align-items:center;text-align:center;padding:30px 20px">
    <div class="thumb" style="width:96px;height:96px;border-radius:50%;background:${col}">${ic('check', 56, '#fff')}</div>
    <div style="font-size:26px;font-weight:700">${esc(d.title)}</div>
    <div class="muted" style="font-size:17px;line-height:1.4">${esc(d.text)}</div>
    <div class="list" style="width:100%;max-width:520px;text-align:left">${d.lines.map((l) => `<div class="li"><span>${esc(l[0])}</span><b>${esc(l[1])}</b></div>`).join('')}</div>
    <div style="flex:1"></div>
    <div style="width:100%;max-width:520px;display:flex;flex-direction:column;gap:10px">
      <button class="btn big dark" ${A('home')}>Fatto</button>
      ${d.batch ? `<button class="btn ghost" ${A('openEdit', { id: d.batch })}>${ic('edit', 18)} Ho sbagliato: modifica (entro ${editMinutes()} minuti)</button>` : ''}
    </div>
  </div>`;
}

/* ---------- home di coordinatore e admin su telefono ---------- */
function staffPhoneHome() {
  const c = counts();
  const tiles = [
    ['carico', 'Arriva merce', c.my_draft ? 'carrello in bozza' : 'carico a carrello'], ['scarico', 'Esce merce', 'vendite e scarti'],
    ['spesa', 'Lista spesa', c.shop_open ? c.shop_open + ' voci aperte' : 'nessuna lista'], ['prospetto', 'Prospetto', 'arriviamo a fine mese?'],
    ['lotti', 'Lotti', 'carrelli e bozze'], ['inventario', 'Fare inventario', S.boot.open_inventory ? 'in corso' : 'conta di tutto'],
    ['correggi', 'Correggi quantità', 'un solo prodotto'], ['giacenze', 'Giacenze', 'tutti i prodotti'],
    ['anagrafica', 'Anagrafica', 'prodotti e categorie'], ['storico', 'Storico movimenti', 'senza nomi'],
    ['miei', 'I miei movimenti', 'modifica entro ' + editMinutes() + ' min'], ['impostazioni', 'Impostazioni', 'e notifiche'],
  ];
  if (isAdmin()) tiles.push(['log', 'Log', 'chi ha fatto cosa'], ['utenti', 'Utenti', 'e dispositivi']);
  return `<div class="scroll">
    <div><div style="font-size:24px;font-weight:700">Ciao ${esc(S.user.display_name.split(' ')[0])}</div><div class="muted">${roleLabel(S.user.role)}</div></div>
    ${c.proposals ? `<button class="alert blue" ${A('go', { s: 'proposte' })}><span class="n">${c.proposals}</span><span><b>Proposte da approvare</b><br>nuovi prodotti e barcode</span></button>` : ''}
    ${c.drafts_others ? `<button class="alert blue" ${A('go', { s: 'lotti' })}><span class="n">${c.drafts_others}</span><span><b>Carrelli in bozza</b><br>lasciati a metà da altri</span></button>` : ''}
    ${c.below_min ? `<button class="alert orange" ${A('go', { s: 'prospetto' })}><span class="n">${c.below_min}</span><span><b>Sotto la scorta minima</b><br>apri il prospetto</span></button>` : ''}
    ${c.expiring ? `<button class="alert amber" ${A('lotsScad')}><span class="n">${c.expiring}</span><span><b>Righe di lotto in scadenza</b><br>o già scadute</span></button>` : ''}
    <div class="grid2">${tiles.map((t) => `<button class="mtile" ${A('go', { s: t[0] })}><span class="t1">${t[1]}</span><span class="t2">${t[2]}</span></button>`).join('')}</div>
  </div>`;
}
/* =====================================================================
   GESTIONE (coordinatore e admin) – stesse viste su PC e telefono
   ===================================================================== */
function bars(w) {
  const mx = Math.max(...w) || 1;
  return `<div class="bars" style="height:44px;min-width:150px">${w.map((v, i) => `<div><i class="${i >= 5 ? 'we' : ''}" style="height:${Math.max(3, Math.round((v / mx) * 30))}px"></i>${WD[i]}</div>`).join('')}</div>`;
}
function docsTable(docs) {
  return `<div class="tablewrap"><table class="t"><thead><tr><th>Documento</th><th>Quando</th><th>Tipo</th><th>Righe</th><th>Motivo / nota</th></tr></thead><tbody>
    ${docs.map((d) => `<tr><td class="main" data-l="Documento">${d.type === 'carico' && d.cart ? 'Lotto ' + esc(d.cart) : esc(KIND[d.type][0])}${d.edited_at ? ' ' + pill('modificato', 'grey') : ''}</td><td data-l="Quando">${esc(fmtTs(d.ts))}</td><td data-l="Tipo">${pill(KIND[d.type][0], KIND[d.type][1])}</td>
      <td data-l="Righe" class="small"><span>${esc(linesText(d.lines))}</span></td><td data-l="Motivo">${esc(d.reason || d.note || '—')}</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Nessun movimento.</td></tr>'}
  </tbody></table></div>`;
}
const PSTATE = { esaurito: ['Esaurito', 'bad'], esaurisce: ['Finisce', 'bad'], sotto_minimo: ['Sotto minimo', 'warn'], ok: ['Basta', 'ok'], fermo: ['Fermo', 'grey'], nessun_dato: ['Pochi dati', 'grey'] };

/* ---------- panoramica (PC) ---------- */
function dashboardView() {
  const d = S.data.dash;
  if (!d) return loading();
  const c = counts();
  const runOut = d.proj.items.filter((i) => ['esaurito', 'esaurisce'].includes(i.status));
  return `
    <div class="kpis">
      <button class="kpi" ${A('go', { s: 'giacenze' })}><span class="v">${c.below_min || 0}</span><span class="l">prodotti sotto la scorta minima</span></button>
      <button class="kpi" ${A('go', { s: 'prospetto' })}><span class="v" style="color:var(--orange)">${runOut.length}</span><span class="l">finiscono entro il ${fmtS(d.proj.end_date)}</span></button>
      <button class="kpi" ${A('lotsScad')}><span class="v" style="color:#8A5A00">${d.exp.items.length}</span><span class="l">righe di lotto in scadenza o scadute</span></button>
      <button class="kpi" ${A('go', { s: 'proposte' })}><span class="v" style="color:var(--blue)">${c.proposals || 0}</span><span class="l">proposte da approvare</span></button>
    </div>
    <div class="pcsplit">
      <div class="main">
        <div class="row"><div class="title grow">Finiscono prima di fine mese</div><button class="btn sm ghost" ${A('go', { s: 'prospetto' })}>Apri prospetto</button></div>
        <div class="tablewrap"><table class="t"><thead><tr><th>Prodotto</th><th class="num">Ci sono</th><th class="num">Usciranno</th><th>Finisce il</th><th class="num">Da comprare</th></tr></thead><tbody>
          ${runOut.map((r) => `<tr><td class="main" data-l="Prodotto">${esc(r.name)}</td><td class="num" data-l="Ci sono">${r.stock}</td><td class="num" data-l="Usciranno">${Math.round(r.expected_out)}</td><td data-l="Finisce il">${pill(r.stock <= 0 ? 'Esaurito' : fmtS(r.stockout_date), 'bad')}</td><td class="num b" data-l="Da comprare" style="color:var(--orange)">${r.suggested} pz</td></tr>`).join('') || '<tr><td colspan="5" class="muted">Nessuno: tutto basta fino a fine mese.</td></tr>'}
        </tbody></table></div>
        <div class="row"><div class="title grow">Ultimi movimenti</div><button class="btn sm ghost" ${A('go', { s: 'storico' })}>Storico completo</button></div>
        ${docsTable(d.hist.items)}
      </div>
      <div class="panel" style="display:flex;flex-direction:column;gap:12px">
        <div class="title">Scadenze vicine</div>
        <div class="list">${d.exp.items.slice(0, 12).map((l) => `<div class="li" style="cursor:pointer" ${A('openCart', { id: l.cart_id, line: l.id })}><span><b>${esc(l.name)}</b><br><span class="muted small">lotto ${esc(l.cart)} · ${l.qty} pz${l.expiry_estimated ? ' · data stimata' : ''}</span></span>${pill(l.days_left < 0 ? 'Scaduto' : 'tra ' + l.days_left + ' gg', 'bad')}</div>`).join('') || '<div class="li muted">Nessuna</div>'}</div>
        ${d.drafts.length ? `<div class="title">Carrelli in bozza</div><div class="list">${d.drafts.map((x) => `<div class="li" style="cursor:pointer" ${A('go', { s: 'lotti' })}><span><b>${esc(x.user)}</b><br><span class="muted small">${x.lines.length} ${x.lines.length === 1 ? 'prodotto' : 'prodotti'} · salvato ${esc(fmtTs(x.updated_at))}</span></span>${pill('bozza', 'est')}</div>`).join('')}</div>` : ''}
        <div class="info white small">${ic('pc', 18)} Sei collegato da <b>${esc(S.user.device_name || '')}</b>. La sessione resta attiva su questo dispositivo.</div>
      </div>
    </div>`;
}

/* ---------- giacenze (gestione) ---------- */
function giacenzeStaff() {
  const rows = activeProducts().filter(qMatch).filter((p) => S.filter !== 'poco' || p.stock <= p.min_stock).map((p) => {
    const st = stockState(p);
    return `<tr>
      <td class="main" data-l="Prodotto"><div class="row">${pc() ? thumb(p, 36) : ''}<span>${esc(p.name)}</span></div></td>
      <td data-l="Categoria">${esc(p.category || '—')}</td>
      <td class="num b" data-l="Giacenza" style="font-size:18px">${p.stock}</td>
      <td class="num" data-l="Minimo">${p.min_stock}</td>
      <td data-l="Prossima scadenza"><span>${p.next_expiry ? fmt(p.next_expiry) + (p.next_expiry_estimated ? ' ' + pill('stimata', 'est') : '') : '—'}</span></td>
      <td data-l="Stato">${pill(st[0], st[1])}</td>
      <td data-l="Azioni"><div class="row" style="gap:6px;justify-content:flex-end"><button class="btn xs ghost" ${A('lotsOfProd', { name: p.name })}>Lotti</button><button class="btn xs ghost" ${A('corrOf', { id: p.id })}>Correggi</button></div></td>
    </tr>`;
  }).join('');
  return `<div class="toolbar">
      <input id="q-gs" class="input" placeholder="Cerca prodotto…" value="${esc(S.q)}" data-bind="q">
      <div class="chips">${[['tutti', 'Tutti'], ['poco', 'Sotto il minimo']].map((f) => `<button class="chip ${S.filter === f[0] ? 'on' : ''}" ${A('filter', { f: f[0] })}>${f[1]}</button>`).join('')}</div>
      <span class="grow"></span><a class="btn sm ghost" href="api/export/giacenze.csv" download>Esporta CSV</a>
    </div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Prodotto</th><th>Categoria</th><th class="num">Giacenza</th><th class="num">Minimo</th><th>Prossima scadenza</th><th>Stato</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="muted">Nessun prodotto.</td></tr>'}</tbody></table></div>`;
}

/* ---------- lotti = carrelli di ingresso ---------- */
const CSTATE = { attivo: ['Attivo', 'ok'], in_scadenza: ['In scadenza', 'bad'], scaduto: ['Ha righe scadute', 'bad'], esaurito: ['Esaurito', 'grey'] };
function lineState(l) {
  if (l.qty === 0) return ['Esaurita', 'grey'];
  if (l.expiry && daysLeft(l.expiry) < 0) return ['Scaduta', 'bad'];
  if (l.expiry && daysLeft(l.expiry) <= warnDays()) return ['Scade tra ' + daysLeft(l.expiry) + ' gg', 'bad'];
  return ['Attiva', 'ok'];
}
function lottiView() {
  const cs = S.data.carts, drafts = S.data.drafts || [];
  const rows = !cs ? '' : cs.items.map((c) => `<tr class="click ${S.selCart === c.id ? 'sel' : ''} ${c.state === 'esaurito' ? 'dim' : ''}" ${A('openCart', { id: c.id })}>
      <td class="main" data-l="Lotto">${esc(c.name)}${c.kind === 'correzione' ? ' ' + pill('correzione', 'grey') : ''}${c.note ? `<div class="muted xsmall" style="font-weight:400">${esc(c.note)}</div>` : ''}</td>
      <td data-l="Creato da">${esc(c.created_by || '—')}</td>
      <td data-l="Contenuto" class="small"><span>${esc(c.products.map(shortName).join(', '))}</span></td>
      <td class="num" data-l="Pezzi entrati">${c.qty_in}</td>
      <td class="num b" data-l="Residui" style="font-size:17px">${c.qty}</td>
      <td data-l="Prima scadenza"><span>${c.first_expiry ? fmt(c.first_expiry) + (c.first_expiry_estimated ? ' ' + pill('stimata', 'est') : '') : '—'}</span></td>
      <td data-l="Stato">${pill(CSTATE[c.state][0], CSTATE[c.state][1])}</td></tr>`).join('');
  const table = `
    ${drafts.length ? `<div class="card" style="gap:8px"><div class="title">Carrelli in bozza di altri utenti</div>
      ${drafts.map((x) => `<div class="row" style="flex-wrap:wrap"><span class="grow"><b>${esc(x.user)}</b> · ${x.lines.length} ${x.lines.length === 1 ? 'prodotto' : 'prodotti'} (${esc(x.lines.map((l) => shortName(l.name)).join(', '))}) · salvato ${esc(fmtTs(x.updated_at))}${x.note ? ' · «' + esc(x.note) + '»' : ''}</span>
        <button class="btn xs dark" ${A('takeDraft', { uid: x.user_id })}>Riprendi a nome mio</button><button class="btn xs danger" ${A('dropOther', { uid: x.user_id })}>Elimina</button></div>`).join('')}</div>` : ''}
    <div class="toolbar">
      ${seg([['attivi', 'Attivi'], ['scad', 'In scadenza'], ['esauriti', 'Esauriti'], ['tutti', 'Tutti']], S.lotFilter, 'lotFilter', 'f')}
      <input id="q-lot" class="input" placeholder="Cerca data, prodotto o nota…" value="${esc(S.lotQ)}" data-bind="lotQ" data-reload="carts">
      <label class="row small b" style="gap:8px"><input type="checkbox" ${S.lotEst ? 'checked' : ''} ${A('toggleEst')} style="width:22px;height:22px;accent-color:var(--green)"> Con date stimate</label>
    </div>
    <div class="muted small">Ogni lotto è un carrello di ingresso confermato, con prodotti misti. Il nome è la data di arrivo, con un progressivo se nello stesso giorno ne arrivano più di uno.</div>
    ${cs ? `<div class="tablewrap"><table class="t"><thead><tr><th>Lotto</th><th>Creato da</th><th>Contenuto</th><th class="num">Entrati</th><th class="num">Residui</th><th>Prima scadenza</th><th>Stato</th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="muted">Nessun lotto con questi filtri.</td></tr>'}</tbody></table></div>` : loading()}`;
  if (pc()) return `<div class="pcsplit"><div class="main">${table}</div>${S.selCart ? `<div class="panel card" style="max-height:calc(100vh - 150px);overflow:auto;width:440px">${cartPanel()}</div>` : `<div class="panel info white">Seleziona un lotto per vedere tutto il contenuto del carrello e correggere scadenze o pezzi residui riga per riga.</div>`}</div>`;
  return table;
}
function cartPanel() {
  const c = S.data.cart;
  if (!c || c.id !== S.selCart) return loading();
  const line = c.lines.find((l) => l.id === S.selLine);
  return `
    <div class="row"><div class="grow"><div class="title" style="font-size:20px">Lotto ${esc(c.name)}</div><div class="muted small">${c.kind === 'correzione' ? 'Lotto di correzione' : 'Carrello'} di ${esc(c.created_by_name || '—')} · ${fmt(c.date)}</div></div>
      ${pc() ? `<button class="iconbtn" ${A('closeCart')} aria-label="Chiudi">${ic('x', 20)}</button>` : ''}</div>
    <label class="field">Nota<div class="row"><input id="cart-note" class="input" style="height:44px" value="${esc(c.note || '')}" data-bind="data.cart.note" placeholder="nessuna nota"><button class="btn sm ghost" ${A('saveCartNote')}>Salva</button></div></label>
    <div class="field">Contenuto (${c.lines.length} ${c.lines.length === 1 ? 'riga' : 'righe'})
      <div class="list" style="background:var(--bg);font-weight:400">${c.lines.map((l) => { const s = lineState(l); return `<button class="li" style="border:none;background:${S.selLine === l.id ? 'var(--blue-bg)' : 'transparent'};width:100%;text-align:left;cursor:pointer;border-bottom:1px solid var(--soft)" ${A('openLine', { id: l.id })}>
        <span class="grow"><b>${esc(l.name)}</b><br><span class="xsmall muted">scade ${l.expiry ? fmt(l.expiry) : 'mai'}${l.expiry_estimated ? ' (stimata)' : ''} · entrati ${l.qty_in}</span></span>
        <span style="text-align:right"><b style="font-size:18px">${l.qty}</b><br>${pill(s[0], s[1])}</span></button>`; }).join('')}</div></div>
    ${line ? linePanel(line) : '<div class="muted small">Tocca una riga per correggerla.</div>'}`;
}
function linePanel(l) {
  const val = S.lineVal == null ? l.qty : S.lineVal;
  const diff = val - l.qty;
  const ready = diff !== 0 && S.lineReason;
  const H = { carico: 'Carico', vendita: 'Venduto', scarico: 'Scarico', correzione: 'Correzione', inventario: 'Inventario' };
  return `<div class="card" style="background:var(--blue-bg);gap:12px">
    <div class="row">${thumb(P(l.product_id) || { name: l.name }, 44)}<div class="grow"><div class="title">${esc(l.name)}</div><div class="muted xsmall">riga del lotto ${esc(S.data.cart.name)}</div></div></div>
    <div class="field">Scadenza
      <div class="row"><input id="line-exp" type="date" class="input" style="height:46px" value="${esc(S.lineExp || l.expiry || '')}" data-bind="lineExp"><button class="btn sm dark" ${A('saveLineExp', { id: l.id })}>Salva</button></div>
      ${l.expiry_estimated ? '<span class="xsmall" style="color:#6B4A00;font-weight:400">Data stimata in base alla categoria: controllala sulla confezione.</span>' : ''}
    </div>
    <div class="field">Pezzi residui di questa riga
      <div class="stepper"><button style="background:#fff" ${A('lineVal', { d: -1 })} aria-label="Uno in meno">−</button><div class="val">${val}</div><button style="background:var(--ink);color:#fff" ${A('lineVal', { d: 1 })} aria-label="Uno in più">+</button></div>
      ${diff ? `<span class="pill ${diff < 0 ? 'bad' : 'ok'}" style="align-self:flex-start;font-size:14px">${diff > 0 ? '+' : ''}${diff} pz rispetto ad ora</span>` : ''}
      <div class="chips" style="font-weight:400">${['Errore di conteggio', 'Ammanco', 'Rotto', 'Trovati in più', 'Altro'].map((r) => `<button class="chip ${S.lineReason === r ? 'on' : ''}" style="min-height:38px;font-size:14px" ${A('lineReason', { r })}>${r}</button>`).join('')}</div>
      <button class="btn ${ready ? 'dark' : 'off'}" ${A('applyLine', { id: l.id })}>Correggi residuo</button>
    </div>
    ${l.qty > 0 ? `<button class="btn orange" ${A('tossLine', { id: l.id })}>Scarica come scaduto (${l.qty} pz)</button>` : ''}
    <div class="field">Storia della riga
      <div class="timeline" style="font-weight:400">${l.history.slice().reverse().map((h) => `<div class="ev"><span class="muted" style="width:86px">${esc(fmtTs(h.ts))}</span><span class="grow">${esc(H[h.type] || h.type)}${h.reason ? ' · ' + esc(h.reason) : ''}</span><b style="color:${h.qty > 0 ? 'var(--green)' : 'var(--orange)'}">${h.qty > 0 ? '+' : ''}${h.qty}</b></div>`).join('')}</div>
    </div></div>`;
}

/* ---------- prospetto ---------- */
function prospettoView() {
  const head = seg([['prev', 'Previsione'], ['mesi', 'Andamento mesi']], S.ptab, 'ptab', 't');
  if (S.ptab === 'mesi') {
    const m = S.data.monthly;
    if (!m) return head + loading();
    const p = m.items.find((x) => x.product_id === Number(S.mesiPid)) || m.items[0];
    if (!p) return head + '<div class="empty muted">Ancora nessun dato.</div>';
    const labels = m.months.map((k) => ['gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic'][Number(k.slice(5)) - 1]);
    const ly = p.out_last_year.map((x) => x || 0);
    const mx = Math.max(...p.out, ...ly, 1);
    const opts = m.items.map((x) => `<option value="${x.product_id}" ${x.product_id === p.product_id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
    const hasLy = p.out_last_year.some((x) => x);
    return `${head}
      <label class="field" style="max-width:420px">Prodotto<select id="mesi-pid" class="input" data-bind="mesiPid">${opts}</select></label>
      <div class="card" style="max-width:760px">
        <div class="title">Pezzi usciti al mese</div>
        <div class="mbars">${p.out.map((v, i) => `<div><b style="color:var(--ink)">${v}</b><div class="pair">
          ${hasLy ? `<i style="width:12px;height:${Math.round((ly[i] / mx) * 140)}px;background:#A9C1DE"></i>` : ''}
          <i style="width:20px;height:${Math.round((v / mx) * 140)}px;background:${i === p.out.length - 1 ? 'repeating-linear-gradient(45deg,#1D4E89 0 4px,#4C77AD 4px 8px)' : '#1D4E89'}"></i></div>${labels[i]}</div>`).join('')}</div>
        <div class="legend"><span><i style="background:#1D4E89"></i>quest'anno</span>${hasLy ? '<span><i style="background:#A9C1DE"></i>anno scorso</span>' : ''}</div>
        <div class="muted small">L'ultimo mese è in corso (barra tratteggiata).${hasLy ? '' : " Nessun dato dell'anno scorso per questo prodotto."}</div>
      </div>`;
  }
  const pr = S.data.proj;
  if (!pr) return head + loading();
  const inList = new Set(((S.data.shop && S.data.shop.items) || []).filter((i) => i.product_id).map((i) => i.product_id));
  const order = { esaurito: 0, esaurisce: 1, sotto_minimo: 2, nessun_dato: 3, ok: 4, fermo: 5 };
  const rows = pr.items.slice().sort((a, b) => order[a.status] - order[b.status]).map((r) => {
    const badge = r.status === 'esaurisce' ? pill('Finisce il ' + fmtS(r.stockout_date), 'bad') : pill(PSTATE[r.status][0], PSTATE[r.status][1]);
    const ideal = r.ideal_dyn ? `<b>${r.ideal_dyn.qty}</b> <span class="xsmall muted">anno scorso ${Math.round(r.ideal_dyn.last_year)}${r.ideal_dyn.trend ? ' · ' + (r.ideal_dyn.trend >= 1 ? '+' : '') + Math.round((r.ideal_dyn.trend - 1) * 100) + '%' : ''}</span>` : (r.ideal_stock ? `${r.ideal_stock} <span class="xsmall muted">fissa</span>` : '—');
    return `<tr>
      <td class="main" data-l="Prodotto">${esc(r.name)}</td>
      <td data-l="Stato">${badge}</td>
      <td class="num" data-l="Ci sono">${r.stock}</td>
      <td class="num" data-l="Usciranno">${Math.round(r.expected_out)}</td>
      <td class="num" data-l="A fine periodo" style="${r.end_stock < 0 ? 'color:var(--red);font-weight:700' : ''}">${r.end_stock < 0 ? 'manca ' + Math.round(-r.end_stock) : Math.round(r.end_stock)}</td>
      <td class="num b" data-l="Da comprare" style="font-size:17px;color:${r.suggested ? 'var(--orange)' : 'var(--muted)'}">${r.suggested ? r.suggested + ' pz' : '—'}</td>
      <td data-l="Scorta ideale"><span>${ideal}</span></td>
      <td data-l="Rischio scadenza"><span>${r.at_risk ? pill(r.at_risk + ' pz entro il ' + fmtS(r.risk_date), 'est') : '—'}</span></td>
      <td data-l="Uscite per giorno">${bars(r.profile)}</td>
      <td data-l="Lista spesa">${r.suggested ? (inList.has(r.product_id) ? pill('in lista', 'ok') : `<button class="btn xs ghost" ${A('suggestAdd', { id: r.product_id, q: r.suggested })}>+ Lista</button>`) : ''}</td>
    </tr>`;
  }).join('');
  return `${head}
    <div class="toolbar"><div class="chips">${[['fine_mese', 'Fine mese'], ['30', '30 giorni'], ['fine_mese_prossimo', 'Fine mese prossimo']].map((h) => `<button class="chip ${S.hz === h[0] ? 'on' : ''}" ${A('hz', { h: h[0] })}>${h[1]}</button>`).join('')}</div></div>
    <div class="info white small">Uscite medie delle <b>ultime ${Math.round(pr.window_days / 7)} settimane</b>, giorno per giorno. Previsione fino al <b>${fmt(pr.end_date)}</b>. La scorta ideale suggerita usa lo stesso periodo dell'anno scorso e compare solo se ci sono i dati. «+ Lista» mette il prodotto nella lista della spesa solo se lo decidi tu.</div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Prodotto</th><th>Stato</th><th class="num">Ci sono</th><th class="num">Usciranno</th><th class="num">A fine periodo</th><th class="num">Da comprare</th><th>Scorta ideale</th><th>Rischio scadenza</th><th>Uscite per giorno</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="10" class="muted">Nessun prodotto.</td></tr>'}</tbody></table></div>`;
}

/* ---------- lista della spesa (coordinatore) ---------- */
function spesaStaff() {
  const tabs = seg([['lista', 'Lista aperta'], ['archivio', 'Archivio']], S.shopTab, 'shopTab', 't');
  if (S.shopTab === 'archivio') {
    const ar = S.data.archive;
    if (!ar) return tabs + loading();
    return `${tabs}<div class="muted small">Le liste chiuse. Le voci non spuntate non vengono riportate.</div>
      ${ar.map((a) => `<div class="card" style="max-width:640px"><div class="row" style="flex-wrap:wrap"><span class="title grow">Lista chiusa il ${fmt(a.closed_at)}</span><span class="muted small">da ${esc(a.closed_by_name || '—')} · ${esc(fmtTs(a.closed_at))}</span></div>
        <div class="list" style="background:var(--bg)">${a.items.map((i) => `<div class="li"><span>${i.checked ? ic('check', 18, '#1F6B52') : ic('x', 18, '#A11A0E')} ${esc(i.label)}</span><b>${esc(i.qty || '')}</b></div>`).join('')}</div></div>`).join('') || '<div class="empty muted">Nessuna lista chiusa.</div>'}`;
  }
  const sh = S.data.shop;
  if (!sh) return tabs + loading();
  const inList = new Set(sh.items.filter((i) => i.product_id).map((i) => i.product_id));
  const opts = activeProducts().filter((p) => !inList.has(p.id)).map((p) => `<option value="${p.id}" ${String(S.addProd.pid) === String(p.id) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  const list = `<div class="card" style="gap:10px">
      <div><div class="title">Lista aperta</div><div class="muted small">La vede l'operatore che va a fare la spesa.</div></div>
      ${sh.items.length ? `<div class="list" style="background:var(--bg)">${sh.items.map((it) => `<div class="li"><span class="grow">${it.checked ? ic('check', 18, '#1F6B52') + ' ' : ''}<b>${esc(shopLabel(it))}</b>${it.product_id ? '' : ' ' + pill('voce libera', 'grey')}</span>
        <input class="input sm" style="width:110px;flex:none" value="${esc(it.qty_text || '')}" data-bind="shopQty" data-id="${it.id}" id="sq-${it.id}" aria-label="Quantità">
        <button class="iconbtn" ${A('shopDel', { id: it.id })} aria-label="Togli">${ic('trash', 18)}</button></div>`).join('')}</div>` : '<div class="muted">La lista è vuota: aggiungi qualcosa qui sotto.</div>'}
      <div class="field">Aggiungi un prodotto dell'anagrafica<div class="row"><select id="add-pid" class="input" data-bind="addProd.pid"><option value="">Scegli…</option>${opts}</select><input id="add-pq" class="input" style="width:110px;flex:none" placeholder="quanti" value="${esc(S.addProd.qty)}" data-bind="addProd.qty"><button class="btn dark" ${A('shopAddProd')}>Aggiungi</button></div></div>
      <div class="field">Aggiungi una voce libera (senza anagrafica)<div class="row"><input id="ni-text" class="input" placeholder="es. tovaglioli, detersivo" value="${esc(S.newItem.text)}" data-bind="newItem.text"><input id="ni-qty" class="input" style="width:110px;flex:none" placeholder="quanti" value="${esc(S.newItem.qty)}" data-bind="newItem.qty"><button class="btn dark" ${A('shopAddFree')}>Aggiungi</button></div></div>
      ${sh.items.length ? `<button class="btn ghost" ${A('sheet', { k: 'closeList' })}>Chiudi la lista (di solito lo fa l'operatore)</button>` : ''}
    </div>`;
  const sug = `<div class="card" style="gap:8px"><div class="title">Suggerimenti dal prospetto</div><div class="muted small">Si aggiungono solo se lo decidi tu.</div>
      ${(sh.suggestions || []).map((s) => `<div class="row"><span class="grow"><b>${esc(s.name)}</b><br><span class="xsmall muted">${s.status === 'esaurito' ? 'esaurito' : s.status === 'esaurisce' ? 'finisce il ' + fmtS(s.stockout_date) : 'sotto la scorta'}</span></span><b>${s.qty} pz</b><button class="btn xs ghost" ${A('suggestAdd', { id: s.product_id, q: s.qty })}>+ Aggiungi</button></div>`).join('') || '<div class="muted small">Niente da suggerire.</div>'}</div>`;
  return `${tabs}<div class="pcsplit"><div class="main">${list}</div><div class="panel">${sug}</div></div>`;
}

/* ---------- fare inventario ---------- */
function targetSelect(pid, cur, bind, id) {
  const ls = S.prodLots[pid];
  if (!ls) { loadProdLots(pid); return '<span class="muted small">carico i lotti…</span>'; }
  return `<select id="${id}" class="input sm" style="min-width:210px" data-bind="${bind}">
    <option value="nuovo" ${cur === 'nuovo' ? 'selected' : ''}>Non so: nuovo lotto di correzione</option>
    ${ls.map((l) => `<option value="${l.id}" ${String(cur) === String(l.id) ? 'selected' : ''}>Lotto ${esc(l.cart)} (scad. ${l.expiry ? fmtS(l.expiry) : '—'})</option>`).join('')}</select>`;
}
function inventarioView() {
  const inv = S.data.inv;
  if (inv === undefined) return loading();
  if (!inv) {
    if (!isStaff()) return '<div class="empty muted">Nessun inventario in corso.</div>';
    const list = S.data.invList || [];
    return `<div class="card" style="max-width:640px"><div class="title">Fare inventario</div>
      <div class="muted" style="line-height:1.4">Si contano fisicamente tutti i prodotti. Mentre l'inventario è in corso tutti (anche gli operatori) possono inserire le quantità contate. Alla fine lo chiudi tu e le differenze vengono applicate ai lotti.</div>
      <button class="btn big dark" ${A('startInv')}>Inizia a contare</button></div>
      <div class="title">Inventari fatti</div>
      <div class="tablewrap"><table class="t"><thead><tr><th>Chiuso il</th><th class="num">Contati</th><th class="num">Differenze</th></tr></thead><tbody>
      ${list.map((i) => `<tr><td class="main" data-l="Chiuso il">${esc(fmtTs(i.closed_at))}</td><td class="num" data-l="Contati">${i.counted}</td><td class="num" data-l="Differenze">${i.differences}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Nessuno.</td></tr>'}
      </tbody></table></div>`;
  }
  const lines = inv.lines;
  const counted = lines.filter((l) => cnt(l) !== '').length;
  const rows = lines.map((l) => {
    const c = cnt(l), has = c !== '';
    const diff = has ? Number(c) - l.stock : 0;
    const tgt = S.invTarget[l.product_id] || l.target || 'nuovo';
    return `<tr><td class="main" data-l="Prodotto">${esc(l.name)}</td>
      <td class="num" data-l="Secondo l'app">${l.stock}</td>
      <td data-l="Contati"><input id="inv-${l.product_id}" class="input sm" inputmode="numeric" style="width:90px;text-align:center;font-weight:700;font-size:18px" value="${esc(c)}" placeholder="—" data-bind="invCount" data-id="${l.product_id}"></td>
      <td data-l="Differenza">${has && diff ? pill((diff > 0 ? '+' : '') + diff, diff < 0 ? 'bad' : 'ok') : has ? pill('ok', 'grey') : ''}</td>
      ${isStaff() ? `<td data-l="Pezzi in più vanno in">${has && diff > 0 ? targetSelect(l.product_id, tgt, 'invTarget', 'invt-' + l.product_id) : ''}</td>` : ''}</tr>`;
  }).join('');
  return `<div class="card"><div class="row"><div class="grow">Iniziato da <b>${esc(inv.started_by_name || '—')}</b> ${esc(fmtTs(inv.started_at))} · contano anche gli operatori</div>${isStaff() ? `<button class="btn sm danger" ${A('cancelInv')}>Annulla</button>` : ''}</div>
      <div class="progress"><i style="width:${Math.round((counted / Math.max(1, lines.length)) * 100)}%"></i></div><div class="muted small">${counted} di ${lines.length} prodotti contati. Si salva da solo. I prodotti non contati restano come sono; i pezzi in meno escono dai lotti che scadono prima.</div></div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Prodotto</th><th class="num">Secondo l'app</th><th>Contati</th><th>Differenza</th>${isStaff() ? '<th>Pezzi in più vanno in</th>' : ''}</tr></thead><tbody>${rows}</tbody></table></div>
    ${isStaff() ? `<button class="btn big dark" style="max-width:520px" ${A('sheet', { k: 'invConfirm' })}>Chiudi e applica le differenze</button>` : ''}`;
}
function cnt(l) { const v = S.invCounts[l.product_id]; return v !== undefined ? String(v) : (l.counted == null ? '' : String(l.counted)); }

/* ---------- correggi quantità (singolo prodotto) ---------- */
function correggiView() {
  const ps = activeProducts();
  if (!ps.length) return '<div class="empty muted">Nessun prodotto.</div>';
  const r = S.corr;
  if (!r.pid || !P(r.pid)) r.pid = ps[0].id;
  const p = P(r.pid), cur = p.stock;
  const val = r.val == null ? cur : r.val;
  const diff = val - cur;
  const ready = diff !== 0 && r.reason;
  const opts = ps.map((x) => `<option value="${x.id}" ${x.id === p.id ? 'selected' : ''}>${esc(x.name)}</option>`).join('');
  return `<div style="max-width:640px;display:flex;flex-direction:column;gap:14px">
    <div class="muted" style="line-height:1.4">Corregge la giacenza di un solo prodotto senza fare l'inventario completo. Per correggere una riga precisa di un lotto usa <button class="btn xs ghost" ${A('go', { s: 'lotti' })}>Lotti</button></div>
    <label class="field">Prodotto<select id="corr-pid" class="input" data-bind="corr.pid">${opts}</select></label>
    <div class="card">
      <div class="row"><span class="grow">Secondo l'app</span><b>${cur} pz</b></div>
      <div class="b">Quanti ce ne sono davvero?</div>
      <div class="stepper"><button style="background:var(--soft)" ${A('corrVal', { d: -1 })} aria-label="Uno in meno">−</button>
        <input id="corr-val" class="val" style="font:inherit;font-size:26px;font-weight:700;text-align:center;width:100%" inputmode="numeric" value="${val}" data-bind="corrValInput">
        <button style="background:var(--ink);color:#fff" ${A('corrVal', { d: 1 })} aria-label="Uno in più">+</button></div>
      <div class="info small b" style="background:${diff < 0 ? 'var(--red-bg)' : diff > 0 ? 'var(--green-bg)' : 'var(--bg)'};color:${diff < 0 ? 'var(--red-ink)' : diff > 0 ? 'var(--green-ink)' : 'var(--muted)'}">${diff === 0 ? 'Nessuna differenza' : diff < 0 ? 'Mancano ' + -diff + ' pz: escono dai lotti che scadono prima' : 'Ci sono ' + diff + ' pz in più'}</div>
      ${diff > 0 ? `<label class="field">In quale lotto li aggiungo?${targetSelect(p.id, r.target, 'corr.target', 'corr-target')}<span class="muted xsmall" style="font-weight:400">Se non lo sai, si crea un lotto di correzione con la data di oggi e scadenza stimata.</span></label>` : ''}
    </div>
    <div class="field">Motivo<div class="chips" style="font-weight:400">${['Errore di conteggio', 'Ammanco', 'Rottura non registrata', 'Trovati in più', 'Altro'].map((x) => `<button class="chip ${r.reason === x ? 'on' : ''}" ${A('corrReason', { r: x })}>${x}</button>`).join('')}</div></div>
    <button class="btn big ${ready ? 'dark' : 'off'}" ${A('doCorr')}>Conferma correzione</button>
  </div>`;
}

/* ---------- anagrafica ---------- */
function anagraficaView() {
  const tabs = seg([['prodotti', 'Prodotti'], ['categorie', 'Categorie']], S.prodTab, 'prodTab', 't');
  if (S.prodTab === 'categorie') {
    const ce = S.catEdit;
    return `${tabs}<div class="muted small">La durata tipica serve a stimare la scadenza quando al carico non viene indicata. Lasciala vuota per i prodotti che non scadono.</div>
      <div class="tablewrap" style="max-width:820px"><table class="t"><thead><tr><th>Categoria</th><th>Durata tipica (giorni)</th><th class="num">Prodotti</th><th></th></tr></thead><tbody>
      ${categories().map((c) => ce && ce.id === c.id ? `<tr><td class="main" data-l="Categoria"><input id="ce-name" class="input sm" value="${esc(ce.name)}" data-bind="catEdit.name"></td><td data-l="Durata"><input id="ce-life" class="input sm" inputmode="numeric" value="${esc(ce.shelf_life_days)}" data-bind="catEdit.shelf_life_days" placeholder="non scade"></td><td></td><td data-l=""><div class="row" style="gap:6px"><button class="btn xs dark" ${A('saveCat')}>Salva</button><button class="btn xs ghost" ${A('cancelCat')}>Annulla</button></div></td></tr>`
        : `<tr><td class="main" data-l="Categoria">${esc(c.name)}</td><td data-l="Durata tipica">${c.shelf_life_days ? c.shelf_life_days + ' giorni (~' + lifeText(c.shelf_life_days) + ')' : 'non scade'}</td><td class="num" data-l="Prodotti">${products().filter((p) => p.category_id === c.id && p.status === 'attivo').length}</td><td data-l=""><div class="row" style="gap:6px;justify-content:flex-end"><button class="btn xs ghost" ${A('editCat', { id: c.id })}>Modifica</button><button class="btn xs danger" ${A('delCat', { id: c.id })}>Elimina</button></div></td></tr>`).join('')}
      ${ce && ce.id === 'new' ? `<tr><td class="main"><input id="ce-name" class="input sm" value="${esc(ce.name)}" data-bind="catEdit.name" placeholder="nome"></td><td><input id="ce-life" class="input sm" inputmode="numeric" value="${esc(ce.shelf_life_days)}" data-bind="catEdit.shelf_life_days" placeholder="non scade"></td><td></td><td><div class="row" style="gap:6px"><button class="btn xs dark" ${A('saveCat')}>Salva</button><button class="btn xs ghost" ${A('cancelCat')}>Annulla</button></div></td></tr>` : ''}
      </tbody></table></div><button class="btn ghost" style="align-self:flex-start" ${A('newCat')}>${ic('plus')} Nuova categoria</button>`;
  }
  const rows = activeProducts().filter(qMatch).map((p) => `<tr class="click ${S.selProd === p.id ? 'sel' : ''}" ${A('openProd', { id: p.id })}>
      <td class="main" data-l="Prodotto"><div class="row">${thumb(p, 36)}<span>${esc(p.name)}</span></div></td>
      <td data-l="Categoria">${esc(p.category || '—')}</td>
      <td data-l="Barcode">${p.barcodes.length ? p.barcodes.length + (p.barcodes.length === 1 ? ' codice' : ' codici') : pill('senza barcode', 'grey')}</td>
      <td class="num" data-l="Minimo">${p.min_stock}</td><td class="num" data-l="Ideale">${p.ideal_stock || '—'}</td><td class="num" data-l="Giacenza">${p.stock}</td></tr>`).join('');
  const table = `${tabs}<div class="toolbar"><input id="q-an" class="input" placeholder="Cerca prodotto o barcode…" value="${esc(S.q)}" data-bind="q"><button class="btn sm green" ${A('openProd', { id: 'new' })}>${ic('plus', 18)} Nuovo prodotto</button><button class="btn sm ghost" ${A('sheet', { k: 'import' })}>Importa da CSV</button><button class="btn sm ghost" ${A('fillPhotos')}>Cerca foto mancanti</button></div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Prodotto</th><th>Categoria</th><th>Barcode</th><th class="num">Minimo</th><th class="num">Ideale</th><th class="num">Giacenza</th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="muted">Nessun prodotto: creane uno o importali da CSV.</td></tr>'}</tbody></table></div>`;
  if (pc()) return `<div class="pcsplit"><div class="main">${table}</div>${S.selProd ? `<div class="panel card" style="max-height:calc(100vh - 150px);overflow:auto">${prodEditor()}</div>` : `<div class="panel info white">Seleziona un prodotto per modificarlo, oppure creane uno nuovo.</div>`}</div>`;
  return table;
}
function prodEditor() {
  const f = S.pe;
  if (!f) return loading();
  const isNew = f.id === 'new';
  const p = isNew ? { name: f.name, cat_color: '#DCE2DF' } : P(f.id) || {};
  const catOpts = categories().map((c) => `<option value="${c.id}" ${String(c.id) === String(f.category_id) ? 'selected' : ''}>${esc(c.name)}${c.shelf_life_days ? ' (scade in ~' + lifeText(c.shelf_life_days) + ')' : ' (non scade)'}</option>`).join('');
  const proj = f.projection && f.projection[0];
  return `
    <div class="row"><div class="title grow">${isNew ? 'Nuovo prodotto' : 'Scheda prodotto'}</div>${pc() ? `<button class="iconbtn" ${A('closeProd')} aria-label="Chiudi">${ic('x', 20)}</button>` : ''}</div>
    ${isNew ? '<div class="muted small">La foto si cerca da sola su Open Food Facts dopo il salvataggio (prima per barcode, poi per nome).</div>' : `
    <div class="row" style="gap:12px">${thumb(p, 84)}<div style="display:flex;flex-direction:column;gap:6px" class="grow">
      <button class="btn sm ghost" ${A('photoSearch')}>${ic('search', 18)} Cerca foto online</button>
      <button class="btn sm ghost" ${A('photoUpload')}>${ic('camera', 18)} Scatta o carica foto</button></div></div>
    ${S.photoResults === 'loading' ? '<div class="loading">Cerco su Open Food Facts…</div>' : Array.isArray(S.photoResults) ? (S.photoResults.length ? `<div class="grid3">${S.photoResults.map((r, i) => `<button class="photo-opt" ${A('photoPick', { i })} title="${esc(r.name)}"><img src="${esc(r.thumb_url)}" alt="${esc(r.name)}"></button>`).join('')}</div>` : '<div class="muted small">Nessuna foto trovata.</div>') : ''}`}
    <label class="field">Nome<input id="pe-name" class="input" value="${esc(f.name)}" data-bind="pe.name"></label>
    <label class="field">Categoria<select id="pe-cat" class="input" data-bind="pe.category_id"><option value="">—</option>${catOpts}</select></label>
    <div class="grid2"><label class="field">Scorta minima<input id="pe-min" class="input" inputmode="numeric" value="${esc(f.min_stock)}" data-bind="pe.min_stock"></label><label class="field">Scorta ideale fissa<input id="pe-ideal" class="input" inputmode="numeric" value="${esc(f.ideal_stock == null ? '' : f.ideal_stock)}" placeholder="facoltativa" data-bind="pe.ideal_stock"></label></div>
    ${proj && proj.ideal_dyn ? `<div class="info blue small"><b>Scorta ideale suggerita: ${proj.ideal_dyn.qty} pz.</b> Nei prossimi ${proj.ideal_dyn.period_days} giorni dell'anno scorso ne sono usciti ${Math.round(proj.ideal_dyn.last_year)}${proj.ideal_dyn.trend ? '; quest\'anno il consumo è ' + (proj.ideal_dyn.trend >= 1 ? '+' : '') + Math.round((proj.ideal_dyn.trend - 1) * 100) + '%' : ''}.</div>` : ''}
    <div class="field">Codici a barre
      <div class="chips" style="font-weight:400">${(f.codes || []).map((c) => `<span class="pill grey" style="font-size:14px;padding:6px 10px">${esc(c)} <button class="iconbtn" style="width:24px;height:24px;background:transparent" ${A('peDelCode', { c })} aria-label="Rimuovi codice">${ic('x', 14)}</button></span>`).join('')}
      <button class="btn xs outline" ${A('peScanCode')}>${ic('scan', 16)} Aggiungi scansionando</button></div>
      ${(f.codes || []).length ? '' : '<span class="muted xsmall" style="font-weight:400">Senza barcode: si sceglie per nome o dalla griglia.</span>'}</div>
    <button class="btn green" ${A('saveProd')}>Salva</button>
    ${isNew ? '' : (p.stock ? `<div class="muted xsmall">Per eliminarlo la giacenza deve essere zero (Correggi quantità).</div>` : `<button class="btn danger" ${A('delProd', { id: f.id })}>Elimina prodotto</button>`)}`;
}

/* ---------- proposte ---------- */
function proposteView() {
  const d = S.data.props;
  if (!d) return loading();
  const others = activeProducts().map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  const cards = d.products.map((p) => `<div class="card" style="max-width:640px">
      <div class="row" style="gap:12px;align-items:flex-start">${thumb(p, 76)}<div style="display:flex;flex-direction:column;gap:3px">
        <span class="xsmall b" style="color:var(--blue)">NUOVO PRODOTTO</span><span class="title" style="font-size:18px">${esc(p.name)}</span>
        <span class="muted small">${esc(p.category || 'senza categoria')}</span><span class="muted small">Proposto da ${esc(p.proposed_by_name || '—')}, ${esc(fmtTs(p.proposed_at))}</span></div></div>
      ${p.proposal_note ? `<div class="info white small" style="background:var(--bg)">«${esc(p.proposal_note)}»</div>` : ''}
      <div class="grid2"><label class="field">Scorta minima<input id="pm-${p.id}" class="input" inputmode="numeric" value="0"></label><label class="field">Scorta ideale<input id="pi-${p.id}" class="input" inputmode="numeric" placeholder="facoltativa"></label></div>
      <button class="btn green" ${A('approveProp', { id: p.id })}>Approva: da ora si può caricare</button>
      <div class="field">Esiste già? Unisci a:<div class="row"><select id="mg-${p.id}" class="input">${others}</select><button class="btn ghost" ${A('mergeProp', { id: p.id })}>Unisci</button></div></div>
      <button class="btn danger" ${A('rejectProp', { id: p.id })}>Rifiuta</button>
    </div>`).join('');
  const codes = d.barcodes.map((c) => `<div class="card" style="max-width:640px"><span class="xsmall b" style="color:var(--blue)">NUOVO BARCODE</span>
      <div style="line-height:1.4">${esc(c.added_by || '—')} ha associato il codice <b>${esc(c.code)}</b> a <b>${esc(c.product)}</b>.</div>
      <div class="muted small">Finché non lo approvi il codice non funziona.</div>
      <div class="grid2"><button class="btn green" ${A('approveCode', { c: c.code })}>Approva</button><button class="btn danger" ${A('rejectCode', { c: c.code })}>Rifiuta</button></div></div>`).join('');
  return cards + codes || '<div class="empty muted" style="max-width:640px">Nessuna proposta in attesa.</div>';
}

/* ---------- storico movimenti (senza nomi) ---------- */
function storicoView() {
  const h = S.data.hist;
  return `<div class="toolbar"><div class="chips">${[['', 'Tutti'], ['carico', 'Carichi'], ['vendita', 'Vendite'], ['scarico', 'Scarichi'], ['correzione', 'Correzioni'], ['inventario', 'Inventari']].map((x) => `<button class="chip ${S.histType === x[0] ? 'on' : ''}" ${A('histType', { t: x[0] })}>${x[1]}</button>`).join('')}</div><span class="grow"></span><a class="btn sm ghost" href="api/export/movimenti.csv" download>Esporta CSV</a></div>
    <div class="muted small">Tutti i movimenti di magazzino, senza i nomi di chi li ha fatti${isAdmin() ? ' (i dettagli completi sono nel Log)' : ''}.</div>
    ${h ? docsTable(h.items) : loading()}`;
}

/* ---------- log (solo admin) ---------- */
function logView() {
  const lg = S.data.log;
  const cats = lg ? lg.categories : {};
  const users = (S.data.users || []).map((u) => `<option value="${u.id}" ${String(S.logUser) === String(u.id) ? 'selected' : ''}>${esc(u.display_name)}</option>`).join('');
  const qs = new URLSearchParams({ category: S.logCat, user_id: S.logUser, q: S.logQ }).toString();
  return `<div class="chips">${[['', 'Tutto'], ...Object.entries(cats)].map((c) => `<button class="chip ${S.logCat === c[0] ? 'on' : ''}" ${A('logCat', { c: c[0] })}>${c[1]}</button>`).join('')}</div>
    <div class="toolbar"><select id="log-user" class="input" style="min-width:200px" data-bind="logUser" data-reload="log"><option value="">Tutte le persone</option>${users}</select>
      <input id="log-q" class="input" placeholder="Cerca nel log…" value="${esc(S.logQ)}" data-bind="logQ" data-reload="log">
      <span class="grow"></span><a class="btn sm ghost" href="api/log.csv?${qs}" download>Esporta CSV</a></div>
    <div class="muted small">Tutto ciò che succede nell'app, con persona, dispositivo e indirizzo. Si conserva per sempre.${lg ? ' Voci: ' + lg.total + '.' : ''}</div>
    ${lg ? `<div class="tablewrap"><table class="t"><thead><tr><th>Quando</th><th>Chi</th><th>Dispositivo</th><th>Tipo</th><th>Cosa</th><th>Dettaglio</th></tr></thead><tbody>
    ${lg.items.map((e) => `<tr><td data-l="Quando" style="white-space:nowrap">${esc(fmtTs(e.ts))}</td><td class="main" data-l="Chi">${esc(e.user_name || '—')}</td><td data-l="Dispositivo"><span>${esc(e.device || '—')}<br><span class="xsmall muted">${esc(e.ip || '')}</span></span></td>
      <td data-l="Tipo">${pill(cats[e.category] || e.category, 'grey')}</td><td data-l="Cosa"><b>${esc(e.action)}</b></td><td data-l="Dettaglio" class="small"><span>${esc(e.detail || '')}</span></td></tr>`).join('') || '<tr><td colspan="6" class="muted">Nessuna voce.</td></tr>'}
    </tbody></table></div>` : loading()}`;
}

/* ---------- utenti e dispositivi (solo admin) ---------- */
function utentiView() {
  const us = S.data.users;
  if (!us) return loading();
  const rows = us.map((u) => {
    const st = !u.active ? pill('Disattivato', 'grey') : u.locked_until && u.locked_until > new Date().toISOString().replace('T', ' ') ? pill('Bloccato fino alle ' + u.locked_until.slice(11, 16), 'bad') : pill('Attivo', 'ok');
    return `<tr class="click ${S.selUser === u.id ? 'sel' : ''}" ${A('openUser', { id: u.id })}><td class="main" data-l="Nome">${esc(u.display_name)}</td><td data-l="Utente">${esc(u.username)}</td><td data-l="Ruolo">${roleLabel(u.role)}</td>
      <td class="num" data-l="Dispositivi"><span>${u.devices} ${ic('device', 16)}</span></td><td data-l="Ultimo accesso">${esc(u.last_login ? fmtTs(u.last_login) : 'mai')}</td><td data-l="Stato">${st}</td></tr>`;
  }).join('');
  const table = `<div class="toolbar"><button class="btn sm green" ${A('openUser', { id: 'new' })}>${ic('plus', 18)} Nuovo utente</button></div>
    <div class="tablewrap"><table class="t"><thead><tr><th>Nome</th><th>Utente</th><th>Ruolo</th><th class="num">Dispositivi</th><th>Ultimo accesso</th><th>Stato</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="info white small">Ogni dispositivo resta collegato finché l'utente non esce o finché lo disconnetti tu. Cambiare il PIN o disattivare l'utente lo disconnette ovunque.</div>`;
  if (pc()) return `<div class="pcsplit"><div class="main">${table}</div>${S.selUser ? `<div class="panel card" style="max-height:calc(100vh - 150px);overflow:auto">${userPanel()}</div>` : `<div class="panel info white">Seleziona un utente per vedere i dispositivi collegati e gestire PIN e ruolo.</div>`}</div>`;
  return table;
}
function userPanel() {
  if (S.selUser === 'new') {
    const n = S.newUser;
    return `<div class="row"><div class="title grow">Nuovo utente</div>${pc() ? `<button class="iconbtn" ${A('closeUser')} aria-label="Chiudi">${ic('x', 20)}</button>` : ''}</div>
      <label class="field">Nome e cognome<input id="nu-name" class="input" value="${esc(n.display_name)}" data-bind="newUser.display_name" placeholder="es. Sara Bianchi"></label>
      <label class="field">Nome utente<input id="nu-user" class="input" value="${esc(n.username)}" data-bind="newUser.username" placeholder="es. sara" autocapitalize="none"></label>
      <div class="field">Ruolo${seg([['operatore', 'Operatore'], ['coordinatore', 'Coordinatore']], n.role, 'nuRole', 'r')}
        <span class="muted xsmall" style="font-weight:400">${n.role === 'operatore' ? 'Carico, scarico, giacenze, lista della spesa, proposte di nuovi prodotti.' : 'Tutto tranne utenti e log.'}</span></div>
      <label class="field">PIN (4–8 cifre)<input id="nu-pin" class="input" inputmode="numeric" value="${esc(n.pin)}" data-bind="newUser.pin" placeholder="••••"></label>
      <button class="btn dark" ${A('createUser')}>Crea utente</button>`;
  }
  const u = (S.data.users || []).find((x) => x.id === S.selUser);
  if (!u) return loading();
  const sess = (S.data.sessions || {})[u.id];
  const locked = u.locked_until && u.locked_until > new Date().toISOString().replace('T', ' ');
  return `<div class="row"><div class="grow"><div class="title">${esc(u.display_name)}</div><div class="muted small">${esc(u.username)} · ${roleLabel(u.role)}</div></div>${pc() ? `<button class="iconbtn" ${A('closeUser')} aria-label="Chiudi">${ic('x', 20)}</button>` : ''}</div>
    ${locked ? `<div class="info amber small row">Bloccato per troppi tentativi fino alle ${esc(u.locked_until.slice(11, 16))}<span class="grow"></span><button class="btn xs dark" ${A('unlockUser', { id: u.id })}>Sblocca</button></div>` : ''}
    <div class="field">Dispositivi collegati${sess ? ' (' + sess.length + ')' : ''}
      ${sess ? `<div class="list" style="background:var(--bg);font-weight:400">${sess.map((s) => `<div class="li"><span class="row" style="gap:10px">${ic(/Windows|Mac|Linux/.test(s.device || '') ? 'pc' : 'device', 22, '#4A5752')}<span><b>${esc(s.device_name || s.device)}</b>${s.current ? ' ' + pill('questo', 'blue') : ''}<br><span class="muted xsmall">${esc(s.device || '')} · dal ${esc(fmt(s.created_at))} · ultimo uso ${esc(fmtTs(s.last_seen))}${s.ip ? ' · ' + esc(s.ip) : ''}</span></span></span>
        ${s.current ? '' : `<button class="btn xs danger" ${A('kick', { id: s.id })}>Disconnetti</button>`}</div>`).join('') || '<div class="li muted">Nessun dispositivo collegato</div>'}</div>
      ${sess.length > 1 ? `<button class="btn sm ghost" ${A('kickAll', { id: u.id })}>Disconnetti da tutti i dispositivi</button>` : ''}` : loading()}</div>
    <button class="btn sm ghost" ${A('userLog', { id: u.id })}>${ic('shield', 18)} Vedi nel log</button>
    ${u.role === 'admin' ? `<div class="field">Cambia password<input id="pw-cur" class="input" type="password" placeholder="password attuale" value="${esc(S.pw.current)}" data-bind="pw.current"><input id="pw-new" class="input" type="password" placeholder="nuova password (almeno 8 caratteri)" value="${esc(S.pw.new)}" data-bind="pw.new"><button class="btn dark" ${A('changePw')}>Cambia password</button></div><div class="muted xsmall">Se la dimentichi si reimposta dalle opzioni dell'add-on in Home Assistant.</div>` : `
      <div class="field">Ruolo${seg([['operatore', 'Operatore'], ['coordinatore', 'Coordinatore']], u.role, 'setRole', 'r')}</div>
      <div class="field">Nuovo PIN<div class="row"><input id="pin-new" class="input" inputmode="numeric" placeholder="4–8 cifre" value="${esc(S.pinNew)}" data-bind="pinNew"><button class="btn ghost" ${A('resetPin', { id: u.id })}>Imposta</button></div></div>
      <button class="btn danger" ${A('toggleActive', { id: u.id })}>${u.active ? 'Disattiva utente' : 'Riattiva utente'}</button>`}`;
}

/* ---------- impostazioni (con notifiche ntfy) ---------- */
function impostazioniView() {
  const s = S.st;
  if (!s) return loading();
  const chk = (k, label) => `<label class="row b"><input type="checkbox" ${s[k] === '1' ? 'checked' : ''} ${A('stToggle', { k })} style="width:22px;height:22px;accent-color:var(--green)"> ${label}</label>`;
  const sel = (k, opts) => `<select class="input" id="st-${k}" data-bind="st.${k}">${opts.map((o) => `<option value="${o[0]}" ${String(s[k]) === String(o[0]) ? 'selected' : ''}>${o[1]}</option>`).join('')}</select>`;
  const topic = (label, t, who) => { const url = (s.ntfy_server || 'https://ntfy.sh').replace(/\/$/, '') + '/' + t; return `<div class="card" style="background:var(--bg);gap:8px"><div class="row" style="align-items:flex-start"><span class="grow"><b>${label}</b><br><span class="small muted">${who}</span><br><span class="small">Canale: <b>${esc(t)}</b></span></span>
      <div class="qr" style="background:#fff;padding:6px;border-radius:10px">${qrSvg(url)}</div></div>
      <div class="row"><input class="input sm" readonly value="${esc(url)}" id="url-${esc(t)}"><button class="btn xs ghost" ${A('copy', { t: url })}>Copia</button></div></div>`; };
  return `<div class="card" style="max-width:780px;gap:14px">
    <div class="title">Generali</div>
    <label class="field">Nome del bar<input id="st-bar" class="input" value="${esc(s.bar_name)}" data-bind="st.bar_name"></label>
    <div class="grid2">
      <label class="field">Uscite medie calcolate su${sel('window_days', [['28', '4 settimane'], ['56', '8 settimane'], ['84', '12 settimane']])}</label>
      <label class="field">Scadenze: avvisa con${sel('expiry_warning_days', [['7', '7 giorni di anticipo'], ['14', '14 giorni di anticipo'], ['21', '21 giorni di anticipo'], ['30', '30 giorni di anticipo']])}</label>
      <label class="field">Scorta ideale: periodo coperto${sel('ideal_period_days', [['30', '30 giorni'], ['45', '45 giorni'], ['60', '60 giorni']])}</label>
      <label class="field">Modifica dei propri movimenti<input class="input" disabled value="entro ${esc(s.edit_minutes)} minuti"></label>
    </div>
    ${chk('include_inventory_loss', "Gli ammanchi trovati con l'inventario contano come consumo")}
    ${chk('photo_lookup', 'Cerca le foto dei prodotti su Open Food Facts')}
  </div>
  <div class="card" style="max-width:780px;gap:14px">
    <div class="row">${ic('bell', 24)}<div class="title grow">Notifiche (ntfy)</div></div>
    <div class="muted small" style="line-height:1.4">Le notifiche arrivano con l'app gratuita <b>ntfy</b> (Android e iPhone). Chi deve riceverle installa l'app, tocca «+» e scrive il nome del canale del suo ruolo (oppure inquadra il QR). I nomi sono lunghi e casuali, così non li indovina nessuno.</div>
    <div class="grid2">${topic('Canale coordinatori', s.ntfy_topic_coord, 'proposte, liste chiuse, riepilogo')}${topic('Canale amministratore', s.ntfy_topic_admin, 'riepilogo settimanale')}</div>
    ${chk('ntfy_proposals', 'Nuove proposte di prodotti o barcode → coordinatori')}
    ${chk('ntfy_list_closed', 'Lista della spesa chiusa → coordinatori')}
    ${chk('ntfy_digest', 'Riepilogo settimanale → coordinatori e amministratore')}
    <div class="grid2" style="padding-left:32px">
      <label class="field">Giorno e ora<div class="row">${sel('digest_weekday', [['0', 'lunedì'], ['1', 'martedì'], ['2', 'mercoledì'], ['3', 'giovedì'], ['4', 'venerdì'], ['5', 'sabato'], ['6', 'domenica']])}<input id="st-time" class="input" style="width:110px;flex:none" value="${esc(s.digest_time)}" data-bind="st.digest_time" placeholder="15:00"></div></label>
      <div class="muted small" style="line-height:1.4">Contiene i prodotti sotto la scorta minima e i lotti che scadono nei prossimi ${esc(s.expiry_warning_days)} giorni.</div>
    </div>
    <label class="field">Server ntfy<input id="st-ntfy" class="input" value="${esc(s.ntfy_server)}" data-bind="st.ntfy_server"></label>
    <div class="row" style="flex-wrap:wrap"><button class="btn ghost" ${A('ntfyTest')}>Invia notifica di prova</button><button class="btn ghost" ${A('ntfyDigest')}>Invia il riepilogo adesso</button></div>
  </div>
  <div style="max-width:780px"><button class="btn big green" ${A('saveSettings')}>Salva le impostazioni</button></div>`;
}
/* =====================================================================
   CORNICI: telefono e PC
   ===================================================================== */
const TITLES = {
  home: 'Bar Oratorio', carico: 'Arriva merce', scarico: 'Esce merce', giacenze: "Quanto c'è", proponi: 'Proponi prodotto', done: 'Fatto',
  spesa: 'Lista della spesa', miei: 'I miei movimenti', inventario: 'Fare inventario',
  dashboard: 'Panoramica', prospetto: 'Prospetto', lotti: 'Lotti', correggi: 'Correggi quantità',
  anagrafica: 'Anagrafica', proposte: 'Proposte', storico: 'Storico movimenti', log: 'Log', utenti: 'Utenti e dispositivi', impostazioni: 'Impostazioni',
};
const OP_SCREENS = ['carico', 'scarico', 'proponi', 'done', 'miei'];
const STAFF_ONLY = ['dashboard', 'prospetto', 'lotti', 'correggi', 'anagrafica', 'proposte', 'storico', 'log', 'utenti', 'impostazioni'];

function staffContent(screen) {
  switch (screen) {
    case 'giacenze': return giacenzeStaff();
    case 'lotti': return lottiView();
    case 'prospetto': return prospettoView();
    case 'spesa': return spesaStaff();
    case 'inventario': return inventarioView();
    case 'correggi': return correggiView();
    case 'anagrafica': return anagraficaView();
    case 'proposte': return proposteView();
    case 'storico': return storicoView();
    case 'log': return isAdmin() ? logView() : '';
    case 'utenti': return isAdmin() ? utentiView() : '';
    case 'impostazioni': return impostazioniView();
    default: return dashboardView();
  }
}
function opContent(screen) {
  switch (screen) {
    case 'carico': return caricoView();
    case 'scarico': return scaricoView();
    case 'proponi': return proponiView();
    case 'done': return doneView();
    case 'giacenze': return giacenzeOp();
    case 'spesa': return spesaOp();
    case 'miei': return mieiView();
    case 'inventario': return '<div class="scroll">' + inventarioView() + '</div>';
    default: return opHome();
  }
}
function phoneShell() {
  const scr = S.screen;
  const staff = isStaff();
  let content;
  if (scr === 'home' || scr === 'dashboard') content = staff ? staffPhoneHome() : opHome();
  else if (OP_SCREENS.includes(scr) || !staff) content = opContent(scr);
  else content = `<div class="scroll">${staffContent(scr)}</div>`;
  const isHome = scr === 'home' || scr === 'dashboard';
  return `<div class="topbar">
      ${!isHome ? `<button class="btn back" ${A('home')} aria-label="Torna all'inizio">${ic('back', 20)}Indietro</button>` : ''}
      <h1>${isHome ? esc((S.boot && S.boot.settings.bar_name) || 'Bar Oratorio') : TITLES[scr]}</h1>
      <div class="who"><b>${esc(S.user.display_name.split(' ')[0])}</b><br>${roleLabel(S.user.role)}</div>
      ${isHome ? `<button class="iconbtn" ${A('sheet', { k: 'logout' })} aria-label="Esci">${ic('logout', 22)}</button>` : ''}
    </div>${content}`;
}
function pcShell() {
  const c = counts();
  const scr = S.screen === 'home' ? 'dashboard' : S.screen;
  const item = (s, label, icon, cnt) => `<button class="${scr === s ? 'on' : ''}" ${A('go', { s })}>${ic(icon, 20)}${label}${cnt ? `<span class="cnt">${cnt}</span>` : ''}</button>`;
  const body = OP_SCREENS.includes(scr) ? `<div class="opwrap">${opContent(scr)}</div>` : `<div class="pcbody">${staffContent(scr)}</div>`;
  return `<div class="pcshell">
    <nav class="side" aria-label="Menu">
      <div class="brand">${ic('cup', 26, '#fff')}<b>${esc(S.boot.settings.bar_name || 'Bar Oratorio')}</b></div>
      ${item('dashboard', 'Panoramica', 'home')}
      <div class="sec">Operatività</div>
      ${item('carico', 'Arriva merce', 'in', c.my_draft ? '•' : 0)}${item('scarico', 'Esce merce', 'out')}${item('giacenze', 'Giacenze', 'list')}${item('spesa', 'Lista della spesa', 'cart', c.shop_open)}${item('miei', 'I miei movimenti', 'history')}
      <div class="sec">Controllo</div>
      ${item('prospetto', 'Prospetto', 'chart')}${item('lotti', 'Lotti', 'layers', c.expiring)}${item('inventario', 'Fare inventario', 'clip', S.boot.open_inventory ? '•' : 0)}${item('correggi', 'Correggi quantità', 'edit')}${item('storico', 'Storico movimenti', 'history')}
      <div class="sec">Anagrafica</div>
      ${item('anagrafica', 'Prodotti', 'tag')}${item('proposte', 'Proposte', 'inbox', c.proposals)}
      <div class="sec">Sistema</div>
      ${isAdmin() ? item('log', 'Log', 'shield') + item('utenti', 'Utenti e dispositivi', 'users') : ''}${item('impostazioni', 'Impostazioni', 'gear')}
      <div class="me"><div><b style="color:#fff">${esc(S.user.display_name)}</b><br>${roleLabel(S.user.role)}</div><div class="row" style="gap:6px;color:#8CC2AD">${ic('pc', 16, '#8CC2AD')} ${esc(S.user.device_name || '')}</div>
        <button ${A('sheet', { k: 'logout' })} style="padding:8px 0">${ic('logout', 18)} Esci da questo dispositivo</button></div>
    </nav>
    <div class="pcmain">
      <div class="pchead"><h1>${TITLES[scr]}</h1><button class="btn sm ghost" ${A('reload')} aria-label="Aggiorna">${ic('refresh', 18)} Aggiorna</button></div>
      ${body}
    </div></div>`;
}

/* ---------------- fogli a comparsa ---------------- */
function editSheet() {
  const e = S.edit;
  const isC = e.type === 'carico';
  return `<div><div class="title" style="font-size:20px">Modifica ${isC ? 'carico · lotto ' + esc(e.cart) : e.type === 'vendita' ? 'vendita' : 'scarico'}</div><div class="muted small">Ancora ${e.minutes_left} minuti per modificarlo. Non si può eliminare tutto.</div></div>
    ${e.rows.map((r, i) => { const p = P(r.product_id) || { name: r.name }; const lf = isC && P(r.product_id) ? catLife(P(r.product_id)) : null; return `<div class="card" style="background:${r.delete ? 'var(--red-bg)' : 'var(--bg)'};gap:8px">
      <div class="row">${thumb(p, 40)}<span class="grow title" style="${r.delete ? 'text-decoration:line-through' : ''}">${esc(p.name)}</span>
        <button class="btn xs ${r.delete ? 'dark' : 'danger'}" ${A('editDel', { i })}>${r.delete ? 'Rimetti' : ic('trash', 16) + ' Elimina riga'}</button></div>
      ${r.delete ? '' : `<div class="stepper"><button style="background:#fff" ${A('editQty', { i, d: -1 })} aria-label="Uno in meno">−</button><div class="val">${r.qty}</div><button style="background:var(--ink);color:#fff" ${A('editQty', { i, d: 1 })} aria-label="Uno in più">+</button></div>
        ${r.used ? `<div class="muted xsmall">Ne sono già usciti ${r.used}: non puoi scendere sotto.</div>` : ''}
        ${isC && lf ? `<label class="row b small"><span style="width:60px">Scade il</span><input id="ed-exp-${i}" type="date" class="input" style="height:44px" value="${esc(r.expiry || '')}" data-bind="editExp" data-i="${i}"></label>` : ''}`}
    </div>`; }).join('')}
    <div class="grid2"><button class="btn outline" ${A('editAddRow')}>${ic('plus', 18)} Prodotto dimenticato</button><button class="btn outline" ${A('editScan')}>${ic('scan', 18)} Scansiona</button></div>
    ${isC ? `<label class="field">Nota<input id="ed-note" class="input" value="${esc(e.note || '')}" data-bind="edit.note"></label>` : ''}
    <button class="btn big green" ${A('saveEdit')}>Salva modifiche</button>
    <button class="btn ghost" ${A('closeEdit')}>Lascia com'era</button>`;
}
function pickList(action) {
  return `<input id="q-pick" class="input" placeholder="Scrivi il nome…" value="${esc(S.q)}" data-bind="q">
    <div style="display:flex;flex-direction:column;gap:6px;max-height:420px;overflow-y:auto">${activeProducts().filter(qMatch).map((p) => `<button class="row" style="padding:8px;border-radius:14px;border:none;background:var(--bg);text-align:left" ${A(action, { id: p.id })}>${thumb(p, 44)}<span class="grow"><b>${esc(p.name)}</b><br><span class="muted xsmall">${esc(p.category || '')}${p.barcodes.length ? '' : ' · senza barcode'}</span></span>${ic('plus', 24, '#1F6B52')}</button>`).join('') || '<div class="muted">Nessun prodotto.</div>'}</div>`;
}
function sheetView() {
  const k = S.sheet;
  if (!k) return '';
  let inner = '';
  if (k === 'unknown') {
    const u = S.unknown, off = u.off;
    inner = `<div class="row">${ic('warn', 30, '#8A5A00')}<div class="title" style="font-size:20px">Prodotto non in elenco</div></div>
      <div class="row" style="gap:12px">${off && off.thumb_url ? `<div class="thumb" style="width:72px;height:72px;background:#fff"><img src="${esc(off.thumb_url)}" alt=""></div>` : ''}
        <div style="font-size:17px;line-height:1.4">Il codice <b>${esc(u.code)}</b> non è registrato.${off && off.name ? ` Online risulta: <b>${esc(off.name)}</b>${off.brand ? ' (' + esc(off.brand) + ')' : ''}.` : ''}</div></div>
      ${isStaff() ? `<button class="btn big green" ${A('unknownCreate')}>Crea il prodotto</button>`
        : `<div class="muted" style="line-height:1.4">Puoi proporlo al coordinatore. Finché non lo approva non si può caricare: mettilo da parte.</div><button class="btn big blue" ${A('unknownPropose')}>È un prodotto nuovo: proponilo</button>`}
      <button class="btn outline" ${A('unknownAssoc')}>È un prodotto che c'è già: associa il codice</button>
      <button class="btn ghost" ${A('closeSheet')}>Lascia stare</button>`;
  } else if (k === 'assoc') {
    inner = `<div class="title" style="font-size:20px">A quale prodotto appartiene il codice ${esc(S.unknown.code)}?</div>${isStaff() ? '' : '<div class="muted small">Il coordinatore lo deve approvare prima che funzioni.</div>'}${pickList('assocTo')}<button class="btn ghost" ${A('closeSheet')}>Annulla</button>`;
  } else if (k === 'pending') {
    inner = `<div class="row">${ic('lock', 30, '#4A5752')}<div class="title" style="font-size:20px">In attesa di approvazione</div></div>
      <div style="font-size:17px;line-height:1.4"><b>${esc(S.pendingName)}</b> è stato proposto ma il coordinatore non l'ha ancora approvato. Per ora non si può movimentare.</div>
      <button class="btn big dark" ${A('closeSheet')}>Ho capito</button>`;
  } else if (k === 'pick') {
    inner = `<div class="title" style="font-size:20px">Cerca per nome</div>${pickList('pickAdd')}<button class="btn ghost" ${A('closePick')}>Fatto</button>`;
  } else if (k === 'expiry') {
    const miss = S.missing || [];
    inner = `<div class="row">${ic('warn', 30, '#8A5A00')}<div class="title" style="font-size:20px">Mancano delle scadenze</div></div>
      <div style="font-size:17px;line-height:1.4">Sei sicuro di non voler inserire la data? Se continui la stimo io in base al tipo di prodotto e la segno come <b>stimata</b>. Il coordinatore potrà correggerla.</div>
      <div class="list" style="background:var(--amber-bg)">${miss.map((m) => `<div class="li" style="border-color:#F1DFAE"><span>${esc(m.name)}</span><b style="color:#6B4A00">~ ${fmt(m.estimated)}</b></div>`).join('')}</div>
      <button class="btn big dark" ${A('closeSheet')}>Torno a inserire le date</button>
      <button class="btn ghost" ${A('doCarico', { confirm: 1 })}>Sì, sono sicuro: usa la stima</button>`;
  } else if (k === 'invConfirm') {
    const lines = (S.data.inv && S.data.inv.lines) || [];
    const diffs = lines.filter((l) => cnt(l) !== '' && Number(cnt(l)) !== l.stock);
    inner = `<div class="title" style="font-size:20px">Applico queste differenze?</div>
      <div class="list" style="background:var(--bg)">${diffs.map((l) => { const d = Number(cnt(l)) - l.stock; const t = S.invTarget[l.product_id] || l.target; const ln = t && t !== 'nuovo' && (S.prodLots[l.product_id] || []).find((x) => String(x.id) === String(t)); return `<div class="li"><span>${esc(l.name)}${d > 0 ? `<br><span class="xsmall muted">in ${ln ? 'lotto ' + esc(ln.cart) : 'nuovo lotto di correzione'}</span>` : ''}</span><b style="color:${d < 0 ? 'var(--red)' : 'var(--green)'}">${d > 0 ? '+' : ''}${d} pz</b></div>`; }).join('') || '<div class="li muted">Nessuna differenza</div>'}</div>
      <div class="muted small" style="line-height:1.4">I prodotti non contati restano come sono. I pezzi mancanti escono dai lotti che scadono prima.</div>
      <button class="btn big dark" ${A('applyInv')}>Sì, chiudi l'inventario</button>
      <button class="btn ghost" ${A('closeSheet')}>Torna al conteggio</button>`;
  } else if (k === 'cart') {
    inner = cartPanel() + `<button class="btn ghost" ${A('closeSheet')}>Chiudi</button>`;
  } else if (k === 'prod') {
    inner = prodEditor() + `<button class="btn ghost" ${A('closeSheet')}>Chiudi</button>`;
  } else if (k === 'user') {
    inner = userPanel() + `<button class="btn ghost" ${A('closeSheet')}>Chiudi</button>`;
  } else if (k === 'edit') {
    inner = S.edit ? editSheet() : loading();
  } else if (k === 'closeList') {
    const items = (S.data.shop && S.data.shop.items) || [], done = items.filter((i) => i.checked).length;
    inner = `<div class="title" style="font-size:20px">Chiudere la lista della spesa?</div>
      <div style="font-size:17px;line-height:1.4">Hai preso <b>${done} su ${items.length}</b> voci. La lista finisce nell'archivio e i coordinatori ricevono un avviso. ${done < items.length ? 'Le voci non spuntate <b>non</b> vengono riportate in una nuova lista.' : ''}</div>
      <div class="info amber small">Ricordati di caricare la merce con «Arriva merce» quando torni al bar.</div>
      <button class="btn big dark" ${A('closeList')}>Sì, chiudi la lista</button>
      <button class="btn ghost" ${A('closeSheet')}>Non ancora</button>`;
  } else if (k === 'dropDraft') {
    inner = `<div class="title" style="font-size:20px">Svuotare il carrello in bozza?</div>
      <div style="line-height:1.4">Tutte le righe inserite verranno perse. Niente entra in magazzino.</div>
      <button class="btn big danger" ${A('dropDraft')}>Sì, svuota</button><button class="btn ghost" ${A('closeSheet')}>No, tienilo</button>`;
  } else if (k === 'import') {
    inner = `<div class="title" style="font-size:20px">Importa prodotti da CSV</div>
      <div class="muted small" style="line-height:1.4">Prima riga con le intestazioni: <b>nome;categoria;minimo;ideale;barcode</b>. Più barcode separati da spazio. I prodotti già presenti vengono saltati; le foto si cercano da sole.</div>
      <textarea id="imp-csv" class="input" data-bind="importCsv" placeholder="nome;categoria;minimo;ideale;barcode&#10;Coca-Cola lattina 33cl;Bibite;24;48;5449000000996">${esc(S.importCsv || '')}</textarea>
      <button class="btn big dark" ${A('doImport')}>Importa</button><button class="btn ghost" ${A('closeSheet')}>Annulla</button>`;
  } else if (k === 'logout') {
    inner = `<div class="title" style="font-size:20px">Vuoi uscire?</div>
      <div style="line-height:1.4">Su questo dispositivo dovrai rifare l'accesso. Se il telefono è quello del bar, di solito conviene restare collegati. Un carrello in bozza non si perde.</div>
      <button class="btn big dark" ${A('logout')}>Esci</button><button class="btn ghost" ${A('closeSheet')}>Resta collegato</button>`;
  }
  return `<div class="overlay" ${A('overlayClick')}><div class="sheet" role="dialog" aria-modal="true">${inner}</div></div>`;
}

/* ---------------- render ---------------- */
function render() {
  const ae = document.activeElement;
  const fid = ae && ae.id && ae.closest && ae.closest('#root') ? ae.id : null;
  let sel = null;
  try { sel = fid && ae.selectionStart != null ? [ae.selectionStart, ae.selectionEnd] : null; } catch (e) { sel = null; }
  const scrollers = [...document.querySelectorAll('#root .scroll, #root .pcbody, #root .sheet, #root .side')].map((el) => el.scrollTop);
  let inner;
  if (!S.user || !S.boot) inner = loginView();
  else if (S.wide) inner = isStaff() ? pcShell() : `<div class="opwrap">${phoneShell()}</div>`;
  else inner = phoneShell();
  const layout = S.wide ? 'pc' : 'phone';
  document.getElementById('root').innerHTML = `<div class="app ${layout}">${inner}${S.user && S.boot ? sheetView() : ''}${S.toast ? `<div class="toast" role="status">${ic('check', 22, '#7DD3AE')}${esc(S.toast)}</div>` : ''}</div>`;
  const now = [...document.querySelectorAll('#root .scroll, #root .pcbody, #root .sheet, #root .side')];
  if (now.length === scrollers.length) now.forEach((el, i) => { el.scrollTop = scrollers[i]; });
  if (fid) {
    const el = document.getElementById(fid);
    if (el) { el.focus({ preventScroll: true }); if (sel) { try { el.setSelectionRange(sel[0], sel[1]); } catch (e) { /* date/select */ } } }
  }
}

/* ---------------- caricamento dati per schermata ---------------- */
async function refreshBoot() {
  S.boot = await get('bootstrap');
  S.user = S.boot.user;
  S.today = S.boot.today;
}
const enc = encodeURIComponent;
async function load(screen) {
  const s = screen || S.screen;
  try {
    switch (s) {
      case 'home': case 'dashboard':
        await refreshBoot();
        if (pc()) {
          const [proj, exp, hist, drafts] = await Promise.all([get('projection'), get('expiring'), get('history?limit=6'), get('drafts')]);
          S.data.dash = { proj, exp, hist, drafts };
        }
        break;
      case 'carico': S.draft = await get('draft'); S.draftSaved = null; await refreshBoot(); break;
      case 'scarico': case 'giacenze': case 'correggi': case 'anagrafica': case 'proponi': await refreshBoot(); break;
      case 'spesa':
        S.data.shop = await get('shop');
        if (isStaff() && S.shopTab === 'archivio') S.data.archive = await get('shop/archive');
        break;
      case 'miei': S.data.mine = await get('my/batches'); break;
      case 'lotti': {
        const [carts, drafts] = await Promise.all([get(`carts?status=${S.lotFilter}&q=${enc(S.lotQ)}&estimated=${S.lotEst}`), get('drafts')]);
        S.data.carts = carts; S.data.drafts = drafts;
        if (S.selCart) S.data.cart = await get('carts/' + S.selCart);
        break;
      }
      case 'prospetto':
        if (S.ptab === 'mesi') S.data.monthly = await get('monthly?months=6');
        else { const [proj, shop] = await Promise.all([get('projection?horizon=' + S.hz), get('shop')]); S.data.proj = proj; S.data.shop = shop; }
        break;
      case 'inventario':
        S.data.inv = await get('inventory/current');
        if (isStaff() && !S.data.inv) S.data.invList = await get('inventory');
        break;
      case 'proposte': S.data.props = await get('proposals'); await refreshBoot(); break;
      case 'storico': S.data.hist = await get('history?limit=300' + (S.histType ? '&type=' + S.histType : '')); break;
      case 'log': {
        if (!S.data.users) S.data.users = await get('users');
        const qs = new URLSearchParams({ category: S.logCat, q: S.logQ, limit: '300' });
        if (S.logUser) qs.set('user_id', S.logUser);
        S.data.log = await get('log?' + qs.toString());
        break;
      }
      case 'utenti': S.data.users = await get('users'); if (S.selUser && S.selUser !== 'new') await loadSessions(S.selUser); break;
      case 'impostazioni': S.st = await get('settings'); break;
      default: break;
    }
  } catch (e) { if (e.status !== 401) fail(e); }
  render();
}
const reloadSoon = debounce(() => load(), 350);
async function loadSessions(uid) { S.data.sessions = S.data.sessions || {}; S.data.sessions[uid] = await get('users/' + uid + '/sessions'); }
const lotsLoading = new Set();
async function loadProdLots(pid) {
  if (lotsLoading.has(pid)) return;
  lotsLoading.add(pid);
  try { S.prodLots[pid] = await get('products/' + pid + '/lots'); } catch (e) { S.prodLots[pid] = []; }
  lotsLoading.delete(pid);
  render();
}

/* ---------------- carrello in bozza: salvataggio automatico ---------------- */
const saveDraftNow = async () => {
  S.draftSaved = 'saving';
  try {
    const d = await put('draft', { lines: S.draft.lines, note: S.draft.note || '' });
    S.draft.updated_at = d.updated_at; S.draftSaved = 'ok';
    if (S.boot) S.boot.counts.my_draft = S.draft.lines.length;
  } catch (e) { S.draftSaved = 'error'; fail(e); }
  render();
};
const saveDraft = debounce(saveDraftNow, 450);
function addToDraft(pid) {
  const l = S.draft.lines.find((x) => x.product_id === pid);
  if (l) l.qty += 1; else S.draft.lines.push({ product_id: pid, qty: 1, expiry: '' });
  saveDraft();
  toast((P(pid) || {}).name + ' nel carrello');
}

/* ---------------- codici a barre ---------------- */
async function handleCode(code) {
  code = String(code || '').replace(/\s+/g, '');
  if (!code) return;
  if (S.pickFor === 'peCode') { S.pickFor = null; await addCodeToProduct(code); return; }
  let r;
  try { r = await get('lookup/' + enc(code)); } catch (e) { fail(e); return; }
  if (r.product) {
    const p = r.product;
    if (r.pending) { S.pendingName = p.name; S.sheet = 'pending'; render(); return; }
    if (S.edit && S.sheet === 'edit') { addEditRow(p.id); return; }
    if (S.screen === 'scarico') { tapOut(p.id); render(); return; }
    if (S.screen === 'proponi') { toast('Questo codice è già di: ' + p.name); return; }
    if (S.screen === 'inventario') { const el = document.getElementById('inv-' + p.id); if (el) { el.focus(); el.scrollIntoView({ block: 'center' }); } return; }
    if (S.screen !== 'carico') { await go('carico'); }
    addToDraft(p.id);
    return;
  }
  S.unknown = { code, off: r.off };
  S.sheet = 'unknown';
  render();
}
async function addCodeToProduct(code) {
  const f = S.pe;
  if (!f) return;
  if (f.id === 'new') { if (!f.codes.includes(code)) f.codes.push(code); render(); return; }
  try { await post('products/' + f.id + '/barcodes', { code }); f.codes.push(code); await refreshBoot(); toast('Barcode aggiunto'); } catch (e) { fail(e); }
}
function tapOut(id) {
  const p = P(id); if (!p) return;
  const n = S.out[p.id] || 0;
  if (p.stock <= 0) { toast(p.name + ': non ce ne sono'); return; }
  if (n >= p.stock) { toast('Ne restano solo ' + p.stock); return; }
  S.out[p.id] = n + 1;
}
function addEditRow(pid) {
  const e = S.edit;
  const ex = e.rows.find((r) => r.product_id === pid && !r.lot_id && e.type !== 'carico') || e.rows.find((r) => r.product_id === pid && e.type !== 'carico');
  if (ex) { ex.qty += 1; ex.delete = false; } else e.rows.push({ product_id: pid, name: (P(pid) || {}).name, qty: 1, expiry: '', delete: false });
  S.sheet = 'edit'; S.pickFor = null; S.q = '';
  render();
}
function finishDone(kind, title, text, lines, batch) { S.done = { kind, title, text, lines, batch }; S.screen = 'done'; S.sheet = null; }
async function go(s) {
  if (STAFF_ONLY.includes(s) && !isStaff()) s = 'home';
  if ((s === 'log' || s === 'utenti') && !isAdmin()) s = 'home';
  if (s === 'dashboard' && !pc()) s = 'home';
  S.screen = s; S.sheet = null; S.q = ''; S.filter = 'tutti';
  if (s !== 'lotti') { S.selCart = null; S.selLine = null; }
  if (s === 'log') { S.logCat = S.logCat || ''; }
  if (s === 'anagrafica') { S.selProd = null; S.pe = null; S.photoResults = null; }
  render();
  await load(s);
}
/* ---------------- azioni ---------------- */
const ACT = {
  /* accesso */
  async loginNext() {
    const L = S.login; if (!L.username.trim()) return;
    try { const r = await post('login/check', { username: L.username.trim() }); L.step = r.method === 'password' ? 'pass' : 'pin'; L.err = ''; L.pin = ''; L.pass = ''; store('user', L.username.trim()); } catch (e) { L.err = e.message; }
  },
  loginBack() { S.login.step = 'user'; S.login.err = ''; },
  pinKey(d) { const L = S.login; if (d.k === 'C') L.pin = ''; else if (d.k === '←') L.pin = L.pin.slice(0, -1); else if (L.pin.length < 8) L.pin += d.k; },
  async loginDo() {
    const L = S.login;
    const secret = L.step === 'pin' ? L.pin : L.pass;
    if (L.busy || (L.step === 'pin' && L.pin.length < 4) || !secret) return;
    L.busy = true; render();
    try {
      await post('login', { username: L.username.trim(), secret, device_name: L.device.trim() });
      store('device', L.device.trim());
      L.pin = ''; L.pass = ''; L.err = ''; L.step = 'user';
      await refreshBoot();
      S.screen = pc() ? 'dashboard' : 'home';
      load();
    } catch (e) {
      L.err = e.status === 401 ? (L.step === 'pin' ? 'PIN errato' : 'Password errata') + '. Dopo 5 tentativi l\'utente si blocca.' : e.message;
      L.pin = '';
    } finally { L.busy = false; }
  },
  async logout() { try { await post('logout'); } catch (e) { /* già fuori */ } S.user = null; S.boot = null; S.sheet = null; S.data = {}; },

  /* navigazione */
  go(d) { return go(d.s); },
  home() { S.out = {}; S.sub = null; S.reason = 'venduto'; S.done = null; S.edit = null; S.prop = { step: 0, code: null, name: '', cat: null, note: '', photo_url: null }; return go(pc() ? 'dashboard' : 'home'); },
  reload() { return load(); },
  goScad() { S.filter = 'scad'; S.screen = 'giacenze'; return load(); },
  lotsScad() { S.lotFilter = 'scad'; S.selCart = null; S.selLine = null; S.sheet = null; S.screen = 'lotti'; return load(); },
  sheet(d) { S.sheet = d.k; if (d.k === 'pick') { S.q = ''; S.pickFor = null; } },
  closeSheet() { S.sheet = null; if (!['scarico', 'giacenze', 'anagrafica'].includes(S.screen)) S.q = ''; },
  closePick() { S.q = ''; S.sheet = S.pickFor === 'edit' ? 'edit' : null; S.pickFor = null; },
  overlayClick(d, e) { if (e.target.classList.contains('overlay')) { if (S.sheet === 'pick' && S.pickFor === 'edit') S.sheet = 'edit'; else if (S.sheet !== 'edit') S.sheet = null; } },
  scan() { Scanner.open(handleCode); },
  async copy(d) { try { await navigator.clipboard.writeText(d.t); toast('Copiato'); } catch (e) { toast('Copia non riuscita: seleziona il testo a mano'); } },

  /* carico */
  cartDel(d) { S.draft.lines.splice(Number(d.i), 1); saveDraft(); },
  cartQty(d) { const c = S.draft.lines[Number(d.i)]; c.qty = Math.max(1, Number(c.qty) + Number(d.d)); saveDraft(); },
  pickAdd(d) { const id = Number(d.id); if (S.pickFor === 'edit') { addEditRow(id); return; } addToDraft(id); },
  async confirmCarico() {
    if (!S.draft.lines.length) return;
    await saveDraftNow();
    return ACT.doCarico({});
  },
  async doCarico(d) {
    try {
      const r = await post('carico', { confirm_missing_expiry: !!(d && d.confirm) });
      const lines = S.draft.lines.map((l) => [(P(l.product_id) || {}).name, '+' + l.qty + ' pz' + (!l.expiry && catLife(P(l.product_id) || {}) ? ' · scad. stimata' : '')]);
      S.draft = { lines: [], note: '', updated_at: null };
      finishDone('carico', 'Lotto ' + r.cart.name + ' registrato', 'La merce è entrata in magazzino. Il carrello è diventato un lotto con la data di oggi.', lines, r.batch);
      refreshBoot().then(render);
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.code === 'missing_expiry') { S.missing = e.detail.lines; S.sheet = 'expiry'; return; }
      fail(e);
    }
  },
  async dropDraft() { try { await del('draft'); S.draft = { lines: [], note: '', updated_at: null }; S.sheet = null; await refreshBoot(); toast('Bozza svuotata'); } catch (e) { fail(e); } },
  async takeDraft(d) { try { await post('drafts/' + d.uid + '/take'); toast('Carrello ripreso: ora è tuo'); await go('carico'); } catch (e) { fail(e); } },
  async dropOther(d) { if (!confirm('Eliminare il carrello in bozza di questo utente?')) return; try { await del('drafts/' + d.uid); toast('Bozza eliminata'); await load(); } catch (e) { fail(e); } },

  /* scarico */
  tapOut(d) { tapOut(Number(d.id)); },
  outDec(d) { const id = Number(d.id); S.out[id]--; if (S.out[id] <= 0) delete S.out[id]; },
  reason(d) { S.reason = d.r; if (d.r === 'venduto') S.sub = null; },
  sub(d) { S.sub = d.r; },
  async confirmScarico() {
    const ids = Object.keys(S.out).map(Number), pieces = ids.reduce((a, id) => a + S.out[id], 0);
    if (!pieces || (S.reason === 'altro' && !S.sub)) return;
    const type = S.reason === 'venduto' ? 'vendita' : 'scarico', why = S.reason === 'venduto' ? 'Venduto al bar' : S.sub;
    try {
      const r = await post('scarico', { type, reason: why, items: ids.map((id) => ({ product_id: id, qty: S.out[id] })) });
      const lines = ids.map((id) => [(P(id) || {}).name, '−' + S.out[id] + ' pz']);
      S.out = {}; S.q = '';
      finishDone('scarico', 'Uscita registrata', type === 'vendita' ? 'Venduto al bar.' : 'Motivo: ' + why + '.', lines, r.batch);
      refreshBoot().then(render);
    } catch (e) { fail(e); await refreshBoot(); }
  },
  filter(d) { S.filter = d.f; },

  /* codice sconosciuto e proposte */
  unknownPropose() { const u = S.unknown; S.prop = { step: 1, code: u.code, name: (u.off && u.off.name) || '', cat: null, note: '', photo_url: (u.off && u.off.image_url) || null }; S.sheet = null; S.screen = 'proponi'; },
  unknownCreate() { const u = S.unknown; S.sheet = null; return go('anagrafica').then(() => { S.selProd = 'new'; S.pe = { id: 'new', name: (u.off && u.off.name) || '', category_id: '', min_stock: 0, ideal_stock: '', codes: [u.code], photo_url: (u.off && u.off.image_url) || null }; if (!pc()) S.sheet = 'prod'; render(); }); },
  unknownAssoc() { S.q = ''; S.sheet = 'assoc'; },
  async assocTo(d) {
    try { const r = await post('products/' + d.id + '/barcodes', { code: S.unknown.code }); S.sheet = null; toast(r.pending ? 'Codice proposto: funzionerà dopo l\'approvazione' : 'Codice associato'); await refreshBoot(); } catch (e) { fail(e); }
  },
  propScan() { Scanner.open(async (code) => { let off = null; try { const r = await get('lookup/' + enc(code)); if (r.product) { toast('Questo codice è già di: ' + r.product.name); return; } off = r.off; } catch (e) { /* offline */ } S.prop = { step: 1, code, name: (off && off.name) || '', cat: null, note: '', photo_url: (off && off.image_url) || null }; render(); }); },
  propNoCode() { S.prop = { step: 1, code: null, name: '', cat: null, note: '', photo_url: null }; },
  propCat(d) { S.prop.cat = Number(d.c); },
  async sendProp() {
    const pr = S.prop; if (!pr.name.trim() || !pr.cat) return;
    try {
      await post('products', { name: pr.name.trim(), category_id: pr.cat, barcodes: pr.code ? [pr.code] : [], proposal_note: pr.note, photo_url: pr.photo_url });
      finishDone('proposta', 'Proposta inviata', 'I coordinatori ricevono un avviso. Quando la approvano potrai caricare il prodotto.', [[pr.name, (categories().find((c) => c.id === pr.cat) || {}).name || '']], null);
      refreshBoot().then(render);
    } catch (e) { fail(e); }
  },

  /* i miei movimenti */
  async openEdit(d) {
    S.sheet = 'edit'; S.edit = null; render();
    try { const r = await get('batches/' + d.id + '/edit'); S.edit = Object.assign(r, { rows: r.rows.map((x) => Object.assign({}, x, { delete: false })) }); } catch (e) { S.sheet = null; fail(e); }
  },
  editQty(d) { const r = S.edit.rows[Number(d.i)]; r.qty = Math.max(1, Number(r.qty) + Number(d.d)); },
  editDel(d) { const r = S.edit.rows[Number(d.i)]; r.delete = !r.delete; },
  editAddRow() { S.pickFor = 'edit'; S.q = ''; S.sheet = 'pick'; },
  editScan() { Scanner.open(handleCode); },
  closeEdit() { S.edit = null; S.sheet = null; },
  async saveEdit() {
    const e = S.edit;
    try {
      const r = await put('batches/' + e.id, { rows: e.rows.map((x) => ({ lot_id: x.lot_id || null, product_id: x.product_id, qty: x.qty, expiry: x.expiry || '', delete: !!x.delete })), note: e.type === 'carico' ? (e.note || '') : undefined });
      S.edit = null; S.sheet = null;
      toast(r.changes && r.changes.length ? 'Modifiche salvate' : 'Nessuna modifica');
      if (S.screen === 'done') S.screen = 'miei';
      await refreshBoot(); await load();
    } catch (err) { fail(err); }
  },

  /* lista della spesa */
  async shopTick(d) { const it = S.data.shop.items.find((x) => x.id === Number(d.id)); it.checked = it.checked ? 0 : 1; render(); try { await post('shop/items/' + d.id + '/toggle'); } catch (e) { it.checked = it.checked ? 0 : 1; fail(e); } },
  async closeList() { try { const r = await post('shop/close'); S.sheet = null; toast('Lista chiusa (' + r.taken + ' di ' + r.total + '): avvisati i coordinatori'); await refreshBoot(); await load(); } catch (e) { fail(e); } },
  async shopDel(d) { try { await del('shop/items/' + d.id); await load(); } catch (e) { fail(e); } },
  async shopAddProd() { if (!S.addProd.pid) { toast('Scegli un prodotto'); return; } try { await post('shop/items', { product_id: Number(S.addProd.pid), qty: S.addProd.qty }); S.addProd = { pid: '', qty: '' }; toast('Aggiunto alla lista'); await load(); } catch (e) { fail(e); } },
  async shopAddFree() { const n = S.newItem; if (!n.text.trim()) { toast('Scrivi cosa comprare'); return; } try { await post('shop/items', { text: n.text.trim(), qty: n.qty }); S.newItem = { text: '', qty: '' }; await load(); } catch (e) { fail(e); } },
  async suggestAdd(d) { try { await post('shop/items', { product_id: Number(d.id), qty: d.q + ' pz' }); toast((P(d.id) || {}).name + ' aggiunto alla lista'); await load(); } catch (e) { fail(e); } },
  shopTab(d) { S.shopTab = d.t; return load(); },

  /* lotti */
  lotFilter(d) { S.lotFilter = d.f; return load(); },
  toggleEst() { S.lotEst = !S.lotEst; return load(); },
  async openCart(d) {
    S.selCart = Number(d.id); S.selLine = d.line ? Number(d.line) : null; S.lineVal = null; S.lineReason = null; S.lineExp = '';
    if (S.screen !== 'lotti') { S.lotFilter = 'tutti'; S.screen = 'lotti'; }
    if (!pc()) S.sheet = 'cart';
    S.data.cart = null; render();
    try { S.data.cart = await get('carts/' + S.selCart); } catch (e) { fail(e); }
    if (pc() && !S.data.carts) load();
  },
  closeCart() { S.selCart = null; S.selLine = null; },
  openLine(d) { S.selLine = Number(d.id); S.lineVal = null; S.lineReason = null; S.lineExp = ''; },
  lineVal(d) { const l = S.data.cart.lines.find((x) => x.id === S.selLine); S.lineVal = Math.max(0, (S.lineVal == null ? l.qty : S.lineVal) + Number(d.d)); },
  lineReason(d) { S.lineReason = d.r; },
  async applyLine(d) {
    const l = S.data.cart.lines.find((x) => x.id === Number(d.id));
    if (S.lineVal == null || S.lineVal === l.qty || !S.lineReason) return;
    try { await post('lots/' + l.id + '/adjust', { qty: S.lineVal, reason: S.lineReason }); toast('Riga corretta'); S.lineVal = null; S.lineReason = null; await refreshBoot(); await reloadCart(); } catch (e) { fail(e); }
  },
  async tossLine(d) {
    if (!confirm('Scaricare come scaduti tutti i pezzi rimasti di questa riga?')) return;
    try { await post('lots/' + d.id + '/toss'); toast('Pezzi scaricati come scaduti'); await refreshBoot(); await reloadCart(); } catch (e) { fail(e); }
  },
  async saveLineExp(d) {
    if (!S.lineExp) { toast('Scegli una data'); return; }
    try { await put('lots/' + d.id, { expiry: S.lineExp }); S.lineExp = ''; toast('Scadenza aggiornata'); await reloadCart(); } catch (e) { fail(e); }
  },
  async saveCartNote() { try { await put('carts/' + S.data.cart.id, { note: S.data.cart.note || '' }); toast('Nota salvata'); await reloadCart(); } catch (e) { fail(e); } },
  lotsOfProd(d) { S.lotQ = d.name; S.lotFilter = 'tutti'; S.selCart = null; S.screen = 'lotti'; return load(); },

  /* prospetto */
  ptab(d) { S.ptab = d.t; return load(); },
  hz(d) { S.hz = d.h; return load(); },

  /* correggi e inventario */
  corrVal(d) { const p = P(S.corr.pid); S.corr.val = Math.max(0, (S.corr.val == null ? p.stock : S.corr.val) + Number(d.d)); },
  corrReason(d) { S.corr.reason = d.r; },
  corrOf(d) { S.corr = { pid: Number(d.id), val: null, reason: null, target: 'nuovo' }; return go('correggi'); },
  async doCorr() {
    const r = S.corr, p = P(r.pid);
    if (r.val == null || r.val === p.stock || !r.reason) return;
    try {
      await post('correggi', { product_id: p.id, stock: r.val, reason: r.reason, target: r.target });
      toast('Correzione registrata: ' + p.name + ' ' + r.val + ' pz');
      S.corr = { pid: p.id, val: null, reason: null, target: 'nuovo' }; delete S.prodLots[p.id];
      await refreshBoot();
    } catch (e) { fail(e); }
  },
  async startInv() { try { await post('inventory/start'); S.invCounts = {}; S.invTarget = {}; await refreshBoot(); await load(); } catch (e) { fail(e); } },
  async cancelInv() { if (!confirm('Annullare l\'inventario in corso? Le quantità contate vanno perse.')) return; try { await post('inventory/' + S.data.inv.id + '/cancel'); await refreshBoot(); await load(); } catch (e) { fail(e); } },
  async applyInv() {
    try {
      await flushInv();
      const r = await post('inventory/' + S.data.inv.id + '/close');
      S.sheet = null; S.invCounts = {}; S.invTarget = {}; S.prodLots = {};
      toast('Inventario chiuso: ' + r.differences + ' differenze applicate');
      await refreshBoot(); await load();
    } catch (e) { fail(e); }
  },

  /* anagrafica */
  prodTab(d) { S.prodTab = d.t; },
  async openProd(d) {
    S.photoResults = null;
    if (d.id === 'new') S.pe = { id: 'new', name: '', category_id: '', min_stock: 0, ideal_stock: '', codes: [] };
    else {
      const p = P(d.id);
      S.pe = { id: p.id, name: p.name, category_id: p.category_id || '', min_stock: p.min_stock, ideal_stock: p.ideal_stock == null ? '' : p.ideal_stock, codes: p.barcodes.slice() };
      get('products/' + p.id).then((x) => { if (S.pe && S.pe.id === p.id) { S.pe.projection = x.projection; render(); } }).catch(() => {});
    }
    S.selProd = S.pe.id;
    if (!pc()) S.sheet = 'prod';
  },
  closeProd() { S.selProd = null; S.pe = null; S.photoResults = null; },
  peScanCode() { S.pickFor = 'peCode'; Scanner.open(handleCode); },
  async peDelCode(d) {
    const f = S.pe;
    if (f.id === 'new') { f.codes = f.codes.filter((c) => c !== d.c); return; }
    if (!confirm('Togliere il codice ' + d.c + ' da questo prodotto?')) return;
    try { await del('barcodes/' + enc(d.c)); f.codes = f.codes.filter((c) => c !== d.c); await refreshBoot(); } catch (e) { fail(e); }
  },
  async saveProd() {
    const f = S.pe;
    const body = { name: f.name, category_id: f.category_id || null, min_stock: f.min_stock === '' ? 0 : f.min_stock, ideal_stock: f.ideal_stock === '' ? null : f.ideal_stock };
    try {
      if (f.id === 'new') { body.barcodes = f.codes; if (f.photo_url) body.photo_url = f.photo_url; await post('products', body); toast('Prodotto creato: cerco la foto online…'); }
      else { await put('products/' + f.id, body); toast('Prodotto salvato'); }
      S.selProd = null; S.pe = null; S.sheet = null; await refreshBoot();
    } catch (e) { fail(e); }
  },
  async delProd(d) {
    if (!confirm('Eliminare questo prodotto? Se ha già avuto movimenti viene archiviato.')) return;
    try { const r = await del('products/' + d.id); toast(r.archived ? 'Prodotto archiviato' : 'Prodotto eliminato'); S.selProd = null; S.pe = null; S.sheet = null; await refreshBoot(); } catch (e) { fail(e); }
  },
  async photoSearch() { S.photoResults = 'loading'; render(); try { const r = await get('photos/search?q=' + enc(S.pe.name)); S.photoResults = r.results; } catch (e) { S.photoResults = []; fail(e); } },
  async photoPick(d) { const r = S.photoResults[Number(d.i)]; try { await post('products/' + S.pe.id + '/photo', { url: r.image_url }); S.photoResults = null; toast('Foto aggiornata'); await refreshBoot(); } catch (e) { fail(e); } },
  async photoUpload() { const data = await pickPhoto(); if (!data) return; try { await post('products/' + S.pe.id + '/photo', { data_url: data }); toast('Foto aggiornata'); await refreshBoot(); } catch (e) { fail(e); } render(); },
  async fillPhotos() { try { const r = await post('photos/fill-missing'); toast('Cerco le foto di ' + r.queued + ' prodotti: ci vuole qualche minuto'); } catch (e) { fail(e); } },
  async doImport() { try { const r = await post('import/products', { csv: S.importCsv || '' }); S.sheet = null; S.importCsv = ''; toast(r.created + ' prodotti creati, ' + r.skipped + ' già presenti' + (r.errors.length ? ', ' + r.errors.length + ' errori' : '')); await refreshBoot(); } catch (e) { fail(e); } },
  newCat() { S.catEdit = { id: 'new', name: '', shelf_life_days: '' }; },
  editCat(d) { const c = categories().find((x) => x.id === Number(d.id)); S.catEdit = { id: c.id, name: c.name, shelf_life_days: c.shelf_life_days || '', color: c.color, sort: c.sort }; },
  cancelCat() { S.catEdit = null; },
  async saveCat() {
    const c = S.catEdit, body = { name: c.name, shelf_life_days: c.shelf_life_days || null, color: c.color || '#DCE2DF', sort: c.sort || 500 };
    try { if (c.id === 'new') await post('categories', body); else await put('categories/' + c.id, body); S.catEdit = null; await refreshBoot(); } catch (e) { fail(e); }
  },
  async delCat(d) { if (!confirm('Eliminare la categoria?')) return; try { await del('categories/' + d.id); await refreshBoot(); } catch (e) { fail(e); } },

  /* proposte */
  async approveProp(d) {
    const min = document.getElementById('pm-' + d.id), ideal = document.getElementById('pi-' + d.id);
    try { await post('products/' + d.id + '/approve', { min_stock: min ? min.value : 0, ideal_stock: ideal && ideal.value ? ideal.value : null }); toast('Approvato: ora si può caricare'); await load(); } catch (e) { fail(e); }
  },
  async mergeProp(d) { const el = document.getElementById('mg-' + d.id); try { await post('products/' + d.id + '/merge', { target_id: Number(el.value) }); toast('Unito: il barcode ora scarica quel prodotto'); await load(); } catch (e) { fail(e); } },
  async rejectProp(d) { if (!confirm('Rifiutare la proposta?')) return; try { await del('products/' + d.id); toast('Proposta rifiutata'); await load(); } catch (e) { fail(e); } },
  async approveCode(d) { try { await post('barcodes/' + enc(d.c) + '/approve'); toast('Barcode approvato'); await load(); } catch (e) { fail(e); } },
  async rejectCode(d) { try { await del('barcodes/' + enc(d.c)); toast('Barcode rifiutato'); await load(); } catch (e) { fail(e); } },

  /* storico e log */
  histType(d) { S.histType = d.t; return load(); },
  logCat(d) { S.logCat = d.c; return load(); },
  userLog(d) { S.logUser = String(d.id); S.logCat = ''; S.logQ = ''; S.sheet = null; S.screen = 'log'; return load(); },

  /* utenti */
  async openUser(d) {
    S.selUser = d.id === 'new' ? 'new' : Number(d.id); S.pinNew = '';
    if (!pc()) S.sheet = 'user';
    render();
    if (S.selUser !== 'new') { try { await loadSessions(S.selUser); } catch (e) { fail(e); } }
  },
  closeUser() { S.selUser = null; },
  async kick(d) { try { await del('sessions/' + d.id); toast('Dispositivo disconnesso'); await loadSessions(S.selUser); S.data.users = await get('users'); } catch (e) { fail(e); } },
  async kickAll(d) { try { await del('users/' + d.id + '/sessions'); toast('Disconnesso da tutti i dispositivi'); await loadSessions(S.selUser); S.data.users = await get('users'); } catch (e) { fail(e); } },
  async unlockUser(d) { try { await put('users/' + d.id, { unlock: true }); toast('Utente sbloccato'); S.data.users = await get('users'); } catch (e) { fail(e); } },
  async toggleActive(d) { const u = S.data.users.find((x) => x.id === Number(d.id)); try { await put('users/' + u.id, { active: !u.active }); toast(u.active ? 'Utente disattivato e disconnesso' : 'Utente riattivato'); S.data.users = await get('users'); await loadSessions(u.id); } catch (e) { fail(e); } },
  async setRole(d) { try { await put('users/' + S.selUser, { role: d.r }); toast('Ruolo aggiornato: l\'utente deve rientrare'); S.data.users = await get('users'); await loadSessions(S.selUser); } catch (e) { fail(e); } },
  async resetPin(d) { if (!/^\d{4,8}$/.test(S.pinNew)) { toast('Il PIN deve avere da 4 a 8 cifre'); return; } try { await put('users/' + d.id, { pin: S.pinNew }); S.pinNew = ''; toast('Nuovo PIN impostato: disconnesso dai dispositivi'); await loadSessions(Number(d.id)); S.data.users = await get('users'); } catch (e) { fail(e); } },
  nuRole(d) { S.newUser.role = d.r; },
  async createUser() {
    const n = S.newUser;
    try { const r = await post('users', n); S.newUser = { display_name: '', username: '', role: 'operatore', pin: '' }; toast('Utente creato: può entrare con il suo PIN'); S.data.users = await get('users'); S.selUser = r.id; await loadSessions(r.id); } catch (e) { fail(e); }
  },
  async changePw() { try { await post('me/password', S.pw); S.pw = { current: '', new: '' }; toast('Password cambiata: gli altri dispositivi sono stati disconnessi'); } catch (e) { fail(e); } },

  /* impostazioni */
  stToggle(d) { S.st[d.k] = S.st[d.k] === '1' ? '0' : '1'; },
  async saveSettings() {
    const keys = ['bar_name', 'window_days', 'include_inventory_loss', 'expiry_warning_days', 'ideal_period_days', 'photo_lookup', 'ntfy_server', 'ntfy_proposals', 'ntfy_list_closed', 'ntfy_digest', 'digest_weekday', 'digest_time'];
    const body = {}; keys.forEach((k) => { body[k] = S.st[k]; });
    try { S.st = await put('settings', body); toast('Impostazioni salvate'); await refreshBoot(); } catch (e) { fail(e); }
  },
  async ntfyTest() { try { await post('ntfy/test'); toast('Notifica di prova inviata ai due canali'); } catch (e) { fail(e); } },
  async ntfyDigest() { try { await post('ntfy/digest'); toast('Riepilogo inviato'); } catch (e) { fail(e); } },
};
async function reloadCart() {
  if (S.selCart) S.data.cart = await get('carts/' + S.selCart);
  if (S.screen === 'lotti') S.data.carts = await get(`carts?status=${S.lotFilter}&q=${enc(S.lotQ)}&estimated=${S.lotEst}`);
}

/* inventario: ogni quantità contata si salva da sola */
const invPending = {};
const flushInvDeb = debounce(() => flushInv().catch(fail), 600);
async function flushInv() {
  const inv = S.data.inv; if (!inv) return;
  const ids = Object.keys(invPending);
  for (const pid of ids) {
    const body = invPending[pid]; delete invPending[pid];
    await put('inventory/' + inv.id + '/line', body);
    const l = inv.lines.find((x) => x.product_id === Number(pid));
    if (l) { l.counted = body.counted === '' ? null : body.counted; if (body.target) l.target = body.target; }
  }
}

/* ---------------- eventi ---------------- */
let busy = false;
document.addEventListener('click', async (e) => {
  const el = e.target.closest('[data-a]');
  if (!el || !el.closest('#root')) return;
  const name = el.dataset.a, fn = ACT[name];
  if (!fn) return;
  if (name === 'overlayClick' && e.target !== el) return;
  if (busy && fn.constructor.name === 'AsyncFunction') return;
  const res = fn(el.dataset, e);
  render();
  if (res && typeof res.then === 'function') {
    busy = true;
    try { await res; } catch (err) { fail(err); } finally { busy = false; render(); }
  }
});
function onBind(e) {
  const el = e.target;
  if (!el.dataset || !el.dataset.bind) return;
  const b = el.dataset.bind, v = el.value, i = Number(el.dataset.i);
  if (b === 'draftQty') { const n = parseInt(v.replace(/\D/g, ''), 10); if (n > 0) { S.draft.lines[i].qty = n; saveDraft(); } return; }
  if (b === 'draftExp') { S.draft.lines[i].expiry = v; saveDraft(); render(); return; }
  if (b === 'draftNote') { S.draft.note = v; saveDraft(); return; }
  if (b === 'editExp') { S.edit.rows[i].expiry = v; return; }
  if (b === 'shopQty') { const it = S.data.shop.items.find((x) => x.id === Number(el.dataset.id)); it.qty_text = v; debounce(() => put('shop/items/' + it.id, { qty: v }).catch(fail), 500)(); return; }
  if (b === 'invCount') {
    const pid = Number(el.dataset.id), clean = v.replace(/\D/g, '');
    S.invCounts[pid] = clean;
    invPending[pid] = Object.assign(invPending[pid] || {}, { product_id: pid, counted: clean === '' ? null : Number(clean) });
    flushInvDeb(); render(); return;
  }
  if (b === 'invTarget') {
    const pid = Number(el.id.replace('invt-', ''));
    S.invTarget[pid] = v;
    const l = S.data.inv.lines.find((x) => x.product_id === pid);
    invPending[pid] = Object.assign(invPending[pid] || {}, { product_id: pid, counted: Number(cnt(l)), target: v });
    flushInvDeb(); return;
  }
  if (b === 'corrValInput') { const n = parseInt(v.replace(/\D/g, ''), 10); S.corr.val = isNaN(n) ? 0 : n; render(); return; }
  if (b === 'importCsv') { S.importCsv = v; return; }
  setPath(b, v);
  if (b === 'corr.pid') { S.corr.pid = Number(v); S.corr.val = null; S.corr.reason = null; S.corr.target = 'nuovo'; }
  if (b === 'mesiPid') S.mesiPid = Number(v);
  if (el.dataset.reload) { reloadSoon(); return; }
  if (['INPUT', 'TEXTAREA'].includes(el.tagName) && el.type !== 'date' && !['q', 'lotQ', 'logQ'].includes(b) && !b.startsWith('login') && !b.startsWith('prop.')) return;
  render();
}
document.addEventListener('input', (e) => { if (e.target.closest('#root') && e.target.tagName !== 'SELECT' && e.target.type !== 'date') onBind(e); });
document.addEventListener('change', (e) => { if (e.target.closest('#root') && (e.target.tagName === 'SELECT' || e.target.type === 'date')) onBind(e); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || !e.target.id) return;
  if (e.target.id === 'lg-user') { ACT.loginNext().then(render); }
  else if (e.target.id === 'lg-pass') { ACT.loginDo().then(render); }
});
window.addEventListener('resize', debounce(() => { const w = window.matchMedia('(min-width: 900px)').matches; if (w !== S.wide) { S.wide = w; if (S.screen === 'dashboard' && !pc()) S.screen = 'home'; if (S.screen === 'home' && pc()) { S.screen = 'dashboard'; load(); } render(); } }, 200));
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && S.user && !S.sheet) load(); });

/* ---------------- avvio ---------------- */
(async function init() {
  try {
    await refreshBoot();
    S.screen = pc() ? 'dashboard' : 'home';
    await load();
  } catch (e) {
    S.user = null; S.boot = null; render();
  }
})();
