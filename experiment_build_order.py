"""Sweep choose_factory_build env-driven constants vs benchmark.py.

Each trial spawns a fresh `python3 benchmark.py` so that ``main`` reloads with
the override applied. We compare an experiment's random/greedy summary lines
against the **baseline** (no override) and report deltas.

Built-in presets:
  --preset scout-cap       sweep CRAWL_SCOUT_CAP in {2, 3, 4}
  --preset early-worker    sweep CRAWL_WORKER_FIRST_STEP in {0, 25, 60}
  --preset late-miner      sweep CRAWL_MINER_LATE_FIRST_BONUS in {0, 3, 6} at step 90
  --preset all             run all presets

Custom override:
  python3 experiment_build_order.py --custom CRAWL_SCOUT_CAP=2 CRAWL_WORKER_NO_WORKER_BONUS=14

Usage:
  python3 experiment_build_order.py --preset all
  python3 experiment_build_order.py --preset scout-cap --repeat 2
  python3 experiment_build_order.py --custom CRAWL_SCOUT_CAP=3 CRAWL_WORKER_NO_WORKER_BONUS=14
"""

import argparse
import os
import re
import statistics
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


REPO_DIR = os.path.dirname(os.path.abspath(__file__))


def run_benchmark(env_overrides: Dict[str, str], extra_args: Optional[List[str]] = None) -> str:
    env = os.environ.copy()
    for k, v in env_overrides.items():
        env[k] = str(v)
    cmd = [sys.executable, "benchmark.py"]
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "") + (proc.stderr or "")


SUMMARY_RE = re.compile(r"^(random|greedy): (\d+)W/(\d+)D/(\d+)L avg_reward=(-?\d+(?:\.\d+)?)")


def parse_summary(text: str) -> Dict[str, Tuple[int, int, int, float]]:
    out: Dict[str, Tuple[int, int, int, float]] = {}
    for line in text.splitlines():
        m = SUMMARY_RE.match(line)
        if m:
            opp, w, d, l, avg = m.groups()
            out[opp] = (int(w), int(d), int(l), float(avg))
    return out


def aggregate_trials(trials: List[Dict[str, Tuple[int, int, int, float]]]) -> Dict[str, Dict[str, float]]:
    agg: Dict[str, Dict[str, float]] = {}
    by_opp: Dict[str, List[Tuple[int, int, int, float]]] = {}
    for t in trials:
        for opp, val in t.items():
            by_opp.setdefault(opp, []).append(val)
    for opp, values in by_opp.items():
        wins = [v[0] for v in values]
        avgs = [v[3] for v in values]
        agg[opp] = {
            "wins_mean": statistics.mean(wins),
            "avg_mean": statistics.mean(avgs),
            "avg_min": min(avgs),
            "avg_max": max(avgs),
            "avg_stdev": statistics.stdev(avgs) if len(avgs) > 1 else 0.0,
        }
    return agg


def run_experiment(
    name: str,
    overrides: Dict[str, str],
    repeat: int,
    bench_args: Optional[List[str]] = None,
) -> Dict[str, Dict[str, float]]:
    print(f"--- {name} {overrides} ---", flush=True)
    trials = []
    for t in range(repeat):
        raw = run_benchmark(overrides, bench_args)
        summary = parse_summary(raw)
        if not summary:
            print(f"  trial {t + 1}: no summary parsed; raw tail:\n{raw[-400:]}")
            continue
        trials.append(summary)
        bits = " | ".join(
            f"{opp}: {w}W/{d}D/{l}L avg={avg:.1f}"
            for opp, (w, d, l, avg) in sorted(summary.items())
        )
        print(f"  trial {t + 1}/{repeat}: {bits}")
    return aggregate_trials(trials)


def diff_vs_baseline(
    label: str,
    baseline: Dict[str, Dict[str, float]],
    experiment: Dict[str, Dict[str, float]],
) -> str:
    parts = [f"{label}:"]
    for opp in sorted(set(baseline) | set(experiment)):
        b = baseline.get(opp, {})
        e = experiment.get(opp, {})
        if not b or not e:
            continue
        d_wins = e["wins_mean"] - b["wins_mean"]
        d_avg = e["avg_mean"] - b["avg_mean"]
        parts.append(
            f"  {opp}: wins {e['wins_mean']:.1f} ({d_wins:+.1f}), "
            f"avg {e['avg_mean']:.1f} ({d_avg:+.1f}, stdev {e['avg_stdev']:.1f})"
        )
    return "\n".join(parts)


