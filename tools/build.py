#!/usr/bin/env python3
"""Builds the Rolls site from the catalogue into _site/.

For each record catalogue/<id>.json: fetch its source (a Mutopia piece at the
pinned MutopiaProject commit), update it with convert-ly, convert it to
MusicXML with lilyport, check the result, and write

    _site/scores/<id>.mxl     the score
    _site/s/<id>/index.html   its page
    _site/index.json          the catalogue for Orchestrion and the web UI
    _site/index.html          the library page
    _site/difficulty/         how difficulty is rated (tools/difficulty.py)
    _site/sitemap.xml, robots.txt  for search engines

A score that fails a check is left out of the site, and the build fails
(unless --keep-going), so that nothing unchecked is published.

    python3 tools/build.py --lilyport ../lilyport [--schema XSD] [--only ID]

lilyport's LilyPond is found as lilyport finds it ($LILYPOND, else the newest
in lilyport's .tools/, else the PATH); convert-ly is taken from beside it.
Standard library only, except lxml for --schema.
"""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import difficulty

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
WEB = ROOT / "web"
SITE_URL = "https://rolls.orchestrion.app/"
MUTOPIA_GIT = "https://github.com/MutopiaProject/MutopiaProject.git"
MUTOPIA_PAGE = "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id={}"
SCHEMA_VERSION = 1
# Ids are slugs: chosen once when the record is created, never recomputed
# from the title, since URLs and Orchestrion's cache rely on them.
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# What a source's header must say for each licence we accept, so that a
# record can't claim a licence its edition doesn't carry.
LICENSE_TEXT = {
    "CC0 1.0": ["Public Domain", "CC0"],
    "Public Domain": ["Public Domain"],
    "CC BY 2.5": ["Attribution 2.5"],
    "CC BY 3.0": ["Attribution 3.0"],
    "CC BY 4.0": ["Attribution 4.0"],
    "CC BY-SA 2.5": ["Attribution-ShareAlike 2.5"],
    "CC BY-SA 3.0": ["Attribution-ShareAlike 3.0"],
    "CC BY-SA 4.0": ["Attribution-ShareAlike 4.0"],
}


class BuildError(Exception):
    pass


def log(message):
    print(message, file=sys.stderr, flush=True)


def load_catalogue(only=None):
    """The records, each with its id (the file name), sorted by id."""
    records = []
    for path in sorted((ROOT / "catalogue").glob("*.json")):
        score_id = path.stem
        if only and score_id not in only:
            continue
        if not ID_PATTERN.match(score_id):
            raise BuildError(f"{path.name}: the id must be a slug (a-z, 0-9, hyphens)")
        record = json.loads(path.read_text(encoding="utf-8"))
        for field in ("title", "composer", "source", "license", "credits"):
            if field not in record:
                raise BuildError(f"{path.name}: no {field}")
        if record["license"]["name"] not in LICENSE_TEXT:
            raise BuildError(f"{path.name}: unknown licence {record['license']['name']}")
        records.append({"id": score_id, **record})
    return records


def find_lilypond(lilyport):
    """The LilyPond lilyport will run (same order as lilyport's find_lilypond)."""
    if os.environ.get("LILYPOND"):
        return Path(os.environ["LILYPOND"])
    installs = sorted((lilyport / ".tools").glob("lilypond-*/bin/lilypond"),
                      key=lambda p: [int(x) for x in p.parts[-3].split("-")[1].split(".")])
    if installs:
        return installs[-1]
    found = shutil.which("lilypond")
    if found:
        return Path(found)
    raise BuildError("LilyPond not found: run lilyport's tools/fetch-lilypond.sh")


def mutopia_checkout(commit, paths):
    """A sparse checkout of MutopiaProject at commit, holding the directories of
    the given source files (a piece may include files beside it)."""
    repo = CACHE / "mutopia" / commit
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        git = ["git", "-C", str(repo)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["remote", "add", "origin", MUTOPIA_GIT], check=True)
        subprocess.run(git + ["fetch", "-q", "--depth", "1", "--filter=blob:none",
                              "origin", commit], check=True)
    directories = sorted({"/" + str(Path(p).parent) + "/" for p in paths})
    git = ["git", "-C", str(repo)]
    subprocess.run(git + ["sparse-checkout", "set", "--no-cone"] + directories, check=True)
    subprocess.run(git + ["checkout", "-q", commit], check=True)
    return repo


