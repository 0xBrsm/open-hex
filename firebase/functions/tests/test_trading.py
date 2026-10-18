"""Player-to-player trading, to colonist.io's rules.

Run from the repository root:
    python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
"""

from __future__ import annotations

import json
import pathlib
import random
import unittest

from openhex import (
    Action, Edge, Event, GameConfig, GameState, OpenHexEngine, PendingAction, PlayerColor,
    generate_standard_board, from_json, to_json, game_log_to_json, game_log_from_json,
)
from openhex.board import VertexBuilding
from openhex.engine import PLAYER_TRADE_ACTIONS
from openhex.json_codec import _action_from_json
from openhex.state import DiceResult
from openhex.types import Resource
from services.hidden_info import filter_event_for_player, filter_state_for_player

RED, BLUE, WHITE, ORANGE = PlayerColor.RED, PlayerColor.BLUE, PlayerColor.WHITE, PlayerColor.ORANGE
WOOD, BRICK, SHEEP, WHEAT, ORE = (Resource.WOOD, Resource.BRICK, Resource.SHEEP,
                                  Resource.WHEAT, Resource.ORE)


def main_phase(colors=(RED, BLUE, WHITE, ORANGE), hands=None) -> GameState:
    """A game past setup, the first seat to move, dice rolled, nothing pending."""
    config = GameConfig(num_players=len(colors))
    state = GameState.new_game(config, generate_standard_board(7), list(colors))
    state.is_setup_phase = False
    state.turn_number = 1
    state.current_player = colors[0]
    state.dice = DiceResult(3, 4)
    state.pending_action = None
    default = {WOOD: 3, BRICK: 3, SHEEP: 3, WHEAT: 3, ORE: 3}
    for p in state.players:
        p.hand = dict((hands or {}).get(p.color, default))
        for r, n in p.hand.items():
            state.bank[r] -= n
    return state


class TradeCase(unittest.TestCase):
    def setUp(self):
        self.engine = OpenHexEngine(rng=random.Random(1))

    def act(self, state, player, type_, **fields):
        """Run an action that must succeed; return (state, event dicts)."""
        result = self.engine.execute_action(state, Action(type=type_, player=player, **fields))
        self.assertTrue(result.success, f"{type_} by {player.value} failed: {result.error}")
        self.assertIsNone(result.state.pending_action, "an offer must never block the game")
        return result.state, [to_json(e) for e in result.events]

    def refused(self, state, player, type_, **fields) -> str:
        result = self.engine.execute_action(state, Action(type=type_, player=player, **fields))
        self.assertFalse(result.success, f"{type_} by {player.value} should be refused")
        return result.error

    def offer(self, state, player=RED, offering=None, requesting=None, **fields):
        state, events = self.act(state, player, "proposeTrade",
                                 offering=offering or {WOOD: 1},
                                 requesting=requesting or {BRICK: 1}, **fields)
        return state, events[-1]["tradeId"]


