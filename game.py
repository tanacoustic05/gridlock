#!/usr/bin/env python3
"""
GRIDLOCK - a tiny, polished, turn-based multiplayer strategy game.

One single-file server process. It serves:
  * a browser UI at GET /            (index.html next to this file)
  * a JSON HTTP API under /api/*     (usable by humans and AI agents alike)

Run:  python game.py            ->  http://localhost:8430
Env:  GRIDLOCK_HOST (default 0.0.0.0), GRIDLOCK_PORT (default 8430)

Python 3.11+ standard library only. No third-party dependencies.
"""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
HOST = os.environ.get("GRIDLOCK_HOST", "0.0.0.0")
PORT = int(os.environ.get("GRIDLOCK_PORT", "8430"))
SIZE = 6                                   # 6x6 grid, coordinates 0..5
NODES = [(1, 1), (4, 1), (1, 4), (4, 4)]   # symmetric power nodes: +1 energy/turn
CORNERS = [(0, 0), (5, 5), (5, 0), (0, 5)]  # seat -> home corner
WIN_CELLS = 14                             # owning 14+ cells wins immediately
MAX_ROUNDS = 30                            # otherwise most cells after round 30
START_ENERGY = 3
COST = {"expand": 1, "strike": 2, "fortify": 1, "harvest": 0}
SEAT_COLORS = ["#e5484d", "#3b82f6", "#f5a524", "#22c55e"]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no confusables

_lock = threading.RLock()
GAMES = {}          # gameId -> game dict
HERE = os.path.dirname(os.path.abspath(__file__))


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def ckey(x, y):
    return "%d,%d" % (x, y)


def parse_key(k):
    x, y = k.split(",")
    return int(x), int(y)


def neighbors(x, y):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < SIZE and 0 <= ny < SIZE:
            yield nx, ny


def owned_cells(g, seat):
    out = []
    for k, c in g["cells"].items():
        if c["owner"] == seat:
            out.append(parse_key(k))
    return out


def count_cells(g, seat):
    return sum(1 for c in g["cells"].values() if c["owner"] == seat)


def count_nodes(g, seat):
    return sum(1 for n in NODES if g["cells"][ckey(*n)]["owner"] == seat)


def seat_of_token(g, token):
    if not token:
        return None
    for s in g["seats"]:
        if s["token"] == token:
            return s["index"]
    return None


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if code not in {g["joinCode"] for g in GAMES.values()}:
            return code


def new_game(num_players, host_name):
    num_players = max(2, min(4, int(num_players)))
    now = time.time()
    g = {
        "gameId": secrets.token_hex(6),
        "joinCode": new_code(),
        "numPlayers": num_players,
        "seats": [],
        "cells": {ckey(x, y): {"owner": None, "defense": 0}
                  for x in range(SIZE) for y in range(SIZE)},
        "energy": [START_ENERGY] * num_players,
        "turn": 0,
        "round": 1,
        "log": [],
        "status": "lobby",          # lobby | active | finished
        "winner": None,             # seat index | "draw" | None
        "created": now,
        "last_move_ts": now,
    }
    for s in range(num_players):
        g["seats"].append({"index": s, "name": None, "kind": "human",
                           "token": secrets.token_urlsafe(16)})
        hx, hy = CORNERS[s]                     # every seat gets its home corner
        g["cells"][ckey(hx, hy)] = {"owner": s, "defense": 1}
    # host occupies seat 0
    g["seats"][0]["name"] = (host_name or "Host")[:24]
    with _lock:
        GAMES[g["gameId"]] = g
    return g


def fill_seats_and_start(g):
    """Start the match once every seat is taken."""
    if g["status"] == "lobby" and all(s["name"] for s in g["seats"]):
        g["status"] = "active"
        g["turn"] = 0
        g["round"] = 1


# --------------------------------------------------------------------------
# Rules: legal moves, applying moves, win check
# --------------------------------------------------------------------------
def legal_moves(g, seat):
    """Every action `seat` may take right now. Used by UI + agents."""
    if g["status"] != "active" or g["turn"] != seat:
        return []
    e = g["energy"][seat]
    mine = set(owned_cells(g, seat))
    moves = []
    empty, enemy, fortify = [], [], []
    for (x, y) in mine:
        for (nx, ny) in neighbors(x, y):
            c = g["cells"][ckey(nx, ny)]
            if c["owner"] is None:
                empty.append([nx, ny])
            elif c["owner"] != seat:
                enemy.append([nx, ny])
        if g["cells"][ckey(x, y)]["defense"] < 2:
            fortify.append([x, y])
    if e >= COST["expand"]:
        for t in empty:
            moves.append({"type": "expand", "target": t})
    if e >= COST["strike"]:
        for t in enemy:
            moves.append({"type": "strike", "target": t})
    if e >= COST["fortify"]:
        for t in fortify:
            moves.append({"type": "fortify", "target": t})
    moves.append({"type": "harvest", "target": None})
    return moves


