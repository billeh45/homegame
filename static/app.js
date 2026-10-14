'use strict';
const $ = s => document.querySelector(s);
const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
const S = {
  token: localStorage.getItem('hg_token'),
  name: localStorage.getItem('hg_name'),
  ws: null, lobby: null, table: null, tableId: null,
  turnKey: null, deadline: null, timeout: 30,
  preFold: false, preFoldHand: null,
};

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
const fmt = n => Number(n).toLocaleString();
// Money: a table's chips are whole dollars ("dollars") or cents ("cents"). The ledger is always in cents.
const perChip = u => (u === 'cents' ? 100 : 1);              // chips per $1
const twoDp = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
const money = (n, u) => {
  const neg = n < 0 ? '-' : ''; n = Math.abs(n);
  return neg + '$' + (u === 'cents' ? (n / 100).toLocaleString(undefined, twoDp) : Number(n).toLocaleString());
};
const cents = n => money(n, 'cents');
const M = n => money(n, S.table && S.table.unit);             // amounts at the open table
const toChips = (dollars, u) => Math.round(Number(dollars) * perChip(u));
const toDollars = (chips, u) => chips / perChip(u);
function show(id) { for (const v of ['login', 'lobby', 'tableView']) $('#' + v).classList.toggle('hidden', v !== id); }
function toast(msg, ms = 2800) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}
function send(m) { if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify(m)); }

// ------------------------------------------------------------ auth
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  $('#loginErr').textContent = '';
  const body = { room_code: $('#roomCode').value.trim(), name: $('#loginName').value.trim(), password: $('#loginPw').value };
  try {
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || 'Login failed');
    S.token = j.token; S.name = j.name;
    localStorage.setItem('hg_token', j.token); localStorage.setItem('hg_name', j.name);
    connect(); route();
  } catch (err) { $('#loginErr').textContent = err.message; }
});
function logout() {
  localStorage.removeItem('hg_token'); localStorage.removeItem('hg_name');
  S.token = null; const ws = S.ws; S.ws = null; if (ws) ws.close();
  history.replaceState(null, '', location.pathname); S.tableId = null; show('login');
}
$('#logoutBtn').onclick = logout;

