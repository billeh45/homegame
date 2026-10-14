"""Deal-fairness check for the Home Game engine.

Deals a large number of full 9-handed hands through the real engine (same code and same
random source the live site uses) and tests the results against exact poker probabilities.
Then runs the same tests on a deliberately broken shuffle to show the tests can catch one.

    python tests/fairness_check.py                  # 1,000,000 hands
    python tests/fairness_check.py --hands 200000   # quicker
    python tests/fairness_check.py --json out.json  # also save results

Needs numpy and scipy (pip install numpy scipy).
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import RANKS, SUITS, Table, best_hand  # noqa: E402

CARDS = [r + s for r in RANKS for s in SUITS]
IDX = {c: i for i, c in enumerate(CARDS)}
SEATS = 9
ALPHA = 0.001          # a test "fails" only if a result this extreme would happen < 1 in 1000 times by chance

# Exact 7-card hand frequencies (out of C(52,7) = 133,784,560), best five of seven
SEVEN_CARD = [
    ("High card", 23_294_460), ("One pair", 58_627_800), ("Two pair", 31_433_400),
    ("Three of a kind", 6_461_620), ("Straight", 6_180_020), ("Flush", 4_047_644),
    ("Full house", 3_473_184), ("Four of a kind", 224_848), ("Straight flush", 41_584),
]
TOTAL7 = 133_784_560


class NaiveShuffle:
    """The classic WRONG shuffle (swap every card with any random position). Looks random, isn't."""

    def __init__(self):
        self.r = random.SystemRandom()

    def shuffle(self, deck):
        n = len(deck)
        for i in range(n):
            j = self.r.randrange(n)
            deck[i], deck[j] = deck[j], deck[i]


def deal(n_hands, rng=None, progress=True):
    """Deal n_hands through the engine. Returns hole (n,9,2) and board (n,5) card indices."""
    t = Table("fair", "fair", 1, 2, 1, 10**9, max_seats=SEATS, rng=rng)
    for i in range(SEATS):
        t.sit(f"p{i}", f"p{i}", i, 10**6)
    hole = np.empty((n_hands, SEATS, 2), dtype=np.int8)
    board = np.empty((n_hands, 5), dtype=np.int8)
    t0 = time.time()
    for h in range(n_hands):
        for s in t.seats:
            s.stack = 10**6
        t.start_hand()
        for _ in range(3):              # flop, turn, river — exactly as the table deals them
            t._deal_street()
        for i, s in enumerate(t.seats):
            hole[h, i] = (IDX[s.hole[0]], IDX[s.hole[1]])
        board[h] = [IDX[c] for c in t.board]
        t.hand_running = False
        if progress and h and h % 100_000 == 0:
            print(f"  dealt {h:,} hands ({time.time() - t0:.0f}s)", flush=True)
    return hole, board


def chi_uniform(counts):
    """Chi-square goodness-of-fit of a count table against 'every cell equally likely' per column."""
    exp = counts.sum(axis=0, keepdims=True) / counts.shape[0]
    chi = ((counts - exp) ** 2 / exp).sum()
    df = (counts.shape[0] - 1) * counts.shape[1]
    return float(chi), int(df), float(stats.chi2.sf(chi, df))