def is_legal(g, seat, action):
    for m in legal_moves(g, seat):
        if m["type"] == action["type"] and (m["target"] or [None]) == (action.get("target") or [None]):
            return True
    return False


def apply_move(g, seat, action):
    """Validate + apply. Returns (ok, error). Mutates g on success."""
    if g["status"] != "active":
        return False, "game is not active"
    if g["turn"] != seat:
        return False, "not your turn"
    atype = action.get("type")
    if atype not in COST:
        return False, "unknown action type"
    target = action.get("target")
    if isinstance(target, dict):
        target = [target.get("x"), target.get("y")]
    if target is not None:
        try:
            target = [int(target[0]), int(target[1])]
        except (TypeError, ValueError, IndexError):
            return False, "bad target"
        if not (0 <= target[0] < SIZE and 0 <= target[1] < SIZE):
            return False, "target out of bounds"
    action = {"type": atype, "target": target}
    if not is_legal(g, seat, action):
        return False, "illegal move"

    if atype == "expand":
        g["energy"][seat] -= COST["expand"]
        g["cells"][ckey(*target)] = {"owner": seat, "defense": 0}
    elif atype == "strike":
        g["energy"][seat] -= COST["strike"]
        c = g["cells"][ckey(*target)]
        if c["defense"] > 0:
            c["defense"] -= 1
        else:
            c["owner"] = seat        # captured
    elif atype == "fortify":
        g["energy"][seat] -= COST["fortify"]
        g["cells"][ckey(*target)]["defense"] += 1
    elif atype == "harvest":
        g["energy"][seat] += 2

    g["log"].append({
        "n": len(g["log"]) + 1,
        "round": g["round"],
        "seat": seat,
        "name": g["seats"][seat]["name"],
        "action": atype,
        "target": target,
    })
    g["last_move_ts"] = time.time()
    _advance_turn(g)
    return True, None


def _advance_turn(g):
    """End the current player's turn, pay the next player's passive income."""
    p = g["numPlayers"]
    nxt = (g["turn"] + 1) % p
    if nxt == 0:                       # a full round just completed
        g["round"] += 1
    g["turn"] = nxt
    g["energy"][nxt] += 1 + count_nodes(g, nxt)   # passive: +1, +1 per owned node
    _check_end(g)


def _check_end(g):
    # immediate win: 14+ cells
    for s in range(g["numPlayers"]):
        if count_cells(g, s) >= WIN_CELLS:
            g["status"] = "finished"
            g["winner"] = s
            return
    if g["round"] > MAX_ROUNDS:
        _finish_by_score(g)


def _finish_by_score(g):
    scores = [(count_cells(g, s), g["energy"][s], s) for s in range(g["numPlayers"])]
    best = max(s[0] for s in scores)
    top = [s for s in scores if s[0] == best]
    if len(top) == 1:
        g["winner"] = top[0][2]
    else:
        best_e = max(s[1] for s in top)
        top2 = [s for s in top if s[1] == best_e]
        g["winner"] = top2[0][2] if len(top2) == 1 else "draw"
    g["status"] = "finished"


def scoreboard(g):
    rows = []
    for s in range(g["numPlayers"]):
        rows.append({
            "seat": s,
            "name": g["seats"][s]["name"],
            "kind": g["seats"][s]["kind"],
            "cells": count_cells(g, s),
            "nodes": count_nodes(g, s),
            "energy": g["energy"][s],
        })
    return rows


# --------------------------------------------------------------------------
# Server-side bot: one simple heuristic, shared by /api/bot seats
# --------------------------------------------------------------------------
def bot_choose(g, seat):
    """Pick a move for the server-side bot. Never returns an illegal move."""
    moves = legal_moves(g, seat)
    if not moves:
        return None
    by = {}
    for m in moves:
        by.setdefault(m["type"], []).append(m["target"])

    mine = set(owned_cells(g, seat))
    my_count = count_cells(g, seat)

    def is_node(t):
        return t is not None and tuple(t) in NODES

    def touches_enemy(t):
        if t is None:
            return False
        return any(g["cells"][ckey(nx, ny)]["owner"] not in (None, seat)
                   for nx, ny in neighbors(t[0], t[1]))

    # 1) expand to win right now
    if my_count + 1 >= WIN_CELLS and by.get("expand"):
        return {"type": "expand", "target": by["expand"][0]}
    # 2) capture an undefended enemy cell (prefer power nodes / weak fronts)
    strikes = by.get("strike", [])
    capture = [t for t in strikes if g["cells"][ckey(*t)]["defense"] == 0]
    if capture:
        capture.sort(key=lambda t: (0 if is_node(t) else 1, 0 if touches_enemy(t) else 1))
        return {"type": "strike", "target": capture[0]}
    # 3) expand onto an empty power node
    nodes_empty = [t for t in by.get("expand", []) if is_node(t)]
    if nodes_empty:
        return {"type": "expand", "target": nodes_empty[0]}
    # 4) any expansion
    if by.get("expand"):
        return {"type": "expand", "target": by["expand"][0]}
    # 5) chip a defended enemy cell if it is close to winning
    if strikes:
        for s in range(g["numPlayers"]):
            if s != seat and count_cells(g, s) >= WIN_CELLS - 2:
                return {"type": "strike", "target": strikes[0]}
    # 6) fortify an exposed frontier cell
    fort = [t for t in by.get("fortify", []) if touches_enemy(t)]
    if fort:
        return {"type": "fortify", "target": fort[0]}
    # 7) harvest
    if by.get("harvest"):
        return {"type": "harvest", "target": None}
    return moves[0]


