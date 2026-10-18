#!/usr/bin/env python3
"""Local 1v1 OpenHex runner — no Firebase, no polling.

Runs N games between two bots directly through the openhex Python engine, which
is the ground-truth simulator. Each bot exposes `pick_action(state, my_color) -> dict`.

Usage:
    # random vs random (fast smoke test)
    python local_runner.py --red random --blue random --games 5

    # canopy vs random
    python local_runner.py --red canopy:http://127.0.0.1:18780 --blue random --games 10

Bot specs:
    random               Uniform over legal actions (best-effort).
    canopy:URL           canopy MCTS via its OpenHex server at URL.

Requires the openhex engine on PYTHONPATH (from firebase/functions).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

# Add firebase/functions to path for openhex imports
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FUNCTIONS_DIR = os.path.abspath(os.path.join(_THIS_DIR, "..", "firebase", "functions"))
if _FUNCTIONS_DIR not in sys.path:
    sys.path.insert(0, _FUNCTIONS_DIR)

from openhex import (  # noqa: E402
    Action,
    DiceMode,
    GameConfig,
    GameState,
    OpenHexEngine,
    PlayerColor,
    RobberMode,
    generate_standard_board,
    to_json,
)
from openhex.board import Board, Port, Tile  # noqa: E402
from openhex.json_codec import _action_from_json  # noqa: E402
from openhex.types import DevCard, HexPoint, Resource, Terrain, VertexPoint, resource_total  # noqa: E402


# ---------------------------------------------------------------------------
# Bot interface
# ---------------------------------------------------------------------------


class Bot:
    """Any object with `pick_action(state, my_color) -> dict` works. The dict is
    the OpenHex action payload (no 'player' field; the runner fills that in)."""

    name: str = "bot"

    def pick_action(self, state: GameState, my_color: PlayerColor) -> dict:
        raise NotImplementedError


class RandomBot(Bot):
    """Best-effort random bot. Samples a plausible legal action by trying a few
    candidates and keeping the first one the engine accepts. Good enough for
    smoke-testing the runner."""

    def __init__(self, name: str = "random", seed: int | None = None):
        self.name = name
        self.rng = random.Random(seed)

    def pick_action(self, state: GameState, my_color: PlayerColor) -> dict:
        pa = state.pending_action

        # Pending dispatch — return the forced action type with a random target.
        if pa:
            if pa.type == "placeInitialSettlement":
                # Pick any empty vertex (that touches the board) respecting distance rule.
                seen = set()
                candidates = []
                for tile in state.board.tiles:
                    for v in tile.location.vertices():
                        if v in seen:
                            continue
                        seen.add(v)
                        if state.board.building_at(v):
                            continue
                        if any(state.board.building_at(a) for a in v.adjacent_vertices()):
                            continue
                        candidates.append(v)
                self.rng.shuffle(candidates)
                v = candidates[0]
                return {"type": "placeInitialSettlement", "vertex": _v(v)}

            if pa.type == "placeInitialRoad":
                last = state.last_setup_settlement
                # Pick an edge from the last settlement.
                edges = []
                if last:
                    for adj in last.adjacent_vertices():
                        edges.append((last, adj))
                self.rng.shuffle(edges)
                for (v1, v2) in edges:
                    return {"type": "placeInitialRoad", "vertices": [_v(v1), _v(v2)]}
                # Fallback: pick any board edge (shouldn't happen in well-formed state).
                return {"type": "placeInitialRoad", "vertices": [_v(last), _v(last)]}

            if pa.type == "moveRobber":
                tiles = [t for t in state.board.tiles if t.location != state.board.robber]
                self.rng.shuffle(tiles)
                return {"type": "moveRobber", "location": _h(tiles[0].location)}

            if pa.type == "stealFromPlayer":
                targets = pa.targets or [c for c in state.turn_order if c != my_color]
                return {"type": "stealFromPlayer", "target": self.rng.choice(targets).value}

            if pa.type == "discardResources":
                # Random valid discard for me.
                n = (pa.players or {}).get(my_color, 0)
                return _random_discard(state, my_color, n, self.rng)

            if pa.type == "placeFreeRoad":
                return {"type": "endTurn"}  # give up; engine will choose fallback

            if pa.type == "pickYearOfPlentyResource":
                return {"type": "pickYearOfPlentyResource",
                        "resource": self.rng.choice(list(Resource)).value}

            if pa.type == "pickMonopolyResource":
                return {"type": "playMonopoly",
                        "resource": self.rng.choice(list(Resource)).value}

        # Main phase — roll first, then end turn.
        if state.dice is None and not state.is_setup_phase:
            return {"type": "rollDice"}
        return {"type": "endTurn"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _v(vertex) -> dict:
    return {"x": vertex.x, "y": vertex.y, "z": vertex.z}


def _h(hex_pt) -> dict:
    return {"x": hex_pt.x, "y": hex_pt.y, "z": hex_pt.z}


def _fallback_candidates(state: GameState, actor: PlayerColor, rng: random.Random):
    """Yield plausible fallback actions for a stuck bot, in decreasing preference."""
    pa = state.pending_action

    if pa and pa.type == "discardResources":
        n = (pa.players or {}).get(actor, 0)
        if n > 0:
            yield _random_discard(state, actor, n, rng)

    if pa and pa.type == "moveRobber":
        tiles = [t for t in state.board.tiles if t.location != state.board.robber]
        rng.shuffle(tiles)
        for t in tiles:
            yield {"type": "moveRobber", "location": _h(t.location)}

    if pa and pa.type == "stealFromPlayer":
        targets = pa.targets or [c for c in state.turn_order if c != actor]
        for t in targets:
            yield {"type": "stealFromPlayer", "target": t.value}

    if pa and pa.type == "pickYearOfPlentyResource":
        for r in list(Resource):
            yield {"type": "pickYearOfPlentyResource", "resource": r.value}

    if pa and pa.type == "pickMonopolyResource":
        for r in list(Resource):
            yield {"type": "playMonopoly", "resource": r.value}

    if not state.is_setup_phase:
        yield {"type": "rollDice"}
        yield {"type": "endTurn"}


def _random_discard(state: GameState, color: PlayerColor, n: int, rng: random.Random) -> dict:
    player = state.get_player(color)
    pool = []
    for r, c in player.hand.items():
        pool.extend([r] * c)
    rng.shuffle(pool)
    chosen = pool[:n]
    rm: dict = {}
    for r in chosen:
        rm[r.value] = rm.get(r.value, 0) + 1
    return {"type": "discardResources", "resources": rm}


# ---------------------------------------------------------------------------
# Canopy bot (stub — requires canopy-side Rust work)
# ---------------------------------------------------------------------------


class CanopyBot(Bot):
    """Canopy MCTS bot via HTTP. Talks to a running canopy openhex server.

    The canopy server is stateful: it maintains its own internal game state.
    This bot syncs by calling /apply for opponent actions and /act for its own.

    Chance nodes (dice, steal, dev-card draw) are resolved by OpenHex and
    injected into canopy via the /apply and /act chance fields.
    """

    def __init__(self, url: str, name: str = "canopy"):
        self.name = name
        self.url = url.rstrip("/")
        self._pending_chance: str | None = None  # "dice", "steal", "dev_card"

    def _post(self, path: str, data: dict) -> dict:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())

    def new_game(self, seed: int) -> dict:
        """Start a new game on the canopy server, return board JSON."""
        return self._post("/new_game", {"seed": seed})

    def apply_action(self, action_dict: dict, events: list | None = None) -> dict:
        """Tell canopy about an opponent's (or self's) action + chance data."""
        payload: dict[str, Any] = {"action": action_dict}

        # Extract chance data from OpenHex events
        if events:
            for ev in events:
                if ev.type == "diceRolled" and ev.die1 is not None:
                    payload["dice"] = [ev.die1, ev.die2]
                if ev.type == "resourceStolen" and ev.resource is not None:
                    payload["stolen_resource"] = ev.resource.value
                if ev.type == "devCardBought" and ev.card is not None:
                    payload["dev_card_drawn"] = _dev_card_to_canopy(ev.card)

        resp = self._post("/apply", payload)
        self._pending_chance = resp.get("pending_chance")
        return resp  # type: ignore[return-value]

    def pick_action(self, state: GameState, my_color: PlayerColor) -> dict:
        """Ask canopy for its next action via MCTS."""
        # If canopy needs chance data before it can act, provide it
        chance_payload: dict[str, Any] = {}
        if self._pending_chance == "dice" and state.dice is not None:
            chance_payload["chance"] = {
                "type": "dice",
                "value": [state.dice.die1, state.dice.die2],
            }
            self._pending_chance = None
        elif self._pending_chance == "steal":
            # Steal was already resolved in OpenHex; skip for now
            # (steal events are injected via apply_action)
            self._pending_chance = None
        elif self._pending_chance == "dev_card":
            self._pending_chance = None

        resp = self._post("/act", chance_payload)

        # Check for game over
        if resp.get("game_over"):
            # Canopy thinks game is over — return endTurn as fallback
            return {"type": "endTurn"}

        # Check if canopy needs chance data (it can't act yet)
        if resp.get("needs_chance"):
            self._pending_chance = resp["needs_chance"]
            # Can't act yet — return a no-op that the caller should handle
            return {"type": "_needs_chance", "chance_type": resp["needs_chance"]}

        self._pending_chance = resp.get("pending_chance")
        action = resp["action"]
        # Remove player field (runner fills it in)
        action.pop("player", None)
        return action


    def sync_state(self, state: GameState) -> None:
        """Sync canopy's internal state with OpenHex ground truth.

        Overwrites hands, bank, dev cards, piece counts, robber, and awards
        so that canopy's MCTS always runs on the correct state.
        """
        def _rmap(hand: dict) -> dict:
            return {
                "wood": hand.get(Resource.WOOD, 0),
                "brick": hand.get(Resource.BRICK, 0),
                "sheep": hand.get(Resource.SHEEP, 0),
                "wheat": hand.get(Resource.WHEAT, 0),
                "ore": hand.get(Resource.ORE, 0),
            }

        def _dmap(cards: dict) -> dict:
            return {
                "knight": cards.get(DevCard.KNIGHT, 0),
                "victoryPoint": cards.get(DevCard.VICTORY_POINT, 0),
                "roadBuilding": cards.get(DevCard.ROAD_BUILDING, 0),
                "yearOfPlenty": cards.get(DevCard.YEAR_OF_PLENTY, 0),
                "monopoly": cards.get(DevCard.MONOPOLY, 0),
            }

        def _player_data(p_state, color: PlayerColor) -> dict:
            n_settlements = sum(1 for b in state.board.settlements if b.player == color)
            n_cities = sum(1 for b in state.board.cities if b.player == color)
            n_roads = sum(1 for r in state.board.roads if r.player == color)
            return {
                "hand": _rmap(p_state.hand),
                "devCards": _dmap(p_state.dev_cards),
                "devCardsBoughtThisTurn": _dmap(p_state.dev_cards_bought_this_turn),
                "knightsPlayed": p_state.knights_played,
                "settlementsLeft": 5 - n_settlements,
                "citiesLeft": 4 - n_cities,
                "roadsLeft": 15 - n_roads,
                "playedDevCardThisTurn": (
                    state.played_dev_card_this_turn
                    if state.current_player == color
                    else False
                ),
            }

        red_p = next(p for p in state.players if p.color == PlayerColor.RED)
        blue_p = next(p for p in state.players if p.color == PlayerColor.BLUE)

        payload = {
            "red": _player_data(red_p, PlayerColor.RED),
            "blue": _player_data(blue_p, PlayerColor.BLUE),
            "bank": _rmap(state.bank),
            "devDeckSize": sum(state.dev_card_deck.values()),
            "robber": {
                "x": state.board.robber.x,
                "y": state.board.robber.y,
                "z": state.board.robber.z,
            },
            "longestRoadPlayer": (
                state.longest_road_player.value if state.longest_road_player else None
            ),
            "largestArmyPlayer": (
                state.largest_army_player.value if state.largest_army_player else None
            ),
        }

        try:
            self._post("/sync", payload)
        except Exception as e:
            print(f"  WARNING: /sync failed: {e}", flush=True)


def _dev_card_to_canopy(card: DevCard) -> str:
    """Map OpenHex DevCard enum to canopy dev card name."""
    return {
        DevCard.KNIGHT: "knight",
        DevCard.VICTORY_POINT: "victoryPoint",
        DevCard.ROAD_BUILDING: "roadBuilding",
        DevCard.YEAR_OF_PLENTY: "yearOfPlenty",
        DevCard.MONOPOLY: "monopoly",
    }[card]


# ---------------------------------------------------------------------------
# Bot factory
# ---------------------------------------------------------------------------


def make_bot(spec: str, name: str, seed: int | None = None) -> Bot:
    """Parse a bot spec like `canopy:http://...` or `random`."""
    if spec == "random":
        return RandomBot(name=name, seed=seed)
    if spec.startswith("canopy:"):
        return CanopyBot(url=spec[len("canopy:"):], name=name)
    raise ValueError(f"Unknown bot spec: {spec!r}")


# ---------------------------------------------------------------------------
# Game loop
# ---------------------------------------------------------------------------


@dataclass
class GameOutcome:
    winner: PlayerColor | None
    actions: int
    turns: int
    red_vp: int
    blue_vp: int
    elapsed_s: float
    reason: str  # "win", "max_actions", "error"


def _apply(engine: OpenHexEngine, state: GameState, action_dict: dict,
           player: PlayerColor) -> tuple[GameState | None, str | None, list]:
    """Try to execute an action; return (new_state, error, events)."""
    d = dict(action_dict)
    d["player"] = player.value
    try:
        action = _action_from_json(d)
    except Exception as e:
        return None, f"action parse error: {e}", []
    result = engine.execute_action(state, action)
    if not result.success:
        return None, result.error, []
    return result.state, None, result.events


def _who_acts(state: GameState) -> PlayerColor | None:
    """Who is being asked to act right now?

    For most pending actions it's current_player; for discardResources it can be
    any player with a non-zero discard count.
    """
    pa = state.pending_action
    if pa and pa.type == "discardResources" and pa.players:
        for c, n in pa.players.items():
            if n > 0:
                return c
        return None
    return state.current_player


def _board_from_canopy_json(board_json: dict) -> Board:
    """Construct an OpenHex Board from canopy's /new_game board JSON."""
    tiles = []
    for t in board_json["tiles"]:
        loc = HexPoint(t["location"]["x"], t["location"]["y"], t["location"]["z"])
        terrain = Terrain(t["terrain"])
        tiles.append(Tile(location=loc, terrain=terrain, number=t.get("number")))

    ports = []
    for p in board_json["ports"]:
        v1 = VertexPoint(p["vertices"][0]["x"], p["vertices"][0]["y"], p["vertices"][0]["z"])
        v2 = VertexPoint(p["vertices"][1]["x"], p["vertices"][1]["y"], p["vertices"][1]["z"])
        res = Resource(p["resource"]) if p.get("resource") else None
        ports.append(Port(vertices=(v1, v2), rate=p["rate"], resource=res))

    robber = None
    if board_json.get("robber"):
        r = board_json["robber"]
        robber = HexPoint(r["x"], r["y"], r["z"])

    return Board(tiles=tiles, ports=ports, robber=robber)


