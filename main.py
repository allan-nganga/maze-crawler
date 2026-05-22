import heapq
import os
from collections import deque
from random import choice

remembered_mining_nodes = set()
remembered_crystals = {}
remembered_enemies = {}
scout_prev_cell = {}
factory_lane_col = None
factory_prev_col = None
factory_last_horiz = None
factory_last_row = None
scout_corridors = {}
scout_next_lane_col = None
last_seen_step = -1

CRYSTAL_MEMORY_TURNS = 6
ENEMY_MEMORY_TURNS = 8
SCOUT_CORRIDOR_MEMORY_TURNS = 8
SCOUT_CORRIDOR_MIN_RUN = 3
SCOUT_CORRIDOR_LANE_BONUS = 8


def _env_int(name, default):
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# choose_factory_build tunables. Override via env for sweeps (see experiment_build_order.py).
SCOUT_CAP = _env_int("CRAWL_SCOUT_CAP", 4)
WORKER_CAP = _env_int("CRAWL_WORKER_CAP", 2)
MINER_CAP = _env_int("CRAWL_MINER_CAP", 2)
SCOUT_BASE_SCORE = _env_int("CRAWL_SCOUT_BASE", 5)
SCOUT_FEWER_THAN_TWO_BONUS = _env_int("CRAWL_SCOUT_FEWER_THAN_TWO_BONUS", 8)
SCOUT_EARLY_BONUS = _env_int("CRAWL_SCOUT_EARLY_BONUS", 4)
SCOUT_EARLY_STEP = _env_int("CRAWL_SCOUT_EARLY_STEP", 60)
SCOUT_ENEMY_PRESSURE_BONUS = _env_int("CRAWL_SCOUT_ENEMY_PRESSURE_BONUS", 3)
SCOUT_NO_WORKER_PENALTY = _env_int("CRAWL_SCOUT_NO_WORKER_PENALTY", 6)
WORKER_BASE_SCORE = _env_int("CRAWL_WORKER_BASE", 3)
WORKER_FIRST_STEP = _env_int("CRAWL_WORKER_FIRST_STEP", 8)
WORKER_FIRST_BONUS = _env_int("CRAWL_WORKER_FIRST_BONUS", 4)
WORKER_ENEMY_SCOUT_BONUS = _env_int("CRAWL_WORKER_ENEMY_SCOUT_BONUS", 6)
WORKER_ENEMY_WORKER_BONUS = _env_int("CRAWL_WORKER_ENEMY_WORKER_BONUS", 3)
WORKER_NO_WORKER_BONUS = _env_int("CRAWL_WORKER_NO_WORKER_BONUS", 10)
MINER_BASE_SCORE = _env_int("CRAWL_MINER_BASE", 7)
MINER_ENEMY_MINE_PENALTY = _env_int("CRAWL_MINER_ENEMY_MINE_PENALTY", 3)
MINER_ENEMY_WORKER_BONUS = _env_int("CRAWL_MINER_ENEMY_WORKER_BONUS", 2)
MINER_FIRST_BONUS = _env_int("CRAWL_MINER_FIRST_BONUS", 4)
MINER_LATE_FIRST_STEP = _env_int("CRAWL_MINER_LATE_FIRST_STEP", 90)
MINER_LATE_FIRST_BONUS = _env_int("CRAWL_MINER_LATE_FIRST_BONUS", 0)
ENDGAME_NO_BUILD_STEP = 380
LANE_REEVAL_INTERVAL = 8
# Factory must not sidestep when this close to the scroll line (see choose_factory_blocked_move).
SCROLL_CRITICAL_MARGIN = 3
FACTORY_SCROLL_PANIC_TURNS = 4
# Require strictly more than this gap vs current lane to switch (ties always keep current).
# Override: CRAWL_LANE_SWITCH_MARGIN=2 python3 benchmark.py  (see experiment_lane_margin.py)
LANE_SWITCH_MARGIN = max(
    0, int(os.environ.get("CRAWL_LANE_SWITCH_MARGIN", "8"))
)
# Dijkstra edge costs (destination_move_cost). Sweep via experiment or env.
PATH_EDGE_BASE = _env_int("CRAWL_PATH_BASE", 2)
PATH_NORTH_EDGE = _env_int("CRAWL_NORTH_EDGE", 1)
PATH_HORIZ_EXTRA = _env_int("CRAWL_HORIZ_EDGE", 2)
PATH_SOUTH_EXTRA = _env_int("CRAWL_SOUTH_EDGE", 3)
# Scout explore (phase 2): corridor-march before frontier BFS.
SCOUT_EXPLORE_SCROLL_MARGIN = _env_int("CRAWL_SCOUT_EXPLORE_SCROLL_MARGIN", 6)
SCOUT_NORTH_PROBE_MIN_RUN = _env_int("CRAWL_SCOUT_NORTH_PROBE_RUN", 2)
# Weighted known-map search limits (factory escape, frontier, goal fallback).
FACTORY_ESCAPE_MAX_COST = _env_int("CRAWL_FACTORY_ESCAPE_MAX_COST", 18)
FRONTIER_MAX_COST = _env_int("CRAWL_FRONTIER_MAX_COST", 48)
WEIGHTED_TARGET_MAX_COST = _env_int("CRAWL_WEIGHTED_TARGET_MAX_COST", 60)
INVALID_UTILITY = -10**6
INF_PATH_COST = 10**9
DIR_ORDER = ["NORTH", "EAST", "WEST", "SOUTH"]
DIRECTION_DELTAS = {
    "NORTH": (0, 1),
    "SOUTH": (0, -1),
    "EAST": (1, 0),
    "WEST": (-1, 0),
}
DIRECTION_WALL_BITS = {
    "NORTH": 1,
    "EAST": 2,
    "SOUTH": 4,
    "WEST": 8,
}
CRUSHES = {
    (0, 3),
    (0, 2),
    (0, 1),
    (3, 2),
    (3, 1),
    (2, 1),
}


def parse_pos_key(pos_key):
    col_str, row_str = pos_key.split(",")
    return int(col_str), int(row_str)


def manhattan(col, row, target_col, target_row):
    return abs(target_col - col) + abs(target_row - row)


def closest_node(col, row, node_keys):
    return min(
        node_keys,
        key=lambda key: manhattan(col, row, *parse_pos_key(key)),
    )


def robots_at(col, row, obs, owner=None):
    matches = []
    for uid, data in obs.robots.items():
        if data[1] == col and data[2] == row:
            if owner is None or data[4] == owner:
                matches.append((uid, data[0], data[4]))
    return matches


def crush_outcome(attacker_type, defender_type):
    if attacker_type == defender_type:
        return "both"
    if defender_type == 0:
        return "lose"
    if attacker_type == 0:
        return "win"
    if (attacker_type, defender_type) in CRUSHES:
        return "win"
    if (defender_type, attacker_type) in CRUSHES:
        return "lose"
    return "both"


def in_bounds(col, row, obs, config):
    return 0 <= col < config.width and obs.southBound <= row <= obs.northBound


def get_scroll_interval(step, config):
    if step >= config.scrollRampSteps:
        return config.scrollEndInterval
    progress = step / max(1, config.scrollRampSteps)
    interval = config.scrollStartInterval - (
        config.scrollStartInterval - config.scrollEndInterval
    ) * progress
    return max(config.scrollEndInterval, round(interval))


def project_south_bound(obs, config, turns_ahead):
    south = obs.southBound
    counter = getattr(obs, "scrollCounter", get_scroll_interval(obs.step, config))
    step = obs.step

    for _ in range(max(0, turns_ahead)):
        counter -= 1
        if counter <= 0:
            south += 1
            counter = get_scroll_interval(step, config)
        step += 1
    return south


