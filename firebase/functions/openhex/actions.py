"""Player action representation."""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Edge, HexPoint, PlayerColor, Resource, ResourceMap, VertexPoint


@dataclass
class Action:
    type: str
    player: PlayerColor
    # Vertex (settlements, cities, setup)
    vertex: VertexPoint | None = None
    # Edge as two vertices (roads)
    vertices: tuple[VertexPoint, VertexPoint] | None = None
    # Hex location (robber, knight)
    location: HexPoint | None = None
    # Target player (steal, confirm trade)
    target: PlayerColor | None = None
    partner: PlayerColor | None = None
    # Resource (monopoly)
    resource: Resource | None = None
    # Resources (year of plenty list, discard map)
    resources: list[Resource] | ResourceMap | None = None
    # Trade
    trade_id: int | None = None
    offering: ResourceMap | None = None
    requesting: ResourceMap | None = None

    def edge(self) -> Edge | None:
        if self.vertices is None:
            return None
        return Edge.between(self.vertices[0], self.vertices[1])
