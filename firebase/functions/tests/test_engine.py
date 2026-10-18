"""Engine and server-helper tests. Run from the repository root:

    python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
"""

from __future__ import annotations

import json
import random
import unittest

from openhex import (
    Action, Board, DiceMode, GameConfig, GameState, OpenHexEngine, PlayerColor,
    Resource, RobberMode, Road, Terrain, Tile, VertexBuilding, Edge, HexPoint,
    TERRAIN_RESOURCE, generate_standard_board, to_json, from_json,
)
from openhex.state import DiceResult, PendingAction
from openhex.types import DevCard
from services.hidden_info import filter_event_for_player, public_event

RED, BLUE, WHITE, ORANGE = PlayerColor.RED, PlayerColor.BLUE, PlayerColor.WHITE, PlayerColor.ORANGE


def duel_config() -> GameConfig:
    return GameConfig(victory_points=15, discard_limit=9, num_players=2,
                      robber_mode=RobberMode.FRIENDLY, dice_mode=DiceMode.BALANCED)


def main_phase(state: GameState) -> GameState:
    """Skip setup: the game waits on the first player's roll."""
    state.is_setup_phase = False
    state.turn_number = 1
    state.current_player = state.turn_order[0]
    state.pending_action = PendingAction(type="rollDice")
    return state


def duel_state(seed: int = 1) -> GameState:
    return main_phase(GameState.new_game(duel_config(), generate_standard_board(seed), [RED, BLUE]))


def round_trip(state: GameState) -> GameState:
    """What the server does between two actions: store the state as JSON, load it back."""
    return from_json(json.loads(json.dumps(to_json(state))), GameState)


def unique_tile(board: Board) -> Tile:
    """The tile whose number no other tile shares (2 or 12), so a roll pays it alone."""
    return next(t for t in board.tiles if t.number == 2)


class BalancedDiceTest(unittest.TestCase):
    def test_deck_survives_json_before_the_first_roll(self):
        state = duel_state()
        self.assertIsNotNone(state.balanced_dice)
        self.assertIn("balancedDice", to_json(state))
        self.assertIsNotNone(round_trip(state).balanced_dice)

    def test_rolls_come_from_a_deck_across_json_round_trips(self):
        # The server loads the state from JSON before every action. A deck of
        # 36 cards reshuffled at 13 left yields 23 distinct cards in a row;
        # independent dice repeat a card long before that.
        state = duel_state()
        engine = OpenHexEngine(rng=random.Random(7))
        cards = []
        for _ in range(23):
            state = round_trip(state)
            cards.append(engine._roll(state))
        self.assertEqual(len(set(cards)), 23, cards)
        self.assertEqual(len(round_trip(state).balanced_dice.deck), 13)

    def test_random_mode_carries_no_deck(self):
        config = GameConfig(num_players=4, dice_mode=DiceMode.RANDOM)
        state = GameState.new_game(config, generate_standard_board(1), [RED, BLUE, WHITE, ORANGE])
        self.assertIsNone(state.balanced_dice)
        self.assertNotIn("balancedDice", to_json(state))
        self.assertIsNone(round_trip(state).balanced_dice)


class ShortBankTest(unittest.TestCase):
    def setUp(self):
        self.state = duel_state()
        self.tile = unique_tile(self.state.board)
        self.resource = TERRAIN_RESOURCE[self.tile.terrain]
        self.corners = self.tile.location.vertices()
        self.engine = OpenHexEngine()

    def produce(self) -> list:
        events = []
        self.engine._distribute_resources(self.state, self.tile.number, events)
        return events

    def test_nobody_is_paid_when_the_bank_cannot_cover_every_claim(self):
        s = self.state
        s.board.settlements += [VertexBuilding(self.corners[0], RED),
                                VertexBuilding(self.corners[2], BLUE)]
        s.bank[self.resource] = 1
        events = self.produce()
        self.assertEqual(s.bank[self.resource], 1)
        self.assertEqual(s.get_player(RED).hand.get(self.resource, 0), 0)
        self.assertEqual(s.get_player(BLUE).hand.get(self.resource, 0), 0)
        self.assertEqual(events[0].production, {RED: {}, BLUE: {}})

    def test_a_sole_claimant_takes_what_is_left(self):
        s = self.state
        s.board.cities.append(VertexBuilding(self.corners[0], RED))
        s.board.settlements.append(VertexBuilding(self.corners[2], RED))
        s.bank[self.resource] = 2
        events = self.produce()
        self.assertEqual(s.bank[self.resource], 0)
        self.assertEqual(s.get_player(RED).hand[self.resource], 2)
        self.assertEqual(events[0].production, {RED: {self.resource: 2}})

    def test_a_full_bank_pays_everyone(self):
        s = self.state
        s.board.cities.append(VertexBuilding(self.corners[0], RED))
        s.board.settlements.append(VertexBuilding(self.corners[2], BLUE))
        before = s.bank[self.resource]
        self.produce()
        self.assertEqual(s.bank[self.resource], before - 3)
        self.assertEqual(s.get_player(RED).hand[self.resource], 2)
        self.assertEqual(s.get_player(BLUE).hand[self.resource], 1)


