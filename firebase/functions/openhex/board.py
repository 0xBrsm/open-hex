"""Board representation: tiles, ports, buildings, roads."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Edge, HexPoint, PlayerColor, Resource, Terrain, VertexPoint


@dataclass
class Tile:
    location: HexPoint
    terrain: Terrain
    number: int | None = None  # None for desert


@dataclass
class Port:
    vertices: tuple[VertexPoint, VertexPoint]
    rate: int  # 2 or 3
    resource: Resource | None = None  # None for generic 3:1


@dataclass
class VertexBuilding:
    vertex: VertexPoint
    player: PlayerColor


@dataclass
class Road:
    edge: Edge
    player: PlayerColor


@dataclass
class Board:
    tiles: list[Tile] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    robber: HexPoint | None = None
    settlements: list[VertexBuilding] = field(default_factory=list)
    cities: list[VertexBuilding] = field(default_factory=list)
    roads: list[Road] = field(default_factory=list)

    def tile_at(self, loc: HexPoint) -> Tile | None:
        for t in self.tiles:
            if t.location == loc:
                return t
        return None

    def hex_locations(self) -> set[HexPoint]:
        return {t.location for t in self.tiles}

    def building_at(self, v: VertexPoint) -> VertexBuilding | None:
        for s in self.settlements:
            if s.vertex == v:
                return s
        for c in self.cities:
            if c.vertex == v:
                return c
        return None

    def road_at(self, e: Edge) -> Road | None:
        for r in self.roads:
            if r.edge == e:
                return r
        return None

    def player_settlement_count(self, player: PlayerColor) -> int:
        return sum(1 for s in self.settlements if s.player == player)

    def player_city_count(self, player: PlayerColor) -> int:
        return sum(1 for c in self.cities if c.player == player)

    def player_road_count(self, player: PlayerColor) -> int:
        return sum(1 for r in self.roads if r.player == player)

    def is_vertex_on_board(self, v: VertexPoint) -> bool:
        """Vertex touches at least one tile on the board."""
        hexes = self.hex_locations()
        return any(h in hexes for h in v.adjacent_hexes())

    def is_edge_on_board(self, e: Edge) -> bool:
        """Edge borders at least one tile on the board (the two hexes flanking
        an edge are exactly the hexes shared by both of its vertices)."""
        hexes = self.hex_locations()
        shared = set(e.v1.adjacent_hexes()) & set(e.v2.adjacent_hexes())
        return any(h in hexes for h in shared)

    def port_for_vertex(self, v: VertexPoint) -> Port | None:
        for p in self.ports:
            if v in p.vertices:
                return p
        return None

    def player_ports(self, player: PlayerColor) -> list[Port]:
        """Ports accessible to a player (has settlement/city on port vertex)."""
        player_vertices = set()
        for s in self.settlements:
            if s.player == player:
                player_vertices.add(s.vertex)
        for c in self.cities:
            if c.player == player:
                player_vertices.add(c.vertex)
        result = []
        seen = set()
        for p in self.ports:
            pid = (p.vertices, p.rate, p.resource)
            if pid not in seen and any(v in player_vertices for v in p.vertices):
                result.append(p)
                seen.add(pid)
        return result

    def best_trade_rate(self, player: PlayerColor, resource: Resource) -> int:
        """Best bank trade rate for a specific resource."""
        rate = 4
        for p in self.player_ports(player):
            if p.resource == resource and p.rate < rate:
                rate = p.rate
            elif p.resource is None and p.rate < rate:
                rate = p.rate
        return rate
