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
- **Weighted pathing (Dijkstra):** goal-directed moves (`step_toward`, crystal assignment distances, refuel `move_toward_goal`) use Dijkstra on the known graph with non-negative edge costs: base step cost, south penalty, same-turn collision targets, scroll projection by move count × move period, crush-illegal destinations as blocked, and cheap edges onto friendly mines when refueling while hungry. If Dijkstra finds no path, behavior falls back to unit-cost BFS then greedy axis moves.
- **Scroll projection:** future `southBound` is projected from `scrollCounter` and config intervals to avoid actions that leave the factory too close to the rising floor.
- **Collision planning:** Mobile units are decided before the factory. Each unit records its destination in `planned_targets` so later units avoid friendly pile-ups on the same cell this turn.
- **Crystal assignment:** Mobile units are matched to crystal goals globally each turn using Dijkstra edge-count distances on the known map + an optimal assignment pass (Hungarian maximize), then fallback local scoring handles leftovers.
- **Crystal claims:** After action selection, claimed crystal keys are still tracked in `planned_crystal_claims` to prevent same-turn duplicates if fallback logic triggers.
- **Mining-node claims:** Miners share `planned_mining_claims` so two miners on the same turn do not pile onto the same node.
- **Deterministic fallbacks:** All movement helpers (`step_toward`, `pick_move`, `scout_explore_action`, `frontier_opening_action`, miner sidestep) use `deterministic_pick_dir`: prefer NORTH if allowed, then E/W ordered by **map-center bias** (left half → EAST first, right half → WEST first), then SOUTH. No `random.choice` is used in action selection, so identical states always produce identical moves.

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
- When **north is open**, always **`NORTH`** first (collects crystals on the north cell; does not build or lane-shift while north is passable).
- When **north is blocked**: try a short **BFS north-escape** (N/E/W only, scroll-safe rows) toward a known cell with an open north edge; then gated **`JUMP_NORTH`**; then **lane shift** (only while north blocked); then deterministic **E/W** sidestep. Sidestep oscillation at the same column prefers escape before more wiggling.
- Lane column commitment and hysteresis unchanged (`LANE_SWITCH_MARGIN` default `8`).

**Spawn safety:** `BUILD_*` only if spawn is known, empty, not reserved in `planned_targets`, no crystal on spawn (≥5 energy), and no friendly standing on the spawn cell. Critical builds (first scout / first worker after step 25 / first miner when nodes known) can fire even when north is open; otherwise north open → **`NORTH`** (step onto crystals, avoid spawning into allies).

## Scout (type 1)

**Refuel and crystals** (via `fuel_action`):

- Disabled within 4 rows of the scroll line.
- On a friendly mine while hungry: `IDLE` to collect.
- Otherwise score visible and remembered crystals; move toward the best unclaimed target using weighted Dijkstra (edge costs above) with Hungarian-assigned goals when applicable.
- If still hungry, path to the nearest friendly mine that is not south of the scout.

**Exploration** when not refueling:

- If only one direction is open, take it (reverse out of a dead end).
- Scouts run a frontier search: BFS to the nearest known cell adjacent to unknown space, then push into that opening.
- If no frontier path exists, prefer north and avoid the previous cell and cells already claimed by another friendly this turn.

## Worker (type 2)

**Energy delivery:** If energy is at least 120 and the factory is adjacent with a clear transfer direction, `TRANSFER_*` all energy to the factory.

**Return-to-factory loop:** If energy reaches at least 120 and the factory is **not** adjacent, the worker actively paths back to the factory (`worker_return_to_factory_action` → `move_toward_goal`). Previously, full workers just kept walking north and never delivered unless they accidentally bumped into the factory.

**Refuel and crystals:** Same scoring and mine rules as scouts when hungry or when a crystal is worth a short detour.

**Default:** Remove the north wall if blocked and affordable; otherwise move north with collision avoidance.

## Miner (type 3)

**On a visible mining node** with enough energy: `TRANSFORM` into a mine.

**Otherwise:** Use the same crystal and mine refuel logic as workers and scouts.

**Target selection:** When not refueling, the miner picks the best **remembered mining node** by **BFS edge distance** from its current cell (`pick_best_mining_node`), filtering out nodes that fail the scroll-safety projection for the time it would take to reach them, and tie-breaking by **more-northern** (larger row, longer life before scroll). Claimed nodes go into `planned_mining_claims` so a second miner does not pick the same target. Falls back to Manhattan-closest only if no BFS-reachable node exists.

## Safety rules

- No southward crystal hunts.
- No refuel detours when within 4 rows of `southBound`.
- Factory never leaves the build-or-survive loop for economy snacks.

## Testing

- `python3 test.py` — single seeded game vs the built-in random bot.
- `python3 benchmark.py` — seeds 1–10 vs `random` and a stronger greedy opponent.
