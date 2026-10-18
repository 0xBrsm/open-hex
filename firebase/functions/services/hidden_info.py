"""Filter game state and events to hide private information from opponents."""

from __future__ import annotations

import copy


def filter_state_for_player(state_json: dict, player_color: str) -> dict:
    """Return a copy of state_json with opponent hands/dev cards hidden."""
    filtered = copy.deepcopy(state_json)

    for p in filtered.get("players", []):
        if p.get("color") != player_color:
            hand = p.get("hand", {})
            dev_cards = p.get("devCards", {})
            hand_size = sum(hand.values())
            dev_card_count = sum(dev_cards.values())
            p["hand"] = {}
            p["handSize"] = hand_size
            p["devCards"] = {}
            p["devCardCount"] = dev_card_count
            p["devCardsBoughtThisTurn"] = {}

    # Hide dev card deck composition — only show total remaining
    deck = filtered.get("devCardDeck", {})
    total = sum(deck.values())
    filtered["devCardDeck"] = {"remaining": total}

    # Hide balanced dice deck — leaks future rolls
    if "balancedDice" in filtered:
        bd = filtered["balancedDice"]
        filtered["balancedDice"] = {"remaining": len(bd.get("deck", []))}

    return filtered


def filter_event_for_player(event_json: dict, player_color: str) -> dict:
    """Return a copy of event_json with hidden info removed for non-involved players."""
    filtered = copy.deepcopy(event_json)

    etype = filtered.get("type")

    if etype == "devCardBought":
        if filtered.get("player") != player_color:
            filtered.pop("card", None)

    elif etype == "resourceStolen":
        if filtered.get("player") != player_color and filtered.get("target") != player_color:
            filtered.pop("resource", None)

    return filtered
