"""Standard board generation."""

from __future__ import annotations

import random

from .board import Board, Port, Tile
from .types import HexPoint, Resource, Terrain, VertexPoint


# Standard 4-player hex positions (3 rings)
RING_0 = [HexPoint(0, 0, 0)]

RING_1 = [
    HexPoint(1, -1, 0),
    HexPoint(1, 0, -1),
    HexPoint(0, 1, -1),
    HexPoint(-1, 1, 0),
    HexPoint(-1, 0, 1),
    HexPoint(0, -1, 1),
]

RING_2 = [
    HexPoint(2, -2, 0),
    HexPoint(2, -1, -1),
    HexPoint(2, 0, -2),
    HexPoint(1, 1, -2),
    HexPoint(0, 2, -2),
    HexPoint(-1, 2, -1),
    HexPoint(-2, 2, 0),
    HexPoint(-2, 1, 1),
    HexPoint(-2, 0, 2),
    HexPoint(-1, -1, 2),
    HexPoint(0, -2, 2),
    HexPoint(1, -2, 1),
]

ALL_HEX_POSITIONS = RING_0 + RING_1 + RING_2

# Standard terrain distribution (19 hexes)
STANDARD_TERRAINS = (
    [Terrain.FOREST] * 4 +
    [Terrain.HILLS] * 3 +
    [Terrain.PASTURE] * 4 +
    [Terrain.FIELDS] * 4 +
    [Terrain.MOUNTAINS] * 3 +
    [Terrain.DESERT] * 1
)

# Standard number tokens (spiral placement order)
STANDARD_NUMBERS = [5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11]

def _build_spiral(corner: int) -> list[HexPoint]:
    """Build number placement spiral starting from a corner (0-5) of the outer ring,
    going clockwise and spiraling inward to center."""
    # Rotate ring 2 to start at the chosen corner (corners are at even indices)
    r2 = RING_2[corner * 2:] + RING_2[:corner * 2]
    # Rotate ring 1 correspondingly
    r1 = RING_1[corner:] + RING_1[:corner]
    # Outside-in: ring 2 → ring 1 → center
    return r2 + r1 + RING_0

# Standard port positions — vertex pairs on the board boundary.
# Each pair is (type-A vertex sum=1, type-B vertex sum=2).
# Derived from 6th-edition hex positions + edge directions.
STANDARD_PORT_POSITIONS: list[tuple[VertexPoint, VertexPoint]] = [
    (VertexPoint(2, -2, 1), VertexPoint(2, -2, 2)),     # Top-right
    (VertexPoint(3, -1, -1), VertexPoint(3, -1, 0)),     # East-NE
    (VertexPoint(3, 0, -2), VertexPoint(3, 1, -2)),      # East-SE
    (VertexPoint(1, 2, -2), VertexPoint(2, 2, -2)),      # Bottom-right
    (VertexPoint(-1, 3, -1), VertexPoint(0, 3, -1)),     # Bottom-SE
    (VertexPoint(-2, 3, 0), VertexPoint(-2, 3, 1)),      # Bottom-left
    (VertexPoint(-2, 1, 2), VertexPoint(-2, 2, 2)),      # Left-SW
    (VertexPoint(-1, -1, 3), VertexPoint(-1, 0, 3)),     # Left-NW
    (VertexPoint(0, -2, 3), VertexPoint(1, -2, 3)),      # Top-left
]

# Standard port types: indices 0,3,5,6 are generic 3:1; others are 2:1
STANDARD_GENERIC_INDICES = {0, 3, 5, 6}


def generate_standard_board(seed: int | None = None) -> Board:
    """Generate a standard 4-player board with randomized terrain and ports."""
    rng = random.Random(seed)

    # Shuffle terrains
    terrains = list(STANDARD_TERRAINS)
    rng.shuffle(terrains)

    # Assign terrains to hex positions
    terrain_map: dict[HexPoint, Terrain] = {}
    for i, pos in enumerate(ALL_HEX_POSITIONS):
        terrain_map[pos] = terrains[i]

    # Build spiral from a random outer-ring corner, spiraling inward to center
    corner = rng.randint(0, 5)
    spiral = _build_spiral(corner)

    # Assign numbers in spiral order, skipping desert
    numbers: list[int] = list(STANDARD_NUMBERS)
    number_idx = 0
    hex_numbers: dict[HexPoint, int] = {}
    for pos in spiral:
        if terrain_map[pos] != Terrain.DESERT and number_idx < len(numbers):
            hex_numbers[pos] = numbers[number_idx]
            number_idx += 1

    # Check for adjacent 6/8 — swap if needed (simple retry)
    for _ in range(100):
        bad = False
        for pos, num in hex_numbers.items():
            if num in (6, 8):
                for nb in HexPoint(pos.x, pos.y, pos.z).neighbors():
                    if hex_numbers.get(nb) in (6, 8):
                        bad = True
                        break
            if bad:
                break
        if not bad:
            break
        # Re-shuffle numbers
        rng.shuffle(numbers)
        number_idx = 0
        hex_numbers.clear()
        for pos in spiral:
            if terrain_map[pos] != Terrain.DESERT and number_idx < len(numbers):
                hex_numbers[pos] = numbers[number_idx]
                number_idx += 1

    # Create tiles
    tiles = []
    robber = None
    for pos in ALL_HEX_POSITIONS:
        terrain = terrain_map[pos]
        number = hex_numbers.get(pos)
        tiles.append(Tile(location=pos, terrain=terrain, number=number))
        if terrain == Terrain.DESERT:
            robber = pos

    # Create ports
    resource_ports = [Resource.WOOD, Resource.BRICK, Resource.SHEEP, Resource.WHEAT, Resource.ORE]
    rng.shuffle(resource_ports)

    ports = []
    res_idx = 0
    for i, (v1, v2) in enumerate(STANDARD_PORT_POSITIONS):
        if i in STANDARD_GENERIC_INDICES:
            ports.append(Port(vertices=(v1, v2), rate=3))
        else:
            ports.append(Port(vertices=(v1, v2), rate=2, resource=resource_ports[res_idx]))
            res_idx += 1

    return Board(tiles=tiles, ports=ports, robber=robber)
