"""Regression checks for known failure seeds (Kaggle replay-derived)."""

from kaggle_environments import make

import main
from main import agent

# Episode 77235446: scout walked SOUTH onto factory; factory died on scroll.
KAGGLE_SCROLL_SCOUT_SEED = 902976396
# vs top agents (77365033 / 77358303): factory NORTH onto scout spawn at step 4.
KAGGLE_FACTORY_CRUSH_SEEDS = (1284102349, 554136581)
# Economy: worker/miner when the factory survives long enough vs random.
KAGGLE_ECONOMY_SEEDS = (9, 1284102349)
MINER_BY_STEP_LIMIT = 100
WORKER_BY_STEP_LIMIT = 45


def run(seed, opponent="random"):
    env = make("crawl", configuration={"seed": seed, "randomSeed": seed}, debug=False)
    env.run([agent, opponent])
    final = env.steps[-1]
    return final[0].reward, final[1].reward, final[0].status


def _first_player_obs_act(steps, idx):
    st = steps[idx][0]
    obs = st.observation
    act = st.action if isinstance(st.action, dict) else {}
    return obs, act


def test_factory_not_north_onto_scout(seed, max_step=20):
    env = make(
        "crawl",
        configuration={"seed": seed, "randomSeed": seed},
        debug=False,
    )
    while not env.done and len(env.steps) < max_step + 1:
        env.step([agent, "random"])

    for idx in range(1, min(max_step, len(env.steps))):
        obs, act = _first_player_obs_act(env.steps, idx)
        my_player = obs.get("player", 0)
        factory = None
        for uid, data in obs.get("robots", {}).items():
            if data[4] == my_player and data[0] == 0:
                factory = (uid, data)
                break
        if factory is None:
            continue
        f_uid, f_data = factory
        if act.get(f_uid) != "NORTH":
            continue
        fcol, frow = f_data[1], f_data[2]
        for uid, data in obs.get("robots", {}).items():
            if data[4] != my_player or data[0] != 1:
                continue
            if data[1] == fcol and data[2] == frow + 1:
                raise AssertionError(
                    f"seed {seed} step {idx}: factory NORTH with scout on spawn ({fcol},{frow + 1})"
                )


def test_kaggle_seed_survives_longer_than_baseline():
    my_r, opp_r, status = run(KAGGLE_SCROLL_SCOUT_SEED)
    assert status != "DONE" or my_r > 0, f"factory eliminated too early: reward={my_r}"
    assert my_r > 790, f"expected improvement over replay loss (~790), got {my_r}"


def test_kaggle_seed_first_scout_not_south_on_factory():
    env = make(
        "crawl",
        configuration={"seed": KAGGLE_SCROLL_SCOUT_SEED, "randomSeed": KAGGLE_SCROLL_SCOUT_SEED},
        debug=False,
    )
    for _ in range(8):
        if env.done:
            break
        env.step([agent, "random"])
    for idx in range(1, min(10, len(env.steps))):
        obs, act = _first_player_obs_act(env.steps, idx)
        my_player = obs.get("player", 0)
        for uid, data in obs.get("robots", {}).items():
            if data[4] != my_player or data[0] != 1:
                continue
            if act.get(uid) == "SOUTH":
                factory = next(
                    (d for d in obs["robots"].values() if d[4] == my_player and d[0] == 0),
                    None,
                )
                if factory and data[1] == factory[1] and data[2] == factory[2] + 1:
                    raise AssertionError(
                        f"step {idx}: scout {uid} on spawn tile must not move SOUTH onto factory"
                    )


def test_top_agent_loss_seeds_no_factory_north_crush():
    for seed in KAGGLE_FACTORY_CRUSH_SEEDS:
        test_factory_not_north_onto_scout(seed)


def test_enemy_factory_ahead_blocks_factory_north_and_jump():
    """Same-column enemy factory directly ahead must block NORTH/JUMP_NORTH."""
    main.remembered_enemies.clear()
    main.remembered_enemies["enemy-fac"] = {
        "type": 0,
        "col": 5,
        "row": 7,
        "energy": 1000,
        "step": 50,
    }
    can_go = {"NORTH": True, "EAST": True, "SOUTH": True, "WEST": True}
    allowed = main.factory_may_go_north(5, 5, can_go, {}, set())
    assert not allowed, "factory NORTH should be blocked by enemy factory at row+2"

    class _Obs:
        southBound = 0
        northBound = 20
        walls = [0] * (21 * 20)
        crystals = {}

    class _Cfg:
        width = 20

    jump_safe = main.factory_jump_north_is_safe(
        5, 5, _Obs(), _Cfg(), {}, set(), False, can_go
    )
    assert not jump_safe, "JUMP_NORTH should be blocked by enemy factory threat ahead"
    main.remembered_enemies.clear()


def _factory_alive_at(steps, my_player, step_idx):
    idx = min(step_idx, len(steps) - 1)
    obs = steps[idx][0].observation
    return any(
        data[4] == my_player and data[0] == 0
        for data in obs.get("robots", {}).values()
    )


def _peak_unit_count(steps, my_player, rtype, max_step):
    peak = 0
    for idx in range(1, min(max_step + 1, len(steps))):
        obs = steps[idx][0].observation
        n = sum(
            1
            for data in obs.get("robots", {}).values()
            if data[4] == my_player and data[0] == rtype
        )
        peak = max(peak, n)
    return peak


def test_replay_seeds_build_worker_and_miner():
    """Economy checks only on sufficiently long games."""
    for seed in KAGGLE_ECONOMY_SEEDS:
        env = make(
            "crawl",
            configuration={"seed": seed, "randomSeed": seed},
            debug=False,
        )
        env.run([agent, "random"])
        my_player = env.steps[-1][0].observation.get("player", 0)
        steps_played = len(env.steps) - 1
        if steps_played < 60:
            # Short games often end before economy ramps.
            continue
        worker_through = min(WORKER_BY_STEP_LIMIT, steps_played)
        if _factory_alive_at(env.steps, my_player, worker_through):
            workers_seen = _peak_unit_count(
                env.steps, my_player, 2, worker_through
            )
            assert workers_seen >= 1, (
                f"seed {seed}: factory alive at step {worker_through}; "
                f"expected a worker, peak workers={workers_seen}"
            )
        if steps_played < 80:
            continue
        miner_through = min(MINER_BY_STEP_LIMIT, steps_played)
        miners_seen = _peak_unit_count(
            env.steps, my_player, 3, miner_through
        )
        assert miners_seen >= 1, (
            f"seed {seed}: game lasted {steps_played} steps; expected a miner by "
            f"step {miner_through}, peak miners={miners_seen}"
        )


if __name__ == "__main__":
    test_kaggle_seed_first_scout_not_south_on_factory()
    print("scout spawn: OK")
    test_top_agent_loss_seeds_no_factory_north_crush()
    print(f"factory north crush ({KAGGLE_FACTORY_CRUSH_SEEDS}): OK")
    test_enemy_factory_ahead_blocks_factory_north_and_jump()
    print("enemy factory north/jump block: OK")
    test_replay_seeds_build_worker_and_miner()
    print(f"worker/miner economy ({KAGGLE_ECONOMY_SEEDS}): OK")
    test_kaggle_seed_survives_longer_than_baseline()
    print(f"kaggle seed {KAGGLE_SCROLL_SCOUT_SEED}: my={run(KAGGLE_SCROLL_SCOUT_SEED)[0]} OK")
