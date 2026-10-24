"""Rules that follow colonist.io's table: any card before the roll, port
types shuffled, and the move made for a seat past its deadline."""

from __future__ import annotations

import unittest

from openhex import (
    Action, DevCard, GameState, OpenHexEngine, PlayerColor, Resource, Road,
    VertexBuilding, Edge, HexPoint, generate_standard_board,
)
from openhex.maps import STANDARD_PORT_POSITIONS
from openhex.state import DiceResult, PendingAction
from tests.test_engine import duel_state, round_trip

RED, BLUE = PlayerColor.RED, PlayerColor.BLUE


def act(s: GameState, **fields):
    result = OpenHexEngine().execute_action(s, Action(player=RED, **fields))
    return result


class CardsBeforeTheRollTest(unittest.TestCase):
    def before_roll(self, card: DevCard) -> GameState:
        s = duel_state()                      # the game waits on red's roll
        s.get_player(RED).dev_cards[card] = 1
        return s

    def test_year_of_plenty_before_the_roll_keeps_the_roll_pending(self):
        s = self.before_roll(DevCard.YEAR_OF_PLENTY)
        r = act(s, type="playYearOfPlenty", resources=[Resource.ORE, Resource.ORE])
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.state.pending_action.type, "rollDice")
        self.assertEqual(r.state.get_player(RED).hand[Resource.ORE], 2)
        self.assertTrue(act(r.state, type="rollDice").success)

    def test_monopoly_before_the_roll(self):
        s = self.before_roll(DevCard.MONOPOLY)
        s.get_player(BLUE).hand[Resource.WHEAT] = 3
        r = act(s, type="playMonopoly", resource=Resource.WHEAT)
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.state.get_player(RED).hand[Resource.WHEAT], 3)
        self.assertEqual(r.state.pending_action.type, "rollDice")

    def test_road_building_before_the_roll_places_its_roads_then_hands_back_the_roll(self):
        s = self.before_roll(DevCard.ROAD_BUILDING)
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements.append(VertexBuilding(corners[0], RED))
        r = act(s, type="playRoadBuilding")
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.state.pending_action.type, "placeFreeRoad")
        self.assertFalse(act(r.state, type="rollDice").success)
        r = act(r.state, type="placeFreeRoad", vertices=(corners[0], corners[1]))
        self.assertTrue(r.success, r.error)
        r = act(r.state, type="placeFreeRoad", vertices=(corners[1], corners[2]))
        self.assertTrue(r.success, r.error)
        self.assertEqual(r.state.pending_action.type, "rollDice")
        self.assertIsNone(r.state.dice)
        self.assertTrue(act(r.state, type="rollDice").success)

    def test_building_still_waits_for_the_roll(self):
        s = duel_state()
        s.get_player(RED).hand = {Resource.WOOD: 1, Resource.BRICK: 1}
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements.append(VertexBuilding(corners[0], RED))
        r = act(s, type="buildRoad", vertices=(corners[0], corners[1]))
        self.assertFalse(r.success)
        self.assertEqual(r.error, "Must resolve pending action: rollDice")

    def test_one_card_a_turn_still_holds_before_the_roll(self):
        s = self.before_roll(DevCard.YEAR_OF_PLENTY)
        s.get_player(RED).dev_cards[DevCard.MONOPOLY] = 1
        r = act(s, type="playYearOfPlenty", resources=[Resource.ORE, Resource.ORE])
        r = act(r.state, type="playMonopoly", resource=Resource.WHEAT)
        self.assertEqual(r.error, "Already played a dev card this turn")

    def test_no_card_while_another_move_is_owed(self):
        s = self.before_roll(DevCard.YEAR_OF_PLENTY)
        s.pending_action = PendingAction(type="moveRobber")
        s.dice = DiceResult(3, 4)
        r = act(s, type="playYearOfPlenty", resources=[Resource.ORE, Resource.ORE])
        self.assertEqual(r.error, "Must resolve pending action: moveRobber")


