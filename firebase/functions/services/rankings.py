"""Bradley-Terry ratings: the ladder's ratings, recomputed on a schedule.

A bot's rating is the one at which its expected wins equal its actual wins
against the bots it has played, every game weighted the same. In 1v1 that is
the Bradley-Terry maximum likelihood fit: with strengths s = 10^(rating / 400),
bot a beats bot b with probability s_a / (s_a + s_b), Elo's usual formula. A
table of three or four seats extends it by the winner alone (Luce's choice
model): bot i wins the table with probability s_i / (sum of the table's s).
With two seats the two are the same, so one fit serves every mode. Places
below first are not used: they are ordered by VP, often tied, and open to
kingmaking. The fit does not depend on the order games were played in.

`scheduled_ratings` (main.py) calls `update_ratings` for each mode every 15
minutes; nothing is updated per game. The record it fits is kept per game by
`game_manager._update_records`: for 1v1 the `head-to-head` collection, for 3p
and 4p the `lineups` collection, one document per set of bots that has shared a
table. Only bots that played within ACTIVE_DAYS are rated, from all their
games against each other. A 1v1 game against an idle bot is left out. A
multiplayer game an active bot won stays in, as a game among the table's active
bots (by the choice model, who wins among a subset of the table does not depend
on the rest); one an idle bot won is left out.

Every bot carries a prior of PRIOR_GAMES virtual games split evenly against a
fixed anchor at 1500: a bot with no games sits at 1500, an unbeaten one gets a
finite rating, and the fit always has a solution. It is solved by Newton's
method. `se` is the standard error of a bot's standing: its rating's distance
from the field's average, each bot weighted by its games, from the inverse of
the Fisher information. (The prior pins the overall level only loosely; that
uncertainty moves every rating together and changes no one's standing.) A bot with fewer than RANK_MIN_GAMES rated games is provisional,
as is one outside the main group (the bots connected by games to the group with
the most games): two groups that never met have no common scale.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import config as cfg

PRIOR_GAMES = 2.0
RANK_MIN_GAMES = 20
RATING_BASE = 1500.0
RATING_SCALE = 400.0
_LOG10_SCALE = RATING_SCALE / math.log(10)  # rating points per unit of log strength
MAX_ITERATIONS = 100
TOLERANCE = 1e-6  # the largest gap between a bot's expected and actual wins, in games

# A table: (bot ids, games played, {bot id: wins}), every game won by one of the
# bots. 1v1 records are tables of two.
Table = tuple[tuple[str, ...], int, dict[str, int]]


def lineup_id(mode: str, bot_ids) -> str:
    """The `lineups` document for a set of bots in a mode."""
    return "__".join([mode, *sorted(bot_ids)])


def tables_from_head_to_head(docs) -> list[Table]:
    """1v1 tables from `head-to-head` documents."""
    tables = []
    for d in docs:
        a, b = d.get("bot1Id"), d.get("bot2Id")
        if not a or not b or a == b:
            continue
        wa = d.get("bot1FirstWins", 0) + d.get("bot2FirstLosses", 0)
        wb = d.get("bot2FirstWins", 0) + d.get("bot1FirstLosses", 0)
        if wa + wb > 0:
            tables.append(((a, b), wa + wb, {a: wa, b: wb}))
    return tables


def tables_from_lineups(docs) -> list[Table]:
    """3p and 4p tables from `lineups` documents."""
    tables = []
    for d in docs:
        members = tuple(d.get("botIds") or ())
        wins = {b: int(w) for b, w in (d.get("wins") or {}).items() if b in members}
        games = sum(wins.values())
        if len(set(members)) == len(members) >= 2 and games > 0:
            tables.append((members, games, wins))
    return tables


def restrict(tables: list[Table], active: set[str]) -> list[Table]:
    """The tables among active bots: idle bots leave, and so do the games they won."""
    out = []
    for members, _, wins in tables:
        kept = tuple(b for b in members if b in active)
        games = sum(wins.get(b, 0) for b in kept)
        if len(kept) >= 2 and games > 0:
            out.append((kept, games, {b: wins.get(b, 0) for b in kept}))
    return out


def main_group(tables: list[Table], ids) -> set[str]:
    """The bots connected by games to the group with the most games."""
    parent = {i: i for i in ids}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for members, _, _ in tables:
        for b in members[1:]:
            parent[root(b)] = root(members[0])
    games = defaultdict(int)
    for members, n, _ in tables:
        games[root(members[0])] += n
    if not games:
        return set()
    best = max(sorted(games), key=lambda r: games[r])
    return {i for i in ids if root(i) == best}


def fit(tables: list[Table], ids, prior_games: float = PRIOR_GAMES,
        min_games: int = RANK_MIN_GAMES) -> tuple[dict, int]:
    """Fit ratings to the tables by Newton's method.

    Returns ({id: {"rating", "se", "games", "wins", "provisional"}}, iterations).
    Every bot in `tables` must be in `ids`; prior_games must be positive.
    """
    import numpy as np  # here, so the API's cold starts don't load it

    ids = sorted(ids)
    if not ids:
        return {}, 0
    idx = {b: k for k, b in enumerate(ids)}
    n = len(ids)

    # Tables grouped by size, as arrays: members (k x seats), games (k), wins (k x seats)
    by_size = defaultdict(list)
    for members, games, wins in tables:
        by_size[len(members)].append((members, games, wins))
    groups = []
    wins_total, games_total = np.zeros(n), np.zeros(n)
    for rows in by_size.values():
        m = np.array([[idx[b] for b in members] for members, _, _ in rows])
        c = np.array([games for _, games, _ in rows], dtype=float)
        w = np.array([[wins.get(b, 0) for b in members] for members, _, wins in rows], dtype=float)
        groups.append((m, c, w))
        np.add.at(wins_total, m, w)
        np.add.at(games_total, m, c[:, None])

    half = prior_games / 2.0

    def log_likelihood(theta):
        ll = float(np.sum(half * theta - prior_games * np.logaddexp(0.0, theta)))
        for m, c, w in groups:
            t = theta[m]
            ll += float(np.sum(w * t) - np.sum(c * np.logaddexp.reduce(t, axis=1)))
        return ll

    def gradient_and_information(theta):
        p0 = 1.0 / (1.0 + np.exp(-theta))
        grad = wins_total + half - prior_games * p0
        info = np.diag(prior_games * p0 * (1.0 - p0))
        for m, c, w in groups:
            t = theta[m]
            p = np.exp(t - np.logaddexp.reduce(t, axis=1)[:, None])
            np.add.at(grad, m, -c[:, None] * p)
            for a in range(m.shape[1]):
                for b in range(m.shape[1]):
                    np.add.at(info, (m[:, a], m[:, b]),
                              c * ((p[:, a] if a == b else 0.0) - p[:, a] * p[:, b]))
        return grad, info

    theta = np.zeros(n)  # log strengths; the anchor is 0
    iterations = 0
    for iterations in range(1, MAX_ITERATIONS + 1):
        grad, info = gradient_and_information(theta)
        if np.max(np.abs(grad)) < TOLERANCE:
            break
        step = np.linalg.solve(info, grad)
        # The log-likelihood is concave; halve a step that would lower it
        ll, t = log_likelihood(theta), 1.0
        while t > 1e-8 and log_likelihood(theta + t * step) < ll:
            t /= 2.0
        theta = theta + t * step
    grad, info = gradient_and_information(theta)
    cov = np.linalg.inv(info)
    # var(theta_i - w.theta) = C_ii - 2 (C w)_i + w C w, w the games-weighted average
    weight = (games_total + prior_games) / np.sum(games_total + prior_games)
    cw = cov @ weight
    se = np.sqrt(np.maximum(np.diag(cov) - 2.0 * cw + weight @ cw, 0.0))

    connected = main_group(tables, ids)
    ratings = {}
    for b, k in idx.items():
        games = int(games_total[k])
        ratings[b] = {
            "rating": RATING_BASE + _LOG10_SCALE * float(theta[k]),
            "se": _LOG10_SCALE * float(se[k]),
            "games": games,
            "wins": int(wins_total[k]),
            "provisional": games < min_games or b not in connected,
        }
    return ratings, iterations


def order(ratings: dict) -> list[tuple[str, dict]]:
    """Leaderboard order: ranked bots by rating, then provisional ones."""
    return sorted(ratings.items(),
                  key=lambda kv: (kv[1]["provisional"], -kv[1]["rating"], kv[0]))


# ---------------------------------------------------------------------------
# The scheduled update
# ---------------------------------------------------------------------------

def _played_since(last_game_at, cutoff: datetime) -> bool:
    if not last_game_at:
        return False
    try:
        t = datetime.fromisoformat(str(last_game_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t >= cutoff


def update_ratings(db, mode: str, now: datetime | None = None) -> dict:
    """Refit `mode` and write each bot's elo{mode}, eloSe{mode}, rank{mode}
    and ratedGames{mode}; `ratings/{mode}` records the run. A bot that is no
    longer active keeps its last rating, which the matchmaker starts from if
    it returns, and loses its rank."""
    now = now or datetime.now(timezone.utc)
    started = time.monotonic()
    cutoff = now - timedelta(days=cfg.ACTIVE_DAYS)
    bots = {d.id: d.to_dict() for d in db.collection("bots").where("mode", "==", mode).stream()}
    active = {b for b, d in bots.items() if _played_since(d.get("lastGameAt"), cutoff)}

    if cfg.MODES[mode] == 2:
        tables = tables_from_head_to_head(d.to_dict() for d in db.collection("head-to-head").stream())
    else:
        tables = tables_from_lineups(
            d.to_dict() for d in db.collection("lineups").where("mode", "==", mode).stream())
    tables = restrict(tables, active)
    ratings, iterations = fit(tables, active)

    rank, writes = 0, []
    for bot_id, r in order(ratings):
        if not r["provisional"]:
            rank += 1
        writes.append((bot_id, {
            f"elo{mode}": r["rating"],
            f"eloSe{mode}": r["se"],
            f"rank{mode}": None if r["provisional"] else rank,
            f"ratedGames{mode}": r["games"],
        }))
    for bot_id, d in bots.items():
        if bot_id not in active and d.get(f"rank{mode}") is not None:
            writes.append((bot_id, {f"rank{mode}": None}))
    for i in range(0, len(writes), 400):
        batch = db.batch()
        for bot_id, fields in writes[i:i + 400]:
            batch.update(db.collection("bots").document(bot_id), fields)
        batch.commit()

    summary = {
        "mode": mode,
        "computedAt": now.isoformat(),
        "activeBots": len(active),
        "rankedBots": rank,
        "tables": len(tables),
        "games": sum(games for _, games, _ in tables),
        "iterations": iterations,
        "seconds": round(time.monotonic() - started, 3),
        "activeDays": cfg.ACTIVE_DAYS,
        "minGames": RANK_MIN_GAMES,
    }
    db.collection("ratings").document(mode).set(summary)
    return summary
