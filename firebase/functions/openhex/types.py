"""Core types: enums, coordinates, and resource map utilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Terrain(str, Enum):
    DESERT = "desert"
    FOREST = "forest"
    HILLS = "hills"
    PASTURE = "pasture"
    FIELDS = "fields"
    MOUNTAINS = "mountains"


class Resource(str, Enum):
    WOOD = "wood"
    BRICK = "brick"
    SHEEP = "sheep"
    WHEAT = "wheat"
    ORE = "ore"


TERRAIN_RESOURCE: dict[Terrain, Resource] = {
    Terrain.FOREST: Resource.WOOD,
    Terrain.HILLS: Resource.BRICK,
    Terrain.PASTURE: Resource.SHEEP,
    Terrain.FIELDS: Resource.WHEAT,
    Terrain.MOUNTAINS: Resource.ORE,
}


class PlayerColor(str, Enum):
    RED = "red"
    BLUE = "blue"
    WHITE = "white"
    ORANGE = "orange"


class DevCard(str, Enum):
    KNIGHT = "knight"
    VICTORY_POINT = "victoryPoint"
    ROAD_BUILDING = "roadBuilding"
    YEAR_OF_PLENTY = "yearOfPlenty"
    MONOPOLY = "monopoly"


class RobberMode(str, Enum):
    NORMAL = "normal"
    FRIENDLY = "friendly"


class DiceMode(str, Enum):
    RANDOM = "random"
    BALANCED = "balanced"


# ---------------------------------------------------------------------------
# Building costs
# ---------------------------------------------------------------------------

ResourceMap = Dict[Resource, int]
DevCardMap = Dict[DevCard, int]

BUILDING_COSTS: dict[str, ResourceMap] = {
    "road": {Resource.WOOD: 1, Resource.BRICK: 1},
    "settlement": {Resource.WOOD: 1, Resource.BRICK: 1, Resource.SHEEP: 1, Resource.WHEAT: 1},
    "city": {Resource.WHEAT: 2, Resource.ORE: 3},
    "devCard": {Resource.SHEEP: 1, Resource.WHEAT: 1, Resource.ORE: 1},
}

STANDARD_DEV_DECK: DevCardMap = {
    DevCard.KNIGHT: 14,
    DevCard.VICTORY_POINT: 5,
    DevCard.ROAD_BUILDING: 2,
    DevCard.YEAR_OF_PLENTY: 2,
    DevCard.MONOPOLY: 2,
}


# ---------------------------------------------------------------------------
# Resource / dev-card map helpers
# ---------------------------------------------------------------------------

def resource_total(rm: ResourceMap) -> int:
    return sum(rm.values())


def add_resources(a: ResourceMap, b: ResourceMap) -> ResourceMap:
    result = dict(a)
    for r, n in b.items():
        result[r] = result.get(r, 0) + n
    return _clean(result)


def subtract_resources(a: ResourceMap, b: ResourceMap) -> ResourceMap:
    result = dict(a)
    for r, n in b.items():
        result[r] = result.get(r, 0) - n
    return _clean(result)


def has_resources(hand: ResourceMap, cost: ResourceMap) -> bool:
    return all(hand.get(r, 0) >= n for r, n in cost.items())


def devcard_total(dm: DevCardMap) -> int:
    return sum(dm.values())


def _clean(rm: dict) -> dict:
    return {k: v for k, v in rm.items() if v != 0}


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HexPoint:
    x: int
    y: int
    z: int

    def __post_init__(self):
        if self.x + self.y + self.z != 0:
            raise ValueError(f"Invalid hex {self}: x+y+z={self.x + self.y + self.z}")

    # -- neighbours ----------------------------------------------------------

    def neighbors(self) -> list[HexPoint]:
        """Six adjacent hexes."""
        x, y, z = self.x, self.y, self.z
        return [
            HexPoint(x + 1, y, z - 1),   # E
            HexPoint(x + 1, y - 1, z),    # NE
            HexPoint(x, y - 1, z + 1),    # NW
            HexPoint(x - 1, y, z + 1),    # W
            HexPoint(x - 1, y + 1, z),    # SW
            HexPoint(x, y + 1, z - 1),    # SE
        ]

    def vertices(self) -> list[VertexPoint]:
        """Six surrounding vertices: N, NE, SE, S, SW, NW."""
        x, y, z = self.x, self.y, self.z
        return [
            VertexPoint(x + 1, y, z + 1),   # N  (type B)
            VertexPoint(x + 1, y, z),        # NE (type A)
            VertexPoint(x + 1, y + 1, z),    # SE (type B)
            VertexPoint(x, y + 1, z),        # S  (type A)
            VertexPoint(x, y + 1, z + 1),    # SW (type B)
            VertexPoint(x, y, z + 1),        # NW (type A)
        ]

    def edges(self) -> list[Edge]:
        """Six edges bounding this hex."""
        vs = self.vertices()
        return [Edge.between(vs[i], vs[(i + 1) % 6]) for i in range(6)]

    def distance(self, other: HexPoint) -> int:
        return max(abs(self.x - other.x), abs(self.y - other.y), abs(self.z - other.z))


@dataclass(frozen=True, slots=True)
class VertexPoint:
    x: int
    y: int
    z: int

    def __post_init__(self):
        t = self.x + self.y + self.z
        if t not in (1, 2):
            raise ValueError(f"Invalid vertex {self}: total={t}")

    @property
    def total(self) -> int:
        return self.x + self.y + self.z

    @property
    def is_type_a(self) -> bool:
        return self.total == 1

    @property
    def is_type_b(self) -> bool:
        return self.total == 2

    def adjacent_hexes(self) -> list[HexPoint]:
        """Three hexes touching this vertex."""
        x, y, z = self.x, self.y, self.z
        if self.is_type_a:
            return [HexPoint(x - 1, y, z), HexPoint(x, y - 1, z), HexPoint(x, y, z - 1)]
        else:
            return [HexPoint(x - 1, y, z - 1), HexPoint(x - 1, y - 1, z), HexPoint(x, y - 1, z - 1)]

    def adjacent_vertices(self) -> list[VertexPoint]:
        """Three vertices connected by edges."""
        x, y, z = self.x, self.y, self.z
        if self.is_type_a:
            return [VertexPoint(x + 1, y, z), VertexPoint(x, y + 1, z), VertexPoint(x, y, z + 1)]
        else:
            return [VertexPoint(x - 1, y, z), VertexPoint(x, y - 1, z), VertexPoint(x, y, z - 1)]

    def edges(self) -> list[Edge]:
        """Three edges from this vertex."""
        return [Edge.between(self, v) for v in self.adjacent_vertices()]


@dataclass(frozen=True, slots=True)
class Edge:
    """An edge between two adjacent vertices. v1 is type A (total=1), v2 is type B (total=2)."""
    v1: VertexPoint
    v2: VertexPoint

    def __post_init__(self):
        if self.v1.total != 1 or self.v2.total != 2:
            raise ValueError("Edge v1 must be type A (total=1) and v2 must be type B (total=2)")

    @staticmethod
    def between(a: VertexPoint, b: VertexPoint) -> Edge:
        if a.total == 1 and b.total == 2:
            return Edge(a, b)
        elif a.total == 2 and b.total == 1:
            return Edge(b, a)
        else:
            raise ValueError(f"Cannot make edge: totals {a.total} and {b.total}")

    @property
    def vertices(self) -> tuple[VertexPoint, VertexPoint]:
        return (self.v1, self.v2)
