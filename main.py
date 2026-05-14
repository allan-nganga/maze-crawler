from collections import deque
from random import choice

remembered_mining_nodes = set()
remembered_crystals = {}
remembered_enemies = {}
scout_prev_cell = {}
factory_lane_col = None
last_seen_step = -1

CRYSTAL_MEMORY_TURNS = 6
ENEMY_MEMORY_TURNS = 8
ENDGAME_NO_BUILD_STEP = 380
INVALID_UTILITY = -10**6
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

    if "NORTH" in options:
        return "NORTH"
    return choice(options) if options else None


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


def choose_factory_lane_col(factory_col, factory_row, obs, config):
    distances = bfs_distances(factory_col, factory_row, obs, config)
    if not distances:
        return factory_col

    lane_scores = {}
    center_col = config.width // 2
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
        best = lane_scores.get(col)
        if best is None or score > best:
            lane_scores[col] = score

    if not lane_scores:
        return factory_col
    return max(lane_scores.items(), key=lambda item: item[1])[0]


def step_toward(col, row, target_col, target_row, can_go, obs, config):
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
    return choice(fallback) if fallback else "IDLE"


def target_cell(col, row, action):
    dcol, drow = DIRECTION_DELTAS.get(action, (0, 0))
    return f"{col + dcol},{row + drow}"


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
    energy, config, counts, remembered_nodes, obs, enemy_count, enemy_mines
):
    if obs.step > ENDGAME_NO_BUILD_STEP:
        return None

    scouts = counts[1]
    workers = counts[2]
    miners = counts[3]
    step = obs.step

    if scouts == 0 and energy >= config.scoutCost:
        return "BUILD_SCOUT"

    def scout_score():
        s = 5
        if scouts < 2:
            s += 8
        if step < 60 and not remembered_nodes:
            s += 4
        if enemy_count[1] >= 3:
            s += 3
        if scouts >= 2 and workers == 0:
            s -= 6
        return s

    def worker_score():
        s = 3
        if enemy_count[1] >= 2:
            s += 6
        if workers == 0 and step > 25:
            s += 4
        if enemy_count[2] >= 1 and workers < 2:
            s += 3
        if scouts >= 2 and workers == 0:
            s += 10
        return s

    def miner_score():
        if not remembered_nodes:
            return -1
        s = 2
        s += 5
        if enemy_mines >= 1:
            s -= 3
        if enemy_count[2] >= 1:
            s += 2
        if miners == 0:
            s += 4
        return s

    candidates = []
    if energy >= config.scoutCost and scouts < 4:
        candidates.append(("BUILD_SCOUT", scout_score()))
    if energy >= config.workerCost and workers < 2:
        candidates.append(("BUILD_WORKER", worker_score()))
    if energy >= config.minerCost and miners < 2 and remembered_nodes:
        ms = miner_score()
        if ms >= 0:
            candidates.append(("BUILD_MINER", ms))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


def pick_move(col, row, can_go, planned_targets, prefer_north=True):
    options = [d for d in DIR_ORDER if can_go[d]]
    if not options:
        return "IDLE"

    safe = [d for d in options if target_cell(col, row, d) not in planned_targets]
    pool = safe if safe else options
    if prefer_north and "NORTH" in pool:
        return "NORTH"
    return choice(pool)


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
):
    action = step_toward(col, row, goal_col, goal_row, can_go, obs, config)
    if action == "IDLE":
        return "IDLE"
    if target_cell(col, row, action) in planned_targets:
        return pick_move(col, row, can_go, planned_targets, prefer_north=prefer_north)
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
        distance_map = bfs_distances(col, row, obs, config)
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
    distance_map = bfs_distances(col, row, obs, config)
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
                ),
                None,
            )

    return None, None


def scout_explore_action(col, row, can_go, planned_targets, uid, obs, config):
    options = [d for d in DIR_ORDER if can_go[d]]
    if not options:
        return "IDLE"

    if len(options) == 1:
        return options[0]

    prev_key = scout_prev_cell.get(uid)
    frontier_action = bfs_first_step_to_frontier(
        col, row, can_go, obs, config, planned_targets, prev_key
    )
    if (
        frontier_action is not None
        and target_cell(col, row, frontier_action) not in planned_targets
    ):
        return frontier_action

    safe = [
        d
        for d in options
        if target_cell(col, row, d) not in planned_targets
        and target_cell(col, row, d) != prev_key
    ]
    pool = safe if safe else [d for d in options if target_cell(col, row, d) not in planned_targets]
    if not pool:
        pool = options

    if "NORTH" in pool:
        return "NORTH"
    return choice(pool)


def agent(obs, config):
    global last_seen_step, factory_lane_col

    if obs.step <= last_seen_step:
        remembered_mining_nodes.clear()
        remembered_crystals.clear()
        remembered_enemies.clear()
        scout_prev_cell.clear()
        factory_lane_col = None
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
    crystal_targets = visible_crystal_targets(obs)
    assigned_crystals = assign_crystals_to_units(
        my_robots, obs, config, crystal_targets
    )

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
            spawn_key = f"{col},{row + 1}"
            spawn_is_safe = spawn_key not in occupied_by_me and spawn_key not in planned_targets
            scroll_margin = row - obs.southBound
            urgent_scroll = not is_row_scroll_safe(
                row, obs, config, turns_ahead=4, buffer_rows=2
            )
            if factory_lane_col is None or obs.step % 8 == 0:
                factory_lane_col = choose_factory_lane_col(col, row, obs, config)
            build_action = choose_factory_build(
                energy,
                config,
                robot_counts,
                remembered_mining_nodes,
                obs,
                enemy_count,
                enemy_mines,
            )
            can_build = (
                build_cd == 0
                and build_action is not None
                and spawn_is_safe
                and scroll_margin > 2
                and not urgent_scroll
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
                and not urgent_scroll
                and scroll_margin >= 8
                and is_row_scroll_safe(row, obs, config, turns_ahead=2, buffer_rows=3)
            )

            if move_cd > 0:
                actions[uid] = build_action if can_build else "IDLE"
            elif urgent_scroll and can_go["NORTH"]:
                actions[uid] = "NORTH"
            elif not can_go["NORTH"]:
                jump_cd = data[6]
                if jump_cd == 0:
                    actions[uid] = "JUMP_NORTH"
                elif can_lane_shift:
                    actions[uid] = lane_direction
                else:
                    side = [d for d in ["EAST", "WEST"] if can_go[d]]
                    actions[uid] = choice(side) if side else "IDLE"
            else:
                if can_lane_shift:
                    actions[uid] = lane_direction
                else:
                    actions[uid] = build_action if can_build else "NORTH"

        elif rtype == 1:
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
                    col, row, can_go, planned_targets, uid, obs, config
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
            elif not can_go["NORTH"] and energy >= config.wallRemoveCost:
                actions[uid] = "REMOVE_NORTH"
            else:
                actions[uid] = pick_move(col, row, can_go, planned_targets, prefer_north=True)

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
                    target_key = closest_node(col, row, remembered_mining_nodes)
                    target_col, target_row = parse_pos_key(target_key)
                    actions[uid] = step_toward(
                        col, row, target_col, target_row, can_go, obs, config
                    )
                elif can_go["NORTH"]:
                    actions[uid] = "NORTH"
                else:
                    passable = [d for d in ["EAST", "WEST", "SOUTH"] if can_go[d]]
                    actions[uid] = choice(passable) if passable else "IDLE"

        planned_targets.add(target_cell(col, row, actions[uid]))
        if crystal_key is not None:
            planned_crystal_claims.add(crystal_key)

    return actions