class ProposeAcceptConfirm(TradeCase):
    def test_offer_executes_with_the_partner_the_proposer_picks(self):
        s = main_phase()
        s, events = self.act(s, RED, "proposeTrade", offering={WOOD: 2}, requesting={ORE: 1})
        self.assertEqual(events, [{"type": "tradeProposed", "player": "red", "tradeId": 1,
                                   "offering": {"wood": 2}, "requesting": {"ore": 1}}])
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=1)
        s, _ = self.act(s, WHITE, "acceptTrade", trade_id=1)
        self.assertEqual(s.trades[0].accepting, [BLUE, WHITE])

        s, events = self.act(s, RED, "confirmTrade", trade_id=1, partner=WHITE)
        self.assertEqual(events, [{"type": "tradeExecuted", "player": "red", "partner": "white",
                                   "tradeId": 1, "offering": {"wood": 2},
                                   "requesting": {"ore": 1}}])
        self.assertEqual(s.get_player(RED).hand[WOOD], 1)
        self.assertEqual(s.get_player(RED).hand[ORE], 4)
        self.assertEqual(s.get_player(WHITE).hand[WOOD], 5)
        self.assertEqual(s.get_player(WHITE).hand[ORE], 2)
        self.assertEqual(s.get_player(BLUE).hand, main_phase().get_player(BLUE).hand)
        self.assertEqual(s.trades, [])
        self.assertEqual(s.current_player, RED)

    def test_confirm_needs_an_accepting_partner(self):
        s, tid = self.offer(main_phase())
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=tid, partner=BLUE),
                         "Partner has not accepted")
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=tid), "Missing partner")
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=tid)
        self.refused(s, BLUE, "confirmTrade", trade_id=tid, partner=BLUE)

    def test_only_the_current_player_makes_offers(self):
        s = main_phase()
        err = self.refused(s, BLUE, "proposeTrade", offering={WOOD: 1}, requesting={BRICK: 1})
        self.assertIn("Only the current player makes offers", err)

    def test_offers_only_after_the_roll_with_nothing_pending(self):
        s = main_phase()
        s.dice = None
        self.assertEqual(self.refused(s, RED, "proposeTrade", offering={WOOD: 1},
                                      requesting={BRICK: 1}), "Must roll dice first")
        s = main_phase()
        s.pending_action = PendingAction(type="moveRobber")
        self.assertIn("pending", self.refused(s, RED, "proposeTrade", offering={WOOD: 1},
                                              requesting={BRICK: 1}))
        s = main_phase()
        s.is_setup_phase = True
        self.refused(s, RED, "proposeTrade", offering={WOOD: 1}, requesting={BRICK: 1})

    def test_no_limit_on_offers_per_turn(self):
        s = main_phase()
        for n in range(1, 4):
            s, _ = self.offer(s, offering={WOOD: n})
        self.assertEqual(len(s.trades), 3)
        self.assertEqual(s.trades_proposed_this_turn, 3)