def prepare_source(record, repo, convert_ly, work):
    """Copies the piece's directory into work/ and updates the copy with
    convert-ly; returns the main file to convert."""
    source = repo / record["source"]["path"]
    if work.exists():
        shutil.rmtree(work)
    # symlinks: most pieces have a Makefile linking to Mutopia's scripts,
    # outside the sparse checkout.
    shutil.copytree(source.parent, work, symlinks=True)
    main = work / source.name
    for ly in work.glob("*.ly"):
        result = subprocess.run([str(convert_ly), "-e", str(ly)],
                                capture_output=True, text=True)
        if result.returncode:
            raise BuildError(f"convert-ly failed on {ly.name}:\n{result.stderr}")
    return main


def header_text(main):
    """The main file's text and that of the files beside it that it includes,
    where a piece's header may be (e.g. \\include "header.ly")."""
    text = main.read_text(encoding="utf-8", errors="replace")
    for name in re.findall(r'\\include\s+"([^"]+)"', text):
        included = main.parent / name
        if included.is_file():
            text += included.read_text(encoding="utf-8", errors="replace")
    return text


def check_license(record, ly_text):
    expected = LICENSE_TEXT[record["license"]["name"]]
    if not any(text in ly_text for text in expected):
        raise BuildError(f"the source doesn't say {record['license']['name']} "
                         f"(looked for {' or '.join(expected)})")


def convert(record, main, lilyport, output):
    command = [sys.executable, "-m", "lilyport", "convert", str(main), "-o", str(output)]
    options = record.get("lilyport", {})
    if "score" in options:
        command += ["--score", str(options["score"])]
    if "tempo" in options:
        command += ["--tempo", options["tempo"]]
    environment = dict(os.environ, PYTHONPATH=str(lilyport))
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    for line in result.stderr.splitlines():
        log(f"    {line}")
    if result.returncode:
        raise BuildError("lilyport failed")


def musicxml_text(mxl):
    """The score document inside a compressed MusicXML file."""
    with zipfile.ZipFile(mxl) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
        return archive.read(rootfile.get("full-path"))


def check_schema(xml_bytes, schema):
    from lxml import etree
    validator = etree.XMLSchema(etree.parse(str(schema)))
    document = etree.fromstring(xml_bytes, etree.XMLParser(resolve_entities=False,
                                                           no_network=True))
    if not validator.validate(document):
        errors = "\n".join(f"  line {e.line}: {e.message}" for e in validator.error_log[:10])
        raise BuildError(f"not valid MusicXML 4.0:\n{errors}")


def measures(xml_bytes):
    """What the index says about the music itself, computed from the MusicXML."""
    root = ET.fromstring(xml_bytes)
    parts = root.findall("part")
    staves = 0
    for part in parts:
        counts = [int(e.text) for e in part.iter("staves")]
        staves += max(counts, default=1)
    return {
        "parts": len(parts),
        "staves": staves,
        "measures": len(parts[0].findall("measure")) if parts else 0,
    }


