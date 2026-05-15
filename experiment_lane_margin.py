"""
Sweep factory lane hysteresis margin vs benchmark seeds.

Each run uses a fresh interpreter so ``main`` reloads with the env override.

Usage:
  python3 experiment_lane_margin.py
  python3 experiment_lane_margin.py 0 1 2 3
  python3 experiment_lane_margin.py --repeat 3 0 1
"""

import argparse
import os
import re
import statistics
import subprocess
import sys
from typing import Optional


def run_benchmark(margin: int) -> str:
    env = os.environ.copy()
    env["CRAWL_LANE_SWITCH_MARGIN"] = str(margin)
    proc = subprocess.run(
        [sys.executable, "benchmark.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return f"(exit {proc.returncode})\n{out}"
    return out


def parse_random_avg(text: str) -> Optional[float]:
    for line in text.splitlines():
        m = re.match(r"^random: \d+W/\d+D/\d+L avg_reward=(-?\d+(?:\.\d+)?)", line)
        if m:
            return float(m.group(1))
    return None


def parse_summaries(text: str) -> list[str]:
    lines = []
    for line in text.splitlines():
        if re.match(r"^(random|greedy): \d+W/\d+D/\d+L", line):
            lines.append(line.strip())
    return lines


def main():
    ap = argparse.ArgumentParser(description="Sweep CRAWL_LANE_SWITCH_MARGIN vs benchmark.py")
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="Run full benchmark N times per margin (separate process each time); report random avg stats",
    )
    ap.add_argument(
        "margins",
        nargs="*",
        type=int,
        default=[0, 1, 2, 3],
        help="Margin values to try (default: 0 1 2 3)",
    )
    args = ap.parse_args()

    margins = args.margins
    print(
        "Lane margin sweep (CRAWL_LANE_SWITCH_MARGIN); seeds 1–10 from benchmark.py\n"
        f"Repeat per margin: {args.repeat}\n"
    )

    table = []
    for m in margins:
        avgs = []
        last_summaries: list[str] = []
        for t in range(args.repeat):
            label = f"margin={m}" + (f" trial={t + 1}/{args.repeat}" if args.repeat > 1 else "")
            print(f"--- {label} ---", flush=True)
            raw = run_benchmark(m)
            last_summaries = parse_summaries(raw)
            ra = parse_random_avg(raw)
            if ra is not None:
                avgs.append(ra)
                print(f"  random avg_reward={ra:.1f}")
            for s in last_summaries:
                if not s.startswith("random:"):
                    print(f"  {s}")
            if not last_summaries and ra is None:
                print(raw[-2000:] if len(raw) > 2000 else raw)
            print()

        if avgs:
            row = f"  margin={m}: random avg over trials: mean={statistics.mean(avgs):.1f}"
            if len(avgs) > 1:
                row += f" stdev={statistics.stdev(avgs):.2f} min={min(avgs):.1f} max={max(avgs):.1f}"
            print(row)
            if last_summaries:
                print("  last greedy line:", [s for s in last_summaries if s.startswith("greedy:")][0])
            print()
            table.append((m, avgs, last_summaries))

    if len(table) > 1:
        print("== Compare random avg_reward (mean over trials) ==")
        for m, avgs, _ in sorted(table, key=lambda x: statistics.mean(x[1])):
            print(f"  margin={m}: {statistics.mean(avgs):.1f}")


if __name__ == "__main__":
    main()