class LongestRoadTest(unittest.TestCase):
    def test_a_road_between_two_opponent_buildings_counts_in_full(self):
        s = duel_state()
        corners = HexPoint(0, 0, 0).vertices()
        # Five roads: around the centre hex from its N corner to its SW one,
        # then one more outward. Blue sits on both ends of the chain.
        path = corners[:5]
        path.append(next(v for v in corners[4].adjacent_vertices() if v not in (corners[3], corners[5])))
        for a, b in zip(path, path[1:]):
            s.board.roads.append(Road(edge=Edge.between(a, b), player=RED))
        s.board.settlements += [VertexBuilding(path[0], BLUE), VertexBuilding(path[-1], BLUE)]
        engine = OpenHexEngine()
        self.assertEqual(engine._compute_longest_road(s, RED), 5)
        events = []
        engine._check_awards(s, Action(type="buildRoad", player=RED), events)
        self.assertEqual(s.longest_road_player, RED)
        self.assertEqual([(e.type, e.player, e.length) for e in events],
                         [("longestRoadAwarded", RED, 5)])

    def test_an_opponent_building_in_the_middle_still_breaks_the_road(self):
        s = duel_state()
        corners = HexPoint(0, 0, 0).vertices()
        for a, b in zip(corners, corners[1:]):
            s.board.roads.append(Road(edge=Edge.between(a, b), player=RED))
        s.board.settlements.append(VertexBuilding(corners[2], BLUE))
        self.assertEqual(OpenHexEngine()._compute_longest_road(s, RED), 3)


class FriendlyRobberTest(unittest.TestCase):
    def small_board(self, tiles: int) -> GameState:
        """A desert with the robber and `tiles` producing hexes around it."""
        around = [HexPoint(1, -1, 0), HexPoint(1, 0, -1), HexPoint(0, 1, -1)]
        board = Board(tiles=[Tile(HexPoint(0, 0, 0), Terrain.DESERT)] +
                      [Tile(h, Terrain.FOREST, 6) for h in around[:tiles]],
                      robber=HexPoint(0, 0, 0))
        state = main_phase(GameState.new_game(duel_config(), board, [RED, BLUE]))
        state.dice = None
        state.pending_action = PendingAction(type="moveRobber")
        return state

    def shield(self, state: GameState, hex_: HexPoint):
        """Blue, still at starting VP, builds on `hex_` (its far corner)."""
        state.board.settlements.append(VertexBuilding(hex_.vertices()[1], BLUE))

    def test_a_seat_at_starting_vp_is_shielded(self):
        s = self.small_board(3)
        self.shield(s, HexPoint(1, -1, 0))
        self.shield(s, HexPoint(1, 0, -1))
        result = OpenHexEngine().execute_action(s, Action(type="moveRobber", player=RED,
                                                          location=HexPoint(1, -1, 0)))
        self.assertFalse(result.success)
        self.assertIn("Friendly robber", result.error)
        result = OpenHexEngine().execute_action(s, Action(type="moveRobber", player=RED,
                                                          location=HexPoint(0, 1, -1)))
        self.assertTrue(result.success, result.error)

    def test_a_shielded_hex_is_allowed_once_every_hex_is_shielded(self):
        s = self.small_board(2)
        self.shield(s, HexPoint(1, -1, 0))
        self.shield(s, HexPoint(1, 0, -1))
        result = OpenHexEngine().execute_action(s, Action(type="moveRobber", player=RED,
                                                          location=HexPoint(1, -1, 0)))
        self.assertTrue(result.success, result.error)

    def test_a_seat_past_starting_vp_is_not_shielded(self):
        s = self.small_board(3)
        self.shield(s, HexPoint(1, -1, 0))
        self.shield(s, HexPoint(1, 0, -1))
        self.shield(s, HexPoint(0, 1, -1))  # three settlements: 3 VP
        result = OpenHexEngine().execute_action(s, Action(type="moveRobber", player=RED,
                                                          location=HexPoint(1, -1, 0)))
        self.assertTrue(result.success, result.error)


