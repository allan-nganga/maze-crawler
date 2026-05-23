# Agent logic

This document describes the behavior of `agent(obs, config)` in `main.py`. The function returns a map of robot UID to action string each turn.

## Persistent memory

State resets when `obs.step` does not increase (a new game).

| Memory | Purpose |
|--------|---------|
| `remembered_mining_nodes` | Mining node positions seen in fog; used to send miners after nodes leave vision |
| `remembered_crystals` | Recently seen crystals (`energy`, `last_seen_step`); kept up to 6 turns after leaving vision |
| `scout_prev_cell` | Last cell each scout occupied; used to avoid immediately walking back into a dead end |
| `scout_corridors` | Per-column north runway intel from scouts (`run`, `row`, `step`); pruned after 8 turns or below scroll |
| `scout_next_lane_col` | Lookahead column: best scout corridor at least 2 rows ahead of the factory |
| `remembered_enemies` | Last known type/position of visible enemy robots (by UID), pruned after 8 turns or when below `southBound` |

## Shared helpers

- **Walls:** `obs.walls` index is `(row - southBound) * width + col`. Bits N=1, E=2, S=4, W=8.
- **Known-wall weighted search:** `spatial_step_cost` / `weighted_search_first_step` share the `CRAWL_*_EDGE` tunables on discovered cells (`obs.walls != -1`). Used for factory north-escape (`CRAWL_FACTORY_ESCAPE_MAX_COST`), scout frontier reach (`CRAWL_FRONTIER_MAX_COST`, north-biased, no south), and as a fallback in `step_toward` before unweighted BFS.
- **Weighted pathing (Dijkstra):** goal-directed moves use full Dijkstra with collision/scroll/crush costs. Default edge weights match **v21** (`CRAWL_NORTH_EDGE=2`, `CRAWL_HORIZ_EDGE=0`, south `PATH_BASE+SOUTH_EXTRA`); set `CRAWL_NORTH_EDGE=1` and `CRAWL_HORIZ_EDGE=2` to re-enable north bias. Fallback order: Dijkstra → weighted known-map search → hop-count BFS → greedy axis.
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

- **Miner opener** (before step `CRAWL_MINER_OPENER_STEP`, default 25): if the spawn cell `(col, row+1)` or the tile one step north of that is a mining node (visible or remembered, within `CRAWL_MINER_OPENER_DIST` BFS hops), **`BUILD_MINER`** before the first scout; if the node is only at `row+2`, the factory takes one **`NORTH`** first (`miner_opener_needs_approach_north`).
- Otherwise build at least one scout for vision.
- If **all scouts are dead** and there is still **no worker** after `CRAWL_WORKER_FIRST_STEP`, build the worker before rebuilding scouts.
- After the first scout exists and **no worker** is on the map, the factory **only** attempts `BUILD_WORKER` (no second scout until a worker exists).
- Among affordable builds (scout up to 4 total, up to 2 workers, up to 2 miners when nodes are remembered), pick the action with the highest **score** from enemy-informed heuristics:
  - **Scouts:** boosted early and when no mining nodes are remembered yet; extra boost if many enemy scouts were seen.
  - **Workers:** boosted when the enemy has two or more scouts (crush), when we have no worker after `CRAWL_WORKER_FIRST_STEP` (default 12), or when an enemy worker is known.
  - **Miners:** when remembered nodes exist, enemy miner rush, miner opener, or **proactive first miner** after a worker exists (`step ≥ CRAWL_MINER_PROACTIVE_STEP`, default 28) / late fallback (`CRAWL_MINER_LATE_FIRST_STEP`, default 35). Hard `BUILD_MINER` in the build ladder; `must_build` / `needs_miner` override movement. Extra score from `CRAWL_MINER_LATE_FIRST_BONUS` (default 6). Slightly penalized if enemy mines are already on the map.
- After step **380**, the factory stops building to preserve energy for tiebreaks.

**Movement:**

- If projected scroll danger is high in the next few turns, force north movement instead of economy actions.
- `SCROLL_CRITICAL_MARGIN` is env-driven via `CRAWL_SCROLL_CRITICAL_MARGIN` (default **5**) to trigger earlier `JUMP_NORTH` / panic escapes on low-spawn maps.
- When **north is open**, always **`NORTH`** first (collects crystals on the north cell; does not build or lane-shift while north is passable).
- When **north is blocked**: try a **weighted north-escape** (N/E/W only, scroll-safe rows, prefers fewer/cheaper horizontal steps) toward a known cell with an open north edge; then gated **`JUMP_NORTH`**; then **lane shift** (only while north blocked); then deterministic **E/W** sidestep. Sidestep oscillation at the same column prefers escape before more wiggling.
- Lane column commitment and hysteresis unchanged (`LANE_SWITCH_MARGIN` default `8`). Lane BFS scores get a **scout corridor bonus**: each turn scouts with `north_run_length ≥ 3` on known tiles update `scout_corridors`; factory lane scoring adds `run × 8` plus north progress, and an extra boost for `scout_next_lane_col` (lookahead corridor ahead of the factory).

**Spawn wait:** If a worker/miner build is due but spawn is blocked, the factory **`IDLE`**s or **`JUMP_NORTH`**s (when safe) instead of walking north onto the spawn tile.

