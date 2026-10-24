"""Game lifecycle management — create, process actions, complete (Firestore-backed)."""

from __future__ import annotations

import json
import random
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from firebase_admin import firestore
from google.cloud.firestore_v1 import FieldFilter

from google.cloud import storage as gcs

from openhex import (
    Action, DiceMode, GameConfig, GameState, OpenHexEngine, PlayerColor,
    RobberMode, generate_standard_board, to_json, from_json,
)
from openhex.json_codec import _action_from_json, _action_to_json, _event_to_json, _config_from_json, _board_from_json, _event_from_json, game_log_to_json, resolve_action_indices

import config as cfg
from services.elo import compute_1v1_updates, compute_4p_updates
from services.hidden_info import filter_event_for_player, public_event


def _get_db():
    return firestore.client()


# ---------------------------------------------------------------------------
# Private documents
# ---------------------------------------------------------------------------
# The Firestore rules let anyone read `games/{id}` and its `events`, which the
# website relies on. Everything a seat must not see therefore lives where the
# rules deny reads: `games/{id}/private/state` holds the seeds and the full
# state (every hand, the dev deck, the dice deck), `games/{id}/privateEvents`
# the events with the card bought and the resource stolen. The public
# `events` carry the spectator view. A game created before this split
# (no `privateEvents` flag) keeps its full events in `events`; its state and
# seed move to the private document on its next action.
#
# The board seed (`seed`) can be found again by trying seeds until one makes
# the public board, so it only lays out the board and the seat order. Dice,
# dev card draws and steals come from `engineSeed`, 128 random bits that never
# leave the private document. A game created before `engineSeed` gets one on
# its next action.

def _private_state_ref(game_ref):
    return game_ref.collection("private").document("state")


def _new_engine_seed() -> str:
    return secrets.token_hex(16)


def _engine_rng(engine_seed: str, action_count: int) -> random.Random:
    # A str seed is hashed with SHA-512, so neighbouring action counts give
    # unrelated streams.
    return random.Random(f"{engine_seed}:{action_count}")


def load_state_json(game_id: str, game: dict, db=None) -> str:
    """The game's full state as JSON text, from the private document."""
    db = db or _get_db()
    snap = _private_state_ref(db.collection("games").document(game_id)).get()
    if snap.exists and snap.to_dict().get("stateJson"):
        return snap.to_dict()["stateJson"]
    return game["stateJson"]


def event_docs(game_id: str, game: dict, after: int = -1, db=None) -> list[dict]:
    """Event documents carrying the full event JSON, in sequence order."""
    db = db or _get_db()
    name = "privateEvents" if game.get("privateEvents") else "events"
    query = (
        db.collection("games").document(game_id).collection(name)
        .where(filter=FieldFilter("seq", ">", after))
        .order_by("seq")
    )
    return [doc.to_dict() for doc in query.stream()]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deadline_iso(seconds: int = cfg.ACTION_TIMEOUT_SECONDS) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# Game creation
# ---------------------------------------------------------------------------