def _inject_chance_events(canopy_bot: CanopyBot, events: list,
                          verbose: bool = False) -> None:
    """Inject chance outcomes (dice, steal, dev card) into canopy via /chance.

    CRITICAL: Updates canopy_bot._pending_chance from the /chance response.
    Without this, pick_action re-sends stale chance data to /act, corrupting state.
    """
    for ev in events:
        if ev.type == "diceRolled" and ev.die1 is not None:
            if canopy_bot._pending_chance != "dice":
                if verbose:
                    print(f"  SKIP /chance dice: pending={canopy_bot._pending_chance}", flush=True)
                continue
            try:
                resp = canopy_bot._post("/chance", {
                    "type": "dice", "value": [ev.die1, ev.die2]
                })
                canopy_bot._pending_chance = resp.get("pending_chance")
                if verbose:
                    print(f"  /chance dice [{ev.die1},{ev.die2}] → phase={resp.get('phase')} pending={resp.get('pending_chance')}", flush=True)
            except Exception as e:
                if verbose:
                    print(f"  WARNING: canopy /chance dice failed: {e}", flush=True)
                canopy_bot._pending_chance = None
        if ev.type == "resourceStolen" and ev.resource is not None:
            if canopy_bot._pending_chance != "steal":
                if verbose:
                    print(f"  SKIP /chance steal: pending={canopy_bot._pending_chance}", flush=True)
                continue
            try:
                resp = canopy_bot._post("/chance", {
                    "type": "steal", "resource": ev.resource.value
                })
                canopy_bot._pending_chance = resp.get("pending_chance")
                if verbose:
                    print(f"  /chance steal {ev.resource.value} → phase={resp.get('phase')} pending={resp.get('pending_chance')}", flush=True)
            except Exception as e:
                if verbose:
                    print(f"  WARNING: canopy /chance steal failed: {e}", flush=True)
                canopy_bot._pending_chance = None
        if ev.type == "devCardBought" and ev.card is not None:
            if canopy_bot._pending_chance != "dev_card":
                if verbose:
                    print(f"  SKIP /chance dev_card: pending={canopy_bot._pending_chance}", flush=True)
                continue
            try:
                resp = canopy_bot._post("/chance", {
                    "type": "dev_card", "card": _dev_card_to_canopy(ev.card)
                })
                canopy_bot._pending_chance = resp.get("pending_chance")
                if verbose:
                    print(f"  /chance dev_card {ev.card} → phase={resp.get('phase')} pending={resp.get('pending_chance')}", flush=True)
            except Exception as e:
                if verbose:
                    print(f"  WARNING: canopy /chance devcard failed: {e}", flush=True)
                canopy_bot._pending_chance = None


