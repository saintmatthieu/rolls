#!/usr/bin/env python3
"""Builds the Rolls site from the catalogue into _site/.

For each record catalogue/<id>.json: fetch its source (a Mutopia piece at the
pinned MutopiaProject commit), update it with convert-ly, convert it to
MusicXML with lilyport, check the result, and write

    _site/scores/<id>.mxl     the score, with its opus and difficulty written
                              in for Orchestrion (stamp())
    _site/s/<id>/index.html   its page
    _site/index.json          the catalogue for Orchestrion and the web UI
    _site/index.html          the library page
    _site/difficulty/         how difficulty is rated (tools/difficulty.py)
    _site/sitemap.xml, robots.txt  for search engines

A score that fails a check is left out of the site, and the build fails
(unless --keep-going), so that nothing unchecked is published.

    python3 tools/build.py --lilyport ../lilyport [--schema XSD] [--only ID]
    python3 tools/build.py --pages-only   # the pages again, from _site/index.json

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
import unicodedata
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
    # The included files too (.ily, subdirectories): a source's own definitions
    # may be as old as its music (Hanon's metronome mark, \note #"4").
    # convert-ly needs a \version; files without one are left as they are.
    for ly in sorted([*work.rglob("*.ly"), *work.rglob("*.ily")]):
        if ly.is_symlink() or "\\version" not in ly.read_text(encoding="utf-8", errors="replace"):
            continue
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


# What the score says about itself for Orchestrion, beside the index: so that
# a copy of it anywhere (a folder of scores, a download) shows its opus and
# difficulty in Orchestrion's shelf. Orchestrion reads these names.
DIFFICULTY_FIELDS = {
    "rhythm": "orchestrion-difficulty-rhythm",
    "notes": "orchestrion-difficulty-notes",
    "method": "orchestrion-difficulty-method",
}


def stamp_document(xml_bytes, opus, levels):
    """The score with the catalogue's opus as its work number and the
    difficulty levels as miscellaneous fields of its identification. Edited
    as text, not reserialized, so that the rest of the document stays as
    lilyport wrote it. Stamping again replaces what an earlier stamp wrote
    (Orchestrion stamps its bundled scores with it too)."""
    text = xml_bytes.decode("utf-8")
    esc = lambda value: html.escape(str(value), quote=False)

    if opus:
        number = f"<work-number>{esc(opus)}</work-number>"
        if re.search(r"<work-number>.*?</work-number>", text, re.S):
            text = re.sub(r"<work-number>.*?</work-number>", lambda m: number, text,
                          count=1, flags=re.S)
        elif "<work>" in text:  # work-number comes first in a work
            text = text.replace("<work>", "<work>" + number, 1)
        else:  # a work comes first in a score
            text = re.sub(r"(<score-partwise\b[^>]*>)", lambda m: m.group(1) +
                          f"<work>{number}</work>", text, count=1)

    # Those of an earlier stamp go, leaving the text around them as it was:
    # stamping again gives the same document.
    for name in DIFFICULTY_FIELDS.values():
        text = re.sub(rf'<miscellaneous-field name="{name}">[^<]*</miscellaneous-field>',
                      "", text)
    text = re.sub(r"<miscellaneous>\s*</miscellaneous>", "", text)
    fields = "".join(f'<miscellaneous-field name="{DIFFICULTY_FIELDS[key]}">'
                     f"{esc(levels[key])}</miscellaneous-field>"
                     for key in ("rhythm", "notes", "method"))
    if "</miscellaneous>" in text:
        text = text.replace("</miscellaneous>", fields + "</miscellaneous>", 1)
    elif "</identification>" in text:  # miscellaneous comes last
        text = text.replace("</identification>",
                            f"<miscellaneous>{fields}</miscellaneous></identification>", 1)
    else:
        raise BuildError("the score has no <identification> to hold its difficulty")
    return text.encode("utf-8")


def stamp(mxl, opus, levels):
    """Rewrites the compressed MusicXML with stamp_document()'s score, its
    other entries as they were (mimetype first and stored, as MXL wants)."""
    with zipfile.ZipFile(mxl) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
        root_path = rootfile.get("full-path")
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    stamped = mxl.with_suffix(".stamped")
    with zipfile.ZipFile(stamped, "w") as archive:
        for info, data in entries:
            if info.filename == root_path:
                data = stamp_document(data, opus, levels)
            archive.writestr(info, data)
    stamped.replace(mxl)


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


def index_entry(record, mxl, xml_bytes, version, levels):
    source = record["source"]
    entry = {
        "id": record["id"],
        "title": record["title"],
        "composer": record["composer"],
        "opus": record.get("opus"),
        "instrument": record.get("instrument"),
        "tags": record.get("tags", []),
        **({"composerSort": record["composerSort"]} if record.get("composerSort") else {}),
        "license": record["license"],
        "credits": record["credits"],
        "source": {
            "kind": source["kind"],
            "edition": source.get("edition"),
        },
        # Relative to index.json, so that the site works under any address.
        "musicxml": f"scores/{record['id']}.mxl",
        "page": f"s/{record['id']}/",
        # Orchestrion downloads the score again when this changes (see
        # build()).
        "version": version,
        "size": mxl.stat().st_size,
        **measures(xml_bytes),
        # Levels on a 1-9 scale calibrated to Henle's, for Orchestrion's two
        # ways of playing; "about" (relative) explains how they are rated.
        "difficulty": {**levels, "about": "difficulty/"},
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


def fold(text):
    """Text to compare: lower case, without accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def composer_key(entry):
    """Surname first. Mutopia's directory for the composer already is
    ("SchumannR", "BachJS", "BeethovenLv", "RougetdeLisleCJ"); otherwise the
    record's composerSort, else the last word of the name. Mutopia's directory
    is only used if its surname is in the name we credit: Mutopia files the
    Minuet in G under BachJS, which we credit to Petzold."""
    if entry.get("composerSort"):
        return fold(entry["composerSort"])
    if entry["source"]["kind"] == "mutopia":
        directory = entry["source"]["path"].split("/")[1]
        surname = re.sub(r"([A-Z][a-z]?)+$", "", directory)
        if surname and fold(surname) in re.sub(r"[\s-]", "", fold(entry["composer"])):
            return fold(directory)
    *given, surname = entry["composer"].split()
    return fold(" ".join([surname, *given]))