def create_game(mode: str, bot_ids: list[str], seed: int | None = None) -> str:
    """Create a new game, return game_id."""
    db = _get_db()

    if seed is None:
        seed = random.randint(0, 2**31)

    if mode == "1v1":
        game_config = GameConfig(
            victory_points=15, discard_limit=9, num_players=2,
            robber_mode=RobberMode.FRIENDLY, dice_mode=DiceMode.BALANCED,
        )
        colors = [PlayerColor.RED, PlayerColor.BLUE]
    else:
        game_config = GameConfig(
            victory_points=10, discard_limit=7, num_players=4,
            robber_mode=RobberMode.NORMAL, dice_mode=DiceMode.RANDOM,
        )
        colors = [PlayerColor.RED, PlayerColor.BLUE, PlayerColor.WHITE, PlayerColor.ORANGE]

    rng = random.Random(seed)
    shuffled_colors = list(colors)
    rng.shuffle(shuffled_colors)

    board = generate_standard_board(seed)
    turn_order = shuffled_colors[:len(bot_ids)]
    state = GameState.new_game(game_config, board, turn_order)

    game_id = uuid.uuid4().hex[:16]

    # Look up bot names for denormalization
    bot_names = {}
    for bid in bot_ids:
        doc = db.collection("bots").document(bid).get()
        if doc.exists:
            bot_names[bid] = doc.to_dict().get("name", "?")

    players = []
    for seat, (bot_id, color) in enumerate(zip(bot_ids, shuffled_colors)):
        players.append({
            "botId": bot_id,
            "name": bot_names.get(bot_id, "?"),
            "color": color.value,
            "seat": seat,
        })

    now = _now_iso()
    game_doc = {
        "mode": mode,
        "status": "active",
        "actionCount": 0,
        "actionDeadline": _deadline_iso(),
        "winnerBotId": None,
        "winnerName": None,
        "createdAt": now,
        "completedAt": None,
        "playerBotIds": bot_ids,
        "players": players,
        "configJson": json.dumps(to_json(game_config)),
        "boardJson": json.dumps(to_json(board)),
        "turnOrderJson": json.dumps([c.value for c in turn_order]),
        "currentPlayerColor": state.current_player.value if state.current_player else None,
        "turnNumber": state.turn_number,
        "privateEvents": True,
    }

    game_ref = db.collection("games").document(game_id)
    batch = db.batch()
    batch.set(game_ref, game_doc)
    batch.set(_private_state_ref(game_ref), {
        "seed": seed,
        "engineSeed": _new_engine_seed(),
        "stateJson": json.dumps(to_json(state)),
    })
    batch.commit()
    return game_id


# ---------------------------------------------------------------------------
# Action processing
# ---------------------------------------------------------------------------

def _log_action(db, game_id: str, bot_id: str, bot_name: str,
                action_dict: dict, success: bool, error: str | None = None,
                duration_ms: int | None = None, auto: bool = False):
    entry: dict = {
        "botId": bot_id,
        "botName": bot_name,
        "actionJson": json.dumps(action_dict),
        "success": success,
        "error": error,
        "createdAt": _now_iso(),
    }
    if duration_ms is not None:
        entry["durationMs"] = duration_ms
    if auto:
        entry["auto"] = True   # made by the server for a seat past its deadline
    db.collection("games").document(game_id).collection("actionLog").add(entry)