def is_row_scroll_safe(row, obs, config, turns_ahead=0, buffer_rows=1):
    future_south = project_south_bound(obs, config, turns_ahead)
    return row - future_south >= buffer_rows


def wall_value(col, row, obs, config):
    if not in_bounds(col, row, obs, config):
        return None
    idx = (row - obs.southBound) * config.width + col
    if idx < 0 or idx >= len(obs.walls):
        return None
    return obs.walls[idx]


def next_cell(col, row, direction):
    dcol, drow = DIRECTION_DELTAS[direction]
    return col + dcol, row + drow


def can_move_known(col, row, direction, obs, config):
    target_col, target_row = next_cell(col, row, direction)
    if not in_bounds(target_col, target_row, obs, config):
        return False

    w = wall_value(col, row, obs, config)
    if w is None or w == -1:
        return False

    if w & DIRECTION_WALL_BITS[direction]:
        return False

    target_w = wall_value(target_col, target_row, obs, config)
    if target_w is None or target_w == -1:
        return False
    return True


def get_move_period_local(rtype, config):
    if rtype == 0:
        return config.factoryMovePeriod
    if rtype == 1:
        return 1
    if rtype == 2:
        return config.workerMovePeriod
    if rtype == 3:
        return config.minerMovePeriod
    return 1


def build_path_ctx(obs, config, rtype, intent, planned_targets, hungry):
    return {
        "obs": obs,
        "config": config,
        "rtype": rtype,
        "intent": intent,
        "hungry": bool(hungry),
        "planned": planned_targets or set(),
        "player": obs.player,
    }


def destination_move_cost(to_col, to_row, direction, arrival_turns, ctx):
    obs = ctx["obs"]
    config = ctx["config"]
    rtype = ctx["rtype"]
    player = ctx["player"]

    if not is_row_scroll_safe(
        to_row, obs, config, turns_ahead=arrival_turns, buffer_rows=1
    ):
        return INF_PATH_COST

    pos_key = f"{to_col},{to_row}"
    for _, occ_type, owner in robots_at(to_col, to_row, obs):
        if owner == player:
            if occ_type == rtype:
                return INF_PATH_COST
        else:
            outcome = crush_outcome(rtype, occ_type)
            if outcome in ("lose", "both"):
                return INF_PATH_COST

    if direction == "NORTH":
        w = PATH_NORTH_EDGE
    elif direction == "SOUTH":
        w = PATH_EDGE_BASE + PATH_SOUTH_EXTRA
    elif direction in ("EAST", "WEST"):
        w = PATH_EDGE_BASE + PATH_HORIZ_EXTRA
    else:
        w = PATH_EDGE_BASE

    if pos_key in ctx["planned"]:
        w += 5

    if (
        ctx["intent"] == "refuel"
        and ctx["hungry"]
        and pos_key in obs.mines
        and obs.mines[pos_key][2] == player
    ):
        w = min(w, 1)

    return w


def dijkstra_spatial(start_col, start_row, obs, config, ctx, stop_at=None):
    start = (start_col, start_row)
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return {}, {}, {}

    period = get_move_period_local(ctx["rtype"], config)
    dist_cost = {start: 0}
    dist_edges = {start: 0}
    parent = {}
    tie = 0
    heap = [(0, tie, start_col, start_row, 0)]

    while heap:
        cost, _, col, row, edges = heapq.heappop(heap)
        state = (col, row)
        if cost > dist_cost.get(state, INF_PATH_COST):
            continue
        if cost == dist_cost[state] and edges > dist_edges.get(state, 0):
            continue
        if stop_at is not None and state == stop_at:
            break

        for direction in DIR_ORDER:
            if not can_move_known(col, row, direction, obs, config):
                continue
            nc, nr = next_cell(col, row, direction)
            nstate = (nc, nr)
            new_edges = edges + 1
            arrival_turns = new_edges * period
            step_w = destination_move_cost(nc, nr, direction, arrival_turns, ctx)
            if step_w >= INF_PATH_COST:
                continue
            new_cost = cost + step_w
            old_c = dist_cost.get(nstate, INF_PATH_COST)
            old_e = dist_edges.get(nstate, INF_PATH_COST)
            if new_cost < old_c or (new_cost == old_c and new_edges < old_e):
                dist_cost[nstate] = new_cost
                dist_edges[nstate] = new_edges
                parent[nstate] = (col, row, direction)
                tie += 1
                heapq.heappush(heap, (new_cost, tie, nc, nr, new_edges))

    return dist_cost, dist_edges, parent


def first_step_from_parent(start, goal, parent):
    if goal == start:
        return "IDLE"
    if goal not in parent:
        return None
    cur = goal
    first_dir = None
    while cur != start:
        pcol, prow, direction = parent[cur]
        first_dir = direction
        cur = (pcol, prow)
    return first_dir


def dijkstra_first_step_to_target(start_col, start_row, goal_col, goal_row, obs, config, ctx):
    goal = (goal_col, goal_row)
    start = (start_col, start_row)
    if start == goal:
        return "IDLE"
    if wall_value(goal_col, goal_row, obs, config) in (None, -1):
        return None

    _, _, parent = dijkstra_spatial(
        start_col, start_row, obs, config, ctx, stop_at=goal
    )
    if goal not in parent:
        return None
    return first_step_from_parent(start, goal, parent)


def spatial_step_cost(direction, allow_south=False):
    """Edge cost for known-map weighted search (uses PATH_* tunables)."""
    if direction == "SOUTH" and not allow_south:
        return INF_PATH_COST
    if direction == "NORTH":
        return PATH_NORTH_EDGE
    if direction in ("EAST", "WEST"):
        return PATH_EDGE_BASE + PATH_HORIZ_EXTRA
    if direction == "SOUTH":
        return PATH_EDGE_BASE + PATH_SOUTH_EXTRA
    return PATH_EDGE_BASE


def weighted_search_first_step(
    start_col,
    start_row,
    obs,
    config,
    is_goal_fn,
    max_cost,
    planned_targets=None,
    neighbor_dirs=None,
    allow_south=False,
    scroll_buffer=1,
):
    """Dijkstra on known walls; returns first step toward a minimum-cost goal cell."""
    if neighbor_dirs is None:
        neighbor_dirs = ("NORTH", "EAST", "WEST", "SOUTH")

    start = (start_col, start_row)
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return None
    if is_goal_fn(start_col, start_row):
        return None

    dist = {start: 0}
    first_step = {}
    tie = 0
    heap = [(0, tie, start_col, start_row)]

    while heap:
        cost, _, col, row = heapq.heappop(heap)
        state = (col, row)
        if cost > dist.get(state, INF_PATH_COST):
            continue
        if is_goal_fn(col, row):
            return first_step.get(state)

        for direction in neighbor_dirs:
            if not can_move_known(col, row, direction, obs, config):
                continue
            step_w = spatial_step_cost(direction, allow_south=allow_south)
            if step_w >= INF_PATH_COST:
                continue
            nc, nr = next_cell(col, row, direction)
            nstate = (nc, nr)
            if not is_row_scroll_safe(
                nr, obs, config, turns_ahead=0, buffer_rows=scroll_buffer
            ):
                continue
            nkey = f"{nc},{nr}"
            if state == start and planned_targets and nkey in planned_targets:
                continue
            new_cost = cost + step_w
            if new_cost > max_cost:
                continue
            if new_cost < dist.get(nstate, INF_PATH_COST):
                dist[nstate] = new_cost
                first_step[nstate] = (
                    direction if state == start else first_step[state]
                )
                tie += 1
                heapq.heappush(heap, (new_cost, tie, nc, nr))

    return None


