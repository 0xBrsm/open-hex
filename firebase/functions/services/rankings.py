"""Elo over every finished game: the leaderboard's order.

The bot documents carry an Elo updated one game at a time with a fixed step
(`services/elo.py`, K=32, no taper or decay), which the matchmaker pairs by.
As a ranking it is a bot's recent run of surprises rather than its record:
points are paid against the expectation at the time of the game, so a bot
that climbs on a weak opponent pays the points back to whoever it meets
next, even while winning (hexn-4d.0 won 60% of 70 games against
catanrl-1v1-search and lost 60 points doing it, because it arrived rated
250 points higher).

The leaderboard instead solves the same Elo equations over the whole record,
every game weighted the same: each bot's rating is the one at which its
expected wins, by Elo's 1 / (1 + 10^((r_b - r_a) / 400)), equal its actual
wins against every opponent it has met. That is the Bradley-Terry maximum
likelihood fit, found by minorization-maximization (Hunter 2004), and it
does not depend on the order the games were played in. The record is the
`head-to-head` collection the server already keeps for every pair of 1v1
bots that has met (`game_manager._update_head_to_head`); the fit is computed
on request by the leaderboard endpoint. Nothing new is stored.

Every bot carries a prior of PRIOR_GAMES virtual games split evenly against
a fixed anchor at 1500: a bot with no games sits at 1500, an unbeaten one
gets a finite rating, and the comparison graph is always connected, so two
groups of bots that never met still rate on one scale. `se` is the rating's
standard error from the diagonal of the Fisher information; a short record
shows as a wide one. Bots with fewer than RANK_MIN_GAMES games are
provisional and listed after the ranked ones.
"""

from __future__ import annotations

import math
from collections import defaultdict

PRIOR_GAMES = 2.0
RANK_MIN_GAMES = 20
RATING_BASE = 1500.0
RATING_SCALE = 400.0
_LOG10_SCALE = RATING_SCALE / math.log(10)  # rating points per unit of log strength
MAX_ITERATIONS = 1000
TOLERANCE = 1e-9


def pairs_from_head_to_head(docs: list[dict]) -> tuple[dict, dict]:
    """The pairwise record and each bot's totals from `head-to-head` documents.

    Returns (pairs, bots): pairs[(id_a, id_b)] = (wins by a, wins by b);
    bots[id] = {"name", "games", "wins"}.
    """
    pairs = {}
    bots = defaultdict(lambda: {"name": "", "games": 0, "wins": 0})
    for d in docs:
        a, b = d.get("bot1Id"), d.get("bot2Id")
        if not a or not b or a == b:
            continue
        wa = d.get("bot1FirstWins", 0) + d.get("bot2FirstLosses", 0)
        wb = d.get("bot2FirstWins", 0) + d.get("bot1FirstLosses", 0)
        if wa + wb <= 0:
            continue
        pairs[(a, b)] = (wa, wb)
        for bid, name, wins in ((a, d.get("bot1Name"), wa), (b, d.get("bot2Name"), wb)):
            rec = bots[bid]
            rec["name"] = name or rec["name"]
            rec["games"] += wa + wb
            rec["wins"] += wins
    return pairs, dict(bots)


def fit(pairs: dict, bots: dict, prior_games: float = PRIOR_GAMES,
        min_games: int = RANK_MIN_GAMES) -> dict:
    """Fit Bradley-Terry strengths by minorization-maximization (Hunter 2004).

    Returns {id: {"rating", "se", "games", "wins", "provisional"}}. The
    anchor (strength 1) is a virtual opponent every bot has played
    `prior_games` times, winning half.
    """
    ids = sorted(bots)
    if not ids:
        return {}
    wins = defaultdict(float)        # wins against real opponents
    met = defaultdict(dict)          # met[i][j] = games between i and j
    for (a, b), (wa, wb) in pairs.items():
        if a not in bots or b not in bots or wa + wb <= 0:
            continue
        wins[a] += wa
        wins[b] += wb
        met[a][b] = met[a].get(b, 0.0) + wa + wb
        met[b][a] = met[b].get(a, 0.0) + wa + wb

    strength = {i: 1.0 for i in ids}
    for _ in range(MAX_ITERATIONS):
        delta = 0.0
        for i in ids:
            numer = wins[i] + prior_games / 2.0
            denom = prior_games / (strength[i] + 1.0)
            for j, n in met[i].items():
                denom += n / (strength[i] + strength[j])
            new = numer / denom
            delta = max(delta, abs(math.log(new) - math.log(strength[i])))
            strength[i] = new
        if delta < TOLERANCE:
            break

    ratings = {}
    for i in ids:
        s = strength[i]
        # Fisher information of log strength, diagonal: sum of n p (1 - p)
        info = prior_games * (s / (s + 1.0)) * (1.0 / (s + 1.0))
        for j, n in met[i].items():
            p = s / (s + strength[j])
            info += n * p * (1.0 - p)
        games = int(bots[i].get("games", 0))
        ratings[i] = {
            "rating": RATING_BASE + _LOG10_SCALE * math.log(s),
            "se": _LOG10_SCALE / math.sqrt(info) if info > 0 else None,
            "games": games,
            "wins": int(bots[i].get("wins", 0)),
            "provisional": games < min_games,
        }
    return ratings


def order(ratings: dict) -> list[tuple[str, dict]]:
    """Leaderboard order: ranked bots by rating, then provisional ones."""
    return sorted(ratings.items(),
                  key=lambda kv: (kv[1]["provisional"], -kv[1]["rating"], kv[0]))
