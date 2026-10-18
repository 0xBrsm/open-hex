#!/usr/bin/env python3
"""OpenHex bot daemon — polls for games and plays them through the OpenHex API.

Usage:
    python daemon.py --api-key <key> --name my_bot

The daemon plays with the random bot from local_runner.py. To run your own bot,
pass any object with `pick_action(state, my_color) -> dict` to BotClient.

Requires the openhex engine on PYTHONPATH (from the firebase/functions dir).
"""

from __future__ import annotations

import argparse
import random
import sys
import time

import requests

# Add firebase/functions to path for openhex imports
sys.path.insert(0, "../firebase/functions")

from openhex import GameState, PlayerColor, from_json
from openhex.types import HexPoint, Terrain

from local_runner import Bot, RandomBot


def _h(h: HexPoint) -> dict:
    return {"x": h.x, "y": h.y, "z": h.z}


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

API_BASE = "https://open-hex.web.app"

RETRY_DELAYS = [1, 2, 5, 10, 30]  # seconds between retries


def _request_with_retry(method: str, url: str, retries: int = 5, **kwargs) -> requests.Response:
    """HTTP request with exponential backoff on transient errors (502/503/504, timeouts)."""
    kwargs.setdefault("timeout", 30)
    last_exc = None
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, url, **kwargs)
            if r.status_code in (502, 503, 504) and attempt < retries:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                print(f"  HTTP {r.status_code} from {url.split('/')[-1]}, retry in {delay}s...", flush=True)
                time.sleep(delay)
                continue
            return r
        except (requests.ConnectionError, requests.Timeout) as e:
            last_exc = e
            if attempt < retries:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                print(f"  Connection error: {e}, retry in {delay}s...", flush=True)
                time.sleep(delay)
            else:
                raise
    raise last_exc  # unreachable but satisfies type checker


def _api_to_internal_names(state_json: dict) -> dict:
    """Convert API field names back to internal names for from_json compatibility.

    API uses: hex (tiles), edge (ports/roads), robberHex
    Internal uses: location (tiles), vertices (ports/roads), robber
    """
    board = state_json.get("board")
    if board:
        for tile in board.get("tiles", []):
            if "hex" in tile and "location" not in tile:
                tile["location"] = tile.pop("hex")
        for port in board.get("ports", []):
            if "edge" in port and "vertices" not in port:
                port["vertices"] = port.pop("edge")
        for r in board.get("roads", []):
            if "edge" in r and "vertices" not in r:
                r["vertices"] = r.pop("edge")
        if "robberHex" in board and "robber" not in board:
            board["robber"] = board.pop("robberHex")
    pa = state_json.get("pendingAction")
    if pa:
        if "hex" in pa and "location" not in pa:
            pa["location"] = pa.pop("hex")
    return state_json


# Board cache: tiles and ports are only sent during setup phase
_cached_boards: dict[str, dict] = {}  # game_id -> {"tiles": [...], "ports": [...]}


def _restore_board(game_id: str, state_json: dict, headers: dict) -> dict:
    """Cache tiles/ports from setup, restore them after setup when stripped.

    On cache miss (daemon restarted mid-game), fetches from /board endpoint.
    """
    board = state_json.get("board", {})
    if board.get("tiles"):
        # Setup phase — cache the static board
        _cached_boards[game_id] = {
            "tiles": board["tiles"],
            "ports": board.get("ports", []),
        }
    elif game_id in _cached_boards:
        # Post-setup — restore cached tiles/ports
        board["tiles"] = _cached_boards[game_id]["tiles"]
        board["ports"] = _cached_boards[game_id]["ports"]
    else:
        # No cache (daemon restarted mid-game) — fetch from board endpoint
        try:
            r = _request_with_retry("GET", f"{API_BASE}/api/games/{game_id}/board", headers=headers)
            r.raise_for_status()
            board_data = r.json().get("board", {})
            # Convert API names back to internal
            for tile in board_data.get("tiles", []):
                if "hex" in tile and "location" not in tile:
                    tile["location"] = tile.pop("hex")
            for port in board_data.get("ports", []):
                if "edge" in port and "vertices" not in port:
                    port["vertices"] = port.pop("edge")
            _cached_boards[game_id] = {
                "tiles": board_data.get("tiles", []),
                "ports": board_data.get("ports", []),
            }
            board["tiles"] = _cached_boards[game_id]["tiles"]
            board["ports"] = _cached_boards[game_id]["ports"]
            print(f"  Fetched board from /board endpoint (cache miss)", flush=True)
        except Exception as e:
            print(f"  Failed to fetch board: {e}", flush=True)
            if "tiles" not in board:
                board["tiles"] = []
            if "ports" not in board:
                board["ports"] = []
    return state_json


