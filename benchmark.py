from random import choice

from kaggle_environments import make
from kaggle_environments.envs.crawl.agents import random_agent

from main import agent


def greedy_opponent(obs, config):
    """Stronger than the built-in random bot: worker, jumps, and steady north movement."""
    actions = {}
    my_robots = {}

    for uid, data in obs.robots.items():
        if data[4] != obs.player:
            continue
        my_robots[uid] = {
            "type": data[0],
            "col": data[1],
            "row": data[2],
            "energy": data[3],
            "build_cd": data[7] if len(data) > 7 else 0,
        }

    width = config.width
    for uid, robot in my_robots.items():
        col = robot["col"]
        row = robot["row"]
        idx = (row - obs.southBound) * width + col
        w = 0
        if 0 <= idx < len(obs.walls) and obs.walls[idx] != -1:
            w = obs.walls[idx]

        can_go = {
            "NORTH": not (w & 1),
            "EAST": not (w & 2),
            "SOUTH": not (w & 4),
            "WEST": not (w & 8),
        }

        if robot["type"] == 0:
            scouts = sum(1 for r in my_robots.values() if r["type"] == 1)
            workers = sum(1 for r in my_robots.values() if r["type"] == 2)
            if not can_go["NORTH"]:
                actions[uid] = "JUMP_NORTH"
            elif scouts < 2 and robot["energy"] >= config.scoutCost and robot["build_cd"] == 0:
                actions[uid] = "BUILD_SCOUT"
            elif workers < 1 and robot["energy"] >= config.workerCost and robot["build_cd"] == 0:
                actions[uid] = "BUILD_WORKER"
            else:
                actions[uid] = "NORTH"
        elif robot["type"] == 2:
            if not can_go["NORTH"] and robot["energy"] >= config.wallRemoveCost:
                actions[uid] = "REMOVE_NORTH"
            else:
                actions[uid] = "NORTH" if can_go["NORTH"] else "IDLE"
        else:
            passable = [d for d in ["NORTH", "EAST", "WEST", "SOUTH"] if can_go[d]]
            actions[uid] = "NORTH" if "NORTH" in passable else (choice(passable) if passable else "IDLE")

    return actions


def run_match(seed, opponent, opponent_name):
    env = make("crawl", configuration={"randomSeed": seed}, debug=False)
    env.run([agent, opponent])
    final = env.steps[-1]
    my_reward = final[0].reward
    opp_reward = final[1].reward
    if my_reward > opp_reward:
        outcome = "W"
    elif my_reward == opp_reward:
        outcome = "D"
    else:
        outcome = "L"
    return {
        "seed": seed,
        "opponent": opponent_name,
        "my_reward": my_reward,
        "opp_reward": opp_reward,
        "outcome": outcome,
    }


def summarize(results):
    wins = sum(1 for r in results if r["outcome"] == "W")
    draws = sum(1 for r in results if r["outcome"] == "D")
    losses = sum(1 for r in results if r["outcome"] == "L")
    avg_reward = sum(r["my_reward"] for r in results) / len(results)
    return wins, draws, losses, avg_reward


def main():
    seeds = list(range(1, 11))
    opponents = [
        ("random", random_agent),
        ("greedy", greedy_opponent),
    ]

    for opponent_name, opponent in opponents:
        results = [run_match(seed, opponent, opponent_name) for seed in seeds]
        wins, draws, losses, avg_reward = summarize(results)
        print(
            f"{opponent_name}: {wins}W/{draws}D/{losses}L "
            f"avg_reward={avg_reward:.1f}"
        )
        for row in results:
            print(
                f"  seed={row['seed']} my={row['my_reward']} "
                f"opp={row['opp_reward']} {row['outcome']}"
            )


if __name__ == "__main__":
    main()
