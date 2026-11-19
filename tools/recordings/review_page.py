#!/usr/bin/env python3
"""Makes the page recordings are reviewed on, from candidates.json and the
catalogue: review_page.html with the data written in.

    python3 tools/recordings/review_page.py OUT.html

The page is published as a claude.ai artifact: ask Claude to publish OUT.html
to the review's URL (tools/recordings/README.md), which keeps the link and
what was entered on it. On the page: tick the videos to keep, name the
performer, give where the piece starts and ends in the video, write notes.
What is entered there is kept in the artifact's database (collection
"review", one document per piece), which apply.py writes into the catalogue.
Standard library only.
"""
import argparse
import json
from pathlib import Path

from find import CANDIDATES, EXCLUDED_CHANNELS, SKIP, load_records

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="the page to write")
    args = parser.parse_args()

    data = json.loads(CANDIDATES.read_text())
    pieces, existing, used = [], {}, set()
    for record in load_records():
        piece = record["id"]
        if SKIP.match(piece):
            continue
        if record.get("recordings"):
            existing[piece] = record["recordings"]
        searched = data["pieces"].get(piece)
        candidates = [candidate for candidate in (searched or {}).get("candidates", [])
                      if data["videos"][candidate["id"]]["channel"] not in EXCLUDED_CHANNELS]
        used.update(candidate["id"] for candidate in candidates)
        if searched is None:
            status = "indexed" if piece in existing else "pending"
        else:
            status = "found" if candidates else "none"
        pieces.append({"id": piece, "title": record["title"], "composer": record["composer"],
                       "opus": record.get("opus") or "", "status": status, "cands": candidates})
    page = {"pieces": pieces, "existing": existing,
            "meta": {video: data["videos"][video] for video in sorted(used)}}
    template = (HERE / "review_page.html").read_text()
    text = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    args.out.write_text(template.replace("/*DATA*/null", text))
    searched = sum(piece["status"] != "pending" for piece in pieces)
    print(f"{args.out}: {searched} of {len(pieces)} pieces searched, {len(used)} videos")


if __name__ == "__main__":
    main()