def index_entry(record, mxl, xml_bytes, model):
    source = record["source"]
    entry = {
        "id": record["id"],
        "title": record["title"],
        "composer": record["composer"],
        "opus": record.get("opus"),
        "instrument": record.get("instrument"),
        "tags": record.get("tags", []),
        "license": record["license"],
        "credits": record["credits"],
        "source": {
            "kind": source["kind"],
            "edition": source.get("edition"),
        },
        # Relative to index.json, so that the site works under any address.
        "musicxml": f"scores/{record['id']}.mxl",
        "page": f"s/{record['id']}/",
        # Orchestrion downloads the score again when this changes: a hash
        # of the document, not of the .mxl, whose zip has the build's dates.
        "version": hashlib.sha256(xml_bytes).hexdigest()[:16],
        "size": mxl.stat().st_size,
        **measures(xml_bytes),
        # Levels on a 1-9 scale calibrated to Henle's, for Orchestrion's two
        # ways of playing; "about" (relative) explains how they are rated.
        "difficulty": {
            **difficulty.levels(difficulty.measures(mxl), model),
            "method": model["method"],
            "about": "difficulty/",
        },
    }
    if source["kind"] == "mutopia":
        entry["source"].update({
            "mutopiaId": source["mutopiaId"],
            "url": MUTOPIA_PAGE.format(source["mutopiaId"]),
            "commit": source["commit"],
            "path": source["path"],
        })
    return entry


# ---- the web pages ----------------------------------------------------------

def page(title, body, path, description="", data=None):
    """A whole HTML page at path (relative to the site root, "" or "s/<id>/"),
    with data as its schema.org JSON-LD, for search engines."""
    up = "../" * path.count("/")
    head = ""
    if data:
        # "</" would end the script element early.
        text = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
        head = f'\n  <script type="application/ld+json">\n{text}\n  </script>'
    template = (WEB / "page.html").read_text(encoding="utf-8")
    return (template
            .replace("{{title}}", html.escape(title))
            .replace("{{description}}", html.escape(description))
            .replace("{{url}}", html.escape(SITE_URL + path))
            .replace("{{root}}", up)
            .replace("{{head}}", head)
            .replace("{{body}}", body))


def full_title(entry):
    """The title with its opus, which is often how a piece is searched for: on
    the work, before a movement ("Sonata, Op. 27 No. 2: I. Adagio")."""
    if not entry["opus"]:
        return entry["title"]
    work, colon, movement = entry["title"].partition(": ")
    return f"{work}, {entry['opus']}{colon}{movement}"


def score_data(entry):
    """What a search engine should know about a score: the work, and the
    MusicXML of it that we publish."""
    data = {
        "@context": "https://schema.org",
        "@type": "MusicComposition",
        "name": full_title(entry),
        "composer": {"@type": "Person", "name": entry["composer"]},
        "url": SITE_URL + entry["page"],
        "encoding": {
            "@type": "MediaObject",
            "name": f"{full_title(entry)}: MusicXML score",
            "contentUrl": SITE_URL + entry["musicxml"],
            "encodingFormat": "application/vnd.recordare.musicxml",
            "license": entry["license"]["url"],
            "isAccessibleForFree": True,
        },
    }
    if entry["source"].get("url"):
        data["encoding"]["isBasedOn"] = entry["source"]["url"]
    return data


def credit_lines(entry):
    e = html.escape
    lines = [f"{e(c['role'])} {e(c['name'])}" for c in entry["credits"]]
    if entry["source"].get("edition"):
        lines.append(f"After {e(entry['source']['edition'])}")
    license_ = entry["license"]
    lines.append(f'Licence: <a href="{e(license_["url"])}">{e(license_["name"])}</a>')
    if entry["source"].get("url"):
        lines.append(f'Source and printable PDF: <a href="{e(entry["source"]["url"])}">'
                     f'Mutopia Project, piece {entry["source"]["mutopiaId"]}</a>')
    return lines


