"""Game event representation."""

from __future__ import annotations

from dataclasses import dataclass

from .types import DevCard, HexPoint, PlayerColor, Resource, ResourceMap, VertexPoint


@dataclass
class Event:
    type: str
    player: PlayerColor | None = None
    # Vertex (settlements, cities)
    vertex: VertexPoint | None = None
    # Edge as two vertices (roads)
    vertices: tuple[VertexPoint, VertexPoint] | None = None
    # Dice
    die1: int | None = None
    die2: int | None = None
    # Resources
    resources: ResourceMap | None = None
    resource: Resource | None = None
    # Resource production per player
    dice_total: int | None = None
    production: dict[PlayerColor, ResourceMap] | None = None
    # Dev card
    card: DevCard | None = None
    # Robber
    location: HexPoint | None = None
    # Steal / trade
    target: PlayerColor | None = None
    partner: PlayerColor | None = None
    # Trade
    trade_id: int | None = None
    offering: ResourceMap | None = None
    requesting: ResourceMap | None = None
    # Monopoly collection per player
    collected: dict[PlayerColor, int] | None = None
    # Awards
    length: int | None = None
    knights: int | None = None
    # Year of plenty (list of 2 resources)
    resource_list: list[Resource] | None = None
    # Turn / game
    turn_number: int | None = None
    victory_points: int | None = None