def weighted_first_step_to_target(
    start_col, start_row, goal_col, goal_row, obs, config, planned_targets=None
):
    if (start_col, start_row) == (goal_col, goal_row):
        return "IDLE"
    if wall_value(goal_col, goal_row, obs, config) in (None, -1):
        return None

    goal = (goal_col, goal_row)

    def is_goal(c, r):
        return (c, r) == goal

    return weighted_search_first_step(
        start_col,
        start_row,
        obs,
        config,
        is_goal,
        WEIGHTED_TARGET_MAX_COST,
        planned_targets=planned_targets,
        neighbor_dirs=DIR_ORDER,
        allow_south=True,
        scroll_buffer=1,
    )


def weighted_first_step_to_frontier(
    start_col, start_row, can_go, obs, config, planned_targets, prev_key
):
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return None
    if is_frontier_cell(start_col, start_row, obs, config):
        return frontier_opening_action(
            start_col, start_row, can_go, obs, config, planned_targets, prev_key
        )

    def is_goal(c, r):
        return is_frontier_cell(c, r, obs, config)

    return weighted_search_first_step(
        start_col,
        start_row,
        obs,
        config,
        is_goal,
        FRONTIER_MAX_COST,
        planned_targets=planned_targets,
        neighbor_dirs=("NORTH", "EAST", "WEST"),
        allow_south=False,
        scroll_buffer=1,
    )


def is_frontier_cell(col, row, obs, config):
    w = wall_value(col, row, obs, config)
    if w is None or w == -1:
        return False

    for direction in DIR_ORDER:
        if w & DIRECTION_WALL_BITS[direction]:
            continue
        next_col, next_row = next_cell(col, row, direction)
        if not in_bounds(next_col, next_row, obs, config):
            continue
        if wall_value(next_col, next_row, obs, config) == -1:
            return True
    return False


def frontier_opening_action(col, row, can_go, obs, config, planned_targets, prev_key):
    options = []
    for direction in DIR_ORDER:
        if not can_go[direction]:
            continue
        next_col, next_row = next_cell(col, row, direction)
        if not in_bounds(next_col, next_row, obs, config):
            continue
        next_key = f"{next_col},{next_row}"
        if wall_value(next_col, next_row, obs, config) != -1:
            continue
        if next_key in planned_targets:
            continue
        if prev_key is not None and next_key == prev_key:
            continue
        options.append(direction)

    return deterministic_pick_dir(col, options, config, prefer_north=True)


def bfs_first_step_to_target(start_col, start_row, goal_col, goal_row, obs, config):
    if (start_col, start_row) == (goal_col, goal_row):
        return "IDLE"
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return None
    if wall_value(goal_col, goal_row, obs, config) in (None, -1):
        return None

    start = (start_col, start_row)
    queue = deque([start])
    visited = {start}
    first_step = {}

    while queue:
        col, row = queue.popleft()
        for direction in DIR_ORDER:
            if not can_move_known(col, row, direction, obs, config):
                continue
            next_col, next_row = next_cell(col, row, direction)
            state = (next_col, next_row)
            if state in visited:
                continue
            visited.add(state)
            first_step[state] = direction if (col, row) == start else first_step[(col, row)]
            if state == (goal_col, goal_row):
                return first_step[state]
            queue.append(state)

    return None


def bfs_first_step_to_frontier(start_col, start_row, can_go, obs, config, planned_targets, prev_key):
    start = (start_col, start_row)
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return None

    queue = deque([start])
    visited = {start}
    first_step = {}

    while queue:
        col, row = queue.popleft()
        if is_frontier_cell(col, row, obs, config):
            if (col, row) == start:
                action = frontier_opening_action(
                    col, row, can_go, obs, config, planned_targets, prev_key
                )
                if action is not None:
                    return action
            else:
                return first_step[(col, row)]

        for direction in DIR_ORDER:
            if not can_move_known(col, row, direction, obs, config):
                continue
            next_col, next_row = next_cell(col, row, direction)
            next_key = f"{next_col},{next_row}"
            if (col, row) == start and next_key in planned_targets:
                continue
            state = (next_col, next_row)
            if state in visited:
                continue
            visited.add(state)
            first_step[state] = direction if (col, row) == start else first_step[(col, row)]
            queue.append(state)

    return None


def bfs_distances(start_col, start_row, obs, config):
    start = (start_col, start_row)
    if wall_value(start_col, start_row, obs, config) in (None, -1):
        return {}

    queue = deque([start])
    distances = {start: 0}
    while queue:
        col, row = queue.popleft()
        for direction in DIR_ORDER:
            if not can_move_known(col, row, direction, obs, config):
                continue
            next_col, next_row = next_cell(col, row, direction)
            state = (next_col, next_row)
            if state in distances:
                continue
            distances[state] = distances[(col, row)] + 1
            queue.append(state)
    return distances


def north_run_length(col, row, obs, config, max_steps=8):
    run = 0
    cur_col = col
    cur_row = row
    for _ in range(max_steps):
        if not can_move_known(cur_col, cur_row, "NORTH", obs, config):
            break
        cur_col, cur_row = next_cell(cur_col, cur_row, "NORTH")
        run += 1
    return run


def prune_scout_corridors(obs, step):
    for col in list(scout_corridors.keys()):
        entry = scout_corridors[col]
        if entry["row"] < obs.southBound:
            scout_corridors.pop(col, None)
        elif step - entry["step"] > SCOUT_CORRIDOR_MEMORY_TURNS:
            scout_corridors.pop(col, None)


def update_scout_corridors(obs, config, my_robots, factory_row):
    global scout_next_lane_col

    step = obs.step
    prune_scout_corridors(obs, step)

    for data in my_robots.values():
        if data[0] != 1:
            continue
        sc, sr = data[1], data[2]
        if wall_value(sc, sr, obs, config) in (None, -1):
            continue
        if not is_row_scroll_safe(sr, obs, config, turns_ahead=0, buffer_rows=1):
            continue
        run = north_run_length(sc, sr, obs, config)
        if run < SCOUT_CORRIDOR_MIN_RUN:
            continue
        prev = scout_corridors.get(sc)
        if prev is None or run > prev["run"] or (run == prev["run"] and sr > prev["row"]):
            scout_corridors[sc] = {"run": run, "row": sr, "step": step}

    scout_next_lane_col = None
    best_metric = None
    for col, entry in scout_corridors.items():
        if entry["row"] <= factory_row + 1:
            continue
        metric = (entry["row"], entry["run"])
        if best_metric is None or metric > best_metric:
            best_metric = metric
            scout_next_lane_col = col


def scout_corridor_lane_bonus(col, factory_row, step):
    entry = scout_corridors.get(col)
    if entry is None:
        return 0
    if step - entry["step"] > SCOUT_CORRIDOR_MEMORY_TURNS:
        return 0
    bonus = entry["run"] * SCOUT_CORRIDOR_LANE_BONUS
    if entry["row"] > factory_row:
        bonus += (entry["row"] - factory_row) * 3
    return bonus


def compute_factory_lane_scores(factory_col, factory_row, obs, config):
    distances = bfs_distances(factory_col, factory_row, obs, config)
    if not distances:
        return {}

    lane_scores = {}
    center_col = config.width // 2
    step = obs.step
    for (col, row), dist in distances.items():
        if row < factory_row:
            continue
        progress = row - factory_row
        if progress == 0 and dist > 0:
            continue
        if not is_row_scroll_safe(row, obs, config, turns_ahead=dist, buffer_rows=1):
            continue
        run = north_run_length(col, row, obs, config)
        center_bias = -abs(col - center_col)
        score = progress * 4 + run * 6 - dist * 2 + center_bias
        score += scout_corridor_lane_bonus(col, factory_row, step)
        if scout_next_lane_col is not None and col == scout_next_lane_col:
            score += 12
        best = lane_scores.get(col)
        if best is None or score > best:
            lane_scores[col] = score
    return lane_scores


