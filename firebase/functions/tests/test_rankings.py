"""Bradley-Terry rating tests (services/rankings.py). Needs numpy
(firebase/functions/requirements.txt); skipped without it. Run from the
repository root:

    python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
"""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    numpy = None

from services import rankings


def record(*results):
    """(tables, ids) from (winner, loser, ...) games: the first bot of each won."""
    tables, ids = {}, set()
    for seats in results:
        key = tuple(sorted(seats))
        games, wins = tables.get(key, (0, {}))
        wins[seats[0]] = wins.get(seats[0], 0) + 1
        tables[key] = (games + 1, wins)
        ids.update(seats)
    return [(k, n, w) for k, (n, w) in tables.items()], ids


def fit(*results, **kwargs):
    return rankings.fit(*record(*results), **kwargs)[0]


def expected_wins(ratings, tables, bot):
    """A bot's expected wins over its tables at the fitted ratings."""
    total = 0.0
    for members, games, _ in tables:
        if bot in members:
            s = {b: 10 ** (ratings[b]["rating"] / 400) for b in members}
            total += games * s[bot] / sum(s.values())
    return total


@unittest.skipIf(numpy is None, "numpy is not installed")
class FitTests(unittest.TestCase):
    def test_no_bots(self):
        self.assertEqual(rankings.fit([], []), ({}, 0))

    def test_unplayed_bots_sit_at_base(self):
        r, _ = rankings.fit([], ["a", "b"])
        self.assertAlmostEqual(r["a"]["rating"], rankings.RATING_BASE, places=6)
        self.assertTrue(r["a"]["provisional"])
        self.assertGreater(r["a"]["se"], 100)

    def test_even_record_is_even(self):
        r = fit(*([("a", "b")] * 30 + [("b", "a")] * 30))
        self.assertAlmostEqual(r["a"]["rating"], r["b"]["rating"], places=6)
        self.assertAlmostEqual(r["a"]["rating"], rankings.RATING_BASE, places=6)

    def test_winner_ranks_higher_and_order_of_play_is_irrelevant(self):
        games = [("a", "b")] * 70 + [("b", "a")] * 30
        forward, backward = fit(*games), fit(*reversed(games))
        self.assertGreater(forward["a"]["rating"], forward["b"]["rating"])
        self.assertAlmostEqual(forward["a"]["rating"], backward["a"]["rating"], places=6)
        # 70:30 is a strength ratio of 7/3, i.e. 400 log10(7/3) ~= 147 points
        # apart, slightly shrunk by the prior
        gap = forward["a"]["rating"] - forward["b"]["rating"]
        self.assertGreater(gap, 120)
        self.assertLess(gap, 400 * math.log10(7 / 3))

    def test_expected_wins_equal_actual_wins(self):
        # The defining property, up to the prior's half win against the anchor
        tables, ids = record(*([("a", "b")] * 40 + [("b", "a")] * 20 + [("b", "c")] * 25
                               + [("c", "b")] * 15 + [("a", "c")] * 9 + [("c", "a")] * 1))
        r, _ = rankings.fit(tables, ids, prior_games=1e-6)
        for bot in ids:
            actual = sum(w.get(bot, 0) for _, _, w in tables)
            self.assertAlmostEqual(expected_wins(r, tables, bot), actual, places=4)

    def test_prior_keeps_an_unbeaten_record_finite_and_wide(self):
        games = [("new", "x")] * 3 + [("old", "x")] * 300 + [("x", "old")] * 100
        r = fit(*games)
        self.assertTrue(math.isfinite(r["new"]["rating"]))
        self.assertTrue(r["new"]["provisional"])
        self.assertFalse(r["old"]["provisional"])
        self.assertGreater(r["new"]["se"], 3 * r["old"]["se"])
        # a stronger prior pulls the short record further toward the base
        weaker = fit(*games, prior_games=10.0)
        self.assertLess(weaker["new"]["rating"], r["new"]["rating"])
        self.assertAlmostEqual(weaker["old"]["rating"] - weaker["x"]["rating"],
                               r["old"]["rating"] - r["x"]["rating"], delta=5)

    def test_a_lopsided_record_converges(self):
        r = fit(*([("a", "b")] * 500))
        self.assertTrue(math.isfinite(r["a"]["rating"]))
        self.assertGreater(r["a"]["rating"] - r["b"]["rating"], 600)

    def test_standard_error_shrinks_with_games(self):
        few = fit(*([("a", "b")] * 6 + [("b", "a")] * 4))
        many = fit(*([("a", "b")] * 600 + [("b", "a")] * 400))
        self.assertGreater(few["a"]["se"], many["a"]["se"])
        self.assertLess(many["a"]["se"], 20)

    def test_a_group_that_never_met_the_main_one_is_provisional(self):
        r = fit(*([("a", "b")] * 30 + [("b", "a")] * 30 + [("c", "d")] * 15 + [("d", "c")] * 10))
        self.assertFalse(r["a"]["provisional"])
        self.assertFalse(r["b"]["provisional"])
        self.assertTrue(r["c"]["provisional"])
        self.assertTrue(r["d"]["provisional"])

    def test_transitive_chain(self):
        # a beats b 3:1, b beats c 3:1: a sits above c without meeting it
        r = fit(*([("a", "b")] * 30 + [("b", "a")] * 10 + [("b", "c")] * 30 + [("c", "b")] * 10))
        self.assertGreater(r["a"]["rating"], r["b"]["rating"])
        self.assertGreater(r["b"]["rating"], r["c"]["rating"])

    def test_order_puts_provisional_last(self):
        r = fit(*([("hot", "x")] * 5 + [("x", "y")] * 15 + [("y", "x")] * 10))
        ordered = [bid for bid, _ in rankings.order(r)]
        self.assertEqual(ordered[-1], "hot")        # 5-0 but provisional
        self.assertEqual(ordered[:2], ["x", "y"])


