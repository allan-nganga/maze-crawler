"""Benchmark and replay utility for the crawler agent.

Usage:
  python3 benchmark.py                        # full sweep, seeds 1-10 vs random + greedy
  python3 benchmark.py --seeds 1 4 6          # restrict seed list
  python3 benchmark.py --opponents greedy     # run vs only one opponent
  python3 benchmark.py --replay 4             # full per-turn dump for seed 4 (vs random)
  python3 benchmark.py --replay 4 --replay-opponent greedy
  python3 benchmark.py --replay 4 --replay-every 10   # only every Nth step

Replay output is verbose; redirect to a file:
  python3 benchmark.py --replay 4 > replay-4.log
"""

import argparse
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


OPPONENTS = {
    "random": random_agent,
    "greedy": greedy_opponent,
}


def _env_config(seed):
    """Kaggle replays use ``seed``; local docs often use ``randomSeed``."""
    return {"seed": seed, "randomSeed": seed}


def run_match(seed, opponent, opponent_name):
    env = make("crawl", configuration=_env_config(seed), debug=False)
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


def _state_to_obj(state):
    """env.steps[i][p].observation is a dict; expose attribute-style access for the agent's internals."""

    class _NS:
        pass

    ns = _NS()
    for k, v in dict(state).items():
        setattr(ns, k, v)
    return ns


def replay_match(seed, opponent_name, every_n=1):
    """Run a match and print a per-turn dump of factory + robot state."""
    opponent = OPPONENTS[opponent_name]
    env = make("crawl", configuration=_env_config(seed), debug=False)
    env.run([agent, opponent])

    print(f"# replay seed={seed} opponent={opponent_name} steps={len(env.steps)}")
    print("# columns: step factory(col,row) energy mines scouts workers miners visible_crystals planned_action")

    for idx, step_states in enumerate(env.steps):
        if every_n > 1 and idx % every_n != 0 and idx != len(env.steps) - 1:
            continue
        my_state = step_states[0]
        obs = my_state.observation

        robots = obs.get("robots", {})
        scouts = workers = miners = 0
        factory = None
        my_player = obs.get("player", 0)
        for uid, data in robots.items():
            if data[4] != my_player:
                continue
            t = data[0]
            if t == 0:
                factory = data
            elif t == 1:
                scouts += 1
            elif t == 2:
                workers += 1
            elif t == 3:
                miners += 1

        my_mines = sum(1 for k, m in obs.get("mines", {}).items() if m[2] == my_player)
        crystal_count = len(obs.get("crystals", {}))
        if factory is None:
            print(f"step={idx} factory=GONE scouts={scouts} workers={workers} miners={miners} crystals={crystal_count} status={my_state.status}")
            continue

        fcol, frow, fenergy = factory[1], factory[2], factory[3]
        action = my_state.action if isinstance(my_state.action, dict) else {}
        factory_uid = next(
            (uid for uid, data in robots.items() if data[4] == my_player and data[0] == 0),
            None,
        )
        chosen = action.get(factory_uid) if factory_uid else None
        print(
            f"step={idx} factory=({fcol},{frow}) e={fenergy} mines={my_mines} "
            f"S={scouts} W={workers} M={miners} crystals={crystal_count} "
            f"factory_action={chosen} reward={my_state.reward}"
        )

    final = env.steps[-1]
    print(f"# final reward me={final[0].reward} opp={final[1].reward}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument(
        "--seeds",
        nargs="*",
        type=int,
        default=list(range(1, 11)),
        help="Seeds to run (default: 1-10)",
    )
    parser.add_argument(
        "--opponents",
        nargs="*",
        choices=sorted(OPPONENTS.keys()),
        default=sorted(OPPONENTS.keys()),
        help="Opponents to run against (default: all)",
    )
    parser.add_argument(
        "--replay",
        type=int,
        default=None,
        metavar="SEED",
        help="Run a single match for SEED and print per-turn replay output instead of summary",
    )
    parser.add_argument(
        "--replay-opponent",
        choices=sorted(OPPONENTS.keys()),
        default="random",
        help="Opponent to use in --replay mode (default: random)",
    )
    parser.add_argument(
        "--replay-every",
        type=int,
        default=1,
        metavar="N",
        help="In --replay mode, only print every Nth step (final step always shown)",
    )
    args = parser.parse_args()

    if args.replay is not None:
        replay_match(args.replay, args.replay_opponent, every_n=args.replay_every)
        return

    for opponent_name in args.opponents:
        opponent = OPPONENTS[opponent_name]
        results = [run_match(seed, opponent, opponent_name) for seed in args.seeds]
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
