from random import choice

remembered_mining_nodes = set()
last_seen_step = -1


def parse_pos_key(pos_key):
    col_str, row_str = pos_key.split(",")
    return int(col_str), int(row_str)


def closest_node(col, row, node_keys):
    return min(node_keys, key=lambda k: abs(parse_pos_key(k)[0] - col) + abs(parse_pos_key(k)[1] - row))


def step_toward(col, row, target_col, target_row, can_go):
    if target_row > row and can_go["NORTH"]:
        return "NORTH"
    if target_row < row and can_go["SOUTH"]:
        return "SOUTH"
    if target_col > col and can_go["EAST"]:
        return "EAST"
    if target_col < col and can_go["WEST"]:
        return "WEST"
    fallback = [d for d in ["NORTH", "EAST", "WEST", "SOUTH"] if can_go[d]]
    return choice(fallback) if fallback else "IDLE"


def target_cell(col, row, action):
    deltas = {
        "NORTH": (0, 1),
        "SOUTH": (0, -1),
        "EAST": (1, 0),
        "WEST": (-1, 0),
    }
    dcol, drow = deltas.get(action, (0, 0))
    return f"{col + dcol},{row + drow}"


def count_robots_by_type(my_robots):
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    for data in my_robots.values():
        counts[data[0]] += 1
    return counts


def choose_factory_build(energy, config, counts, remembered_nodes):
    scouts = counts[1]
    workers = counts[2]
    miners = counts[3]

    if scouts < 2 and energy >= config.scoutCost:
        return "BUILD_SCOUT"
    if miners < 1 and remembered_nodes and energy >= config.minerCost:
        return "BUILD_MINER"
    if workers < 1 and energy >= config.workerCost:
        return "BUILD_WORKER"
    if scouts < 4 and energy >= config.scoutCost:
        return "BUILD_SCOUT"
    if miners < 2 and remembered_nodes and energy >= config.minerCost:
        return "BUILD_MINER"
    return None


def pick_move(col, row, can_go, planned_targets, prefer_north=True):
    options = [d for d in ["NORTH", "EAST", "WEST", "SOUTH"] if can_go[d]]
    if not options:
        return "IDLE"

    safe = [d for d in options if target_cell(col, row, d) not in planned_targets]
    pool = safe if safe else options
    if prefer_north and "NORTH" in pool:
        return "NORTH"
    return choice(pool)


def agent(obs, config):
    global last_seen_step

    if obs.step <= last_seen_step:
        remembered_mining_nodes.clear()
    last_seen_step = obs.step

    actions = {}
    width = config.width

    # Separate your robots from the enemy's
    my_robots = {
        uid: data for uid, data in obs.robots.items()
        if data[4] == obs.player
    }

    # Mining nodes are only visible in fog range, so remember seen ones.
    remembered_mining_nodes.update(obs.miningNodes.keys())
    for pos_key in list(remembered_mining_nodes):
        _, node_row = parse_pos_key(pos_key)
        if pos_key in obs.mines or node_row < obs.southBound:
            remembered_mining_nodes.discard(pos_key)

    occupied_by_me = set()
    for _, data in my_robots.items():
        occupied_by_me.add(f"{data[1]},{data[2]}")

    robot_counts = count_robots_by_type(my_robots)
    planned_targets = set()

    # Resolve mobile units first so factory spawn safety can consider planned moves.
    ordered_uids = [
        uid for uid, data in my_robots.items() if data[0] != 0
    ] + [
        uid for uid, data in my_robots.items() if data[0] == 0
    ]

    for uid in ordered_uids:
        data = my_robots[uid]
        rtype = data[0]  # 0=Factory, 1=Scout, 2=Worker, 3=Miner
        col = data[1]
        row = data[2]
        energy = data[3]
        move_cd = data[5]
        build_cd = data[7]

        # Look up this robot's wall bitfield
        # walls index is relative to southBound, not absolute row
        idx = (row - obs.southBound) * width + col
        walls = obs.walls
        w = walls[idx] if 0 <= idx < len(walls) and walls[idx] != -1 else 0

        # Helper: check if a direction is passable (no wall blocking it)
        # Wall bits: N=1, E=2, S=4, W=8
        can_go = {
            "NORTH": not (w & 1),
            "EAST": not (w & 2),
            "SOUTH": not (w & 4),
            "WEST": not (w & 8),
        }

        # --- FACTORY (type 0) ---
        if rtype == 0:
            spawn_key = f"{col},{row + 1}"
            spawn_is_safe = spawn_key not in occupied_by_me and spawn_key not in planned_targets
            scroll_margin = row - obs.southBound
            build_action = choose_factory_build(
                energy, config, robot_counts, remembered_mining_nodes
            )
            can_build = (
                build_cd == 0
                and build_action is not None
                and spawn_is_safe
                and scroll_margin > 2
            )

            if move_cd > 0:
                # Factory is on cooldown, can still issue build actions
                actions[uid] = build_action if can_build else "IDLE"
            elif scroll_margin <= 2 and can_go["NORTH"]:
                actions[uid] = "NORTH"
            elif not can_go["NORTH"]:
                # Wall blocking north — jump over it if cooldown is ready
                jump_cd = data[6]
                if jump_cd == 0:
                    actions[uid] = "JUMP_NORTH"
                else:
                    side = [d for d in ["EAST", "WEST"] if can_go[d]]
                    actions[uid] = choice(side) if side else "IDLE"
            else:
                actions[uid] = build_action if can_build else "NORTH"

        # --- SCOUT (type 1) ---
        # Scouts are fast (move every turn). Priority: go NORTH, explore sides if blocked
        elif rtype == 1:
            actions[uid] = pick_move(col, row, can_go, planned_targets, prefer_north=True)

        # --- WORKER (type 2) ---
        # Workers: if north is blocked, remove the wall. Otherwise move north.
        elif rtype == 2:
            if not can_go["NORTH"] and energy >= config.wallRemoveCost:
                actions[uid] = "REMOVE_NORTH"
            else:
                actions[uid] = pick_move(col, row, can_go, planned_targets, prefer_north=True)

        # --- MINER (type 3) ---
        # Miners: transform if on a mining node, else head toward nearest remembered node.
        elif rtype == 3:
            pos_key = f"{col},{row}"
            if pos_key in obs.miningNodes and energy >= config.transformCost:
                actions[uid] = "TRANSFORM"
            elif remembered_mining_nodes:
                target_key = closest_node(col, row, remembered_mining_nodes)
                target_col, target_row = parse_pos_key(target_key)
                actions[uid] = step_toward(col, row, target_col, target_row, can_go)
            elif can_go["NORTH"]:
                actions[uid] = "NORTH"
            else:
                passable = [d for d in ["EAST", "WEST", "SOUTH"] if can_go[d]]
                actions[uid] = choice(passable) if passable else "IDLE"

        planned_targets.add(target_cell(col, row, actions[uid]))

    return actions