def pick_factory_lane_with_hysteresis(factory_col, current_lane, lane_scores):
    if not lane_scores:
        return factory_col
    best_col, best_score = max(lane_scores.items(), key=lambda item: item[1])
    if current_lane is None:
        return best_col
    if current_lane not in lane_scores:
        return best_col
    cur_score = lane_scores[current_lane]
    if cur_score == best_score:
        return current_lane
    if best_score > cur_score + LANE_SWITCH_MARGIN:
        return best_col
    return current_lane


def crystal_energy_at(col, row, obs):
    key = f"{col},{row}"
    if key in obs.crystals:
        return obs.crystals[key]
    if key in remembered_crystals:
        return remembered_crystals[key][0]
    return None


def factory_spawn_is_safe(
    col, row, obs, config, occupied_by_me, planned_targets, my_robots
):
    spawn_col, spawn_row = col, row + 1
    spawn_key = f"{spawn_col},{spawn_row}"
    if spawn_key in occupied_by_me or spawn_key in planned_targets:
        return False
    if not in_bounds(spawn_col, spawn_row, obs, config):
        return False
    if wall_value(spawn_col, spawn_row, obs, config) in (None, -1):
        return False
    if not can_move_known(col, row, "NORTH", obs, config):
        return False

    crystal_e = crystal_energy_at(spawn_col, spawn_row, obs)
    if crystal_e is not None and crystal_e >= 5:
        return False

    for _, data in my_robots.items():
        if data[0] == 0:
            continue
        ucol, urow = data[1], data[2]
        if ucol == spawn_col and urow == spawn_row:
            return False
        if ucol == spawn_col and urow == spawn_row:
            return False

    return True


def factory_jump_north_is_safe(
    col, row, obs, config, occupied_by_me, planned_targets, urgent_scroll, can_go
):
    land_col, land_row = col, row + 2
    if not in_bounds(land_col, land_row, obs, config):
        return False
    land_key = f"{land_col},{land_row}"
    if land_key in occupied_by_me or land_key in planned_targets:
        return False
    if wall_value(land_col, land_row, obs, config) in (None, -1):
        return False

    if not urgent_scroll:
        mid_crystal = crystal_energy_at(col, row + 1, obs)
        if mid_crystal is not None and mid_crystal >= 15:
            if can_go.get("EAST") or can_go.get("WEST"):
                return False

    land_crystal = crystal_energy_at(land_col, land_row, obs)
    if land_crystal is not None and land_crystal >= 30 and not urgent_scroll:
        return False
    return True


def factory_local_north_escape(col, row, can_go, obs, config, planned_targets, max_depth=6):
    """Weighted search for a cheap path to a cell with a known-open north edge."""
    if wall_value(col, row, obs, config) in (None, -1):
        return None
    if can_move_known(col, row, "NORTH", obs, config):
        return "NORTH" if can_go.get("NORTH") else None

    max_cost = min(
        FACTORY_ESCAPE_MAX_COST,
        max(PATH_NORTH_EDGE, PATH_EDGE_BASE + PATH_HORIZ_EXTRA) * max(1, max_depth),
    )

    def north_open(c, r):
        return can_move_known(c, r, "NORTH", obs, config)

    return weighted_search_first_step(
        col,
        row,
        obs,
        config,
        north_open,
        max_cost,
        planned_targets=planned_targets,
        neighbor_dirs=("NORTH", "EAST", "WEST"),
        allow_south=False,
        scroll_buffer=2,
    )


def deterministic_factory_sidestep(col, can_go, config, lane_col=None):
    side = [d for d in ["EAST", "WEST"] if can_go[d]]
    if not side:
        return "IDLE"
    if len(side) == 1:
        return side[0]
    if lane_col is not None and lane_col != col:
        if lane_col > col and "EAST" in side:
            return "EAST"
        if lane_col < col and "WEST" in side:
            return "WEST"
    mid = config.width // 2
    if col < mid:
        return "EAST" if "EAST" in side else "WEST"
    return "WEST" if "WEST" in side else "EAST"


def choose_factory_blocked_move(
    col,
    row,
    can_go,
    obs,
    config,
    planned_targets,
    occupied_by_me,
    urgent_scroll,
    jump_cd,
    lane_direction,
    can_lane_shift,
    factory_lane_col,
    scroll_margin,
):
    global factory_prev_col, factory_last_horiz, factory_last_row

    critical_scroll = scroll_margin <= SCROLL_CRITICAL_MARGIN

    escape = factory_local_north_escape(
        col, row, can_go, obs, config, planned_targets
    )
    row_stuck = factory_last_row == row and factory_prev_col == col
    horiz_stuck = row_stuck and factory_last_horiz in ("EAST", "WEST")
    panic = urgent_scroll or critical_scroll

    if escape is not None and (not horiz_stuck or panic):
        dest = target_cell(col, row, escape)
        if dest not in planned_targets:
            return escape

    if jump_cd == 0 and factory_jump_north_is_safe(
        col, row, obs, config, occupied_by_me, planned_targets, True, can_go
    ):
        if panic or horiz_stuck:
            return "JUMP_NORTH"

    if escape is not None:
        dest = target_cell(col, row, escape)
        if dest not in planned_targets:
            return escape

    if jump_cd == 0 and factory_jump_north_is_safe(
        col, row, obs, config, occupied_by_me, planned_targets, panic, can_go
    ):
        return "JUMP_NORTH"

    if can_go.get("NORTH") and panic:
        return "NORTH"

    if not critical_scroll:
        if can_lane_shift and lane_direction is not None and not horiz_stuck:
            return lane_direction
        return deterministic_factory_sidestep(col, can_go, config, factory_lane_col)

    return "IDLE"


def step_toward(
    col,
    row,
    target_col,
    target_row,
    can_go,
    obs,
    config,
    rtype=1,
    planned_targets=None,
    intent="travel",
    hungry=False,
):
    ctx = build_path_ctx(obs, config, rtype, intent, planned_targets, hungry)
    action = dijkstra_first_step_to_target(
        col, row, target_col, target_row, obs, config, ctx
    )
    if action is not None:
        return action

    action = weighted_first_step_to_target(
        col, row, target_col, target_row, obs, config, planned_targets
    )
    if action is not None:
        return action

    action = bfs_first_step_to_target(col, row, target_col, target_row, obs, config)
    if action is not None:
        return action

    if target_row > row and can_go["NORTH"]:
        return "NORTH"
    if target_row < row and can_go["SOUTH"]:
        return "SOUTH"
    if target_col > col and can_go["EAST"]:
        return "EAST"
    if target_col < col and can_go["WEST"]:
        return "WEST"
    fallback = [d for d in DIR_ORDER if can_go[d]]
    if not fallback:
        return "IDLE"
    return deterministic_pick_dir(col, fallback, config, prefer_north=False) or "IDLE"


def target_cell(col, row, action):
    dcol, drow = DIRECTION_DELTAS.get(action, (0, 0))
    return f"{col + dcol},{row + drow}"


def deterministic_pick_dir(col, options, config, prefer_north=True):
    if not options:
        return None
    if prefer_north and "NORTH" in options:
        return "NORTH"
    mid = config.width // 2
    horiz = ("EAST", "WEST") if col < mid else ("WEST", "EAST")
    for d in horiz:
        if d in options:
            return d
    if not prefer_north and "NORTH" in options:
        return "NORTH"
    if "SOUTH" in options:
        return "SOUTH"
    return options[0]


def count_robots_by_type(my_robots):
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for data in my_robots.values():
        counts[data[0]] += 1
    return counts