// ------------------------------------------------------------ socket
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(S.token)}`);
  S.ws = ws;
  ws.onopen = () => { $('#conn').classList.add('hidden'); if (S.tableId) send({ type: 'watch', table_id: S.tableId }); };
  ws.onmessage = e => onMsg(JSON.parse(e.data));
  ws.onclose = () => {
    if (S.ws !== ws || !S.token) return;
    $('#conn').classList.remove('hidden');
    setTimeout(() => { if (S.ws === ws && S.token) connect(); }, 1500);
  };
}
setInterval(() => send({ type: 'ping' }), 25000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && S.token && (!S.ws || S.ws.readyState > 1)) connect();
});

function onMsg(m) {
  switch (m.type) {
    case 'auth_failed': logout(); break;
    case 'lobby': S.lobby = m; renderLobby(); break;
    case 'table':
      if (m.table.id !== S.tableId) return;
      S.table = m.table;
      if (m.table.time_left != null) S.deadline = performance.now() + m.table.time_left * 1000; else S.deadline = null;
      S.timeout = m.table.timeout;
      renderTable(); break;
    case 'chat_history': $('#chatLog').innerHTML = ''; m.messages.forEach(addChat); break;
    case 'chat': addChat(m.message); break;
    case 'created': location.hash = '#t=' + encodeURIComponent(m.table_id); break;
    case 'table_closed': if (m.table_id === S.tableId) { toast('That table is closed'); location.hash = ''; } break;
    case 'error': toast(m.message); if (S.table) { S.turnKey = null; renderTable(); } break;
  }
}

// ------------------------------------------------------------ routing
function route() {
  if (!S.token) { show('login'); return; }
  const m = location.hash.match(/^#t=(.+)$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    if (S.tableId !== id) {
      S.tableId = id; S.table = null; S.turnKey = null;
      $('#chatLog').innerHTML = ''; clearFelt();
      send({ type: 'watch', table_id: id });
    }
    show('tableView');
  } else {
    if (S.tableId) send({ type: 'unwatch' });
    S.tableId = null; S.table = null;
    show('lobby'); send({ type: 'lobby' });
  }
}
window.addEventListener('hashchange', route);
$('#backBtn').onclick = () => { location.hash = ''; };

// ------------------------------------------------------------ modal helpers
function openModal() { $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); $('#modalBox').innerHTML = ''; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

function formModal(title, fields, submitLabel, onSubmit) {
  const box = $('#modalBox'); box.innerHTML = '';
  box.append(el('h3', null, title));
  const f = el('form', 'form-grid');
  const inputs = {};
  for (const fd of fields) {
    if (fd.type === 'checkbox') {
      const lab = el('label', 'check-row full');
      const inp = el('input'); inp.type = 'checkbox'; inp.checked = !!fd.value;
      lab.append(inp, el('span', null, fd.label));
      if (fd.hint) lab.append(el('div', 'muted small', fd.hint));
      f.append(lab); inputs[fd.key] = inp; continue;
    }
    if (fd.type === 'seg') {
      const wrap = el('div', 'seg-row full');
      wrap.append(el('div', 'seg-label', fd.label));
      const seg = el('div', 'seg');
      const holder = { type: 'seg', value: fd.value };
      for (const [val, text] of fd.options) {
        const b = el('button', 'seg-btn' + (val === fd.value ? ' on' : ''), text); b.type = 'button';
        b.onclick = () => {
          holder.value = val;
          seg.querySelectorAll('.seg-btn').forEach(x => x.classList.toggle('on', x === b));
          if (fd.onChange) fd.onChange(val, inputs);
        };
        seg.append(b);
      }
      wrap.append(seg); f.append(wrap); inputs[fd.key] = holder; continue;
    }
    const lab = el('label', fd.full ? 'full' : null, fd.label);
    const inp = el('input');
    inp.type = fd.type || 'number';
    if (inp.type === 'number') { inp.inputMode = fd.step && fd.step < 1 ? 'decimal' : 'numeric'; inp.step = fd.step || 1; }
    inp.value = fd.value;
    if (fd.min != null) inp.min = fd.min;
    if (fd.max != null) inp.max = fd.max;
    if (fd.maxlength) inp.maxLength = fd.maxlength;
    inp.required = true;
    lab.append(inp); f.append(lab); inputs[fd.key] = inp;
  }
  const row = el('div', 'modal-actions full');
  const cancel = el('button', 'btn ghost', 'Cancel'); cancel.type = 'button'; cancel.onclick = closeModal;
  const ok = el('button', 'btn primary', submitLabel); ok.type = 'submit';
  row.append(cancel, ok); f.append(row);
  f.onsubmit = e => {
    e.preventDefault();
    const vals = {};
    for (const k in inputs) {
      const i = inputs[k];
      vals[k] = i.type === 'seg' ? i.value : i.type === 'checkbox' ? i.checked : i.type === 'number' ? Number(i.value) : i.value.trim();
    }
    closeModal(); onSubmit(vals);
  };
  box.append(f); openModal();
  const first = f.querySelector('input'); if (first) first.select();
}

// ------------------------------------------------------------ lobby
function renderLobby() {
  const L = S.lobby; if (!L) return;
  $('#whoami').textContent = S.name;
  const list = $('#tableList'); list.innerHTML = '';
  if (!L.tables.length) list.append(el('p', 'muted', 'No tables yet — start one.'));
  for (const t of L.tables) {
    const row = el('div', 'table-row');
    const info = el('div', 'info');
    const tn = el('div', 'tname', t.name);
    if (t.practice) tn.append(el('span', 'badge', 'Practice'));
    info.append(tn,
      el('div', 'muted small', `NLH ${money(t.sb, t.unit)}/${money(t.bb, t.unit)} · buy-in ${money(t.min_buy, t.unit)}–${money(t.max_buy, t.unit)} · ${t.players.length}/${t.max_seats} seated`));
    if (t.players.length) info.append(el('div', 'small players', t.players.join(', ')));
    const btns = el('div', 'btns');
    const open = el('button', 'btn primary small', 'Open');
    open.onclick = () => { location.hash = '#t=' + encodeURIComponent(t.id); };
    btns.append(open);
    if (t.creator === S.name && !(t.humans || t.players).length) {
      const x = el('button', 'btn ghost small', 'Close');
      x.onclick = () => { if (confirm(`Close "${t.name}"?`)) send({ type: 'close_table', table_id: t.id }); };
      btns.append(x);
    }
    row.append(info, btns); list.append(row);
  }
  const body = $('#ledgerBody'); body.innerHTML = '';
  if (!L.ledger.length) {
    const tr = el('tr'); const td = el('td', 'muted', 'Nothing yet'); td.colSpan = 5; tr.append(td); body.append(tr);
  }
  for (const r of L.ledger) {
    const tr = el('tr');
    const net = el('td', r.net > 0 ? 'pos' : r.net < 0 ? 'neg' : '', (r.net > 0 ? '+' : '') + cents(r.net));
    tr.append(el('td', null, r.name), el('td', null, cents(r.buyin)), el('td', null, cents(r.cashout)), el('td', null, cents(r.in_play)), net);
    body.append(tr);
  }
}

const STAKE_DEFAULTS = {
  dollars: { sb: 1, bb: 2, min_buy: 40, max_buy: 200, step: 1, min: 1 },
  cents: { sb: 0.10, bb: 0.25, min_buy: 10, max_buy: 25, step: 0.01, min: 0.01 },
};
$('#newTableBtn').onclick = () => formModal('New cash table', [
  { key: 'name', label: 'Table name', type: 'text', value: `${S.name}'s game`, maxlength: 30, full: true },
  { key: 'unit', type: 'seg', label: 'Stakes in', value: 'dollars',
    options: [['dollars', 'Dollars  ($1/$2)'], ['cents', 'Cents  ($0.10/$0.25)']],
    onChange: (u, inputs) => {
      const d = STAKE_DEFAULTS[u];
      for (const k of ['sb', 'bb', 'min_buy', 'max_buy']) {
        inputs[k].step = d.step; inputs[k].min = d.min;
        inputs[k].inputMode = u === 'cents' ? 'decimal' : 'numeric';
        inputs[k].value = u === 'cents' ? d[k].toFixed(2) : d[k];
      }
    } },
  { key: 'sb', label: 'Small blind ($)', value: 1, min: 1 },
  { key: 'bb', label: 'Big blind ($)', value: 2, min: 1 },
  { key: 'min_buy', label: 'Min buy-in ($)', value: 40, min: 1 },
  { key: 'max_buy', label: 'Max buy-in ($)', value: 200, min: 1 },
  { key: 'max_seats', label: 'Seats (2–9)', value: 9, min: 2, max: 9 },
  { key: 'timeout', label: 'Turn timer (sec)', value: 30, min: 10, max: 120 },
  { key: 'practice', type: 'checkbox', label: 'Practice table', value: false,
    hint: 'Allows bots. Chips here never count on the ledger.' },
], 'Create', v => {
  for (const k of ['sb', 'bb', 'min_buy', 'max_buy']) v[k] = toChips(v[k], v.unit);
  send({ type: 'create_table', ...v });
});

