"""Regression checks for known failure seeds (Kaggle replay-derived)."""

from kaggle_environments import make

from main import agent

# Episode 77235446: scout walked SOUTH onto factory; factory died on scroll.
KAGGLE_SCROLL_SCOUT_SEED = 902976396
# vs top agents (77365033 / 77358303): factory NORTH onto scout spawn at step 4.
KAGGLE_FACTORY_CRUSH_SEEDS = (1284102349, 554136581)


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


if __name__ == "__main__":
    test_kaggle_seed_first_scout_not_south_on_factory()
    print("scout spawn: OK")
    test_top_agent_loss_seeds_no_factory_north_crush()
    print(f"factory north crush ({KAGGLE_FACTORY_CRUSH_SEEDS}): OK")
    test_kaggle_seed_survives_longer_than_baseline()
    print(f"kaggle seed {KAGGLE_SCROLL_SCOUT_SEED}: my={run(KAGGLE_SCROLL_SCOUT_SEED)[0]} OK")
