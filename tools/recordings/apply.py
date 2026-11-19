#!/usr/bin/env python3
"""Writes the reviewed recordings into the catalogue.

Takes the review page's documents (collection "review", one per piece, as
saved by Claude's ArtifactData tool with out_dir: <dir>/<piece id>.json, the
document's fields under "data" or at the top level) and, for each ticked
video, puts {"url", "performer"} in catalogue/<piece id>.json "recordings":
https://www.youtube.com/watch?v=ID[&t=START][&end=END], in seconds. A video
already there is updated in place, one that isn't is added at the end; videos
unticked on the page are left alone (remove them by hand).

    python3 tools/recordings/apply.py DIR [--dry-run]

The record is edited as text, so that its layout stays as it is: the
recordings are its last field. Then check them:
python3 tools/build.py --check-recordings. Standard library only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def video_of(url):
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


def recordings_text(recordings):
    entries = ",\n".join(
        "    {\n" + ",\n".join(f"      {json.dumps(key)}: {json.dumps(entry[key], ensure_ascii=False)}"
                             for key in ("url", "performer")) + "\n    }"
        for entry in recordings)
    return '"recordings": [\n' + entries + "\n  ]"


def apply(path, review):
    text = path.read_text()
    record = json.loads(text)
    recordings = list(record.get("recordings", []))
    starts, ends = review.get("starts", {}), review.get("ends", {})
    for video, pick in review.get("picked", {}).items():
        url = "https://www.youtube.com/watch?v=" + video
        if starts.get(video):
            url += f"&t={starts[video]}"
        if ends.get(video):
            url += f"&end={ends[video]}"
        entry = {"url": url, "performer": (pick.get("performer") or "").strip()}
        if not entry["performer"]:
            sys.exit(f"{path.stem}: {video} has no performer")
        same = [number for number, other in enumerate(recordings) if video_of(other["url"]) == video]
        if same:
            recordings[same[0]] = entry
        else:
            recordings.append(entry)
    if recordings == record.get("recordings", []):
        return False
    if "recordings" in record:
        if list(record)[-1] != "recordings":
            sys.exit(f"{path.stem}: recordings isn't the record's last field; edit it by hand")
        start = text.index('"recordings": [')
        text = text[:start] + recordings_text(recordings) + "\n}\n"
    else:
        if not text.endswith("\n}\n"):
            sys.exit(f"{path.stem}: the record doesn't end with a closing brace on its own line")
        text = text[:-3] + ",\n  " + recordings_text(recordings) + "\n}\n"
    after = json.loads(text)
    assert {key: value for key, value in after.items() if key != "recordings"} == \
        {key: value for key, value in record.items() if key != "recordings"}
    assert after["recordings"] == recordings
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dir", type=Path, help="the review's documents, one JSON file per piece")
    parser.add_argument("--dry-run", action="store_true", help="say what would change")
    args = parser.parse_args()
    for source in sorted(args.dir.glob("*.json")):
        document = json.loads(source.read_text())
        review = document.get("data", document)
        if not review.get("picked"):
            continue
        path = ROOT / "catalogue" / source.name
        if not path.exists():
            sys.exit(f"{source.stem}: not in the catalogue")
        text = apply(path, review)
        if text is False:
            continue
        print(f"{source.stem}: {len(json.loads(text)['recordings'])} recording(s)")
        if not args.dry_run:
            path.write_text(text)


if __name__ == "__main__":
    main()