$('#settleBtn').onclick = () => {
  if (confirm('Mark everyone as settled up? This zeroes the ledger (everyone must be standing).')) send({ type: 'settle' });
};

// ------------------------------------------------------------ table
function cardEl(c, big) {
  const e = el('div', 'card' + (big ? ' lg' : ''));
  if (c === '??') { e.classList.add('back'); return e; }
  e.classList.add('s-' + c[1]);
  e.append(el('span', 'r', c[0] === 'T' ? '10' : c[0]), el('span', 's', SUIT[c[1]]));
  return e;
}
function seatPos(i, t, rx, ry) {
  const n = t.max_seats, off = t.me != null ? t.me : 0;
  const k = ((i - off) % n + n) % n;
  const th = Math.PI / 2 + 2 * Math.PI * k / n;      // viewer sits at the bottom
  return [50 + rx * Math.cos(th), 50 + ry * Math.sin(th)];
}
function clearFelt() {
  document.querySelectorAll('#felt .seat, #felt .bet, #felt .dealer').forEach(e => e.remove());
  for (const id of ['#board', '#actionBar', '#myControls']) $(id).innerHTML = '';
  for (const id of ['#pot', '#result', '#handLog']) $(id).textContent = '';
}

function renderTable() {
  const t = S.table; if (!t) return;
  $('#tName').textContent = t.name;
  $('#tStakes').textContent = `NLH ${M(t.sb)}/${M(t.bb)}${t.practice ? ' · Practice' : ''}`;
  const felt = $('#felt');
  felt.querySelectorAll('.seat, .bet, .dealer').forEach(e => e.remove());
  const portrait = window.innerHeight > window.innerWidth;
  const R = portrait ? [41, 45] : [44, 43];
  const winners = new Set();
  if (t.results) t.results.forEach(r => r.seats.forEach(i => winners.add(i)));

  t.seats.forEach((s, i) => {
    const [x, y] = seatPos(i, t, R[0], R[1]);
    const d = el('div', 'seat');
    d.style.left = x + '%'; d.style.top = y + '%';
    if (!s) {
      if (t.me == null) { const b = el('button', 'btn sit', 'Sit'); b.onclick = () => sitModal(i); d.append(b); }
      if (t.practice) { const b = el('button', 'btn sit bot', '+ Bot'); b.onclick = () => botModal(i); d.append(b); }
      if (t.me != null && !t.practice) { d.classList.add('empty'); d.append(el('div', 'empty-dot')); }
      felt.append(d); return;
    }
    if (s.is_you) d.classList.add('me');
    if (s.folded) d.classList.add('folded');
    if (t.current === i) d.classList.add('active');
    if (winners.has(i)) d.classList.add('winner');
    if (s.sitting_out && !s.in_hand) d.classList.add('away');
    const cards = el('div', 'cards');
    s.cards.forEach(c => cards.append(cardEl(c, s.is_you)));
    d.append(cards);
    const plate = el('div', 'plate');
    const pn = el('div', 'pname', s.name);
    if (s.bot) pn.prepend(el('span', 'bot-tag', 'BOT'));
    plate.append(pn, el('div', 'pstack', M(s.stack)));
    if (s.bot && t.practice && !s.leaving) {
      const x = el('button', 'rm-bot', '×'); x.title = 'Remove bot';
      x.onclick = () => send({ type: 'remove_bot', seat: i });
      plate.append(x);
    }
    if (t.current === i) { const tm = el('div', 'timer'); tm.append(el('div', 'fill')); plate.append(tm); }
    d.append(plate);
    let status = '';
    if (s.leaving) status = 'Leaving';
    else if (s.all_in) status = 'All in';
    else if (s.last_action && s.in_hand) status = s.last_action;
    else if (s.sitting_out) status = 'Sitting out';
    else if (s.stack === 0 && !s.in_hand) status = 'Busted';
    if (status) d.append(el('div', 'status', status));
    felt.append(d);
    if (s.bet > 0) {
      const [bx, by] = seatPos(i, t, R[0] * 0.6, R[1] * 0.55);
      const b = el('div', 'bet', M(s.bet)); b.style.left = bx + '%'; b.style.top = by + '%'; felt.append(b);
    }
    if (t.button === i) {
      const [dx, dy] = seatPos(i, t, R[0] * 0.72, R[1] * 0.68);
      const b = el('div', 'dealer', 'D'); b.style.left = (dx + 6) + '%'; b.style.top = dy + '%'; felt.append(b);
    }
  });

  const board = $('#board'); board.innerHTML = '';
  for (let k = 0; k < 5; k++) board.append(t.board[k] ? cardEl(t.board[k]) : el('div', 'card slot'));
  $('#pot').textContent = t.pot > 0 ? `Pot ${M(t.pot)}` : '';
  $('#result').textContent = t.results
    ? t.results.map(r => `${r.winners.join(' & ')} ${r.winners.length > 1 ? 'split' : 'wins'} ${M(r.amount)}${r.hand ? ' — ' + r.hand : ''}`).join('\n')
    : '';
  const log = $('#handLog'); log.textContent = t.log.join('\n'); log.scrollTop = log.scrollHeight;

  if (t.hand_no !== S.preFoldHand) S.preFold = false;
  const key = t.options ? `${t.hand_no}:${t.action_seq}` : null;
  if (!t.options || key !== S.turnKey) { S.turnKey = key; buildActionBar(t); }
  buildMyControls(t);
}