class PortShuffleTest(unittest.TestCase):
    def test_every_board_has_four_generic_ports_and_one_per_resource(self):
        for seed in range(100):
            kinds = [p.resource for p in generate_standard_board(seed).ports]
            self.assertEqual(kinds.count(None), 4, seed)
            self.assertEqual(sorted(k.value for k in kinds if k), sorted(r.value for r in Resource), seed)

    def test_a_generic_port_can_sit_at_any_position(self):
        generic_at = [0] * len(STANDARD_PORT_POSITIONS)
        for seed in range(300):
            for i, port in enumerate(generate_standard_board(seed).ports):
                if port.resource is None:
                    generic_at[i] += 1
        self.assertTrue(all(generic_at), generic_at)
        # about 4 in 9 of the time at each position
        self.assertTrue(all(90 <= n <= 180 for n in generic_at), generic_at)


class DefaultActionTest(unittest.TestCase):
    """The move made for a seat past its deadline is always legal."""

    def default(self, s: GameState, player=RED):
        engine = OpenHexEngine()
        action = engine.default_action(s, player)
        self.assertIsNotNone(action)
        result = engine.execute_action(s, action)
        self.assertTrue(result.success, result.error)
        return action, result.state

    def test_setup_placements(self):
        s = GameState.new_game(duel_state().config, generate_standard_board(3), [RED, BLUE])
        action, s = self.default(s)
        self.assertEqual(action.type, "placeInitialSettlement")
        action, s = self.default(s)
        self.assertEqual(action.type, "placeInitialRoad")
        self.assertEqual(s.current_player, BLUE)

    def test_roll_then_end_turn(self):
        s = duel_state()
        action, s = self.default(s)
        self.assertEqual(action.type, "rollDice")
        if s.pending_action is None:                  # not a seven
            action, s = self.default(s)
            self.assertEqual(action.type, "endTurn")
            self.assertEqual(s.current_player, BLUE)

    def test_discard_takes_the_most_held_cards(self):
        s = duel_state()
        s.dice = DiceResult(3, 4)
        s.get_player(BLUE).hand = {Resource.WOOD: 6, Resource.ORE: 3, Resource.WHEAT: 1}
        s.pending_action = PendingAction(type="discardResources", players={BLUE: 5})
        action, s = self.default(s, BLUE)
        self.assertEqual(action.resources, {Resource.WOOD: 4, Resource.ORE: 1})
        self.assertEqual(s.pending_action.type, "moveRobber")

    def test_robber_goes_where_it_robs_nobody_when_it_can(self):
        s = duel_state()
        s.dice = DiceResult(3, 4)
        s.pending_action = PendingAction(type="moveRobber")
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements += [VertexBuilding(corners[0], BLUE), VertexBuilding(corners[2], BLUE),
                                VertexBuilding(corners[4], BLUE)]        # blue at 3 VP: robbable
        s.get_player(BLUE).hand = {Resource.ORE: 2}
        action, s = self.default(s)
        self.assertEqual(action.type, "moveRobber")
        self.assertIsNone(s.pending_action)                               # nobody to steal from

    def test_steal_and_free_road(self):
        s = duel_state()
        s.dice = DiceResult(3, 4)
        s.pending_action = PendingAction(type="stealFromPlayer", location=HexPoint(0, 0, 0), targets=[BLUE])
        s.get_player(BLUE).hand = {Resource.ORE: 1}
        action, s = self.default(s)
        self.assertEqual((action.type, action.target), ("stealFromPlayer", BLUE))
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements.append(VertexBuilding(corners[0], RED))
        s.pending_action = PendingAction(type="placeFreeRoad", remaining=1)
        action, s = self.default(s)
        self.assertEqual(action.type, "placeFreeRoad")
        self.assertEqual(s.board.player_road_count(RED), 1)

    def test_survives_json(self):
        s = round_trip(duel_state())
        self.assertEqual(OpenHexEngine().default_action(s, RED).type, "rollDice")


if __name__ == "__main__":
    unittest.main()