def score_page(entry):
    e = html.escape
    facts = " · ".join(x for x in (entry["instrument"], f"{entry['measures']} bars",
                                   f"{entry['staves']} staves") if x)
    credits = "".join(f"<li>{line}</li>" for line in credit_lines(entry))
    level = entry["difficulty"]
    levels = (f'<p class="levels">Difficulty: rhythm <b>{level["rhythm"]}</b> · '
              f'notes <b>{level["notes"]}</b> <span class="scale">(1 to 9)</span> · '
              f'<a href="../../{e(level["about"])}">how it is rated</a></p>')
    body = f"""
<article class="score">
  <p class="crumb"><a href="../../">All scores</a></p>
  <h1>{e(full_title(entry))}</h1>
  <p class="composer">{e(entry['composer'])}</p>
  <p class="facts">{e(facts)}</p>
  {levels}
  <div class="actions">
    <a class="button primary" href="https://orchestrion.app/">Play it in Orchestrion</a>
    <a class="button" href="../../{e(entry['musicxml'])}" download>Download MusicXML</a>
  </div>
  <p class="hint">Get Orchestrion, download the MusicXML and open it there:
  tap the rhythm, and the score plays the notes.</p>
  <ul class="credits">{credits}</ul>
</article>"""
    instrument = f" for {entry['instrument'].lower()}" if entry["instrument"] else ""
    return page(f"{full_title(entry)} — {entry['composer']} — free sheet music (MusicXML)",
                body, entry["page"],
                f"Free sheet music of {full_title(entry)} by {entry['composer']}{instrument}: "
                f"download the MusicXML score ({entry['license']['name']}), "
                f"or play it in Orchestrion.",
                score_data(entry))


def library_page(entries):
    e = html.escape
    cards = []
    for entry in sorted(entries, key=lambda x: (x["composer"], x["title"])):
        search = " ".join([entry["title"], entry["composer"], entry["opus"] or "",
                           *entry["tags"]]).lower()
        cards.append(f"""
  <li class="card" data-search="{e(search)}">
    <a href="{e(entry['page'])}">
      <span class="title">{e(entry['title'])}</span>
      <span class="composer">{e(entry['composer'])}{' · ' + e(entry['opus']) if entry['opus'] else ''}</span>
      <span class="levels">rhythm {entry['difficulty']['rhythm']} · notes {entry['difficulty']['notes']}</span>
    </a>
  </li>""")
    body = f"""
<section class="library">
  <h1>Free scores to play</h1>
  <p class="lead">Public-domain and openly licensed scores, as MusicXML, made for
  <a href="https://orchestrion.app/">Orchestrion</a>: the app where you tap the
  rhythm and the score supplies the notes.</p>
  <input id="search" type="search" placeholder="Search by title or composer" aria-label="Search">
  <ul class="cards" id="cards">{''.join(cards)}
  </ul>
  <p class="empty" id="empty" hidden>No score matches.</p>
</section>
<script src="library.js"></script>"""
    composers = sorted({entry["composer"].split()[-1] for entry in entries})
    return page("Rolls — free sheet music to play (MusicXML)", body, "",
                "Free sheet music, public domain and openly licensed, as MusicXML: "
                f"{', '.join(composers)}. Made for Orchestrion.",
                {"@context": "https://schema.org", "@type": "WebSite",
                 "name": "Rolls", "url": SITE_URL})


REFERENCES = [
    ("G. Henle Verlag", "Levels of difficulty (piano: after Rolf Koenen)",
     "https://www.henle.de/Levels-of-Difficulty/"),
    ("E. Nakamura and K. Yoshii (2018)", "Statistical piano reduction controlling performance difficulty. "
     "APSIPA Transactions on Signal and Information Processing", "https://arxiv.org/abs/1808.05006"),
    ("E. Nakamura, N. Ono and S. Sagayama (2014)", "Merged-output HMM for piano fingering of both hands. ISMIR",
     "https://archives.ismir.net/ismir2014/paper/000251.pdf"),
    ("V. Sébastien, H. Ralambondrainy, O. Sébastien and N. Conruyt (2012)", "Score Analyzer: automatically "
     "determining scores difficulty level for instrumental e-learning. ISMIR",
     "https://ismir2012.ismir.net/authors/event/papers/571_ISMIR_2012.pdf"),
    ("P. Ramoneda et al. (2022)", "Score difficulty analysis for piano performance education based on fingering. "
     "ICASSP", "https://arxiv.org/abs/2203.13010"),
    ("P. Ramoneda et al. (2024)", "Combining piano performance dimensions for score difficulty classification. "
     "Expert Systems with Applications 238", "https://arxiv.org/abs/2306.08480"),
    ("H. C. Longuet-Higgins and C. S. Lee (1984)", "The rhythmic interpretation of monophonic music. "
     "Music Perception 1(4)", None),
    ("F. Gómez, A. Melvin, D. Rappaport and G. T. Toussaint (2005)", "Mathematical measures of syncopation. "
     "Bridges", None),
    ("G. T. Toussaint (2013)", "The Geometry of Musical Rhythm. CRC Press", None),
]

