"""Bradley-Terry ranking tests (services/rankings.py). Run from the repository root:

    python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
"""

from __future__ import annotations

import math
import unittest

from services import rankings


def record(*results):
    """(pairs, bots) from (winner, loser) games."""
    pairs = {}
    bots = {}
    for w, l in results:
        a, b = sorted((w, l))
        wa, wb = pairs.get((a, b), (0, 0))
        pairs[(a, b)] = (wa + (w == a), wb + (w == b))
        for bid in (w, l):
            rec = bots.setdefault(bid, {"name": bid, "games": 0, "wins": 0})
            rec["games"] += 1
            rec["wins"] += bid == w
    return pairs, bots


class FitTests(unittest.TestCase):
    def test_no_bots(self):
        self.assertEqual(rankings.fit({}, {}), {})

    def test_unplayed_bot_sits_at_base(self):
        r = rankings.fit({}, {"a": {"name": "a", "games": 0, "wins": 0}})
        self.assertAlmostEqual(r["a"]["rating"], rankings.RATING_BASE, places=6)
        self.assertTrue(r["a"]["provisional"])
        self.assertGreater(r["a"]["se"], 0)

    def test_even_record_is_even(self):
        r = rankings.fit(*record(*([("a", "b")] * 30 + [("b", "a")] * 30)))
        self.assertAlmostEqual(r["a"]["rating"], r["b"]["rating"], places=6)
        self.assertAlmostEqual(r["a"]["rating"], rankings.RATING_BASE, places=6)

    def test_winner_ranks_higher_and_order_of_play_is_irrelevant(self):
        games = [("a", "b")] * 70 + [("b", "a")] * 30
        forward = rankings.fit(*record(*games))
        backward = rankings.fit(*record(*reversed(games)))
        self.assertGreater(forward["a"]["rating"], forward["b"]["rating"])
        self.assertAlmostEqual(forward["a"]["rating"], backward["a"]["rating"], places=6)
        # 70:30 is a strength ratio of 7/3, i.e. 400 log10(7/3) ~= 147 points
        # apart, slightly shrunk by the prior
        gap = forward["a"]["rating"] - forward["b"]["rating"]
        self.assertGreater(gap, 120)
        self.assertLess(gap, 400 * math.log10(7 / 3))

    def test_prior_keeps_an_unbeaten_record_finite_and_wide(self):
        pairs, bots = record(*([("new", "x")] * 3 + [("old", "x")] * 300 + [("x", "old")] * 100))
        r = rankings.fit(pairs, bots)
        self.assertTrue(math.isfinite(r["new"]["rating"]))
        self.assertTrue(r["new"]["provisional"])
        self.assertFalse(r["old"]["provisional"])
        self.assertGreater(r["new"]["se"], 3 * r["old"]["se"])
        # a stronger prior pulls the short record further toward the base
        weaker = rankings.fit(pairs, bots, prior_games=10.0)
        self.assertLess(weaker["new"]["rating"], r["new"]["rating"])
        self.assertAlmostEqual(weaker["old"]["rating"] - weaker["x"]["rating"],
                               r["old"]["rating"] - r["x"]["rating"], delta=5)

    def test_standard_error_shrinks_with_games(self):
        few = rankings.fit(*record(*([("a", "b")] * 6 + [("b", "a")] * 4)))
        many = rankings.fit(*record(*([("a", "b")] * 600 + [("b", "a")] * 400)))
        self.assertGreater(few["a"]["se"], many["a"]["se"])
        self.assertLess(many["a"]["se"], 20)

    def test_disconnected_groups_still_fit(self):
        r = rankings.fit(*record(*([("a", "b")] * 10 + [("c", "d")] * 10)))
        self.assertEqual(set(r), {"a", "b", "c", "d"})
        self.assertAlmostEqual(r["a"]["rating"], r["c"]["rating"], places=6)

    def test_transitive_chain(self):
        # a beats b 3:1, b beats c 3:1: a sits above c without meeting it
        r = rankings.fit(*record(*([("a", "b")] * 30 + [("b", "a")] * 10 + [("b", "c")] * 30 + [("c", "b")] * 10)))
        self.assertGreater(r["a"]["rating"], r["b"]["rating"])
        self.assertGreater(r["b"]["rating"], r["c"]["rating"])

    def test_order_puts_provisional_last(self):
        r = rankings.fit(*record(*([("hot", "x")] * 5 + [("x", "y")] * 15 + [("y", "x")] * 10)))
        ordered = [bid for bid, _ in rankings.order(r)]
        self.assertEqual(ordered[-1], "hot")        # 5-0 but provisional
        self.assertEqual(ordered[:2], ["x", "y"])


class HeadToHeadTests(unittest.TestCase):
    def test_pairs_from_documents(self):
        docs = [
            {"bot1Id": "a", "bot1Name": "A", "bot2Id": "b", "bot2Name": "B",
             "bot1FirstWins": 4, "bot1FirstLosses": 1, "bot2FirstWins": 2, "bot2FirstLosses": 3,
             "totalGames": 10},
            {"bot1Id": "a", "bot1Name": "A", "bot2Id": "c", "bot2Name": "C",
             "bot1FirstWins": 0, "bot1FirstLosses": 0, "bot2FirstWins": 1, "bot2FirstLosses": 0,
             "totalGames": 1},
        ]
        pairs, bots = rankings.pairs_from_head_to_head(docs)
        self.assertEqual(pairs, {("a", "b"): (7, 3), ("a", "c"): (0, 1)})
        self.assertEqual(bots["a"], {"name": "A", "games": 11, "wins": 7})
        self.assertEqual(bots["b"], {"name": "B", "games": 10, "wins": 3})
        self.assertEqual(bots["c"], {"name": "C", "games": 1, "wins": 1})

    def test_empty_and_degenerate_documents_are_skipped(self):
        docs = [{"bot1Id": "a", "bot2Id": "b"}, {"bot1Id": "a", "bot2Id": "a", "bot1FirstWins": 2}]
        self.assertEqual(rankings.pairs_from_head_to_head(docs), ({}, {}))


if __name__ == "__main__":
    unittest.main()