def _fix_filtered_state(state_json: dict) -> dict:
    deck = state_json.get("devCardDeck", {})
    if "remaining" in deck:
        remaining = deck["remaining"]
        state_json["devCardDeck"] = {"knight": remaining} if remaining > 0 else {}
    return state_json


class BotClient:
    def __init__(self, api_key: str, bot: Bot):
        self.api_key = api_key
        self.bot = bot
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def join_queue(self):
        r = requests.post(f"{API_BASE}/api/queue/join",
                          json={"mode": "1v1"}, headers=self.headers)
        return r

    def leave_queue(self):
        r = requests.post(f"{API_BASE}/api/queue/leave",
                          json={"mode": "1v1"}, headers=self.headers)
        return r

    def get_active_games(self) -> list[dict]:
        r = _request_with_retry("GET", f"{API_BASE}/api/games",
                                params={"status": "active"}, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def get_state(self, game_id: str) -> dict:
        r = _request_with_retry("GET", f"{API_BASE}/api/games/{game_id}/state",
                                headers=self.headers)
        r.raise_for_status()
        return r.json()

    def submit_action(self, game_id: str, action: dict, duration_ms: int | None = None) -> dict:
        body: dict = {"action": action}
        if duration_ms is not None:
            body["duration_ms"] = duration_ms
        r = _request_with_retry("POST", f"{API_BASE}/api/games/{game_id}/actions",
                                json=body, headers=self.headers)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def play_game(client: BotClient, game_id: str):
    """Play through a game until completion."""
    gs = client.get_state(game_id)
    my_color = gs["your_color"]
    print(f"  Playing as {my_color}", flush=True)

    max_actions = 2000
    action_count = 0
    consecutive_failures = 0
    network_errors = 0

    while action_count < max_actions:
        try:
            gs = client.get_state(game_id)
        except Exception as e:
            network_errors += 1
            if network_errors > 10:
                print(f"  Too many network errors ({network_errors}), abandoning game", flush=True)
                return None
            print(f"  get_state error: {e}, retrying in 5s...", flush=True)
            time.sleep(5)
            continue

        network_errors = 0  # reset on success
        game_info = gs["game"]

        if game_info["status"] != "active":
            winner = game_info.get("winner")
            winner_name = winner["name"] if winner else "?"
            print(f"  Game over! Winner: {winner_name}", flush=True)
            return game_info

        current_color = game_info["current_player_color"]
        state_json = gs["state"]
        pending = state_json.get("pendingAction")

        # Handle discards (might need to act even when not current player)
        if pending and pending.get("type") == "discardResources":
            discard_players = pending.get("players", {})
            if my_color in discard_players:
                state = from_json(_fix_filtered_state(_restore_board(game_id, _api_to_internal_names(state_json), client.headers)), GameState)
                player = state.get_player(PlayerColor(my_color))
                hand_list = []
                for r, n in player.hand.items():
                    hand_list.extend([r] * n)
                random.shuffle(hand_list)
                to_discard = hand_list[:discard_players[my_color]]
                rm = {}
                for r in to_discard:
                    rm[r.value] = rm.get(r.value, 0) + 1
                try:
                    result = client.submit_action(game_id, {"type": "discardResources", "resources": rm})
                    if result["success"]:
                        print(f"    [{action_count}] discardResources", flush=True)
                except Exception as e:
                    print(f"    [{action_count}] discardResources network error: {e}", flush=True)
                action_count += 1
                continue
            # Not our discard — wait
            time.sleep(1)
            continue

        # Not our turn — wait
        if current_color != my_color:
            time.sleep(1)
            continue

        state = from_json(_fix_filtered_state(_restore_board(game_id, _api_to_internal_names(state_json), client.headers)), GameState)

        try:
            t0 = time.monotonic()
            action = client.bot.pick_action(state, PlayerColor(my_color))
            duration_ms = int((time.monotonic() - t0) * 1000)
        except Exception as e:
            print(f"    [{action_count}] Bot error: {e}", flush=True)
            consecutive_failures += 1
            if consecutive_failures > 20:
                print(f"  Too many failures, aborting game", flush=True)
                return None
            # Fallback: try endTurn if dice already rolled
            if state.dice is not None and state.pending_action is None:
                try:
                    fb = client.submit_action(game_id, {"type": "endTurn"})
                    if fb["success"]:
                        print(f"    [{action_count}] endTurn (bot-error fallback)", flush=True)
                        consecutive_failures = 0
                except Exception:
                    pass
            action_count += 1
            time.sleep(2)
            continue

        try:
            result = client.submit_action(game_id, action, duration_ms=duration_ms)
        except Exception as e:
            print(f"    [{action_count}] submit error: {e}", flush=True)
            consecutive_failures += 1
            if consecutive_failures > 20:
                print(f"  Too many failures, aborting game", flush=True)
                return None
            action_count += 1
            time.sleep(2)
            continue

        if result["success"]:
            extra = ""
            if action["type"] == "rollDice":
                for ev in result.get("events", []):
                    e = ev.get("event", {})
                    if e.get("type") == "diceRoll":
                        extra = f" -> {e.get('diceTotal')}"
            print(f"    [{action_count}] {action['type']}{extra}", flush=True)
            action_count += 1
            consecutive_failures = 0
        else:
            error_msg = result.get("error", "")
            consecutive_failures += 1
            if consecutive_failures <= 5:
                print(f"    [{action_count}] FAILED {action['type']} - {error_msg}", flush=True)

            # Robber fallback: try all hexes (friendly robber may reject many)
            if action["type"] in ("moveRobber", "playKnight") and "robber" in error_msg.lower():
                # Sort: desert first, then hexes with no adjacent buildings, then rest
                def _robber_priority(t):
                    if t.terrain == Terrain.DESERT:
                        return 0
                    has_building = any(
                        state.board.building_at(v) is not None
                        for v in t.location.vertices()
                    )
                    return 2 if has_building else 1
                sorted_tiles = sorted(state.board.tiles, key=_robber_priority)
                for tile in sorted_tiles:
                    if tile.location == state.board.robber:
                        continue
                    fb = dict(action)
                    fb["location"] = _h(tile.location)
                    try:
                        fb_result = client.submit_action(game_id, fb)
                        if fb_result["success"]:
                            print(f"    [{action_count}] {action['type']} (robber fallback)", flush=True)
                            consecutive_failures = 0
                            break
                    except Exception:
                        break
            elif state.dice is not None and state.pending_action is None:
                try:
                    fb = client.submit_action(game_id, {"type": "endTurn"})
                    if fb["success"]:
                        print(f"    [{action_count}] endTurn (fallback)", flush=True)
                        consecutive_failures = 0
                except Exception:
                    pass

            if consecutive_failures > 20:
                print(f"  Too many failures, aborting game", flush=True)
                return None
            action_count += 1

    print(f"  Reached max actions ({max_actions})", flush=True)
    return None


def daemon_loop(client: BotClient):
    """Main daemon: join queue, wait for match, play, repeat. Never exits."""
    print(f"Starting daemon for {client.bot.name}", flush=True)

    backoff = 0
    while True:
        try:
            # Check for active game first
            games = client.get_active_games()
            if games:
                game_id = games[0]["id"]
                print(f"\nFound active game: {game_id}", flush=True)
                play_game(client, game_id)
                backoff = 0
                print(f"Sleeping 20 min before next game...", flush=True)
                time.sleep(1200)
                continue

            # Join queue
            r = client.join_queue()
            if r.status_code == 200:
                print("Joined queue, waiting for match...", flush=True)
            elif r.status_code == 409:
                pass  # Already queued or in game
            else:
                print(f"Queue join error: {r.status_code} {r.text}", flush=True)

            backoff = 0
            # Wait for matchmaking cron (runs every 1 min)
            time.sleep(10)

        except KeyboardInterrupt:
            print("\nShutting down.", flush=True)
            return
        except Exception as e:
            backoff = min(backoff + 1, len(RETRY_DELAYS) - 1)
            delay = RETRY_DELAYS[backoff]
            print(f"\nDaemon error: {e}, restarting in {delay}s...", flush=True)
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="OpenHex bot daemon")
    parser.add_argument("--api-key", required=True, help="Bot API key")
    parser.add_argument("--name", default="bot", help="Bot display name (for logs)")
    args = parser.parse_args()

    bot = RandomBot(args.name)
    client = BotClient(args.api_key, bot)
    daemon_loop(client)


if __name__ == "__main__":
    main()
