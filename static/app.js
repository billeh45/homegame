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
    const lab = el('label', fd.full ? 'full' : null, fd.label);
    const inp = el('input');
    inp.type = fd.type || 'number';
    if (inp.type === 'number') inp.inputMode = 'numeric';
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
    for (const k in inputs) vals[k] = inputs[k].type === 'number' ? Number(inputs[k].value) : inputs[k].value.trim();
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
    info.append(el('div', 'tname', t.name),
      el('div', 'muted small', `NLH ${fmt(t.sb)}/${fmt(t.bb)} · buy-in ${fmt(t.min_buy)}–${fmt(t.max_buy)} · ${t.players.length}/${t.max_seats} seated`));
    if (t.players.length) info.append(el('div', 'small players', t.players.join(', ')));
    const btns = el('div', 'btns');
    const open = el('button', 'btn primary small', 'Open');
    open.onclick = () => { location.hash = '#t=' + encodeURIComponent(t.id); };
    btns.append(open);
    if (t.creator === S.name && !t.players.length) {
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
    const net = el('td', r.net > 0 ? 'pos' : r.net < 0 ? 'neg' : '', (r.net > 0 ? '+' : '') + fmt(r.net));
    tr.append(el('td', null, r.name), el('td', null, fmt(r.buyin)), el('td', null, fmt(r.cashout)), el('td', null, fmt(r.in_play)), net);
    body.append(tr);
  }
}

$('#newTableBtn').onclick = () => formModal('New cash table', [
  { key: 'name', label: 'Table name', type: 'text', value: `${S.name}'s game`, maxlength: 30, full: true },
  { key: 'sb', label: 'Small blind', value: 1, min: 1 },
  { key: 'bb', label: 'Big blind', value: 2, min: 1 },
  { key: 'min_buy', label: 'Min buy-in', value: 40, min: 1 },
  { key: 'max_buy', label: 'Max buy-in', value: 200, min: 1 },
  { key: 'max_seats', label: 'Seats (2–9)', value: 9, min: 2, max: 9 },
  { key: 'timeout', label: 'Turn timer (sec)', value: 30, min: 10, max: 120 },
], 'Create', v => send({ type: 'create_table', ...v }));

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
  $('#tStakes').textContent = `NLH ${fmt(t.sb)}/${fmt(t.bb)}`;
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
      else { d.classList.add('empty'); d.append(el('div', 'empty-dot')); }
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
    plate.append(el('div', 'pname', s.name), el('div', 'pstack', fmt(s.stack)));
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
      const b = el('div', 'bet', fmt(s.bet)); b.style.left = bx + '%'; b.style.top = by + '%'; felt.append(b);
    }
    if (t.button === i) {
      const [dx, dy] = seatPos(i, t, R[0] * 0.72, R[1] * 0.68);
      const b = el('div', 'dealer', 'D'); b.style.left = (dx + 6) + '%'; b.style.top = dy + '%'; felt.append(b);
    }
  });

  const board = $('#board'); board.innerHTML = '';
  for (let k = 0; k < 5; k++) board.append(t.board[k] ? cardEl(t.board[k]) : el('div', 'card slot'));
  $('#pot').textContent = t.pot > 0 ? `Pot ${fmt(t.pot)}` : '';
  $('#result').textContent = t.results
    ? t.results.map(r => `${r.winners.join(' & ')} ${r.winners.length > 1 ? 'split' : 'wins'} ${fmt(r.amount)}${r.hand ? ' — ' + r.hand : ''}`).join('\n')
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
    const cc = o.can_check ? el('button', 'btn act check', 'Check') : el('button', 'btn act call', `Call ${fmt(o.to_call)}`);
    cc.onclick = () => act(o.can_check ? 'check' : 'call');
    row.append(fold, cc);
    if (o.can_raise) {
      const label = o.is_bet ? 'Bet' : 'Raise to';
      const rb = el('button', 'btn act raise');
      const range = el('input'); range.type = 'range'; range.min = o.min_to; range.max = o.max_to; range.step = 1;
      const num = el('input', 'num'); num.type = 'number'; num.inputMode = 'numeric'; num.min = o.min_to; num.max = o.max_to;
      const upd = v => {
        v = Math.max(o.min_to, Math.min(o.max_to, Math.round(Number(v) || 0)));
        range.value = v; num.value = v; rb.dataset.v = v;
        rb.textContent = v === o.max_to ? `All in ${fmt(v)}` : `${label} ${fmt(v)}`;
      };
      range.oninput = () => upd(range.value);
      num.onchange = () => upd(num.value);
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
  stand.onclick = () => { if (confirm(`Stand up and cash out ${fmt(s.stack + s.pending)}?`)) send({ type: 'stand' }); };
  const so = el('button', 'btn ghost small', s.sitting_out ? "I'm back" : 'Sit out');
  so.onclick = () => send({ type: 'sitout', value: !s.sitting_out });
  if (s.sitting_out) so.classList.replace('ghost', 'primary');
  box.append(stand, so);
  const room = t.max_buy - s.stack - s.pending;
  if (room > 0) {
    const add = el('button', s.stack === 0 && !s.pending ? 'btn primary small' : 'btn ghost small', s.stack === 0 ? 'Rebuy' : 'Add chips');
    add.onclick = () => formModal('Add chips', [{ key: 'amount', label: `Amount (up to ${fmt(room)})`, value: room, min: 1, max: room, full: true }],
      'Add', v => send({ type: 'addchips', amount: v.amount }));
    box.append(add);
  }
  if (s.pending) box.append(el('span', 'muted small', `+${fmt(s.pending)} after this hand`));
}

function sitModal(i) {
  const t = S.table;
  formModal(`Sit in seat ${i + 1}`, [{ key: 'buyin', label: `Buy-in (${fmt(t.min_buy)}–${fmt(t.max_buy)})`, value: t.max_buy, min: t.min_buy, max: t.max_buy, full: true }],
    'Sit down', v => send({ type: 'sit', seat: i, buyin: v.buyin }));
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

// ------------------------------------------------------------ boot
if (S.token) connect();
route();