def opus_key(opus):
    """Catalogue numbers in the order publishers list them: opus numbers, then
    works without one (WoO), then appendices (Anh.), each numerically
    ("Op. 68 No. 2" before "Op. 68 No. 10"); pieces with no opus last."""
    if not opus:
        return (3, "", ())
    system = re.match(r"\D*", opus).group().strip()
    numbers = tuple(int(n) for n in re.findall(r"\d+", opus))
    rank = 2 if "Anh" in system else 1 if system.startswith("WoO") else 0
    return (rank, fold(system), numbers)


def title_key(title):
    return re.sub(r"^(the|a|an) ", "", fold(title))


def ranks(entries, key):
    """Each entry's position, by id, when sorted by key (ties share a rank)."""
    values = sorted({key(entry) for entry in entries})
    return {entry["id"]: values.index(key(entry)) for entry in entries}


def library_page(entries):
    """The table of scores in the default order (composer, opus, title); each
    row carries its rank for the other orders, which library.js applies when
    a column's heading is clicked."""
    e = html.escape
    default = lambda x: (composer_key(x), fold(x["composer"]), opus_key(x["opus"]),
                         title_key(x["title"]))
    title = ranks(entries, lambda x: title_key(x["title"]))
    # Across composers, an opus sorts by its catalogue (BWV, K., Op., ...),
    # then numerically; pieces without one last.
    opus = ranks(entries, lambda x: (x["opus"] is None, *opus_key(x["opus"])[1:]))
    rows = []
    for entry in sorted(entries, key=default):
        search = " ".join([entry["title"], entry["composer"], entry["opus"] or ""]).lower()
        tags = " ".join(entry["tags"]).lower()
        level = entry["difficulty"]
        catalogue = e(entry["opus"] or "")
        rows.append(f"""
    <tr data-search="{e(search)}" data-tags="{e(tags)}" data-title="{title[entry['id']]}" data-opus="{opus[entry['id']]}"
        data-rhythm="{level['rhythm']}" data-notes="{level['notes']}" data-length="{entry['measures']}">
      <td class="title"><a href="{e(entry['page'])}">{e(entry['title'])}</a>
        <span class="sub">{e(entry['composer'])}{' · <span>' + catalogue + '</span>' if catalogue else ''}</span></td>
      <td class="composer">{e(entry['composer'])}</td>
      <td class="opus">{catalogue}</td>
      <td class="level">{level['rhythm']}</td>
      <td class="level">{level['notes']}</td>
      <td class="bars">{entry['measures']}</td>
    </tr>""")
    sortable = lambda key, label, attrs="": (
        f'<th{attrs} data-sort="{key}"><button type="button">{label}</button></th>')
    body = f"""
<section class="library">
  <h1>Free scores to play</h1>
  <p class="lead">Public-domain and openly licensed scores, as MusicXML, made for
  <a href="https://orchestrion.app/">Orchestrion</a>.</p>
  <input id="search" type="search" placeholder="Search by title, composer or opus" aria-label="Search">
  <table class="scores" id="scores">
    <thead>
    <tr>
      {sortable("title", "Title", ' rowspan="2" class="title"')}
      {sortable("composer", "Composer", ' rowspan="2" class="composer"')}
      {sortable("opus", "Opus", ' rowspan="2" class="opus"')}
      {sortable("difficulty", "Difficulty", ' colspan="2" class="level"')}
      {sortable("length", "Bars", ' rowspan="2" class="bars"')}
    </tr>
    <tr>
      {sortable("rhythm", "Rhythm", ' class="level"')}
      {sortable("notes", "Notes", ' class="level"')}
    </tr>
    </thead>
    <tbody>{''.join(rows)}
    </tbody>
  </table>
  <p class="empty" id="empty" hidden>No score matches.</p>
  <p class="note">Difficulty from 1 to 9, for rhythm and for notes:
  <a href="difficulty/">how it is rated</a>. Sorting by Difficulty uses the
  higher of a score's two levels.</p>
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

def write_pages(out, entries, model):
    """Everything but the scores and index.json: the pages and their assets."""
    for entry in entries:
        directory = out / entry["page"]
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        (directory / "index.html").write_text(score_page(entry), encoding="utf-8")
    (out / "index.html").write_text(library_page(entries), encoding="utf-8")
    (out / "difficulty").mkdir(exist_ok=True)
    (out / "difficulty" / "index.html").write_text(difficulty_page(model), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(entries), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
                                    encoding="utf-8")
    for asset in ("style.css", "library.js", "favicon.png"):
        if (WEB / asset).exists():
            shutil.copy(WEB / asset, out / asset)


def build_pages(args):
    """Writes the pages again from the index.json of an earlier build, without
    converting the scores: for work on the pages."""
    out = Path(args.out).resolve()
    index = out / "index.json"
    if not index.exists():
        raise BuildError(f"no {index}: run a full build first")
    entries = json.loads(index.read_text(encoding="utf-8"))["scores"]
    model = json.loads(difficulty.MODEL.read_text(encoding="utf-8"))
    write_pages(out, entries, model)
    log(f"pages of {len(entries)} scores written in {out}")
    return 0


def build(args):
    if args.pages_only:
        return build_pages(args)
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
            # A hash of the document as converted, not of the .mxl, whose zip
            # has the build's dates, nor of what stamp() writes into it: a new
            # opus or a recalibrated difficulty doesn't make Orchestrion offer
            # an update of music that didn't change (it takes them from the
            # index anyway).
            version = hashlib.sha256(musicxml_text(mxl)).hexdigest()[:16]
            levels = {**difficulty.levels(difficulty.measures(mxl), model),
                      "method": model["method"]}
            stamp(mxl, record.get("opus"), levels)
            xml_bytes = musicxml_text(mxl)
            if args.schema:
                check_schema(xml_bytes, args.schema)
            entries.append(index_entry(record, mxl, xml_bytes, version, levels))
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
    write_pages(out, entries, model)

    log(f"{len(entries)} scores in {out}" +
        (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures and not args.keep_going else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--lilyport", help="a lilyport checkout (required unless --pages-only)")
    parser.add_argument("--pages-only", action="store_true",
                        help="write the pages again from the existing index.json, "
                             "without converting the scores")
    parser.add_argument("--out", default=str(ROOT / "_site"))
    parser.add_argument("--schema", help="MusicXML 4.0 musicxml.xsd (tools/fetch-schema.sh)")
    parser.add_argument("--only", nargs="+", metavar="ID", help="build these scores only")
    parser.add_argument("--keep-going", action="store_true",
                        help="publish the scores that pass even if others fail")
    args = parser.parse_args()
    if not args.pages_only and not args.lilyport:
        parser.error("--lilyport is required")
    try:
        sys.exit(build(args))
    except BuildError as error:
        log(f"error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