def process_action(game_id: str, bot_id: str, bot_name: str, action_dict: dict,
                    *, duration_ms: int | None = None, auto: bool = False) -> dict:
    """Process a submitted action using a Firestore transaction for concurrency.

    `auto` marks a move the server makes for a seat past its deadline
    (`check_timeouts`); a move of the seat's own clears its timeout strikes."""
    action_dict = resolve_action_indices(action_dict)

    # Sanitize duration_ms: must be a non-negative integer
    if duration_ms is not None:
        try:
            duration_ms = int(duration_ms)
            if duration_ms < 0:
                duration_ms = None
        except (TypeError, ValueError):
            duration_ms = None

    db = _get_db()
    game_ref = db.collection("games").document(game_id)

    @firestore.transactional
    def _transact(transaction):
        game_snap = game_ref.get(transaction=transaction)
        if not game_snap.exists:
            return {"success": False, "events": [], "error": "Game not found"}

        game = game_snap.to_dict()
        private_ref = _private_state_ref(game_ref)
        private_snap = private_ref.get(transaction=transaction)
        private = private_snap.to_dict() if private_snap.exists else {}
        if game["status"] != "active":
            return {"success": False, "events": [], "error": "Game is not active"}

        # Find bot's color
        bot_color = None
        for p in game["players"]:
            if p["botId"] == bot_id:
                bot_color = p["color"]
                break
        if bot_color is None:
            return {"success": False, "events": [], "error": "You are not in this game"}

        # Inject player color
        action_dict_copy = dict(action_dict)
        action_dict_copy["player"] = bot_color

        # Deserialize state
        state = from_json(json.loads(private.get("stateJson") or game["stateJson"]), GameState)

        # Deserialize action
        try:
            action = _action_from_json(action_dict_copy)
        except Exception as e:
            return {"success": False, "events": [], "error": f"Invalid action: {e}",
                    "_log": ("fail", action_dict_copy)}

        # Execute with the secret engine seed, never the board seed
        action_count = game["actionCount"]
        seed = private.get("seed", game.get("seed"))
        engine_seed = private.get("engineSeed") or _new_engine_seed()
        engine = OpenHexEngine(rng=_engine_rng(engine_seed, action_count))
        result = engine.execute_action(state, action)

        if not result.success:
            return {"success": False, "events": [], "error": result.error,
                    "_log": ("fail", action_dict_copy)}

        new_state = result.state
        new_action_count = action_count + 1

        # Get current max seq from game doc (track in game doc to avoid subcollection query in transaction)
        next_seq = game.get("nextEventSeq", 0)

        # Build event entries (will write outside transaction)
        event_data_list = []
        event_entries = []
        for i, event in enumerate(result.events):
            event_json = _event_to_json(event)
            seq = next_seq + i
            event_data_list.append({
                "seq": seq,
                "eventJson": json.dumps(event_json),
                "actionJson": json.dumps(action_dict_copy) if i == 0 else None,
                "createdAt": _now_iso(),
            })
            filtered = filter_event_for_player(event_json, bot_color)
            event_entries.append({"seq": seq, "event": filtered})

        # Determine game outcome
        deadline = _deadline_iso() if new_state.winner is None else None
        status = "completed" if new_state.winner is not None else "active"
        winner_bot_id = None
        winner_name = None
        completed_at = None

        if new_state.winner is not None:
            completed_at = _now_iso()
            for p in game["players"]:
                if p["color"] == new_state.winner.value:
                    winner_bot_id = p["botId"]
                    winner_name = p["name"]
                    break

        # Update duration stats (running totals on game doc)
        dur_stats = game.get("durationStats") or {}
        if duration_ms is not None:
            bot_stats = dur_stats.get(bot_id) or {"totalMs": 0, "count": 0, "maxMs": 0}
            bot_stats["totalMs"] = bot_stats["totalMs"] + duration_ms
            bot_stats["count"] = bot_stats["count"] + 1
            if duration_ms > bot_stats["maxMs"]:
                bot_stats["maxMs"] = duration_ms
            dur_stats[bot_id] = bot_stats

        # Update game document; the state and seed go to the private document
        update = {
            "actionCount": new_action_count,
            "actionDeadline": deadline,
            "status": status,
            "winnerBotId": winner_bot_id,
            "winnerName": winner_name,
            "completedAt": completed_at,
            "currentPlayerColor": new_state.current_player.value if new_state.current_player else None,
            "turnNumber": new_state.turn_number,
            "nextEventSeq": next_seq + len(result.events),
            "durationStats": dur_stats,
        }
        for public_field in ("stateJson", "seed"):
            if public_field in game:
                update[public_field] = firestore.DELETE_FIELD
        # A move the seat owed clears its strikes; an answer to another
        # seat's offer does not, or a bot that misses its own turns but
        # answers offers would never strike out.
        strikes = game.get("timeoutStrikes") or {}
        owed = bot_color in {c.value for c in state.players_to_act()}
        if not auto and owed and strikes.get(bot_id):
            update["timeoutStrikes"] = {**strikes, bot_id: 0}
        transaction.update(game_ref, update)
        transaction.set(private_ref, {
            "seed": seed,
            "engineSeed": engine_seed,
            "stateJson": json.dumps(to_json(new_state)),
        })

        return {
            "success": True,
            "events": event_entries,
            "error": None,
            "_event_data": event_data_list,
            "_split_events": bool(game.get("privateEvents")),
            "_log": ("ok", action_dict_copy),
            "_completed": status == "completed",
            "_game_id": game_id,
            "_winner_bot_id": winner_bot_id,
            "_duration_ms": duration_ms,
        }

    transaction = db.transaction()
    result = _transact(transaction)

    # Write events and action log outside the transaction (non-critical)
    event_data = result.pop("_event_data", [])
    split_events = result.pop("_split_events", False)
    log_info = result.pop("_log", None)
    completed = result.pop("_completed", False)
    g_id = result.pop("_game_id", game_id)
    winner_bid = result.pop("_winner_bot_id", None)
    dur_ms = result.pop("_duration_ms", None)

    for ed in event_data:
        seq_str = f"{ed['seq']:08d}"
        if split_events:
            game_ref.collection("privateEvents").document(seq_str).set(ed)
            ed = dict(ed, eventJson=json.dumps(public_event(json.loads(ed["eventJson"]))))
        game_ref.collection("events").document(seq_str).set(ed)

    if log_info:
        success = log_info[0] == "ok"
        error = result.get("error") if not success else None
        _log_action(db, game_id, bot_id, bot_name, log_info[1], success, error,
                    duration_ms=dur_ms if success else None, auto=auto)

    if completed and winner_bid:
        _update_ratings(game_id)
        _update_head_to_head(game_id)
        _save_replay_to_gcs(game_id)

    return {"success": result["success"], "events": result["events"], "error": result["error"]}