def _resolve_canopy_pending(engine: OpenHexEngine, state: GameState,
                            canopy_bot: CanopyBot, canopy_color: PlayerColor,
                            events: list, rng: random.Random,
                            verbose: bool = False) -> GameState:
    """Resolve canopy's pending chance states using available events + auto-resolution.

    This handles chained resolutions (e.g., steal → dice after pre-roll knight).
    Returns updated OpenHex state (may have advanced if steal was auto-resolved).
    """
    for _ in range(5):  # prevent infinite loops
        pc = canopy_bot._pending_chance
        if not pc:
            break

        if pc == "dice":
            found = False
            for ev in events:
                if ev.type == "diceRolled" and ev.die1 is not None:
                    try:
                        resp = canopy_bot._post("/chance", {
                            "type": "dice", "value": [ev.die1, ev.die2]
                        })
                        canopy_bot._pending_chance = resp.get("pending_chance")
                        found = True
                        if verbose:
                            print(f"  resolve dice [{ev.die1},{ev.die2}] → phase={resp.get('phase')} pending={resp.get('pending_chance')}", flush=True)
                    except Exception as e:
                        if verbose:
                            print(f"  WARN: resolve /chance dice failed: {e}", flush=True)
                        canopy_bot._pending_chance = None
                    break
            if not found:
                break  # dice not available yet — game loop will handle via rollDice

        elif pc == "steal":
            # Try events first
            found = False
            for ev in events:
                if ev.type == "resourceStolen" and ev.resource is not None:
                    try:
                        resp = canopy_bot._post("/chance", {
                            "type": "steal", "resource": ev.resource.value
                        })
                        canopy_bot._pending_chance = resp.get("pending_chance")
                        found = True
                        if verbose:
                            print(f"  resolve steal {ev.resource.value} (from events) → phase={resp.get('phase')}", flush=True)
                    except Exception as e:
                        canopy_bot._pending_chance = None
                    break

            if not found:
                # Auto-resolve stealFromPlayer in OpenHex
                pa = state.pending_action
                if pa and pa.type == "stealFromPlayer":
                    # Use current_player (who OpenHex says should steal), not always canopy_color
                    steal_actor = state.current_player
                    targets = pa.targets or [c for c in state.turn_order if c != steal_actor]
                    if targets:
                        steal_dict = {"type": "stealFromPlayer", "target": targets[0].value}
                        new_state, err, steal_events = _apply(engine, state, steal_dict, steal_actor)
                        if err is None:
                            state = new_state
                            events = steal_events  # update for next loop iteration
                            for ev in steal_events:
                                if ev.type == "resourceStolen" and ev.resource is not None:
                                    try:
                                        resp = canopy_bot._post("/chance", {
                                            "type": "steal", "resource": ev.resource.value
                                        })
                                        canopy_bot._pending_chance = resp.get("pending_chance")
                                        found = True
                                        if verbose:
                                            print(f"  auto-resolve steal → {ev.resource.value} → phase={resp.get('phase')}", flush=True)
                                    except Exception as e:
                                        canopy_bot._pending_chance = None
                                    break
                            if not found:
                                if verbose:
                                    print(f"  WARN: stealFromPlayer succeeded but no resourceStolen event", flush=True)
                                canopy_bot._pending_chance = None
                        else:
                            if verbose:
                                print(f"  WARN: auto-resolve stealFromPlayer failed: {err}", flush=True)
                            canopy_bot._pending_chance = None
                    else:
                        canopy_bot._pending_chance = None
                else:
                    # OpenHex has no steal pending — canopy/OpenHex mismatch, force clear
                    if verbose:
                        print(f"  WARN: canopy wants steal but OpenHex has no stealFromPlayer pending", flush=True)
                    canopy_bot._pending_chance = None

        elif pc == "dev_card":
            found = False
            for ev in events:
                if ev.type == "devCardBought" and ev.card is not None:
                    try:
                        resp = canopy_bot._post("/chance", {
                            "type": "dev_card", "card": _dev_card_to_canopy(ev.card)
                        })
                        canopy_bot._pending_chance = resp.get("pending_chance")
                        found = True
                    except Exception as e:
                        canopy_bot._pending_chance = None
                    break
            if not found:
                canopy_bot._pending_chance = None
                break
        else:
            canopy_bot._pending_chance = None
            break

    return state


