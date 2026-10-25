"""Game state, configuration, pending actions, and trades."""

from __future__ import annotations

from dataclasses import dataclass, field

from .board import Board
from .dice import BalancedDice
from .player import PlayerState
from .types import (
    DevCard,
    DevCardMap,
    DiceMode,
    HexPoint,
    PlayerColor,
    Resource,
    ResourceMap,
    RobberMode,
    VertexPoint,
    STANDARD_DEV_DECK,
    resource_total,
)


@dataclass
class GameConfig:
    victory_points: int = 10
    discard_limit: int = 7
    num_players: int = 4
    robber_mode: RobberMode = RobberMode.NORMAL
    dice_mode: DiceMode = DiceMode.RANDOM
    max_settlements: int = 5
    max_cities: int = 4
    max_roads: int = 15


@dataclass
class PendingAction:
    type: str
    # Type-specific data
    players: dict[PlayerColor, int] | None = None   # discardResources: player -> count
    location: HexPoint | None = None                 # stealFromPlayer: robber hex
    targets: list[PlayerColor] | None = None         # stealFromPlayer: valid targets
    remaining: int | None = None                     # placeFreeRoad: roads left


@dataclass
class Trade:
    """A standing offer between players.

    `proposer` is the offer's creator: it gives `offering` and receives
    `requesting`. A plain offer is made by the current player to every other
    seat. A counter-offer (`counter_to` set to the id of a standing offer by
    the current player) is made by another seat and is addressed to the
    current player alone, who takes it with confirmTrade.
    """
    trade_id: int
    proposer: PlayerColor
    offering: ResourceMap
    requesting: ResourceMap
    accepting: list[PlayerColor] = field(default_factory=list)
    rejected: list[PlayerColor] = field(default_factory=list)
    counter_to: int | None = None


@dataclass
class DiceResult:
    die1: int
    die2: int

    @property
    def total(self) -> int:
        return self.die1 + self.die2


@dataclass
class GameState:
    config: GameConfig = field(default_factory=GameConfig)
    board: Board = field(default_factory=Board)
    bank: ResourceMap = field(default_factory=dict)
    dev_card_deck: DevCardMap = field(default_factory=dict)
    players: list[PlayerState] = field(default_factory=list)
    current_player: PlayerColor | None = None
    turn_order: list[PlayerColor] = field(default_factory=list)
    turn_number: int = 0
    is_setup_phase: bool = True
    dice: DiceResult | None = None
    pending_action: PendingAction | None = None
    played_dev_card_this_turn: bool = False
    trades: list[Trade] = field(default_factory=list)
    trades_proposed_this_turn: int = 0
    longest_road_player: PlayerColor | None = None
    largest_army_player: PlayerColor | None = None
    winner: PlayerColor | None = None
    balanced_dice: BalancedDice | None = None
    next_trade_id: int = 1

    # Setup tracking
    setup_round: int = 1            # 1 = first round, 2 = second round
    setup_index: int = 0            # index into turn_order (or reversed)
    setup_placed_settlement: bool = False
    last_setup_settlement: VertexPoint | None = None

    def get_player(self, color: PlayerColor) -> PlayerState:
        for p in self.players:
            if p.color == color:
                return p
        raise ValueError(f"No player with color {color}")

    def players_to_act(self) -> list[PlayerColor]:
        """Players the game is waiting on: the players still to discard
        during a discard, otherwise the current player."""
        pa = self.pending_action
        if pa is not None and pa.type == "discardResources" and pa.players:
            return list(pa.players.keys())
        return [self.current_player] if self.current_player is not None else []

    def compute_vp(self, color: PlayerColor) -> int:
        """Compute total VP for a player."""
        vp = 0
        # Settlements
        vp += self.board.player_settlement_count(color)
        # Cities (2 VP each)
        vp += self.board.player_city_count(color) * 2
        # VP dev cards
        player = self.get_player(color)
        vp += player.dev_cards.get(DevCard.VICTORY_POINT, 0)
        # Longest road
        if self.longest_road_player == color:
            vp += 2
        # Largest army
        if self.largest_army_player == color:
            vp += 2
        return vp

    def compute_public_vp(self, color: PlayerColor) -> int:
        """VP visible to all players (no hidden VP cards)."""
        vp = 0
        vp += self.board.player_settlement_count(color)
        vp += self.board.player_city_count(color) * 2
        if self.longest_road_player == color:
            vp += 2
        if self.largest_army_player == color:
            vp += 2
        return vp

    @staticmethod
    def new_game(config: GameConfig, board: Board, turn_order: list[PlayerColor]) -> GameState:
        """Create a new game ready for setup phase."""
        bank: ResourceMap = {r: 19 for r in Resource}
        dev_deck = dict(STANDARD_DEV_DECK)
        players = [PlayerState(color=c) for c in turn_order]

        state = GameState(
            config=config,
            board=board,
            bank=bank,
            dev_card_deck=dev_deck,
            players=players,
            current_player=turn_order[0],
            turn_order=list(turn_order),
            turn_number=0,
            is_setup_phase=True,
            pending_action=PendingAction(type="placeInitialSettlement"),
            setup_round=1,
            setup_index=0,
        )
        if config.dice_mode == DiceMode.BALANCED:
            # Will be initialized when setup ends
            state.balanced_dice = BalancedDice()
        return state