function act(action, amount) {
  send({ type: 'action', action, amount: amount || 0 });
  $('#actionBar').innerHTML = '';
}

function buildActionBar(t) {
  const bar = $('#actionBar'); bar.innerHTML = '';
  const me = t.me != null ? t.seats[t.me] : null;
  const o = t.options;
  if (o) {
    if (S.preFold && S.preFoldHand === t.hand_no) { S.preFold = false; act(o.can_check ? 'check' : 'fold'); return; }
    if (navigator.vibrate) navigator.vibrate(60);
    const row = el('div', 'act-row');
    const fold = el('button', 'btn act fold', 'Fold'); fold.onclick = () => act('fold');
    const cc = o.can_check ? el('button', 'btn act check', 'Check') : el('button', 'btn act call', `Call ${M(o.to_call)}`);
    cc.onclick = () => act(o.can_check ? 'check' : 'call');
    row.append(fold, cc);
    if (o.can_raise) {
      const label = o.is_bet ? 'Bet' : 'Raise to';
      const rb = el('button', 'btn act raise');
      const range = el('input'); range.type = 'range'; range.min = o.min_to; range.max = o.max_to; range.step = 1;
      const num = el('input', 'num'); num.type = 'number';
      const upd = v => {
        v = Math.max(o.min_to, Math.min(o.max_to, Math.round(Number(v) || 0)));
        range.value = v; num.value = t.unit === 'cents' ? toDollars(v, t.unit).toFixed(2) : v; rb.dataset.v = v;
        rb.textContent = v === o.max_to ? `All in ${M(v)}` : `${label} ${M(v)}`;
      };
      range.oninput = () => upd(range.value);
      num.step = t.unit === 'cents' ? 0.01 : 1; num.inputMode = t.unit === 'cents' ? 'decimal' : 'numeric';
      num.onchange = () => upd(toChips(num.value, t.unit));
      const owe = o.current_bet - o.my_bet;
      const potTo = f => o.current_bet + f * (t.pot + owe);
      const presets = el('div', 'presets');
      [['Min', () => o.min_to], ['½', () => potTo(0.5)], ['¾', () => potTo(0.75)], ['Pot', () => potTo(1)], ['All in', () => o.max_to]]
        .forEach(([lab, fn]) => { const b = el('button', 'btn chip', lab); b.onclick = () => upd(fn()); presets.append(b); });
      rb.onclick = () => act(o.is_bet ? 'bet' : 'raise', Number(rb.dataset.v));
      upd(o.min_to);
      const sizing = el('div', 'sizing');
      const sr = el('div', 'slider-row'); sr.append(range, num);
      sizing.append(presets, sr);
      bar.append(sizing);
      row.append(rb);
    }
    bar.append(row);
    return;
  }
  if (me && t.hand_running && me.in_hand && !me.folded && !me.all_in) {
    const lab = el('label', 'prefold');
    const cb = el('input'); cb.type = 'checkbox';
    cb.checked = S.preFold && S.preFoldHand === t.hand_no;
    cb.onchange = () => { S.preFold = cb.checked; S.preFoldHand = t.hand_no; };
    lab.append(cb, document.createTextNode('Check / Fold'));
    bar.append(lab);
  }
}