# ---------------------------------------------------------------------------
# Rating updates
# ---------------------------------------------------------------------------

def _update_ratings(game_id: str) -> None:
    db = _get_db()
    game_doc = db.collection("games").document(game_id).get()
    if not game_doc.exists:
        return
    game = game_doc.to_dict()
    mode = game["mode"]
    winner_bot_id = game["winnerBotId"]
    players = game["players"]

    elo_key = "elo1v1" if mode == "1v1" else "elo4p"
    games_key = "games1v1" if mode == "1v1" else "games4p"
    wins_key = "wins1v1" if mode == "1v1" else "wins4p"
    # The leaderboard hides bots whose last game is more than 30 days old
    last_game_at = game.get("completedAt") or _now_iso()

    # Read current bot ratings
    bot_docs = {}
    for p in players:
        doc = db.collection("bots").document(p["botId"]).get()
        if doc.exists:
            bot_docs[p["botId"]] = doc.to_dict()

    if mode == "1v1" and len(players) == 2:
        p0, p1 = players[0], players[1]
        r0 = bot_docs.get(p0["botId"], {}).get(elo_key, cfg.INITIAL_ELO)
        r1 = bot_docs.get(p1["botId"], {}).get(elo_key, cfg.INITIAL_ELO)

        if p0["botId"] == winner_bot_id:
            new_r0, new_r1 = compute_1v1_updates(r0, r1)
        else:
            new_r1, new_r0 = compute_1v1_updates(r1, r0)

        for p, opp in [(p0, p1), (p1, p0)]:
            is_winner = p["botId"] == winner_bot_id
            new_r = new_r0 if p is p0 else new_r1
            bot_data = bot_docs.get(p["botId"], {})
            db.collection("bots").document(p["botId"]).update({
                elo_key: new_r,
                games_key: bot_data.get(games_key, 0) + 1,
                wins_key: bot_data.get(wins_key, 0) + (1 if is_winner else 0),
                "lastOpponentId": opp["botId"],
                "lastGameAt": last_game_at,
            })
    else:
        # 4p
        state = from_json(json.loads(load_state_json(game_id, game, db)), GameState)
        seat_vp = []
        for p in players:
            color = PlayerColor(p["color"])
            vp = state.compute_vp(color)
            seat_vp.append((p["seat"], vp, p["botId"]))

        seat_vp.sort(key=lambda x: (-1 if x[2] == winner_bot_id else 0, -x[1]))
        finishing_order = [x[0] for x in seat_vp]

        ratings = []
        for p in sorted(players, key=lambda x: x["seat"]):
            ratings.append(bot_docs.get(p["botId"], {}).get(elo_key, cfg.INITIAL_ELO))

        new_ratings = compute_4p_updates(ratings, finishing_order)
        sorted_players = sorted(players, key=lambda x: x["seat"])

        for p, new_r in zip(sorted_players, new_ratings):
            is_winner = p["botId"] == winner_bot_id
            bot_data = bot_docs.get(p["botId"], {})
            db.collection("bots").document(p["botId"]).update({
                elo_key: new_r,
                games_key: bot_data.get(games_key, 0) + 1,
                wins_key: bot_data.get(wins_key, 0) + (1 if is_winner else 0),
                "lastGameAt": last_game_at,
            })