class CounterOffers(TradeCase):
    def test_counter_is_addressed_to_the_mover_and_executes_as_written(self):
        s, tid = self.offer(main_phase(), offering={WOOD: 1}, requesting={BRICK: 1})
        s, events = self.act(s, BLUE, "proposeTrade", offering={BRICK: 1},
                             requesting={WOOD: 2}, counter_to=tid)
        self.assertEqual(events, [{"type": "tradeProposed", "player": "blue", "tradeId": 2,
                                   "offering": {"brick": 1}, "requesting": {"wood": 2},
                                   "counterTo": 1}])
        counter = s.trades[1]
        self.assertEqual((counter.proposer, counter.counter_to), (BLUE, tid))

        # The mover takes it with confirmTrade; the partner is the counter's creator.
        s, events = self.act(s, RED, "confirmTrade", trade_id=2)
        self.assertEqual(events, [{"type": "tradeExecuted", "player": "blue", "partner": "red",
                                   "tradeId": 2, "offering": {"brick": 1},
                                   "requesting": {"wood": 2}}])
        self.assertEqual((s.get_player(BLUE).hand[BRICK], s.get_player(BLUE).hand[WOOD]), (2, 5))
        self.assertEqual((s.get_player(RED).hand[BRICK], s.get_player(RED).hand[WOOD]), (4, 1))
        # The original offer still stands; only the counter executed.
        self.assertEqual([t.trade_id for t in s.trades], [tid])

    def test_counter_rules(self):
        s, tid = self.offer(main_phase())
        self.assertIn("not counter-offers", self.refused(
            s, RED, "proposeTrade", offering={WOOD: 1}, requesting={SHEEP: 1}, counter_to=tid))
        self.assertEqual(self.refused(s, BLUE, "proposeTrade", offering={BRICK: 1},
                                      requesting={WOOD: 1}, counter_to=99),
                         "Offer to counter not found")
        s, cid = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)
        self.assertIn("can be countered", self.refused(
            s, WHITE, "proposeTrade", offering={ORE: 1}, requesting={WOOD: 1}, counter_to=cid))
        # Nobody but the mover answers a counter, and the mover takes it by confirming.
        self.assertIn("current player only", self.refused(s, WHITE, "acceptTrade", trade_id=cid))
        self.assertIn("current player only", self.refused(s, WHITE, "rejectTrade", trade_id=cid))
        self.assertEqual(self.refused(s, RED, "acceptTrade", trade_id=cid),
                         "Take a counter-offer with confirmTrade")
        self.assertIn("seat that made it",
                      self.refused(s, RED, "confirmTrade", trade_id=cid, partner=WHITE))
        self.refused(s, BLUE, "confirmTrade", trade_id=cid)
        # Partner may be named explicitly.
        self.act(s, RED, "confirmTrade", trade_id=cid, partner=BLUE)

    def test_mover_rejecting_a_counter_closes_it(self):
        s, tid = self.offer(main_phase())
        s, cid = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)
        s, events = self.act(s, RED, "rejectTrade", trade_id=cid)
        self.assertEqual(events, [
            {"type": "tradeRejected", "player": "red", "tradeId": cid},
            {"type": "tradeCancelled", "player": "blue", "tradeId": cid, "reason": "allRejected"},
        ])
        self.assertEqual([t.trade_id for t in s.trades], [tid])

    def test_creator_cancels_its_counter(self):
        s, tid = self.offer(main_phase())
        s, cid = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)
        self.assertEqual(self.refused(s, RED, "cancelTrade", trade_id=cid),
                         "Only the offer's creator can cancel it")
        s, events = self.act(s, BLUE, "cancelTrade", trade_id=cid)
        self.assertEqual(events, [{"type": "tradeCancelled", "player": "blue", "tradeId": cid,
                                   "reason": "cancelled"}])

    def test_counter_needs_the_creators_cards(self):
        s, tid = self.offer(main_phase(hands={BLUE: {SHEEP: 1}}))
        self.assertEqual(self.refused(s, BLUE, "proposeTrade", offering={BRICK: 1},
                                      requesting={WOOD: 1}, counter_to=tid),
                         "Insufficient resources to offer")

    def test_mover_answers_a_counter_with_a_new_offer(self):
        s, tid = self.offer(main_phase())
        s, cid = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)
        s, new = self.offer(s, RED, offering={WOOD: 1, SHEEP: 1}, requesting={BRICK: 1})
        self.assertIsNone(s.trades[-1].counter_to)
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=new)
        s, events = self.act(s, RED, "confirmTrade", trade_id=new, partner=BLUE)
        # Executes as written: the mover gives wood and sheep, receives brick.
        self.assertEqual(events[0]["player"], "red")
        self.assertEqual(s.get_player(RED).hand, {WOOD: 2, BRICK: 4, SHEEP: 2, WHEAT: 3, ORE: 3})