function buildMyControls(t) {
  const box = $('#myControls'); box.innerHTML = '';
  if (t.me == null) {
    box.append(el('div', 'muted small center', t.seats.some(s => !s) ? 'Tap an open seat to sit down' : 'Table is full — you\'re watching'));
    return;
  }
  const s = t.seats[t.me];
  if (s.leaving) { box.append(el('span', 'muted small', 'You\'ll be cashed out when this hand ends')); return; }
  const stand = el('button', 'btn ghost small', 'Stand up');
  stand.onclick = () => { if (confirm(`Stand up and cash out ${M(s.stack + s.pending)}?`)) send({ type: 'stand' }); };
  const so = el('button', 'btn ghost small', s.sitting_out ? "I'm back" : 'Sit out');
  so.onclick = () => send({ type: 'sitout', value: !s.sitting_out });
  if (s.sitting_out) so.classList.replace('ghost', 'primary');
  box.append(stand, so);
  const room = t.max_buy - s.stack - s.pending;
  if (room > 0) {
    const add = el('button', s.stack === 0 && !s.pending ? 'btn primary small' : 'btn ghost small', s.stack === 0 ? 'Rebuy' : 'Add chips');
    const u = t.unit, d = n => (u === 'cents' ? toDollars(n, u).toFixed(2) : n);
    add.onclick = () => formModal('Add chips', [{ key: 'amount', label: `Amount in $ (up to ${M(room)})`, value: d(room),
      min: d(1), max: d(room), step: u === 'cents' ? 0.01 : 1, full: true }],
      'Add', v => send({ type: 'addchips', amount: toChips(v.amount, u) }));
    box.append(add);
  }
  if (s.pending) box.append(el('span', 'muted small', `+${M(s.pending)} after this hand`));
}