@unittest.skipIf(numpy is None, "numpy is not installed")
class MultiplayerFitTests(unittest.TestCase):
    def test_even_tables_are_even(self):
        r = fit(*([("a", "b", "c")] * 10 + [("b", "c", "a")] * 10 + [("c", "a", "b")] * 10))
        for bot in "abc":
            self.assertAlmostEqual(r[bot]["rating"], rankings.RATING_BASE, places=6)

    def test_expected_wins_equal_actual_wins(self):
        tables, ids = record(*([("a", "b", "c", "d")] * 12 + [("b", "a", "c", "d")] * 6
                               + [("c", "a", "b", "e")] * 5 + [("e", "a", "b", "c")] * 2
                               + [("d", "a", "c", "e")] * 4 + [("a", "c", "d", "e")] * 3))
        r, _ = rankings.fit(tables, ids, prior_games=1e-6)
        for bot in ids:
            actual = sum(w.get(bot, 0) for _, _, w in tables)
            self.assertAlmostEqual(expected_wins(r, tables, bot), actual, places=4)

    def test_winning_half_of_four_seat_tables_is_three_times_the_rest(self):
        # a wins half, the others a sixth each: a's strength is 3 times theirs
        r = fit(*([("a", "b", "c", "d")] * 30 + [("b", "a", "c", "d")] * 10
                  + [("c", "a", "b", "d")] * 10 + [("d", "a", "b", "c")] * 10), prior_games=1e-6)
        self.assertAlmostEqual(r["a"]["rating"] - r["b"]["rating"], 400 * math.log10(3), places=3)

    def test_two_seat_tables_match_one_on_one(self):
        games = [("a", "b")] * 14 + [("b", "a")] * 6 + [("b", "c")] * 11 + [("c", "b")] * 9
        one_on_one = fit(*games)
        tables = rankings.tables_from_lineups(
            {"botIds": list(m), "games": n, "wins": w} for m, n, w in record(*games)[0])
        as_lineups, _ = rankings.fit(tables, {"a", "b", "c"})
        for bot in "abc":
            self.assertAlmostEqual(as_lineups[bot]["rating"], one_on_one[bot]["rating"], places=6)


class RecordTests(unittest.TestCase):
    def test_tables_from_head_to_head(self):
        docs = [
            {"bot1Id": "a", "bot1Name": "A", "bot2Id": "b", "bot2Name": "B",
             "bot1FirstWins": 4, "bot1FirstLosses": 1, "bot2FirstWins": 2, "bot2FirstLosses": 3,
             "totalGames": 10},
            {"bot1Id": "a", "bot2Id": "c", "bot2FirstWins": 1, "totalGames": 1},
            {"bot1Id": "a", "bot2Id": "b"},
            {"bot1Id": "a", "bot2Id": "a", "bot1FirstWins": 2},
        ]
        self.assertEqual(rankings.tables_from_head_to_head(docs),
                         [(("a", "b"), 10, {"a": 7, "b": 3}), (("a", "c"), 1, {"a": 0, "c": 1})])

    def test_tables_from_lineups(self):
        docs = [
            {"mode": "4p", "botIds": ["a", "b", "c", "d"], "games": 5, "wins": {"a": 3, "c": 2}},
            {"mode": "3p", "botIds": ["a", "b", "c"], "games": 0, "wins": {}},
            {"mode": "3p", "botIds": ["a", "a", "b"], "games": 1, "wins": {"a": 1}},
        ]
        self.assertEqual(rankings.tables_from_lineups(docs),
                         [(("a", "b", "c", "d"), 5, {"a": 3, "c": 2})])

    def test_lineup_id_ignores_seat_order(self):
        self.assertEqual(rankings.lineup_id("3p", ["c", "a", "b"]), "3p__a__b__c")

    def test_restrict_drops_idle_bots_and_the_games_they_won(self):
        tables = [(("a", "b"), 5, {"a": 3, "b": 2}),
                  (("a", "x"), 4, {"a": 1, "x": 3}),
                  (("a", "b", "c", "x"), 10, {"a": 4, "b": 1, "x": 5}),
                  (("a", "x", "y"), 3, {"a": 1, "x": 1, "y": 1})]
        self.assertEqual(rankings.restrict(tables, {"a", "b", "c"}), [
            (("a", "b"), 5, {"a": 3, "b": 2}),
            (("a", "b", "c"), 5, {"a": 4, "b": 1, "c": 0}),
        ])


