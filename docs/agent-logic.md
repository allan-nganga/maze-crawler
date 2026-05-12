# Agent logic

This document describes the behavior of `agent(obs, config)` in `main.py`. The function returns a map of robot UID to action string each turn.

## Persistent memory

State resets when `obs.step` does not increase (a new game).

| Memory | Purpose |
|--------|---------|
| `remembered_mining_nodes` | Mining node positions seen in fog; used to send miners after nodes leave vision |
| `remembered_crystals` | Recently seen crystals (`energy`, `last_seen_step`); kept up to 6 turns after leaving vision |
| `scout_prev_cell` | Last cell each scout occupied; used to avoid immediately walking back into a dead end |

## Shared helpers

- **Walls:** `obs.walls` index is `(row - southBound) * width + col`. Bits N=1, E=2, S=4, W=8.
- **Collision planning:** Mobile units are decided before the factory. Each unit records its destination in `planned_targets` so later units avoid friendly pile-ups on the same cell this turn.
- **Crystal claims:** When a unit commits to a scored crystal, that crystal key is added to `planned_crystal_claims` so another friendly does not chase the same snack.

## Combat awareness on crystals

Visible robots appear in `obs.robots` as `[type, col, row, energy, owner, move_cd, jump_cd, build_cd]`.

Crush hierarchy: Factory > Miner > Worker > Scout. Same type on one cell destroys all of that type. Factory is indestructible against non-factories.

`score_crystal` estimates whether a crystal is worth chasing:

- Base value is crystal energy minus Manhattan distance (rough step cost).
- Crystals south of the unit are ignored.
- Enemy on the crystal: skip if we lose or tie; add a small bonus if we win the crush.
- Friendly of the same type on the crystal: skip (friendly fire).
- Other friendlies on the crystal: penalize the score.
- **Hungry** units need net value at least 6.
- **Not hungry:** only detour for adjacent high-value crystals or short north-side pickups (distance ≤ 2, value ≥ 8).

## Factory (type 0)

The factory does not hunt crystals or refuel.

**Build order** when spawn is safe, build cooldown is ready, and scroll margin is greater than 2:

1. Scouts until 2
2. Miner if a remembered mining node exists
3. Worker until 1
4. Scouts until 4
5. Second miner if nodes are remembered

**Movement:**

- Within 2 rows of `southBound`, move north instead of building.
- If north is blocked and jump is ready, `JUMP_NORTH`; otherwise sidestep east or west.
- Otherwise build when possible, else move north.

**Spawn safety:** `BUILD_*` only if the cell north of the factory is not occupied by a friendly and not already targeted this turn.

## Scout (type 1)

**Refuel and crystals** (via `fuel_action`):

- Disabled within 4 rows of the scroll line.
- On a friendly mine while hungry: `IDLE` to collect.
- Otherwise score visible and remembered crystals; move toward the best unclaimed target.
- If still hungry, path to the nearest friendly mine that is not south of the scout.

**Exploration** when not refueling:

- If only one direction is open, take it (reverse out of a dead end).
- Prefer north; avoid the previous cell and cells already claimed by another friendly this turn.

## Worker (type 2)

**Energy delivery:** If energy is at least 120 and the factory is adjacent with a clear transfer direction, `TRANSFER_*` all energy to the factory.

**Refuel and crystals:** Same scoring and mine rules as scouts when hungry or when a crystal is worth a short detour.

**Default:** Remove the north wall if blocked and affordable; otherwise move north with collision avoidance.

## Miner (type 3)

**On a visible mining node** with enough energy: `TRANSFORM` into a mine.

**Otherwise:** Use the same crystal and mine refuel logic as workers and scouts.

**If not refueling:** Step toward the closest remembered mining node, else north, else a random side or south escape.

## Safety rules

- No southward crystal hunts.
- No refuel detours when within 4 rows of `southBound`.
- Factory never leaves the build-or-survive loop for economy snacks.

## Testing

- `python3 test.py` — single seeded game vs the built-in random bot.
- `python3 benchmark.py` — seeds 1–10 vs `random` and a stronger greedy opponent.
