/* OpenHex SPA — reads Firestore directly for public data. */

// Firebase config — replace with your project's config
const firebaseConfig = {
  apiKey: "AIzaSyA2uNXw2pq-cjv5lKYEO305pQPqVyF8Oto",
  authDomain: "open-hex.firebaseapp.com",
  projectId: "open-hex",
  storageBucket: "open-hex.firebasestorage.app",
  messagingSenderId: "1060048530612",
  appId: "1:1060048530612:web:4e87ee9dfabf8b9545bbe1",
};

firebase.initializeApp(firebaseConfig);
const db = firebase.firestore();

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

const $app = document.getElementById("app");
const $nav = document.getElementById("nav");

const NAV_LINKS = [
  { hash: "#/", label: "Home", key: "home" },
  { hash: "#/leaderboard/1v1", label: "1v1 Leaderboard", key: "1v1" },
  { hash: "#/leaderboard/4p", label: "4p Leaderboard", key: "4p" },
  { hash: "#/games", label: "Games", key: "games" },
  { hash: "#/guide", label: "Guide", key: "guide" },
  { href: "https://github.com/bovard/open-hex", label: "GitHub", key: "github" },
];

function renderNav(activeKey) {
  $nav.innerHTML = NAV_LINKS.map(
    (l) =>
      `<a href="${l.href || l.hash}" class="${l.key === activeKey ? "active" : ""}">${l.label}</a>`
  ).join("");
}

function route() {
  const hash = location.hash || "#/";
  const parts = hash.slice(2).split("/"); // remove "#/"

  if (parts[0] === "" || parts[0] === undefined) {
    renderNav("home");
    renderHome();
  } else if (parts[0] === "leaderboard" && parts[1]) {
    renderNav(parts[1]);
    renderLeaderboard(parts[1], parts[2] === "all");
  } else if (parts[0] === "bots" && parts[1]) {
    renderNav("");
    renderBotDetail(parts[1]);
  } else if (parts[0] === "games" && parts[1]) {
    renderNav("games");
    renderGameDetail(parts[1]);
  } else if (parts[0] === "games") {
    renderNav("games");
    renderGamesList();
  } else if (parts[0] === "guide") {
    renderNav("guide");
    renderGuide();
  } else if (parts[0] === "coordinates") {
    renderNav("guide");
    renderCoordinates();
  } else if (parts[0] === "board-setup") {
    renderNav("guide");
    renderBoardSetup();
  } else if (parts[0] === "events-reference") {
    renderNav("guide");
    renderEventsReference();
  } else {
    renderNav("");
    $app.innerHTML = '<p class="empty">Page not found</p>';
  }
}

// Replay viewer state (must be declared before route() runs)
let _replayData = null;
let _replayPos = 0;

window.addEventListener("hashchange", route);
route();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

function colorDot(color) {
  return `<span class="color-dot color-${esc(color)}"></span>`;
}

function fmtElo(elo) {
  return Math.round(elo || 1500);
}

function timeAgo(ts) {
  if (!ts) return "";
  // Firestore Timestamp objects have .toDate(); plain strings/numbers use Date constructor
  const date = typeof ts.toDate === "function" ? ts.toDate() : new Date(ts);
  if (isNaN(date.getTime())) return "";
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

// Leaderboards only show bots that played within this many days, unless "See all" is on
const ACTIVE_DAYS = 30;

function playedRecently(bot) {
  if (!bot.lastGameAt) return false;
  const t = new Date(bot.lastGameAt).getTime();
  return !isNaN(t) && Date.now() - t <= ACTIVE_DAYS * 24 * 60 * 60 * 1000;
}

function toggleLabels(svgId, btn, mode) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const showCoord = mode === "coord";
  svg.querySelectorAll(".label-coord").forEach(el => el.setAttribute("display", showCoord ? "" : "none"));
  svg.querySelectorAll(".label-index").forEach(el => el.setAttribute("display", showCoord ? "none" : ""));
  // Update tab styling
  const tabs = btn.parentElement.querySelectorAll("a");
  tabs.forEach(a => a.classList.remove("active"));
  btn.classList.add("active");
}

// ---------------------------------------------------------------------------
// Replay viewer
// ---------------------------------------------------------------------------

const TERRAIN_COLORS = {
  forest: "#2d6a4f", hills: "#b5651d", pasture: "#a7c957", fields: "#e9c46a",
  mountains: "#6c757d", desert: "#d4a574",
};
const PLAYER_COLORS = {
  red: "#ef4444", blue: "#3b82f6", white: "#e2e8f0", orange: "#f97316",
  green: "#22c55e", brown: "#92400e",
};
const RES_EMOJI = { wood: "🪵", brick: "🧱", sheep: "🐑", wheat: "🌾", ore: "⛏️" };
const TERRAIN_RES = { forest: "wood", hills: "brick", pasture: "sheep", fields: "wheat", mountains: "ore" };

// Hex to pixel (flat-top, same as coordinates page — y negated for SVG)
const R_HEX = 32;
function _h2p(x, y, z) {
  const px = R_HEX * (3 / 2) * x;
  const py = R_HEX * (Math.sqrt(3) / 2 * x + Math.sqrt(3) * y);
  return [px, -py];
}
function _v2p(x, y, z) {
  const sum = x + y + z;
  if (sum === 1) {
    const h1 = [x, y - 1, z], h2 = [x - 1, y, z], h3 = [x, y, z - 1];
    const p1 = _h2p(...h1), p2 = _h2p(...h2), p3 = _h2p(...h3);
    return [(p1[0] + p2[0] + p3[0]) / 3, (p1[1] + p2[1] + p3[1]) / 3];
  } else {
    const h1 = [x - 1, y, z - 1], h2 = [x, y - 1, z - 1], h3 = [x - 1, y - 1, z];
    const p1 = _h2p(...h1), p2 = _h2p(...h2), p3 = _h2p(...h3);
    return [(p1[0] + p2[0] + p3[0]) / 3, (p1[1] + p2[1] + p3[1]) / 3];
  }
}

// Build SVG for the board and current state
function buildReplayBoardSvg(board, state) {
  const tiles = board.tiles || [];

  // Compute vertex positions
  function vKey(v) { return `${v.x},${v.y},${v.z}`; }

  // Hex corners for flat-top
  function hexCorners(cx, cy) {
    const c = [];
    for (let i = 0; i < 6; i++) {
      const a = Math.PI / 180 * (60 * i);
      c.push([cx + R_HEX * Math.cos(a), cy + R_HEX * Math.sin(a)]);
    }
    return c;
  }

  // Compute bounds
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const t of tiles) {
    const [px, py] = _h2p(t.location.x, t.location.y, t.location.z);
    const corners = hexCorners(px, py);
    for (const [cx, cy] of corners) {
      minX = Math.min(minX, cx); maxX = Math.max(maxX, cx);
      minY = Math.min(minY, cy); maxY = Math.max(maxY, cy);
    }
  }
  const pad = 45;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;

  let svg = `<svg viewBox="${minX} ${minY} ${maxX - minX} ${maxY - minY}" style="width:100%;max-width:600px;display:block;margin:0 auto;">`;

  // Draw hexes
  for (const t of tiles) {
    const [px, py] = _h2p(t.location.x, t.location.y, t.location.z);
    const corners = hexCorners(px, py);
    const pts = corners.map(c => c.join(",")).join(" ");
    const fill = TERRAIN_COLORS[t.terrain] || "#333";
    svg += `<polygon points="${pts}" fill="${fill}" stroke="#1e293b" stroke-width="1.5" opacity="0.85"/>`;
    // Number token
    if (t.number) {
      const isRed = t.number === 6 || t.number === 8;
      svg += `<circle cx="${px}" cy="${py}" r="11" fill="#f8fafc" stroke="#334155" stroke-width="1"/>`;
      svg += `<text x="${px}" y="${py + 4}" text-anchor="middle" fill="${isRed ? '#dc2626' : '#1e293b'}" font-size="11" font-weight="${isRed ? '800' : '600'}" font-family="SF Mono,monospace">${t.number}</text>`;
    }
  }

  // Draw robber
  if (state.robber) {
    const [rx, ry] = _h2p(state.robber.x, state.robber.y, state.robber.z);
    svg += `<circle cx="${rx}" cy="${ry + 16}" r="6" fill="#000" opacity="0.7"/>`;
    svg += `<text x="${rx}" y="${ry + 20}" text-anchor="middle" fill="#fff" font-size="8" font-weight="bold">R</text>`;
  }

  // Draw roads
  for (const road of state.roads) {
    const [x1, y1] = _v2p(road.v1.x, road.v1.y, road.v1.z);
    const [x2, y2] = _v2p(road.v2.x, road.v2.y, road.v2.z);
    const c = PLAYER_COLORS[road.player] || "#fff";
    svg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="4" stroke-linecap="round"/>`;
  }

  // Draw settlements and cities
  for (const b of state.buildings) {
    const [bx, by] = _v2p(b.vertex.x, b.vertex.y, b.vertex.z);
    const c = PLAYER_COLORS[b.player] || "#fff";
    if (b.type === "city") {
      // City: larger square
      svg += `<rect x="${bx - 7}" y="${by - 7}" width="14" height="14" fill="${c}" stroke="#0f172a" stroke-width="1.5" rx="2"/>`;
    } else {
      // Settlement: small house shape
      svg += `<polygon points="${bx},${by - 7} ${bx + 6},${by - 2} ${bx + 6},${by + 5} ${bx - 6},${by + 5} ${bx - 6},${by - 2}" fill="${c}" stroke="#0f172a" stroke-width="1.5"/>`;
    }
  }

  // Draw ports
  for (const port of board.ports || []) {
    const v1 = port.vertices[0], v2 = port.vertices[1];
    const [px1, py1] = _v2p(v1.x, v1.y, v1.z);
    const [px2, py2] = _v2p(v2.x, v2.y, v2.z);
    const mx = (px1 + px2) / 2, my = (py1 + py2) / 2;
    const label = port.resource ? `${port.rate}:1 ${RES_EMOJI[port.resource] || port.resource}` : `${port.rate}:1`;
    svg += `<line x1="${px1}" y1="${py1}" x2="${px2}" y2="${py2}" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3,2" opacity="0.5"/>`;
    svg += `<text x="${mx}" y="${my - 4}" text-anchor="middle" fill="#94a3b8" font-size="7" font-family="SF Mono,monospace">${label}</text>`;
  }

  svg += "</svg>";
  return svg;
}

