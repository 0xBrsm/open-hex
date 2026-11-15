"""The 3p ladder mode: 4p's rules with three seats.

Run from the repository root:
    python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
"""

from __future__ import annotations

import random
import unittest

from openhex import (
    Action, DiceMode, GameConfig, GameState, OpenHexEngine, PlayerColor, Resource, RobberMode,
    generate_standard_board,
)

RED, BLUE, WHITE = PlayerColor.RED, PlayerColor.BLUE, PlayerColor.WHITE
COSTS = {
    "buildCity": {Resource.WHEAT: 2, Resource.ORE: 3},
    "buildSettlement": {Resource.WOOD: 1, Resource.BRICK: 1, Resource.SHEEP: 1, Resource.WHEAT: 1},
    "buildRoad": {Resource.WOOD: 1, Resource.BRICK: 1},
    "buyDevCard": {Resource.SHEEP: 1, Resource.WHEAT: 1, Resource.ORE: 1},
}


def three_player_config() -> GameConfig:
    """The server's 3p config (`game_manager.create_game`)."""
    return GameConfig(victory_points=10, discard_limit=7, num_players=3,
                      robber_mode=RobberMode.NORMAL, dice_mode=DiceMode.RANDOM)


class Builder:
    """Plays a seat by building whatever it can afford, trading 4:1 (or at a
    port's rate) toward what it lacks, and otherwise making the server's
    default move for it."""

    def __init__(self, engine: OpenHexEngine, board):
        self.engine = engine
        self.vertices = sorted({v for t in board.tiles for v in t.location.vertices()},
                               key=lambda v: (v.x, v.y, v.z))
        self.edges = [(v, w) for v in self.vertices for w in v.adjacent_vertices()]

    def legal(self, s: GameState, action: Action) -> bool:
        return self.engine._validate(s, action) is None

    def move(self, s: GameState, player: PlayerColor) -> Action:
        if s.pending_action is None and s.dice is not None:
            hand = s.get_player(player).hand
            for kind, cost in COSTS.items():
                if any(hand.get(r, 0) < n for r, n in cost.items()):
                    continue
                if kind == "buyDevCard":
                    candidates = [Action(type=kind, player=player)]
                elif kind == "buildRoad":
                    candidates = [Action(type=kind, player=player, vertices=e) for e in self.edges]
                else:
                    candidates = [Action(type=kind, player=player, vertex=v) for v in self.vertices]
                for action in candidates:
                    if self.legal(s, action):
                        return action
            for give in Resource:
                rate = s.board.best_trade_rate(player, give)
                if hand.get(give, 0) >= rate:
                    want = min((r for r in Resource if r != give), key=lambda r: hand.get(r, 0))
                    action = Action(type="tradeWithBank", player=player,
                                    offering={give: rate}, requesting={want: 1})
                    if self.legal(s, action):
                        return action
        return self.engine.default_action(s, player)


class ThreePlayerGameTest(unittest.TestCase):
    def test_a_three_player_game_plays_to_a_winner(self):
        engine = OpenHexEngine(rng=random.Random(3))
        board = generate_standard_board(3)
        state = GameState.new_game(three_player_config(), board, [RED, BLUE, WHITE])
        builder = Builder(engine, board)
        setup_order = []

        for _ in range(20000):
            if state.winner is not None:
                break
            player = state.players_to_act()[0]
            action = builder.move(state, player)
            self.assertIsNotNone(action, f"no move for {player.value}")
            if action.type == "placeInitialSettlement":
                setup_order.append(player)
            result = engine.execute_action(state, action)
            self.assertTrue(result.success, f"{action.type} by {player.value}: {result.error}")
            state = result.state

        self.assertEqual(setup_order, [RED, BLUE, WHITE, WHITE, BLUE, RED])
        self.assertIsNotNone(state.winner, "no one reached 10 VP")
        self.assertGreaterEqual(state.compute_vp(state.winner), 10)
        self.assertEqual(len(state.players), 3)


if __name__ == "__main__":
    unittest.main()