function botModal(i) {
  const box = $('#modalBox'); box.innerHTML = '';
  box.append(el('h3', null, `Add a bot to seat ${i + 1}`));
  const styles = [
    ['rock', 'Rock', 'Tight. Plays few hands, rarely bluffs.'],
    ['shark', 'Shark', 'Solid and balanced. Bluffs sometimes.'],
    ['maniac', 'Maniac', 'Loose and aggressive. Bets and bluffs a lot.'],
    ['random', 'Surprise me', 'Random style.'],
  ];
  for (const [key, label, desc] of styles) {
    const b = el('button', 'btn style-pick');
    b.append(el('div', 'tname', label), el('div', 'muted small', desc));
    b.onclick = () => { closeModal(); send({ type: 'add_bot', seat: i, style: key }); };
    box.append(b);
  }
  const c = el('button', 'btn ghost wide', 'Cancel'); c.onclick = closeModal; box.append(c);
  openModal();
}

function sitModal(i) {
  const t = S.table;
  const u = t.unit, d = n => (u === 'cents' ? toDollars(n, u).toFixed(2) : n);
  formModal(`Sit in seat ${i + 1}`, [{ key: 'buyin', label: `Buy-in in $ (${M(t.min_buy)}–${M(t.max_buy)})`, value: d(t.max_buy),
    min: d(t.min_buy), max: d(t.max_buy), step: u === 'cents' ? 0.01 : 1, full: true }],
    'Sit down', v => send({ type: 'sit', seat: i, buyin: toChips(v.buyin, u) }));
}

function tick() {
  const f = document.querySelector('#felt .seat.active .timer .fill');
  if (f && S.deadline) {
    const frac = Math.max(0, S.deadline - performance.now()) / (S.timeout * 1000);
    f.style.width = (frac * 100) + '%';
    f.classList.toggle('low', frac < 0.3);
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
window.addEventListener('resize', () => { if (S.table) renderTable(); });

// ------------------------------------------------------------ chat, log, history
function addChat(m) {
  const log = $('#chatLog');
  const line = el('div', 'chat-line');
  line.append(el('b', null, m.name + ': '), document.createTextNode(m.text));
  log.append(line); log.scrollTop = log.scrollHeight;
}
$('#chatForm').onsubmit = e => {
  e.preventDefault();
  const v = $('#chatInput').value.trim();
  if (v) { send({ type: 'chat', text: v }); $('#chatInput').value = ''; }
};
document.querySelectorAll('.tab').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === b));
  $('#chatPane').classList.toggle('hidden', b.dataset.tab !== 'chat');
  $('#logPane').classList.toggle('hidden', b.dataset.tab !== 'log');
});
$('#historyBtn').onclick = async () => {
  const r = await fetch(`/api/hands/${encodeURIComponent(S.tableId)}?token=${encodeURIComponent(S.token)}`);
  if (!r.ok) { toast('Could not load history'); return; }
  const hands = await r.json();
  const box = $('#modalBox'); box.innerHTML = '';
  box.append(el('h3', null, 'Recent hands'));
  const wrap = el('div', 'history');
  if (!hands.length) wrap.append(el('p', 'muted', 'No hands played yet.'));
  hands.forEach(h => wrap.append(el('pre', 'hand-log', h.log)));
  const c = el('button', 'btn ghost wide', 'Close'); c.onclick = closeModal;
  box.append(wrap, c); openModal();
};

// ------------------------------------------------------------ table design toggle (per device)
function setTheme(n) {
  n = ['1', '2', '3', '4'].includes(String(n)) ? String(n) : '1';
  $('#felt').dataset.theme = n;
  document.querySelectorAll('#themePick button[data-theme]').forEach(b => b.classList.toggle('on', b.dataset.theme === n));
  try { localStorage.setItem('hg_theme', n); } catch (e) { /* storage blocked: still works this visit */ }
}
document.querySelectorAll('#themePick button[data-theme]').forEach(b => b.onclick = () => setTheme(b.dataset.theme));
function setRoom(n) {
  n = ['1', '2', '3', '4'].includes(String(n)) ? String(n) : '1';
  $('#tableView').dataset.room = n;
  document.querySelectorAll('#themePick button[data-room]').forEach(b => b.classList.toggle('on', b.dataset.room === n));
  try { localStorage.setItem('hg_room', n); } catch (e) { /* storage blocked: still works this visit */ }
}
document.querySelectorAll('#themePick button[data-room]').forEach(b => b.onclick = () => setRoom(b.dataset.room));
let savedRoom = '1';
try { savedRoom = localStorage.getItem('hg_room') || '1'; } catch (e) { /* ignore */ }
setRoom(savedRoom);
let savedTheme = '1';
try { savedTheme = localStorage.getItem('hg_theme') || '1'; } catch (e) { /* ignore */ }
setTheme(savedTheme);

// ------------------------------------------------------------ boot
if (S.token) connect();
route();
