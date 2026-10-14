# Home Game — private No-Limit Hold'em for your crew

A Donkhouse-style web app: a lobby of cash tables, real-time play in the browser (phone or desktop), and a running ledger so everyone settles up afterward. No real money moves through it — chips are tracked, you square up among yourselves.

## What's in it
- **Lobby**: anyone can open a table (blinds, buy-in range, 2–9 seats, turn timer). The creator can close it once it's empty.
- **Table**: NLHE with correct blinds (incl. heads-up), min-raise rules, short all-in raises that don't reopen action, side pots, split pots / odd chips, uncalled bets returned, all-in runouts.
- **Turn timer**: time out → auto check/fold, and you're sat out until you tap "I'm back".
- **Stand up / sit out / add chips / rebuy**. Standing mid-hand folds you and cashes you out when the hand ends.
- **Bet sizing**: Min, ½, ¾, Pot, All-in presets plus slider. Pre-action "Check / Fold" box.
- **Chat**, current-hand log, and saved **hand history**.
- **Practice tables with bots**: tick "Practice table" when creating a table, then tap **+ Bot** on any empty seat (Rock = tight, Shark = balanced, Maniac = loose/aggressive). Bots rebuy automatically, hands only deal while a human is seated, and practice chips never touch the ledger.
- **Dollars or cents**: pick when creating a table — whole-dollar stakes ($1/$2) or cents ($0.10/$0.25). Everything shows as money, and the ledger adds both kinds together.
- **Looks**: each player picks a table design (Table 1–6) and a room background (Room 1–4: Dark, Casino, Den, Vegas); it's remembered on their device.
- **Ledger**: buy-ins, cash-outs, chips on table, net per player (in dollars and cents). "Mark settled" zeroes it once everyone's standing.
- Hole cards are dealt server-side and only sent to their owner (and to everyone at showdown / all-in).
- If the server restarts mid-session, everyone seated is cashed out at their stack from the start of that hand, so the ledger stays balanced.

## Run locally
```bash
pip install -r requirements.txt
ROOM_CODE=letsplay uvicorn server:app --reload
# open http://localhost:8000
```
Run the engine tests: `python tests/test_engine.py`

## Deploy on Render (simplest)
1. Push this folder to a GitHub repo.
2. In Render: **New → Blueprint**, pick the repo. It reads `render.yaml`.
3. Set `ROOM_CODE` to whatever shared password you want.
4. Share the `https://…onrender.com` URL. Each friend enters the room code plus their own name/password (accounts are created on first login).

Notes:
- The smallest paid plan (`0.5c-512mb`) is used because it keeps a persistent disk for the ledger and history. On the free plan the database resets whenever the service restarts or sleeps, and the first visit after idling takes ~30s to wake.
- Keep it to **one instance / one worker** — live game state is in memory.
- Railway or Fly.io also work: start command `uvicorn server:app --host 0.0.0.0 --port $PORT`, mount a volume, and point `DB_PATH` at it.

## Files
- `engine.py` — the poker rules (pure Python, no I/O)
- `bots.py` — practice bots (equity simulation + play style)
- `server.py` — FastAPI + WebSockets: lobby, tables, timers, ledger
- `db.py` — SQLite (accounts, ledger, hand history)
- `static/` — the web client (plain HTML/CSS/JS)
