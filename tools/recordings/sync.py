#!/usr/bin/env python3
"""Puts the syncs tapped in Orchestrion into the catalogue.

Orchestrion (Advanced ▸ Sync video) saves a recording's sync as
<its data folder>/sync/<score id>/<video id>.json. This checks each one it is
given as the build does (tools/build.py, check_sync: its format, a recording
of the score's, points in order), and copies it to
catalogue/sync/<score id>/<video id>.json, which the build publishes and links
from the recording's entry in index.json.

    python3 tools/recordings/sync.py [PATH ...] [--only ID ...] [--dry-run]

A PATH is a sync file or a folder of them (a data folder's sync/, or one
score's folder in it); none takes Orchestrion's own (a development build's
and a release's). A sync that fails a check is reported and left out; the
others are copied. One tapped on another version of the score than the last
build's (_site/index.json) is copied with a warning: open it in the app to see
that it still fits; one that doesn't say which version it was tapped on is
taken to be of the last build's. Standard library only.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build  # noqa: E402

ROOT = build.ROOT


def app_folders():
    """Orchestrion's sync folders on this machine: a release's and a
    development build's."""
    home = Path.home()
    if sys.platform == "darwin":
        bases = [home / "Library" / "Application Support"]
    elif sys.platform == "win32":
        bases = [Path(os.environ.get(name, "")) for name in ("LOCALAPPDATA", "APPDATA")
                 if os.environ.get(name)]
    else:
        bases = [Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")]
    return [base / app / "sync" for base in bases
            for app in ("Orchestrion", "OrchestrionDevelopment")
            if (base / app / "sync").is_dir()]


def sync_files(paths):
    files = []
    for path in paths:
        if path.is_dir():
            files += sorted(path.glob("*.json")) or sorted(path.glob("*/*.json"))
        elif path.is_file():
            files.append(path)
        else:
            raise build.BuildError(f"{path}: no such file or folder")
    return files


def built_versions():
    """Each score's version in the last build, if there was one."""
    index = ROOT / "_site" / "index.json"
    if not index.exists():
        return {}
    return {entry["id"]: entry["version"]
            for entry in json.loads(index.read_text(encoding="utf-8"))["scores"]}


def take(path, versions, dry_run):
    """Checks the sync and copies it into the catalogue; what was done."""
    score_id, video = path.parent.name, path.stem
    if not (ROOT / "catalogue" / f"{score_id}.json").exists():
        raise build.BuildError(f"no record {score_id} (the sync's folder)")
    record, = build.load_catalogue([score_id])
    if video not in {build.video_of(entry["url"]) for entry in record["recordings"]}:
        raise build.BuildError(f"{video} isn't among {score_id}'s recordings")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise build.BuildError(f"not JSON ({error})")
    built = versions.get(score_id)
    # Saved by an Orchestrion that didn't know the version of a score
    # downloaded before the index had it: the last build's.
    if isinstance(data, dict) and data.get("score") == "":
        if not built:
            raise build.BuildError("the score's version isn't in the sync, nor a build's")
        print(f"note: {path}: no score version, taken as the last build's ({built})",
              file=sys.stderr)
        data["score"] = built
    sync = build.check_sync(data)
    if built and sync["score"] != built:
        print(f"warning: {path}: tapped on another version of the score ({sync['score']}, "
              f"built {built}): check it in the app", file=sys.stderr)
    target = build.SYNCS / score_id / f"{video}.json"
    text = build.sync_text(sync)
    if target.exists() and target.read_text(encoding="utf-8") == text:
        return "unchanged"
    done = "updated" if target.exists() else "added"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return f"{done}: {target.relative_to(ROOT)} ({len(sync['points'])} points)"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, metavar="PATH",
                        help="sync files or folders of them (default: Orchestrion's)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="these scores' syncs only")
    parser.add_argument("--dry-run", action="store_true", help="check, but copy nothing")
    args = parser.parse_args()
    try:
        paths = args.paths or app_folders()
        if not paths:
            raise build.BuildError("Orchestrion's sync folder wasn't found: give the path")
        files = [path for path in sync_files(paths)
                 if not args.only or path.parent.name in args.only]
    except build.BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    versions = built_versions()
    failed = 0
    for path in files:
        try:
            print(f"{path}: {take(path, versions, args.dry_run)}")
        except build.BuildError as error:
            print(f"error: {path}: {error}", file=sys.stderr)
            failed += 1
    print(f"{len(files) - failed} of {len(files)} syncs checked" +
          (" (dry run: nothing copied)" if args.dry_run else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
