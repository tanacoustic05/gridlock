# GRIDLOCK - Rules

**Board.** A 6x6 grid; cells are addressed `(x,y)` with `x,y` in `0..5`. A cell is either
empty or owned by one player, and carries a **defense** of `0`, `1` or `2`. Four symmetric
**power nodes** at `(1,1) (4,1) (1,4) (4,4)` each pay their owner **+1 energy** at the start
of every one of that owner's turns.

**Players.** `2` to `4`. Seats are assigned in order; each player starts owning their own
**corner home cell** at `defense 1` and with **3 energy**:

| seat | color  | home corner |
|------|--------|-------------|
| 0    | red    | (0,0)       |
| 1    | blue   | (5,5)       |
| 2    | amber  | (5,0)       |
| 3    | green  | (0,5)       |

**Income.** At the start of each of your turns you gain **+1 energy**, plus **+1 per power
node** you own. You start your first turn with your 3 opening energy.

**A turn = exactly one action**, after which play passes clockwise (round-robin):

| action  | cost | effect |
|---------|------|--------|
| **EXPAND**  | 1 | Claim an **empty** cell orthogonally adjacent to a cell you own. New cell has defense 0. |
| **STRIKE**  | 2 | Target an **orthogonally adjacent enemy** cell. If its defense is `>0`, reduce it by 1. If its defense is `0`, you **capture** it (it becomes yours, defense 0). |
| **FORTIFY** | 1 | Add +1 defense (max 2) to one of your own cells. |
| **HARVEST** | 0 | Gain +2 energy immediately (in addition to the passive +1). |

You may never act out of turn, spend energy you do not have, or target a non-adjacent cell.
`/api/legal` (and `legalMoves` in `/api/state`) always lists exactly the moves currently available.

**Win condition.**

* **Immediate:** the first player to own **14 or more cells** wins at once.
* **After round 30** (i.e. when round 30 has been completed): the player with the **most
  cells** wins. Ties break on **energy**; if still tied, the match is a **shared draw**.

**Rounds.** A "round" is one pass through all seats. The round counter advances when play
returns to seat 0. Score/`round` are reported in every state payload.

**Server bot.** Any empty seat may be filled by the built-in in-process bot (host calls
`POST /api/bot`). It is a simple greedy heuristic, not the sample `bot.py` script.
