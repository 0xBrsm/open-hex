"""OpenHex Cloud Functions — Flask API + scheduled tasks."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

import firebase_admin
from firebase_admin import firestore
from firebase_functions import https_fn, scheduler_fn, options
from flask import Flask, request, jsonify

from google.cloud import storage as gcs

from openhex.types import PlayerColor
from openhex.json_codec import (
    _config_from_json, _board_from_json, _event_from_json, game_log_to_json,
    convert_state_to_indices, convert_event_to_indices, strip_static_board,
    convert_state_field_names, convert_event_field_names, normalize_action_fields,
    _hex_dict_to_index, _edge_dicts_to_index,
)

from services.game_manager import (
    process_action, check_timeouts, create_game, _now_iso, load_state_json, event_docs,
)
from services.hidden_info import filter_state_for_player, filter_event_for_player
from services.matchmaker import run_matchmaking
from services import rankings
from config import INITIAL_ELO, GCS_REPLAY_BUCKET, MODES, RENAME_COOLDOWN_SECONDS

firebase_admin.initialize_app()

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _get_db():
    return firestore.client()


def _api_key() -> str | None:
    """The key in an Authorization: Bearer <api_key> header, or None."""
    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


def _get_current_bot():
    """Extract bot info from Authorization: Bearer <api_key> header."""
    api_key = _api_key()
    if api_key is None:
        return None, (jsonify({"detail": "Invalid authorization header"}), 401)
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

MODE_RULE = "Mode must be one of " + ", ".join(f"'{m}'" for m in MODES)
NAME_RULE = "Name must be 3-32 characters"
DESCRIPTION_RULE = "Description must be 280 characters or fewer"


def _name_ok(name: str) -> bool:
    return 3 <= len(name) <= 32


def _description_ok(description: str) -> bool:
    return len(description) <= 280


def _name_taken(db, name: str, mode: str, exclude_id: str | None = None) -> bool:
    """Whether another bot in this mode already has this name."""
    docs = (
        db.collection("bots")
        .where("name", "==", name)
        .where("mode", "==", mode)
        .limit(2)
        .stream()
    )
    return any(doc.id != exclude_id for doc in docs)


@app.route("/api/bots", methods=["POST"])
def register_bot():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    mode = data.get("mode", "")
    coord_fmt = data.get("coordinateFormat", "coordinates")
    description = data.get("description", "").strip()

    if not _name_ok(name):
        return jsonify({"detail": NAME_RULE}), 400
    if mode not in MODES:
        return jsonify({"detail": MODE_RULE}), 400
    if coord_fmt not in ("coordinates", "indices"):
        return jsonify({"detail": "coordinateFormat must be 'coordinates' or 'indices'"}), 400
    if not _description_ok(description):
        return jsonify({"detail": DESCRIPTION_RULE}), 400

    db = _get_db()

    if _name_taken(db, name, mode):
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
        "createdAt": _now_iso(),
    }
    for m in MODES:
        bot_doc.update({f"elo{m}": INITIAL_ELO, f"games{m}": 0, f"wins{m}": 0})
    db.collection("bots").document(bot_id).set(bot_doc)

    # Store API key in separate collection (document ID = key for O(1) lookup)
    db.collection("apiKeys").document(api_key).set({
        "botId": bot_id,
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
    }
    for m in MODES:
        resp.update({
            f"elo_{m}": bot.get(f"elo{m}", INITIAL_ELO),
            f"elo_se_{m}": bot.get(f"eloSe{m}"),
            f"rank_{m}": bot.get(f"rank{m}"),
            f"games_{m}": bot.get(f"games{m}", 0),
            f"wins_{m}": bot.get(f"wins{m}", 0),
        })
    return resp


@app.route("/api/bots/me")
@require_auth
def get_self(bot):
    db = _get_db()
    doc = db.collection("bots").document(bot["botId"]).get()
    if not doc.exists:
        return jsonify({"detail": "Bot not found"}), 404
    return jsonify({"id": doc.id, **_bot_response(doc.to_dict())})


@app.route("/api/bots/me", methods=["PATCH"])
@require_auth
def update_self(bot):
    """Rename the bot or change its description, keeping its id, key and rating.

    Games already created keep the name they were created with; games created
    from now on, the leaderboard and the bot's page show the new one. A bot
    may change its name once every RENAME_COOLDOWN_SECONDS; its description
    as often as it likes.
    """
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify({"detail": "Body must be a JSON object"}), 400
    unknown = sorted(set(data) - {"name", "description"})
    if unknown:
        return jsonify({"detail": f"Only name and description can be changed, not {', '.join(unknown)}"}), 400
    if not data:
        return jsonify({"detail": "Nothing to change: send name, description or both"}), 400

    changes = {}
    if "name" in data:
        if not isinstance(data["name"], str) or not _name_ok(data["name"].strip()):
            return jsonify({"detail": NAME_RULE}), 400
        changes["name"] = data["name"].strip()
    if "description" in data:
        if not isinstance(data["description"], str) or not _description_ok(data["description"].strip()):
            return jsonify({"detail": DESCRIPTION_RULE}), 400
        changes["description"] = data["description"].strip()

    db = _get_db()
    ref = db.collection("bots").document(bot["botId"])
    doc = ref.get()
    if not doc.exists:
        return jsonify({"detail": "Bot not found"}), 404
    current = doc.to_dict()

    renamed = "name" in changes and changes["name"] != current.get("name")
    if renamed:
        now = datetime.now(timezone.utc)
        if current.get("renamedAt"):
            next_rename = (datetime.fromisoformat(current["renamedAt"])
                           + timedelta(seconds=RENAME_COOLDOWN_SECONDS))
            if now < next_rename:
                resp = jsonify({"detail": f"A bot can be renamed once every "
                                          f"{RENAME_COOLDOWN_SECONDS // 3600} hours; "
                                          f"the next rename is allowed at {next_rename.isoformat()}"})
                resp.headers["Retry-After"] = str(int((next_rename - now).total_seconds()) + 1)
                return resp, 429
        if _name_taken(db, changes["name"], current.get("mode", "1v1"), exclude_id=doc.id):
            return jsonify({"detail": "Bot name already taken for this mode"}), 409
        changes["renamedAt"] = now.isoformat()

    ref.update(changes)
    return jsonify({"id": doc.id, **_bot_response({**current, **changes})})


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
    if mode not in MODES:
        return jsonify({"detail": MODE_RULE}), 400

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
    if mode not in MODES:
        return jsonify({"detail": MODE_RULE}), 400

    db = _get_db()
    queue_id = f"{bot['botId']}_{mode}"
    db.collection("queue").document(queue_id).delete()
    return jsonify({"left": True, "mode": mode})


@app.route("/api/queue/status")
@require_auth
def queue_status(bot):
    db = _get_db()
    bot_id = bot["botId"]
    return jsonify({"queued": {
        m: db.collection("queue").document(f"{bot_id}_{m}").get().exists for m in MODES
    }})


# ---------------------------------------------------------------------------
# Game endpoints
# ---------------------------------------------------------------------------

def _build_game_info(game_id: str, game: dict) -> dict:
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
        "current_player_color": game.get("currentPlayerColor"),
        "turn_number": game.get("turnNumber", 0),
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

    state_json = json.loads(load_state_json(game_id, game, db))
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
    coord_fmt = bot.get("coordinateFormat", "coordinates")
    entries = []
    for edata in event_docs(game_id, game, after=after, db=db):
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

    result = process_action(game_id, bot["botId"], action_dict, duration_ms=duration_ms)

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

    events = [_event_from_json(json.loads(edata["eventJson"]))
              for edata in event_docs(game_id, game, db=db)]

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
    """The mode's bots: ranked ones by rank, then the rest by rating. Ratings
    are refitted every 15 minutes (services/rankings.py); `computed_at` is
    the last fit."""
    if mode not in MODES:
        return jsonify({"detail": "Invalid mode"}), 404

    limit_val = int(request.args.get("limit", 50))
    db = _get_db()
    elo_key, rank_key = f"elo{mode}", f"rank{mode}"

    docs = [(doc.id, doc.to_dict()) for doc in db.collection("bots").where("mode", "==", mode).stream()]
    docs.sort(key=lambda kv: (kv[1].get(rank_key) is None, kv[1].get(rank_key) or 0,
                              -kv[1].get(elo_key, INITIAL_ELO), kv[0]))

    entries = []
    for bot_id, d in docs[:limit_val]:
        entries.append({
            "rank": d.get(rank_key),
            "bot_id": bot_id,
            "name": d.get("name", "?"),
            "elo": d.get(elo_key, INITIAL_ELO),
            "elo_se": d.get(f"eloSe{mode}"),
            "games": d.get(f"games{mode}", 0),
            "wins": d.get(f"wins{mode}", 0),
            "last_game_at": d.get("lastGameAt"),
        })

    run = db.collection("ratings").document(mode).get()
    return jsonify({"mode": mode, "computed_at": run.to_dict().get("computedAt") if run.exists else None,
                    "entries": entries})


# ---------------------------------------------------------------------------
# Admin / utility
# ---------------------------------------------------------------------------

@app.route("/api/matchmaker/trigger", methods=["POST"])
def trigger_matchmaking():
    game_ids = run_matchmaking()
    return jsonify({"games_created": len(game_ids), "game_ids": game_ids})


@app.route("/api/stats")
def get_stats():
    """Site-wide counts for the home page, counted on the server."""
    db = _get_db()

    def count(query) -> int:
        return int(query.count().get()[0][0].value)

    resp = jsonify({
        "bots": count(db.collection("bots")),
        "active_games": count(db.collection("games").where("status", "==", "active")),
        "completed_games": count(
            db.collection("games").where("status", "in", ["completed", "aborted"])),
        "queue": count(db.collection("queue")),
    })
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


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


@scheduler_fn.on_schedule(schedule="every 15 minutes", memory=options.MemoryOption.MB_512,
                          timeout_sec=300)
def scheduled_ratings(event: scheduler_fn.ScheduledEvent) -> None:
    """Refit every mode's ratings from its games (services/rankings.py)."""
    for mode in MODES:
        s = rankings.update_ratings(_get_db(), mode)
        print(f"Ratings {mode}: {s['activeBots']} active bots, {s['rankedBots']} ranked, "
              f"{s['tables']} tables, {s['games']} games, {s['iterations']} iterations, {s['seconds']}s")