class Answers(TradeCase):
    def test_any_answer_can_change_while_the_offer_stands(self):
        s, tid = self.offer(main_phase())
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=tid)
        self.assertEqual((s.trades[0].accepting, s.trades[0].rejected), ([BLUE], []))
        self.assertEqual(self.refused(s, BLUE, "acceptTrade", trade_id=tid), "Already accepted")

        s, _ = self.act(s, BLUE, "rejectTrade", trade_id=tid)
        self.assertEqual((s.trades[0].accepting, s.trades[0].rejected), ([], [BLUE]))
        self.assertEqual(self.refused(s, BLUE, "rejectTrade", trade_id=tid), "Already rejected")
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=tid, partner=BLUE),
                         "Partner has not accepted")

        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=tid)
        self.assertEqual((s.trades[0].accepting, s.trades[0].rejected), ([BLUE], []))

        s, events = self.act(s, BLUE, "withdrawTradeAnswer", trade_id=tid)
        self.assertEqual(events, [{"type": "tradeAnswerWithdrawn", "player": "blue",
                                   "tradeId": tid}])
        self.assertEqual((s.trades[0].accepting, s.trades[0].rejected), ([], []))
        self.assertEqual(self.refused(s, BLUE, "withdrawTradeAnswer", trade_id=tid),
                         "No answer to withdraw")
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=tid, partner=BLUE),
                         "Partner has not accepted")

    def test_cannot_answer_own_offer(self):
        s, tid = self.offer(main_phase())
        for type_ in ("acceptTrade", "rejectTrade", "withdrawTradeAnswer"):
            self.assertEqual(self.refused(s, RED, type_, trade_id=tid),
                             "Cannot answer your own offer")

    def test_closes_when_the_last_seat_rejects(self):
        s, tid = self.offer(main_phase())
        s, _ = self.act(s, BLUE, "rejectTrade", trade_id=tid)
        s, _ = self.act(s, WHITE, "rejectTrade", trade_id=tid)
        self.assertEqual(len(s.trades), 1)
        s, events = self.act(s, ORANGE, "rejectTrade", trade_id=tid)
        self.assertEqual(events, [
            {"type": "tradeRejected", "player": "orange", "tradeId": tid},
            {"type": "tradeCancelled", "player": "red", "tradeId": tid, "reason": "allRejected"},
        ])
        self.assertEqual(s.trades, [])
        self.assertEqual(self.refused(s, ORANGE, "withdrawTradeAnswer", trade_id=tid),
                         "Trade not found")

    def test_stands_while_any_seat_has_not_rejected(self):
        s, tid = self.offer(main_phase())
        s, _ = self.act(s, BLUE, "rejectTrade", trade_id=tid)
        s, _ = self.act(s, WHITE, "acceptTrade", trade_id=tid)
        s, _ = self.act(s, ORANGE, "rejectTrade", trade_id=tid)
        self.assertEqual(len(s.trades), 1)
        s, events = self.act(s, WHITE, "rejectTrade", trade_id=tid)
        self.assertEqual(events[-1]["reason"], "allRejected")
        self.assertEqual(s.trades, [])

    def test_two_player_game_closes_on_the_one_rejection(self):
        s, tid = self.offer(main_phase(colors=(RED, BLUE)))
        s, events = self.act(s, BLUE, "rejectTrade", trade_id=tid)
        self.assertEqual(events[-1], {"type": "tradeCancelled", "player": "red",
                                      "tradeId": tid, "reason": "allRejected"})

    def test_cancel(self):
        s, tid = self.offer(main_phase())
        self.assertEqual(self.refused(s, BLUE, "cancelTrade", trade_id=tid),
                         "Only the offer's creator can cancel it")
        s, events = self.act(s, RED, "cancelTrade", trade_id=tid)
        self.assertEqual(events, [{"type": "tradeCancelled", "player": "red", "tradeId": tid,
                                   "reason": "cancelled"}])
        self.assertEqual(self.refused(s, BLUE, "acceptTrade", trade_id=tid), "Trade not found")