# ---------------------------------------------------------------------------
# update_ratings against an in-memory Firestore
# ---------------------------------------------------------------------------

class _Doc:
    def __init__(self, doc_id, data):
        self.id, self._data = doc_id, data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data)


class _Ref:
    def __init__(self, store, doc_id):
        self.store, self.id = store, doc_id

    def set(self, data):
        self.store[self.id] = dict(data)


class _Collection:
    def __init__(self, store, filters=()):
        self.store, self.filters = store, filters

    def where(self, field, op, value):
        return _Collection(self.store, self.filters + ((field, value),))

    def stream(self):
        return iter([_Doc(k, v) for k, v in self.store.items()
                     if all(v.get(f) == x for f, x in self.filters)])

    def document(self, doc_id):
        return _Ref(self.store, doc_id)


class _Batch:
    def __init__(self):
        self.ops = []

    def update(self, ref, fields):
        self.ops.append((ref, fields))

    def commit(self):
        for ref, fields in self.ops:
            ref.store[ref.id].update(fields)


class _Db:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return _Collection(self.data.setdefault(name, {}))

    def batch(self):
        return _Batch()


@unittest.skipIf(numpy is None, "numpy is not installed")
class UpdateRatingsTests(unittest.TestCase):
    NOW = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    def bot(self, mode, days_ago, **extra):
        last = (self.NOW - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
        return {"mode": mode, "name": "?", f"elo{mode}": 1500.0, "lastGameAt": last, **extra}

    def h2h(self, a, b, wins_a, wins_b):
        return {"bot1Id": a, "bot2Id": b, "bot1FirstWins": wins_a, "bot2FirstWins": wins_b,
                "totalGames": wins_a + wins_b}

    def test_rates_active_bots_from_their_games_with_each_other(self):
        db = _Db({
            "bots": {"a": self.bot("1v1", 1), "b": self.bot("1v1", 2),
                     "c": self.bot("1v1", 3), "old": self.bot("1v1", 45, rank1v1=4, elo1v1=1610.0),
                     "new": self.bot("1v1", None)},
            "head-to-head": {
                "a__b": self.h2h("a", "b", 30, 10),     # older games among active bots count
                "b__c": self.h2h("b", "c", 30, 10),
                "a__old": self.h2h("a", "old", 0, 50),  # an idle bot's games do not
            },
        })
        summary = rankings.update_ratings(db, "1v1", now=self.NOW)
        bots = db.data["bots"]
        self.assertEqual([bots[b]["rank1v1"] for b in "abc"], [1, 2, 3])
        self.assertGreater(bots["a"]["elo1v1"], bots["b"]["elo1v1"])
        self.assertEqual(bots["a"]["ratedGames1v1"], 40)
        self.assertGreater(bots["a"]["eloSe1v1"], 0)
        # the idle bot loses its rank and keeps its last rating
        self.assertIsNone(bots["old"]["rank1v1"])
        self.assertEqual(bots["old"]["elo1v1"], 1610.0)
        self.assertNotIn("rank1v1", bots["new"])
        self.assertEqual((summary["activeBots"], summary["rankedBots"], summary["games"]), (3, 3, 80))
        self.assertEqual(db.data["ratings"]["1v1"]["computedAt"], self.NOW.isoformat())

    def test_multiplayer_modes_read_their_lineups(self):
        db = _Db({
            "bots": {b: self.bot("3p", 1) for b in "abc"},
            "lineups": {
                "3p__a__b__c": {"mode": "3p", "botIds": ["a", "b", "c"], "games": 30,
                                "wins": {"a": 20, "b": 6, "c": 4}},
                "4p__a__b__c__d": {"mode": "4p", "botIds": ["a", "b", "c", "d"], "games": 9,
                                   "wins": {"d": 9}},
            },
        })
        summary = rankings.update_ratings(db, "3p", now=self.NOW)
        bots = db.data["bots"]
        self.assertGreater(bots["a"]["elo3p"], bots["b"]["elo3p"])
        self.assertGreater(bots["b"]["elo3p"], bots["c"]["elo3p"])
        self.assertEqual(bots["a"]["ratedGames3p"], 30)
        self.assertEqual(summary["games"], 30)


if __name__ == "__main__":
    unittest.main()
