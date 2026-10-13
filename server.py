"""Home Game — private No-Limit Hold'em for friends.

Run locally:  ROOM_CODE=letsplay uvicorn server:app --reload
Must run as a single process (game state lives in memory).
"""
import asyncio
import json
import os
import random
import re
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import bots
import db
from engine import Table, TableError

ROOM_CODE = os.environ.get("ROOM_CODE", "changeme")
MAX_TABLES = 20
BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
NAME_RE = re.compile(r"[A-Za-z0-9 _\-]{2,16}")


class Runner:
    """Async wrapper around one engine Table: lock, turn timer, runout and next-hand scheduling."""

    def __init__(self, table, creator, practice=False):
        self.t = table
        self.creator = creator
        self.practice = practice      # practice tables allow bots and never touch the ledger
        self.bot_styles = {}          # bot pid -> style
        self.bot_task = None
        self.bot_key = None
        self.lock = asyncio.Lock()
        self.timer_task = None
        self.timer_key = None
        self.deadline = None
        self.runout_task = None
        self.next_task = None
        self.chat = []


class Conn:
    def __init__(self, ws, name):
        self.ws = ws
        self.name = name
        self.table_id = None


runners: dict[str, Runner] = {}
conns: set[Conn] = set()
_last_lobby = None


@asynccontextmanager
async def lifespan(app):
    # Anyone still seated when the server last stopped gets cashed out at their last known stack.
    for row in db.take_orphans():
        if row["stack"] > 0:
            db.add_ledger(row["name"], row["table_id"], row["table_name"], "cashout", row["stack"])
    for tid, cfg, creator in db.load_tables():
        practice = bool(cfg.pop("practice", False))
        t = Table(tid, **cfg)
        t.hand_no = db.max_hand_no(tid)
        runners[tid] = Runner(t, creator, practice)
    yield


app = FastAPI(lifespan=lifespan)


# ------------------------------------------------------------------ helpers

def cancel(task):
    if task and not task.done():
        task.cancel()


def in_play(s, t):
    """Chips a seated player owns: stack at the start of the current hand plus pending add-ons."""
    return s.stack + s.pending_chips + (s.contrib if t.hand_running and s.in_hand else 0)


def is_bot(s):
    return str(s.pid).startswith("bot:")


def snapshot(r):
    if r.practice:
        return
    t = r.t
    db.snapshot(t.id, t.name, [(s.name, in_play(s, t)) for s in t.seats if s])


def cash_out(r, s):
    amt = s.stack + s.pending_chips
    if amt > 0 and not r.practice:
        db.add_ledger(s.name, r.t.id, r.t.name, "cashout", amt)


def ready(r):
    """A hand can start: 2+ eligible players and at least one of them is a human."""
    t = r.t
    return t.can_start() and any(not is_bot(t.seats[i]) for i in t.eligible())


async def send(c, msg):
    try:
        await asyncio.wait_for(c.ws.send_text(json.dumps(msg)), timeout=5)
    except Exception:
        pass


def view_msg(r, name):
    v = r.t.view(name)
    v["time_left"] = max(0.0, r.deadline - time.time()) if r.deadline else None
    v["timeout"] = r.t.timeout
    v["practice"] = r.practice
    return {"type": "table", "table": v}


async def broadcast_table(r):
    for c in list(conns):
        if c.table_id == r.t.id:
            await send(c, view_msg(r, c.name))


def lobby_payload():
    tables = []
    inplay = {}
    for r in runners.values():
        t = r.t
        tables.append(dict(id=t.id, name=t.name, sb=t.sb, bb=t.bb, min_buy=t.min_buy, max_buy=t.max_buy,
                           max_seats=t.max_seats, timeout=t.timeout, creator=r.creator,
                           practice=r.practice, players=[s.name for s in t.seats if s],
                           humans=[s.name for s in t.seats if s and not is_bot(s)]))
        if r.practice:
            continue
        for s in t.seats:
            if s:
                inplay[s.name] = inplay.get(s.name, 0) + in_play(s, t)
    summary = db.ledger_summary()
    ledger = []
    for n in set(summary) | set(inplay):
        b, c = summary.get(n, (0, 0))
        ip = inplay.get(n, 0)
        ledger.append(dict(name=n, buyin=b, cashout=c, in_play=ip, net=c + ip - b))
    ledger.sort(key=lambda x: (-x["net"], x["name"].lower()))
    return {"type": "lobby", "tables": tables, "ledger": ledger}


async def broadcast_lobby(force=False):
    global _last_lobby
    p = lobby_payload()
    key = json.dumps(p, sort_keys=True)
    if key == _last_lobby and not force:
        return
    _last_lobby = key
    for c in list(conns):
        await send(c, p)


# ------------------------------------------------------------------ table lifecycle (call with r.lock held)

