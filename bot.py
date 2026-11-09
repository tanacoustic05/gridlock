#!/usr/bin/env python3
"""
GRIDLOCK sample bot - a standalone HTTP/JSON client.

It shows how little it takes for an AI agent to play GRIDLOCK: join a game
(or create one), poll the state, and POST one move per turn using a simple
greedy heuristic:

    1. EXPAND onto an adjacent empty cell   (claim ground)
    2. STRIKE an undefended adjacent enemy  (cheap capture)
    3. FORTIFY a border cell                (hold the front)
    4. HARVEST                              (bank energy)

Usage:
    python bot.py --code ABCD                          # join a game by code
    python bot.py --create 2                           # create + play a 2p game
    python bot.py --code ABCD --name Robo --url http://localhost:8430

Options:
    --url BASE     server base url      (default http://localhost:8430)
    --code CODE    join code to join
    --create N     instead of joining, create an N-player game and fill the
                   other seats with the server-side bot
    --name NAME    display name         (default "SampleBot")
    --quiet        only print the final result

Stdlib only. Requires the game server to be running.
"""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SIZE = 6


def call(base, path, method="GET", body=None, token=None):
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Player-Token", token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def choose(state):
    """Pick a move from the server-provided legalMoves list."""
    legal = state.get("legalMoves") or []
    if not legal:
        return None
    grid = state["grid"]
    mine = state["you"]
    by = {}
    for m in legal:
        by.setdefault(m["type"], []).append(m["target"])

    def is_node(t):
        return t is not None and tuple(t) in [tuple(n) for n in state["nodes"]]

    def enemy_adjacent(t):
        if t is None:
            return False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = t[0] + dx, t[1] + dy
            if 0 <= nx < SIZE and 0 <= ny < SIZE:
                o = grid[ny][nx]["owner"]
                if o is not None and o != mine:
                    return True
        return False

    # 1. expand (prefer a power node)
    expand = by.get("expand", [])
    if expand:
        expand = sorted(expand, key=lambda t: (0 if is_node(t) else 1))
        return {"type": "expand", "target": expand[0]}
    # 2. strike an undefended enemy
    undef = [t for t in by.get("strike", []) if grid[t[1]][t[0]]["defense"] == 0]
    if undef:
        return {"type": "strike", "target": undef[0]}
    # 3. fortify a border cell
    border = [t for t in by.get("fortify", []) if enemy_adjacent(t)]
    if border:
        return {"type": "fortify", "target": border[0]}
    # 4. harvest
    if by.get("harvest"):
        return {"type": "harvest", "target": None}
    return legal[0]


def main():
    ap = argparse.ArgumentParser(description="GRIDLOCK sample bot")
    ap.add_argument("--url", default="http://localhost:8430")
    ap.add_argument("--code")
    ap.add_argument("--create", type=int, metavar="N")
    ap.add_argument("--name", default="SampleBot")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    base = args.url.rstrip("/")

    def log(*a):
        if not args.quiet:
            print(*a, flush=True)

    if args.create:
        g = call(base, "/api/game", "POST", {"players": args.create, "hostName": args.name})
        gid, token = g["gameId"], g["token"]
        empties = len([s for s in g["state"]["seats"] if not s["name"]])
        log("created game %s (code %s)" % (gid, g["joinCode"]))
        for _ in range(empties):                      # fill the other seats with server bots
            call(base, "/api/bot", "POST", {"gameId": gid, "token": token}, token)
    elif args.code:
        g = call(base, "/api/join", "POST",
                 {"joinCode": args.code.upper(), "name": args.name, "kind": "human"})
        gid, token = g["state"]["gameId"], g["token"]
        log("joined game %s as seat %d" % (gid, g["seat"]))
    else:
        ap.error("give --code CODE or --create N")

    moves = 0
    while True:
        state = call(base, "/api/state?" + urllib.parse.urlencode({"gameId": gid, "token": token}))
        if state["status"] == "finished":
            log("FINISHED  winner=%s  moves_by_bot=%d" % (state["winner"], moves))
            return
        if state["you"] != state["turn"]:
            time.sleep(0.4)
            continue
        mv = choose(state)
        if mv is None:
            time.sleep(0.4)
            continue
        r = call(base, "/api/move", "POST",
                 {"gameId": gid, "token": token, "action": mv}, token)
        if not r.get("ok"):
            log("move rejected:", r.get("error"))
            time.sleep(0.4)
            continue
        moves += 1
        log("move %2d: %-8s %s" % (moves, mv["type"], mv["target"]))
        if moves > 500:
            log("safety stop")
            return


if __name__ == "__main__":
    main()
