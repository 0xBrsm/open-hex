"""Balanced dice — weighted 36-card deck with recency and seven balancing.

Algorithm (per draw):
  1. If cards remaining <= RESHUFFLE_THRESHOLD, reshuffle full 36-card deck.
  2. Compute base weight per sum k: count_of_cards_with_sum_k / total_cards.
  3. Recency penalty: for each sum, multiply weight by max(0, 1 - 0.34 * appearances_in_last_5_rolls).
  4. Seven balancing (2+ players): streak + imbalance adjustment on the 7-weight.
  5. Normalize weights to probabilities, weighted random draw.

Background reading on balanced dice:
  - https://blog.colonist.io/designing-balanced-dice/
  - https://blog.colonist.io/balancing-7s-on-1v1/
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

# Algorithm constants
RESHUFFLE_THRESHOLD = 13       # reshuffle when this many cards remain
RECENCY_WINDOW = 5             # track last N rolls
RECENCY_PENALTY = 0.34         # weight multiplier reduction per recent appearance
SEVEN_STREAK_PENALTY = 0.4     # per-streak-count adjustment for 7s
SEVEN_MULTIPLIER_MIN = 0.0
SEVEN_MULTIPLIER_MAX = 2.0

# All 36 dice pair combinations
_FULL_DECK: list[tuple[int, int]] = [
    (d1, d2) for d1 in range(1, 7) for d2 in range(1, 7)
]


@dataclass
class BalancedDice:
    """Weighted 36-card dice deck with recency and seven balancing."""

    deck: list[tuple[int, int]] = field(default_factory=list)
    recent_rolls: list[int] = field(default_factory=list)       # last N roll totals
    sevens_by_player: dict[str, int] = field(default_factory=dict)  # player -> count of 7s
    seven_streak_player: str | None = None   # player on the current 7-streak
    seven_streak_count: int = 0              # length of current 7-streak

    @staticmethod
    def create(rng: random.Random) -> BalancedDice:
        bd = BalancedDice()
        bd._reshuffle(rng)
        return bd

    def _reshuffle(self, rng: random.Random) -> None:
        self.deck = list(_FULL_DECK)
        rng.shuffle(self.deck)

    def draw(self, rng: random.Random, current_player: str | None = None,
             num_players: int = 2) -> tuple[int, int]:
        """Draw a weighted card from the deck.

        Args:
            rng: Random number generator.
            current_player: Color string of the rolling player (for seven balancing).
            num_players: Number of players in the game.
        """
        # Step 1: Reshuffle if needed
        if len(self.deck) <= RESHUFFLE_THRESHOLD:
            self._reshuffle(rng)

        # Step 2-4: Compute weights per sum
        weights = self._compute_weights(current_player, num_players)

        # Step 5: Weighted random selection
        card = self._weighted_draw(rng, weights)

        # Remove drawn card from deck
        self.deck.remove(card)

        # Update state
        total = card[0] + card[1]
        self._update_recent_rolls(total)
        self._update_seven_tracking(total, current_player)

        return card

    def _compute_weights(self, current_player: str | None,
                         num_players: int) -> dict[int, float]:
        """Compute adjusted probability weight for each sum (2-12)."""
        # Group remaining cards by sum
        cards_by_sum: dict[int, list[tuple[int, int]]] = {}
        for card in self.deck:
            s = card[0] + card[1]
            cards_by_sum.setdefault(s, []).append(card)

        total_cards = len(self.deck)
        weights: dict[int, float] = {}

        for k in range(2, 13):
            count = len(cards_by_sum.get(k, []))
            if count == 0:
                weights[k] = 0.0
                continue

            # Base weight: proportion of remaining cards with this sum
            w = count / total_cards

            # Recency penalty
            appearances = sum(1 for r in self.recent_rolls if r == k)
            penalty = 1.0 - RECENCY_PENALTY * appearances
            w *= max(0.0, penalty)

            weights[k] = w

        # Seven balancing
        if current_player is not None and num_players >= 2 and weights.get(7, 0) > 0:
            multiplier = self._seven_multiplier(current_player, num_players)
            weights[7] *= multiplier

        return weights

    def _seven_multiplier(self, current_player: str, num_players: int) -> float:
        """Compute the combined seven probability multiplier."""
        # Streak adjustment
        streak_adj = 0.0
        if self.seven_streak_count > 0 and self.seven_streak_player is not None:
            direction = -1.0 if self.seven_streak_player == current_player else 1.0
            streak_adj = SEVEN_STREAK_PENALTY * self.seven_streak_count * direction

        # Imbalance adjustment
        total_sevens = sum(self.sevens_by_player.values())
        if total_sevens >= num_players:
            player_sevens = self.sevens_by_player.get(current_player, 0)
            pct = player_sevens / total_sevens
            ideal = 1.0 / num_players
            imbalance_adj = 1.0 + (ideal - pct) / ideal
        else:
            imbalance_adj = 1.0

        combined = imbalance_adj + streak_adj
        return max(SEVEN_MULTIPLIER_MIN, min(SEVEN_MULTIPLIER_MAX, combined))

    def _weighted_draw(self, rng: random.Random,
                       weights: dict[int, float]) -> tuple[int, int]:
        """Select a card from the deck using per-sum weights."""
        # Build per-card weights
        card_weights: list[float] = []
        for card in self.deck:
            s = card[0] + card[1]
            count = sum(1 for c in self.deck if c[0] + c[1] == s)
            w = weights.get(s, 0.0)
            # Distribute the sum's weight equally among its remaining cards
            card_weights.append(w / count if count > 0 else 0.0)

        # If all weights are zero (shouldn't happen), fall back to uniform
        total_w = sum(card_weights)
        if total_w <= 0:
            return self.deck[rng.randint(0, len(self.deck) - 1)]

        # Weighted random selection
        r = rng.random() * total_w
        cumulative = 0.0
        for i, cw in enumerate(card_weights):
            cumulative += cw
            if r <= cumulative:
                return self.deck[i]
        return self.deck[-1]  # floating point fallback

    def _update_recent_rolls(self, total: int) -> None:
        self.recent_rolls.append(total)
        if len(self.recent_rolls) > RECENCY_WINDOW:
            self.recent_rolls.pop(0)

    def _update_seven_tracking(self, total: int, player: str | None) -> None:
        if player is None:
            return
        if total == 7:
            self.sevens_by_player[player] = self.sevens_by_player.get(player, 0) + 1
            if self.seven_streak_player == player:
                self.seven_streak_count += 1
            else:
                self.seven_streak_player = player
                self.seven_streak_count = 1
        else:
            # Any non-7 breaks the streak
            self.seven_streak_count = 0
            self.seven_streak_player = None