async def after_change(r):
    t = r.t
    t.auto_actions()
    if t.runout_pending and r.runout_task is None:
        r.runout_task = asyncio.create_task(runout(r))
    if t.hand_running and t.current is not None:
        key = (t.hand_no, t.action_seq)
        if r.timer_key != key:
            cancel(r.timer_task)
            r.timer_key = key
            r.deadline = time.time() + t.timeout
            r.timer_task = asyncio.create_task(turn_timer(r, key))
        if is_bot(t.seats[t.current]) and r.bot_key != key:
            r.bot_key = key
            r.bot_task = asyncio.create_task(bot_turn(r, key))
    else:
        cancel(r.timer_task)
        r.timer_task = r.timer_key = r.deadline = None
    if not t.hand_running:
        for s in t.seats:                       # bots rebuy and never sit out
            if s and is_bot(s):
                s.sitting_out = False
                s.auto = None
                if s.stack == 0:
                    s.stack = t.max_buy
    while t.finished:
        h = t.finished.pop(0)
        db.save_hand(t.id, h["hand_no"], h["log"])
    while t.leavers:
        s = t.leavers.pop(0)
        r.bot_styles.pop(s.pid, None)
        cash_out(r, s)
    snapshot(r)
    if not t.hand_running and r.next_task is None and ready(r):
        if t.results and any(x.get("hand") for x in t.results):
            delay = 4.5      # let people see the showdown
        elif t.results:
            delay = 2
        else:
            delay = 1
        r.next_task = asyncio.create_task(next_hand(r, delay))
    await broadcast_table(r)
    await broadcast_lobby()


async def turn_timer(r, key):
    try:
        await asyncio.sleep(r.t.timeout)
    except asyncio.CancelledError:
        return
    async with r.lock:
        if r.timer_key != key:
            return
        r.timer_task = r.timer_key = None
        r.t.timeout_current()
        await after_change(r)


async def runout(r):
    while True:
        await asyncio.sleep(1.4)
        async with r.lock:
            done = r.t.runout_step()
            if done:
                r.runout_task = None
            await after_change(r)
            if done:
                return


async def next_hand(r, delay):
    await asyncio.sleep(delay)
    async with r.lock:
        r.next_task = None
        if ready(r):
            r.t.start_hand()
        await after_change(r)


async def bot_turn(r, key):
    await asyncio.sleep(random.uniform(0.8, 2.0))   # feel human
    async with r.lock:
        t = r.t
        if (t.hand_no, t.action_seq) != key or t.current is None:
            return
        s = t.seats[t.current]
        if not is_bot(s):
            return
        pid, o = s.pid, t.view(s.pid)["options"]
        args = (o, list(s.hole), list(t.board), t.pot_total(), len(t.live()) - 1,
                r.bot_styles.get(pid, "shark"))
    action, amount = await asyncio.to_thread(bots.decide, *args, random.Random())
    async with r.lock:
        t = r.t
        if (t.hand_no, t.action_seq) != key:
            return
        try:
            t.act(pid, action, amount)
        except TableError:
            try:
                t.act(pid, "check" if o["can_check"] else "fold")
            except TableError:
                return
        await after_change(r)


# ------------------------------------------------------------------ websocket

def _int(m, key, label, lo, hi):
    try:
        v = int(m.get(key))
    except (TypeError, ValueError):
        raise TableError(f"{label} must be a number")
    if not lo <= v <= hi:
        raise TableError(f"{label} must be between {lo} and {hi}")
    return v


async def create_table(c, m):
    if len(runners) >= MAX_TABLES:
        raise TableError("Too many tables open — close one first")
    name = str(m.get("name", "")).strip()[:30] or f"{c.name}'s game"
    sb = _int(m, "sb", "Small blind", 1, 1_000_000)
    bb = _int(m, "bb", "Big blind", sb, 2_000_000)
    min_buy = _int(m, "min_buy", "Min buy-in", bb, 100_000_000)
    max_buy = _int(m, "max_buy", "Max buy-in", min_buy, 100_000_000)
    seats = _int(m, "max_seats", "Seats", 2, 9)
    timeout = _int(m, "timeout", "Turn timer", 10, 120)
    practice = bool(m.get("practice"))
    tid = secrets.token_urlsafe(5)
    t = Table(tid, name, sb, bb, min_buy, max_buy, max_seats=seats, timeout=timeout)
    runners[tid] = Runner(t, c.name, practice)
    db.save_table(tid, {**t.config(), "practice": practice}, c.name)
    await send(c, {"type": "created", "table_id": tid})
    await broadcast_lobby()


async def close_table(c, m):
    r = runners.get(m.get("table_id"))
    if not r:
        raise TableError("No such table")
    if r.creator != c.name:
        raise TableError("Only the table's creator can close it")
    if any(s and not is_bot(s) for s in r.t.seats):
        raise TableError("Everyone has to stand up first")
    for task in (r.timer_task, r.runout_task, r.next_task, r.bot_task):
        cancel(task)
    del runners[r.t.id]
    db.delete_table(r.t.id)
    for x in list(conns):
        if x.table_id == r.t.id:
            x.table_id = None
            await send(x, {"type": "table_closed", "table_id": r.t.id})
    await broadcast_lobby()