// Process events up to position to derive game state
function deriveStateAtPosition(replay, pos) {
  const players = {};
  for (const c of replay.turnOrder) {
    players[c] = { hand: {}, devCards: {}, devCardsBoughtThisTurn: {}, settlements: 0, cities: 0, roads: 0,
      knightsPlayed: 0, vpCards: 0, longestRoad: false, largestArmy: false, publicVP: 0 };
  }

  const buildings = []; // { vertex, player, type: "settlement"|"city" }
  const roads = []; // { v1, v2, player }
  let robber = replay.board.robber ? { ...replay.board.robber } : null;
  let longestRoadPlayer = null, longestRoadLen = 0;
  let largestArmyPlayer = null, largestArmyCount = 0;
  let turnNumber = 0;
  let lastDice = null;
  let currentEvent = null;

  const events = replay.events || [];
  for (let i = 0; i <= pos && i < events.length; i++) {
    const ev = events[i];
    const p = ev.player;
    currentEvent = ev;

    switch (ev.type) {
      case "setupSettlementPlaced":
      case "settlementBuilt":
        buildings.push({ vertex: ev.vertex, player: p, type: "settlement" });
        if (players[p]) {
          players[p].settlements++;
          players[p].publicVP++;
          // Deduct resources for non-setup
          if (ev.type === "settlementBuilt" && players[p]) {
            _deduct(players[p].hand, { wood: 1, brick: 1, sheep: 1, wheat: 1 });
          }
        }
        break;
      case "setupRoadPlaced":
      case "roadBuilt":
      case "freeRoadPlaced":
        if (ev.vertices && ev.vertices.length === 2) {
          roads.push({ v1: ev.vertices[0], v2: ev.vertices[1], player: p });
          if (players[p]) players[p].roads++;
          if (ev.type === "roadBuilt") _deduct(players[p]?.hand, { wood: 1, brick: 1 });
        }
        break;
      case "cityBuilt":
        // Upgrade settlement to city
        if (ev.vertex) {
          const idx = buildings.findIndex(b => b.player === p && b.type === "settlement" &&
            b.vertex.x === ev.vertex.x && b.vertex.y === ev.vertex.y && b.vertex.z === ev.vertex.z);
          if (idx >= 0) buildings[idx].type = "city";
          if (players[p]) {
            players[p].settlements--;
            players[p].cities++;
            players[p].publicVP++; // net +1 (was 1 for settlement, now 2 for city)
            _deduct(players[p].hand, { wheat: 2, ore: 3 });
          }
        }
        break;
      case "setupResourcesGranted":
      case "resourcesProduced":
        if (ev.resources && p && players[p]) {
          _add(players[p].hand, ev.resources);
        }
        if (ev.production) {
          for (const [pc, res] of Object.entries(ev.production)) {
            if (players[pc]) _add(players[pc].hand, res);
          }
        }
        break;
      case "diceRolled":
        lastDice = { die1: ev.die1, die2: ev.die2, total: (ev.die1 || 0) + (ev.die2 || 0) };
        break;
      case "turnEnded":
        turnNumber = ev.turnNumber || turnNumber + 1;
        // Clear bought-this-turn
        for (const c of replay.turnOrder) {
          if (players[c]) players[c].devCardsBoughtThisTurn = {};
        }
        break;
      case "devCardBought":
        if (players[p]) {
          const card = ev.card || "unknown";
          players[p].devCards[card] = (players[p].devCards[card] || 0) + 1;
          players[p].devCardsBoughtThisTurn[card] = (players[p].devCardsBoughtThisTurn[card] || 0) + 1;
          _deduct(players[p].hand, { wheat: 1, ore: 1, sheep: 1 });
        }
        break;
      case "knightPlayed":
        if (players[p]) {
          players[p].knightsPlayed++;
          players[p].devCards["knight"] = Math.max(0, (players[p].devCards["knight"] || 0) - 1);
        }
        break;
      case "monopolyPlayed":
        if (players[p]) {
          players[p].devCards["monopoly"] = Math.max(0, (players[p].devCards["monopoly"] || 0) - 1);
          // Collect from all other players
          if (ev.resource) {
            for (const [oc, op] of Object.entries(players)) {
              if (oc !== p && op.hand[ev.resource]) {
                const stolen = op.hand[ev.resource];
                op.hand[ev.resource] = 0;
                players[p].hand[ev.resource] = (players[p].hand[ev.resource] || 0) + stolen;
              }
            }
          }
        }
        break;
      case "yearOfPlentyPlayed":
        if (players[p]) {
          players[p].devCards["yearOfPlenty"] = Math.max(0, (players[p].devCards["yearOfPlenty"] || 0) - 1);
          if (ev.resources) {
            if (Array.isArray(ev.resources)) {
              for (const r of ev.resources) players[p].hand[r] = (players[p].hand[r] || 0) + 1;
            } else {
              _add(players[p].hand, ev.resources);
            }
          }
        }
        break;
      case "roadBuildingPlayed":
        if (players[p]) {
          players[p].devCards["roadBuilding"] = Math.max(0, (players[p].devCards["roadBuilding"] || 0) - 1);
        }
        break;
      case "victoryPointRevealed":
        if (players[p]) {
          players[p].vpCards++;
          players[p].devCards["victoryPoint"] = Math.max(0, (players[p].devCards["victoryPoint"] || 0) - 1);
        }
        break;
      case "robberMoved":
        if (ev.location) robber = { ...ev.location };
        break;
      case "resourceStolen":
        if (ev.resource && players[p] && ev.target && players[ev.target]) {
          players[ev.target].hand[ev.resource] = Math.max(0, (players[ev.target].hand[ev.resource] || 0) - 1);
          players[p].hand[ev.resource] = (players[p].hand[ev.resource] || 0) + 1;
        }
        break;
      case "resourcesDiscarded":
        if (ev.resources && players[p]) {
          _deduct(players[p].hand, ev.resources);
        }
        break;
      case "bankTradeExecuted":
        if (players[p]) {
          if (ev.offering) _deduct(players[p].hand, ev.offering);
          if (ev.requesting) _add(players[p].hand, ev.requesting);
        }
        break;
      case "longestRoadAwarded":
        if (longestRoadPlayer && players[longestRoadPlayer]) {
          players[longestRoadPlayer].longestRoad = false;
          players[longestRoadPlayer].publicVP -= 2;
        }
        longestRoadPlayer = p;
        if (players[p]) { players[p].longestRoad = true; players[p].publicVP += 2; }
        longestRoadLen = ev.length || 0;
        break;
      case "largestArmyAwarded":
        if (largestArmyPlayer && players[largestArmyPlayer]) {
          players[largestArmyPlayer].largestArmy = false;
          players[largestArmyPlayer].publicVP -= 2;
        }
        largestArmyPlayer = p;
        if (players[p]) { players[p].largestArmy = true; players[p].publicVP += 2; }
        largestArmyCount = ev.knights || 0;
        break;
    }
  }

  // Clean negative resources
  for (const p of Object.values(players)) {
    for (const r of Object.keys(p.hand)) {
      if (p.hand[r] <= 0) delete p.hand[r];
    }
    for (const c of Object.keys(p.devCards)) {
      if (p.devCards[c] <= 0) delete p.devCards[c];
    }
  }

  return { players, buildings, roads, robber, turnNumber, lastDice, currentEvent };
}

function _add(hand, resources) {
  if (!hand || !resources) return;
  for (const [r, n] of Object.entries(resources)) {
    hand[r] = (hand[r] || 0) + n;
  }
}

function _deduct(hand, resources) {
  if (!hand || !resources) return;
  for (const [r, n] of Object.entries(resources)) {
    hand[r] = (hand[r] || 0) - n;
  }
}

