"""Practice-table bots. Each decision estimates hand equity by simulation, then applies a style."""
import random

from engine import RANKS, SUITS, best_hand

STYLES = {
    # raise_eq: how strong (vs. a fair share of the pot) a hand must be to bet/raise
    # call_margin: equity cushion over pot odds needed to call (negative = calls light)
    # bluff: chance of betting/raising without a strong hand; size: bet size as a fraction of pot
    "rock":   {"name": "Rock",   "raise_eq": 0.70, "call_margin": 0.06,  "bluff": 0.03, "size": 0.6},
    "shark":  {"name": "Shark",  "raise_eq": 0.60, "call_margin": 0.00,  "bluff": 0.12, "size": 0.7},
    "maniac": {"name": "Maniac", "raise_eq": 0.47, "call_margin": -0.08, "bluff": 0.32, "size": 1.0},
}


def equity(hole, board, n_opp, sims=220, rng=random):
    """Fraction of pots won (ties count half) against n_opp random hands."""
    used = set(hole) | set(board)
    deck = [r + s for r in RANKS for s in SUITS if r + s not in used]
    need = 5 - len(board)
    won = 0.0
    for _ in range(sims):
        draw = rng.sample(deck, need + 2 * n_opp)
        full = board + draw[:need]
        mine = best_hand(hole + full)[0]
        best_opp = max(best_hand(draw[need + 2 * k:need + 2 * k + 2] + full)[0] for k in range(n_opp))
        if mine > best_opp:
            won += 1
        elif mine == best_opp:
            won += 0.5
    return won / sims


def decide(o, hole, board, pot, n_opp, style, rng=random):
    """o is the engine's options dict for the bot. Returns (action, amount)."""
    st = STYLES.get(style, STYLES["shark"])
    n_opp = max(1, n_opp)
    e = equity(hole, board, n_opp, rng=rng)
    rel = e * (n_opp + 1)              # 1.0 = exactly a fair share; heads-up rel = 2 * equity
    owe = o["current_bet"] - o["my_bet"]

    def sized(frac):
        if o["is_bet"]:
            target = int(frac * pot)
        else:
            target = o["current_bet"] + int(frac * (pot + owe))
        target = max(o["min_to"], min(o["max_to"], target))
        if target > 0.7 * o["max_to"]:   # committing most of the stack anyway: just shove
            target = o["max_to"]
        return target

    strong = rel > 2 * st["raise_eq"]
    bluffing = rng.random() < st["bluff"]
    raise_word = "bet" if o["is_bet"] else "raise"

    if o["can_check"]:
        if o["can_raise"] and (strong or bluffing):
            return raise_word, sized(st["size"] * rng.uniform(0.7, 1.2))
        return "check", 0

    pot_odds = owe / (pot + owe)
    very_strong = rel > 2 * (st["raise_eq"] + 0.12)
    if o["can_raise"] and (very_strong or (bluffing and rng.random() < 0.4)):
        return "raise", sized(st["size"] * rng.uniform(0.9, 1.4))
    if e >= pot_odds + st["call_margin"]:
        return "call", 0
    return "fold", 0