class Lifecycle(TradeCase):
    def standing(self):
        """Two offers by the mover and a counter, partly answered."""
        s, a = self.offer(main_phase())
        s, b = self.offer(s, offering={SHEEP: 1}, requesting={ORE: 1})
        s, c = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=a)
        s, _ = self.act(s, WHITE, "acceptTrade", trade_id=a)
        return s, [a, b, c]

    def assert_closed_by(self, events, ids, creators=("red", "red", "blue")):
        closed = [e for e in events if e["type"] == "tradeCancelled"]
        self.assertEqual(closed, [
            {"type": "tradeCancelled", "player": who, "tradeId": tid,
             "reason": "currentPlayerMoved"} for who, tid in zip(creators, ids)])
        # Taken down before the move itself happens.
        self.assertEqual([e["type"] for e in events[:len(ids)]], ["tradeCancelled"] * len(ids))

    def test_buying_closes_every_standing_offer(self):
        s, ids = self.standing()
        s, events = self.act(s, RED, "buyDevCard")
        self.assert_closed_by(events, ids)
        self.assertEqual(events[-1]["type"], "devCardBought")
        self.assertEqual(s.trades, [])

    def test_bank_trade_closes_every_standing_offer(self):
        s, ids = self.standing()
        s.get_player(RED).hand[ORE] = 4
        s, events = self.act(s, RED, "tradeWithBank", offering={ORE: 4}, requesting={WHEAT: 1})
        self.assert_closed_by(events, ids)
        self.assertEqual(events[-1]["type"], "bankTradeExecuted")

    def test_building_closes_every_standing_offer(self):
        s, ids = self.standing()
        vertex, edge = self.road_spot(s)
        s.board.settlements.append(VertexBuilding(vertex=vertex, player=RED))
        s, events = self.act(s, RED, "buildRoad", vertices=(edge.v1, edge.v2))
        self.assert_closed_by(events, ids)
        self.assertEqual(events[-1]["type"], "roadBuilt")

    def test_ending_the_turn_closes_every_standing_offer(self):
        s, ids = self.standing()
        s, events = self.act_end_turn(s)
        self.assert_closed_by(events, ids)
        self.assertEqual(events[-1]["type"], "turnEnded")
        self.assertEqual(s.trades, [])
        self.assertEqual(s.trades_proposed_this_turn, 0)
        self.assertEqual(s.current_player, BLUE)

    def test_a_refused_move_leaves_offers_standing(self):
        s, ids = self.standing()
        s.get_player(RED).hand = {WOOD: 1}
        self.refused(s, RED, "buyDevCard")
        self.assertEqual([t.trade_id for t in s.trades], ids)

    def test_trade_actions_do_not_close_other_offers(self):
        s, (a, b, c) = self.standing()
        s, _ = self.act(s, RED, "confirmTrade", trade_id=a, partner=WHITE)
        self.assertEqual([t.trade_id for t in s.trades], [b, c])
        s, _ = self.offer(s, offering={WHEAT: 1}, requesting={ORE: 1})
        self.assertEqual(len(s.trades), 3)

    def test_unanswered_offers_never_block_the_mover(self):
        s, ids = self.standing()
        self.assertIsNone(s.pending_action)
        self.assertEqual(s.current_player, RED)
        s, _ = self.act_end_turn(s)
        self.assertEqual(s.pending_action.type, "rollDice")

    def act_end_turn(self, s):
        result = self.engine.execute_action(s, Action(type="endTurn", player=s.current_player))
        self.assertTrue(result.success, result.error)
        return result.state, [to_json(e) for e in result.events]

    @staticmethod
    def road_spot(s):
        for tile in s.board.tiles:
            for v in tile.location.vertices():
                if s.board.building_at(v):
                    continue
                for w in v.adjacent_vertices():
                    edge = Edge.between(v, w)
                    if s.board.is_edge_on_board(edge):
                        return v, edge
        raise AssertionError("no edge on the board")


