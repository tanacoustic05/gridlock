# GRIDLOCK

A small, polished, turn-based multiplayer strategy game for **humans (browser)** and
**AI agents (HTTP/JSON API)**. Two to four players fight over a 6x6 grid: claim empty
cells, crack enemy defenses, fortify your border, and bank energy â the first player to
own **14 cells** wins instantly, otherwise after round 30 the player with the most cells
wins (ties break on energy, then a shared draw). Four symmetric **power nodes** (`*`) pay
**+1 energy** each turn while you hold them. Every turn you get **+1 energy** and take
exactly **one** action â so you must keep choosing between **expanding** wide,
**striking** a neighbor, **fortifying** a front, and **harvesting** for a burst. No single
line dominates: turtling loses the score race, pure rushing runs out of energy, and greedy
expanding leaves your border thin.

## Run it

```bash
python game.py                 # -> http://localhost:8430
```

Open the page, pick a name and player count, press **Create**. Share the 4-letter join
code (or the **Copy invite** link). Empty seats can be filled by the built-in server bot
with the **Add bot** button (host only). There is no install and no login.

Environment overrides: `GRIDLOCK_HOST` (default `0.0.0.0`), `GRIDLOCK_PORT` (default `8430`).

## Run the sample bot

```bash
python bot.py --code ABCD                       # join an existing game by code
python bot.py --create 2                        # create a 2-player game and play it
python bot.py --code ABCD --name Robo --url http://localhost:8430
```

## Repo layout

```
game.py       single-file server: rules engine + HTTP/JSON API + static file host
index.html    vanilla-JS browser UI (served at /) - grid, log, replay, Add bot
bot.py        standalone sample agent (<150 lines, stdlib only) that talks the API
RULES.md      full rules
README.md     this file
LICENSE       MIT
.gitignore
```

## API

All endpoints return JSON. Authenticate with the per-seat token in the
`X-Player-Token` header **or** a `token` query parameter.

### `POST /api/game` â create a game
Body: `{"players": 2|3|4, "hostName": "Alice"}`.
Response: `{"ok":true, "gameId", "joinCode", "token", "state"}`.
The caller owns seat 0 (a home corner cell, defense 1, 3 energy).

```bash
curl -s -X POST localhost:8430/api/game -H 'Content-Type: application/json' \
  -d '{"players":2,"hostName":"Alice"}'
```

### `POST /api/join` â take a seat
Body: `{"joinCode":"ABCD", "name":"Bob", "kind":"human"}` (`kind` may be `bot`).
Response: `{"ok":true, "seat":1, "token":"...", "state"}`.

```bash
curl -s -X POST localhost:8430/api/join -H 'Content-Type: application/json' \
  -d '{"joinCode":"ABCD","name":"Bob","kind":"human"}'
```

### `GET /api/state?gameId=&token=` â full state
Returns everything: `status` (`lobby|active|finished`), `round`, `turn`, `grid` (6 rows of
`{x,y,owner,defense,node}`), `seats`, `winner`, `log`, and `legalMoves` **for the token's seat**.

```bash
curl -s "localhost:8430/api/state?gameId=GID&token=TOK"
```

### `GET /api/legal?gameId=&token=` â just the legal moves
Response: `{"ok":true, "seat", "turn", "energy", "legalMoves":[{"type":"expand","target":[x,y]}, ...]}`.

```bash
curl -s "localhost:8430/api/legal?gameId=GID&token=TOK"
```

### `POST /api/move` â take one action
Body: `{"gameId":"GID","token":"TOK","action":{"type":"strike","target":[4,4]}}`.
`target` is `[x,y]` (or `{"x":..,"y":..}`) and is `null` for `harvest`.
Response: `{"ok":true, "state":{...}}` or `{"ok":false, "error":"...", "state":{...}}`.

```bash
curl -s -X POST localhost:8430/api/move -H 'Content-Type: application/json' \
  -d '{"gameId":"GID","token":"TOK","action":{"type":"expand","target":[1,0]}}'
```

### `POST /api/bot` â fill one empty seat with the server bot (host only)
Body: `{"gameId":"GID","token":"TOK"}` (see the `curl` above; same shape). Response adds `seat`.

### `GET /api/replay?gameId=` â the move log
Response: `{"ok":true, "players", "nodes", "initial", "log", "status", "winner"}`.
`initial` is the board before move 1, so a client can re-simulate the whole match.
Each log entry: `{"n", "round", "seat", "name", "action", "target"}`.

```bash
curl -s "localhost:8430/api/replay?gameId=GID"
```

### `GET /health`
`{"ok":true,"games":<count>}` â a readiness probe.

## Actions

| action  | cost | effect |
|---------|------|--------|
| expand  | 1    | claim an empty cell orthogonally adjacent to one you own |
| strike  | 2    | hit an adjacent enemy cell: defense>0 loses 1; defense 0 is captured |
| fortify | 1    | +1 defense (max 2) on one of your cells |
| harvest | 0    | +2 energy (on top of the +1 passive income) |

## License

MIT â see `LICENSE`.
