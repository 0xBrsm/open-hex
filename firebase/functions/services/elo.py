"""ELO rating calculations."""

from __future__ import annotations

import config


def expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo(rating: float, expected: float, actual: float,
               k: float = config.ELO_K_FACTOR) -> float:
    return rating + k * (actual - expected)


def compute_1v1_updates(
    winner_elo: float, loser_elo: float, k: float = config.ELO_K_FACTOR
) -> tuple[float, float]:
    """Return (new_winner_elo, new_loser_elo)."""
    e_w = expected_score(winner_elo, loser_elo)
    e_l = 1.0 - e_w
    return update_elo(winner_elo, e_w, 1.0, k), update_elo(loser_elo, e_l, 0.0, k)


def compute_4p_updates(
    ratings: list[float], finishing_order: list[int], k: float = config.ELO_K_FACTOR
) -> list[float]:
    """Pairwise ELO for 4-player game.

    ratings: list of 4 ELO values in seat order.
    finishing_order: list of 4 seat indices, best first (index 0 = winner).
    Returns new ratings in seat order.
    """
    n = len(ratings)
    k_eff = k / (n - 1)
    new_ratings = list(ratings)

    rank = [0] * n
    for place, seat in enumerate(finishing_order):
        rank[seat] = place

    for i in range(n):
        for j in range(i + 1, n):
            e_i = expected_score(ratings[i], ratings[j])
            e_j = 1.0 - e_i
            if rank[i] < rank[j]:
                s_i, s_j = 1.0, 0.0
            elif rank[i] > rank[j]:
                s_i, s_j = 0.0, 1.0
            else:
                s_i, s_j = 0.5, 0.5
            new_ratings[i] += k_eff * (s_i - e_i)
            new_ratings[j] += k_eff * (s_j - e_j)

    return new_ratings