def run_tests(hole, board, n7):
    n = hole.shape[0]
    out = []

    def add(name, plain, p, detail):
        out.append({"name": name, "plain": plain, "p": p, "pass": bool(p > ALPHA), "detail": detail})

    # 0. Integrity: no card ever dealt twice in a hand
    allc = np.concatenate([hole.reshape(n, -1), board], axis=1)
    srt = np.sort(allc, axis=1)
    dupes = int((srt[:, 1:] == srt[:, :-1]).any(axis=1).sum())
    out.append({"name": "No duplicate cards", "plain": "No card ever appeared twice in the same hand.",
                "p": None, "pass": dupes == 0, "detail": {"hands_with_duplicates": dupes}})

    # 1. Every card equally likely in every dealt slot (18 hole-card slots + 5 board slots)
    slots = allc.shape[1]
    counts = np.zeros((52, slots), dtype=np.int64)
    for k in range(slots):
        counts[:, k] = np.bincount(allc[:, k], minlength=52)
    chi, df, p = chi_uniform(counts)
    worst = float(np.abs(counts / (n / 52) - 1).max())
    add("Every card, every position",
        "Each of the 52 cards turns up equally often in every seat's hole cards and on every board card.",
        p, {"chi2": chi, "df": df, "expected_per_cell": n / 52, "max_deviation_pct": round(worst * 100, 2)})

    # 2. Starting-hand rates vs exact odds, overall and per seat
    r1, r2 = hole[..., 0] // 4, hole[..., 1] // 4
    s1, s2 = hole[..., 0] % 4, hole[..., 1] % 4
    pair = r1 == r2
    suited = s1 == s2
    hi = np.maximum(r1, r2)
    lo = np.minimum(r1, r2)
    premium = (pair & (r1 >= 10)) | ((hi == 12) & (lo == 11))   # QQ+, AK
    aces = pair & (r1 == 12)
    theory = {"pocket_pair": 78 / 1326, "suited": 312 / 1326, "pocket_aces": 6 / 1326, "premium_QQ+_AK": 34 / 1326}
    observed = {"pocket_pair": pair, "suited": suited, "pocket_aces": aces, "premium_QQ+_AK": premium}
    rows = {}
    for key, arr in observed.items():
        k = int(arr.sum())
        m = arr.size
        p = float(stats.binomtest(k, m, theory[key]).pvalue)
        rows[key] = {"observed": k / m, "expected": theory[key], "p": p}
        add(f"Starting hands: {key.replace('_', ' ')}",
            {"pocket_pair": "How often players are dealt a pocket pair (exact odds 5.88%).",
             "suited": "How often hole cards are suited (exact odds 23.53%).",
             "pocket_aces": "How often players get pocket aces (exact odds 0.45%, 1 in 221).",
             "premium_QQ+_AK": "How often players get QQ, KK, AA or AK (exact odds 2.56%)."}[key],
            p, rows[key])
    # per seat: is any seat luckier?
    prem_by_seat = premium.sum(axis=0)
    chi = float(((prem_by_seat - n * theory["premium_QQ+_AK"]) ** 2 / (n * theory["premium_QQ+_AK"])).sum())
    p = float(stats.chi2.sf(chi, SEATS))
    add("No lucky seat",
        "Every seat (1–9) gets premium hands at the same rate — nobody's chair is hot.",
        p, {"premium_rate_by_seat": [round(float(x) / n, 5) for x in prem_by_seat]})
    # relative to the button: is any position luckier? (button moves every hand)
    # deal order rotates with the button, so check by position-from-button too
    btn = np.arange(n) % SEATS            # button advances one seat per hand with all 9 seated
    pos_counts = np.zeros(SEATS, dtype=np.int64)
    for seat in range(SEATS):
        pos = (seat - btn - 1) % SEATS      # 0 = small blind ... 8 = button
        pos_counts += np.bincount(pos[premium[:, seat]], minlength=SEATS)
    chi = float(((pos_counts - n * theory["premium_QQ+_AK"]) ** 2 / (n * theory["premium_QQ+_AK"])).sum())
    p = float(stats.chi2.sf(chi, SEATS))
    add("No lucky position",
        "Blinds, button and every other position get premium hands at the same rate.",
        p, {"premium_rate_by_position_SB_to_BTN": [round(float(x) / n, 5) for x in pos_counts]})

    # 3. Final 7-card hands vs exact odds
    cats = np.zeros(9, dtype=np.int64)
    for h in range(n7):
        cards = [CARDS[c] for c in hole[h, 0]] + [CARDS[c] for c in board[h]]
        cats[best_hand(cards)[0][0]] += 1
    exp = np.array([c for _, c in SEVEN_CARD], dtype=float) / TOTAL7 * n7
    chi = float(((cats - exp) ** 2 / exp).sum())
    p = float(stats.chi2.sf(chi, 8))
    add("Final hands match the odds",
        "Across all hands, how often a player ends up with a flush, full house, quads, etc. matches exact math.",
        p, {"hands_checked": n7, "rows": [{"hand": name, "observed": int(o), "observed_pct": o / n7 * 100,
                                           "expected_pct": c / TOTAL7 * 100}
                                          for (name, c), o in zip(SEVEN_CARD, cats)]})

    # 4. Independence: one hand tells you nothing about the next
    a, b = allc[:-1, 0].astype(np.int64), allc[1:, 0].astype(np.int64)
    table = np.bincount(a * 52 + b, minlength=52 * 52).reshape(52, 52)
    chi, p, df, _ = stats.chi2_contingency(table, correction=False)
    add("Hands are independent",
        "The cards in one hand give no clue about the next hand's cards.",
        float(p), {"chi2": float(chi), "df": int(df)})
    a, b = allc[:, 0].astype(np.int64), allc[:, 18].astype(np.int64)   # your card vs the first flop card
    table = np.bincount(a * 52 + b, minlength=52 * 52).reshape(52, 52)
    np.fill_diagonal(table, 0)
    # same-card is impossible, so compare against 'any other card equally likely'
    exp = table.sum(axis=1, keepdims=True) / 51.0
    mask = ~np.eye(52, dtype=bool)
    chi = float((((table - exp) ** 2) / exp)[mask].sum())
    df = 52 * 50
    add("Board doesn't depend on your cards",
        "What you're holding has no influence on which cards come on the flop.",
        float(stats.chi2.sf(chi, df)), {"chi2": chi, "df": df})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", type=int, default=1_000_000)
    ap.add_argument("--control-hands", type=int, default=300_000)
    ap.add_argument("--seven-card", type=int, default=200_000)
    ap.add_argument("--json")
    args = ap.parse_args()

    print(f"Dealing {args.hands:,} nine-handed hands with the live site's shuffle (OS secure random)…")
    t0 = time.time()
    hole, board = deal(args.hands)
    real = run_tests(hole, board, min(args.seven_card, args.hands))
    real_secs = time.time() - t0

    print(f"\nControl: {args.control_hands:,} hands with a deliberately broken shuffle…")
    hole_b, board_b = deal(args.control_hands, rng=NaiveShuffle(), progress=False)
    broken = run_tests(hole_b, board_b, min(50_000, args.control_hands))

    for label, res in (("LIVE SITE SHUFFLE", real), ("BROKEN SHUFFLE (control)", broken)):
        print(f"\n=== {label} ===")
        for r in res:
            p = "" if r["p"] is None else f"p={r['p']:.4f}"
            print(f"  {'PASS' if r['pass'] else 'FAIL'}  {r['name']:<36} {p}")
    ok = all(r["pass"] for r in real)
    print(f"\nLive shuffle: {'ALL TESTS PASSED' if ok else 'SOME TESTS FAILED'}  ({real_secs:.0f}s)")
    print(f"Broken shuffle: caught by {sum(not r['pass'] for r in broken)} test(s)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"hands": args.hands, "control_hands": args.control_hands, "alpha": ALPHA,
                       "seconds": real_secs, "real": real, "broken": broken}, f, indent=1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