def update_remembered_enemies(obs):
    for uid, data in obs.robots.items():
        if data[4] == obs.player:
            continue
        remembered_enemies[uid] = {
            "type": data[0],
            "col": data[1],
            "row": data[2],
            "energy": data[3],
            "step": obs.step,
        }
    for uid in list(remembered_enemies):
        row = remembered_enemies[uid]["row"]
        if row < obs.southBound:
            remembered_enemies.pop(uid, None)
            continue
        if obs.step - remembered_enemies[uid]["step"] > ENEMY_MEMORY_TURNS:
            remembered_enemies.pop(uid, None)


def enemy_unit_counts():
    return {
        t: sum(1 for e in remembered_enemies.values() if e["type"] == t)
        for t in (0, 1, 2, 3)
    }


def count_enemy_mines(obs):
    return sum(1 for _, mine in obs.mines.items() if mine[2] != obs.player)


def choose_factory_build(
    energy,
    config,
    counts,
    remembered_nodes,
    obs,
    enemy_count,
    enemy_mines,
    factory_col,
    factory_row,
):
    if obs.step > ENDGAME_NO_BUILD_STEP:
        return None

    scouts = counts[1]
    workers = counts[2]
    miners = counts[3]
    step = obs.step

    if scouts == 0 and energy >= config.scoutCost:
        if can_move_known(factory_col, factory_row, "NORTH", obs, config):
            return "BUILD_SCOUT"

    # One scout for vision, then prioritize worker before more scouts or miners.
    if scouts >= 1 and workers == 0:
        if energy >= config.workerCost:
            return "BUILD_WORKER"
        return None

    def scout_score():
        s = SCOUT_BASE_SCORE
        if scouts < 2:
            s += SCOUT_FEWER_THAN_TWO_BONUS
        if step < SCOUT_EARLY_STEP and not remembered_nodes:
            s += SCOUT_EARLY_BONUS
        if enemy_count[1] >= 3:
            s += SCOUT_ENEMY_PRESSURE_BONUS
        if scouts >= 2 and workers == 0:
            s -= SCOUT_NO_WORKER_PENALTY
        return s

    def worker_score():
        s = WORKER_BASE_SCORE
        if enemy_count[1] >= 2:
            s += WORKER_ENEMY_SCOUT_BONUS
        if workers == 0 and step > WORKER_FIRST_STEP:
            s += WORKER_FIRST_BONUS
        if enemy_count[2] >= 1 and workers < WORKER_CAP:
            s += WORKER_ENEMY_WORKER_BONUS
        if scouts >= 2 and workers == 0:
            s += WORKER_NO_WORKER_BONUS
        return s

    def miner_score():
        if not remembered_nodes:
            return -1
        s = MINER_BASE_SCORE
        if enemy_mines >= 1:
            s -= MINER_ENEMY_MINE_PENALTY
        if enemy_count[2] >= 1:
            s += MINER_ENEMY_WORKER_BONUS
        if miners == 0:
            s += MINER_FIRST_BONUS
        if miners == 0 and step >= MINER_LATE_FIRST_STEP:
            s += MINER_LATE_FIRST_BONUS
        return s

    candidates = []
    if energy >= config.scoutCost and scouts < SCOUT_CAP:
        candidates.append(("BUILD_SCOUT", scout_score()))
    if energy >= config.workerCost and workers < WORKER_CAP:
        candidates.append(("BUILD_WORKER", worker_score()))
    if energy >= config.minerCost and miners < MINER_CAP and remembered_nodes:
        ms = miner_score()
        if ms >= 0:
            candidates.append(("BUILD_MINER", ms))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def pick_move(col, row, can_go, planned_targets, config, prefer_north=True):
    options = [d for d in DIR_ORDER if can_go[d]]
    if not options:
        return "IDLE"

    safe = [d for d in options if target_cell(col, row, d) not in planned_targets]
    pool = safe if safe else options
    return deterministic_pick_dir(col, pool, config, prefer_north=prefer_north) or "IDLE"


def energy_is_low(rtype, energy, config):
    if rtype == 1:
        return energy < 40
    if rtype == 2:
        return energy < 100
    if rtype == 3:
        return energy < 150
    return False


def move_toward_goal(
    col,
    row,
    can_go,
    planned_targets,
    goal_col,
    goal_row,
    obs,
    config,
    prefer_north=True,
    rtype=1,
    intent="travel",
    hungry=False,
):
    action = step_toward(
        col,
        row,
        goal_col,
        goal_row,
        can_go,
        obs,
        config,
        rtype=rtype,
        planned_targets=planned_targets,
        intent=intent,
        hungry=hungry,
    )
    if action == "IDLE":
        return "IDLE"
    if target_cell(col, row, action) in planned_targets:
        return pick_move(col, row, can_go, planned_targets, config, prefer_north=prefer_north)
    return action


def update_remembered_crystals(obs):
    for pos_key, energy in obs.crystals.items():
        remembered_crystals[pos_key] = (energy, obs.step)

    for pos_key in list(remembered_crystals):
        _, crystal_row = parse_pos_key(pos_key)
        if crystal_row < obs.southBound:
            remembered_crystals.pop(pos_key, None)
            continue
        if obs.step - remembered_crystals[pos_key][1] > CRYSTAL_MEMORY_TURNS:
            remembered_crystals.pop(pos_key, None)
            continue
        if robots_at(*parse_pos_key(pos_key), obs, owner=obs.player):
            remembered_crystals.pop(pos_key, None)


def visible_crystal_targets(obs):
    targets = dict(obs.crystals)
    for pos_key, (energy, _) in remembered_crystals.items():
        if pos_key not in targets:
            targets[pos_key] = energy
    return targets


def score_crystal(
    col, row, rtype, crystal_key, crystal_energy, obs, hungry, distance=None
):
    target_col, target_row = parse_pos_key(crystal_key)
    if target_row < row:
        return None

    if distance is None:
        distance = manhattan(col, row, target_col, target_row)
    if distance == 0:
        return None

    value = crystal_energy - distance
    occupants = robots_at(target_col, target_row, obs)
    for _, occupant_type, owner in occupants:
        if owner == obs.player:
            if occupant_type == rtype:
                return None
            value -= 8
            continue

        outcome = crush_outcome(rtype, occupant_type)
        if outcome == "lose":
            return None
        if outcome == "both":
            return None
        if outcome == "win":
            value += 6

    if hungry:
        if value < 6:
            return None
        return value

    if distance <= 1 and crystal_energy >= 12:
        return value
    if target_row >= row and distance <= 2 and value >= 8:
        return value
    return None


def pick_best_crystal(
    col,
    row,
    rtype,
    energy,
    obs,
    planned_crystal_claims,
    hungry,
    distance_map=None,
):
    best_key = None
    best_score = None
    for crystal_key, crystal_energy in visible_crystal_targets(obs).items():
        if crystal_key in planned_crystal_claims:
            continue
        distance = None
        if distance_map is not None:
            target_col, target_row = parse_pos_key(crystal_key)
            distance = distance_map.get((target_col, target_row))
            if distance is None:
                continue
        score = score_crystal(
            col, row, rtype, crystal_key, crystal_energy, obs, hungry, distance=distance
        )
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_key = crystal_key
            best_score = score
    return best_key


