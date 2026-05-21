"""Regression checks for known failure seeds (Kaggle replay-derived)."""

from kaggle_environments import make

from main import agent

# Episode 77235446: first scout walked SOUTH onto factory; factory died on scroll.
KAGGLE_SCROLL_SCOUT_SEED = 902976396


def run(seed, opponent="random"):
    env = make("crawl", configuration={"seed": seed, "randomSeed": seed}, debug=False)
    env.run([agent, opponent])
    final = env.steps[-1]
    return final[0].reward, final[1].reward, final[0].status


def test_kaggle_seed_survives_longer_than_baseline():
    my_r, opp_r, status = run(KAGGLE_SCROLL_SCOUT_SEED)
    assert status != "DONE" or my_r > 0, f"factory eliminated too early: reward={my_r}"
    # Pre-patch run ended ~790 with scroll death at step 455; expect materially better.
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
    steps = env.steps
    for idx in range(1, min(10, len(steps))):
        st = steps[idx][0]
        act = st.action if isinstance(st.action, dict) else {}
        obs = st.observation
        for uid, data in obs.get("robots", {}).items():
            if data[4] != obs.get("player", 0) or data[0] != 1:
                continue
            if uid in act and act[uid] == "SOUTH":
                factory = next(
                    (d for d in obs["robots"].values() if d[4] == data[4] and d[0] == 0),
                    None,
                )
                if factory and data[1] == factory[1] and data[2] == factory[2] + 1:
                    raise AssertionError(
                        f"step {idx}: scout {uid} on spawn tile must not move SOUTH onto factory"
                    )


if __name__ == "__main__":
    test_kaggle_seed_first_scout_not_south_on_factory()
    print("scout spawn: OK")
    test_kaggle_seed_survives_longer_than_baseline()
    print(f"kaggle seed {KAGGLE_SCROLL_SCOUT_SEED}: my={run(KAGGLE_SCROLL_SCOUT_SEED)[0]} OK")