class Terms(TradeCase):
    def test_identical_standing_offer_is_dropped(self):
        s, tid = self.offer(main_phase(), offering={WOOD: 1, SHEEP: 1}, requesting={BRICK: 1})
        err = self.refused(s, RED, "proposeTrade", offering={SHEEP: 1, WOOD: 1},
                           requesting={BRICK: 1})
        self.assertEqual(err, f"An identical offer is already standing (tradeId {tid})")
        self.assertEqual(len(s.trades), 1)
        # Different cards stand side by side; the same offer is fine once the first closed.
        s, _ = self.offer(s, offering={WOOD: 1}, requesting={BRICK: 1})
        s, _ = self.act(s, RED, "cancelTrade", trade_id=tid)
        s, _ = self.offer(s, offering={WOOD: 1, SHEEP: 1}, requesting={BRICK: 1})
        self.assertEqual(len(s.trades), 2)

    def test_identical_counter_is_dropped(self):
        s, tid = self.offer(main_phase())
        s, cid = self.offer(s, BLUE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)
        self.assertIn(f"tradeId {cid}", self.refused(
            s, BLUE, "proposeTrade", offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid))
        # Another seat may make the same counter.
        self.offer(s, WHITE, offering={BRICK: 1}, requesting={WOOD: 2}, counter_to=tid)

    def test_insufficient_resources_are_refused(self):
        s = main_phase(hands={RED: {WOOD: 1}, BLUE: {WHEAT: 2}, WHITE: {BRICK: 1}, ORANGE: {}})
        self.assertEqual(self.refused(s, RED, "proposeTrade", offering={WOOD: 2},
                                      requesting={BRICK: 1}), "Insufficient resources to offer")
        s, tid = self.offer(s, offering={WOOD: 1}, requesting={BRICK: 1})
        self.assertEqual(self.refused(s, BLUE, "acceptTrade", trade_id=tid),
                         "Insufficient resources to accept")
        # Rejecting needs no cards.
        self.act(s, BLUE, "rejectTrade", trade_id=tid)

    def test_confirm_rechecks_both_hands(self):
        s = main_phase(hands={RED: {WOOD: 1}, BLUE: {BRICK: 1}, WHITE: {BRICK: 1},
                              ORANGE: {}})
        s, a = self.offer(s, offering={WOOD: 1}, requesting={BRICK: 1})
        s, b = self.offer(s, offering={WOOD: 1}, requesting={BRICK: 1, SHEEP: 1})
        s.get_player(BLUE).hand[SHEEP] = 1
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=a)
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=b)
        s, _ = self.act(s, WHITE, "acceptTrade", trade_id=a)
        s, _ = self.act(s, RED, "confirmTrade", trade_id=a, partner=BLUE)
        # Red's wood is gone, and so is Blue's brick.
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=b, partner=BLUE),
                         "red no longer has the offered resources")
        s.get_player(RED).hand[WOOD] = 1
        self.assertEqual(self.refused(s, RED, "confirmTrade", trade_id=b, partner=BLUE),
                         "blue no longer has the requested resources")

    def test_both_sides_non_empty_positive_and_distinct(self):
        s = main_phase()
        bad = [
            ({}, {BRICK: 1}, "Must offer and request resources"),
            ({WOOD: 1}, {}, "Must offer and request resources"),
            ({WOOD: 1}, None, "Must offer and request resources"),
            ({WOOD: -1}, {BRICK: 1}, "Counts in offering must be positive integers"),
            ({WOOD: 1}, {BRICK: -2}, "Counts in requesting must be positive integers"),
            ({WOOD: 1.5}, {BRICK: 1}, "Counts in offering must be positive integers"),
            ({WOOD: True}, {BRICK: 1}, "Counts in offering must be positive integers"),
            ({WOOD: 2}, {WOOD: 1, BRICK: 1}, "Cannot trade a resource for itself: wood"),
        ]
        for offering, requesting, err in bad:
            with self.subTest(offering=offering, requesting=requesting):
                self.assertEqual(self.refused(s, RED, "proposeTrade", offering=offering,
                                              requesting=requesting), err)

    def test_api_payload_with_negative_counts_is_refused(self):
        action = _action_from_json({"type": "proposeTrade", "player": "red",
                                    "offering": {"wood": 1}, "requesting": {"brick": -3}})
        result = self.engine.execute_action(main_phase(), action)
        self.assertFalse(result.success)