**Spawn safety:** `BUILD_*` only if **the wall between factory and spawn cell is passable** (no wall blocks NORTH; previously omitted, causing builds to silently fail when north was walled), spawn cell is known, empty, not reserved in `planned_targets`, no crystal on spawn (≥5 energy), and no friendly standing on the spawn cell. **`NORTH`** is blocked when `(col, row+1)` is occupied by any friendly unit (factory must not crush a scout on the spawn tile). Critical builds (first scout / first worker after `CRAWL_WORKER_FIRST_STEP` / first miner when nodes known) can fire even when north is open; otherwise north open and spawn clear → **`NORTH`**.
**Miner rush:** If an enemy **miner** is visible or remembered, the factory may **`BUILD_MINER`** before the first worker (while `step ≤ CRAWL_MINER_RUSH_STEP`, default 80), with extra miner score (`CRAWL_MINER_ENEMY_MINER_BONUS`) so top-agent miner openings are answered.

**Build tunables (env-driven):** `choose_factory_build` reads its scoring constants from environment variables so they can be swept without editing source:

- `CRAWL_SCOUT_CAP`, `CRAWL_WORKER_CAP`, `CRAWL_MINER_CAP`
- `CRAWL_SCOUT_BASE`, `CRAWL_SCOUT_FEWER_THAN_TWO_BONUS`, `CRAWL_SCOUT_EARLY_BONUS`, `CRAWL_SCOUT_EARLY_STEP`, `CRAWL_SCOUT_ENEMY_PRESSURE_BONUS`, `CRAWL_SCOUT_NO_WORKER_PENALTY`
- `CRAWL_WORKER_BASE`, `CRAWL_WORKER_FIRST_STEP`, `CRAWL_WORKER_FIRST_BONUS`, `CRAWL_WORKER_ENEMY_SCOUT_BONUS`, `CRAWL_WORKER_ENEMY_WORKER_BONUS`, `CRAWL_WORKER_NO_WORKER_BONUS`
- `CRAWL_MINER_BASE`, `CRAWL_MINER_ENEMY_MINE_PENALTY`, `CRAWL_MINER_ENEMY_WORKER_BONUS`, `CRAWL_MINER_FIRST_BONUS`, `CRAWL_MINER_LATE_FIRST_STEP`, `CRAWL_MINER_LATE_FIRST_BONUS`
- `CRAWL_WORKER_TRANSFER_ENERGY` (default 90 — return path and `TRANSFER_*` threshold)
- `CRAWL_MINER_PROACTIVE_STEP` (default 28 — first miner after worker exists)
- `CRAWL_SCROLL_CRITICAL_MARGIN` (default 5 — earlier survival panic / jump behavior)

`experiment_build_order.py` provides preset sweeps and a `--custom` mode; defaults match v18 behavior.

## Scout (type 1)

**Refuel and crystals** (via `fuel_action`):

- Disabled within 4 rows of the scroll line.
- On a friendly mine while hungry: `IDLE` to collect.
- Otherwise score visible and remembered crystals; move toward the best unclaimed target using weighted Dijkstra (edge costs above) with Hungarian-assigned goals when applicable.
- If still hungry, path to the nearest friendly mine that is not south of the scout.

**Exploration** when not refueling (in order):

- If only one direction is open, take it (reverse out of a dead end).
- Within **`CRAWL_SCOUT_EXPLORE_SCROLL_MARGIN`** rows (default 6) of `southBound`: only **`NORTH`** or **`IDLE`** (no sideways frontier detours).
- **Corridor march:** if this column has a fresh **`scout_corridors`** entry with `run ≥ 3`, step **`NORTH`** when scroll-safe.
- **Lane steer:** one scroll-safe **`EAST`/`WEST`** step toward **`scout_next_lane_col`** (best corridor ahead of the factory); on that column, march north if possible.
- **North probe:** if local `north_run_length ≥ CRAWL_SCOUT_NORTH_PROBE_RUN` (default 2), step **`NORTH`**.
- Else **weighted frontier search** (north-biased costs, no south) to the cheapest reachable fog-adjacent cell, then unweighted frontier BFS fallback, then deterministic north-biased fallback (avoid previous cell / friendly blocks).
- While exploring, scouts with `north_run_length ≥ 3` update **`scout_corridors`** so the factory lane scorer can commit to scout-discovered paths.

## Worker (type 2)

**Energy delivery:** If energy is at least `CRAWL_WORKER_TRANSFER_ENERGY` (default **90**) and the factory is adjacent with a clear transfer direction, `TRANSFER_*` all energy to the factory.

**Return-to-factory loop:** If energy reaches at least `CRAWL_WORKER_TRANSFER_ENERGY` (default **90**) and the factory is **not** adjacent, the worker paths back to the factory (`worker_return_to_factory_action` → `move_toward_goal`). Adjacent workers `TRANSFER_*` at the same threshold.

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
- `python3 benchmark.py` — default seeds 1–10 vs `random` and a stronger greedy opponent.
- `python3 benchmark.py --seeds 1 2 3 --opponents greedy` — restrict seeds and opponents.
- `python3 benchmark.py --replay 4 --replay-every 5` — per-step factory/robot dump for one seed (use to diagnose losses).
- `python3 experiment_lane_margin.py` — sweep `LANE_SWITCH_MARGIN`.
- `python3 experiment_build_order.py --preset all --seeds $(seq 1 30) --opponents random` — sweep build-order constants vs a 30-seed baseline.