def hungarian_maximize(utility_matrix):
    if not utility_matrix or not utility_matrix[0]:
        return []

    rows = len(utility_matrix)
    cols = len(utility_matrix[0])
    max_utility = max(max(row) for row in utility_matrix)
    costs = [
        [max_utility - utility_matrix[r][c] for c in range(cols)]
        for r in range(rows)
    ]

    u = [0] * (rows + 1)
    v = [0] * (cols + 1)
    p = [0] * (cols + 1)
    way = [0] * (cols + 1)

    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (cols + 1)
        used = [False] * (cols + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                cur = costs[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assignment = [-1] * rows
    for j in range(1, cols + 1):
        if p[j] != 0:
            assignment[p[j] - 1] = j - 1
    return assignment


def assign_crystals_to_units(my_robots, obs, config, crystal_targets):
    mobile_units = []
    for uid, data in my_robots.items():
        rtype = data[0]
        if rtype == 0:
            continue
        col, row, energy = data[1], data[2], data[3]
        if row - obs.southBound <= 4:
            continue
        pos_key = f"{col},{row}"
        if (
            pos_key in obs.mines
            and obs.mines[pos_key][2] == obs.player
            and energy_is_low(rtype, energy, config)
        ):
            continue
        mobile_units.append((uid, rtype, col, row, energy))

    crystal_keys = list(crystal_targets.keys())
    if not mobile_units or not crystal_keys:
        return {}

    # Give solver an explicit "no crystal" option per unit.
    total_cols = len(crystal_keys) + len(mobile_units)
    utility = [[0] * total_cols for _ in mobile_units]

    for unit_idx, (_, rtype, col, row, energy) in enumerate(mobile_units):
        hungry = energy_is_low(rtype, energy, config)
        ctx = build_path_ctx(obs, config, rtype, "refuel", set(), hungry)
        _, dist_edges, _ = dijkstra_spatial(col, row, obs, config, ctx, stop_at=None)
        distance_map = dist_edges
        for crystal_idx, crystal_key in enumerate(crystal_keys):
            target_col, target_row = parse_pos_key(crystal_key)
            distance = distance_map.get((target_col, target_row))
            if distance is None:
                utility[unit_idx][crystal_idx] = INVALID_UTILITY
                continue
            score = score_crystal(
                col,
                row,
                rtype,
                crystal_key,
                crystal_targets[crystal_key],
                obs,
                hungry,
                distance=distance,
            )
            if score is None:
                utility[unit_idx][crystal_idx] = INVALID_UTILITY
            else:
                utility[unit_idx][crystal_idx] = int(score * 10)

    assignment = hungarian_maximize(utility)
    assigned = {}
    for unit_idx, col_idx in enumerate(assignment):
        if col_idx < 0 or col_idx >= len(crystal_keys):
            continue
        if utility[unit_idx][col_idx] <= 0:
            continue
        assigned[mobile_units[unit_idx][0]] = crystal_keys[col_idx]
    return assigned


def find_my_factory(my_robots):
    for data in my_robots.values():
        if data[0] == 0:
            return data
    return None


def friendly_blocking_cells(my_robots, exclude_uid=None):
    """Positions of friendly units scouts must not enter (factory crush, stacking)."""
    cells = set()
    for uid, data in my_robots.items():
        if uid == exclude_uid:
            continue
        cells.add(f"{data[1]},{data[2]}")
    return cells


def scout_on_factory_spawn(col, row, my_robots):
    factory = find_my_factory(my_robots)
    if factory is None:
        return False
    return col == factory[1] and row == factory[2] + 1


def filter_scout_directions(col, row, directions, blocking_cells, planned_targets):
    safe = []
    for direction in directions:
        dest = target_cell(col, row, direction)
        if dest in blocking_cells or dest in planned_targets:
            continue
        safe.append(direction)
    return safe


def scout_safe_move_action(
    col, row, proposed, can_go, blocking_cells, planned_targets, config, prefer_north=True
):
    """Reject moves onto friendly cells; pick a legal alternative."""
    if proposed in (None, "IDLE") or not isinstance(proposed, str):
        return proposed or "IDLE"
    if proposed.startswith(("BUILD_", "TRANSFER_", "REMOVE_", "JUMP_", "TRANSFORM")):
        return proposed

    dest = target_cell(col, row, proposed)
    if dest not in blocking_cells and dest not in planned_targets:
        return proposed

    pool = filter_scout_directions(
        col, row, [d for d in DIR_ORDER if can_go[d]], blocking_cells, planned_targets
    )
    return deterministic_pick_dir(col, pool, config, prefer_north=prefer_north) or "IDLE"


def scout_spawn_action(col, row, can_go, blocking_cells, planned_targets, config):
    """First move from the factory spawn tile: never step south onto the factory."""
    dirs = [d for d in ("NORTH", "EAST", "WEST") if can_go[d]]
    pool = filter_scout_directions(col, row, dirs, blocking_cells, planned_targets)
    if "NORTH" in pool:
        return "NORTH"
    return deterministic_pick_dir(col, pool, config, prefer_north=True) or "IDLE"


def worker_return_to_factory_action(
    col, row, energy, config, my_robots, can_go, planned_targets, obs
):
    if energy < 120:
        return None
    factory = find_my_factory(my_robots)
    if factory is None:
        return None
    fcol, frow = factory[1], factory[2]
    if manhattan(col, row, fcol, frow) <= 1:
        return None
    return move_toward_goal(
        col,
        row,
        can_go,
        planned_targets,
        fcol,
        frow,
        obs,
        config,
        prefer_north=False,
        rtype=2,
        intent="travel",
        hungry=False,
    )


def pick_best_mining_node(
    col, row, obs, config, planned_mining_claims, distances=None
):
    if not remembered_mining_nodes:
        return None
    if distances is None:
        distances = bfs_distances(col, row, obs, config)
    best_key = None
    best_metric = None
    for key in remembered_mining_nodes:
        if key in planned_mining_claims:
            continue
        tc, tr = parse_pos_key(key)
        if not in_bounds(tc, tr, obs, config):
            continue
        dist = distances.get((tc, tr))
        if dist is None:
            continue
        if not is_row_scroll_safe(tr, obs, config, turns_ahead=dist, buffer_rows=1):
            continue
        # Prefer closer node; tie-break by more northern (larger row) since the
        # scroll line keeps creeping up and a northerly node lives longer.
        metric = (dist, -tr, tc)
        if best_metric is None or metric < best_metric:
            best_metric = metric
            best_key = key
    return best_key


def transfer_to_factory_action(col, row, rtype, energy, obs, my_robots, can_go):
    if rtype != 2 or energy < 120:
        return None

    factory = None
    for data in my_robots.values():
        if data[0] == 0:
            factory = data
            break
    if factory is None:
        return None

    fcol, frow = factory[1], factory[2]
    if manhattan(col, row, fcol, frow) != 1:
        return None

    direction = None
    if frow == row + 1:
        direction = "NORTH"
    elif frow == row - 1:
        direction = "SOUTH"
    elif fcol == col + 1:
        direction = "EAST"
    elif fcol == col - 1:
        direction = "WEST"

    if direction and can_go[direction]:
        return f"TRANSFER_{direction}"
    return None


def fuel_action(
    uid,
    col,
    row,
    rtype,
    energy,
    obs,
    config,
    can_go,
    planned_targets,
    planned_crystal_claims,
    assigned_crystals,
    my_robots,
):
    if rtype == 0:
        return None, None

    scroll_margin = row - obs.southBound
    if scroll_margin <= 4:
        return None, None

    pos_key = f"{col},{row}"
    if pos_key in obs.mines and obs.mines[pos_key][2] == obs.player:
        if energy_is_low(rtype, energy, config):
            return "IDLE", None
        return None, None

    transfer = transfer_to_factory_action(col, row, rtype, energy, obs, my_robots, can_go)
    if transfer is not None:
        return transfer, None

    hungry = energy_is_low(rtype, energy, config)
    path_ctx = build_path_ctx(obs, config, rtype, "refuel", planned_targets, hungry)
    _, dist_edges, _ = dijkstra_spatial(col, row, obs, config, path_ctx, stop_at=None)
    distance_map = dist_edges
    crystal_key = assigned_crystals.get(uid)
    if crystal_key is None or crystal_key in planned_crystal_claims:
        crystal_key = pick_best_crystal(
            col,
            row,
            rtype,
            energy,
            obs,
            planned_crystal_claims,
            hungry,
            distance_map=distance_map,
        )
    if crystal_key is not None:
        target_col, target_row = parse_pos_key(crystal_key)
        if (target_col, target_row) not in distance_map:
            crystal_key = None
    if crystal_key is not None:
        target_col, target_row = parse_pos_key(crystal_key)
        return (
            move_toward_goal(
                col,
                row,
                can_go,
                planned_targets,
                target_col,
                target_row,
                obs,
                config,
                prefer_north=False,
                rtype=rtype,
                intent="refuel",
                hungry=hungry,
            ),
            crystal_key,
        )

    if not hungry:
        return None, None

    friendly_mines = [key for key, data in obs.mines.items() if data[2] == obs.player]
    if friendly_mines:
        mine_key = closest_node(col, row, friendly_mines)
        target_col, target_row = parse_pos_key(mine_key)
        if target_row >= row:
            return (
                move_toward_goal(
                    col,
                    row,
                    can_go,
                    planned_targets,
                    target_col,
                    target_row,
                    obs,
                    config,
                    prefer_north=False,
                    rtype=rtype,
                    intent="refuel",
                    hungry=True,
                ),
                None,
            )

    return None, None


def scout_corridor_entry_fresh(col, step):
    entry = scout_corridors.get(col)
    if entry is None:
        return None
    if step - entry["step"] > SCOUT_CORRIDOR_MEMORY_TURNS:
        return None
    return entry


def scout_north_step_ok(col, row, can_go, blocking_cells, planned_targets, obs, config):
    if not can_go.get("NORTH"):
        return False
    dest = target_cell(col, row, "NORTH")
    if dest in blocking_cells or dest in planned_targets:
        return False
    _, nrow = next_cell(col, row, "NORTH")
    return is_row_scroll_safe(nrow, obs, config, turns_ahead=0, buffer_rows=1)


def scout_explore_scroll_panic(
    col, row, can_go, blocking_cells, planned_targets, obs, config
):
    """Near the scroll line: only north or idle (no sideways frontier detours)."""
    if scout_north_step_ok(col, row, can_go, blocking_cells, planned_targets, obs, config):
        return "NORTH"
    return "IDLE"


def scout_explore_corridor_march(
    col, row, can_go, blocking_cells, planned_targets, obs, config, step
):
    entry = scout_corridor_entry_fresh(col, step)
    if entry is None or entry["run"] < SCOUT_CORRIDOR_MIN_RUN:
        return None
    if scout_north_step_ok(col, row, can_go, blocking_cells, planned_targets, obs, config):
        return "NORTH"
    return None


def scout_explore_lane_steer(
    col, row, lane_col, can_go, blocking_cells, planned_targets, obs, config
):
    if lane_col is None or lane_col == col:
        return None
    direction = "EAST" if lane_col > col else "WEST"
    if direction not in can_go or not can_go[direction]:
        return None
    dest = target_cell(col, row, direction)
    if dest in blocking_cells or dest in planned_targets:
        return None
    _, nrow = next_cell(col, row, direction)
    if not is_row_scroll_safe(nrow, obs, config, turns_ahead=0, buffer_rows=1):
        return None
    return direction


def scout_explore_north_probe(
    col, row, can_go, blocking_cells, planned_targets, obs, config
):
    if north_run_length(col, row, obs, config) < SCOUT_NORTH_PROBE_MIN_RUN:
        return None
    if scout_north_step_ok(col, row, can_go, blocking_cells, planned_targets, obs, config):
        return "NORTH"
    return None


def scout_explore_action(
    col, row, can_go, planned_targets, uid, obs, config, blocking_cells
):
    options = filter_scout_directions(
        col, row, [d for d in DIR_ORDER if can_go[d]], blocking_cells, planned_targets
    )
    if not options:
        return "IDLE"

    if len(options) == 1:
        return options[0]

    if row - obs.southBound <= SCOUT_EXPLORE_SCROLL_MARGIN:
        return scout_explore_scroll_panic(
            col, row, can_go, blocking_cells, planned_targets, obs, config
        )

    march = scout_explore_corridor_march(
        col, row, can_go, blocking_cells, planned_targets, obs, config, obs.step
    )
    if march is not None:
        return march

    if scout_next_lane_col is not None:
        steer = scout_explore_lane_steer(
            col,
            row,
            scout_next_lane_col,
            can_go,
            blocking_cells,
            planned_targets,
            obs,
            config,
        )
        if steer is not None:
            return steer

    probe = scout_explore_north_probe(
        col, row, can_go, blocking_cells, planned_targets, obs, config
    )
    if probe is not None:
        return probe

    prev_key = scout_prev_cell.get(uid)
    frontier_action = weighted_first_step_to_frontier(
        col, row, can_go, obs, config, planned_targets, prev_key
    )
    if frontier_action is None:
        frontier_action = bfs_first_step_to_frontier(
            col, row, can_go, obs, config, planned_targets, prev_key
        )
    if frontier_action is not None:
        dest = target_cell(col, row, frontier_action)
        if dest not in planned_targets and dest not in blocking_cells:
            return frontier_action

    safe = [
        d
        for d in options
        if target_cell(col, row, d) != prev_key
    ]
    pool = safe if safe else options

    return deterministic_pick_dir(col, pool, config, prefer_north=True) or "IDLE"


def agent(obs, config):
    global last_seen_step, factory_lane_col, factory_prev_col, factory_last_horiz
    global factory_last_row, scout_corridors, scout_next_lane_col

    if obs.step <= last_seen_step:
        remembered_mining_nodes.clear()
        remembered_crystals.clear()
        remembered_enemies.clear()
        scout_prev_cell.clear()
        scout_corridors.clear()
        scout_next_lane_col = None
        factory_lane_col = None
        factory_prev_col = None
        factory_last_horiz = None
        factory_last_row = None
    last_seen_step = obs.step

    actions = {}
    width = config.width

    my_robots = {
        uid: data for uid, data in obs.robots.items() if data[4] == obs.player
    }

    remembered_mining_nodes.update(obs.miningNodes.keys())
    for pos_key in list(remembered_mining_nodes):
        _, node_row = parse_pos_key(pos_key)
        if pos_key in obs.mines or node_row < obs.southBound:
            remembered_mining_nodes.discard(pos_key)

    update_remembered_crystals(obs)
    update_remembered_enemies(obs)
    enemy_count = enemy_unit_counts()
    enemy_mines = count_enemy_mines(obs)

    occupied_by_me = set()
    for _, data in my_robots.items():
        occupied_by_me.add(f"{data[1]},{data[2]}")

    robot_counts = count_robots_by_type(my_robots)
    planned_targets = set()
    planned_crystal_claims = set()
    planned_mining_claims = set()
    crystal_targets = visible_crystal_targets(obs)
    assigned_crystals = assign_crystals_to_units(
        my_robots, obs, config, crystal_targets
    )

    factory_row = obs.southBound
    for data in my_robots.values():
        if data[0] == 0:
            factory_row = data[2]
            break
    update_scout_corridors(obs, config, my_robots, factory_row)

    ordered_uids = [
        uid for uid, data in my_robots.items() if data[0] != 0
    ] + [
        uid for uid, data in my_robots.items() if data[0] == 0
    ]

    for uid in ordered_uids:
        data = my_robots[uid]
        rtype = data[0]
        col = data[1]
        row = data[2]
        energy = data[3]
        move_cd = data[5]
        build_cd = data[7]
        crystal_key = None

        idx = (row - obs.southBound) * width + col
        walls = obs.walls
        w = walls[idx] if 0 <= idx < len(walls) and walls[idx] != -1 else 0

        can_go = {
            "NORTH": not (w & 1),
            "EAST": not (w & 2),
            "SOUTH": not (w & 4),
            "WEST": not (w & 8),
        }

        if rtype == 0:
            scroll_margin = row - obs.southBound
            critical_scroll = scroll_margin <= SCROLL_CRITICAL_MARGIN
            urgent_scroll = critical_scroll or not is_row_scroll_safe(
                row, obs, config, turns_ahead=FACTORY_SCROLL_PANIC_TURNS, buffer_rows=2
            )
            spawn_is_safe = factory_spawn_is_safe(
                col, row, obs, config, occupied_by_me, planned_targets, my_robots
            )
            if factory_lane_col is None or obs.step % LANE_REEVAL_INTERVAL == 0:
                lane_scores = compute_factory_lane_scores(col, row, obs, config)
                factory_lane_col = pick_factory_lane_with_hysteresis(
                    col, factory_lane_col, lane_scores
                )
            build_action = choose_factory_build(
                energy,
                config,
                robot_counts,
                remembered_mining_nodes,
                obs,
                enemy_count,
                enemy_mines,
                col,
                row,
            )
            can_build = (
                build_cd == 0
                and build_action is not None
                and spawn_is_safe
                and scroll_margin > 2
                and not urgent_scroll
            )
            must_build = can_build and (
                robot_counts[1] == 0
                or (
                    robot_counts[2] == 0
                    and robot_counts[1] >= 1
                    and obs.step >= WORKER_FIRST_STEP
                )
                or (
                    robot_counts[3] == 0
                    and robot_counts[1] >= 1
                    and remembered_mining_nodes
                )
            )
            lane_direction = None
            if factory_lane_col is not None and factory_lane_col != col:
                if factory_lane_col > col and can_go["EAST"]:
                    lane_direction = "EAST"
                elif factory_lane_col < col and can_go["WEST"]:
                    lane_direction = "WEST"
            if lane_direction is not None:
                lane_target_key = target_cell(col, row, lane_direction)
                if lane_target_key in planned_targets:
                    lane_direction = None
            can_lane_shift = (
                lane_direction is not None
                and not can_go["NORTH"]
                and not urgent_scroll
                and not critical_scroll
                and scroll_margin >= 8
                and is_row_scroll_safe(row, obs, config, turns_ahead=2, buffer_rows=3)
            )
            jump_cd = data[6]
            needs_worker = (
                robot_counts[2] == 0
                and robot_counts[1] >= 1
                and obs.step >= WORKER_FIRST_STEP
            )

            if move_cd > 0:
                actions[uid] = build_action if can_build else "IDLE"
            elif critical_scroll and jump_cd == 0 and factory_jump_north_is_safe(
                col,
                row,
                obs,
                config,
                occupied_by_me,
                planned_targets,
                True,
                can_go,
            ):
                actions[uid] = "JUMP_NORTH"
            elif urgent_scroll and can_go["NORTH"]:
                actions[uid] = "NORTH"
            elif must_build:
                actions[uid] = build_action
            elif can_build and needs_worker:
                actions[uid] = build_action
            elif (
                needs_worker
                and not can_go["NORTH"]
                and jump_cd == 0
                and factory_jump_north_is_safe(
                    col,
                    row,
                    obs,
                    config,
                    occupied_by_me,
                    planned_targets,
                    urgent_scroll,
                    can_go,
                )
            ):
                actions[uid] = "JUMP_NORTH"
            elif can_go["NORTH"]:
                actions[uid] = "NORTH"
            elif can_build:
                actions[uid] = build_action
            else:
                actions[uid] = choose_factory_blocked_move(
                    col,
                    row,
                    can_go,
                    obs,
                    config,
                    planned_targets,
                    occupied_by_me,
                    urgent_scroll,
                    jump_cd,
                    lane_direction,
                    can_lane_shift,
                    factory_lane_col,
                    scroll_margin,
                )

            if actions[uid] in ("EAST", "WEST"):
                factory_last_horiz = actions[uid]
                factory_prev_col = col
                factory_last_row = row
            elif actions[uid] != "IDLE":
                factory_last_horiz = None
                factory_prev_col = col
                factory_last_row = row

        elif rtype == 1:
            scout_blocking = friendly_blocking_cells(my_robots, exclude_uid=uid)
            if scout_on_factory_spawn(col, row, my_robots):
                actions[uid] = scout_spawn_action(
                    col, row, can_go, scout_blocking, planned_targets, config
                )
            else:
                fuel, crystal_key = fuel_action(
                    uid,
                    col,
                    row,
                    rtype,
                    energy,
                    obs,
                    config,
                    can_go,
                    planned_targets,
                    planned_crystal_claims,
                    assigned_crystals,
                    my_robots,
                )
                if fuel is not None:
                    actions[uid] = fuel
                else:
                    actions[uid] = scout_explore_action(
                        col,
                        row,
                        can_go,
                        planned_targets,
                        uid,
                        obs,
                        config,
                        scout_blocking,
                    )
            actions[uid] = scout_safe_move_action(
                col,
                row,
                actions[uid],
                can_go,
                scout_blocking,
                planned_targets,
                config,
                prefer_north=True,
            )
            scout_prev_cell[uid] = f"{col},{row}"

        elif rtype == 2:
            fuel, crystal_key = fuel_action(
                uid,
                col,
                row,
                rtype,
                energy,
                obs,
                config,
                can_go,
                planned_targets,
                planned_crystal_claims,
                assigned_crystals,
                my_robots,
            )
            if fuel is not None:
                actions[uid] = fuel
            else:
                deliver = worker_return_to_factory_action(
                    col,
                    row,
                    energy,
                    config,
                    my_robots,
                    can_go,
                    planned_targets,
                    obs,
                )
                if deliver is not None:
                    actions[uid] = deliver
                elif not can_go["NORTH"] and energy >= config.wallRemoveCost:
                    actions[uid] = "REMOVE_NORTH"
                else:
                    actions[uid] = pick_move(
                        col, row, can_go, planned_targets, config, prefer_north=True
                    )

        elif rtype == 3:
            pos_key = f"{col},{row}"
            if pos_key in obs.miningNodes and energy >= config.transformCost:
                actions[uid] = "TRANSFORM"
            else:
                fuel, crystal_key = fuel_action(
                    uid,
                    col,
                    row,
                    rtype,
                    energy,
                    obs,
                    config,
                    can_go,
                    planned_targets,
                    planned_crystal_claims,
                    assigned_crystals,
                    my_robots,
                )
                if fuel is not None:
                    actions[uid] = fuel
                elif remembered_mining_nodes:
                    target_key = pick_best_mining_node(
                        col, row, obs, config, planned_mining_claims
                    )
                    if target_key is None:
                        target_key = closest_node(col, row, remembered_mining_nodes)
                    planned_mining_claims.add(target_key)
                    target_col, target_row = parse_pos_key(target_key)
                    actions[uid] = step_toward(
                        col,
                        row,
                        target_col,
                        target_row,
                        can_go,
                        obs,
                        config,
                        rtype=rtype,
                        planned_targets=planned_targets,
                        intent="travel",
                        hungry=False,
                    )
                elif can_go["NORTH"]:
                    actions[uid] = "NORTH"
                else:
                    passable = [d for d in ["EAST", "WEST", "SOUTH"] if can_go[d]]
                    actions[uid] = (
                        deterministic_pick_dir(col, passable, config, prefer_north=False)
                        or "IDLE"
                    )

        planned_targets.add(target_cell(col, row, actions[uid]))
        if crystal_key is not None:
            planned_crystal_claims.add(crystal_key)

    return actions