def _sync_canopy(canopy_bot: CanopyBot, action_dict: dict, actor: PlayerColor,
                 events: list, verbose: bool = False) -> None:
    """Notify canopy about an action (from either player) with chance data."""
    d = dict(action_dict)
    d["player"] = actor.value
    try:
        resp = canopy_bot.apply_action(d, events)
        if verbose:
            print(f"  /apply {d.get('type')} → phase={resp.get('phase')} pending={resp.get('pending_chance')}", flush=True)
    except Exception as e:
        print(f"  WARNING: canopy /apply failed for {json.dumps(d)}: {e}", flush=True)


def play_one_game(bots: dict[PlayerColor, Bot], seed: int, max_actions: int = 2000,
                  verbose: bool = False, log_path: str | None = None) -> GameOutcome:
    rng = random.Random(seed)
    engine = OpenHexEngine(rng=rng)

    config = GameConfig(
        victory_points=15, discard_limit=9, num_players=2,
        robber_mode=RobberMode.FRIENDLY, dice_mode=DiceMode.BALANCED,
    )
    colors = [PlayerColor.RED, PlayerColor.BLUE]

    # Find canopy bot if any (to use its board and sync state)
    canopy_bot: CanopyBot | None = None
    canopy_color: PlayerColor | None = None
    for c, b in bots.items():
        if isinstance(b, CanopyBot):
            canopy_bot = b
            canopy_color = c
            break

    if canopy_bot is not None:
        # Use canopy's board generation + fixed turn order (canopy always does red first)
        game_data = canopy_bot.new_game(seed)
        board = _board_from_canopy_json(game_data["board"])
        # Don't shuffle — canopy expects RED first
    else:
        board = generate_standard_board(seed)
        # Randomize seat order when no canopy bot
        turn_rng = random.Random(seed + 1)
        turn_rng.shuffle(colors)

    state = GameState.new_game(config, board, colors)

    t_start = time.time()
    action_log: list[dict] = [] if log_path else []  # only keep if logging
    actions_done = 0

    while state.winner is None and actions_done < max_actions:
        actor = _who_acts(state)
        if actor is None:
            return GameOutcome(
                winner=None, actions=actions_done, turns=state.turn_number,
                red_vp=state.compute_vp(PlayerColor.RED),
                blue_vp=state.compute_vp(PlayerColor.BLUE),
                elapsed_s=time.time() - t_start, reason="error")

        bot = bots[actor]
        is_canopy_turn = isinstance(bot, CanopyBot)

        try:
            raw_action = bot.pick_action(state, actor)
        except Exception as e:
            if verbose:
                print(f"  [{actions_done}] {actor.value} bot error: {e}", flush=True)
            if state.dice is None and not state.is_setup_phase:
                raw_action = {"type": "rollDice"}
            else:
                raw_action = {"type": "endTurn"}

        # Handle canopy needing chance data
        if raw_action.get("type") == "_needs_chance":
            chance_type = raw_action.get("chance_type")
            if chance_type == "dice" and state.dice is not None:
                # Re-try with dice data available
                raw_action = bot.pick_action(state, actor)
            elif chance_type == "steal":
                # Canopy needs steal resolution — auto-generate stealFromPlayer
                pa = state.pending_action
                if pa and pa.type == "stealFromPlayer":
                    targets = pa.targets or [c for c in state.turn_order if c != actor]
                    if targets:
                        raw_action = {"type": "stealFromPlayer", "target": targets[0].value}
                        if verbose:
                            print(f"  [{actions_done}] auto-steal for canopy → {targets[0].value}", flush=True)
                    else:
                        raw_action = {"type": "endTurn"}
                else:
                    if verbose:
                        print(f"  [{actions_done}] canopy needs steal but no stealFromPlayer pending", flush=True)
                    # Force-clear canopy's pending and continue
                    canopy_bot._pending_chance = None
                    if state.dice is None and not state.is_setup_phase:
                        raw_action = {"type": "rollDice"}
                    else:
                        raw_action = {"type": "endTurn"}
            else:
                if verbose:
                    print(f"  [{actions_done}] canopy needs_chance={chance_type}, using fallback",
                          flush=True)
                if state.dice is None and not state.is_setup_phase:
                    raw_action = {"type": "rollDice"}
                else:
                    raw_action = {"type": "endTurn"}

        new_state, err, events = _apply(engine, state, raw_action, actor)

        # Retry a few times for non-canopy bots
        retries = 0
        while err is not None and retries < 8:
            try:
                raw_action = bot.pick_action(state, actor)
            except Exception:
                break
            if raw_action.get("type") == "_needs_chance":
                break
            new_state, err, events = _apply(engine, state, raw_action, actor)
            retries += 1

        if err is not None:
            if verbose:
                print(f"  [{actions_done}] {actor.value} REJECTED {raw_action.get('type')}: {err}",
                      flush=True)
            fallback_ok = False
            for fb in _fallback_candidates(state, actor, rng):
                new_state, err, events = _apply(engine, state, fb, actor)
                if err is None:
                    raw_action = fb
                    if verbose:
                        print(f"  [{actions_done}] {actor.value} fallback {fb['type']}", flush=True)
                    fallback_ok = True
                    break
            if not fallback_ok:
                if verbose:
                    print(f"  Aborting game: no valid fallback for {actor.value}", flush=True)
                return GameOutcome(
                    winner=None, actions=actions_done, turns=state.turn_number,
                    red_vp=state.compute_vp(PlayerColor.RED),
                    blue_vp=state.compute_vp(PlayerColor.BLUE),
                    elapsed_s=time.time() - t_start, reason="error")
        else:
            if verbose and actions_done % 100 == 0:
                print(f"  [{actions_done}] {actor.value} {raw_action.get('type')} "
                      f"(VP R={state.compute_vp(PlayerColor.RED)} "
                      f"B={state.compute_vp(PlayerColor.BLUE)})", flush=True)

        # Sync canopy with the action + chance outcomes
        if canopy_bot is not None:
            if is_canopy_turn:
                # Canopy already applied the action. Feed back chance data via /chance.
                if events:
                    _inject_chance_events(canopy_bot, events, verbose)
                # Resolve any remaining pending chance (e.g., steal after moveRobber)
                if canopy_bot._pending_chance:
                    new_state = _resolve_canopy_pending(
                        engine, new_state, canopy_bot, canopy_color, events, rng, verbose)
            else:
                # Opponent action: tell canopy about it via /apply (includes chance data)
                _sync_canopy(canopy_bot, raw_action, actor, events, verbose)
                # If canopy still has unresolved pending chance, resolve it
                if canopy_bot._pending_chance:
                    new_state = _resolve_canopy_pending(
                        engine, new_state, canopy_bot, canopy_color, events, rng, verbose)

            # After every action, sync canopy's state with OpenHex ground truth
            # to prevent resource/dev-card divergence from accumulating.
            canopy_bot.sync_state(new_state)

        if log_path:
            action_log.append({**raw_action, "player": actor.value})

        state = new_state  # type: ignore[assignment]
        actions_done += 1

    outcome = GameOutcome(
        winner=state.winner,
        actions=actions_done,
        turns=state.turn_number,
        red_vp=state.compute_vp(PlayerColor.RED),
        blue_vp=state.compute_vp(PlayerColor.BLUE),
        elapsed_s=time.time() - t_start,
        reason="win" if state.winner else "max_actions",
    )

    if log_path:
        with open(log_path, "w") as f:
            json.dump({
                "seed": seed,
                "winner": state.winner.value if state.winner else None,
                "actions": action_log,
                "finalState": to_json(state),
            }, f, indent=2, default=str)

    return outcome