def parse_overrides(items: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--custom expects KEY=VALUE pairs, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


PRESETS: Dict[str, List[Tuple[str, Dict[str, str]]]] = {
    "scout-cap": [
        ("scout_cap=2", {"CRAWL_SCOUT_CAP": "2"}),
        ("scout_cap=3", {"CRAWL_SCOUT_CAP": "3"}),
        ("scout_cap=4 (baseline)", {"CRAWL_SCOUT_CAP": "4"}),
    ],
    "early-worker": [
        ("worker_first_step=0", {"CRAWL_WORKER_FIRST_STEP": "0"}),
        ("worker_first_step=25 (baseline)", {"CRAWL_WORKER_FIRST_STEP": "25"}),
        ("worker_first_step=60", {"CRAWL_WORKER_FIRST_STEP": "60"}),
    ],
    "late-miner": [
        ("late_miner_bonus=0 (baseline)", {"CRAWL_MINER_LATE_FIRST_BONUS": "0"}),
        ("late_miner_bonus=3@step90", {"CRAWL_MINER_LATE_FIRST_BONUS": "3", "CRAWL_MINER_LATE_FIRST_STEP": "90"}),
        ("late_miner_bonus=6@step90", {"CRAWL_MINER_LATE_FIRST_BONUS": "6", "CRAWL_MINER_LATE_FIRST_STEP": "90"}),
    ],
    "no-worker-bonus": [
        ("no_worker_bonus=10 (baseline)", {"CRAWL_WORKER_NO_WORKER_BONUS": "10"}),
        ("no_worker_bonus=15", {"CRAWL_WORKER_NO_WORKER_BONUS": "15"}),
        ("no_worker_bonus=20", {"CRAWL_WORKER_NO_WORKER_BONUS": "20"}),
    ],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--preset",
        choices=sorted(PRESETS.keys()) + ["all"],
        help="Which preset sweep to run",
    )
    ap.add_argument(
        "--custom",
        nargs="*",
        default=None,
        help="Custom KEY=VALUE overrides (one experiment) instead of a preset",
    )
    ap.add_argument("--repeat", type=int, default=1, help="Trials per setting (default 1)")
    ap.add_argument(
        "--seeds",
        nargs="*",
        type=int,
        default=None,
        help="Forward to benchmark.py --seeds (default benchmark default 1-10)",
    )
    ap.add_argument(
        "--opponents",
        nargs="*",
        default=None,
        help="Forward to benchmark.py --opponents",
    )
    args = ap.parse_args()
    bench_args: List[str] = []
    if args.seeds:
        bench_args += ["--seeds", *[str(s) for s in args.seeds]]
    if args.opponents:
        bench_args += ["--opponents", *args.opponents]

    if not args.preset and not args.custom:
        ap.error("Pick --preset or --custom")

    print(
        f"Establishing baseline (no overrides) seeds={args.seeds or '1-10'} "
        f"opponents={args.opponents or 'all'}…",
        flush=True,
    )
    baseline = run_experiment("baseline", {}, args.repeat, bench_args)
    print()
    if not baseline:
        raise SystemExit("Could not establish baseline; aborting.")

    deltas: List[str] = []
    if args.custom:
        overrides = parse_overrides(args.custom)
        agg = run_experiment("custom", overrides, args.repeat, bench_args)
        deltas.append(diff_vs_baseline(f"custom {overrides}", baseline, agg))

    if args.preset:
        presets = list(PRESETS.keys()) if args.preset == "all" else [args.preset]
        for preset in presets:
            for label, overrides in PRESETS[preset]:
                agg = run_experiment(f"{preset}/{label}", overrides, args.repeat, bench_args)
                deltas.append(diff_vs_baseline(f"{preset}/{label}", baseline, agg))

    print("\n== Summary (delta vs baseline) ==")
    for d in deltas:
        print(d)


if __name__ == "__main__":
    main()