async function toggleReplayViewer(gameId) {
  const viewer = document.getElementById("replay-viewer");
  const btn = document.getElementById("replay-toggle-btn");
  if (!viewer) return;

  if (viewer.style.display !== "none") {
    viewer.style.display = "none";
    btn.textContent = "Show Replay Viewer";
    return;
  }

  btn.textContent = "Hide Replay Viewer";
  viewer.style.display = "block";

  if (!_replayData) {
    viewer.innerHTML = '<div class="loading">Loading replay...</div>';
    try {
      const resp = await fetch(`/api/games/${gameId}/replay`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      _replayData = await resp.json();
      _replayPos = 0;
    } catch (e) {
      viewer.innerHTML = `<p class="empty">Failed to load replay: ${esc(e.message)}</p>`;
      return;
    }
  }

  renderReplayStep();
}

function renderReplayStep() {
  const viewer = document.getElementById("replay-viewer");
  if (!viewer || !_replayData) return;

  const events = _replayData.events || [];
  const total = events.length;

  if (total === 0) {
    viewer.innerHTML = '<div class="card"><p class="empty">No events in replay</p></div>';
    return;
  }

  const state = deriveStateAtPosition(_replayData, _replayPos);
  const ev = state.currentEvent;
  const boardSvg = buildReplayBoardSvg(_replayData.board, state);

  // Player stats panels
  let playersHtml = "";
  for (const color of _replayData.turnOrder) {
    const p = state.players[color];
    if (!p) continue;
    const pc = PLAYER_COLORS[color] || "#888";
    const handTotal = Object.values(p.hand).reduce((s, n) => s + n, 0);
    const devTotal = Object.values(p.devCards).reduce((s, n) => s + n, 0);
    const totalVP = p.publicVP + p.vpCards;
    const vpDisplay = p.vpCards > 0 ? `${p.publicVP} (${totalVP})` : `${p.publicVP}`;

    // Resource icons
    let resHtml = "";
    for (const [r, emoji] of Object.entries(RES_EMOJI)) {
      const n = p.hand[r] || 0;
      if (n > 0) resHtml += `<span style="margin-right:0.3rem">${emoji}${n}</span>`;
    }
    if (!resHtml) resHtml = '<span style="color:var(--muted)">none</span>';

    // Dev cards
    let devHtml = "";
    const devNames = { knight: "Kn", roadBuilding: "RB", yearOfPlenty: "YoP", monopoly: "Mon", victoryPoint: "VP" };
    for (const [c, n] of Object.entries(p.devCards)) {
      if (n > 0) devHtml += `<span style="margin-right:0.3rem; background:rgba(255,255,255,0.1); padding:0.1rem 0.3rem; border-radius:3px; font-size:0.7rem">${devNames[c] || c}×${n}</span>`;
    }

    // Awards
    let awards = "";
    if (p.longestRoad) awards += '<span style="color:#a78bfa; font-size:0.7rem; margin-right:0.3rem">🛤️ LR</span>';
    if (p.largestArmy) awards += '<span style="color:#f472b6; font-size:0.7rem; margin-right:0.3rem">⚔️ LA</span>';

    playersHtml += `
      <div style="background:var(--card); border:2px solid ${pc}; border-radius:0.5rem; padding:0.5rem 0.75rem; flex:1; min-width:200px">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.3rem">
          <strong style="color:${pc}; text-transform:capitalize">${esc(color)}</strong>
          <span style="font-size:1.1rem; font-weight:800">${vpDisplay} VP</span>
        </div>
        <div style="font-size:0.8rem; margin-bottom:0.2rem">${resHtml} <span style="color:var(--muted)">(${handTotal})</span></div>
        <div style="font-size:0.8rem; margin-bottom:0.2rem">${devHtml || '<span style="color:var(--muted)">no dev cards</span>'} ${devTotal > 0 ? `<span style="color:var(--muted)">(${devTotal})</span>` : ""}</div>
        <div style="font-size:0.75rem; color:var(--muted)">
          🏠${p.settlements} 🏙️${p.cities} 🛣️${p.roads} ⚔️${p.knightsPlayed}
          ${awards}
        </div>
      </div>`;
  }

  // Current event description
  let eventDesc = "";
  if (ev) {
    const pColor = ev.player ? (PLAYER_COLORS[ev.player] || "#888") : "";
    const pName = ev.player ? `<span style="color:${pColor}; font-weight:700; text-transform:capitalize">${esc(ev.player)}</span>` : "";
    eventDesc = _describeEvent(ev, pName);
  }

  // Dice display
  let diceHtml = "";
  if (state.lastDice) {
    const d = state.lastDice;
    diceHtml = `<span style="background:var(--card); border:1px solid var(--border); padding:0.2rem 0.5rem; border-radius:0.3rem; font-weight:700; font-size:0.9rem">🎲 ${d.die1} + ${d.die2} = ${d.total}</span>`;
  }

  const btnStyle = 'padding:0.3rem 0.6rem; cursor:pointer; background:var(--card); border:1px solid var(--border); color:var(--fg); border-radius:0.3rem; font-size:0.85rem';

  viewer.innerHTML = `
    <div style="margin-bottom:0">
      <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.75rem; flex-wrap:wrap">
        <button onclick="replayJump(0)" style="${btnStyle}">|&lt;&lt;</button>
        <button onclick="replayStep(-10)" style="${btnStyle}">-10</button>
        <button onclick="replayStep(-1)" style="${btnStyle}">&lt;</button>
        <span style="font-weight:600; min-width:80px; text-align:center">${_replayPos + 1} / ${total}</span>
        <button onclick="replayStep(1)" style="${btnStyle}">&gt;</button>
        <button onclick="replayStep(10)" style="${btnStyle}">+10</button>
        <button onclick="replayJump(${total - 1})" style="${btnStyle}">&gt;&gt;|</button>
        ${diceHtml}
        <span style="color:var(--muted); font-size:0.8rem">Turn ${state.turnNumber}</span>
      </div>
      <div style="margin-bottom:0.5rem; font-size:0.9rem">${eventDesc}</div>
      <div style="display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:0.75rem">${playersHtml}</div>
      ${boardSvg}
    </div>
  `;
}

function _describeEvent(ev, pName) {
  switch (ev.type) {
    case "setupSettlementPlaced": return `${pName} placed a settlement`;
    case "setupRoadPlaced": return `${pName} placed a road`;
    case "setupResourcesGranted": {
      const res = ev.resources ? Object.entries(ev.resources).map(([r, n]) => `${n} ${r}`).join(", ") : "";
      return `${pName} received ${res}`;
    }
    case "settlementBuilt": return `${pName} built a settlement`;
    case "roadBuilt": return `${pName} built a road`;
    case "cityBuilt": return `${pName} built a city`;
    case "diceRolled": return `${pName} rolled ${(ev.die1||0) + (ev.die2||0)}`;
    case "resourcesProduced": {
      if (!ev.production) return `Resources produced (dice ${ev.diceTotal})`;
      const parts = [];
      for (const [c, res] of Object.entries(ev.production)) {
        const rList = Object.entries(res).map(([r, n]) => `${n} ${r}`).join(", ");
        parts.push(`<span style="color:${PLAYER_COLORS[c] || '#888'}; text-transform:capitalize">${c}</span>: ${rList}`);
      }
      return `Produced: ${parts.join(" | ")}`;
    }
    case "turnEnded": return `${pName} ended turn ${ev.turnNumber || ""}`;
    case "devCardBought": return `${pName} bought a dev card`;
    case "knightPlayed": return `${pName} played a knight`;
    case "monopolyPlayed": return `${pName} played monopoly on ${ev.resource || "?"}`;
    case "yearOfPlentyPlayed": {
      const res = Array.isArray(ev.resources) ? ev.resources.join(", ") : "";
      return `${pName} played year of plenty: ${res}`;
    }
    case "roadBuildingPlayed": return `${pName} played road building`;
    case "freeRoadPlaced": return `${pName} placed a free road`;
    case "robberMoved": return `${pName} moved the robber`;
    case "resourceStolen": return `${pName} stole from <span style="color:${PLAYER_COLORS[ev.target] || '#888'}; text-transform:capitalize">${ev.target || "?"}</span>`;
    case "resourcesDiscarded": return `${pName} discarded resources`;
    case "bankTradeExecuted": {
      const off = ev.offering ? Object.entries(ev.offering).map(([r, n]) => `${n} ${r}`).join(", ") : "";
      const req = ev.requesting ? Object.entries(ev.requesting).map(([r, n]) => `${n} ${r}`).join(", ") : "";
      return `${pName} traded ${off} for ${req}`;
    }
    case "longestRoadAwarded": return `${pName} earned Longest Road (${ev.length || "?"})`;
    case "largestArmyAwarded": return `${pName} earned Largest Army (${ev.knights || "?"})`;
    case "victoryPointRevealed": return `${pName} revealed a VP card`;
    case "gameWon": return `${pName} won the game! 🎉`;
    default: return `<span style="color:var(--accent)">${esc(ev.type)}</span> ${pName || ""}`;
  }
}

function replayStep(delta) {
  if (!_replayData) return;
  const total = (_replayData.events || []).length;
  _replayPos = Math.max(0, Math.min(total - 1, _replayPos + delta));
  renderReplayStep();
}

function replayJump(pos) {
  if (!_replayData) return;
  const total = (_replayData.events || []).length;
  _replayPos = Math.max(0, Math.min(total - 1, pos));
  renderReplayStep();
}

// ---------------------------------------------------------------------------
// Home page
// ---------------------------------------------------------------------------

async function renderHome() {
  $app.innerHTML = '<div class="loading">Loading...</div>';

  const [botsSnap, activeSnap, completedSnap, queueSnap] = await Promise.all([
    db.collection("bots").get(),
    db.collection("games").where("status", "==", "active").get(),
    db.collection("games").where("status", "in", ["completed", "aborted"]).get(),
    db.collection("queue").get(),
  ]);

  const botCount = botsSnap.size;
  const activeCount = activeSnap.size;
  const completedCount = completedSnap.size;
  const queueCount = queueSnap.size;

  // Top 5 per mode
  const [top1v1, top4p, recentGames] = await Promise.all([
    db.collection("bots").where("mode", "==", "1v1").orderBy("elo1v1", "desc").limit(100).get(),
    db.collection("bots").where("mode", "==", "4p").orderBy("elo4p", "desc").limit(100).get(),
    db.collection("games")
      .where("status", "in", ["completed", "aborted"])
      .orderBy("completedAt", "desc")
      .limit(5)
      .get(),
  ]);

  function miniLb(snap, eloKey, gamesKey, winsKey) {
    if (snap.empty) return '<p class="empty">No bots registered yet</p>';
    const docs = snap.docs.filter((doc) => playedRecently(doc.data())).slice(0, 5);
    if (docs.length === 0) return `<p class="empty">No bots have played in the last ${ACTIVE_DAYS} days</p>`;
    let html = "<table><tr><th>#</th><th>Bot</th><th>ELO</th><th>W/L</th></tr>";
    let rank = 0;
    docs.forEach((doc) => {
      rank++;
      const d = doc.data();
      const g = d[gamesKey] || 0;
      const w = d[winsKey] || 0;
      html += `<tr><td>${rank}</td><td><a href="#/bots/${doc.id}">${esc(d.name)}</a></td>`;
      html += `<td>${fmtElo(d[eloKey])}</td><td>${w}/${g - w}</td></tr>`;
    });
    return html + "</table>";
  }

  let recentHtml = "";
  if (!recentGames.empty) {
    recentHtml =
      '<table><tr><th>Game</th><th>Mode</th><th>Winner</th><th>Status</th><th>When</th></tr>';
    recentGames.forEach((doc) => {
      const g = doc.data();
      const winner = g.winnerName ? esc(g.winnerName) : "—";
      recentHtml += `<tr><td><a href="#/games/${doc.id}">${doc.id.slice(0, 8)}...</a></td>`;
      recentHtml += `<td>${esc(g.mode)}</td><td>${winner}</td>`;
      recentHtml += `<td><span class="badge badge-${g.status}">${esc(g.status)}</span></td>`;
      recentHtml += `<td style="color:var(--muted); font-size:0.85rem">${timeAgo(g.completedAt || g.createdAt)}</td></tr>`;
    });
    recentHtml += "</table>";
  } else {
    recentHtml = '<p class="empty">No completed games yet</p>';
  }

  $app.innerHTML = `
    <div class="stat-grid" style="margin-bottom:1.5rem">
      <div class="stat"><div class="stat-value">${botCount}</div><div class="stat-label">Bots</div></div>
      <div class="stat"><div class="stat-value">${activeCount}</div><div class="stat-label">Active Games</div></div>
      <div class="stat"><div class="stat-value">${completedCount}</div><div class="stat-label">Completed</div></div>
      <div class="stat"><div class="stat-value">${queueCount}</div><div class="stat-label">In Queue</div></div>
    </div>
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem; margin-bottom:1.5rem">
      <div class="card">
        <h2>1v1 Leaderboard</h2>
        ${miniLb(top1v1, "elo1v1", "games1v1", "wins1v1")}
        <div style="margin-top:0.5rem; text-align:right"><a href="#/leaderboard/1v1">View all &rarr;</a></div>
      </div>
      <div class="card">
        <h2>4p Leaderboard</h2>
        ${miniLb(top4p, "elo4p", "games4p", "wins4p")}
        <div style="margin-top:0.5rem; text-align:right"><a href="#/leaderboard/4p">View all &rarr;</a></div>
      </div>
    </div>
    <div class="card">
      <h2>Recent Games</h2>
      ${recentHtml}
      <div style="margin-top:0.5rem; text-align:right"><a href="#/games">View all &rarr;</a></div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Leaderboard
// ---------------------------------------------------------------------------

async function renderLeaderboard(mode, showAll) {
  if (mode !== "1v1" && mode !== "4p") {
    $app.innerHTML = '<p class="empty">Invalid mode</p>';
    return;
  }
  $app.innerHTML = '<div class="loading">Loading...</div>';

  const eloKey = mode === "1v1" ? "elo1v1" : "elo4p";
  const gamesKey = mode === "1v1" ? "games1v1" : "games4p";
  const winsKey = mode === "1v1" ? "wins1v1" : "wins4p";

  const [docs, activeSnap] = await Promise.all([
    db.collection("bots")
      .where("mode", "==", mode)
      .orderBy(eloKey, "desc")
      .limit(100)
      .get(),
    db.collection("games")
      .where("status", "==", "active")
      .where("mode", "==", mode)
      .get(),
  ]);

  // Active games section
  let activeHtml = "";
  if (!activeSnap.empty) {
    activeHtml = '<div class="card" style="margin-bottom:1rem"><h2>Active Games</h2>';
    activeHtml += '<div class="table-scroll"><table><tr><th>Game</th><th>Players</th><th>Turn</th><th>Started</th></tr>';
    activeSnap.forEach((doc) => {
      const g = doc.data();
      const players = (g.players || [])
        .map((p) => `${colorDot(p.color)} <a href="#/bots/${esc(p.botId)}">${esc(p.name)}</a>`)
        .join(" vs ");
      activeHtml += `<tr>
        <td><a href="#/games/${doc.id}">${doc.id.slice(0, 8)}...</a></td>
        <td>${players}</td>
        <td>${g.turnNumber || 0}</td>
        <td style="color:var(--muted); font-size:0.85rem">${timeAgo(g.createdAt)}</td>
      </tr>`;
    });
    activeHtml += '</table></div></div>';
  }

  const base = `#/leaderboard/${mode}`;
  const shown = showAll ? docs.docs : docs.docs.filter((doc) => playedRecently(doc.data()));
  const filterBar = showAll
    ? `<div class="lb-filter">All bots. <a href="${base}">Active only</a></div>`
    : `<div class="lb-filter">Bots that played in the last ${ACTIVE_DAYS} days. <a href="${base}/all">See all</a></div>`;

  let table = "";
  if (docs.empty) {
    table =
      '<p class="empty">No bots registered yet. <a href="#/guide">Check the guide</a> to get started!</p>';
  } else if (shown.length === 0) {
    table = `<p class="empty">No bots have played a ${esc(mode)} game in the last ${ACTIVE_DAYS} days.</p>`;
  } else {
    table =
      '<div class="table-scroll"><table><tr><th>#</th><th>Bot</th><th>ELO</th><th>Games</th><th>Wins</th><th>Win Rate</th><th>Last Game</th></tr>';
    let rank = 0;
    shown.forEach((doc) => {
      rank++;
      const d = doc.data();
      const g = d[gamesKey] || 0;
      const w = d[winsKey] || 0;
      const wr = g > 0 ? `${Math.round((w / g) * 100)}%` : "—";
      table += `<tr>
        <td>${rank}</td>
        <td><a href="#/bots/${doc.id}">${esc(d.name)}</a></td>
        <td><strong>${fmtElo(d[eloKey])}</strong></td>
        <td>${g}</td><td>${w}</td><td>${wr}</td>
        <td style="color:var(--muted); font-size:0.85rem">${timeAgo(d.lastGameAt) || "—"}</td>
      </tr>`;
    });
    table += "</table></div>";
  }

  const cta =
    '<div style="margin-top:1.5rem; text-align:center; padding:1rem; background:var(--card); border:1px solid var(--border); border-radius:8px;">' +
    'Want to compete? <a href="#/guide"><strong>Read the getting started guide</strong></a>.' +
    "</div>";

  $app.innerHTML = `
    <div class="tabs">
      <a href="#/leaderboard/1v1${showAll ? "/all" : ""}" class="${mode === "1v1" ? "active" : ""}">1v1</a>
      <a href="#/leaderboard/4p${showAll ? "/all" : ""}" class="${mode === "4p" ? "active" : ""}">4-Player</a>
    </div>
    ${activeHtml}
    <div class="card">
      <h2>${mode.toUpperCase()} Leaderboard</h2>
      ${filterBar}
      ${table}
      ${cta}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Bot detail
// ---------------------------------------------------------------------------

async function renderBotDetail(botId) {
  $app.innerHTML = '<div class="loading">Loading...</div>';

  const botDoc = await db.collection("bots").doc(botId).get();
  if (!botDoc.exists) {
    $app.innerHTML = '<p class="empty">Bot not found</p>';
    return;
  }
  const bot = botDoc.data();
  const mode = bot.mode || "1v1";
  const eloKey = mode === "1v1" ? "elo1v1" : "elo4p";
  const gamesKey = mode === "1v1" ? "games1v1" : "games4p";
  const winsKey = mode === "1v1" ? "wins1v1" : "wins4p";
  const totalGames = bot[gamesKey] || 0;
  const totalWins = bot[winsKey] || 0;
  const wr = totalGames > 0 ? `${Math.round((totalWins / totalGames) * 100)}%` : "—";

  // Fetch completed games involving this bot
  const gamesSnap = await db
    .collection("games")
    .where("status", "in", ["completed", "aborted"])
    .orderBy("completedAt", "desc")
    .limit(200)
    .get();

  // Filter to games where this bot participated
  const botGames = [];
  gamesSnap.forEach((doc) => {
    const g = doc.data();
    const involved = (g.players || []).some((p) => p.botId === botId);
    if (involved) botGames.push({ id: doc.id, ...g });
  });

  // Head-to-head stats
  const h2h = {}; // opponentId -> { name, wins, losses, games }
  for (const g of botGames) {
    if (g.status !== "completed") continue;
    for (const p of g.players || []) {
      if (p.botId === botId) continue;
      if (!h2h[p.botId]) h2h[p.botId] = { name: p.name, wins: 0, losses: 0, games: 0 };
      h2h[p.botId].games++;
      if (g.winnerBotId === botId) h2h[p.botId].wins++;
      else if (g.winnerBotId === p.botId) h2h[p.botId].losses++;
    }
  }

  // Sort h2h by games desc
  const h2hSorted = Object.entries(h2h).sort((a, b) => b[1].games - a[1].games);

  let h2hHtml = "";
  if (h2hSorted.length > 0) {
    h2hHtml = "<table><tr><th>Opponent</th><th>Games</th><th>Wins</th><th>Losses</th><th>Win Rate</th></tr>";
    for (const [oppId, s] of h2hSorted) {
      const oppWr = s.games > 0 ? `${Math.round((s.wins / s.games) * 100)}%` : "—";
      h2hHtml += `<tr>
        <td><a href="#/bots/${esc(oppId)}">${esc(s.name)}</a></td>
        <td>${s.games}</td><td>${s.wins}</td><td>${s.losses}</td><td>${oppWr}</td>
      </tr>`;
    }
    h2hHtml += "</table>";
  } else {
    h2hHtml = '<p class="empty">No completed games yet</p>';
  }

  // Recent games list
  let recentHtml = "";
  if (botGames.length > 0) {
    recentHtml = "<table><tr><th>Game</th><th>Opponent</th><th>Result</th><th>Turns</th><th>Status</th><th>When</th></tr>";
    for (const g of botGames.slice(0, 30)) {
      const opp = (g.players || []).find((p) => p.botId !== botId);
      const oppName = opp ? esc(opp.name) : "—";
      const oppLink = opp ? `<a href="#/bots/${esc(opp.botId)}">${oppName}</a>` : oppName;
      let result = "—";
      if (g.status === "completed") {
        result = g.winnerBotId === botId
          ? '<span style="color:var(--green); font-weight:700">Win</span>'
          : '<span style="color:var(--red); font-weight:700">Loss</span>';
      }
      recentHtml += `<tr>
        <td><a href="#/games/${esc(g.id)}">${g.id.slice(0, 8)}...</a></td>
        <td>${oppLink}</td>
        <td>${result}</td>
        <td>${g.turnNumber || 0}</td>
        <td><span class="badge badge-${g.status}">${esc(g.status)}</span></td>
        <td style="color:var(--muted); font-size:0.85rem">${timeAgo(g.completedAt || g.createdAt)}</td>
      </tr>`;
    }
    recentHtml += "</table>";
  } else {
    recentHtml = '<p class="empty">No games yet</p>';
  }

  const descHtml = bot.description
    ? `<p style="color:var(--muted); margin-bottom:1rem">${esc(bot.description)}</p>`
    : "";

  $app.innerHTML = `
    <h2 style="margin-bottom:0.5rem">${esc(bot.name)}</h2>
    ${descHtml}
    <div class="stat-grid" style="margin-bottom:1.5rem">
      <div class="stat"><div class="stat-value">${fmtElo(bot[eloKey])}</div><div class="stat-label">ELO</div></div>
      <div class="stat"><div class="stat-value">${totalGames}</div><div class="stat-label">Games</div></div>
      <div class="stat"><div class="stat-value">${totalWins}</div><div class="stat-label">Wins</div></div>
      <div class="stat"><div class="stat-value">${wr}</div><div class="stat-label">Win Rate</div></div>
      <div class="stat"><div class="stat-value"><span class="badge">${esc(mode)}</span></div><div class="stat-label">Mode</div></div>
    </div>
    <div class="card" style="margin-bottom:1rem">
      <h2>Head-to-Head</h2>
      ${h2hHtml}
    </div>
    <div class="card">
      <h2>Recent Games</h2>
      ${recentHtml}
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Games list
// ---------------------------------------------------------------------------

async function renderGamesList() {
  $app.innerHTML = '<div class="loading">Loading...</div>';

  const [activeSnap, docs] = await Promise.all([
    db.collection("games").where("status", "==", "active").get(),
    db.collection("games")
      .where("status", "in", ["completed", "aborted"])
      .orderBy("completedAt", "desc")
      .limit(50)
      .get(),
  ]);

  let rows = "";
  if (docs.empty) {
    rows = '<p class="empty">No games yet</p>';
  } else {
    rows =
      "<table><tr><th>Game</th><th>Mode</th><th>Players</th><th>Winner</th><th>Status</th><th>When</th></tr>";
    docs.forEach((doc) => {
      const g = doc.data();
      const players = (g.players || []).map((p) => esc(p.name)).join(", ");
      const winner = g.winnerName ? esc(g.winnerName) : "—";
      rows += `<tr>
        <td><a href="#/games/${doc.id}">${doc.id.slice(0, 8)}...</a></td>
        <td>${esc(g.mode)}</td>
        <td>${players}</td>
        <td>${winner}</td>
        <td><span class="badge badge-${g.status}">${esc(g.status)}</span></td>
        <td style="color:var(--muted); font-size:0.85rem">${timeAgo(g.completedAt || g.createdAt)}</td>
      </tr>`;
    });
    rows += "</table>";
  }

  // Active games section
  let activeRows = "";
  if (!activeSnap.empty) {
    activeRows = '<div class="card" style="margin-bottom:1rem"><h2>Active Games</h2>';
    activeRows += '<table><tr><th>Game</th><th>Mode</th><th>Players</th><th>Turn</th><th>Started</th></tr>';
    activeSnap.forEach((doc) => {
      const g = doc.data();
      const players = (g.players || [])
        .map((p) => `${colorDot(p.color)} <a href="#/bots/${esc(p.botId)}">${esc(p.name)}</a>`)
        .join(" vs ");
      activeRows += `<tr>
        <td><a href="#/games/${doc.id}">${doc.id.slice(0, 8)}...</a></td>
        <td>${esc(g.mode)}</td>
        <td>${players}</td>
        <td>${g.turnNumber || 0}</td>
        <td style="color:var(--muted); font-size:0.85rem">${timeAgo(g.createdAt)}</td>
      </tr>`;
    });
    activeRows += '</table></div>';
  }

  $app.innerHTML = `${activeRows}<div class="card"><h2>Completed Games</h2>${rows}</div>`;
}

// ---------------------------------------------------------------------------
// Game detail
// ---------------------------------------------------------------------------

async function renderGameDetail(gameId) {
  _replayData = null;
  _replayPos = 0;
  $app.innerHTML = '<div class="loading">Loading...</div>';

  const gameDoc = await db.collection("games").doc(gameId).get();
  if (!gameDoc.exists) {
    $app.innerHTML = '<p class="empty">Game not found</p>';
    return;
  }
  const g = gameDoc.data();

  // Player cards
  let playersHtml = '<div style="display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem">';
  for (const p of g.players || []) {
    const isWinner = p.botId === g.winnerBotId;
    playersHtml += `<div class="card" style="flex:1; min-width:180px">
      <div>${colorDot(p.color)} <strong><a href="#/bots/${esc(p.botId)}">${esc(p.name)}</a></strong></div>
      <div style="color:var(--muted); font-size:0.85rem">Seat ${p.seat}</div>
      ${isWinner ? '<div style="color:var(--green); font-size:0.75rem; font-weight:700">WINNER</div>' : ""}
    </div>`;
  }
  playersHtml += "</div>";

  // Stats
  const createdStr = timeAgo(g.createdAt);
  const completedStr = timeAgo(g.completedAt);
  const timeInfo = completedStr ? `Completed ${completedStr}` : createdStr ? `Started ${createdStr}` : "";
  const statsHtml = `
    <div class="stat-grid" style="margin-bottom:1rem">
      <div class="stat"><div class="stat-value">${esc(g.mode)}</div><div class="stat-label">Mode</div></div>
      <div class="stat"><div class="stat-value">${g.turnNumber || 0}</div><div class="stat-label">Turns</div></div>
      <div class="stat"><div class="stat-value">${g.actionCount || 0}</div><div class="stat-label">Actions</div></div>
      <div class="stat">
        <div class="stat-value"><span class="badge badge-${g.status}">${esc(g.status)}</span></div>
        <div class="stat-label">Status</div>
      </div>
    </div>
    ${timeInfo ? `<div style="color:var(--muted); font-size:0.85rem; margin-bottom:1rem">${timeInfo}</div>` : ""}
  `;

  // Duration stats
  let durationHtml = "";
  const durStats = g.durationStats;
  if (durStats && Object.keys(durStats).length > 0) {
    durationHtml = '<div class="card" style="margin-bottom:1rem"><h2>Think Time</h2>';
    durationHtml += '<table><tr><th>Bot</th><th>Avg (ms)</th><th>Max (ms)</th><th>Total (s)</th><th>Actions</th></tr>';
    for (const p of g.players || []) {
      const ds = durStats[p.botId];
      if (ds && ds.count > 0) {
        const avg = Math.round(ds.totalMs / ds.count);
        const totalSec = (ds.totalMs / 1000).toFixed(1);
        durationHtml += `<tr>
          <td>${colorDot(p.color)} <a href="#/bots/${esc(p.botId)}">${esc(p.name)}</a></td>
          <td>${avg}</td>
          <td>${ds.maxMs}</td>
          <td>${totalSec}</td>
          <td>${ds.count}</td>
        </tr>`;
      }
    }
    durationHtml += '</table></div>';
  }

  // Events
  const eventsSnap = await db
    .collection("games")
    .doc(gameId)
    .collection("events")
    .orderBy("seq")
    .get();

  let eventsHtml = "";
  if (!eventsSnap.empty) {
    eventsHtml =
      '<div style="max-height:500px; overflow-y:auto; font-family:monospace; font-size:0.8rem; background:var(--card); border:1px solid var(--border); border-radius:0.5rem; padding:0.75rem">';
    eventsSnap.forEach((doc) => {
      const e = doc.data();
      const evt = JSON.parse(e.eventJson);
      const type = evt.type || "?";
      const player = evt.player || "";
      eventsHtml += `<div style="padding:0.15rem 0; border-bottom:1px solid rgba(255,255,255,0.03)">`;
      eventsHtml += `<span style="color:var(--muted)">${e.seq}</span> `;
      eventsHtml += `<span style="color:var(--accent)">${esc(type)}</span>`;
      if (player) eventsHtml += ` <strong>${esc(player)}</strong>`;
      eventsHtml += "</div>";
    });
    eventsHtml += "</div>";
  }

  // Action log
  const logSnap = await db
    .collection("games")
    .doc(gameId)
    .collection("actionLog")
    .orderBy("createdAt")
    .get();

  let logHtml = "";
  if (!logSnap.empty) {
    const failCount = logSnap.docs.filter((d) => !d.data().success).length;
    if (failCount > 0) {
      logHtml = `<div class="card" style="margin-top:1rem"><h2>Action Log (${failCount} failed)</h2>`;
      logHtml +=
        "<table><tr><th>Bot</th><th>Action</th><th>Result</th><th>Error</th></tr>";
      logSnap.forEach((doc) => {
        const l = doc.data();
        if (!l.success) {
          const action = JSON.parse(l.actionJson);
          logHtml += `<tr>
            <td>${esc(l.botName)}</td>
            <td><code>${esc(action.type || "?")}</code></td>
            <td><span class="badge badge-aborted">FAIL</span></td>
            <td style="font-size:0.8rem; color:var(--red)">${esc(l.error || "")}</td>
          </tr>`;
        }
      });
      logHtml += "</table></div>";
    }
  }

  // Replay link (only for completed/aborted games)
  const isFinished = g.status === "completed" || g.status === "aborted";
  const replayHtml = isFinished
    ? `<div style="margin-bottom:1rem; display:flex; gap:0.75rem; align-items:center">
        <a href="/api/games/${esc(gameId)}/replay" target="_blank"
           style="display:inline-flex; align-items:center; gap:0.4rem; padding:0.5rem 1rem; background:var(--accent); color:#000; border-radius:0.4rem; text-decoration:none; font-weight:600; font-size:0.9rem">
          Download Replay JSON
        </a>
        <button onclick="toggleReplayViewer('${esc(gameId)}')" id="replay-toggle-btn"
           style="padding:0.5rem 1rem; background:var(--card); border:1px solid var(--border); color:var(--fg); border-radius:0.4rem; cursor:pointer; font-size:0.9rem">
          Show Replay Viewer
        </button>
      </div>
      <div id="replay-viewer" style="display:none"></div>`
    : "";

  $app.innerHTML = `
    <h2 style="margin-bottom:1rem">Game ${esc(gameId.slice(0, 8))}...</h2>
    ${statsHtml}
    ${playersHtml}
    ${durationHtml}
    ${replayHtml}
    <div class="card">
      <h2>Events (${eventsSnap.size})</h2>
      ${eventsHtml || '<p class="empty">No events</p>'}
    </div>
    ${logHtml}
  `;
}

// ---------------------------------------------------------------------------
// Guide (static content)
// ---------------------------------------------------------------------------

function renderGuide() {
  $app.innerHTML = `
  <div class="guide">
    <h1 style="font-size:1.5rem; margin-bottom:0.5rem;">Getting Started</h1>
    <p style="color:var(--muted); margin-bottom:1.5rem;">
      Build a bot that plays OpenHex games through the REST API.
    </p>
    <div class="toc">
      <strong>Contents</strong>
      <ol>
        <li><a href="#register">Register Your Bot</a></li>
        <li><a href="#game-spec">Game Specification</a></li>
        <li><a href="#events">Parsing &amp; Responding to Game Events</a></li>
        <li><a href="#queue">Joining the Queue</a></li>
        <li><a href="#game-loop">The Full Game Loop</a></li>
      </ol>
      <strong style="margin-top:0.5rem;display:block">Reference Pages</strong>
      <ul style="margin-bottom:0">
        <li><a href="#/coordinates">Coordinate Map</a> &mdash; all hex, vertex, and edge coordinates</li>
        <li><a href="#/board-setup">Board Setup JSON</a> &mdash; how the initial board/state is structured</li>
        <li><a href="#/events-reference">Events Reference</a> &mdash; every event type with example JSON</li>
      </ul>
    </div>

    <h2 id="register">1. Register Your Bot</h2>
    <p>Create a bot by sending a <code>POST</code> to <code>/api/bots</code>.
       Each registration is scoped to a game mode &mdash; the same bot name can register
       separately for <code>1v1</code> and <code>4p</code> with different API keys.</p>
    <pre>curl -X POST /api/bots \\
  -H "Content-Type: application/json" \\
  -d '{"name": "my_bot", "mode": "1v1", "coordinateFormat": "coordinates", "description": "My first OpenHex bot"}'</pre>
    <p>The optional <code>description</code> field (max 280 characters) is displayed on your bot's profile page.</p>

    <h3>Coordinate Format</h3>
    <p>When registering, choose how coordinates are represented in all API responses and expected in actions:</p>
    <table>
      <tr><th>Format</th><th>Description</th><th>Example (vertex)</th></tr>
      <tr><td><code>"coordinates"</code></td><td>Full cube coordinates (default)</td><td><code>{"x": 1, "y": 0, "z": 0}</code></td></tr>
      <tr><td><code>"indices"</code></td><td>Integer indices (see <a href="#/coordinates">Coordinate Map</a>)</td><td><code>17</code></td></tr>
    </table>
    <p>With <code>"indices"</code>, all hex locations become hex indices (0&ndash;18), vertices become vertex indices (0&ndash;53),
       and edges become edge indices (0&ndash;71) in both <strong>state</strong>, <strong>events</strong>, and <strong>action responses</strong>.
       Your actions can use either format regardless of this setting.</p>

    <p>Response:</p>
    <pre>{
  "bot": {
    "id": "a1b2c3d4e5f60718",
    "name": "my_bot",
    "mode": "1v1",
    "elo_1v1": 1500.0,
    "elo_4p": 1500.0,
    "games_1v1": 0, "wins_1v1": 0,
    "games_4p": 0, "wins_4p": 0
  },
  "api_key": "your-secret-api-key"
}</pre>
    <div class="note">
      <strong>Save your API key!</strong> It is returned only once at registration.
      If you lose it, you'll need to register with a different bot name.
      Re-posting the same <code>name</code> + <code>mode</code> returns a <code>409 Conflict</code>.
    </div>
    <p>All authenticated endpoints require the key in the <code>Authorization</code> header:</p>
    <pre>Authorization: Bearer your-secret-api-key</pre>

    <h2 id="game-spec">2. Game Specification</h2>
    <h3>Game Modes</h3>
    <table>
      <tr><th></th><th>1v1</th><th>4-Player</th></tr>
      <tr><td>Victory Points</td><td><strong>15</strong></td><td><strong>10</strong></td></tr>
      <tr><td>Discard Limit</td><td>9</td><td>7</td></tr>
      <tr><td>Robber Mode</td><td>Friendly</td><td>Normal</td></tr>
      <tr><td>Dice Mode</td><td>Balanced</td><td>Random</td></tr>
      <tr><td>Players</td><td>2 (Red, Blue)</td><td>4 (Red, Blue, White, Orange)</td></tr>
    </table>
    <div class="note">
      <strong>Friendly robber</strong> means you cannot place the robber on a hex adjacent to a
      player who has &le; the starting number of victory points (2 settlements = 2 VP).
    </div>
    <h3>Board &amp; Coordinates</h3>
    <p>The board uses <strong>cube coordinates</strong> where <code>x + y + z = 0</code> for hex tiles.
       Vertices (settlement/city locations) satisfy <code>x + y + z = 1</code> or <code>2</code>.</p>
    <p><a href="#/coordinates"><strong>View the full coordinate reference &rarr;</strong></a>
       &mdash; interactive board diagram with all 19 hex and 54 vertex coordinates.</p>
    <h3>Resources</h3>
    <p>Five resource types: <code>wood</code>, <code>brick</code>, <code>sheep</code>,
       <code>wheat</code>, <code>ore</code>. The resource bank starts with 19 of each.</p>
    <h3>Action Timeout</h3>
    <p>You have <strong>60 seconds</strong> per action. If you miss the deadline the game is aborted.</p>

    <h2 id="events">3. Parsing &amp; Responding to Game Events</h2>
    <h3>Game State</h3>
    <p>Get the full (filtered) game state at any time:</p>
    <pre>GET /api/games/{game_id}/state
Authorization: Bearer your-api-key</pre>
    <p>Opponent hands and dev cards are hidden &mdash; you see <code>handSize</code> and
       <code>devCardCount</code> instead of the actual contents.</p>
    <div class="note">
      <strong>Board optimization:</strong> The <code>board.tiles</code> and <code>board.ports</code> arrays are only
      included during the <strong>setup phase</strong>. After setup, the board is static &mdash; cache it locally.
      Dynamic board state (robber, settlements, cities, roads) is always included.
    </div>
    <h3>Polling Events</h3>
    <pre>GET /api/games/{game_id}/events?after=5
Authorization: Bearer your-api-key</pre>

    <h3>Hidden Information</h3>
    <ul>
      <li><strong>Opponent dev card purchases:</strong> <code>card</code> field removed for other players.</li>
      <li><strong>Steals:</strong> <code>resource</code> field hidden unless you are the stealer or victim.</li>
      <li><strong>Dev card deck:</strong> only <code>{"remaining": N}</code>, not the composition.</li>
    </ul>

    <h3>Action Types</h3>
    <table>
      <tr><th>Action</th><th>Required Fields</th><th>Phase</th></tr>
      <tr><td><code>placeInitialSettlement</code></td><td><code>vertex: {x,y,z}</code> or <code>vertex: int</code></td><td>Setup</td></tr>
      <tr><td><code>placeInitialRoad</code></td><td><code>edge: [{x,y,z},{x,y,z}]</code> or <code>edge: int</code></td><td>Setup</td></tr>
      <tr><td><code>rollDice</code></td><td>&mdash;</td><td>Main</td></tr>
      <tr><td><code>buildRoad</code></td><td><code>edge: [{x,y,z},{x,y,z}]</code> or <code>edge: int</code></td><td>Main</td></tr>
      <tr><td><code>buildSettlement</code></td><td><code>vertex: {x,y,z}</code> or <code>vertex: int</code></td><td>Main</td></tr>
      <tr><td><code>buildCity</code></td><td><code>vertex: {x,y,z}</code> or <code>vertex: int</code></td><td>Main</td></tr>
      <tr><td><code>buyDevCard</code></td><td>&mdash;</td><td>Main</td></tr>
      <tr><td><code>playKnight</code></td><td><code>hex: {x,y,z}</code> or <code>hex: int</code></td><td>Main</td></tr>
      <tr><td><code>playRoadBuilding</code></td><td>&mdash;</td><td>Main</td></tr>
      <tr><td><code>playYearOfPlenty</code></td><td><code>resources: ["wood","brick"]</code></td><td>Main</td></tr>
      <tr><td><code>playMonopoly</code></td><td><code>resource: "wheat"</code></td><td>Main</td></tr>
      <tr><td><code>tradeWithBank</code></td><td><code>offering: {res:n}, requesting: {res:n}</code></td><td>Main</td></tr>
      <tr><td><code>moveRobber</code></td><td><code>hex: {x,y,z}</code> or <code>hex: int</code></td><td>After 7 / knight</td></tr>
      <tr><td><code>stealFromPlayer</code></td><td><code>target: "blue"</code></td><td>After robber move</td></tr>
      <tr><td><code>discardResources</code></td><td><code>resources: {res:n, ...}</code></td><td>After 7 (over limit)</td></tr>
      <tr><td><code>endTurn</code></td><td>&mdash;</td><td>Main</td></tr>
    </table>
    <div class="note">
      <strong>Backward compatibility:</strong> The old field names <code>vertices</code> (for edges)
      and <code>location</code> (for hexes) are still accepted in actions. New code should use
      <code>edge</code> and <code>hex</code>.
    </div>

    <h3>Submitting Actions</h3>
    <pre>curl -X POST /api/games/{game_id}/actions \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"action": {"type": "rollDice"}, "duration_ms": 42}'</pre>
    <p>The optional <code>duration_ms</code> field (alongside <code>action</code>, not inside it)
       reports how long your bot took to decide this move, in milliseconds. These are
       aggregated per-bot and shown on the game detail page.</p>

    <h3>Event Types</h3>
    <table>
      <tr><th>Event Type</th><th>Key Fields</th></tr>
      <tr><td><code>diceRolled</code></td><td>player, dice (array of 2)</td></tr>
      <tr><td><code>settlementBuilt</code></td><td>player, vertex</td></tr>
      <tr><td><code>cityBuilt</code></td><td>player, vertex</td></tr>
      <tr><td><code>roadBuilt</code></td><td>player, edge</td></tr>
      <tr><td><code>devCardBought</code></td><td>player, card (hidden for opponents)</td></tr>
      <tr><td><code>knightPlayed</code></td><td>player, hex</td></tr>
      <tr><td><code>robberMoved</code></td><td>player, hex</td></tr>
      <tr><td><code>resourceStolen</code></td><td>player, target, resource (maybe hidden)</td></tr>
      <tr><td><code>bankTradeExecuted</code></td><td>player, offering, requesting</td></tr>
      <tr><td><code>turnEnded</code></td><td>player</td></tr>
      <tr><td><code>gameWon</code></td><td>player, victoryPoints</td></tr>
    </table>

    <h2 id="queue">4. Joining the Queue</h2>
    <p>When your bot is ready to play, join the matchmaking queue for your registered mode:</p>
    <pre>curl -X POST /api/queue/join \\
  -H "Authorization: Bearer your-api-key" \\
  -H "Content-Type: application/json" \\
  -d '{"mode": "1v1"}'</pre>
    <p>The server matches players automatically. Check your queue status:</p>
    <pre>GET /api/queue/status
Authorization: Bearer your-api-key</pre>

    <h2 id="game-loop">5. The Full Game Loop</h2>
    <ol>
      <li><strong>Register</strong> once: <code>POST /api/bots</code></li>
      <li><strong>Join queue</strong>: <code>POST /api/queue/join</code></li>
      <li><strong>Poll for games</strong>: <code>GET /api/games?status=active</code></li>
      <li><strong>Game loop</strong> (repeat until game ends):
        <ol style="list-style-type: lower-alpha;">
          <li>Get state: <code>GET /api/games/{id}/state</code></li>
          <li>Check if it's your turn</li>
          <li>Choose and submit an action: <code>POST /api/games/{id}/actions</code></li>
          <li>Poll events: <code>GET /api/games/{id}/events?after={last_seq}</code></li>
        </ol>
      </li>
      <li><strong>Game ends</strong>: status changes to <code>completed</code></li>
      <li><strong>Re-queue</strong>: <code>POST /api/queue/join</code> to play again</li>
    </ol>

    <h3>Step 1: Register (run once)</h3>
    <pre>import requests

API = "https://open-hex.web.app/api"

resp = requests.post(f"{API}/bots", json={
    "name": "my_bot",
    "mode": "1v1",
    "coordinateFormat": "indices",   # or "coordinates" for cube coords
    "description": "MCTS bot with 256 simulations",  # optional, max 280 chars
})
data = resp.json()
print(f"API key: {data['api_key']}")   # save this!</pre>

    <h3>Step 2: Run the bot daemon</h3>
    <pre>import requests, time

API = "https://open-hex.web.app/api"
API_KEY = "your-api-key"              # from step 1
MODE = "1v1"
headers = {"Authorization": f"Bearer {API_KEY}"}

# Cache board tiles/ports from setup (stripped after setup phase)
cached_board = {}


def play_game(game_id):
    global cached_board
    last_seq = -1
    while True:
        state_resp = requests.get(f"{API}/games/{game_id}/state", headers=headers).json()
        game_info = state_resp["game"]
        my_color = state_resp["your_color"]
        state = state_resp["state"]

        # Cache board tiles/ports during setup (they're stripped after)
        board = state.get("board", {})
        if board.get("tiles"):
            cached_board = {"tiles": board["tiles"], "ports": board.get("ports", [])}
        elif cached_board:
            board["tiles"] = cached_board["tiles"]
            board["ports"] = cached_board["ports"]

        if game_info["status"] != "active":
            print(f"Game {game_id[:8]} finished: {game_info['status']}")
            return

        if game_info["current_player_color"] == my_color:
            t0 = time.monotonic()
            action = decide_action(state, my_color)  # your logic
            duration_ms = int((time.monotonic() - t0) * 1000)

            requests.post(f"{API}/games/{game_id}/actions",
                          json={"action": action, "duration_ms": duration_ms},
                          headers=headers)

        events_resp = requests.get(
            f"{API}/games/{game_id}/events?after={last_seq}", headers=headers
        ).json()
        for e in events_resp["events"]:
            last_seq = max(last_seq, e["seq"])

        time.sleep(1)


# Main loop: queue up, play games, repeat
requests.post(f"{API}/queue/join", json={"mode": MODE}, headers=headers)
try:
    while True:
        games = requests.get(f"{API}/games?status=active", headers=headers).json()
        if not games:
            time.sleep(5)
            continue

        play_game(games[0]["id"])
        requests.post(f"{API}/queue/join", json={"mode": MODE}, headers=headers)
finally:
    requests.post(f"{API}/queue/leave", json={"mode": MODE}, headers=headers)</pre>

    <p style="margin-top:1.5rem; color:var(--muted);">
      All API endpoints are documented above. For the full coordinate reference,
      see the <a href="#/coordinates">coordinate map</a>.
    </p>
  </div>
  `;
}

// ---------------------------------------------------------------------------
// Coordinates reference page
// ---------------------------------------------------------------------------

function renderCoordinates() {
  // All 19 hex cube coordinates (x+y+z=0)
  const hexes = [
    [0,0,0],
    [1,-1,0],[1,0,-1],[0,1,-1],[-1,1,0],[-1,0,1],[0,-1,1],
    [2,-2,0],[2,-1,-1],[2,0,-2],[1,1,-2],[0,2,-2],[-1,2,-1],
    [-2,2,0],[-2,1,1],[-2,0,2],[-1,-1,2],[0,-2,2],[1,-2,1],
  ];

  // All 54 vertex cube coordinates
  const vertices = [
    // Type A (x+y+z=1)
    [-2,0,3],[-2,1,2],[-2,2,1],[-2,3,0],
    [-1,-1,3],[-1,0,2],[-1,1,1],[-1,2,0],[-1,3,-1],
    [0,-2,3],[0,-1,2],[0,0,1],[0,1,0],[0,2,-1],[0,3,-2],
    [1,-2,2],[1,-1,1],[1,0,0],[1,1,-1],[1,2,-2],
    [2,-2,1],[2,-1,0],[2,0,-1],[2,1,-2],
    [3,-2,0],[3,-1,-1],[3,0,-2],
    // Type B (x+y+z=2)
    [-2,1,3],[-2,2,2],[-2,3,1],
    [-1,0,3],[-1,1,2],[-1,2,1],[-1,3,0],
    [0,-1,3],[0,0,2],[0,1,1],[0,2,0],[0,3,-1],
    [1,-2,3],[1,-1,2],[1,0,1],[1,1,0],[1,2,-1],[1,3,-2],
    [2,-2,2],[2,-1,1],[2,0,0],[2,1,-1],[2,2,-2],
    [3,-2,1],[3,-1,0],[3,0,-1],[3,1,-2],
  ];

  // Cube to pixel conversion (flat-top hexagons)
  const hexSize = 52;
  function hexToPixel(x, y, z) {
    const px = hexSize * (3/2 * x);
    const py = hexSize * (Math.sqrt(3)/2 * x + Math.sqrt(3) * y);
    return [px, -py]; // negate y for SVG (y-down)
  }

  // Vertex to pixel: each vertex is at a corner of the hexes it touches
  function vertexToPixel(x, y, z) {
    const sum = x + y + z;
    // Vertices sit at hex corners. For cube coords:
    // Type A (sum=1): the vertex is the "north" corner shared by adjacent hexes
    // Type B (sum=2): the vertex is the "south" corner
    // A vertex at (vx,vy,vz) with sum=1 is between hexes (vx,vy-1,vz), (vx-1,vy,vz), (vx,vy,vz-1)
    // A vertex at (vx,vy,vz) with sum=2 is between hexes (vx-1,vy,vz-1), (vx,vy-1,vz-1), (vx-1,vy-1,vz)
    if (sum === 1) {
      // Average of the 3 neighboring hex centers, offset toward the shared corner
      const h1 = [x, y-1, z]; // x+y-1+z = 0 ✓
      const h2 = [x-1, y, z]; // x-1+y+z = 0 ✓
      const h3 = [x, y, z-1]; // x+y+z-1 = 0 ✓
      const p1 = hexToPixel(...h1);
      const p2 = hexToPixel(...h2);
      const p3 = hexToPixel(...h3);
      return [(p1[0]+p2[0]+p3[0])/3, (p1[1]+p2[1]+p3[1])/3];
    } else {
      const h1 = [x-1, y, z-1];
      const h2 = [x, y-1, z-1];
      const h3 = [x-1, y-1, z];
      const p1 = hexToPixel(...h1);
      const p2 = hexToPixel(...h2);
      const p3 = hexToPixel(...h3);
      return [(p1[0]+p2[0]+p3[0])/3, (p1[1]+p2[1]+p3[1])/3];
    }
  }

  // Build hex polygons
  function hexCorners(cx, cy) {
    const corners = [];
    for (let i = 0; i < 6; i++) {
      const angle = Math.PI / 180 * (60 * i);
      corners.push([
        cx + hexSize * Math.cos(angle),
        cy + hexSize * Math.sin(angle),
      ]);
    }
    return corners;
  }

  // Compute bounds
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const h of hexes) {
    const [px, py] = hexToPixel(...h);
    const corners = hexCorners(px, py);
    for (const [cx, cy] of corners) {
      minX = Math.min(minX, cx); maxX = Math.max(maxX, cx);
      minY = Math.min(minY, cy); maxY = Math.max(maxY, cy);
    }
  }
  const pad = 30;
  minX -= pad; minY -= pad; maxX += pad; maxY += pad;
  const svgW = maxX - minX;
  const svgH = maxY - minY;

  // Build hex SVG with both label sets
  let svg = `<svg id="hex-svg" viewBox="${minX} ${minY} ${svgW} ${svgH}" style="width:100%;max-width:700px;display:block;margin:0 auto;">`;
  for (const [x,y,z] of hexes) {
    const [px, py] = hexToPixel(x, y, z);
    const corners = hexCorners(px, py);
    const points = corners.map(c => c.join(",")).join(" ");
    const idx = hexes.findIndex(h => h[0]===x && h[1]===y && h[2]===z);
    svg += `<polygon points="${points}" fill="rgba(56,189,248,0.08)" stroke="#334155" stroke-width="1.5"/>`;
    svg += `<text class="label-coord" x="${px}" y="${py}" text-anchor="middle" dominant-baseline="central" fill="#38bdf8" font-size="11" font-family="SF Mono,monospace">${x},${y},${z}</text>`;
    svg += `<text class="label-index" x="${px}" y="${py}" text-anchor="middle" dominant-baseline="central" fill="#38bdf8" font-size="14" font-weight="bold" font-family="SF Mono,monospace" display="none">${idx}</text>`;
  }
  for (const [x,y,z] of vertices) {
    const [px, py] = vertexToPixel(x, y, z);
    const sum = x+y+z;
    const color = sum === 1 ? "#4ade80" : "#facc15";
    svg += `<circle cx="${px}" cy="${py}" r="3.5" fill="${color}" stroke="#0f172a" stroke-width="1"/>`;
  }
  svg += "</svg>";

  // Compute edge list for SVG rendering
  const vertexSet = new Set(vertices.map(v => v.join(",")));
  const edges = [];
  for (const [x,y,z] of vertices) {
    if (x+y+z !== 1) continue;
    for (const [dx,dy,dz] of [[1,0,0],[0,1,0],[0,0,1]]) {
      const nb = [x+dx,y+dy,z+dz];
      if (vertexSet.has(nb.join(","))) {
        edges.push([[x,y,z], nb]);
      }
    }
  }

  // Build vertex+edge SVG with both label sets
  let svg2 = `<svg id="vertex-svg" viewBox="${minX - 20} ${minY - 20} ${svgW + 40} ${svgH + 40}" style="width:100%;max-width:700px;display:block;margin:0 auto;">`;
  for (const [x,y,z] of hexes) {
    const [px, py] = hexToPixel(x, y, z);
    const corners = hexCorners(px, py);
    const points = corners.map(c => c.join(",")).join(" ");
    svg2 += `<polygon points="${points}" fill="rgba(56,189,248,0.04)" stroke="#1e293b" stroke-width="1"/>`;
  }
  // Draw edge lines and labels (index mode only)
  for (let ei = 0; ei < edges.length; ei++) {
    const [v1, v2] = edges[ei];
    const [px1, py1] = vertexToPixel(...v1);
    const [px2, py2] = vertexToPixel(...v2);
    svg2 += `<line class="label-index" x1="${px1}" y1="${py1}" x2="${px2}" y2="${py2}" stroke="#94a3b8" stroke-width="1.5" opacity="0.3" display="none"/>`;
    const mx = (px1+px2)/2, my = (py1+py2)/2;
    svg2 += `<text class="label-index" x="${mx}" y="${my+3}" text-anchor="middle" fill="#94a3b8" font-size="6.5" font-weight="bold" font-family="SF Mono,monospace" display="none">${ei}</text>`;
  }
  // Draw vertices with labels
  for (let vi = 0; vi < vertices.length; vi++) {
    const [x,y,z] = vertices[vi];
    const [px, py] = vertexToPixel(x, y, z);
    const sum = x+y+z;
    const color = sum === 1 ? "#4ade80" : "#facc15";
    svg2 += `<circle cx="${px}" cy="${py}" r="3" fill="${color}" stroke="#0f172a" stroke-width="0.5"/>`;
    svg2 += `<text class="label-coord" x="${px}" y="${py - 7}" text-anchor="middle" fill="${color}" font-size="7" font-family="SF Mono,monospace" opacity="0.9">${x},${y},${z}</text>`;
    svg2 += `<text class="label-index" x="${px}" y="${py - 7}" text-anchor="middle" fill="${color}" font-size="8" font-weight="bold" font-family="SF Mono,monospace" opacity="0.9" display="none">${vi}</text>`;
  }
  svg2 += "</svg>";

  // Build tables
  let hexTable = '<table><tr><th>#</th><th>x</th><th>y</th><th>z</th><th>Ring</th></tr>';
  hexes.forEach(([x,y,z], i) => {
    const ring = Math.max(Math.abs(x), Math.abs(y), Math.abs(z));
    hexTable += `<tr><td>${i}</td><td>${x}</td><td>${y}</td><td>${z}</td><td>${ring}</td></tr>`;
  });
  hexTable += '</table>';

  let vertexTable = '<table><tr><th>#</th><th>x</th><th>y</th><th>z</th><th>Sum</th><th>Type</th></tr>';
  vertices.forEach(([x,y,z], i) => {
    const sum = x+y+z;
    const type = sum === 1 ? 'A' : 'B';
    const color = sum === 1 ? 'var(--green)' : 'var(--yellow)';
    vertexTable += `<tr><td>${i}</td><td>${x}</td><td>${y}</td><td>${z}</td><td style="color:${color}">${sum}</td><td style="color:${color}">${type}</td></tr>`;
  });
  vertexTable += '</table>';

  $app.innerHTML = `
  <div class="guide">
    <h1 style="font-size:1.5rem; margin-bottom:0.5rem;">Coordinate Reference</h1>
    <p style="color:var(--muted); margin-bottom:1.5rem;">
      All hex and vertex coordinates on the standard 19-hex board.
      <a href="#/guide">&larr; Back to guide</a>
    </p>

    <h2 style="border-top:none; margin-top:0; padding-top:0;">Hex Tiles (19)</h2>
    <p>Hex tiles use <strong>cube coordinates</strong> where <code>x + y + z = 0</code>.</p>
    <div class="tabs" style="margin-bottom:0.5rem">
      <a href="#" class="active" onclick="toggleLabels('hex-svg',this,'coord');return false">Coordinates</a>
      <a href="#" onclick="toggleLabels('hex-svg',this,'index');return false">Indices</a>
    </div>
    ${svg}

    <div class="card" style="margin-top:1rem; max-height:400px; overflow-y:auto;">
      <h2>All Hex Coordinates</h2>
      ${hexTable}
    </div>

    <h2>Vertices (54) &amp; Edges (72)</h2>
    <p>Vertices use cube coordinates where the sum is either
       <strong style="color:var(--green)">1 (Type A)</strong> or
       <strong style="color:var(--yellow)">2 (Type B)</strong>.
       Settlements and cities are placed on vertices. In index mode, edge indices are shown on the lines between vertices.</p>
    <div class="tabs" style="margin-bottom:0.5rem">
      <a href="#" class="active" onclick="toggleLabels('vertex-svg',this,'coord');return false">Coordinates</a>
      <a href="#" onclick="toggleLabels('vertex-svg',this,'index');return false">Indices</a>
    </div>
    ${svg2}

    <div class="card" style="margin-top:1rem; max-height:500px; overflow-y:auto;">
      <h2>All Vertex Coordinates</h2>
      ${vertexTable}
    </div>

    <h2>Using Indices Instead of Coordinates</h2>
    <p>Every action field that takes coordinates also accepts an <strong>integer index</strong>
       from the tables on this page. This is often simpler than constructing <code>{x,y,z}</code> objects.</p>
    <table>
      <tr><th>Field</th><th>Coordinates</th><th>Index</th></tr>
      <tr>
        <td><code>vertex</code></td>
        <td><code>{"vertex": {"x":0,"y":0,"z":1}}</code></td>
        <td><code>{"vertex": 11}</code></td>
      </tr>
      <tr>
        <td><code>edge</code></td>
        <td><code>{"edge": [{"x":0,"y":0,"z":1}, {"x":0,"y":1,"z":1}]}</code></td>
        <td><code>{"edge": 15}</code></td>
      </tr>
      <tr>
        <td><code>hex</code></td>
        <td><code>{"hex": {"x":0,"y":0,"z":0}}</code></td>
        <td><code>{"hex": 0}</code></td>
      </tr>
    </table>

    <h3>Examples</h3>
    <pre>// Place settlement at vertex #11 (0,0,1)
{"type": "placeInitialSettlement", "vertex": 11}

// Build road on edge #15
{"type": "buildRoad", "edge": 15}

// Move robber to hex #3 (0,1,-1)
{"type": "moveRobber", "hex": 3}

// You can still use full coordinates — both formats work
{"type": "buildSettlement", "vertex": {"x": 1, "y": 0, "z": 0}}</pre>

    <h2>Edges / Roads (72)</h2>
    <p>Edges are defined by <strong>two adjacent vertex coordinates</strong>.
       When submitting a <code>buildRoad</code> action, provide both endpoints or an edge index:</p>
    <pre>// Using edge index:
{"type": "buildRoad", "edge": 15}

// Using coordinates:
{"type": "buildRoad", "edge": [{"x":0,"y":0,"z":1}, {"x":0,"y":1,"z":1}]}</pre>
    <p>Two vertices are adjacent when one has sum=1 and the other has sum=2, and they
       differ by exactly 1 in one coordinate. Every edge connects a Type A vertex to a Type B vertex.</p>

    <h3>Adjacency Rule</h3>
    <p>A vertex <code>(a,b,c)</code> with sum=1 is adjacent to these Type B vertices:</p>
    <ul>
      <li><code>(a, b+1, c)</code> &mdash; if it exists on the board (sum=2)</li>
      <li><code>(a, b, c+1)</code> &mdash; if it exists on the board (sum=2)</li>
      <li><code>(a+1, b, c)</code> &mdash; if it exists on the board (sum=2)</li>
    </ul>

    ${(() => {
      // Compute all edges: A vertex (sum=1) connects to B vertex (sum=2) if they differ by +1 in exactly one coordinate
      const vertexSet = new Set(vertices.map(v => v.join(",")));
      const edgeList = [];
      for (const [x,y,z] of vertices) {
        if (x+y+z !== 1) continue;
        const neighbors = [[x+1,y,z],[x,y+1,z],[x,y,z+1]];
        for (const [nx,ny,nz] of neighbors) {
          if (vertexSet.has([nx,ny,nz].join(","))) {
            edgeList.push([[x,y,z],[nx,ny,nz]]);
          }
        }
      }
      let edgeTable = '<div class="card" style="max-height:500px; overflow-y:auto;">';
      edgeTable += '<h2>All ' + edgeList.length + ' Edges</h2>';
      edgeTable += '<table><tr><th>#</th><th>Vertex A (sum=1)</th><th>Vertex B (sum=2)</th></tr>';
      edgeList.forEach(([a,b], i) => {
        edgeTable += '<tr><td>' + i + '</td>';
        edgeTable += '<td style="color:var(--green)">' + a.join(',') + '</td>';
        edgeTable += '<td style="color:var(--yellow)">' + b.join(',') + '</td></tr>';
      });
      edgeTable += '</table></div>';
      return edgeTable;
    })()}

    <h2>Coordinate System Rules</h2>
    <table>
      <tr><td><strong>Hex tiles</strong></td><td><code>x + y + z = 0</code></td><td>19 positions</td></tr>
      <tr><td><strong>Type A vertices</strong></td><td><code>x + y + z = 1</code></td><td>27 positions</td></tr>
      <tr><td><strong>Type B vertices</strong></td><td><code>x + y + z = 2</code></td><td>27 positions</td></tr>
      <tr><td><strong>Edges</strong></td><td>Adjacent A + B vertex pair</td><td>72 edges</td></tr>
    </table>
    <p style="margin-top:1rem">Each vertex touches up to 3 hex tiles. Each hex tile has 6 vertices (corners).
       Each edge connects exactly one Type A and one Type B vertex.</p>
  </div>
  `;
}

// ---------------------------------------------------------------------------
// Board Setup reference page
// ---------------------------------------------------------------------------

function renderBoardSetup() {
  $app.innerHTML = `
  <div class="guide">
    <h1 style="font-size:1.5rem; margin-bottom:0.5rem;">Board Setup JSON</h1>
    <p style="color:var(--muted); margin-bottom:1.5rem;">
      How the game state is structured when you call <code>GET /api/games/{id}/state</code>.
      <strong>Note:</strong> <code>board.tiles</code> and <code>board.ports</code> are only sent during
      the setup phase &mdash; cache them locally. Dynamic state (robber, buildings, roads) is always included.
      <a href="#/guide">&larr; Back to guide</a>
    </p>

    <h2 style="border-top:none; margin-top:0; padding-top:0;">Game Info Response</h2>
    <p>When you call <code>GET /api/games/{id}/state</code>, you receive:</p>
    <pre>{
  "game": {
    "id": "a1b2c3d4e5f60718",
    "mode": "1v1",
    "status": "active",
    "players": [
      {"botId": "abc123", "name": "MyBot", "color": "red", "seat": 0},
      {"botId": "def456", "name": "Opponent", "color": "blue", "seat": 1}
    ],
    "current_player_color": "red",
    "turn_number": 0,
    "action_deadline": "2026-04-11T06:15:00+00:00",
    "winner": null
  },
  "state": { ... },     // full game state (see below)
  "your_color": "red"   // your assigned color
}</pre>

    <h2>State Object</h2>
    <p>The <code>state</code> object contains the full game state. Here is a complete example
       at the start of a game (setup phase):</p>
    <pre>{
  "config": {
    "victoryPoints": 15,
    "discardLimit": 9,
    "numPlayers": 2,
    "robberMode": "friendly",
    "diceMode": "balanced",
    "maxSettlements": 5,
    "maxCities": 4,
    "maxRoads": 15
  },
  "board": {
    "tiles": [
      {"hex": {"x": 0, "y": 0, "z": 0}, "terrain": "forest", "number": 6},
      {"hex": {"x": 1, "y": -1, "z": 0}, "terrain": "hills", "number": 5},
      {"hex": {"x": 1, "y": 0, "z": -1}, "terrain": "pasture", "number": 9},
      {"hex": {"x": 0, "y": 1, "z": -1}, "terrain": "fields", "number": 4},
      {"hex": {"x": -1, "y": 1, "z": 0}, "terrain": "mountains", "number": 3},
      {"hex": {"x": -1, "y": 0, "z": 1}, "terrain": "pasture", "number": 11},
      {"hex": {"x": 0, "y": -1, "z": 1}, "terrain": "fields", "number": 8},
      {"hex": {"x": 2, "y": -2, "z": 0}, "terrain": "mountains", "number": 10},
      {"hex": {"x": 2, "y": -1, "z": -1}, "terrain": "forest", "number": 2},
      {"hex": {"x": 2, "y": 0, "z": -2}, "terrain": "pasture", "number": 12},
      {"hex": {"x": 1, "y": 1, "z": -2}, "terrain": "forest", "number": 8},
      {"hex": {"x": 0, "y": 2, "z": -2}, "terrain": "hills", "number": 5},
      {"hex": {"x": -1, "y": 2, "z": -1}, "terrain": "fields", "number": 10},
      {"hex": {"x": -2, "y": 2, "z": 0}, "terrain": "hills", "number": 3},
      {"hex": {"x": -2, "y": 1, "z": 1}, "terrain": "forest", "number": 11},
      {"hex": {"x": -2, "y": 0, "z": 2}, "terrain": "fields", "number": 4},
      {"hex": {"x": -1, "y": -1, "z": 2}, "terrain": "pasture", "number": 9},
      {"hex": {"x": 0, "y": -2, "z": 2}, "terrain": "mountains", "number": 6},
      {"hex": {"x": 1, "y": -2, "z": 1}, "terrain": "desert"}
    ],
    "ports": [
      {
        "edge": [{"x": 3, "y": -2, "z": 0}, {"x": 3, "y": -2, "z": 1}],
        "rate": 3
      },
      {
        "edge": [{"x": 3, "y": 0, "z": -2}, {"x": 3, "y": 1, "z": -2}],
        "rate": 2,
        "resource": "ore"
      }
    ],
    "robberHex": {"x": 1, "y": -2, "z": 1}
  },
  "turnOrder": ["red", "blue"],
  "currentPlayer": "red",
  "turnNumber": 0,
  "isSetupPhase": true,
  "players": [
    {
      "color": "red",
      "hand": {},
      "devCards": {},
      "devCardsBoughtThisTurn": {},
      "playedDevCards": {},
      "knightsPlayed": 0,
      "longestRoadLength": 0
    },
    {
      "color": "blue",
      "hand": {},
      "devCards": {},
      "devCardsBoughtThisTurn": {},
      "playedDevCards": {},
      "knightsPlayed": 0,
      "longestRoadLength": 0
    }
  ],
  "devCardDeck": {"remaining": 25},
  "resourceBank": {"wood": 19, "brick": 19, "sheep": 19, "wheat": 19, "ore": 19},
  "pending": null,
  "longestRoadPlayer": null,
  "longestRoadLength": 0,
  "largestArmyPlayer": null,
  "largestArmySize": 0
}</pre>

    <h2>Key Fields</h2>
    <table>
      <tr><th>Field</th><th>Type</th><th>Description</th></tr>
      <tr><td><code>config</code></td><td>object</td><td>Game rules (VP target, discard limit, robber mode, etc.)</td></tr>
      <tr><td><code>board.tiles</code></td><td>array</td><td>19 hex tiles with <code>hex</code>, terrain, and number token</td></tr>
      <tr><td><code>board.ports</code></td><td>array</td><td>9 ports with <code>edge</code> (vertex pair) and trade rate (2 or 3)</td></tr>
      <tr><td><code>board.robberHex</code></td><td>object</td><td>Current robber hex location</td></tr>
      <tr><td><code>board.settlements</code></td><td>array</td><td>Placed settlements (vertex + player color)</td></tr>
      <tr><td><code>board.cities</code></td><td>array</td><td>Placed cities (vertex + player color)</td></tr>
      <tr><td><code>board.roads</code></td><td>array</td><td>Placed roads (<code>edge</code> + player color)</td></tr>
      <tr><td><code>turnOrder</code></td><td>array</td><td>Player colors in turn order</td></tr>
      <tr><td><code>currentPlayer</code></td><td>string</td><td>Color of the player whose turn it is</td></tr>
      <tr><td><code>isSetupPhase</code></td><td>boolean</td><td>True during initial placement</td></tr>
      <tr><td><code>players</code></td><td>array</td><td>Player states (hand, dev cards, knights, etc.)</td></tr>
      <tr><td><code>pending</code></td><td>object|null</td><td>Pending action required (discard, robber move, steal, etc.)</td></tr>
    </table>

    <h2>Terrain Types</h2>
    <table>
      <tr><th>Terrain</th><th>Produces</th><th>Count</th></tr>
      <tr><td><code>forest</code></td><td>wood</td><td>4</td></tr>
      <tr><td><code>hills</code></td><td>brick</td><td>3</td></tr>
      <tr><td><code>pasture</code></td><td>sheep</td><td>4</td></tr>
      <tr><td><code>fields</code></td><td>wheat</td><td>4</td></tr>
      <tr><td><code>mountains</code></td><td>ore</td><td>3</td></tr>
      <tr><td><code>desert</code></td><td>nothing</td><td>1</td></tr>
    </table>

    <h2>Port Types</h2>
    <table>
      <tr><th>Type</th><th>Rate</th><th>Resource Field</th><th>Count</th></tr>
      <tr><td>Generic</td><td>3:1</td><td>absent</td><td>4</td></tr>
      <tr><td>Resource-specific</td><td>2:1</td><td><code>"wood"</code>, <code>"brick"</code>, etc.</td><td>5 (one per resource)</td></tr>
    </table>
    <p>A port's <code>edge</code> contains the two coastal vertices. Build a settlement on either vertex to gain the port's trade rate.</p>

    <h2>Pending Actions</h2>
    <p>When <code>state.pending</code> is non-null, a special action is required before the game can continue:</p>
    <table>
      <tr><th>Pending Type</th><th>Meaning</th><th>Required Action</th></tr>
      <tr><td><code>discard</code></td><td>Players over the limit must discard</td><td><code>discardResources</code></td></tr>
      <tr><td><code>moveRobber</code></td><td>Must place the robber</td><td><code>moveRobber</code></td></tr>
      <tr><td><code>steal</code></td><td>Must choose a player to steal from</td><td><code>stealFromPlayer</code></td></tr>
      <tr><td><code>roadBuilding</code></td><td>Must place free roads</td><td><code>buildRoad</code></td></tr>
    </table>

    <h2>Hidden Information</h2>
    <p>When you receive the state, opponent information is filtered:</p>
    <ul>
      <li><strong>Opponent hands:</strong> <code>hand: {}</code> (empty), but <code>handSize: N</code> is provided</li>
      <li><strong>Opponent dev cards:</strong> <code>devCards: {}</code> (empty), but <code>devCardCount: N</code> is provided</li>
      <li><strong>Dev card deck:</strong> Only <code>{"remaining": N}</code>, not the full composition</li>
    </ul>
  </div>
  `;
}

// ---------------------------------------------------------------------------
// Events reference page
// ---------------------------------------------------------------------------

function renderEventsReference() {
  $app.innerHTML = `
  <div class="guide">
    <h1 style="font-size:1.5rem; margin-bottom:0.5rem;">Events Reference</h1>
    <p style="color:var(--muted); margin-bottom:1.5rem;">
      Every event type with example JSON. Events are returned by
      <code>GET /api/games/{id}/events?after={seq}</code> and in action responses.
      <a href="#/guide">&larr; Back to guide</a>
    </p>

    <h2 style="border-top:none; margin-top:0; padding-top:0;">Event Format</h2>
    <p>Each event in the response has a <code>seq</code> (sequence number) and an <code>event</code> object:</p>
    <pre>{
  "events": [
    {"seq": 0, "event": {"type": "settlementBuilt", "player": "red", "vertex": {"x": 0, "y": 0, "z": 1}}},
    {"seq": 1, "event": {"type": "roadBuilt", "player": "red", "edge": [{"x": 0, "y": 0, "z": 1}, {"x": 0, "y": 1, "z": 1}]}}
  ]
}</pre>
    <p>Track the highest <code>seq</code> you've seen and pass it as <code>?after=N</code> to get only new events.</p>

    <h2>Setup Phase Events</h2>

    <h3>settlementBuilt</h3>
    <p>A settlement was placed (setup or main phase).</p>
    <pre>{
  "type": "settlementBuilt",
  "player": "red",
  "vertex": {"x": 1, "y": 0, "z": 0}
}</pre>

    <h3>roadBuilt</h3>
    <p>A road was placed. Edges are defined by their two endpoint vertices.</p>
    <pre>{
  "type": "roadBuilt",
  "player": "red",
  "edge": [{"x": 1, "y": 0, "z": 0}, {"x": 1, "y": 1, "z": 0}]
}</pre>

    <h3>setupResourcesCollected</h3>
    <p>Resources granted from second settlement placement (setup phase only).</p>
    <pre>{
  "type": "setupResourcesCollected",
  "player": "red",
  "resources": {"wood": 1, "wheat": 1, "ore": 1}
}</pre>

    <h2>Dice &amp; Production Events</h2>

    <h3>diceRolled</h3>
    <p>A player rolled the dice. Includes individual dice and the total.</p>
    <pre>{
  "type": "diceRolled",
  "player": "red",
  "die1": 3,
  "die2": 5,
  "diceTotal": 8
}</pre>

    <h3>resourcesProduced</h3>
    <p>Resources distributed to players after a dice roll. The <code>production</code> map
       shows what each player received.</p>
    <pre>{
  "type": "resourcesProduced",
  "diceTotal": 8,
  "production": {
    "red": {"wood": 1, "sheep": 1},
    "blue": {"ore": 2}
  }
}</pre>

    <h2>Building Events</h2>

    <h3>cityBuilt</h3>
    <p>A settlement was upgraded to a city.</p>
    <pre>{
  "type": "cityBuilt",
  "player": "red",
  "vertex": {"x": 1, "y": 0, "z": 0}
}</pre>

    <h2>Development Card Events</h2>

    <h3>devCardBought</h3>
    <p>A development card was purchased. The <code>card</code> field is hidden for opponents.</p>
    <pre>// Your own purchase:
{
  "type": "devCardBought",
  "player": "red",
  "card": "knight"
}

// Opponent's purchase (card hidden):
{
  "type": "devCardBought",
  "player": "blue"
}</pre>
    <p>Card values: <code>knight</code>, <code>victoryPoint</code>, <code>monopoly</code>,
       <code>roadBuilding</code>, <code>yearOfPlenty</code></p>

    <h3>knightPlayed</h3>
    <p>A knight card was played (triggers robber move).</p>
    <pre>{
  "type": "knightPlayed",
  "player": "red"
}</pre>

    <h3>monopolyPlayed</h3>
    <p>A monopoly card was played.</p>
    <pre>{
  "type": "monopolyPlayed",
  "player": "red",
  "resource": "wheat",
  "collected": {"blue": 3}
}</pre>

    <h3>yearOfPlentyPlayed</h3>
    <p>A Year of Plenty card was played.</p>
    <pre>{
  "type": "yearOfPlentyPlayed",
  "player": "red",
  "resources": ["wood", "brick"]
}</pre>

    <h3>roadBuildingPlayed</h3>
    <p>A Road Building card was played (player then places 2 free roads via <code>roadBuilt</code> events).</p>
    <pre>{
  "type": "roadBuildingPlayed",
  "player": "red"
}</pre>

    <h2>Robber Events</h2>

    <h3>robberMoved</h3>
    <p>The robber was placed on a new hex.</p>
    <pre>{
  "type": "robberMoved",
  "player": "red",
  "hex": {"x": 0, "y": 1, "z": -1}
}</pre>

    <h3>resourceStolen</h3>
    <p>A resource was stolen after robber placement. The <code>resource</code> is hidden
       unless you are the stealer or victim.</p>
    <pre>// If you are involved:
{
  "type": "resourceStolen",
  "player": "red",
  "target": "blue",
  "resource": "wheat"
}

// If you are a spectator:
{
  "type": "resourceStolen",
  "player": "red",
  "target": "blue"
}</pre>

    <h3>resourcesDiscarded</h3>
    <p>A player discarded resources after a 7 was rolled.</p>
    <pre>{
  "type": "resourcesDiscarded",
  "player": "blue",
  "resources": {"wood": 2, "sheep": 1, "ore": 1}
}</pre>

    <h2>Trading Events</h2>

    <h3>bankTradeExecuted</h3>
    <p>A player traded with the bank (4:1, 3:1, or 2:1 with port).</p>
    <pre>{
  "type": "bankTradeExecuted",
  "player": "red",
  "offering": {"sheep": 4},
  "requesting": {"ore": 1}
}</pre>

    <h2>Special Award Events</h2>

    <h3>longestRoadClaimed</h3>
    <p>A player claimed or took over Longest Road (2 VP).</p>
    <pre>{
  "type": "longestRoadClaimed",
  "player": "red",
  "length": 5
}</pre>

    <h3>largestArmyClaimed</h3>
    <p>A player claimed or took over Largest Army (2 VP).</p>
    <pre>{
  "type": "largestArmyClaimed",
  "player": "red",
  "knights": 3
}</pre>

    <h2>Turn &amp; Game End Events</h2>

    <h3>turnEnded</h3>
    <p>A player ended their turn.</p>
    <pre>{
  "type": "turnEnded",
  "player": "red",
  "turnNumber": 12
}</pre>

    <h3>gameWon</h3>
    <p>A player has won the game.</p>
    <pre>{
  "type": "gameWon",
  "player": "red",
  "victoryPoints": 15
}</pre>

    <h2>Event Type Summary</h2>
    <table>
      <tr><th>Event Type</th><th>Key Fields</th><th>Phase</th></tr>
      <tr><td><code>settlementBuilt</code></td><td>player, vertex</td><td>Setup / Main</td></tr>
      <tr><td><code>roadBuilt</code></td><td>player, edge</td><td>Setup / Main</td></tr>
      <tr><td><code>setupResourcesCollected</code></td><td>player, resources</td><td>Setup</td></tr>
      <tr><td><code>diceRolled</code></td><td>player, die1, die2, diceTotal</td><td>Main</td></tr>
      <tr><td><code>resourcesProduced</code></td><td>diceTotal, production</td><td>Main</td></tr>
      <tr><td><code>cityBuilt</code></td><td>player, vertex</td><td>Main</td></tr>
      <tr><td><code>devCardBought</code></td><td>player, card (hidden for opponents)</td><td>Main</td></tr>
      <tr><td><code>knightPlayed</code></td><td>player</td><td>Main</td></tr>
      <tr><td><code>monopolyPlayed</code></td><td>player, resource, collected</td><td>Main</td></tr>
      <tr><td><code>yearOfPlentyPlayed</code></td><td>player, resources (list)</td><td>Main</td></tr>
      <tr><td><code>roadBuildingPlayed</code></td><td>player</td><td>Main</td></tr>
      <tr><td><code>robberMoved</code></td><td>player, hex</td><td>After 7 / knight</td></tr>
      <tr><td><code>resourceStolen</code></td><td>player, target, resource (maybe hidden)</td><td>After robber</td></tr>
      <tr><td><code>resourcesDiscarded</code></td><td>player, resources</td><td>After 7</td></tr>
      <tr><td><code>bankTradeExecuted</code></td><td>player, offering, requesting</td><td>Main</td></tr>
      <tr><td><code>longestRoadClaimed</code></td><td>player, length</td><td>Main</td></tr>
      <tr><td><code>largestArmyClaimed</code></td><td>player, knights</td><td>Main</td></tr>
      <tr><td><code>turnEnded</code></td><td>player, turnNumber</td><td>Main</td></tr>
      <tr><td><code>gameWon</code></td><td>player, victoryPoints</td><td>Main</td></tr>
    </table>
  </div>
  `;
}
