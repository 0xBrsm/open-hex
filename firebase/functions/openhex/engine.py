"""OpenHex game engine — validates and executes actions."""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from dataclasses import dataclass, field

from .actions import Action
from .board import Road, VertexBuilding
from .events import Event
from .dice import BalancedDice
from .state import DiceResult, GameState, PendingAction, Trade
from .types import (
    BUILDING_COSTS,
    DevCard,
    DiceMode,
    Edge,
    HexPoint,
    PlayerColor,
    Resource,
    ResourceMap,
    RobberMode,
    Terrain,
    TERRAIN_RESOURCE,
    VertexPoint,
    add_resources,
    has_resources,
    resource_total,
    subtract_resources,
)


@dataclass
class ActionResult:
    success: bool
    state: GameState | None = None
    events: list[Event] = field(default_factory=list)
    error: str | None = None


class OpenHexEngine:
    def __init__(self, rng: random.Random | None = None):
        self.rng = rng or random.Random()

    def execute_action(self, state: GameState, action: Action) -> ActionResult:
        s = copy.deepcopy(state)
        events: list[Event] = []

        err = self._validate(s, action)
        if err:
            return ActionResult(success=False, error=err)

        self._execute(s, action, events)
        self._check_awards(s, action, events)
        self._check_victory(s, events)

        return ActionResult(success=True, state=s, events=events)

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------

    def _validate(self, s: GameState, a: Action) -> str | None:
        if s.winner is not None:
            return "Game is over"

        handler = {
            "placeInitialSettlement": self._validate_place_initial_settlement,
            "placeInitialRoad": self._validate_place_initial_road,
            "rollDice": self._validate_roll_dice,
            "buildRoad": self._validate_build_road,
            "buildSettlement": self._validate_build_settlement,
            "buildCity": self._validate_build_city,
            "buyDevCard": self._validate_buy_dev_card,
            "playKnight": self._validate_play_knight,
            "playRoadBuilding": self._validate_play_road_building,
            "playYearOfPlenty": self._validate_play_year_of_plenty,
            "playMonopoly": self._validate_play_monopoly,
            "proposeTrade": self._validate_propose_trade,
            "acceptTrade": self._validate_accept_trade,
            "rejectTrade": self._validate_reject_trade,
            "confirmTrade": self._validate_confirm_trade,
            "cancelTrade": self._validate_cancel_trade,
            "tradeWithBank": self._validate_trade_with_bank,
            "moveRobber": self._validate_move_robber,
            "stealFromPlayer": self._validate_steal_from_player,
            "discardResources": self._validate_discard_resources,
            "placeFreeRoad": self._validate_place_free_road,
            "endTurn": self._validate_end_turn,
        }.get(a.type)

        if handler is None:
            return f"Unknown action type: {a.type}"
        return handler(s, a)

    def _require_current_player(self, s: GameState, a: Action) -> str | None:
        if a.player != s.current_player:
            return f"Not {a.player}'s turn"
        return None

    def _require_pending(self, s: GameState, expected: str) -> str | None:
        if s.pending_action is None or s.pending_action.type != expected:
            return f"Expected pending action '{expected}'"
        return None

    def _require_no_pending(self, s: GameState) -> str | None:
        if s.pending_action is not None:
            return f"Must resolve pending action: {s.pending_action.type}"
        return None

    def _require_main_phase(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if s.is_setup_phase:
            return "Not in main phase"
        err = self._require_no_pending(s)
        if err:
            return err
        if s.dice is None:
            return "Must roll dice first"
        return None

    # -- setup ---------------------------------------------------------------

    def _validate_place_initial_settlement(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        err = self._require_pending(s, "placeInitialSettlement")
        if err:
            return err
        if a.vertex is None:
            return "Missing vertex"
        if not s.board.is_vertex_on_board(a.vertex):
            return "Vertex not on board"
        if s.board.building_at(a.vertex):
            return "Vertex occupied"
        for adj in a.vertex.adjacent_vertices():
            if s.board.building_at(adj):
                return "Too close to another building (distance rule)"
        return None

    def _validate_place_initial_road(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        err = self._require_pending(s, "placeInitialRoad")
        if err:
            return err
        if a.vertices is None:
            return "Missing vertices"
        edge = a.edge()
        if edge is None:
            return "Invalid edge"
        if not s.board.is_edge_on_board(edge):
            return "Edge not on board"
        if s.board.road_at(edge):
            return "Edge occupied"
        # Must be adjacent to last placed settlement
        last = s.last_setup_settlement
        if last is None:
            return "No settlement placed yet"
        if last not in edge.vertices:
            return "Road must be adjacent to your settlement"
        return None

    # -- main phase ----------------------------------------------------------

    def _validate_roll_dice(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if s.is_setup_phase:
            return "Cannot roll during setup"
        if s.pending_action and s.pending_action.type != "rollDice":
            return f"Must resolve pending action: {s.pending_action.type}"
        if s.dice is not None and (s.pending_action is None or s.pending_action.type != "rollDice"):
            return "Already rolled"
        return None

    def _validate_build_road(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        if a.vertices is None:
            return "Missing vertices"
        edge = a.edge()
        if edge is None:
            return "Invalid edge"
        if not s.board.is_edge_on_board(edge):
            return "Edge not on board"
        player = s.get_player(a.player)
        if not has_resources(player.hand, BUILDING_COSTS["road"]):
            return "Insufficient resources"
        if s.board.player_road_count(a.player) >= s.config.max_roads:
            return "Max roads reached"
        if s.board.road_at(edge):
            return "Edge occupied"
        if not self._is_road_connected(s, a.player, edge):
            return "Road must connect to your network"
        return None

    def _validate_build_settlement(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        if a.vertex is None:
            return "Missing vertex"
        player = s.get_player(a.player)
        if not has_resources(player.hand, BUILDING_COSTS["settlement"]):
            return "Insufficient resources"
        if s.board.player_settlement_count(a.player) >= s.config.max_settlements:
            return "Max settlements reached"
        if not s.board.is_vertex_on_board(a.vertex):
            return "Vertex not on board"
        if s.board.building_at(a.vertex):
            return "Vertex occupied"
        for adj in a.vertex.adjacent_vertices():
            if s.board.building_at(adj):
                return "Too close to another building"
        if not self._is_vertex_connected(s, a.player, a.vertex):
            return "Must connect to your road network"
        return None

    def _validate_build_city(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        if a.vertex is None:
            return "Missing vertex"
        player = s.get_player(a.player)
        if not has_resources(player.hand, BUILDING_COSTS["city"]):
            return "Insufficient resources"
        if s.board.player_city_count(a.player) >= s.config.max_cities:
            return "Max cities reached"
        building = s.board.building_at(a.vertex)
        if building is None or building.player != a.player:
            return "Must upgrade your own settlement"
        found = False
        for i, st in enumerate(s.board.settlements):
            if st.vertex == a.vertex and st.player == a.player:
                found = True
                break
        if not found:
            return "No settlement to upgrade"
        return None

    def _validate_buy_dev_card(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        player = s.get_player(a.player)
        if not has_resources(player.hand, BUILDING_COSTS["devCard"]):
            return "Insufficient resources"
        if sum(s.dev_card_deck.values()) == 0:
            return "No dev cards left"
        return None

    def _validate_play_dev_card_common(self, s: GameState, a: Action, card: DevCard) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if s.is_setup_phase:
            return "Cannot play dev cards during setup"
        if s.played_dev_card_this_turn:
            return "Already played a dev card this turn"
        player = s.get_player(a.player)
        if player.dev_cards.get(card, 0) <= 0:
            return f"No {card.value} cards"
        if player.dev_cards_bought_this_turn.get(card, 0) > 0:
            available = player.dev_cards.get(card, 0) - player.dev_cards_bought_this_turn.get(card, 0)
            if available <= 0:
                return "Cannot play a card bought this turn"
        return None

    def _require_card_window(self, s: GameState, a: Action) -> str | None:
        """When a development card may be played: by the current player,
        before the roll or during the main phase, never while another move
        is owed (the rulebook: one card, at any time during your turn)."""
        err = self._require_current_player(s, a)
        if err:
            return err
        if s.is_setup_phase:
            return "Cannot play dev cards during setup"
        if s.pending_action is not None and s.pending_action.type != "rollDice":
            return f"Must resolve pending action: {s.pending_action.type}"
        return None

    def _validate_play_knight(self, s: GameState, a: Action) -> str | None:
        err = self._require_card_window(s, a)
        if err:
            return err
        err = self._validate_play_dev_card_common(s, a, DevCard.KNIGHT)
        if err:
            return err
        if a.location is None:
            return "Missing robber location"
        return self._validate_robber_placement(s, a)

    def _validate_play_road_building(self, s: GameState, a: Action) -> str | None:
        err = self._require_card_window(s, a)
        if err:
            return err
        err = self._validate_play_dev_card_common(s, a, DevCard.ROAD_BUILDING)
        if err:
            return err
        # A card with no road to place would leave a free road owed that can
        # never be placed, and the game stuck until the seat times out.
        if s.board.player_road_count(a.player) >= s.config.max_roads:
            return "No road pieces left"
        if not self._free_road_edges(s, a.player):
            return "No room for a free road"
        return None

    def _free_road_edges(self, s: GameState, player: PlayerColor) -> list[Edge]:
        """Edges a free road may take now: on the board, empty, joined to the
        player's network."""
        out: list[Edge] = []
        seen: set[Edge] = set()
        for tile in s.board.tiles:
            for edge in tile.location.edges():
                if edge in seen:
                    continue
                seen.add(edge)
                if s.board.road_at(edge) is None and self._is_road_connected(s, player, edge):
                    out.append(edge)
        return out

    def _validate_play_year_of_plenty(self, s: GameState, a: Action) -> str | None:
        err = self._require_card_window(s, a)
        if err:
            return err
        err = self._validate_play_dev_card_common(s, a, DevCard.YEAR_OF_PLENTY)
        if err:
            return err
        if not isinstance(a.resources, list) or len(a.resources) != 2:
            return "Must select exactly 2 resources"
        for r in a.resources:
            if not isinstance(r, Resource):
                return f"Invalid resource: {r}"
        # Check bank has enough
        needed: dict[Resource, int] = {}
        for r in a.resources:
            needed[r] = needed.get(r, 0) + 1
        for r, n in needed.items():
            if s.bank.get(r, 0) < n:
                return f"Bank has insufficient {r.value}"
        return None

    def _validate_play_monopoly(self, s: GameState, a: Action) -> str | None:
        err = self._require_card_window(s, a)
        if err:
            return err
        err = self._validate_play_dev_card_common(s, a, DevCard.MONOPOLY)
        if err:
            return err
        if a.resource is None:
            return "Must name a resource"
        return None

    # -- trading -------------------------------------------------------------

    def _validate_propose_trade(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        if not a.offering or not a.requesting:
            return "Must offer and request resources"
        player = s.get_player(a.player)
        if not has_resources(player.hand, a.offering):
            return "Insufficient resources to offer"
        return None

    def _validate_accept_trade(self, s: GameState, a: Action) -> str | None:
        if s.is_setup_phase:
            return "Cannot trade during setup"
        if a.trade_id is None:
            return "Missing tradeId"
        trade = self._find_trade(s, a.trade_id)
        if trade is None:
            return "Trade not found"
        if a.player == trade.proposer:
            return "Cannot accept own trade"
        if a.player in trade.accepting:
            return "Already accepted"
        player = s.get_player(a.player)
        if not has_resources(player.hand, trade.requesting):
            return "Insufficient resources to accept"
        return None

    def _validate_reject_trade(self, s: GameState, a: Action) -> str | None:
        if s.is_setup_phase:
            return "Cannot trade during setup"
        if a.trade_id is None:
            return "Missing tradeId"
        trade = self._find_trade(s, a.trade_id)
        if trade is None:
            return "Trade not found"
        if a.player == trade.proposer:
            return "Cannot reject own trade"
        return None

    def _validate_confirm_trade(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if a.trade_id is None or a.partner is None:
            return "Missing tradeId or partner"
        trade = self._find_trade(s, a.trade_id)
        if trade is None:
            return "Trade not found"
        if a.player != trade.proposer:
            return "Only proposer can confirm"
        if a.partner not in trade.accepting:
            return "Partner has not accepted"
        # Re-check both sides have resources
        proposer = s.get_player(a.player)
        partner = s.get_player(a.partner)
        if not has_resources(proposer.hand, trade.offering):
            return "Proposer no longer has offered resources"
        if not has_resources(partner.hand, trade.requesting):
            return "Partner no longer has requested resources"
        return None

    def _validate_cancel_trade(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if a.trade_id is None:
            return "Missing tradeId"
        trade = self._find_trade(s, a.trade_id)
        if trade is None:
            return "Trade not found"
        if a.player != trade.proposer:
            return "Only proposer can cancel"
        return None

    def _validate_trade_with_bank(self, s: GameState, a: Action) -> str | None:
        err = self._require_main_phase(s, a)
        if err:
            return err
        if not a.offering or not a.requesting:
            return "Must offer and request resources"
        # Offering must be single resource type
        off_resources = list(a.offering.keys())
        req_resources = list(a.requesting.keys())
        if len(off_resources) != 1 or len(req_resources) != 1:
            return "Bank trade must involve exactly one resource type each"
        off_r = off_resources[0]
        req_r = req_resources[0]
        off_n = a.offering[off_r]
        req_n = a.requesting[req_r]
        if req_n != 1:
            return "Can only request 1 resource from bank"
        expected_rate = s.board.best_trade_rate(a.player, off_r)
        if off_n != expected_rate:
            return f"Must offer {expected_rate} {off_r.value} (your best rate)"
        player = s.get_player(a.player)
        if not has_resources(player.hand, a.offering):
            return "Insufficient resources"
        if s.bank.get(req_r, 0) < 1:
            return "Bank has insufficient resources"
        return None

    # -- robber / discard ----------------------------------------------------

    def _validate_move_robber(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        err = self._require_pending(s, "moveRobber")
        if err:
            return err
        if a.location is None:
            return "Missing location"
        return self._validate_robber_placement(s, a)

    def _validate_robber_placement(self, s: GameState, a: Action) -> str | None:
        loc = a.location
        if loc == s.board.robber:
            return "Must move robber to a different hex"
        tile = s.board.tile_at(loc)
        if tile is None:
            return "Not a valid hex"
        if tile.terrain == Terrain.DESERT and s.board.robber == loc:
            return "Robber already on desert"
        # Friendly robber: hexes with an opponent still at starting VP are
        # off limits, unless every other hex is too (the spec's fallback).
        if (s.config.robber_mode == RobberMode.FRIENDLY
                and self._friendly_robber_shields(s, a.player, loc)
                and any(not self._friendly_robber_shields(s, a.player, t.location)
                        for t in s.board.tiles if t.location != s.board.robber)):
            return "Friendly robber: cannot block player at starting VP"
        return None

    def _friendly_robber_shields(self, s: GameState, mover: PlayerColor,
                                 loc: HexPoint) -> bool:
        """Whether an opponent at starting VP (2 visible points) has a building on `loc`."""
        for v in loc.vertices():
            b = s.board.building_at(v)
            if b and b.player != mover and s.compute_public_vp(b.player) <= 2:
                return True
        return False

    def _validate_steal_from_player(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        err = self._require_pending(s, "stealFromPlayer")
        if err:
            return err
        if a.target is None:
            return "Missing target"
        if s.pending_action.targets and a.target not in s.pending_action.targets:
            return "Invalid steal target"
        return None

    def _validate_discard_resources(self, s: GameState, a: Action) -> str | None:
        if s.pending_action is None or s.pending_action.type != "discardResources":
            return "No pending discard"
        if s.pending_action.players is None or a.player not in s.pending_action.players:
            return "You don't need to discard"
        expected = s.pending_action.players[a.player]
        if not isinstance(a.resources, dict):
            return "Resources must be a map"
        # A negative count would take cards from the bank instead of giving
        # them (subtract_resources adds them), and a non-integer corrupts
        # the hand; the codec only drops zeros.
        for n in a.resources.values():
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                return "Discard counts must be positive whole numbers"
        total = sum(a.resources.values())
        if total != expected:
            return f"Must discard exactly {expected} cards, got {total}"
        player = s.get_player(a.player)
        if not has_resources(player.hand, a.resources):
            return "Don't have those resources"
        return None

    def _validate_place_free_road(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        err = self._require_pending(s, "placeFreeRoad")
        if err:
            return err
        if a.vertices is None:
            return "Missing vertices"
        edge = a.edge()
        if edge is None:
            return "Invalid edge"
        if not s.board.is_edge_on_board(edge):
            return "Edge not on board"
        if s.board.road_at(edge):
            return "Edge occupied"
        if s.board.player_road_count(a.player) >= s.config.max_roads:
            return "Max roads reached"
        if not self._is_road_connected(s, a.player, edge):
            return "Road must connect to your network"
        return None

    def _validate_end_turn(self, s: GameState, a: Action) -> str | None:
        err = self._require_current_player(s, a)
        if err:
            return err
        if s.is_setup_phase:
            return "Cannot end turn during setup"
        err = self._require_no_pending(s)
        if err:
            return err
        if s.dice is None:
            return "Must roll dice first"
        return None

    # -----------------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------------

    def _execute(self, s: GameState, a: Action, events: list[Event]):
        handler = {
            "placeInitialSettlement": self._exec_place_initial_settlement,
            "placeInitialRoad": self._exec_place_initial_road,
            "rollDice": self._exec_roll_dice,
            "buildRoad": self._exec_build_road,
            "buildSettlement": self._exec_build_settlement,
            "buildCity": self._exec_build_city,
            "buyDevCard": self._exec_buy_dev_card,
            "playKnight": self._exec_play_knight,
            "playRoadBuilding": self._exec_play_road_building,
            "playYearOfPlenty": self._exec_play_year_of_plenty,
            "playMonopoly": self._exec_play_monopoly,
            "proposeTrade": self._exec_propose_trade,
            "acceptTrade": self._exec_accept_trade,
            "rejectTrade": self._exec_reject_trade,
            "confirmTrade": self._exec_confirm_trade,
            "cancelTrade": self._exec_cancel_trade,
            "tradeWithBank": self._exec_trade_with_bank,
            "moveRobber": self._exec_move_robber,
            "stealFromPlayer": self._exec_steal_from_player,
            "discardResources": self._exec_discard_resources,
            "placeFreeRoad": self._exec_place_free_road,
            "endTurn": self._exec_end_turn,
        }[a.type]
        handler(s, a, events)

    # -- setup ---------------------------------------------------------------

    def _exec_place_initial_settlement(self, s: GameState, a: Action, events: list[Event]):
        s.board.settlements.append(VertexBuilding(vertex=a.vertex, player=a.player))
        events.append(Event(type="setupSettlementPlaced", player=a.player, vertex=a.vertex))
        s.last_setup_settlement = a.vertex
        s.setup_placed_settlement = True
        s.pending_action = PendingAction(type="placeInitialRoad")

    def _exec_place_initial_road(self, s: GameState, a: Action, events: list[Event]):
        edge = a.edge()
        s.board.roads.append(Road(edge=edge, player=a.player))
        events.append(Event(type="setupRoadPlaced", player=a.player,
                            vertices=(edge.v1, edge.v2)))

        # Grant resources for second settlement
        if s.setup_round == 2:
            granted: ResourceMap = {}
            for h in s.last_setup_settlement.adjacent_hexes():
                tile = s.board.tile_at(h)
                if tile and tile.terrain != Terrain.DESERT and tile.terrain in TERRAIN_RESOURCE:
                    r = TERRAIN_RESOURCE[tile.terrain]
                    granted[r] = granted.get(r, 0) + 1
            if granted:
                player = s.get_player(a.player)
                player.hand = add_resources(player.hand, granted)
                for r, n in granted.items():
                    s.bank[r] = s.bank.get(r, 0) - n
                events.append(Event(type="setupResourcesGranted", player=a.player,
                                    resources=granted))

        s.setup_placed_settlement = False
        s.last_setup_settlement = None
        self._advance_setup(s)

    def _advance_setup(self, s: GameState):
        """Advance to next player in setup or transition to main phase."""
        n = len(s.turn_order)

        if s.setup_round == 1:
            s.setup_index += 1
            if s.setup_index >= n:
                # Start round 2 (reverse order)
                s.setup_round = 2
                s.setup_index = 0
                s.current_player = s.turn_order[n - 1 - s.setup_index]
            else:
                s.current_player = s.turn_order[s.setup_index]
            s.pending_action = PendingAction(type="placeInitialSettlement")
        elif s.setup_round == 2:
            s.setup_index += 1
            if s.setup_index >= n:
                # Setup complete — transition to main phase
                s.is_setup_phase = False
                s.turn_number = 1
                s.current_player = s.turn_order[0]
                s.pending_action = PendingAction(type="rollDice")
                # Reshuffle balanced dice for main phase
                if s.config.dice_mode == DiceMode.BALANCED and s.balanced_dice is not None:
                    s.balanced_dice._reshuffle(self.rng)
            else:
                s.current_player = s.turn_order[n - 1 - s.setup_index]
                s.pending_action = PendingAction(type="placeInitialSettlement")

    # -- dice ----------------------------------------------------------------

    def _exec_roll_dice(self, s: GameState, a: Action, events: list[Event]):
        die1, die2 = self._roll(s)
        s.dice = DiceResult(die1=die1, die2=die2)
        s.pending_action = None
        events.append(Event(type="diceRolled", player=a.player, die1=die1, die2=die2))

        total = die1 + die2
        if total == 7:
            self._handle_seven(s, a.player, events)
        else:
            self._distribute_resources(s, total, events)

    def _roll(self, s: GameState) -> tuple[int, int]:
        if s.config.dice_mode == DiceMode.BALANCED:
            if s.balanced_dice is None:
                # A state loaded from JSON may lack the deck (older servers
                # dropped it); draw() shuffles a full deck on first use.
                s.balanced_dice = BalancedDice()
            player = s.current_player.value if s.current_player else None
            return s.balanced_dice.draw(self.rng, current_player=player,
                                        num_players=s.config.num_players)
        return self.rng.randint(1, 6), self.rng.randint(1, 6)

    def _distribute_resources(self, s: GameState, total: int, events: list[Event]):
        production: dict[PlayerColor, ResourceMap] = defaultdict(dict)

        for tile in s.board.tiles:
            if tile.number != total:
                continue
            if tile.location == s.board.robber:
                continue
            if tile.terrain not in TERRAIN_RESOURCE:
                continue
            res = TERRAIN_RESOURCE[tile.terrain]

            for v in tile.location.vertices():
                building = s.board.building_at(v)
                if building is None:
                    continue
                amount = 2 if any(c.vertex == v for c in s.board.cities) else 1
                production[building.player][res] = production[building.player].get(res, 0) + amount

        # Short bank: a resource the bank cannot pay in full goes to nobody
        # when more than one player claims it; a sole claimant takes what
        # is left. Each resource is judged over every claim on it, so the
        # bank never goes negative.
        claimants: dict[Resource, list[PlayerColor]] = defaultdict(list)
        for player_color, res_map in production.items():
            for r in res_map:
                claimants[r].append(player_color)
        for r, colors in claimants.items():
            claimed = sum(production[c][r] for c in colors)
            available = s.bank.get(r, 0)
            if claimed <= available:
                continue
            if len(colors) == 1 and available > 0:
                production[colors[0]][r] = available
            else:
                for c in colors:
                    del production[c][r]

        # Distribute
        if production:
            for player_color, res_map in production.items():
                if res_map:
                    player = s.get_player(player_color)
                    player.hand = add_resources(player.hand, res_map)
                    for r, n in res_map.items():
                        s.bank[r] = s.bank.get(r, 0) - n

            events.append(Event(
                type="resourcesProduced",
                dice_total=total,
                production=dict(production),
            ))

    def _handle_seven(self, s: GameState, roller: PlayerColor, events: list[Event]):
        # Check for discards
        discard_players: dict[PlayerColor, int] = {}
        for p in s.players:
            if p.hand_size > s.config.discard_limit:
                discard_players[p.color] = p.hand_size // 2

        if discard_players:
            s.pending_action = PendingAction(
                type="discardResources",
                players=discard_players,
            )
        else:
            s.pending_action = PendingAction(type="moveRobber")

    # -- building ------------------------------------------------------------

    def _exec_build_road(self, s: GameState, a: Action, events: list[Event]):
        edge = a.edge()
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, BUILDING_COSTS["road"])
        self._return_to_bank(s, BUILDING_COSTS["road"])
        s.board.roads.append(Road(edge=edge, player=a.player))
        events.append(Event(type="roadBuilt", player=a.player,
                            vertices=(edge.v1, edge.v2)))

    def _exec_build_settlement(self, s: GameState, a: Action, events: list[Event]):
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, BUILDING_COSTS["settlement"])
        self._return_to_bank(s, BUILDING_COSTS["settlement"])
        s.board.settlements.append(VertexBuilding(vertex=a.vertex, player=a.player))
        events.append(Event(type="settlementBuilt", player=a.player, vertex=a.vertex))

    def _exec_build_city(self, s: GameState, a: Action, events: list[Event]):
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, BUILDING_COSTS["city"])
        self._return_to_bank(s, BUILDING_COSTS["city"])
        # Remove settlement, add city
        s.board.settlements = [st for st in s.board.settlements
                               if not (st.vertex == a.vertex and st.player == a.player)]
        s.board.cities.append(VertexBuilding(vertex=a.vertex, player=a.player))
        events.append(Event(type="cityBuilt", player=a.player, vertex=a.vertex))

    # -- dev cards -----------------------------------------------------------

    def _exec_buy_dev_card(self, s: GameState, a: Action, events: list[Event]):
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, BUILDING_COSTS["devCard"])
        self._return_to_bank(s, BUILDING_COSTS["devCard"])

        # Draw random card from deck
        cards = []
        for card, count in s.dev_card_deck.items():
            cards.extend([card] * count)
        drawn = self.rng.choice(cards)
        s.dev_card_deck[drawn] -= 1

        player.dev_cards[drawn] = player.dev_cards.get(drawn, 0) + 1
        player.dev_cards_bought_this_turn[drawn] = \
            player.dev_cards_bought_this_turn.get(drawn, 0) + 1

        events.append(Event(type="devCardBought", player=a.player, card=drawn))

    def _use_dev_card(self, s: GameState, player: PlayerColor, card: DevCard):
        p = s.get_player(player)
        p.dev_cards[card] -= 1
        if p.dev_cards[card] == 0:
            del p.dev_cards[card]
        p.played_dev_cards[card] = p.played_dev_cards.get(card, 0) + 1
        s.played_dev_card_this_turn = True

    def _exec_play_knight(self, s: GameState, a: Action, events: list[Event]):
        self._use_dev_card(s, a.player, DevCard.KNIGHT)
        player = s.get_player(a.player)
        player.knights_played += 1

        # Move robber
        s.board.robber = a.location
        events.append(Event(type="knightPlayed", player=a.player, location=a.location))

        # Set up steal
        targets = self._get_steal_targets(s, a.player, a.location)
        if targets:
            # Save whatever pending action was there (rollDice) to restore after steal
            s.pending_action = PendingAction(
                type="stealFromPlayer",
                location=a.location,
                targets=targets,
            )
        # If knight was played before dice, we still need to roll
        elif s.dice is None:
            s.pending_action = PendingAction(type="rollDice")

    def _exec_play_road_building(self, s: GameState, a: Action, events: list[Event]):
        self._use_dev_card(s, a.player, DevCard.ROAD_BUILDING)
        events.append(Event(type="roadBuildingPlayed", player=a.player))
        roads_available = s.config.max_roads - s.board.player_road_count(a.player)
        s.pending_action = PendingAction(type="placeFreeRoad", remaining=min(2, roads_available))

    def _exec_play_year_of_plenty(self, s: GameState, a: Action, events: list[Event]):
        self._use_dev_card(s, a.player, DevCard.YEAR_OF_PLENTY)
        player = s.get_player(a.player)
        for r in a.resources:
            player.hand[r] = player.hand.get(r, 0) + 1
            s.bank[r] = s.bank.get(r, 0) - 1
        events.append(Event(type="yearOfPlentyPlayed", player=a.player,
                            resource_list=list(a.resources)))

    def _exec_play_monopoly(self, s: GameState, a: Action, events: list[Event]):
        self._use_dev_card(s, a.player, DevCard.MONOPOLY)
        player = s.get_player(a.player)
        collected: dict[PlayerColor, int] = {}
        for other in s.players:
            if other.color != a.player:
                amount = other.hand.get(a.resource, 0)
                if amount > 0:
                    other.hand[a.resource] = 0
                    player.hand[a.resource] = player.hand.get(a.resource, 0) + amount
                    collected[other.color] = amount
        events.append(Event(type="monopolyPlayed", player=a.player,
                            resource=a.resource, collected=collected))

    # -- trading -------------------------------------------------------------

    def _exec_propose_trade(self, s: GameState, a: Action, events: list[Event]):
        trade = Trade(
            trade_id=s.next_trade_id,
            proposer=a.player,
            offering=dict(a.offering),
            requesting=dict(a.requesting),
        )
        s.trades.append(trade)
        s.next_trade_id += 1
        s.trades_proposed_this_turn += 1
        events.append(Event(type="tradeProposed", player=a.player,
                            trade_id=trade.trade_id,
                            offering=trade.offering, requesting=trade.requesting))

    def _exec_accept_trade(self, s: GameState, a: Action, events: list[Event]):
        trade = self._find_trade(s, a.trade_id)
        trade.accepting.append(a.player)
        events.append(Event(type="tradeAccepted", player=a.player, trade_id=a.trade_id))

    def _exec_reject_trade(self, s: GameState, a: Action, events: list[Event]):
        trade = self._find_trade(s, a.trade_id)
        trade.rejected.append(a.player)
        events.append(Event(type="tradeRejected", player=a.player, trade_id=a.trade_id))

    def _exec_confirm_trade(self, s: GameState, a: Action, events: list[Event]):
        trade = self._find_trade(s, a.trade_id)
        proposer = s.get_player(a.player)
        partner = s.get_player(a.partner)

        proposer.hand = subtract_resources(proposer.hand, trade.offering)
        proposer.hand = add_resources(proposer.hand, trade.requesting)
        partner.hand = subtract_resources(partner.hand, trade.requesting)
        partner.hand = add_resources(partner.hand, trade.offering)

        s.trades = [t for t in s.trades if t.trade_id != a.trade_id]
        events.append(Event(type="tradeExecuted", player=a.player, partner=a.partner,
                            trade_id=a.trade_id,
                            offering=trade.offering, requesting=trade.requesting))

    def _exec_cancel_trade(self, s: GameState, a: Action, events: list[Event]):
        s.trades = [t for t in s.trades if t.trade_id != a.trade_id]
        events.append(Event(type="tradeCancelled", player=a.player, trade_id=a.trade_id))

    def _exec_trade_with_bank(self, s: GameState, a: Action, events: list[Event]):
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, a.offering)
        player.hand = add_resources(player.hand, a.requesting)
        for r, n in a.offering.items():
            s.bank[r] = s.bank.get(r, 0) + n
        for r, n in a.requesting.items():
            s.bank[r] = s.bank.get(r, 0) - n
        events.append(Event(type="bankTradeExecuted", player=a.player,
                            offering=a.offering, requesting=a.requesting))

    # -- robber / discard ----------------------------------------------------

    def _exec_move_robber(self, s: GameState, a: Action, events: list[Event]):
        s.board.robber = a.location
        events.append(Event(type="robberMoved", player=a.player, location=a.location))

        targets = self._get_steal_targets(s, a.player, a.location)
        if targets:
            s.pending_action = PendingAction(
                type="stealFromPlayer",
                location=a.location,
                targets=targets,
            )
        else:
            s.pending_action = None

    def _exec_steal_from_player(self, s: GameState, a: Action, events: list[Event]):
        target = s.get_player(a.target)
        if target.hand_size > 0:
            # Steal random resource
            cards = []
            for r, n in target.hand.items():
                cards.extend([r] * n)
            stolen = self.rng.choice(cards)
            target.hand[stolen] -= 1
            if target.hand[stolen] == 0:
                del target.hand[stolen]
            thief = s.get_player(a.player)
            thief.hand[stolen] = thief.hand.get(stolen, 0) + 1
            events.append(Event(type="resourceStolen", player=a.player,
                                target=a.target, resource=stolen))
        else:
            events.append(Event(type="nothingToSteal", player=a.player, target=a.target))

        # If knight was played pre-roll, restore rollDice pending
        if s.dice is None:
            s.pending_action = PendingAction(type="rollDice")
        else:
            s.pending_action = None

    def _exec_discard_resources(self, s: GameState, a: Action, events: list[Event]):
        player = s.get_player(a.player)
        player.hand = subtract_resources(player.hand, a.resources)
        for r, n in a.resources.items():
            s.bank[r] = s.bank.get(r, 0) + n
        events.append(Event(type="resourcesDiscarded", player=a.player, resources=a.resources))

        # Remove this player from pending discards
        del s.pending_action.players[a.player]
        if not s.pending_action.players:
            s.pending_action = PendingAction(type="moveRobber")

    def _exec_place_free_road(self, s: GameState, a: Action, events: list[Event]):
        edge = a.edge()
        s.board.roads.append(Road(edge=edge, player=a.player))
        events.append(Event(type="freeRoadPlaced", player=a.player,
                            vertices=(edge.v1, edge.v2)))
        s.pending_action.remaining -= 1
        # A second road with no piece left or nowhere to go is forfeited
        # (the rulebook: a player with one road left places one), rather
        # than owed forever.
        if (s.pending_action.remaining <= 0
                or s.board.player_road_count(a.player) >= s.config.max_roads
                or not self._free_road_edges(s, a.player)):
            # Played before the roll, the card hands back to the roll
            s.pending_action = PendingAction(type="rollDice") if s.dice is None else None

    # -- end turn ------------------------------------------------------------

    def _exec_end_turn(self, s: GameState, a: Action, events: list[Event]):
        events.append(Event(type="turnEnded", player=a.player, turn_number=s.turn_number))

        # Clear turn state
        s.trades.clear()
        s.trades_proposed_this_turn = 0
        s.played_dev_card_this_turn = False
        s.dice = None

        # Move bought dev cards to available
        for p in s.players:
            if p.color == a.player:
                p.dev_cards_bought_this_turn.clear()

        # Advance to next player
        idx = s.turn_order.index(s.current_player)
        s.current_player = s.turn_order[(idx + 1) % len(s.turn_order)]
        s.turn_number += 1
        s.pending_action = PendingAction(type="rollDice")

    # -----------------------------------------------------------------------
    # The move made for a seat that misses its deadline
    # -----------------------------------------------------------------------

    def default_action(self, s: GameState, player: PlayerColor) -> Action | None:
        """The move the server makes for `player` when it misses its
        deadline, as colonist.io does: the move the game waits on, resolved
        the least consequential legal way (a roll; the most-held cards
        discarded; the robber where it robs nobody if it can; the first
        steal target; the first free road; the first legal setup placement),
        otherwise the turn ended. `None` when no legal move exists."""
        pa = s.pending_action
        kind = pa.type if pa is not None else None

        def legal(action: Action) -> bool:
            return self._validate(s, action) is None

        if kind == "placeInitialSettlement":
            for tile in s.board.tiles:
                for v in tile.location.vertices():
                    action = Action(type=kind, player=player, vertex=v)
                    if legal(action):
                        return action
            return None
        if kind == "placeInitialRoad":
            last = s.last_setup_settlement
            for v in (last.adjacent_vertices() if last is not None else []):
                action = Action(type=kind, player=player, vertices=(last, v))
                if legal(action):
                    return action
            return None
        if kind == "rollDice" or (kind is None and s.dice is None):
            action = Action(type="rollDice", player=player)
            return action if legal(action) else None
        if kind == "discardResources":
            owed = (pa.players or {}).get(player, 0)
            hand = dict(s.get_player(player).hand)
            picked: ResourceMap = {}
            for _ in range(owed):
                if not hand:
                    break
                r = max(hand, key=lambda x: hand[x])
                picked[r] = picked.get(r, 0) + 1
                hand[r] -= 1
                if hand[r] == 0:
                    del hand[r]
            action = Action(type=kind, player=player, resources=picked)
            return action if legal(action) else None
        if kind == "moveRobber":
            moves = [Action(type=kind, player=player, location=t.location) for t in s.board.tiles]
            moves = [m for m in moves if legal(m)]
            for m in moves:
                if not self._get_steal_targets(s, player, m.location):
                    return m
            return moves[0] if moves else None
        if kind == "stealFromPlayer":
            targets = pa.targets or []
            action = Action(type=kind, player=player, target=targets[0]) if targets else None
            return action if action is not None and legal(action) else None
        if kind == "placeFreeRoad":
            edges = self._free_road_edges(s, player)
            action = Action(type=kind, player=player, vertices=edges[0].vertices) if edges else None
            return action if action is not None and legal(action) else None
        if kind is None:
            action = Action(type="endTurn", player=player)
            return action if legal(action) else None
        return None

    # -----------------------------------------------------------------------
    # Awards
    # -----------------------------------------------------------------------

    def _check_awards(self, s: GameState, a: Action, events: list[Event]):
        # Longest road
        if a.type in ("buildRoad", "placeFreeRoad", "placeInitialRoad",
                       "buildSettlement", "placeInitialSettlement"):
            self._update_longest_road(s, events)

        # Largest army
        if a.type == "playKnight":
            self._update_largest_army(s, a.player, events)

    def _update_longest_road(self, s: GameState, events: list[Event]):
        # Compute longest road for each player
        best_player = None
        best_length = 4  # minimum 5 to qualify

        for color in s.turn_order:
            length = self._compute_longest_road(s, color)
            s.get_player(color).longest_road_length = length
            if length > best_length:
                best_length = length
                best_player = color

        # Check for ties — current holder keeps it on tie
        if best_player is not None:
            # If someone else also has this length, check if current holder is one of them
            tied = [c for c in s.turn_order
                    if s.get_player(c).longest_road_length == best_length]
            if len(tied) > 1 and s.longest_road_player in tied:
                best_player = s.longest_road_player
            elif len(tied) > 1:
                best_player = None  # No one gets it if tied and no current holder

        if best_player != s.longest_road_player:
            s.longest_road_player = best_player
            if best_player:
                events.append(Event(type="longestRoadAwarded",
                                    player=best_player, length=best_length))
            else:
                events.append(Event(type="longestRoadAwarded",
                                    player=None, length=0))

    def _compute_longest_road(self, s: GameState, player: PlayerColor) -> int:
        """DFS for longest simple path in player's road network."""
        adj: dict[VertexPoint, list[VertexPoint]] = defaultdict(list)
        for road in s.board.roads:
            if road.player == player:
                adj[road.edge.v1].append(road.edge.v2)
                adj[road.edge.v2].append(road.edge.v1)

        if not adj:
            return 0

        # Opponent buildings block the road
        blocked = set()
        for bld in s.board.settlements + s.board.cities:
            if bld.player != player:
                blocked.add(bld.vertex)

        max_length = 0

        def dfs(v: VertexPoint, visited: set[Edge], length: int):
            nonlocal max_length
            max_length = max(max_length, length)
            for nb in adj[v]:
                edge = Edge.between(v, nb)
                if edge in visited:
                    continue
                visited.add(edge)
                if nb in blocked:
                    max_length = max(max_length, length + 1)
                else:
                    dfs(nb, visited, length + 1)
                visited.remove(edge)

        # A path may start (or end) at a vertex an opponent has built on;
        # the building only stops it from continuing through. Starting the
        # search at blocked vertices too counts a road that runs between
        # two opponent buildings.
        for start in adj:
            dfs(start, set(), 0)

        return max_length

    def _update_largest_army(self, s: GameState, player: PlayerColor, events: list[Event]):
        p = s.get_player(player)
        if p.knights_played < 3:
            return

        if s.largest_army_player is None:
            s.largest_army_player = player
            events.append(Event(type="largestArmyAwarded",
                                player=player, knights=p.knights_played))
        elif s.largest_army_player != player:
            current = s.get_player(s.largest_army_player)
            if p.knights_played > current.knights_played:
                s.largest_army_player = player
                events.append(Event(type="largestArmyAwarded",
                                    player=player, knights=p.knights_played))

    # -----------------------------------------------------------------------
    # Victory
    # -----------------------------------------------------------------------

    def _check_victory(self, s: GameState, events: list[Event]):
        if s.is_setup_phase or s.winner is not None:
            return
        vp = s.compute_vp(s.current_player)
        if vp >= s.config.victory_points:
            s.winner = s.current_player
            events.append(Event(type="gameWon", player=s.current_player,
                                victory_points=vp))

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _is_road_connected(self, s: GameState, player: PlayerColor, edge: Edge) -> bool:
        """Check if edge connects to player's existing network."""
        for v in edge.vertices:
            # Connected if player has a building here
            b = s.board.building_at(v)
            if b and b.player == player:
                return True
            # Connected if player has a road to this vertex (and no opponent building blocking)
            if b and b.player != player:
                continue  # Opponent building blocks connection
            for road in s.board.roads:
                if road.player == player and v in road.edge.vertices:
                    return True
        return False

    def _is_vertex_connected(self, s: GameState, player: PlayerColor, vertex: VertexPoint) -> bool:
        """Check if vertex is connected to player's road network."""
        for road in s.board.roads:
            if road.player == player and vertex in road.edge.vertices:
                return True
        return False

    def _get_steal_targets(self, s: GameState, thief: PlayerColor,
                           location: HexPoint) -> list[PlayerColor]:
        """Players with buildings adjacent to hex who can be stolen from."""
        targets = set()
        for v in location.vertices():
            b = s.board.building_at(v)
            if b and b.player != thief and s.get_player(b.player).hand_size > 0:
                targets.add(b.player)
        return list(targets)

    def _return_to_bank(self, s: GameState, cost: ResourceMap):
        for r, n in cost.items():
            s.bank[r] = s.bank.get(r, 0) + n

    def _find_trade(self, s: GameState, trade_id: int) -> Trade | None:
        for t in s.trades:
            if t.trade_id == trade_id:
                return t
        return None