# ---------------------------------------------------------------------------
# Head-to-head stats
# ---------------------------------------------------------------------------

def _update_head_to_head(game_id: str) -> None:
    """Update head-to-head record after a completed/aborted game."""
    db = _get_db()
    game_doc = db.collection("games").document(game_id).get()
    if not game_doc.exists:
        return
    game = game_doc.to_dict()
    if game["mode"] != "1v1" or len(game["players"]) != 2:
        return

    winner_bot_id = game["winnerBotId"]
    p0, p1 = game["players"][0], game["players"][1]  # seat 0, seat 1

    # Doc ID: alphabetically sorted bot IDs
    ids = sorted([p0["botId"], p1["botId"]])
    doc_id = f"{ids[0]}__{ids[1]}"

    h2h_ref = db.collection("head-to-head").document(doc_id)
    h2h_doc = h2h_ref.get()

    if h2h_doc.exists:
        data = h2h_doc.to_dict()
    else:
        data = {
            "bot1Id": ids[0],
            "bot1Name": "",
            "bot2Id": ids[1],
            "bot2Name": "",
            "bot1FirstWins": 0,   # bot1 placed first (seat 0) and won
            "bot1FirstLosses": 0, # bot1 placed first and lost
            "bot2FirstWins": 0,   # bot2 placed first (seat 0) and won
            "bot2FirstLosses": 0, # bot2 placed first and lost
            "totalGames": 0,
        }

    # Determine who placed first (seat 0)
    first_bot_id = p0["botId"]
    first_bot_name = p0["name"]
    second_bot_id = p1["botId"]
    second_bot_name = p1["name"]

    # Update names (may change)
    if first_bot_id == ids[0]:
        data["bot1Name"] = first_bot_name
        data["bot2Name"] = second_bot_name
    else:
        data["bot1Name"] = second_bot_name
        data["bot2Name"] = first_bot_name

    # Who placed first, who won?
    first_won = (winner_bot_id == first_bot_id)

    if first_bot_id == ids[0]:
        # bot1 placed first
        if first_won:
            data["bot1FirstWins"] += 1
        else:
            data["bot1FirstLosses"] += 1
    else:
        # bot2 placed first
        if first_won:
            data["bot2FirstWins"] += 1
        else:
            data["bot2FirstLosses"] += 1

    data["totalGames"] += 1
    data["lastGameAt"] = _now_iso()

    h2h_ref.set(data)


# ---------------------------------------------------------------------------
# Replay saving to GCS
# ---------------------------------------------------------------------------

