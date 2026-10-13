"""Run: python -m pytest tests  (or python tests/test_engine.py)"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import Table, TableError, best_hand, describe, eval5  # noqa: E402


def score(s):
    return best_hand(s.split())[0]


def test_evaluator_ordering():
    ladder = [
        "As Ks Qs Js Ts 2d 3c",   # royal
        "9h 8h 7h 6h 5h Ac Ad",   # straight flush
        "Qc Qd Qh Qs 2c 3d 4h",   # quads
        "Kc Kd Kh 2s 2c 7d 8h",   # full house
        "Ah 9h 6h 4h 2h Kc Kd",   # flush
        "5c 4d 3h 2s Ac Kd Kh",   # wheel straight
        "7c 7d 7h Ks 2c 4d 9h",   # trips
        "Jc Jd 4h 4s Ac 2d 3h",   # two pair
        "Tc Td Ah 8s 6c 3d 2h",   # pair
        "Ac Qd 9h 7s 5c 3d 2h",   # high card
    ]
    scores = [score(h) for h in ladder]
    assert scores == sorted(scores, reverse=True)
    assert describe(scores[0]) == "Royal Flush"
    assert describe(scores[5]) == "Straight, Five high"
    assert describe(scores[3]) == "Full House, Kings full of Twos"


def test_kickers_and_ties():
    assert score("Ac Ad Kh 9s 5c 3d 2h") > score("Ac Ad Qh 9s 5c 3d 2h")
    assert score("2c 2d 3h 3s 4c 4d Ah") > score("2c 2d 3h 3s 4c 4d Kh")  # best two pair + kicker
    assert eval5("Ac Kd Qh Js Tc".split()) == eval5("Ad Kc Qs Jh Td".split())


def test_heads_up_blinds_and_order():
    t = Table("t", "t", 1, 2, 10, 200, max_seats=6, rng=random.Random(0))
    t.sit("a", "a", 0, 100)
    t.sit("b", "b", 3, 100)
    t.start_hand()
    btn = t.button
    assert t.seats[btn].bet == 1  # button posts SB heads-up
    assert t.current == btn       # and acts first preflop
    t.act(t.seats[btn].pid, "call")
    other = t.current
    assert other != btn
    t.act(t.seats[other].pid, "check")
    assert t.street == "flop"
    assert t.current == other     # BB acts first postflop


def test_min_raise_and_short_allin_does_not_reopen():
    t = Table("t", "t", 1, 2, 2, 500, max_seats=3, rng=random.Random(1))
    t.sit("a", "a", 0, 200)
    t.sit("b", "b", 1, 200)
    t.sit("c", "c", 2, 200)
    t.start_hand()                       # button 0, sb 1, bb 2, a first
    try:
        t.act("a", "raise", 3)
        assert False, "raise to 3 should be below the minimum"
    except TableError:
        pass
    t.act("a", "raise", 10)              # raise of 8
    t.seats[1].stack = 13                # b can only go to 14 total (short)
    t.act("b", "raise", 14)              # incomplete raise (+4 < 8)
    t.act("c", "call")                   # c hadn't acted since full raise → still may raise; just calls
    assert t.current == 0
    opts = t.view("a")["options"]
    assert opts["can_raise"] is False    # a already acted; short all-in doesn't reopen
    t.act("a", "call")
    assert t.street == "flop"


def test_side_pots():
    t = Table("t", "t", 1, 2, 1, 1000, max_seats=3, rng=random.Random(2))
    t.sit("a", "a", 0, 50)
    t.sit("b", "b", 1, 100)
    t.sit("c", "c", 2, 300)
    t.start_hand()
    t.act("a", "raise", 50)
    t.act("b", "raise", 100)
    t.act("c", "call")
    while t.runout_pending:
        t.runout_step()
    assert not t.hand_running
    assert sum(s.stack for s in t.seats) == 450
    assert len(t.results) == 2 or t.results[0]["amount"] == 250  # main 150 + side 100 (or merged winner)
    assert sum(r["amount"] for r in t.results) == 250
    assert t.seats[2].stack >= 200       # c's uncalled 200 came back


def _random_action(rng, t):
    s = t.seats[t.current]
    o = t.view(s.pid)["options"]
    r = rng.random()
    if r < 0.15:
        return "fold", 0
    if o["can_raise"] and r < 0.45:
        lo, hi = o["min_to"], o["max_to"]
        return "raise", rng.choice([lo, hi, rng.randint(lo, hi)])
    return ("check", 0) if o["can_check"] else ("call", 0)


def test_fuzz_chip_conservation():
    rng = random.Random(42)
    for trial in range(400):
        n = rng.randint(2, 9)
        t = Table("x", "x", 1, 2, 5, 400, max_seats=n, rng=random.Random(trial))
        bank = 0     # chips that entered the table minus chips that left
        for p in rng.sample(range(n), rng.randint(2, n)):
            amt = rng.randint(5, 400)
            t.sit(f"p{p}", f"p{p}", p, amt)
            bank += amt
        for _ in range(40):
            if not t.can_start():
                break
            t.start_hand()
            dealt = [c for s in t.seats if s and s.in_hand for c in s.hole]
            assert all(len(s.hole) == 2 for s in t.seats if s and s.in_hand), "everyone gets two cards"
            assert len(set(dealt)) == len(dealt) and not set(dealt) & set(t.deck)
            steps = 0
            while t.hand_running:
                steps += 1
                assert steps < 1000
                if t.runout_pending:
                    t.runout_step()
                    continue
                if rng.random() < 0.03:                  # someone stands mid-hand
                    s = rng.choice([s for s in t.seats if s])
                    gone = t.stand(s.pid)
                    if gone:
                        bank -= gone.stack + gone.pending_chips
                    t.auto_actions()
                    continue
                if rng.random() < 0.03:
                    t.timeout_current()
                    t.auto_actions()
                    continue
                a, amt = _random_action(rng, t)
                t.act(t.seats[t.current].pid, a, amt)
                t.auto_actions()
            for s in t.leavers:
                bank -= s.stack + s.pending_chips
            t.leavers.clear()
            on_table = sum(s.stack + s.pending_chips for s in t.seats if s)
            assert on_table == bank, (trial, on_table, bank)
            for s in t.seats:                            # bring players back sometimes
                if s and s.sitting_out and rng.random() < 0.5:
                    t.set_sitting_out(s.pid, False)
                if s and s.stack == 0 and rng.random() < 0.5:
                    t.add_chips(s.pid, 50)
                    bank += 50


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