def autoplay(g, max_steps=400):
    """Let server-side bot seats move until it is a human's turn (or game over)."""
    steps = 0
    while g["status"] == "active" and steps < max_steps:
        s = g["turn"]
        if g["seats"][s]["kind"] != "bot":
            break
        mv = bot_choose(g, s)
        if mv is None:
            break
        ok, _ = apply_move(g, s, mv)
        if not ok:
            break
        steps += 1
    return steps


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------
def serialize(g, token=None):
    you = seat_of_token(g, token)
    grid = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            c = g["cells"][ckey(x, y)]
            row.append({"x": x, "y": y, "owner": c["owner"], "defense": c["defense"],
                        "node": (x, y) in NODES})
        grid.append(row)
    return {
        "gameId": g["gameId"],
        "joinCode": g["joinCode"],
        "numPlayers": g["numPlayers"],
        "status": g["status"],
        "round": g["round"],
        "maxRounds": MAX_ROUNDS,
        "turn": g["turn"],
        "turnName": g["seats"][g["turn"]]["name"],
        "you": you,
        "seats": [{"seat": s["index"], "name": s["name"], "kind": s["kind"],
                   "energy": g["energy"][s["index"]],
                   "cells": count_cells(g, s["index"]),
                   "nodes": count_nodes(g, s["index"]),
                   "color": SEAT_COLORS[s["index"] % 4]} for s in g["seats"]],
        "grid": grid,
        "nodes": [list(n) for n in NODES],
        "winCells": WIN_CELLS,
        "legalMoves": legal_moves(g, you) if you is not None else [],
        "scoreboard": scoreboard(g),
        "winner": g["winner"],
        "log": g["log"],
    }


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Gridlock/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---- plumbing ----
    def _send(self, code, obj=None, raw=None, ctype="application/json; charset=utf-8"):
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Player-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _token(self, qs):
        return self.headers.get("X-Player-Token") or (qs.get("token", [None])[0])

    def _game(self, qs, body=None):
        gid = (qs.get("gameId", [None])[0]
               or (body or {}).get("gameId"))
        if not gid:
            return None
        return GAMES.get(gid)

    # ---- routing ----
    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path == "/health":
                return self._send(200, {"ok": True, "games": len(GAMES)})
            if path == "/api/state":
                return self._api_state(qs)
            if path == "/api/legal":
                return self._api_legal(qs)
            if path == "/api/replay":
                return self._api_replay(qs)
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:                       # noqa: BLE001
            return self._send(500, {"ok": False, "error": "server error: %s" % exc})

    def do_POST(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        body = self._body()
        try:
            if path == "/api/game":
                return self._api_create(body)
            if path == "/api/join":
                return self._api_join(body)
            if path == "/api/move":
                return self._api_move(body, qs)
            if path == "/api/bot":
                return self._api_bot(body, qs)
            return self._send(404, {"ok": False, "error": "not found"})
        except Exception as exc:                       # noqa: BLE001
            return self._send(500, {"ok": False, "error": "server error: %s" % exc})

    def _serve_index(self):
        try:
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self._send(200, raw=fh.read(), ctype="text/html; charset=utf-8")
        except OSError:
            return self._send(500, raw="index.html missing", ctype="text/plain")

    # ---- API endpoints ----
    def _api_create(self, body):
        players = body.get("players", 2)
        host = body.get("hostName", "Host")
        try:
            players = int(players)
        except (TypeError, ValueError):
            players = 2
        g = new_game(players, host)
        fill_seats_and_start(g)
        with _lock:
            autoplay(g)
        return self._send(200, {"ok": True, "gameId": g["gameId"], "joinCode": g["joinCode"],
                                "token": g["seats"][0]["token"], "state": serialize(g, g["seats"][0]["token"])})

    def _api_join(self, body):
        code = str(body.get("joinCode", "")).strip().upper()
        name = (body.get("name") or "Player")[:24]
        kind = body.get("kind", "human")
        if kind not in ("human", "bot"):
            kind = "human"
        g = next((x for x in GAMES.values() if x["joinCode"] == code), None)
        if not g:
            return self._send(404, {"ok": False, "error": "no game with that join code"})
        with _lock:
            seat = next((s for s in g["seats"] if not s["name"]), None)
            if seat is None:
                return self._send(409, {"ok": False, "error": "game is full"})
            seat["name"] = name
            seat["kind"] = kind
            fill_seats_and_start(g)
            autoplay(g)
            return self._send(200, {"ok": True, "seat": seat["index"], "token": seat["token"],
                                    "state": serialize(g, seat["token"])})

    def _api_state(self, qs):
        g = self._game(qs)
        if not g:
            return self._send(404, {"ok": False, "error": "unknown gameId"})
        with _lock:
            autoplay(g)
            return self._send(200, serialize(g, self._token(qs)))

    def _api_legal(self, qs):
        g = self._game(qs)
        if not g:
            return self._send(404, {"ok": False, "error": "unknown gameId"})
        seat = seat_of_token(g, self._token(qs))
        if seat is None:
            return self._send(403, {"ok": False, "error": "bad token"})
        with _lock:
            autoplay(g)
            return self._send(200, {"ok": True, "gameId": g["gameId"], "seat": seat,
                                    "turn": g["turn"], "energy": g["energy"][seat],
                                    "legalMoves": legal_moves(g, seat)})

    def _api_move(self, body, qs):
        g = self._game(qs, body)
        if not g:
            return self._send(404, {"ok": False, "error": "unknown gameId"})
        seat = seat_of_token(g, self._token(qs) or body.get("token"))
        if seat is None:
            return self._send(403, {"ok": False, "error": "bad token"})
        action = body.get("action") or {}
        with _lock:
            autoplay(g)                        # clear any bots queued ahead of you
            ok, err = apply_move(g, seat, action)
            if ok:
                autoplay(g)                    # then let bots respond
            return self._send(200 if ok else 400,
                              {"ok": ok, "state": serialize(g, self._token(qs) or body.get("token"))}
                              if ok else {"ok": False, "error": err, "state": serialize(g, seat)})

    def _api_bot(self, body, qs):
        g = self._game(qs, body)
        if not g:
            return self._send(404, {"ok": False, "error": "unknown gameId"})
        seat = seat_of_token(g, self._token(qs) or body.get("token"))
        if seat != 0:
            return self._send(403, {"ok": False, "error": "only the host (seat 0) may add bots"})
        with _lock:
            empty = next((s for s in g["seats"] if not s["name"]), None)
            if empty is None:
                # no open seat: let the host step out so a bot can take over seat 0,
                # but only before anyone has actually moved.
                own = g["seats"][seat]
                if not g["log"] and g["turn"] == 0 and own["kind"] != "bot":
                    own["kind"] = "bot"
                    own["name"] = body.get("name") or ("Bot %d" % own["index"])
                    fill_seats_and_start(g)
                    autoplay(g)
                    return self._send(200, {"ok": True, "seat": own["index"],
                                            "state": serialize(g, g["seats"][seat]["token"])})
                return self._send(409, {"ok": False, "error": "no empty seats",
                                        "state": serialize(g, g["seats"][seat]["token"])})
            empty["name"] = body.get("name") or ("Bot %d" % (empty["index"]))
            empty["kind"] = "bot"
            fill_seats_and_start(g)
            autoplay(g)
            return self._send(200, {"ok": True, "seat": empty["index"],
                                    "state": serialize(g, g["seats"][seat]["token"])})

    def _api_replay(self, qs):
        g = self._game(qs)
        if not g:
            return self._send(404, {"ok": False, "error": "unknown gameId"})
        # include the initial board so a client can re-simulate the whole match
        initial = {ckey(x, y): {"owner": None, "defense": 0}
                   for x in range(SIZE) for y in range(SIZE)}
        for s in range(g["numPlayers"]):
            hx, hy = CORNERS[s]
            initial[ckey(hx, hy)] = {"owner": s, "defense": 1}
        return self._send(200, {
            "ok": True,
            "gameId": g["gameId"],
            "numPlayers": g["numPlayers"],
            "players": [{"seat": s["index"], "name": s["name"], "kind": s["kind"],
                         "color": SEAT_COLORS[s["index"] % 4]} for s in g["seats"]],
            "nodes": [list(n) for n in NODES],
            "initial": initial,
            "log": g["log"],
            "status": g["status"],
            "winner": g["winner"],
        })


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("GRIDLOCK listening on http://localhost:%d  (Ctrl-C to stop)" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