def _save_replay_to_gcs(game_id: str) -> None:
    """Save the full replay JSON to GCS after a game completes."""
    try:
        db = _get_db()
        game_doc = db.collection("games").document(game_id).get()
        if not game_doc.exists:
            return
        game = game_doc.to_dict()

        config = _config_from_json(json.loads(game["configJson"]))
        board = _board_from_json(json.loads(game["boardJson"]))

        events = [_event_from_json(json.loads(edata["eventJson"]))
                  for edata in event_docs(game_id, game, db=db)]

        turn_order = [PlayerColor(c) for c in json.loads(game["turnOrderJson"])]
        game_log = game_log_to_json(config, board, turn_order, events)

        # Add metadata
        game_log["gameId"] = game_id
        game_log["players"] = game["players"]
        game_log["status"] = game["status"]
        game_log["winnerBotId"] = game.get("winnerBotId")
        game_log["completedAt"] = game.get("completedAt")
        if game.get("durationStats"):
            game_log["durationStats"] = game["durationStats"]

        client = gcs.Client()
        bucket = client.bucket(cfg.GCS_REPLAY_BUCKET)
        blob = bucket.blob(f"replays/{game_id}.json")
        blob.upload_from_string(json.dumps(game_log), content_type="application/json")
    except Exception as e:
        print(f"Failed to save replay to GCS for {game_id}: {e}")


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------

def check_timeouts() -> int:
    """Act for the seats past their action deadline, as colonist.io does: the
    move the game waits on is made for the seat (`OpenHexEngine.default_action`)
    and a strike recorded; `cfg.TIMEOUT_STRIKES` strikes in a row, or no
    legal move to make, abort the game with that seat at fault. A move of
    the seat's own clears its strikes (`process_action`). Returns the count
    of games aborted."""
    db = _get_db()
    now = _now_iso()

    overdue = (
        db.collection("games")
        .where(filter=FieldFilter("status", "==", "active"))
        .where(filter=FieldFilter("actionDeadline", "<", now))
        .stream()
    )

    count = 0
    for game_snap in overdue:
        game = game_snap.to_dict()
        game_id = game_snap.id
        game_ref = db.collection("games").document(game_id)

        state = from_json(json.loads(load_state_json(game_id, game, db)), GameState)
        # The seats the game is waiting on are at fault: the current player,
        # or during a discard the players still to discard. (The roller of a
        # 7 must not lose the game because the other seat sat on its discard.)
        overdue_colors = [c.value for c in state.players_to_act()]
        if not overdue_colors:
            continue

        seats = {p["color"]: p for p in game["players"]}
        strikes = dict(game.get("timeoutStrikes") or {})
        struck_out: set[str] = set()
        for color in overdue_colors:
            seat = seats[color]
            strikes[seat["botId"]] = strikes.get(seat["botId"], 0) + 1
            if strikes[seat["botId"]] >= cfg.TIMEOUT_STRIKES:
                struck_out.add(color)
                continue
            action = OpenHexEngine().default_action(state, PlayerColor(color))
            if action is None:
                struck_out.add(color)
                continue
            result = process_action(game_id, seat["botId"], seat["name"],
                                    _action_to_json(action), auto=True)
            if not result["success"]:
                struck_out.add(color)
                continue
            # The move changed the game (another seat may still owe a discard)
            game = game_ref.get().to_dict()
            state = from_json(json.loads(load_state_json(game_id, game, db)), GameState)

        if game["status"] != "active":
            continue   # the move made for the seat ended the game
        if not struck_out:
            game_ref.update({"timeoutStrikes": strikes})
            continue

        # Find winner: the highest VP among the seats not at fault
        other_players = [p for p in game["players"] if p["color"] not in struck_out]
        if not other_players:
            other_players = list(game["players"])
        best = max(other_players, key=lambda p: state.compute_vp(PlayerColor(p["color"])))
        winner_bot_id = best["botId"]
        winner_name = best["name"]

        game_ref.update({
            "status": "aborted",
            "winnerBotId": winner_bot_id,
            "winnerName": winner_name,
            "completedAt": _now_iso(),
            "actionDeadline": None,
            "timeoutStrikes": strikes,
        })

        _update_ratings(game_id)
        _update_head_to_head(game_id)
        _save_replay_to_gcs(game_id)
        count += 1

    return count
