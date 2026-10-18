"""OpenHex — reference implementation of the OpenHex game specification."""

from .types import (
    Terrain, Resource, PlayerColor, DevCard, RobberMode, DiceMode,
    HexPoint, VertexPoint, Edge,
    TERRAIN_RESOURCE, BUILDING_COSTS,
    ResourceMap, DevCardMap,
    resource_total, add_resources, subtract_resources, has_resources,
)
from .board import Tile, Port, VertexBuilding, Road, Board
from .player import PlayerState
from .dice import BalancedDice
from .state import GameConfig, PendingAction, Trade, GameState
from .actions import Action
from .events import Event
from .engine import OpenHexEngine, ActionResult
from .maps import generate_standard_board
from .json_codec import to_json, from_json, game_log_to_json, game_log_from_json

__all__ = [
    "Terrain", "Resource", "PlayerColor", "DevCard", "RobberMode", "DiceMode",
    "HexPoint", "VertexPoint", "Edge",
    "TERRAIN_RESOURCE", "BUILDING_COSTS",
    "ResourceMap", "DevCardMap",
    "resource_total", "add_resources", "subtract_resources", "has_resources",
    "Tile", "Port", "VertexBuilding", "Road", "Board",
    "PlayerState",
    "BalancedDice",
    "GameConfig", "PendingAction", "Trade", "GameState",
    "Action", "Event",
    "OpenHexEngine", "ActionResult",
    "generate_standard_board",
    "to_json", "from_json", "game_log_to_json", "game_log_from_json",
]