class RoadBuildingTest(unittest.TestCase):
    """Road Building must never leave a free road owed that cannot be placed."""

    def holding_road_building(self) -> GameState:
        s = duel_state()
        s.pending_action = None
        s.dice = DiceResult(3, 4)
        s.get_player(RED).dev_cards[DevCard.ROAD_BUILDING] = 1
        return s

    def play(self, s: GameState):
        return OpenHexEngine().execute_action(s, Action(type="playRoadBuilding", player=RED))

    def test_refused_with_no_road_piece_left(self):
        s = self.holding_road_building()
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements.append(VertexBuilding(corners[0], RED))
        s.board.roads.append(Road(edge=Edge.between(corners[0], corners[1]), player=RED))
        s.config.max_roads = 1
        result = self.play(s)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "No road pieces left")

    def test_refused_with_nowhere_to_place_a_road(self):
        s = self.holding_road_building()
        v = HexPoint(0, 0, 0).vertices()[1]            # a type-A corner of the centre hex
        far = v.adjacent_vertices()
        s.board.settlements.append(VertexBuilding(v, RED))
        s.board.roads.append(Road(edge=Edge.between(v, far[0]), player=RED))
        s.board.settlements.append(VertexBuilding(far[0], BLUE))   # blocks the road's far end
        for w in far[1:]:                                           # blue sits on the other two edges
            s.board.roads.append(Road(edge=Edge.between(v, w), player=BLUE))
        result = self.play(s)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "No room for a free road")

    def test_one_piece_left_places_one_road_and_moves_on(self):
        s = self.holding_road_building()
        corners = HexPoint(0, 0, 0).vertices()
        s.board.settlements.append(VertexBuilding(corners[0], RED))
        s.config.max_roads = 1
        result = self.play(s)
        self.assertTrue(result.success, result.error)
        s = result.state
        self.assertEqual((s.pending_action.type, s.pending_action.remaining), ("placeFreeRoad", 1))
        result = OpenHexEngine().execute_action(s, Action(type="placeFreeRoad", player=RED,
                                                          vertices=(corners[0], corners[1])))
        self.assertTrue(result.success, result.error)
        self.assertIsNone(result.state.pending_action)

    def test_second_road_is_forfeited_when_the_first_uses_the_last_room(self):
        s = self.holding_road_building()
        v = HexPoint(0, 0, 0).vertices()[1]
        far = v.adjacent_vertices()
        s.board.settlements.append(VertexBuilding(v, RED))
        s.board.settlements.append(VertexBuilding(far[0], BLUE))   # the one open edge ends at blue
        for w in far[1:]:
            s.board.roads.append(Road(edge=Edge.between(v, w), player=BLUE))
        result = self.play(s)
        self.assertTrue(result.success, result.error)
        result = OpenHexEngine().execute_action(result.state, Action(type="placeFreeRoad", player=RED,
                                                                     vertices=(v, far[0])))
        self.assertTrue(result.success, result.error)
        self.assertIsNone(result.state.pending_action)


class PlayersToActTest(unittest.TestCase):
    def test_the_current_player_owes_the_move_by_default(self):
        s = duel_state()
        self.assertEqual(s.players_to_act(), [RED])
        s.pending_action = PendingAction(type="moveRobber")
        self.assertEqual(s.players_to_act(), [RED])

    def test_the_discarding_players_owe_the_move_during_a_discard(self):
        s = duel_state()
        s.pending_action = PendingAction(type="discardResources", players={BLUE: 5})
        self.assertEqual(s.players_to_act(), [BLUE])
        s.pending_action = PendingAction(type="discardResources", players={RED: 4, BLUE: 5})
        self.assertEqual(s.players_to_act(), [RED, BLUE])

    def test_players_to_act_survives_json(self):
        s = duel_state()
        s.pending_action = PendingAction(type="discardResources", players={BLUE: 5})
        self.assertEqual(round_trip(s).players_to_act(), [BLUE])


class PublicEventTest(unittest.TestCase):
    def test_the_spectator_view_drops_the_card_and_the_resource(self):
        bought = {"type": "devCardBought", "player": "red", "card": "victoryPoint"}
        stolen = {"type": "resourceStolen", "player": "red", "target": "blue", "resource": "ore"}
        self.assertEqual(public_event(bought), {"type": "devCardBought", "player": "red"})
        self.assertEqual(public_event(stolen), {"type": "resourceStolen", "player": "red", "target": "blue"})
        self.assertEqual(filter_event_for_player(bought, "red"), bought)
        self.assertEqual(filter_event_for_player(stolen, "blue"), stolen)
        self.assertEqual(filter_event_for_player(stolen, "white"), public_event(stolen))


if __name__ == "__main__":
    unittest.main()
