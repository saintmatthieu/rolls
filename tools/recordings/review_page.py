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
import difflib
import json
import re
import unicodedata
from pathlib import Path

from find import CANDIDATES, EXCLUDED_CHANNELS, SKIP, load_records

HERE = Path(__file__).resolve().parent

PARTICLES = {"van", "von", "de", "di", "da", "du", "der", "del", "della", "le", "la", "ten", "y", "e", "&", "and"}
NOT_A_NAME = re.compile(
    r"\b(sonat\w*|nocturne|prelude|fugue|etude|étude|waltz|valse|minuet|menuet|march|polonaise|ballade|"
    r"impromptu|fantais\w*|arabesque|gymnop\w*|gnossienne|variations?|suite|concerto|album|op|no|bwv|kv|"
    r"piano|live|full|complete|official|hd|music|classical|recital|concert|performance|movement|major|"
    r"minor|flat|sharp|records?|orchestra|symphony|society|studio|school|academy|channel|topic|"
    r"lounge|sheet|score|tutorial|exam|grade|abrsm|the|"
    r"allegr\w*|andant\w*|adagi\w*|moderato|presto|vivace|largo|lento|grave|cantabile|maestoso|molto|"
    r"romanza|rondo|scherzo|polacca|polonesa|hongroise|dur|moll|duet|preview|demo|view|movements?|"
    r"exercises?|play-along|paintings|festival|fortepiano|harpsichord|app|arr|version|studie|study|"
    r"secondo|primo|speed|practice|\w*marsch)\b", re.I)


def plain(text):
    """Lower case, without accents: "Burgmüller" and "Burgmuller" are one."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def name_like(text):
    """Whether text reads as people's names: a few capitalised words (and
    particles: van, de, &, …), no digits or music words."""
    words = text.replace(",", " ").split()
    return (0 < len(words) <= 8 and not re.search(r"\d", text) and not NOT_A_NAME.search(text)
            and all(word[0].isupper() or word.lower() in PARTICLES for word in words)
            and not any(word.isupper() and len(word) <= 4 and "." not in word for word in words)
            and any(word[0].isupper() for word in words))


def guess_performer(title, channel, composers, piece_titles):
    """Who plays, read off the video's title: "X plays …", "performed by X",
    "X – Chopin: …", "… (X)", "… {X}". None when the title doesn't say, or
    names only the composer, the channel or the piece. The review page offers
    it in place of the channel's name, which for labels, venues and makers
    (Deutsche Grammophon, Yellow Lounge, Steinway & Sons) isn't the pianist."""
    def ok(text):
        text = text.strip(" \"'“”‘’«»-–—|:,.")
        words = set(re.findall(r"\w+", plain(text)))
        # A composer's name, however spelt (Pezold for Petzold, Burgmuller).
        composer = any(difflib.SequenceMatcher(None, word, name).ratio() > 0.8
                       for word in words for name in composers)
        if (not name_like(text) or text.lower() == channel.strip().lower() or composer
                or any(words and words <= set(re.findall(r"\w+", plain(piece))) for piece in piece_titles)):
            return None
        return text

    for pattern in (r"^(.+?)\s+(?:plays|performs|spielt|joue|interpreta)\s",
                    r"\b(?:performed|played|interpreted)\s+by\s+(.+?)(?:\s+(?:and|with|at|in|on)\s|[,(|]|$)"):
        match = re.search(pattern, title, re.I)
        if match and ok(match.group(1)):
            return ok(match.group(1))
    # Segments first, without what is in brackets; then the brackets
    # themselves. A segment that is the channel's name says the channel
    # plays: "… - Inga Fiolia (Klavierfestival Ruhr)" on Inga Fiolia's
    # channel is Inga Fiolia, not the festival.
    bare = re.sub(r"\s*[({\[][^)}\]]*[)}\]]", "", title)
    for text in re.split(r"\s*[-–—]\s+|\s+[-–—|/]+\s+|:\s+|\s*//\s*", bare):
        if text.strip(" \"'“”‘’«»-–—|:,.").lower() == channel.strip().lower():
            return None
        if ok(text):
            return ok(text)
    for text in re.findall(r"[({\[]([^)}\]]+)[)}\]]", title):
        if ok(text):
            return ok(text)
    return None


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
    composers = {plain(record["composer"].split()[-1]) for record in load_records()}
    offered = {}
    for piece in pieces:
        for candidate in piece["cands"]:
            offered.setdefault(candidate["id"], []).append(piece["title"])
    meta = {}
    for video in sorted(used):
        meta[video] = dict(data["videos"][video])
        performer = guess_performer(meta[video]["title"], meta[video]["channel"], composers, offered[video])
        if performer:
            meta[video]["performer"] = performer
    page = {"pieces": pieces, "existing": existing, "meta": meta}
    template = (HERE / "review_page.html").read_text()
    text = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    args.out.write_text(template.replace("/*DATA*/null", text))
    searched = sum(piece["status"] != "pending" for piece in pieces)
    print(f"{args.out}: {searched} of {len(pieces)} pieces searched, {len(used)} videos")


if __name__ == "__main__":
    main()
