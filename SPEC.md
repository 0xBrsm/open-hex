# OpenHex Specification v0.1

An open standard for hex-based trading and building games. This specification defines JSON formats for game configuration, state, actions, and events for the base game and 1v1 variant.

## Table of Contents

1. [Conventions](#1-conventions)
2. [Enumerations](#2-enumerations)
3. [Coordinate System](#3-coordinate-system)
4. [Game Configuration](#4-game-configuration)
5. [Board](#5-board)
6. [Game State](#6-game-state)
7. [Actions](#7-actions)
8. [Events](#8-events)
9. [Game Flow](#9-game-flow)
10. [Balanced Dice](#10-balanced-dice)
11. [Hidden Information](#11-hidden-information)
12. [1v1 Variant](#12-1v1-variant)
13. [Game Log Format](#13-game-log-format)
14. [Standard Board Layout](#14-standard-board-layout)

---

## 1. Conventions

### JSON Format

- All data is encoded as JSON (RFC 8259)
- Field names use **camelCase**
- Enum values use **camelCase** strings
- Resource/card counts use **object maps** with string keys: `{"wood": 3, "brick": 2}`
- Zero-count entries MAY be omitted from count maps
- Coordinates use **object notation**: `{"x": 0, "y": 0, "z": 0}`
- Arrays are used for ordered collections (turn order, tile list, event log)
- Fields with default/empty values MAY be omitted

### Terminology

| Term | Meaning |
|------|---------|
| Hex | A hexagonal tile on the board |
| Vertex | A corner where 2-3 hexes meet; location for settlements/cities |
| Edge | A border between two adjacent vertices; location for roads |
| Bank | The shared supply of resources and development cards |
| Hand | A player's private resource cards |

---

## 2. Enumerations

### Terrain

| Value | Description |
|-------|-------------|
| `"desert"` | Produces nothing, robber starts here |
| `"forest"` | Produces wood |
| `"hills"` | Produces brick |
| `"pasture"` | Produces sheep |
| `"fields"` | Produces wheat |
| `"mountains"` | Produces ore |

### Resource

| Value | Produced By |
|-------|-------------|
| `"wood"` | Forest |
| `"brick"` | Hills |
| `"sheep"` | Pasture |
| `"wheat"` | Fields |
| `"ore"` | Mountains |

### Player Color

| Value |
|-------|
| `"red"` |
| `"blue"` |
| `"white"` |
| `"orange"` |

For 1v1 games, only `"red"` and `"blue"` are used.

### Development Card

| Value | Count in Deck | Description |
|-------|---------------|-------------|
| `"knight"` | 14 | Move robber, steal a resource |
| `"victoryPoint"` | 5 | Worth 1 VP (hidden until win) |
| `"roadBuilding"` | 2 | Place 2 free roads |
| `"yearOfPlenty"` | 2 | Take any 2 resources from bank |
| `"monopoly"` | 2 | Take all of one resource from all players |

Standard deck: **25 cards** total. Use this deck for all player counts (2-4).

### Building

| Value | Cost | VP |
|-------|------|----|
| `"settlement"` | 1 wood, 1 brick, 1 sheep, 1 wheat | 1 |
| `"city"` | 2 wheat, 3 ore | 2 |
| `"road"` | 1 wood, 1 brick | 0 |

### Robber Mode

| Value | Description |
|-------|-------------|
| `"normal"` | Standard robber rules |
| `"friendly"` | Cannot place robber on a hex adjacent to a player still at starting VP (2 visible points), unless every other hex is (see [Robber Rules](#robber-rules)) |

### Dice Mode

| Value | Description |
|-------|-------------|
| `"random"` | True random 2d6 each roll |
| `"balanced"` | 36-card deck, reshuffled every 25 draws (see [Section 10](#10-balanced-dice)) |

---

## 3. Coordinate System

OpenHex uses **cube coordinates** for a hexagonal grid where the constraint `x + y + z = constant` holds for each element type.

### Hex Coordinates

A hex is identified by `{"x", "y", "z"}` where **x + y + z = 0**.

```json
{"x": 0, "y": 0, "z": 0}
```

The six neighbors of a hex are reached by adding these direction offsets:

| Direction | Offset |
|-----------|--------|
| East | (+1, 0, -1) |
| West | (-1, 0, +1) |
| NE | (+1, -1, 0) |
| NW | (0, -1, +1) |
| SE | (0, +1, -1) |
| SW | (-1, +1, 0) |

### Vertex Coordinates

A vertex is identified by `{"x", "y", "z"}` where **x + y + z = 1** (Type A) or **x + y + z = 2** (Type B).

```json
{"x": 1, "y": 0, "z": 0}
```

Vertices sit at corners where exactly 3 hexes meet. The two types alternate around each hex.

#### Hex to Vertices

Each hex at `(hx, hy, hz)` has 6 surrounding vertices, alternating between Type A (total=1) and Type B (total=2):

| Label | Vertex | Total |
|-------|--------|-------|
| N | `(hx+1, hy, hz+1)` | 2 |
| NE | `(hx+1, hy, hz)` | 1 |
| SE | `(hx+1, hy+1, hz)` | 2 |
| S | `(hx, hy+1, hz)` | 1 |
| SW | `(hx, hy+1, hz+1)` | 2 |
| NW | `(hx, hy, hz+1)` | 1 |

**Example:** The origin hex `(0,0,0)` has vertices: N=(1,0,1), NE=(1,0,0), SE=(1,1,0), S=(0,1,0), SW=(0,1,1), NW=(0,0,1).

#### Vertex to Adjacent Hexes

Each vertex touches exactly 3 hexes.

**Type A vertex** (total=1) at `(vx, vy, vz)` — subtract 1 from each coordinate in turn:
- `(vx-1, vy, vz)`
- `(vx, vy-1, vz)`
- `(vx, vy, vz-1)`

**Type B vertex** (total=2) at `(vx, vy, vz)` — subtract 1 from each pair of coordinates:
- `(vx-1, vy, vz-1)`
- `(vx-1, vy-1, vz)`
- `(vx, vy-1, vz-1)`

#### Vertex Adjacency

Two vertices are **adjacent** (connected by an edge) if and only if one has total=1 and the other has total=2, and they differ in exactly one coordinate by 1.

Equivalently:

**Type A vertex** (total=1) at `(vx, vy, vz)` has 3 neighbors — add 1 to each coordinate in turn:
- `(vx+1, vy, vz)` — total=2
- `(vx, vy+1, vz)` — total=2
- `(vx, vy, vz+1)` — total=2

**Type B vertex** (total=2) at `(vx, vy, vz)` has 3 neighbors — subtract 1 from each coordinate in turn:
- `(vx-1, vy, vz)` — total=1
- `(vx, vy-1, vz)` — total=1
- `(vx, vy, vz-1)` — total=1

Each vertex has exactly **3 adjacent vertices**.

### Edge Representation

An edge connects two adjacent vertices. Since adjacent vertices always have different totals (one is Type A with total=1, one is Type B with total=2), edges are represented as an array of two vertex coordinates with the **Type A vertex first** (total=1 before total=2):

```json
[{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 1}]
```

This ordering is canonical — every valid edge has exactly one representation.

---

## 4. Game Configuration

A game configuration defines the rules and parameters for a game.

```json
{
  "victoryPoints": 10,
  "discardLimit": 7,
  "numPlayers": 4,
  "robberMode": "normal",
  "diceMode": "random",
  "maxSettlements": 5,
  "maxCities": 4,
  "maxRoads": 15
}
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `victoryPoints` | integer | 10 | VP needed to win |
| `discardLimit` | integer | 7 | Max hand size before forced discard on 7 |
| `numPlayers` | integer | 4 | Number of players (2-4) |
| `robberMode` | string | `"normal"` | Robber placement rules |
| `diceMode` | string | `"random"` | Dice rolling method |
| `maxSettlements` | integer | 5 | Max settlements per player |
| `maxCities` | integer | 4 | Max cities per player |
| `maxRoads` | integer | 15 | Max roads per player |

---

## 5. Board

The board represents the physical game state: tiles, ports, the robber, and all placed buildings.

```json
{
  "tiles": [
    {"location": {"x": 0, "y": 0, "z": 0}, "terrain": "desert"},
    {"location": {"x": 1, "y": -1, "z": 0}, "terrain": "forest", "number": 6},
    {"location": {"x": 1, "y": 0, "z": -1}, "terrain": "hills", "number": 8}
  ],
  "ports": [
    {
      "vertices": [
        {"x": 0, "y": -1, "z": 2},
        {"x": 1, "y": -1, "z": 1}
      ],
      "rate": 3
    },
    {
      "vertices": [
        {"x": 2, "y": -1, "z": 0},
        {"x": 2, "y": 0, "z": 0}
      ],
      "rate": 2,
      "resource": "ore"
    }
  ],
  "robber": {"x": 0, "y": 0, "z": 0},
  "settlements": [
    {"vertex": {"x": 0, "y": 0, "z": 1}, "player": "red"}
  ],
  "cities": [
    {"vertex": {"x": 1, "y": 1, "z": 0}, "player": "blue"}
  ],
  "roads": [
    {
      "vertices": [
        {"x": 0, "y": 0, "z": 1},
        {"x": 1, "y": 0, "z": 0}
      ],
      "player": "red"
    }
  ]
}
```

### Tile

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `location` | HexPoint | Yes | Cube coordinate of the hex |
| `terrain` | string | Yes | Terrain type |
| `number` | integer | No | Dice number (2-12). Omitted for desert |

### Port

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `vertices` | [Vertex, Vertex] | Yes | The two vertices where a settlement grants port access |
| `rate` | integer | Yes | Trade rate (2 or 3) |
| `resource` | string | No | Specific resource for 2:1 ports. Omitted for generic 3:1 |

### Vertex Building (Settlement or City)

| Field | Type | Description |
|-------|------|-------------|
| `vertex` | VertexPoint | Location |
| `player` | string | Owner's color |

### Road

| Field | Type | Description |
|-------|------|-------------|
| `vertices` | [VertexPoint, VertexPoint] | The two endpoint vertices (lower total first) |
| `player` | string | Owner's color |

---

## 6. Game State

The complete game state at any point in time. This is the authoritative representation — given a game state, the game can be continued.

```json
{
  "config": {
    "victoryPoints": 10,
    "discardLimit": 7,
    "numPlayers": 4,
    "robberMode": "normal",
    "diceMode": "balanced"
  },
  "board": { ... },
  "bank": {"wood": 15, "brick": 16, "sheep": 14, "wheat": 17, "ore": 19},
  "devCardDeck": {"knight": 12, "victoryPoint": 5, "roadBuilding": 2, "yearOfPlenty": 2, "monopoly": 1},
  "players": [
    {
      "color": "red",
      "hand": {"wood": 3, "brick": 1, "ore": 2},
      "devCards": {"knight": 1},
      "devCardsBoughtThisTurn": {},
      "playedDevCards": {"knight": 2},
      "knightsPlayed": 2,
      "longestRoadLength": 4,
      "victoryPoints": 5,
      "publicVictoryPoints": 4
    }
  ],
  "currentPlayer": "red",
  "turnOrder": ["red", "blue", "white", "orange"],
  "turnNumber": 12,
  "isSetupPhase": false,
  "dice": {"die1": 3, "die2": 4},
  "pendingAction": null,
  "playedDevCardThisTurn": false,
  "trades": [],
  "tradesProposedThisTurn": 0,
  "longestRoadPlayer": null,
  "largestArmyPlayer": null,
  "winner": null,
  "balancedDice": {
    "deck": [[1,1],[3,4],[6,2]],
    "drawCount": 12
  }
}
```

### Player State

Each entry in the `players` array contains both private and public information.

| Field | Type | Visibility | Description |
|-------|------|------------|-------------|
| `color` | string | Public | Player color |
| `hand` | ResourceMap | **Private** | Resources in hand |
| `devCards` | DevCardMap | **Private** | Unplayed development cards |
| `devCardsBoughtThisTurn` | DevCardMap | **Private** | Cards bought this turn (can't be played yet) |
| `playedDevCards` | DevCardMap | Public | Played development cards |
| `knightsPlayed` | integer | Public | Number of knight cards played |
| `longestRoadLength` | integer | Public | Current longest contiguous road |
| `victoryPoints` | integer | **Private** | Total VP including hidden VP cards |
| `publicVictoryPoints` | integer | Public | VP visible to all players |

**Resource Map**: `{"wood": N, "brick": N, "sheep": N, "wheat": N, "ore": N}` — zero-count resources MAY be omitted.

**Dev Card Map**: `{"knight": N, "victoryPoint": N, ...}` — zero-count cards MAY be omitted.

### Turn State

| Field | Type | Description |
|-------|------|-------------|
| `currentPlayer` | string | Whose turn it is |
| `turnOrder` | [string] | Player order for the game |
| `turnNumber` | integer | Current turn (0 = setup) |
| `isSetupPhase` | boolean | True during initial placement |
| `dice` | DiceResult \| null | Last dice roll, null if not yet rolled |
| `playedDevCardThisTurn` | boolean | Whether a dev card was played this turn |
| `tradesProposedThisTurn` | integer | Number of trades proposed this turn |

### Pending Action

The `pendingAction` field indicates what must happen next before the game can proceed. `null` means no pending action (player may take any valid action or end turn).

| Value | Description |
|-------|-------------|
| `null` | No pending action |
| `{"type": "rollDice"}` | Current player must roll dice |
| `{"type": "discardResources", "players": {"red": 4, "blue": 3}}` | Listed players must discard the specified number of cards |
| `{"type": "moveRobber"}` | Current player must move the robber |
| `{"type": "stealFromPlayer", "location": {"x":1,"y":-1,"z":0}, "targets": ["blue","white"]}` | Current player must choose a steal target |
| `{"type": "placeFreeRoad", "remaining": 2}` | Current player must place a free road (from Road Building) |
| `{"type": "placeInitialSettlement"}` | Setup: must place a settlement |
| `{"type": "placeInitialRoad"}` | Setup: must place a road adjacent to last settlement |

### Awards

| Field | Type | Description |
|-------|------|-------------|
| `longestRoadPlayer` | string \| null | Player with Longest Road (5+ segments) |
| `largestArmyPlayer` | string \| null | Player with Largest Army (3+ knights) |

### Balanced Dice State

When `diceMode` is `"balanced"`, the state includes:

| Field | Type | Visibility | Description |
|-------|------|------------|-------------|
| `balancedDice.deck` | [[int,int]] | **Hidden** | Remaining cards as [die1, die2] pairs |
| `balancedDice.drawCount` | integer | Public | Cards drawn since last shuffle (0-24) |

### Dice Result

```json
{"die1": 3, "die2": 4}
```

| Field | Type | Description |
|-------|------|-------------|
| `die1` | integer | First die (1-6) |
| `die2` | integer | Second die (1-6) |

---

## 7. Actions

An action is a player's input to the game. Each action has a `type` and `player`, plus type-specific fields.

### Setup Actions

#### placeInitialSettlement

Place a settlement during the setup phase (no cost).

```json
{"type": "placeInitialSettlement", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

#### placeInitialRoad

Place a road adjacent to the just-placed settlement during setup (no cost).

```json
{"type": "placeInitialRoad", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

### Dice

#### rollDice

Roll the dice at the start of a turn.

```json
{"type": "rollDice", "player": "red"}
```

### Building

#### buildRoad

Build a road (costs 1 wood, 1 brick).

```json
{"type": "buildRoad", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

#### buildSettlement

Build a settlement (costs 1 wood, 1 brick, 1 sheep, 1 wheat).

```json
{"type": "buildSettlement", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

#### buildCity

Upgrade a settlement to a city (costs 2 wheat, 3 ore).

```json
{"type": "buildCity", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

### Development Cards

#### buyDevCard

Buy a development card (costs 1 sheep, 1 wheat, 1 ore).

```json
{"type": "buyDevCard", "player": "red"}
```

#### playKnight

Play a knight card. Moves the robber to the specified hex.

```json
{"type": "playKnight", "player": "red", "location": {"x": 1, "y": -1, "z": 0}}
```

After this action, a `stealFromPlayer` action may be required (see [Pending Actions](#pending-action)).

#### playRoadBuilding

Play a Road Building card. The player then must place 2 free roads.

```json
{"type": "playRoadBuilding", "player": "red"}
```

#### playYearOfPlenty

Play a Year of Plenty card and take 2 resources from the bank.

```json
{"type": "playYearOfPlenty", "player": "red", "resources": ["wheat", "ore"]}
```

The `resources` array MUST contain exactly 2 entries. Both may be the same resource. Each requested resource must be available in the bank.

#### playMonopoly

Play a Monopoly card and name a resource. All other players give all of that resource to you.

```json
{"type": "playMonopoly", "player": "red", "resource": "wheat"}
```

### Trading

#### proposeTrade

Propose a trade to other players.

```json
{
  "type": "proposeTrade",
  "player": "red",
  "tradeId": 1,
  "offering": {"wood": 2},
  "requesting": {"wheat": 1}
}
```

The `tradeId` is a unique integer within the game, assigned sequentially starting from 1.

#### acceptTrade

Accept a proposed trade.

```json
{"type": "acceptTrade", "player": "blue", "tradeId": 1}
```

#### rejectTrade

Reject a proposed trade.

```json
{"type": "rejectTrade", "player": "blue", "tradeId": 1}
```

#### confirmTrade

The proposing player confirms a trade with a specific accepting partner.

```json
{"type": "confirmTrade", "player": "red", "tradeId": 1, "partner": "blue"}
```

#### cancelTrade

Cancel a proposed trade.

```json
{"type": "cancelTrade", "player": "red", "tradeId": 1}
```

#### tradeWithBank

Trade resources with the bank at the applicable rate.

```json
{"type": "tradeWithBank", "player": "red", "offering": {"wood": 4}, "requesting": {"wheat": 1}}
```

The offering must contain exactly one resource type. The count must match the player's best available rate:
- **4:1** — default (no port)
- **3:1** — with a generic port
- **2:1** — with a matching resource port

The requesting must contain exactly one resource type with count 1.

### Robber

#### moveRobber

Move the robber to a new hex (after rolling a 7 or playing a knight).

```json
{"type": "moveRobber", "player": "red", "location": {"x": 1, "y": -1, "z": 0}}
```

The robber MUST be moved to a different hex than its current position. In `"friendly"` robber mode, the robber cannot be placed on a hex adjacent to a player whose visible VP are still the starting 2, unless every other hex is such a hex (see [Robber Rules](#robber-rules)).

#### stealFromPlayer

Steal one random resource from a player who has a settlement or city adjacent to the robber's hex.

```json
{"type": "stealFromPlayer", "player": "red", "target": "blue"}
```

If no players have buildings adjacent to the robber's new hex, this action is skipped. If exactly one player has buildings there, they are automatically targeted (this action may still be required explicitly depending on implementation).

### Discard

#### discardResources

Discard resources when hand exceeds the discard limit after a 7 is rolled. Each player must discard **floor(handSize / 2)** cards.

```json
{"type": "discardResources", "player": "red", "resources": {"wood": 2, "sheep": 1, "ore": 1}}
```

### Free Placement

#### placeFreeRoad

Place a road at no cost (from Road Building card).

```json
{"type": "placeFreeRoad", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

### Turn Management

#### endTurn

End the current player's turn.

```json
{"type": "endTurn", "player": "red"}
```

---

## 8. Events

Events are the outputs produced by the game engine in response to actions. They form the canonical record of what happened and are used for replays, spectating, and state reconstruction.

Each event has a `type` and additional fields depending on the event type.

### Setup Events

#### setupSettlementPlaced

```json
{"type": "setupSettlementPlaced", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

#### setupRoadPlaced

```json
{"type": "setupRoadPlaced", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

#### setupResourcesGranted

Resources granted after placing the second settlement (one of each from adjacent producing tiles).

```json
{"type": "setupResourcesGranted", "player": "red", "resources": {"wood": 1, "wheat": 1, "ore": 1}}
```

### Dice Events

#### diceRolled

```json
{"type": "diceRolled", "player": "red", "die1": 3, "die2": 4}
```

#### resourcesProduced

Resources distributed to players based on the dice roll.

```json
{
  "type": "resourcesProduced",
  "diceTotal": 8,
  "production": {
    "red": {"ore": 1, "wheat": 2},
    "blue": {"ore": 1}
  }
}
```

Only players who received resources are included. If no resources are produced (e.g., robber blocks all matching hexes, or 7 rolled), this event is omitted.

### Building Events

#### settlementBuilt

```json
{"type": "settlementBuilt", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

#### cityBuilt

```json
{"type": "cityBuilt", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}
```

#### roadBuilt

```json
{"type": "roadBuilt", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

### Development Card Events

#### devCardBought

```json
{"type": "devCardBought", "player": "red", "card": "knight"}
```

The `card` field is **private** — see [Hidden Information](#11-hidden-information).

#### knightPlayed

```json
{"type": "knightPlayed", "player": "red", "location": {"x": 1, "y": -1, "z": 0}}
```

#### roadBuildingPlayed

```json
{"type": "roadBuildingPlayed", "player": "red"}
```

Followed by 1-2 `freeRoadPlaced` events.

#### freeRoadPlaced

```json
{"type": "freeRoadPlaced", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]}
```

#### yearOfPlentyPlayed

```json
{"type": "yearOfPlentyPlayed", "player": "red", "resources": ["wheat", "ore"]}
```

#### monopolyPlayed

```json
{
  "type": "monopolyPlayed",
  "player": "red",
  "resource": "wheat",
  "collected": {"blue": 3, "white": 1, "orange": 0}
}
```

Zero-count entries in `collected` MAY be omitted.

### Trading Events

#### tradeProposed

```json
{
  "type": "tradeProposed",
  "player": "red",
  "tradeId": 1,
  "offering": {"wood": 2},
  "requesting": {"wheat": 1}
}
```

#### tradeAccepted

```json
{"type": "tradeAccepted", "player": "blue", "tradeId": 1}
```

#### tradeRejected

```json
{"type": "tradeRejected", "player": "white", "tradeId": 1}
```

#### tradeExecuted

```json
{
  "type": "tradeExecuted",
  "tradeId": 1,
  "player": "red",
  "partner": "blue",
  "offering": {"wood": 2},
  "requesting": {"wheat": 1}
}
```

#### tradeCancelled

```json
{"type": "tradeCancelled", "player": "red", "tradeId": 1}
```

#### bankTradeExecuted

```json
{
  "type": "bankTradeExecuted",
  "player": "red",
  "offering": {"wood": 4},
  "requesting": {"wheat": 1}
}
```

### Robber Events

#### robberMoved

```json
{"type": "robberMoved", "player": "red", "location": {"x": 1, "y": -1, "z": 0}}
```

#### resourceStolen

```json
{"type": "resourceStolen", "player": "red", "target": "blue", "resource": "wheat"}
```

The `resource` field is **private** — only visible to the thief and victim.

#### nothingToSteal

Emitted when the robber is moved to a hex with adjacent buildings but all adjacent players have empty hands.

```json
{"type": "nothingToSteal", "player": "red", "target": "blue"}
```

### Discard Events

#### resourcesDiscarded

```json
{"type": "resourcesDiscarded", "player": "red", "resources": {"wood": 2, "sheep": 1}}
```

The `resources` field is **private** — other players only see that a discard occurred and the count.

### Award Events

#### longestRoadAwarded

```json
{"type": "longestRoadAwarded", "player": "red", "length": 5}
```

Emitted when Longest Road changes hands. Also emitted with `"player": null` if no one qualifies anymore.

#### largestArmyAwarded

```json
{"type": "largestArmyAwarded", "player": "red", "knights": 3}
```

Emitted when Largest Army changes hands.

### Turn Events

#### turnEnded

```json
{"type": "turnEnded", "player": "red", "turnNumber": 12}
```

### Game End

#### gameWon

```json
{"type": "gameWon", "player": "red", "victoryPoints": 10}
```

---

## 9. Game Flow

### Setup Phase

1. Set `isSetupPhase = true`, `turnNumber = 0`
2. **First round** (in turn order): each player places one settlement then one road
3. **Second round** (in reverse turn order): each player places one settlement then one road
4. After each second-round settlement, the player receives one resource for each adjacent producing tile (settlement adjacent to a forest with number 6 grants 1 wood, etc.)
5. Set `isSetupPhase = false`, `turnNumber = 1`
6. First player in turn order begins with a dice roll

### Main Phase Turn

Each turn follows this sequence:

1. **Pre-roll** (optional): Play a knight card before rolling
2. **Roll dice**: Mandatory. Produces a `diceRolled` event
3. **Resolve dice**:
   - **Total 2-6 or 8-12**: Distribute resources. Each settlement adjacent to a hex matching the total produces 1 resource; each city produces 2. Hexes with the robber produce nothing.
   - **Total 7**: All players with more than `discardLimit` cards must discard half (rounded down). Then the active player moves the robber and may steal.
4. **Main actions** (any order, any number of times):
   - Build roads, settlements, cities
   - Buy development cards
   - Play one development card (except VP cards and cards bought this turn)
   - Propose, accept, reject, confirm, or cancel trades
   - Trade with the bank
5. **End turn**: Player submits `endTurn`

### Development Card Rules

- A player may play **at most one** development card per turn
- A card **cannot** be played on the turn it was purchased
- **Knight** cards may be played before rolling dice
- **Victory Point** cards are never "played" — they are automatically counted toward VP and revealed when the player wins
- The one-card-per-turn limit does **not** apply to Victory Point cards (they aren't played)

### Robber Rules

When a 7 is rolled (or a knight is played):

1. Any player with more than `discardLimit` resources must discard `floor(handSize / 2)` resources
2. Active player moves the robber to any **different** land hex
3. If any opponents have settlements/cities adjacent to the new hex, the active player steals one random resource from one of them

**Friendly Robber** (`robberMode: "friendly"`):
- The robber cannot be placed on a hex where it would block a player whose visible VP equals their starting VP (i.e., players who only have their initial 2 settlements)
- If no valid non-friendly hex exists, the robber may be placed on any hex (fallback)

### Longest Road

- Requires a minimum of **5 connected road segments**
- Awarded to the first player to reach 5
- Transfers to another player only if they have a **strictly longer** road
- If the current holder's road is broken (e.g., by an opponent building a settlement on it — not applicable in base game standard rules, but documented for completeness), the award is recalculated
- Worth **2 VP**

### Largest Army

- Requires a minimum of **3 knight cards played**
- Awarded to the first player to reach 3
- Transfers to another player only if they have **strictly more** knights played
- Worth **2 VP**

### Winning

- A player wins when their total VP reaches the `victoryPoints` threshold
- Victory is checked at the **end of any action on the player's own turn**
- VP sources: settlements (1 each), cities (2 each), Victory Point cards (1 each), Longest Road (2), Largest Army (2)

---

## 10. Balanced Dice

When `diceMode` is `"balanced"`, dice rolls use a 36-card deck system instead of random number generation.

### Deck Composition

The deck contains 36 cards, one for each possible outcome of rolling two six-sided dice:

```
(1,1) (1,2) (1,3) (1,4) (1,5) (1,6)
(2,1) (2,2) (2,3) (2,4) (2,5) (2,6)
(3,1) (3,2) (3,3) (3,4) (3,5) (3,6)
(4,1) (4,2) (4,3) (4,4) (4,5) (4,6)
(5,1) (5,2) (5,3) (5,4) (5,5) (5,6)
(6,1) (6,2) (6,3) (6,4) (6,5) (6,6)
```

This yields the natural 2d6 distribution:

| Total | Cards | Probability |
|-------|-------|-------------|
| 2 | 1 | 2.78% |
| 3 | 2 | 5.56% |
| 4 | 3 | 8.33% |
| 5 | 4 | 11.11% |
| 6 | 5 | 13.89% |
| 7 | 6 | 16.67% |
| 8 | 5 | 13.89% |
| 9 | 4 | 11.11% |
| 10 | 3 | 8.33% |
| 11 | 2 | 5.56% |
| 12 | 1 | 2.78% |

### Procedure

1. **Initialization**: At game start (after setup phase ends), create the 36-card deck and shuffle it uniformly at random. Set `drawCount = 0`.

2. **Drawing**: When a dice roll is needed:
   - Draw the top card from the deck
   - Increment `drawCount`
   - The card's `(die1, die2)` values are the dice result

3. **Reshuffling**: After drawing the 25th card (when `drawCount` reaches 25):
   - Recreate the full 36-card deck
   - Shuffle uniformly at random
   - Reset `drawCount = 0`

4. **Next cycle**: Continue drawing from the newly shuffled deck

### Rationale

Drawing 25 of 36 cards before reshuffling ensures:
- The distribution approximates true 2d6 probabilities within each cycle
- 11 cards remain undrawn each cycle, preserving uncertainty about which totals will appear
- No single total can appear more than its natural maximum in 25 draws (e.g., 7 can appear at most 6 times)
- Games feel fair without being deterministic

### State Representation

```json
{
  "balancedDice": {
    "deck": [[3,4],[1,6],[5,5],[2,1]],
    "drawCount": 12
  }
}
```

The `deck` array contains the remaining undrawn cards in order. This field is **hidden** from players.

The `drawCount` is public information — players can know how many cards have been drawn in the current cycle.

---

## 11. Hidden Information

In a multiplayer game, not all state is visible to all players. The spec defines three visibility levels:

### Private (owner only)

- `hand` — a player's resource cards
- `devCards` — a player's unplayed development cards
- `devCardsBoughtThisTurn` — cards purchased this turn
- `victoryPoints` — total VP (includes hidden VP cards)

### Private (involved parties)

- `devCardBought.card` — only the buyer sees which card was drawn
- `resourceStolen.resource` — only the thief and victim see what was stolen
- `resourcesDiscarded.resources` — only the discarding player sees the exact cards

### Public (all players)

- Everything in `board` (tiles, ports, robber, buildings)
- `bank` and `devCardDeck` counts
- `playedDevCards` — played development cards
- `knightsPlayed`, `longestRoadLength`
- `publicVictoryPoints`
- `currentPlayer`, `turnOrder`, `turnNumber`
- `balancedDice.drawCount`
- All event types and fields not listed as private above

### Per-Player Views

When serving state to a specific player, implementations SHOULD:

1. Include full private state only for the requesting player
2. Replace other players' `hand` with `{"cardCount": N}` (total count only)
3. Replace other players' `devCards` with `{"cardCount": N}`
4. Omit `victoryPoints` for other players (only show `publicVictoryPoints`)
5. Omit `balancedDice.deck`
6. Redact private fields in events (replace with `null` or omit)

### Public Card Counts

Other players can see:

| Field | Visible As |
|-------|-----------|
| `hand` | Total card count |
| `devCards` | Total unplayed dev card count |
| `devCardsBoughtThisTurn` | Count of cards bought this turn |
| `devCardDeck` | Total cards remaining in deck |

---

## 12. 1v1 Variant

The 1v1 variant uses the same board and rules as the standard game with these configuration changes:

```json
{
  "victoryPoints": 15,
  "discardLimit": 9,
  "numPlayers": 2,
  "robberMode": "friendly",
  "diceMode": "balanced"
}
```

| Setting | Standard | 1v1 |
|---------|----------|-----|
| Victory Points | 10 | 15 |
| Discard Limit | 7 | 9 |
| Players | 3-4 | 2 |
| Robber Mode | normal | friendly |
| Dice Mode | random | balanced (recommended) |

**Dev Card Deck**: Always use the standard 25-card deck (14 knights, 5 VP, 2 monopoly, 2 road building, 2 year of plenty) regardless of player count. Do NOT scale down for 2-player games.

**Board**: Uses the standard 19-hex board. The same board layout works for all player counts (2-4).

**Setup**: Same as standard (first settlement forward, second settlement reverse). With 2 players, each player places 2 settlements and 2 roads.

---

## 13. Game Log Format

A game log is the canonical record of a complete game. Given the initial configuration, board, and sequence of events, the entire game can be faithfully replayed.

```json
{
  "openhex": "0.1",
  "config": {
    "victoryPoints": 10,
    "discardLimit": 7,
    "numPlayers": 4,
    "robberMode": "normal",
    "diceMode": "random"
  },
  "board": {
    "tiles": [ ... ],
    "ports": [ ... ],
    "robber": {"x": 0, "y": 0, "z": 0}
  },
  "turnOrder": ["red", "blue", "white", "orange"],
  "events": [
    {"type": "setupSettlementPlaced", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}},
    {"type": "setupRoadPlaced", "player": "red", "vertices": [{"x": 0, "y": 0, "z": 1}, {"x": 1, "y": 0, "z": 0}]},
    ...
    {"type": "gameWon", "player": "red", "victoryPoints": 10}
  ]
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `openhex` | string | Yes | Spec version (e.g., `"0.1"`) |
| `config` | GameConfig | Yes | Game configuration |
| `board` | Board | Yes | Initial board state (tiles, ports, robber — no buildings) |
| `turnOrder` | [string] | Yes | Player turn order |
| `events` | [Event] | Yes | Ordered list of all game events |

The `board` in a game log represents the **initial state** before any buildings are placed. Buildings are reconstructed by replaying the events.

### Replay Reconstruction

To reconstruct game state at any point:

1. Start with the initial board (from `board`) and config
2. Initialize empty player states for each player in `turnOrder`
3. Process events sequentially, updating the game state for each event
4. At any point, the accumulated state is the game state at that moment

---

## 14. Standard Board Layout

### 4-Player Standard Board

The standard board consists of 19 land hexes arranged in a 3-ring pattern using cube coordinates.

#### Hex Positions

**Center (ring 0):**
```json
{"x": 0, "y": 0, "z": 0}
```

**Inner ring (ring 1) — 6 hexes:**
```json
{"x": 1, "y": -1, "z": 0}
{"x": 1, "y": 0, "z": -1}
{"x": 0, "y": 1, "z": -1}
{"x": -1, "y": 1, "z": 0}
{"x": -1, "y": 0, "z": 1}
{"x": 0, "y": -1, "z": 1}
```

**Outer ring (ring 2) — 12 hexes:**
```json
{"x": 2, "y": -2, "z": 0}
{"x": 2, "y": -1, "z": -1}
{"x": 2, "y": 0, "z": -2}
{"x": 1, "y": 1, "z": -2}
{"x": 0, "y": 2, "z": -2}
{"x": -1, "y": 2, "z": -1}
{"x": -2, "y": 2, "z": 0}
{"x": -2, "y": 1, "z": 1}
{"x": -2, "y": 0, "z": 2}
{"x": -1, "y": -1, "z": 2}
{"x": 0, "y": -2, "z": 2}
{"x": 1, "y": -2, "z": 1}
```

#### Terrain Distribution

| Terrain | Count |
|---------|-------|
| Forest | 4 |
| Hills | 3 |
| Pasture | 4 |
| Fields | 4 |
| Mountains | 3 |
| Desert | 1 |

**Total: 19 hexes**

#### Number Tokens

18 tokens for non-desert hexes, placed in a spiral starting from a randomly chosen outer-ring corner, going clockwise and spiraling inward to the center, skipping the desert hex:

```
5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11
```

The 6 possible starting corners are the corner hexes of ring 2 (indices 0, 2, 4, 6, 8, 10). The spiral visits all 12 outer hexes, then all 6 inner hexes, then the center.

Note: There is no 7 token (7 triggers the robber).

#### Ports

9 ports around the board perimeter:

| Type | Count |
|------|-------|
| Generic (3:1) | 4 |
| Wood (2:1) | 1 |
| Brick (2:1) | 1 |
| Sheep (2:1) | 1 |
| Wheat (2:1) | 1 |
| Ore (2:1) | 1 |

#### Board Generation

For a randomized board:
1. Shuffle the 19 terrain types and assign to hex positions
2. Pick a random starting corner (one of 6 outer-ring corners)
3. Place number tokens on non-desert hexes following the spiral from that corner inward to center
4. Ensure no two adjacent hexes both have 6 or 8 (red numbers) — if violated, re-shuffle and re-place
5. Shuffle the 5 resource port types among the 5 2:1 port positions
6. Place the robber on the desert hex

---

## Appendix A: Building Costs Quick Reference

| Building | Wood | Brick | Sheep | Wheat | Ore |
|----------|------|-------|-------|-------|-----|
| Road | 1 | 1 | | | |
| Settlement | 1 | 1 | 1 | 1 | |
| City | | | | 2 | 3 |
| Dev Card | | | 1 | 1 | 1 |

## Appendix B: Victory Point Sources

| Source | VP | Notes |
|--------|-----|-------|
| Settlement | 1 | Per settlement on the board |
| City | 2 | Per city on the board (replaces settlement) |
| Victory Point card | 1 | Per card held (hidden until win) |
| Longest Road | 2 | Min 5 road segments |
| Largest Army | 2 | Min 3 knights played |

## Appendix C: Resource Production Reference

| Number | Probability | Dots on Token |
|--------|-------------|---------------|
| 2 | 1/36 (2.8%) | 1 dot |
| 3 | 2/36 (5.6%) | 2 dots |
| 4 | 3/36 (8.3%) | 3 dots |
| 5 | 4/36 (11.1%) | 4 dots |
| 6 | 5/36 (13.9%) | 5 dots |
| 8 | 5/36 (13.9%) | 5 dots |
| 9 | 4/36 (11.1%) | 4 dots |
| 10 | 3/36 (8.3%) | 3 dots |
| 11 | 2/36 (5.6%) | 2 dots |
| 12 | 1/36 (2.8%) | 1 dot |