# ---------------------------------------------------------------------------
# Tournament
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Local 1v1 OpenHex runner")
    parser.add_argument("--red", required=True, help="Red bot spec (random | canopy:URL)")
    parser.add_argument("--blue", required=True, help="Blue bot spec")
    parser.add_argument("--games", type=int, default=1, help="Number of games")
    parser.add_argument("--seed", type=int, default=42, help="Base seed")
    parser.add_argument("--max-actions", type=int, default=2000,
                        help="Abort a game if it exceeds this action count")
    parser.add_argument("--verbose", action="store_true", help="Per-action logging")
    parser.add_argument("--log-dir", type=str, default=None,
                        help="If set, write per-game JSON logs here")
    parser.add_argument("--swap-seats", action="store_true",
                        help="Alternate red/blue bot each game to control for seat bias")
    args = parser.parse_args()

    red = make_bot(args.red, name="red")
    blue = make_bot(args.blue, name="blue")

    if args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)

    print(f"Red:  {args.red}")
    print(f"Blue: {args.blue}")
    print(f"Games: {args.games}")
    print()

    wins = {"red": 0, "blue": 0, "none": 0}
    bot_wins = {args.red: 0, args.blue: 0}
    total_actions = 0
    total_time = 0.0

    for i in range(args.games):
        seed = args.seed + i

        # Optionally swap which bot plays which color to cancel seat bias.
        if args.swap_seats and (i % 2 == 1):
            bots = {PlayerColor.RED: blue, PlayerColor.BLUE: red}
            red_spec, blue_spec = args.blue, args.red
        else:
            bots = {PlayerColor.RED: red, PlayerColor.BLUE: blue}
            red_spec, blue_spec = args.red, args.blue

        log_path = None
        if args.log_dir:
            log_path = os.path.join(args.log_dir, f"game_{i:04d}_seed{seed}.json")

        t0 = time.time()
        outcome = play_one_game(
            bots=bots, seed=seed, max_actions=args.max_actions,
            verbose=args.verbose, log_path=log_path,
        )
        dt = time.time() - t0
        total_actions += outcome.actions
        total_time += dt

        if outcome.winner is None:
            wins["none"] += 1
            winner_str = f"none ({outcome.reason})"
        elif outcome.winner == PlayerColor.RED:
            wins["red"] += 1
            bot_wins[red_spec] += 1
            winner_str = f"red  ({red_spec})"
        else:
            wins["blue"] += 1
            bot_wins[blue_spec] += 1
            winner_str = f"blue ({blue_spec})"

        print(f"  [{i+1:>3}/{args.games}] seed={seed:<6} winner={winner_str}  "
              f"actions={outcome.actions:<4} turns={outcome.turns:<3} "
              f"vp R={outcome.red_vp} B={outcome.blue_vp}  time={dt:.1f}s",
              flush=True)

    print()
    print("=== Results ===")
    print(f"  Red wins:  {wins['red']}/{args.games} ({100*wins['red']/args.games:.1f}%)")
    print(f"  Blue wins: {wins['blue']}/{args.games} ({100*wins['blue']/args.games:.1f}%)")
    if wins["none"]:
        print(f"  No winner: {wins['none']}/{args.games} (timeouts / errors)")
    if args.swap_seats:
        print()
        print("=== Per-bot (seats swapped) ===")
        for spec, w in bot_wins.items():
            print(f"  {spec}:  {w}/{args.games} ({100*w/args.games:.1f}%)")
    print()
    print(f"Total actions: {total_actions}  "
          f"({total_actions/max(1,total_time):.0f}/sec across {total_time:.1f}s)")


if __name__ == "__main__":
    main()