MEASURES = {
    "pace": "how fast the hands act: presses and releases per second for rhythm, notes per second "
            "for notes, in the busiest passages (90th percentile of 3-second windows) [2]",
    "r_indep": "independence of the hands: the share of a hand's actions the other hand doesn't share",
    "r_sync": "syncopation as heard: onsets on weak beats with the stronger beat that follows left silent [7, 8]",
    "r_hsync": "syncopation within each hand's own rhythm [7, 8]",
    "r_offgrid": "notes off the beat's duple and triple subdivisions (other tuplets, irregular groups)",
    "r_cross": "cross-rhythm: beats where duplets and triplets meet, in one hand or between the hands",
    "r_ioi_h": "variety of the durations between a hand's onsets (entropy) [9]",
    "n_chord": "notes per chord [4]",
    "n_span": "hand span: the interval within a hand's chords [4, 6]",
    "n_shift": "hand shifts: how far, beyond an octave, the notes a hand plays within 0.4 s reach [3, 4]",
    "n_held": "notes held while the same hand plays others (finger independence) [4]",
}


def difficulty_page(model):
    """How difficulty is rated, with the model's current weights and how well
    it predicts Henle's levels."""
    e = html.escape
    refs = "".join(
        f'<li id="ref{i}">{e(who)}. {f"<a href={chr(34)}{e(url)}{chr(34)}>{e(what)}</a>" if url else e(what)}.</li>'
        for i, (who, what, url) in enumerate(REFERENCES, start=1))
    weights = [("pace", model["pace"])] + [(n, w) for axis in ("rhythm", "notes")
                                            for n, w in model[axis].items()]
    rows = "".join(f"<tr><td>{e(MEASURES[n])}</td><td>{w:.2f}</td></tr>" for n, w in weights)
    cal = model["calibration"]
    body = f"""
<article class="about">
  <p class="crumb"><a href="../">All scores</a></p>
  <h1>How difficulty is rated</h1>
  <p class="lead">Each score has two levels, from 1 (easiest) to 9, after
  Orchestrion's two ways of playing: <b>rhythm</b>, where you play each hand's
  rhythm on any keys and the score supplies the notes, and <b>notes</b>, where
  you play the notes yourself.</p>

  <h2>What is measured</h2>
  <p>Everything is computed from the score itself. Each staff is one hand,
  whose voices combine into the rhythm it plays. A hand acts when it presses
  and when it releases; a release counts when it doesn't coincide with the
  hand's next press: a little when a note is merely not held to the next, much
  more for a staccato. The measures, and the weight each currently has:</p>
  <table class="weights"><tr><th>Measure</th><th>Weight</th></tr>{rows}</table>

  <h2>How the levels are set</h2>
  <p>The weights are fitted to the levels of difficulty that the publisher
  G. Henle Verlag gives its piano editions [1], for {cal['pieces']} pieces from
  the Mutopia Project that Henle has graded. Henle grades a piece as a whole,
  so the fit predicts that level: a base, plus the piece's pace, plus its other
  rhythm and notes measures. A score's rhythm level is the base, its pace, and
  twice its other rhythm measures: what its level would be if its notes were as
  hard as its rhythm; likewise for notes. Each piece left out of the fit in
  turn, the fitted level is within one of Henle's for
  {cal['within_one_level']:.0%} of the pieces, and equal for {cal['exact']:.0%}.</p>
  <p>The levels are ours, not Henle's: a score's level here is what the model
  makes of it, on a scale calibrated to Henle's.</p>

  <h2>Prior work</h2>
  <p>The measures follow research on the difficulty of piano scores and on
  rhythmic complexity: speed and fingering [2, 3], hand span, chords and
  polyphony [4, 5, 6], syncopation [7, 8] and rhythmic variety [9]. Combining
  several aspects of performance, rather than one, follows [6]. Weighting
  releases, and the split into a rhythm and a notes level, are our own.</p>
  <ol class="references">{refs}</ol>
  <p class="hint">Method {e(model['method'])}. The code and the calibration data are in
  <a href="https://github.com/saintmatthieu/rolls/tree/main/tools">the repository</a>
  (<code>difficulty.py</code>, <code>calibrate.py</code>, <code>difficulty-calibration.tsv</code>).</p>
</article>"""
    return page("How difficulty is rated — Rolls", body, "difficulty/",
                "How Rolls rates the difficulty of a score, for rhythm and for notes, "
                "and the research it builds on.")


