#!/usr/bin/env python3
"""Builds the Rolls site from the catalogue into _site/.

For each record catalogue/<id>.json: fetch its source (a Mutopia piece at the
pinned MutopiaProject commit), update it with convert-ly, convert it to
MusicXML with lilyport, check the result, and write

    _site/scores/<id>.mxl     the score
    _site/s/<id>/index.html   its page
    _site/index.json          the catalogue for Orchestrion and the web UI
    _site/index.html          the library page

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
    shutil.copytree(source.parent, work)
    main = work / source.name
    for ly in work.glob("*.ly"):
        result = subprocess.run([str(convert_ly), "-e", str(ly)],
                                capture_output=True, text=True)
        if result.returncode:
            raise BuildError(f"convert-ly failed on {ly.name}:\n{result.stderr}")
    return main


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


def index_entry(record, mxl, xml_bytes):
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
        # Left for its own design: rhythm-only, notes, overall.
        "difficulty": None,
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

def page(title, body, depth, description=""):
    """A whole HTML page; depth is how many directories below the site root it
    lives, for its relative links."""
    up = "../" * depth
    template = (WEB / "page.html").read_text(encoding="utf-8")
    return (template
            .replace("{{title}}", html.escape(title))
            .replace("{{description}}", html.escape(description))
            .replace("{{root}}", up)
            .replace("{{body}}", body))


def credit_lines(entry):
    e = html.escape
    lines = [f"{e(c['role'])} {e(c['name'])}" for c in entry["credits"]]
    if entry["source"].get("edition"):
        lines.append(f"After {e(entry['source']['edition'])}")
    license_ = entry["license"]
    lines.append(f'Licence: <a href="{e(license_["url"])}">{e(license_["name"])}</a>')
    if entry["source"].get("url"):
        lines.append(f'Source: <a href="{e(entry["source"]["url"])}">'
                     f'Mutopia Project, piece {entry["source"]["mutopiaId"]}</a>')
    return lines


def score_page(entry):
    e = html.escape
    subtitle = " · ".join(x for x in (entry["composer"], entry["opus"]) if x)
    facts = f"{entry['measures']} bars · {entry['staves']} staves"
    credits = "".join(f"<li>{line}</li>" for line in credit_lines(entry))
    body = f"""
<article class="score">
  <p class="crumb"><a href="../../">All scores</a></p>
  <h1>{e(entry['title'])}</h1>
  <p class="composer">{e(subtitle)}</p>
  <p class="facts">{e(facts)}</p>
  <div class="actions">
    <a class="button primary" href="https://orchestrion.app/">Play it in Orchestrion</a>
    <a class="button" href="../../{e(entry['musicxml'])}" download>Download MusicXML</a>
  </div>
  <p class="hint">Get Orchestrion, download the MusicXML and open it there:
  tap the rhythm, and the score plays the notes.</p>
  <ul class="credits">{credits}</ul>
</article>"""
    return page(f"{entry['title']} — {entry['composer']} — Rolls", body, 2,
                f"{entry['title']} by {entry['composer']}: a free score to play in Orchestrion.")


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
    return page("Rolls — free scores to play", body, 0,
                "Free, playable scores (MusicXML), made for Orchestrion.")


# ---- the build --------------------------------------------------------------

def build(args):
    lilyport = Path(args.lilyport).resolve()
    if not (lilyport / "lilyport" / "__main__.py").exists():
        raise BuildError(f"{lilyport} is not a lilyport checkout")
    lilypond = find_lilypond(lilyport)
    convert_ly = lilypond.with_name("convert-ly")
    log(f"LilyPond: {lilypond}")
    records = load_catalogue(args.only)

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
            check_license(record, main.read_text(encoding="utf-8", errors="replace"))
            mxl = out / "scores" / f"{record['id']}.mxl"
            convert(record, main, lilyport, mxl)
            xml_bytes = musicxml_text(mxl)
            if args.schema:
                check_schema(xml_bytes, args.schema)
            entries.append(index_entry(record, mxl, xml_bytes))
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
