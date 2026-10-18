"""Matchmaking — queue management and game creation (Firestore-backed)."""

from __future__ import annotations

import random

from firebase_admin import firestore

from services.game_manager import create_game


def _get_db():
    return firestore.client()


def run_matchmaking() -> list[str]:
    """Run one matchmaking pass. Returns list of created game IDs."""
    game_ids = []
    game_ids.extend(_match_mode("1v1", 2))
    game_ids.extend(_match_mode("4p", 4))
    return game_ids


def _match_mode(mode: str, players_needed: int) -> list[str]:
    """Match players for a given mode. Returns created game IDs."""
    db = _get_db()
    elo_key = "elo1v1" if mode == "1v1" else "elo4p"

    # Get queued bots for this mode, sorted by join time
    queue_docs = list(
        db.collection("queue")
        .where("mode", "==", mode)
        .order_by("joinedAt")
        .stream()
    )

    if len(queue_docs) < players_needed:
        return []

    # Read bot Elo and last opponent for sorting/filtering
    bot_elos = {}
    bot_last_opp = {}
    for qdoc in queue_docs:
        qdata = qdoc.to_dict()
        bot_id = qdata["botId"]
        bot_doc = db.collection("bots").document(bot_id).get()
        if bot_doc.exists:
            bdata = bot_doc.to_dict()
            bot_elos[bot_id] = bdata.get(elo_key, 1500.0)
            bot_last_opp[bot_id] = bdata.get("lastOpponentId")

    # Sort by Elo with jitter to avoid always pairing the same bots
    queue_docs.sort(key=lambda d: bot_elos.get(d.to_dict()["botId"], 1500.0) + random.gauss(0, 50))

    game_ids = []
    i = 0
    while i + players_needed <= len(queue_docs):
        group = queue_docs[i:i + players_needed]
        bot_ids = [d.to_dict()["botId"] for d in group]

        # Skip pairing if both bots just played each other (1v1 only)
        if mode == "1v1" and len(bot_ids) == 2:
            a, b = bot_ids
            if bot_last_opp.get(a) == b and bot_last_opp.get(b) == a:
                # Only skip if there are other options in the queue
                if len(queue_docs) > players_needed:
                    i += 1
                    continue

        # Check none are in an active game already
        skip = False
        for bid in bot_ids:
            active = (
                db.collection("games")
                .where("status", "==", "active")
                .where("playerBotIds", "array_contains", bid)
                .limit(1)
                .get()
            )
            if len(active) > 0:
                skip = True
                # Remove stale queue entry
                db.collection("queue").document(group[bot_ids.index(bid)].id).delete()
                break

        if skip:
            i += 1
            continue

        game_id = create_game(mode, bot_ids)
        game_ids.append(game_id)

        # Remove matched bots from queue
        for doc in group:
            db.collection("queue").document(doc.id).delete()

        i += players_needed

    return game_ids