class Serialization(TradeCase):
    def test_state_round_trip_keeps_offers_answers_and_counters(self):
        s, tid = self.offer(main_phase())
        s, _ = self.act(s, BLUE, "acceptTrade", trade_id=tid)
        s, _ = self.act(s, WHITE, "rejectTrade", trade_id=tid)
        s, cid = self.offer(s, ORANGE, offering={ORE: 1}, requesting={WOOD: 1}, counter_to=tid)

        data = json.loads(json.dumps(to_json(s)))
        self.assertEqual(data["trades"], [
            {"tradeId": tid, "proposer": "red", "offering": {"wood": 1},
             "requesting": {"brick": 1}, "accepting": ["blue"], "rejected": ["white"],
             "counterTo": None},
            {"tradeId": cid, "proposer": "orange", "offering": {"ore": 1},
             "requesting": {"wood": 1}, "accepting": [], "rejected": [], "counterTo": tid},
        ])
        back = from_json(data, GameState)
        self.assertEqual(back.trades, s.trades)
        self.assertEqual(back.next_trade_id, s.next_trade_id)
        # The restored game carries on: the mover takes the counter.
        back, events = self.act(back, RED, "confirmTrade", trade_id=cid)
        self.assertEqual(events[0]["partner"], "red")

    def test_state_without_counter_field_still_loads(self):
        s, tid = self.offer(main_phase())
        data = to_json(s)
        del data["trades"][0]["counterTo"]
        self.assertIsNone(from_json(data, GameState).trades[0].counter_to)

    def test_actions_and_events_round_trip(self):
        action = Action(type="proposeTrade", player=BLUE, offering={BRICK: 1},
                        requesting={WOOD: 2}, counter_to=4)
        data = to_json(action)
        self.assertEqual(data["counterTo"], 4)
        self.assertEqual(from_json(data, Action), action)
        for action in (Action(type="withdrawTradeAnswer", player=BLUE, trade_id=3),
                       Action(type="confirmTrade", player=RED, trade_id=3)):
            self.assertEqual(from_json(to_json(action), Action), action)

        s, tid = self.offer(main_phase())
        s, proposed = self.act(s, BLUE, "proposeTrade", offering={BRICK: 1},
                               requesting={WOOD: 2}, counter_to=tid)
        s, _ = self.act(s, WHITE, "acceptTrade", trade_id=tid)
        s, withdrawn = self.act(s, WHITE, "withdrawTradeAnswer", trade_id=tid)
        s, rejected = self.act(s, RED, "rejectTrade", trade_id=2)
        s, closed = self.act(s, RED, "buyDevCard")
        events = proposed + withdrawn + rejected + closed
        self.assertEqual([e["type"] for e in events], [
            "tradeProposed", "tradeAnswerWithdrawn", "tradeRejected", "tradeCancelled",
            "tradeCancelled", "devCardBought"])
        for e in events:
            self.assertEqual(to_json(from_json(e, Event)), e)

        log = json.loads(json.dumps(game_log_to_json(
            s.config, s.board, s.turn_order, [from_json(e, Event) for e in events])))
        _, _, _, back = game_log_from_json(log)
        self.assertEqual([to_json(e) for e in back], events)


class PublicInformation(TradeCase):
    def test_every_seat_sees_every_offer_and_answer(self):
        s, tid = self.offer(main_phase())
        s, accepted = self.act(s, BLUE, "acceptTrade", trade_id=tid)
        s, executed = self.act(s, RED, "confirmTrade", trade_id=tid, partner=BLUE)
        s, _ = self.offer(s, offering={SHEEP: 1}, requesting={ORE: 1})
        state = to_json(s)
        for seat in ("white", "orange"):
            self.assertEqual(filter_state_for_player(state, seat)["trades"], state["trades"])
            for e in accepted + executed:
                self.assertEqual(filter_event_for_player(e, seat), e)


class Contract(unittest.TestCase):
    def test_trade_action_names(self):
        self.assertEqual(PLAYER_TRADE_ACTIONS, {
            "proposeTrade", "acceptTrade", "rejectTrade", "withdrawTradeAnswer",
            "confirmTrade", "cancelTrade"})

    def test_schema_lists_every_trade_action_and_event(self):
        schema = json.loads((pathlib.Path(__file__).parents[1] / "openhex" / "schema.json")
                            .read_text())
        actions = set(schema["$defs"]["ActionType"]["enum"])
        self.assertLessEqual(PLAYER_TRADE_ACTIONS | {"tradeWithBank"}, actions)
        events = set(schema["$defs"]["EventType"]["enum"])
        self.assertLessEqual({"tradeProposed", "tradeAccepted", "tradeRejected",
                              "tradeAnswerWithdrawn", "tradeExecuted", "tradeCancelled",
                              "bankTradeExecuted"}, events)
        self.assertIn("counterTo", schema["$defs"]["Trade"]["properties"])
        self.assertIn("counterTo", schema["$defs"]["Action"]["properties"])
        self.assertIn("reason", schema["$defs"]["Event"]["properties"])


if __name__ == "__main__":
    unittest.main()
