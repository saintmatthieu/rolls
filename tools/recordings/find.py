#!/usr/bin/env python3
"""Finds YouTube recordings of the catalogue's pieces, for review.

For each piece: search YouTube, leave out what isn't a performance of it
(tutorials, Synthesia, slow practice, covers, the channels in
EXCLUDED_CHANNELS), and keep the most viewed videos that show no ads and can
be embedded (youtube.check()), up to --keep of them. Then offer a video of a
whole set (its title says "complete", "all movements", or gives a range like
"BWV 933-938") to every piece of that set: searched one by one, a set's
pieces don't all find it.

The result goes to tools/recordings/candidates.json, merged into what is
there: {piece id: {"query", "candidates": [{"id", "shared"}]}} and
{"videos": {video id: {"title", "channel", "length", "views"}}}, one record
per video whichever pieces it's offered for. review_page.py makes the page
they are reviewed on.

    python3 tools/recordings/find.py                 # pieces not searched yet
    python3 tools/recordings/find.py --only ID [ID]  # these again
    python3 tools/recordings/find.py --share-only    # just the whole-set rule

Pieces that already have recordings in their record are skipped, and so are
the parts of a work recorded whole (SKIP). The checks are cached in
.cache/recordings/checks.json. Paced to stay under YouTube's rate limit: a
few seconds per piece. Standard library only.
"""
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import youtube

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATES = HERE / "candidates.json"
CHECKS = ROOT / ".cache" / "recordings" / "checks.json"

# Ruled out in review: not human performances.
EXCLUDED_CHANNELS = {"Bach Piano Scores"}
# The variations of K. 265 are recorded with their theme: point them at the
# whole work's video, with t= and end=, rather than searching.
SKIP = re.compile(r"^mozart-k265-")

NOT_A_PERFORMANCE = re.compile(
    r"tutorial|synthesia|slow|lesson|how to|learn|easy piano|karaoke|backing|midi|"
    r"8[- ]?bit|ringtone|guitar|cover|remix|lofi|lo-fi|hours|1 hour|\bmix\b", re.I)
WHOLE_SET = re.compile(r"complete|all movements|\b\d{3}\s*[-–]\s*\d{3}\b", re.I)
PART_OF_SET = re.compile(r"selection|part \d", re.I)


def load_records():
    records = []
    for path in sorted((ROOT / "catalogue").glob("*.json")):
        record = json.loads(path.read_text())
        record["id"] = path.stem
        records.append(record)
    return records


def query_for(record):
    """What to search for. Numbered exercises by their opus and number; a
    duet's primo by its sonatina, for four hands."""
    piece, title, opus = record["id"], record["title"], record.get("opus") or ""
    composer = record["composer"]
    number = re.search(r"-(\d+)(?:-primo)?$", piece)
    if piece.startswith(("czerny-", "diabelli-op149-")):
        return f"{composer.split()[-1]} {opus} piano"
    if piece.startswith("hanon-"):
        return f"Hanon exercise {int(number.group(1))} virtuoso pianist"
    if piece.startswith("diabelli-op163-"):
        sonatina = title.split("Sonatina No. ")[1].split(":")[0]
        return f"Diabelli op 163 Jugendfreuden sonatina {sonatina} piano four hands"
    return f"{composer} {title} {opus} piano"


def views(video):
    return int(re.sub(r"\D", "", video.get("views", "")) or 0)


def find(record, checks, keep, pause):
    query = query_for(record)
    found, seen = [], set()
    for text in (query, query + " performance"):
        for video in youtube.search(text):
            if video["id"] not in seen:
                seen.add(video["id"])
                found.append(video)
        time.sleep(pause)
    found = sorted((video for video in found
                    if not NOT_A_PERFORMANCE.search(video["title"])
                    and video["channel"] not in EXCLUDED_CHANNELS),
                   key=views, reverse=True)
    kept = []
    for video in found[:15]:
        if video["id"] not in checks:
            checks[video["id"]] = youtube.check(video["id"])
            time.sleep(pause)
        if not checks[video["id"]]["ads"] and checks[video["id"]]["embed"]:
            kept.append(video)
            if len(kept) == keep:
                break
    return query, kept


def other_opus(title, opus):
    wanted = re.findall(r"\d+", opus)[:1]
    named = re.findall(r"op\.?\s*(\d+)", title, re.I)
    return bool(wanted and named and wanted[0] not in named)


def share_whole_sets(records, data):
    """Offers a whole set's video to all its pieces: those of one composer
    whose titles share what comes before the colon ("Six Little Preludes:
    No. 1 in C major")."""
    sets = defaultdict(list)
    for record in records:
        if record["id"] in data["pieces"]:
            title = record["title"]
            sets[(record["composer"], title.split(":")[0])].append(record)
    for members in sets.values():
        if len(members) < 2:
            continue
        whole = {candidate["id"] for member in members
                 for candidate in data["pieces"][member["id"]]["candidates"]
                 if WHOLE_SET.search(data["videos"][candidate["id"]]["title"])
                 and not PART_OF_SET.search(data["videos"][candidate["id"]]["title"])
                 and not other_opus(data["videos"][candidate["id"]]["title"],
                                    members[0].get("opus") or "")}
        for member in members:
            candidates = data["pieces"][member["id"]]["candidates"]
            for video in sorted(whole - {candidate["id"] for candidate in candidates}):
                candidates.append({"id": video, "shared": True})


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", nargs="+", metavar="ID", help="search these pieces (again)")
    parser.add_argument("--keep", type=int, default=3, help="videos to keep per piece (3)")
    parser.add_argument("--pause", type=float, default=1.5, help="seconds between requests (1.5)")
    parser.add_argument("--share-only", action="store_true", help="only apply the whole-set rule")
    args = parser.parse_args()

    records = load_records()
    data = json.loads(CANDIDATES.read_text()) if CANDIDATES.exists() else {"pieces": {}, "videos": {}}
    checks = json.loads(CHECKS.read_text()) if CHECKS.exists() else {}
    if args.share_only:
        todo = []
    elif args.only:
        known = {record["id"] for record in records}
        unknown = set(args.only) - known
        if unknown:
            sys.exit(f"not in the catalogue: {', '.join(sorted(unknown))}")
        todo = [record for record in records if record["id"] in args.only]
    else:
        todo = [record for record in records
                if record["id"] not in data["pieces"] and not record.get("recordings")
                and not SKIP.match(record["id"])]

    CHECKS.parent.mkdir(parents=True, exist_ok=True)
    for number, record in enumerate(todo, 1):
        query, kept = find(record, checks, args.keep, args.pause)
        data["pieces"][record["id"]] = {"query": query,
                                        "candidates": [{"id": video["id"], "shared": False} for video in kept]}
        for video in kept:
            data["videos"][video["id"]] = {"title": video["title"], "channel": video["channel"],
                                           "length": video["length"],
                                           "views": video["views"].replace(" views", "")}
        print(f"[{number}/{len(todo)}] {record['id']}: {len(kept)}", flush=True)
        # Saved as it goes: a long run can be stopped and taken up again.
        CHECKS.write_text(json.dumps(checks))
        CANDIDATES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")

    share_whole_sets(records, data)
    CANDIDATES.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