async def table_request(c, typ, m):
    r = runners.get(c.table_id) if c.table_id else None
    if not r:
        raise TableError("Open a table first")
    if typ == "chat":
        text = str(m.get("text", "")).strip()[:300]
        if not text:
            return
        msg = {"name": c.name, "text": text, "ts": time.time()}
        r.chat = (r.chat + [msg])[-200:]
        for x in list(conns):
            if x.table_id == r.t.id:
                await send(x, {"type": "chat", "message": msg})
        return
    async with r.lock:
        t = r.t
        if typ == "sit":
            try:
                seat = _int(m, "seat", "Seat", 0, t.max_seats - 1)
            except TableError:
                raise TableError("Pick an open seat")
            buyin = _int(m, "buyin", "Buy-in", t.min_buy, t.max_buy)
            t.sit(c.name, c.name, seat, buyin)
            if not r.practice:
                db.add_ledger(c.name, t.id, t.name, "buyin", buyin)
        elif typ == "action":
            t.act(c.name, m.get("action"), m.get("amount") or 0)
        elif typ == "stand":
            s = t.stand(c.name)
            if s:
                cash_out(r, s)
        elif typ == "sitout":
            t.set_sitting_out(c.name, bool(m.get("value")))
        elif typ == "addchips":
            amt = _int(m, "amount", "Amount", 1, t.max_buy)
            t.add_chips(c.name, amt)
            if not r.practice:
                db.add_ledger(c.name, t.id, t.name, "buyin", amt)
        elif typ == "add_bot":
            if not r.practice:
                raise TableError("Bots are only allowed at practice tables")
            try:
                seat = _int(m, "seat", "Seat", 0, t.max_seats - 1)
            except TableError:
                raise TableError("Pick an open seat")
            style = m.get("style") if m.get("style") in bots.STYLES else random.choice(list(bots.STYLES))
            base = bots.STYLES[style]["name"]
            taken = {s.name for s in t.seats if s}
            n = 1
            name = base
            while name in taken:
                n += 1
                name = f"{base} {n}"
            pid = "bot:" + secrets.token_hex(4)
            t.sit(pid, name, seat, t.max_buy)
            r.bot_styles[pid] = style
        elif typ == "remove_bot":
            seat = _int(m, "seat", "Seat", 0, t.max_seats - 1)
            s = t.seats[seat]
            if not s or not is_bot(s):
                raise TableError("That seat isn't a bot")
            if t.stand(s.pid):
                r.bot_styles.pop(s.pid, None)
        else:
            raise TableError("Unknown request")
        await after_change(r)


async def handle(c, m):
    typ = m.get("type")
    if typ == "ping":
        return
    if typ == "lobby":
        await send(c, lobby_payload())
    elif typ == "watch":
        r = runners.get(m.get("table_id"))
        if not r:
            await send(c, {"type": "table_closed", "table_id": m.get("table_id")})
            return
        c.table_id = r.t.id
        await send(c, {"type": "chat_history", "messages": r.chat[-60:]})
        await send(c, view_msg(r, c.name))
    elif typ == "unwatch":
        c.table_id = None
    elif typ == "create_table":
        await create_table(c, m)
    elif typ == "close_table":
        await close_table(c, m)
    elif typ == "settle":
        if any(s for r in runners.values() if not r.practice for s in r.t.seats):
            raise TableError("Everyone has to stand up before settling")
        db.settle()
        await broadcast_lobby(force=True)
    else:
        await table_request(c, typ, m)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = ""):
    await ws.accept()
    name = db.user_for_token(token)
    if not name:
        await ws.send_text(json.dumps({"type": "auth_failed"}))
        await ws.close()
        return
    c = Conn(ws, name)
    conns.add(c)
    await send(c, lobby_payload())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                m = json.loads(raw)
                if not isinstance(m, dict):
                    continue
                await handle(c, m)
            except TableError as e:
                await send(c, {"type": "error", "message": str(e)})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        conns.discard(c)


# ------------------------------------------------------------------ http

@app.post("/api/login")
async def api_login(req: Request):
    b = await req.json()
    if not secrets.compare_digest(str(b.get("room_code", "")), ROOM_CODE):
        raise HTTPException(403, "Wrong room code")
    name = str(b.get("name", "")).strip()
    if not NAME_RE.fullmatch(name):
        raise HTTPException(400, "Name must be 2–16 letters, numbers, spaces, - or _")
    pw = str(b.get("password", ""))
    if len(pw) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    try:
        name, token = db.login(name, pw)
    except ValueError as e:
        raise HTTPException(401, str(e))
    return {"token": token, "name": name}


@app.get("/api/hands/{tid}")
async def api_hands(tid: str, token: str = "", limit: int = 30):
    if not db.user_for_token(token):
        raise HTTPException(401, "Log in again")
    return db.recent_hands(tid, max(1, min(limit, 100)))


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
