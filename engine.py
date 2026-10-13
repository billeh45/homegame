"""No-Limit Hold'em table engine. Pure Python, no I/O — the server wraps it."""
import itertools
import random
from collections import Counter

RANKS = "23456789TJQKA"
SUITS = "shdc"
RV = {r: i + 2 for i, r in enumerate(RANKS)}
RNAME = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight",
         9: "Nine", 10: "Ten", 11: "Jack", 12: "Queen", 13: "King", 14: "Ace"}


class TableError(Exception):
    """A rejected request; the message is safe to show the player."""


# ---------------------------------------------------------------- hand evaluation

def eval5(cards):
    vals = sorted((RV[c[0]] for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    uniq = sorted(set(vals), reverse=True)
    straight_hi = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_hi = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight_hi = 5
    groups = sorted(Counter(vals).items(), key=lambda x: (x[1], x[0]), reverse=True)
    ordered = [v for v, _ in groups]
    if straight_hi and flush:
        return (8, straight_hi)
    if groups[0][1] == 4:
        return (7, *ordered)
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, *ordered)
    if flush:
        return (5, *vals)
    if straight_hi:
        return (4, straight_hi)
    if groups[0][1] == 3:
        return (3, *ordered)
    if groups[0][1] == 2 and groups[1][1] == 2:
        return (2, *ordered)
    if groups[0][1] == 2:
        return (1, *ordered)
    return (0, *vals)


def best_hand(cards):
    best, best_combo = None, None
    for combo in itertools.combinations(cards, 5):
        s = eval5(combo)
        if best is None or s > best:
            best, best_combo = s, combo
    return best, list(best_combo)


def _pl(v):
    n = RNAME[v]
    return n + "es" if n == "Six" else n + "s"


def describe(score):
    c, v = score[0], score[1:]
    if c == 8:
        return "Royal Flush" if v[0] == 14 else f"Straight Flush, {RNAME[v[0]]} high"
    if c == 7:
        return f"Four {_pl(v[0])}"
    if c == 6:
        return f"Full House, {_pl(v[0])} full of {_pl(v[1])}"
    if c == 5:
        return f"Flush, {RNAME[v[0]]} high"
    if c == 4:
        return f"Straight, {RNAME[v[0]]} high"
    if c == 3:
        return f"Three {_pl(v[0])}"
    if c == 2:
        return f"Two Pair, {_pl(v[0])} and {_pl(v[1])}"
    if c == 1:
        return f"Pair of {_pl(v[0])}"
    return f"{RNAME[v[0]]} High"


# ---------------------------------------------------------------- table

class Seat:
    def __init__(self, pid, name, stack):
        self.pid = pid
        self.name = name
        self.stack = stack
        self.pending_chips = 0        # add-on waiting for the current hand to finish
        self.sitting_out = False
        self.leave_after_hand = False
        self.reset_hand()

    def reset_hand(self):
        self.in_hand = False
        self.folded = False
        self.bet = 0                  # chips in front this street
        self.contrib = 0              # chips put in this hand
        self.hole = []
        self.has_acted = False
        self.can_raise = True
        self.last_action = ""
        self.won = 0
        self.auto = None              # None | "fold" | "checkfold"

    @property
    def all_in(self):
        return self.in_hand and not self.folded and self.stack == 0


class Table:
    def __init__(self, tid, name, sb, bb, min_buy, max_buy, max_seats=9, timeout=30, rng=None):
        self.id = tid
        self.name = name
        self.sb, self.bb = sb, bb
        self.min_buy, self.max_buy = min_buy, max_buy
        self.max_seats = max_seats
        self.timeout = timeout
        self.rng = rng or random.SystemRandom()
        self.seats = [None] * max_seats
        self.button = -1
        self.hand_no = 0
        self.hand_running = False
        self.street = None
        self.board, self.deck = [], []
        self.current = None
        self.current_bet = 0
        self.min_raise = bb
        self.action_seq = 0
        self.runout_pending = False
        self.reveal_all = False
        self.log = []
        self.results = None
        self.leavers = []    # Seats removed at hand end; server cashes them out
        self.finished = []   # finished hand logs; server persists them

    def config(self):
        return dict(name=self.name, sb=self.sb, bb=self.bb, min_buy=self.min_buy,
                    max_buy=self.max_buy, max_seats=self.max_seats, timeout=self.timeout)

    # ---- helpers
    def seat_index(self, pid):
        for i, s in enumerate(self.seats):
            if s and s.pid == pid:
                return i
        return None

    def _seat(self, pid):
        i = self.seat_index(pid)
        if i is None:
            raise TableError("You're not seated at this table")
        return self.seats[i]

    def _next_in(self, idx, cands):
        for k in range(1, self.max_seats + 1):
            j = (idx + k) % self.max_seats
            if j in cands:
                return j
        return None

    def eligible(self):
        return [i for i, s in enumerate(self.seats)
                if s and not s.sitting_out and not s.leave_after_hand and s.stack + s.pending_chips > 0]

    def can_start(self):
        return not self.hand_running and len(self.eligible()) >= 2

    def in_hand_seats(self):
        return [i for i, s in enumerate(self.seats) if s and s.in_hand]

    def live(self):
        return [i for i in self.in_hand_seats() if not self.seats[i].folded]

    def pot_total(self):
        return sum(self.seats[i].contrib for i in self.in_hand_seats())

    def _put(self, i, amt):
        s = self.seats[i]
        amt = min(amt, s.stack)
        s.stack -= amt
        s.bet += amt
        s.contrib += amt
        return amt

    # ---- seating
    def sit(self, pid, name, idx, buyin):
        if self.seat_index(pid) is not None:
            raise TableError("You're already seated here")
        if not (0 <= idx < self.max_seats) or self.seats[idx]:
            raise TableError("That seat is taken")
        if not (self.min_buy <= buyin <= self.max_buy):
            raise TableError(f"Buy-in must be {self.min_buy}–{self.max_buy}")
        self.seats[idx] = Seat(pid, name, buyin)

    def stand(self, pid):
        """Returns the Seat if removed now, or None if they leave when the hand ends."""
        i = self.seat_index(pid)
        if i is None:
            raise TableError("You're not seated")
        s = self.seats[i]
        if self.hand_running and s.in_hand:
            s.leave_after_hand = True
            s.sitting_out = True
            if not s.folded and s.stack > 0:
                s.auto = "fold"
            return None
        self.seats[i] = None
        return s

    def set_sitting_out(self, pid, value):
        s = self._seat(pid)
        if s.leave_after_hand:
            raise TableError("You're leaving after this hand")
        s.sitting_out = value
        if not value:
            s.auto = None

    def add_chips(self, pid, amt):
        s = self._seat(pid)
        if amt <= 0:
            raise TableError("Enter a positive amount")
        if s.stack + s.pending_chips + amt > self.max_buy:
            raise TableError(f"Max stack at this table is {self.max_buy}")
        if self.hand_running and s.in_hand:
            s.pending_chips += amt
        else:
            s.stack += amt

    # ---- hand flow
    def start_hand(self):
        if not self.can_start():
            return False
        for s in self.seats:
            if s:
                s.stack += s.pending_chips
                s.pending_chips = 0
                s.reset_hand()
        elig = self.eligible()
        self.hand_no += 1
        self.hand_running = True
        self.deck = [r + su for r in RANKS for su in SUITS]
        self.rng.shuffle(self.deck)
        self.board = []
        self.results = None
        self.reveal_all = False
        self.runout_pending = False
        for i in elig:
            self.seats[i].in_hand = True
        self.button = self._next_in(self.button, elig)
        if len(elig) == 2:
            sb = self.button
            bb = self._next_in(sb, elig)
        else:
            sb = self._next_in(self.button, elig)
            bb = self._next_in(sb, elig)
        self.log = [f"Hand #{self.hand_no} — {self.name} (NLH {self.sb}/{self.bb})",
                    f"Button: {self.seats[self.button].name}"]
        for i in elig:
            s = self.seats[i]
            self.log.append(f"Seat {i + 1}: {s.name} ({s.stack})")
        self._post(sb, self.sb, "SB")
        self._post(bb, self.bb, "BB")
        order, j = [], self.button
        for _ in range(len(elig)):          # deal starting left of the button
            j = self._next_in(j, elig)
            order.append(j)
        for _ in range(2):
            for i in order:
                self.seats[i].hole.append(self.deck.pop())
        self.street = "preflop"
        self.current_bet = self.bb
        self.min_raise = self.bb
        self.current = None
        self._move_to_next(bb)
        return True

    def _post(self, i, amount, label):
        s = self.seats[i]
        amt = self._put(i, amount)
        s.last_action = label
        self.log.append(f"{s.name} posts {label} {amt}" + (" and is all in" if s.stack == 0 else ""))

    def _find_next(self, from_idx):
        for k in range(1, self.max_seats + 1):
            j = (from_idx + k) % self.max_seats
            s = self.seats[j]
            if (s and s.in_hand and not s.folded and s.stack > 0
                    and (not s.has_acted or s.bet < self.current_bet)):
                return j
        return None

    def _move_to_next(self, from_idx):
        nxt = self._find_next(from_idx)
        if nxt is None:
            self._end_round()
        else:
            self.current = nxt
            self.action_seq += 1

    def act(self, pid, action, amount=0):
        if not self.hand_running or self.current is None:
            raise TableError("No action pending")
        i = self.current
        s = self.seats[i]
        if s.pid != pid:
            raise TableError("It's not your turn")
        owe = self.current_bet - s.bet
        if action == "fold":
            s.folded = True
            s.last_action = "Fold"
            self.log.append(f"{s.name} folds")
        elif action == "check":
            if owe > 0:
                raise TableError("You can't check — there's a bet to call")
            s.has_acted = True
            s.last_action = "Check"
            self.log.append(f"{s.name} checks")
        elif action == "call":
            if owe <= 0:
                raise TableError("Nothing to call")
            amt = self._put(i, owe)
            s.has_acted = True
            s.last_action = "Call"
            self.log.append(f"{s.name} calls {amt}" + (" and is all in" if s.stack == 0 else ""))
        elif action in ("bet", "raise"):
            if not s.can_raise or s.stack <= owe:
                raise TableError("You can't raise here")
            max_to = s.bet + s.stack
            try:
                target = min(int(amount), max_to)
            except (TypeError, ValueError):
                raise TableError("Enter a raise amount")
            min_to = self.current_bet + self.min_raise
            if target <= self.current_bet or (target < min_to and target < max_to):
                raise TableError(f"Minimum is {min(min_to, max_to)}")
            size = target - self.current_bet
            is_bet = self.current_bet == 0
            self._put(i, target - s.bet)
            full = size >= self.min_raise
            for j in self.live():
                if j == i:
                    continue
                o = self.seats[j]
                if o.stack == 0:
                    continue
                if full:
                    o.has_acted = False
                    o.can_raise = True
                elif o.has_acted:          # short all-in raise doesn't reopen action
                    o.has_acted = False
                    o.can_raise = False
            if full:
                self.min_raise = size
            self.current_bet = target
            s.has_acted = True
            s.last_action = ("Bet " if is_bet else "Raise ") + str(target)
            self.log.append(f"{s.name} {'bets' if is_bet else 'raises to'} {target}"
                            + (" and is all in" if s.stack == 0 else ""))
        else:
            raise TableError("Unknown action")
        if len(self.live()) == 1:
            self._finish_uncontested()
            return
        self._move_to_next(i)

    def _auto_act(self, i):
        s = self.seats[i]
        if s.auto == "checkfold" and s.bet >= self.current_bet:
            self.act(s.pid, "check")
        else:
            self.act(s.pid, "fold")

    def auto_actions(self):
        guard = 0
        while (self.hand_running and self.current is not None
               and self.seats[self.current].auto and guard < 50):
            self._auto_act(self.current)
            guard += 1

    def timeout_current(self):
        if not self.hand_running or self.current is None:
            return
        s = self.seats[self.current]
        s.sitting_out = True
        s.auto = "checkfold"
        self.log.append(f"{s.name} timed out")
        self._auto_act(self.current)

    def _return_uncalled(self):
        ids = self.in_hand_seats()
        if not ids:
            return
        top = max(ids, key=lambda i: self.seats[i].bet)
        tb = self.seats[top].bet
        second = max((self.seats[i].bet for i in ids if i != top), default=0)
        if tb > second:
            ex = tb - second
            s = self.seats[top]
            s.bet -= ex
            s.contrib -= ex
            s.stack += ex
            self.log.append(f"Uncalled bet ({ex}) returned to {s.name}")

    def _end_round(self):
        self._return_uncalled()
        for i in self.in_hand_seats():
            s = self.seats[i]
            s.bet = 0
            s.has_acted = False
            s.can_raise = True
            if not s.folded:
                s.last_action = ""
        self.current_bet = 0
        self.min_raise = self.bb
        self.current = None
        if self.street == "river":
            self._showdown()
            return
        able = [i for i in self.live() if self.seats[i].stack > 0]
        if len(able) < 2:
            self.runout_pending = True    # server deals the rest with a pause between streets
            self.reveal_all = True
            return
        self._deal_street()
        self._move_to_next(self.button)

    def _deal_street(self):
        self.deck.pop()  # burn
        if self.street == "preflop":
            self.board += [self.deck.pop() for _ in range(3)]
            self.street = "flop"
        elif self.street == "flop":
            self.board.append(self.deck.pop())
            self.street = "turn"
        elif self.street == "turn":
            self.board.append(self.deck.pop())
            self.street = "river"
        self.log.append(f"*** {self.street.upper()} *** {' '.join(self.board)}")

    def runout_step(self):
        """Deal one more street during an all-in runout. Returns True when the hand is over."""
        if not self.runout_pending:
            return True
        self._deal_street()
        if self.street == "river":
            self.runout_pending = False
            self._showdown()
            return True
        return False

    def _finish_uncontested(self):
        self._return_uncalled()
        w = self.live()[0]
        s = self.seats[w]
        pot = self.pot_total()
        s.stack += pot
        s.won = pot
        self.current = None
        self.log.append(f"{s.name} wins {pot}")
        self.results = [{"amount": pot, "winners": [s.name], "seats": [w], "hand": None}]
        self._end_hand()

    def _build_pots(self):
        ids = self.in_hand_seats()
        rem = {i: self.seats[i].contrib for i in ids}
        live = self.live()
        pots = []
        while any(rem[i] > 0 for i in live):
            lvl = min(rem[i] for i in live if rem[i] > 0)
            elig = [i for i in live if rem[i] > 0]
            amt = 0
            for i in ids:
                take = min(rem[i], lvl)
                amt += take
                rem[i] -= take
            if pots and pots[-1][1] == elig:
                pots[-1][0] += amt
            else:
                pots.append([amt, elig])
        leftover = sum(rem.values())
        if leftover and pots:
            pots[-1][0] += leftover
        return pots

    def _showdown(self):
        self.street = "showdown"
        self.reveal_all = True
        self.current = None
        n = self.max_seats
        scores = {}
        for i in self.live():
            s = self.seats[i]
            sc, best = best_hand(s.hole + self.board)
            scores[i] = (sc, best)
            self.log.append(f"{s.name} shows {' '.join(s.hole)} ({describe(sc)})")
        results = []
        pots = self._build_pots()
        for k, (amount, elig) in enumerate(pots):
            if amount <= 0:
                continue
            top = max(scores[i][0] for i in elig)
            winners = [i for i in elig if scores[i][0] == top]
            winners.sort(key=lambda i: (i - self.button) % n or n)  # odd chip left of button
            share, rem = divmod(amount, len(winners))
            for j, i in enumerate(winners):
                win = share + (1 if j < rem else 0)
                self.seats[i].stack += win
                self.seats[i].won += win
            names = [self.seats[i].name for i in winners]
            label = "main pot" if k == 0 else f"side pot {k}"
            if len(pots) == 1:
                label = "the pot"
            verb = "split" if len(winners) > 1 else "wins"
            self.log.append(f"{' & '.join(names)} {verb} {label} ({amount}) with {describe(top)}")
            results.append({"amount": amount, "winners": names, "seats": winners,
                            "hand": describe(top), "cards": scores[winners[0]][1]})
        self.results = results
        self._end_hand()

    def _end_hand(self):
        self.hand_running = False
        self.current = None
        self.runout_pending = False
        self.finished.append({"hand_no": self.hand_no, "log": "\n".join(self.log)})
        for i, s in enumerate(self.seats):
            if s and s.leave_after_hand:
                self.leavers.append(s)
                self.seats[i] = None

    # ---- what one viewer is allowed to see
    def view(self, pid):
        me = self.seat_index(pid)
        seats = []
        for i, s in enumerate(self.seats):
            if not s:
                seats.append(None)
                continue
            mine = s.pid == pid
            cards = []
            if s.hole and s.in_hand and (not s.folded or mine):
                show = mine or (not s.folded and self.reveal_all)
                cards = list(s.hole) if show else ["??", "??"]
            seats.append({
                "name": s.name, "stack": s.stack, "bet": s.bet, "folded": s.folded,
                "all_in": s.all_in, "in_hand": s.in_hand, "sitting_out": s.sitting_out,
                "last_action": s.last_action, "cards": cards, "is_you": mine,
                "leaving": s.leave_after_hand, "pending": s.pending_chips,
                "bot": str(s.pid).startswith("bot:"),
            })
        v = {
            "id": self.id, "name": self.name, "sb": self.sb, "bb": self.bb,
            "min_buy": self.min_buy, "max_buy": self.max_buy, "max_seats": self.max_seats,
            "hand_no": self.hand_no, "hand_running": self.hand_running, "street": self.street,
            "board": list(self.board), "button": self.button if self.hand_no else None,
            "current": self.current if self.hand_running else None,
            "action_seq": self.action_seq, "pot": self.pot_total(),
            "results": None if self.hand_running else self.results,
            "me": me, "seats": seats, "log": self.log[-80:], "options": None,
        }
        if me is not None and self.hand_running and self.current == me:
            s = self.seats[me]
            owe = self.current_bet - s.bet
            max_to = s.bet + s.stack
            v["options"] = {
                "to_call": min(owe, s.stack), "can_check": owe <= 0,
                "can_raise": s.can_raise and s.stack > owe,
                "min_to": min(self.current_bet + self.min_raise, max_to), "max_to": max_to,
                "current_bet": self.current_bet, "my_bet": s.bet, "is_bet": self.current_bet == 0,
            }
        return v
