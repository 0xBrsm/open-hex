"""OpenHex Cloud Functions — Flask API + scheduled tasks."""

from __future__ import annotations

import json
import uuid
from functools import wraps

import firebase_admin
from firebase_admin import firestore
from firebase_functions import https_fn, scheduler_fn, options
from flask import Flask, request, jsonify

from google.cloud import storage as gcs

from openhex import GameState, from_json
from openhex.types import PlayerColor
from openhex.json_codec import (
    _config_from_json, _board_from_json, _event_from_json, game_log_to_json,
    convert_state_to_indices, convert_event_to_indices, strip_static_board,
    convert_state_field_names, convert_event_field_names, normalize_action_fields,
    _hex_dict_to_index, _edge_dicts_to_index,
)

from services.game_manager import process_action, check_timeouts, create_game, _now_iso
from services.hidden_info import filter_state_for_player, filter_event_for_player
from services.matchmaker import run_matchmaking
from config import INITIAL_ELO, GCS_REPLAY_BUCKET

firebase_admin.initialize_app()

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _get_db():
    return firestore.client()


def _get_current_bot():
    """Extract bot info from Authorization: Bearer <api_key> header."""
    auth = request.headers.get("Authorization", "")
    parts = auth.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None, (jsonify({"detail": "Invalid authorization header"}), 401)
    api_key = parts[1]
    db = _get_db()
    doc = db.collection("apiKeys").document(api_key).get()
    if not doc.exists:
        return None, (jsonify({"detail": "Invalid API key"}), 401)
    return doc.to_dict(), None


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        bot, err = _get_current_bot()
        if err:
            return err
        return f(bot, *args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Bot endpoints
# ---------------------------------------------------------------------------

@app.route("/api/bots", methods=["POST"])
def register_bot():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    mode = data.get("mode", "")
    coord_fmt = data.get("coordinateFormat", "coordinates")
    description = data.get("description", "").strip()

    if not name or len(name) < 3 or len(name) > 32:
        return jsonify({"detail": "Name must be 3-32 characters"}), 400
    if mode not in ("1v1", "4p"):
        return jsonify({"detail": "Mode must be '1v1' or '4p'"}), 400
    if coord_fmt not in ("coordinates", "indices"):
        return jsonify({"detail": "coordinateFormat must be 'coordinates' or 'indices'"}), 400
    if len(description) > 280:
        return jsonify({"detail": "Description must be 280 characters or fewer"}), 400

    db = _get_db()

    # Check uniqueness: name + mode
    existing = list(
        db.collection("bots")
        .where("name", "==", name)
        .where("mode", "==", mode)
        .limit(1)
        .stream()
    )
    if existing:
        return jsonify({
            "detail": "Bot name already taken for this mode. If this is your bot, use your existing API key."
        }), 409

    bot_id = uuid.uuid4().hex[:16]
    api_key = uuid.uuid4().hex

    bot_doc = {
        "name": name,
        "mode": mode,
        "coordinateFormat": coord_fmt,
        "description": description,
        "elo1v1": INITIAL_ELO,
        "elo4p": INITIAL_ELO,
        "games1v1": 0, "wins1v1": 0,
        "games4p": 0, "wins4p": 0,
        "createdAt": _now_iso(),
    }
    db.collection("bots").document(bot_id).set(bot_doc)

    # Store API key in separate collection (document ID = key for O(1) lookup)
    db.collection("apiKeys").document(api_key).set({
        "botId": bot_id,
        "botName": name,
        "mode": mode,
        "coordinateFormat": coord_fmt,
    })

    return jsonify({
        "bot": {"id": bot_id, **_bot_response(bot_doc)},
        "api_key": api_key,
    })


def _bot_response(bot: dict) -> dict:
    resp = {
        "name": bot.get("name", ""),
        "mode": bot.get("mode", "1v1"),
        "description": bot.get("description", ""),
        "elo_1v1": bot.get("elo1v1", INITIAL_ELO),
        "elo_4p": bot.get("elo4p", INITIAL_ELO),
        "games_1v1": bot.get("games1v1", 0),
        "wins_1v1": bot.get("wins1v1", 0),
        "games_4p": bot.get("games4p", 0),
        "wins_4p": bot.get("wins4p", 0),
    }
    return resp


@app.route("/api/bots/me")
@require_auth
def get_self(bot):
    db = _get_db()
    doc = db.collection("bots").document(bot["botId"]).get()
    if not doc.exists:
        return jsonify({"detail": "Bot not found"}), 404
    return jsonify({"id": doc.id, **_bot_response(doc.to_dict())})


@app.route("/api/bots/<bot_id>")
def get_bot(bot_id):
    db = _get_db()
    doc = db.collection("bots").document(bot_id).get()
    if not doc.exists:
        return jsonify({"detail": "Bot not found"}), 404
    return jsonify({"id": doc.id, **_bot_response(doc.to_dict())})


@app.route("/api/bots/<bot_id>/head-to-head")
def get_head_to_head(bot_id):
    """Get head-to-head records for a bot against all opponents."""
    db = _get_db()

    # Query all h2h docs where this bot is bot1 or bot2
    results = []
    for field in ["bot1Id", "bot2Id"]:
        docs = db.collection("head-to-head").where(field, "==", bot_id).stream()
        for doc in docs:
            d = doc.to_dict()
            # Normalize: "me" is always the queried bot
            if d["bot1Id"] == bot_id:
                results.append({
                    "opponentId": d["bot2Id"],
                    "opponentName": d["bot2Name"],
                    "myFirstWins": d.get("bot1FirstWins", 0),
                    "myFirstLosses": d.get("bot1FirstLosses", 0),
                    "oppFirstWins": d.get("bot2FirstWins", 0),
                    "oppFirstLosses": d.get("bot2FirstLosses", 0),
                    "totalGames": d.get("totalGames", 0),
                })
            else:
                results.append({
                    "opponentId": d["bot1Id"],
                    "opponentName": d["bot1Name"],
                    "myFirstWins": d.get("bot2FirstWins", 0),
                    "myFirstLosses": d.get("bot2FirstLosses", 0),
                    "oppFirstWins": d.get("bot1FirstWins", 0),
                    "oppFirstLosses": d.get("bot1FirstLosses", 0),
                    "totalGames": d.get("totalGames", 0),
                })

    return jsonify({"botId": bot_id, "records": results})


# ---------------------------------------------------------------------------
# Queue endpoints
# ---------------------------------------------------------------------------

@app.route("/api/queue/join", methods=["POST"])
@require_auth
def join_queue(bot):
    data = request.get_json(force=True)
    mode = data.get("mode", "")
    if mode not in ("1v1", "4p"):
        return jsonify({"detail": "Mode must be '1v1' or '4p'"}), 400

    if bot["mode"] != mode:
        return jsonify({"detail": f"Bot registered for {bot['mode']}, cannot join {mode} queue"}), 403

    db = _get_db()
    bot_id = bot["botId"]

    # Check not in active game
    active = list(
        db.collection("games")
        .where("status", "==", "active")
        .where("playerBotIds", "array_contains", bot_id)
        .limit(1)
        .get()
    )
    if active:
        return jsonify({"detail": "Already in an active game for this mode"}), 409

    queue_id = f"{bot_id}_{mode}"
    queue_ref = db.collection("queue").document(queue_id)
    if queue_ref.get().exists:
        return jsonify({"detail": "Already in queue for this mode"}), 409

    queue_ref.set({
        "botId": bot_id,
        "mode": mode,
        "joinedAt": _now_iso(),
    })

    return jsonify({"queued": True, "mode": mode})


@app.route("/api/queue/leave", methods=["POST"])
@require_auth
def leave_queue(bot):
    data = request.get_json(force=True)
    mode = data.get("mode", "")
    if mode not in ("1v1", "4p"):
        return jsonify({"detail": "Mode must be '1v1' or '4p'"}), 400

    db = _get_db()
    queue_id = f"{bot['botId']}_{mode}"
    db.collection("queue").document(queue_id).delete()
    return jsonify({"left": True, "mode": mode})


@app.route("/api/queue/status")
@require_auth
def queue_status(bot):
    db = _get_db()
    bot_id = bot["botId"]
    q1v1 = db.collection("queue").document(f"{bot_id}_1v1").get().exists
    q4p = db.collection("queue").document(f"{bot_id}_4p").get().exists
    return jsonify({"queued": {"1v1": q1v1, "4p": q4p}})


# ---------------------------------------------------------------------------
# Game endpoints
# ---------------------------------------------------------------------------

def _build_game_info(game_id: str, game: dict) -> dict:
    state = from_json(json.loads(game["stateJson"]), GameState)
    winner = None
    if game.get("winnerBotId"):
        for p in game["players"]:
            if p["botId"] == game["winnerBotId"]:
                winner = p
                break
    return {
        "id": game_id,
        "mode": game["mode"],
        "status": game["status"],
        "players": game["players"],
        "current_player_color": state.current_player.value if state.current_player else None,
        "turn_number": state.turn_number,
        "action_deadline": game.get("actionDeadline"),
        "winner": winner,
    }


@app.route("/api/games")
@require_auth
def list_games(bot):
    status = request.args.get("status", "active")
    db = _get_db()

    query = (
        db.collection("games")
        .where("playerBotIds", "array_contains", bot["botId"])
        .where("status", "==", status)
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(50)
    )
    docs = list(query.stream())
    result = []
    for doc in docs:
        game = doc.to_dict()
        result.append(_build_game_info(doc.id, game))
    return jsonify(result)


@app.route("/api/games/<game_id>")
@require_auth
def get_game(bot, game_id):
    db = _get_db()
    doc = db.collection("games").document(game_id).get()
    if not doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    game = doc.to_dict()
    if bot["botId"] not in game.get("playerBotIds", []):
        return jsonify({"detail": "You are not in this game"}), 403

    return jsonify(_build_game_info(game_id, game))


@app.route("/api/games/<game_id>/state")
@require_auth
def get_game_state(bot, game_id):
    db = _get_db()
    doc = db.collection("games").document(game_id).get()
    if not doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    game = doc.to_dict()
    bot_color = None
    for p in game["players"]:
        if p["botId"] == bot["botId"]:
            bot_color = p["color"]
            break
    if bot_color is None:
        return jsonify({"detail": "You are not in this game"}), 403

    state_json = json.loads(game["stateJson"])
    filtered = filter_state_for_player(state_json, bot_color)

    # Strip static board data after setup phase
    strip_static_board(filtered)

    # Convert coordinates to indices if bot prefers (before field rename)
    coord_fmt = bot.get("coordinateFormat", "coordinates")
    if coord_fmt == "indices":
        convert_state_to_indices(filtered)

    # Rename internal field names to API names (vertices->edge, location->hex)
    convert_state_field_names(filtered)

    return jsonify({
        "game": _build_game_info(game_id, game),
        "state": filtered,
        "your_color": bot_color,
    })


@app.route("/api/games/<game_id>/board")
@require_auth
def get_game_board(bot, game_id):
    """Return the static board (tiles + ports) for a game. Always available."""
    db = _get_db()
    doc = db.collection("games").document(game_id).get()
    if not doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    game = doc.to_dict()
    if bot["botId"] not in game.get("playerBotIds", []):
        return jsonify({"detail": "You are not in this game"}), 403

    board_json = json.loads(game["boardJson"])

    # Apply field name conversion
    for tile in board_json.get("tiles", []):
        if "location" in tile and "hex" not in tile:
            tile["hex"] = tile.pop("location")
    for port in board_json.get("ports", []):
        if "vertices" in port and "edge" not in port:
            port["edge"] = port.pop("vertices")
    if "robber" in board_json and "robberHex" not in board_json:
        board_json["robberHex"] = board_json.pop("robber")

    # Convert to indices if bot prefers
    coord_fmt = bot.get("coordinateFormat", "coordinates")
    if coord_fmt == "indices":
        for tile in board_json.get("tiles", []):
            if isinstance(tile.get("hex"), dict):
                tile["hex"] = _hex_dict_to_index(tile["hex"])
        for port in board_json.get("ports", []):
            edge = port.get("edge")
            if edge and len(edge) == 2 and isinstance(edge[0], dict):
                port["edge"] = _edge_dicts_to_index(edge)
        if isinstance(board_json.get("robberHex"), dict):
            board_json["robberHex"] = _hex_dict_to_index(board_json["robberHex"])

    return jsonify({"board": board_json})


@app.route("/api/games/<game_id>/events")
@require_auth
def get_game_events(bot, game_id):
    after = int(request.args.get("after", -1))
    db = _get_db()

    # Verify bot is in game
    game_doc = db.collection("games").document(game_id).get()
    if not game_doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    game = game_doc.to_dict()
    bot_color = None
    for p in game["players"]:
        if p["botId"] == bot["botId"]:
            bot_color = p["color"]
            break
    if bot_color is None:
        return jsonify({"detail": "You are not in this game"}), 403

    # Query events with seq > after
    events_ref = db.collection("games").document(game_id).collection("events")
    query = events_ref.where("seq", ">", after).order_by("seq").stream()

    coord_fmt = bot.get("coordinateFormat", "coordinates")
    entries = []
    for edoc in query:
        edata = edoc.to_dict()
        event = json.loads(edata["eventJson"])
        filtered = filter_event_for_player(event, bot_color)
        if coord_fmt == "indices":
            convert_event_to_indices(filtered)
        convert_event_field_names(filtered)
        entries.append({"seq": edata["seq"], "event": filtered})

    return jsonify({"events": entries})


@app.route("/api/games/<game_id>/actions", methods=["POST"])
@require_auth
def submit_action(bot, game_id):
    data = request.get_json(force=True)
    action_dict = data.get("action", {})
    duration_ms = data.get("duration_ms")

    # Normalize API field names to internal names (edge->vertices, hex->location)
    action_dict = normalize_action_fields(action_dict)

    result = process_action(game_id, bot["botId"], bot["botName"], action_dict,
                            duration_ms=duration_ms)

    # Convert response events
    coord_fmt = bot.get("coordinateFormat", "coordinates")
    events = result.get("events", [])
    for entry in events:
        ev = entry.get("event")
        if ev and isinstance(ev, dict):
            if coord_fmt == "indices":
                convert_event_to_indices(ev)
            convert_event_field_names(ev)

    return jsonify({
        "success": result["success"],
        "events": events,
        "error": result.get("error"),
    })


@app.route("/api/games/<game_id>/replay")
def get_game_replay(game_id):
    db = _get_db()
    game_doc = db.collection("games").document(game_id).get()
    if not game_doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    game = game_doc.to_dict()
    if game["status"] not in ("completed", "aborted"):
        return jsonify({"detail": "Game is still in progress"}), 400

    # Try GCS first (cheaper than assembling from Firestore events)
    try:
        client = gcs.Client()
        bucket = client.bucket(GCS_REPLAY_BUCKET)
        blob = bucket.blob(f"replays/{game_id}.json")
        if blob.exists():
            return app.response_class(
                response=blob.download_as_bytes(),
                status=200,
                mimetype="application/json",
            )
    except Exception:
        pass  # Fall through to Firestore assembly

    # Fallback: assemble from Firestore events
    config = _config_from_json(json.loads(game["configJson"]))
    board = _board_from_json(json.loads(game["boardJson"]))

    events_docs = list(
        db.collection("games").document(game_id).collection("events")
        .order_by("seq")
        .stream()
    )
    events = []
    actions = []
    for edoc in events_docs:
        edata = edoc.to_dict()
        events.append(_event_from_json(json.loads(edata["eventJson"])))
        if edata.get("actionJson"):
            actions.append(json.loads(edata["actionJson"]))

    turn_order = [PlayerColor(c) for c in json.loads(game["turnOrderJson"])]
    game_log = game_log_to_json(config, board, turn_order, events)
    return jsonify(game_log)


@app.route("/api/games/<game_id>/action-log")
def get_action_log(game_id):
    db = _get_db()
    game_doc = db.collection("games").document(game_id).get()
    if not game_doc.exists:
        return jsonify({"detail": "Game not found"}), 404

    logs = list(
        db.collection("games").document(game_id).collection("actionLog")
        .order_by("createdAt")
        .stream()
    )
    entries = []
    for ldoc in logs:
        ldata = ldoc.to_dict()
        entries.append({
            "id": ldoc.id,
            "bot_id": ldata["botId"],
            "bot_name": ldata.get("botName", "?"),
            "action": json.loads(ldata["actionJson"]),
            "success": ldata["success"],
            "error": ldata.get("error"),
            "created_at": ldata.get("createdAt"),
        })
    return jsonify(entries)


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@app.route("/api/leaderboard/<mode>")
def get_leaderboard(mode):
    if mode not in ("1v1", "4p"):
        return jsonify({"detail": "Invalid mode"}), 404

    limit_val = int(request.args.get("limit", 50))
    db = _get_db()
    elo_key = "elo1v1" if mode == "1v1" else "elo4p"
    games_key = "games1v1" if mode == "1v1" else "games4p"
    wins_key = "wins1v1" if mode == "1v1" else "wins4p"

    docs = list(
        db.collection("bots")
        .where("mode", "==", mode)
        .order_by(elo_key, direction=firestore.Query.DESCENDING)
        .limit(limit_val)
        .stream()
    )

    entries = []
    for i, doc in enumerate(docs):
        d = doc.to_dict()
        entries.append({
            "rank": i + 1,
            "bot_id": doc.id,
            "name": d.get("name", "?"),
            "elo": d.get(elo_key, INITIAL_ELO),
            "games": d.get(games_key, 0),
            "wins": d.get(wins_key, 0),
        })

    return jsonify({"mode": mode, "entries": entries})


# ---------------------------------------------------------------------------
# Admin / utility
# ---------------------------------------------------------------------------

@app.route("/api/matchmaker/trigger", methods=["POST"])
def trigger_matchmaking():
    game_ids = run_matchmaking()
    return jsonify({"games_created": len(game_ids), "game_ids": game_ids})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Cloud Function entry points
# ---------------------------------------------------------------------------

@https_fn.on_request(
    memory=options.MemoryOption.MB_512,
    timeout_sec=60,
    max_instances=10,
)
def api(req: https_fn.Request) -> https_fn.Response:
    """Single HTTP function serving the entire REST API."""
    with app.request_context(req.environ):
        rv = app.full_dispatch_request()
        return https_fn.Response(
            response=rv.get_data(),
            status=rv.status_code,
            headers=dict(rv.headers),
        )


@scheduler_fn.on_schedule(schedule="every 1 minutes")
def scheduled_matchmaking(event: scheduler_fn.ScheduledEvent) -> None:
    """Run matchmaking every minute."""
    game_ids = run_matchmaking()
    if game_ids:
        print(f"Matchmaking created {len(game_ids)} games: {game_ids}")


@scheduler_fn.on_schedule(schedule="every 1 minutes")
def scheduled_timeout_check(event: scheduler_fn.ScheduledEvent) -> None:
    """Check for timed-out games every minute."""
    count = check_timeouts()
    if count:
        print(f"Aborted {count} timed-out games")
