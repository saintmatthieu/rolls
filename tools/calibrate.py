#!/usr/bin/env python3
"""Fits tools/difficulty.py's model to Henle's levels of difficulty.

The calibration pieces (tools/difficulty-calibration.tsv: Mutopia pieces with
Henle's level) are converted to MusicXML the way the site's are, into
.cache/calibration/ (kept between runs); the model's weights (non-negative:
no measure makes a piece easier) are fitted by least squares and written to
tools/difficulty-model.json, with how well each piece is predicted when left
out of the fit.

    ../lilyport/.venv/bin/python tools/calibrate.py convert --lilyport ../lilyport [-j 4]
    python3 tools/calibrate.py fit     # needs numpy

The levels are G. Henle Verlag's (https://www.henle.de/Levels-of-Difficulty/),
as shown for each work on its product pages.
"""
import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402
import difficulty  # noqa: E402

COMMIT = "2144afd6f52d56c5b6995b8b589ef1268b3139f0"
TABLE = build.ROOT / "tools" / "difficulty-calibration.tsv"
OUT = build.CACHE / "calibration"


def pieces():
    """(slug, Mutopia path, Henle level, HN number, Henle's title)."""
    rows = []
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        path, level, hn, title = line.split("\t")
        slug = re.sub(r"[^a-z0-9]+", "-", Path(path).stem.lower()).strip("-")
        rows.append((f"{Path(path).parts[1].lower()}-{slug}", path, int(level), hn, title))
    return rows


METHOD = "rolls-1"
PACE = ["r_rate", "n_rate"]
RHYTHM = ["r_indep", "r_sync", "r_hsync", "r_offgrid", "r_cross", "r_ioi_h"]
NOTES = ["n_chord", "n_span", "n_shift", "n_held"]


def nnls(a, b):
    """Lawson and Hanson's non-negative least squares: min |ax - b|, x >= 0."""
    import numpy as np
    n = a.shape[1]
    x, active = np.zeros(n), np.zeros(n, bool)
    for _ in range(10 * n):
        gradient = a.T @ (b - a @ x)
        if active.all() or gradient[~active].max() <= 1e-10:
            break
        active[np.argmax(np.where(active, -np.inf, gradient))] = True
        while True:
            z = np.zeros(n)
            z[active] = np.linalg.lstsq(a[:, active], b, rcond=None)[0]
            if (z[active] > 0).all():
                x = z
                break
            blocking = active & (z <= 0)
            x = x + np.min(x[blocking] / (x[blocking] - z[blocking])) * (z - x)
            active &= x > 1e-12
    return x


def fit(args):
    import numpy as np
    rows = []
    for slug, path, level, hn, title in pieces():
        mxl = OUT / f"{slug}.mxl"
        if mxl.exists():
            rows.append((slug, level, difficulty.measures(mxl)))
    names = PACE + RHYTHM + NOTES
    raw = {n: [difficulty.TRANSFORMS.get(n, lambda v: v)(m[n]) for _, _, m in rows] for n in names}
    ranges = {n: [min(v), max(v)] for n, v in raw.items()}
    model = {"method": METHOD, "range": ranges}

    def columns(model, m):
        z = {n: difficulty.scaled(model, n, m) for n in names}
        # One pace weight, on the mean of the two rates; the levels then take
        # each axis' own rate.
        return [(z["r_rate"] + z["n_rate"]) / 2] + [z[n] for n in RHYTHM + NOTES]

    x = np.array([columns(model, m) for _, _, m in rows])
    y = np.array([level for _, level, _ in rows], float)

    def solve(x, y):
        a = np.hstack([np.ones((len(x), 1)), -np.ones((len(x), 1)), x])
        w = nnls(a, y)
        return {"intercept": float(w[0] - w[1]), "pace": float(w[2]),
                "rhythm": dict(zip(RHYTHM, map(float, w[3:3 + len(RHYTHM)]))),
                "notes": dict(zip(NOTES, map(float, w[3 + len(RHYTHM):])))}

    report = []
    for i, (slug, level, m) in enumerate(rows):
        keep = np.arange(len(rows)) != i
        held_out = {**model, **solve(x[keep], y[keep])}
        pace, rest = difficulty.contributions(held_out, m)
        overall = held_out["intercept"] + (pace["rhythm"] + pace["notes"]) / 2 + rest["rhythm"] + rest["notes"]
        report.append((slug, level, overall, difficulty.levels(m, held_out)))
    model.update(solve(x, y))
    errors = [abs(min(max(round(p), 1), 9) - level) for _, level, p, _ in report]
    model["calibration"] = {
        "pieces": len(rows),
        "within_one_level": round(sum(e <= 1 for e in errors) / len(errors), 3),
        "exact": round(sum(e == 0 for e in errors) / len(errors), 3),
        "mean_error": round(float(np.mean([abs(p - level) for _, level, p, _ in report])), 2),
        "note": "leave-one-out: each piece predicted by a model fitted without it",
    }
    difficulty.MODEL.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8")
    for slug, level, p, lv in sorted(report, key=lambda r: (r[1], r[0])):
        miss = "  <-- off by more than 1" if abs(min(max(round(p), 1), 9) - level) > 1 else ""
        print(f"Henle {level}  fitted {p:4.1f}  rhythm {lv['rhythm']}  notes {lv['notes']}  {slug}{miss}")
    print(json.dumps(model["calibration"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert", help="convert the calibration pieces")
    convert.add_argument("--lilyport", required=True)
    convert.add_argument("-j", "--jobs", type=int, default=4)
    commands.add_parser("fit", help="fit the model to the converted pieces")
    args = parser.parse_args()
    if args.command == "fit":
        return fit(args)
    lilyport = Path(args.lilyport).resolve()
    convert_ly = build.find_lilypond(lilyport).with_name("convert-ly")
    rows = pieces()
    repo = build.mutopia_checkout(COMMIT, [r[1] for r in rows])
    OUT.mkdir(parents=True, exist_ok=True)

    def one(row):
        slug, path, *_ = row
        output = OUT / f"{slug}.mxl"
        if output.exists():
            return slug, "cached"
        record = {"source": {"path": path}}
        try:
            main = build.prepare_source(record, repo, convert_ly, build.CACHE / "work" / f"cal-{slug}")
            build.convert(record, main, lilyport, output)
            return slug, "ok"
        except (build.BuildError, subprocess.CalledProcessError, OSError) as error:
            output.unlink(missing_ok=True)
            return slug, f"FAILED: {str(error).splitlines()[0][:120]}"

    with ThreadPoolExecutor(args.jobs) as pool:
        for slug, status in pool.map(one, rows):
            print(f"{slug}: {status}", flush=True)


if __name__ == "__main__":
    main()