def sitemap(entries):
    """sitemap.xml: every page, for search engines to find them all."""
    urls = [SITE_URL, SITE_URL + "difficulty/"] + [SITE_URL + entry["page"] for entry in entries]
    lines = [f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(lines) + "\n</urlset>\n")


# ---- the build --------------------------------------------------------------

def build(args):
    lilyport = Path(args.lilyport).resolve()
    if not (lilyport / "lilyport" / "__main__.py").exists():
        raise BuildError(f"{lilyport} is not a lilyport checkout")
    lilypond = find_lilypond(lilyport)
    convert_ly = lilypond.with_name("convert-ly")
    log(f"LilyPond: {lilypond}")
    records = load_catalogue(args.only)
    model = json.loads(difficulty.MODEL.read_text(encoding="utf-8"))

    out = Path(args.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    (out / "scores").mkdir(parents=True)

    by_commit = {}
    for record in records:
        if record["source"]["kind"] != "mutopia":
            raise BuildError(f"{record['id']}: unknown source kind {record['source']['kind']}")
        by_commit.setdefault(record["source"]["commit"], []).append(record)
    repos = {commit: mutopia_checkout(commit, [r["source"]["path"] for r in group])
             for commit, group in by_commit.items()}

    entries, failures = [], []
    for record in records:
        log(f"{record['id']}")
        try:
            main = prepare_source(record, repos[record["source"]["commit"]], convert_ly,
                                  CACHE / "work" / record["id"])
            check_license(record, header_text(main))
            mxl = out / "scores" / f"{record['id']}.mxl"
            convert(record, main, lilyport, mxl)
            xml_bytes = musicxml_text(mxl)
            if args.schema:
                check_schema(xml_bytes, args.schema)
            entries.append(index_entry(record, mxl, xml_bytes, model))
            log(f"    ok")
        except (BuildError, subprocess.CalledProcessError, OSError, ET.ParseError) as error:
            log(f"    FAILED: {error}")
            failures.append(record["id"])
            (out / "scores" / f"{record['id']}.mxl").unlink(missing_ok=True)

    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site": SITE_URL,
        "scores": entries,
    }
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    for entry in entries:
        directory = out / entry["page"]
        directory.mkdir(parents=True)
        (directory / "index.html").write_text(score_page(entry), encoding="utf-8")
    (out / "index.html").write_text(library_page(entries), encoding="utf-8")
    (out / "difficulty").mkdir()
    (out / "difficulty" / "index.html").write_text(difficulty_page(model), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(entries), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
                                    encoding="utf-8")
    for asset in ("style.css", "library.js", "favicon.png"):
        if (WEB / asset).exists():
            shutil.copy(WEB / asset, out / asset)

    log(f"{len(entries)} scores in {out}" +
        (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures and not args.keep_going else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lilyport", required=True, help="a lilyport checkout")
    parser.add_argument("--out", default=str(ROOT / "_site"))
    parser.add_argument("--schema", help="MusicXML 4.0 musicxml.xsd (tools/fetch-schema.sh)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="build these scores only")
    parser.add_argument("--keep-going", action="store_true",
                        help="publish the scores that pass even if others fail")
    args = parser.parse_args()
    try:
        sys.exit(build(args))
    except BuildError as error:
        log(f"error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
