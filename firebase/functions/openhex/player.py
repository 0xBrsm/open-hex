"""Player state."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import DevCard, DevCardMap, PlayerColor, ResourceMap, devcard_total, resource_total


@dataclass
class PlayerState:
    color: PlayerColor
    hand: ResourceMap = field(default_factory=dict)
    dev_cards: DevCardMap = field(default_factory=dict)
    dev_cards_bought_this_turn: DevCardMap = field(default_factory=dict)
    played_dev_cards: DevCardMap = field(default_factory=dict)
    knights_played: int = 0
    longest_road_length: int = 0

    @property
    def hand_size(self) -> int:
        return resource_total(self.hand)

    @property
    def dev_card_count(self) -> int:
        return devcard_total(self.dev_cards)

    @property
    def victory_points(self) -> int:
        """Total VP including hidden VP cards."""
        # Settlements and cities VP are computed from board state, not stored here.
        # This only tracks VP from dev cards held.
        return self.dev_cards.get(DevCard.VICTORY_POINT, 0)

    @property
    def public_victory_points(self) -> int:
        """VP visible to all (excludes hidden VP cards). Cards VP computed externally."""
        return 0  # Board-based VP computed in GameState
