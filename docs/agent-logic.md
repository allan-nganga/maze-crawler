# Agent logic

This document describes the behavior of `agent(obs, config)` in `main.py`. The function returns a map of robot UID to action string each turn.

## Persistent memory

State resets when `obs.step` does not increase (a new game).

| Memory | Purpose |
|--------|---------|
| `remembered_mining_nodes` | Mining node positions seen in fog; used to send miners after nodes leave vision |
| `remembered_crystals` | Recently seen crystals (`energy`, `last_seen_step`); kept up to 6 turns after leaving vision |
| `scout_prev_cell` | Last cell each scout occupied; used to avoid immediately walking back into a dead end |
| `remembered_enemies` | Last known type/position of visible enemy robots (by UID), pruned after 8 turns or when below `southBound` |

## Shared helpers

- **Walls:** `obs.walls` index is `(row - southBound) * width + col`. Bits N=1, E=2, S=4, W=8.
- **Known-wall BFS:** goal pathing uses BFS over discovered cells (`obs.walls != -1`) instead of greedy axis steps, so units route around known wall mazes with fewer wasted moves.
- **Scroll projection:** future `southBound` is projected from `scrollCounter` and config intervals to avoid actions that leave the factory too close to the rising floor.
- **Collision planning:** Mobile units are decided before the factory. Each unit records its destination in `planned_targets` so later units avoid friendly pile-ups on the same cell this turn.
- **Crystal assignment:** Mobile units are matched to crystal goals globally each turn using BFS reachability + an optimal assignment pass (Hungarian maximize), then fallback local scoring handles leftovers.
- **Crystal claims:** After action selection, claimed crystal keys are still tracked in `planned_crystal_claims` to prevent same-turn duplicates if fallback logic triggers.

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

**Build order** when spawn is safe, build cooldown is ready, scroll margin is greater than 2, and before step 380:

- Always build at least one scout for vision.
- Among affordable builds (scout up to 4 total, up to 2 workers, up to 2 miners when nodes are remembered), pick the action with the highest **score** from enemy-informed heuristics:
  - **Scouts:** boosted early and when no mining nodes are remembered yet; extra boost if many enemy scouts were seen.
  - **Workers:** boosted when the enemy has two or more scouts (crush), when we have no worker after step 25, or when an enemy worker is known.
  - **Miners:** only if remembered mining nodes exist; boosted for first miner and when an enemy worker is known; slightly penalized if enemy mines are already on the map.
- After step **380**, the factory stops building to preserve energy for tiebreaks.

**Movement:**

- If projected scroll danger is high in the next few turns, force north movement instead of economy actions.
- If north is blocked and jump is ready, `JUMP_NORTH`; otherwise sidestep east or west.
- The factory commits to a lane column (re-evaluated periodically) chosen by reachable north progress plus local north corridor depth.
- When safe, it side-steps toward that lane before resuming north/build behavior.

**Spawn safety:** `BUILD_*` only if the cell north of the factory is not occupied by a friendly and not already targeted this turn.

## Scout (type 1)

**Refuel and crystals** (via `fuel_action`):

- Disabled within 4 rows of the scroll line.
- On a friendly mine while hungry: `IDLE` to collect.
- Otherwise score visible and remembered crystals; move toward the best unclaimed target.
- If still hungry, path to the nearest friendly mine that is not south of the scout.

**Exploration** when not refueling:

- If only one direction is open, take it (reverse out of a dead end).
- Scouts run a frontier search: BFS to the nearest known cell adjacent to unknown space, then push into that opening.
- If no frontier path exists, prefer north and avoid the previous cell and cells already claimed by another friendly this turn.

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
