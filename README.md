# Rolls — free scores to play

A library of freely licensed scores as MusicXML, at
**[rolls.orchestrion.app](https://rolls.orchestrion.app/)**, made for
[Orchestrion](https://orchestrion.app/): the free app where you tap the rhythm
on any keyboard and the score supplies the notes. Orchestrions played from
pinned barrels and punched music rolls; this is a library of rolls.

The scores are free for any use their licence allows, in Orchestrion or
elsewhere.

## How it works

- `catalogue/<id>.json`: one record per score: title, composer, licence,
  credits, and where it comes from: for a Mutopia Project piece (`"kind":
  "mutopia"`), its id, the source file and the MutopiaProject commit it is
  built from; for a MusicXML transcription in a git repository (`"kind":
  "musicxml"`, such as the Mozart variations bundled with Orchestrion), the
  repository, commit and file, and the edition it is transcribed from. The file name
  is the score's id, used in its URLs (`scores/<id>.mxl`, `s/<id>/`): chosen
  once, never renamed.
- `tools/build.py` fetches each source, updates it with `convert-ly`, converts
  it with [lilyport](https://github.com/saintmatthieu/lilyport) (a MusicXML
  source is taken as it is), checks it (the licence its source declares, in
  its header or its `<rights>`, the MusicXML 4.0 schema), and writes the
  site into `_site/`: the scores, a page for each with a preview of its first
  system (engraved by LilyPond from the source, through `musicxml2ly` for a
  MusicXML one), the library page,
  `index.json`, and `sitemap.xml` and `robots.txt` for search engines.
- `index.json` is what Orchestrion and the web pages read. It has a
  `schemaVersion`; URLs in it are relative to it; each score's `version` is a
  hash of its MusicXML as lilyport wrote it, so Orchestrion downloads a score
  again only when its music changed; its `preview` is the SVG of its first
  system (`url`, `width`, `height` and `staffSpace`, the gap between a
  staff's lines, in the SVG's units), which Orchestrion's shelf shows at its
  own staff size until the score is downloaded.
- `index.json` also has the `recordings` of the pieces (see *Adding a
  recording*), and the site a `player.html`: the page Orchestrion plays a
  YouTube video in (`player.html?v=VIDEOID[&start=SECONDS][&end=SECONDS]`), since YouTube's
  player only plays on a page of a real site. It is for the app only, and
  kept out of search engines.
- Each score also carries its opus (`<work-number>`) and difficulty
  (`<miscellaneous-field>`s `orchestrion-difficulty-rhythm`, `-notes` and
  `-method`), written in after conversion, so that Orchestrion shows them for
  a copy of the score anywhere, not only for those it downloaded.
- `pins.env` pins lilyport and LilyPond. The workflow builds and deploys on
  every push to `main`; generated files are never committed.

## Difficulty

Each score has a **rhythm** and a **notes** level, 1 to 9, for Orchestrion's
two ways of playing (`difficulty` in `index.json`; explained at
[/difficulty/](https://rolls.orchestrion.app/difficulty/)). `tools/difficulty.py`
measures the score: pace, hand independence, syncopation, tuplets and
cross-rhythms, chords, hand span and shifts, held notes. The measures' weights
are fitted (`tools/calibrate.py`) to G. Henle Verlag's
[levels of difficulty](https://www.henle.de/Levels-of-Difficulty/) for 118
Mutopia pieces Henle has graded (`tools/difficulty-calibration.tsv`), and kept
in `tools/difficulty-model.json`; left out of the fit in turn, 86 % of them get
a level within one of Henle's. The levels are ours, on a scale calibrated to
Henle's.

The measures build on research on piano score difficulty (Nakamura & Yoshii
2018; Nakamura, Ono & Sagayama 2014; Sébastien et al. 2012; Ramoneda et al.
2022, 2024) and on rhythmic complexity (Longuet-Higgins & Lee 1984; Gómez et
al. 2005; Toussaint 2013): full references on the site's page.

```sh
../lilyport/.venv/bin/python tools/calibrate.py convert --lilyport ../lilyport
python3 tools/calibrate.py fit     # needs numpy; rewrites difficulty-model.json
```

## Building locally

```sh
# in a lilyport checkout next to this one:
tools/fetch-lilypond.sh && python3 -m venv .venv && .venv/bin/pip install lxml
# here:
tools/fetch-schema.sh
../lilyport/.venv/bin/python tools/build.py --lilyport ../lilyport \
    --schema .cache/musicxml-4.0/musicxml.xsd
python3 -m http.server -d _site
```

Converted scores are kept in `.cache/scores/`: a build converts again only
the scores whose source, record fixes or options, lilyport, LilyPond or
conversion steps changed (`--no-cache` converts them all). Converting every
score is slow. To work on the pages (`web/`, the HTML in
`tools/build.py`), write them again from the last build's `_site/index.json`,
in well under a second:

```sh
python3 tools/build.py --pages-only
```

## Adding a score

Add a record to `catalogue/` in a pull request; the workflow checks that it
converts and validates.

A source with an error that LilyPond now refuses can be corrected in its
record, after `convert-ly`; the score's page says what was corrected:

```json
"fixes": [{"file": "25EF-01.ly", "replace": "f8'-5", "with": "f'8-5",
           "why": "bar 9, an octave mark after the duration (f8' for f'8)"}]
```

`replace` must occur exactly once in the file, so that a change of the
source stops the build rather than going unnoticed. Prefer fixing the source
upstream too.

## Adding a recording

Orchestrion shows videos of the open score being played. They are listed in
the score's record, best first: the first is the one whose thumbnail
Orchestrion shows, and the one a click plays.

```json
"recordings": [
  {"url": "https://www.youtube.com/watch?v=VIDEOID", "performer": "Name"},
  {"url": "https://youtu.be/VIDEOID?t=30", "performer": "Other name"},
  {"url": "https://youtu.be/VIDEOID?t=1m30s&end=3m12s", "performer": "Third name"}
]
```

`t=` is where the piece starts in the video, and `end=` where it stops, for
a piece that is only a part of it (a variation, a movement, one of a set).
`end=` is Orchestrion's: YouTube's own pages ignore it.

The build gathers them in `index.json`, under `recordings`, by score id. A
YouTube link, however it was copied, is written there as
`https://www.youtube.com/watch?v=ID[&t=SECONDS][&end=SECONDS]`, and plays in the app; any
other `https` link is opened in the browser. The build refuses an entry
Orchestrion couldn't use (no performer, a link that isn't `https`, a YouTube
link that isn't a video's, an end that isn't after the start) and the same
video twice. To also ask YouTube
whether the videos exist:

```sh
python3 tools/build.py --check-recordings
```

How the recordings are found and reviewed, and what makes one good enough:
[tools/recordings/README.md](tools/recordings/README.md).

### Its sync

A YouTube recording can have a **sync**: when it plays each beat of the
score, so that Orchestrion follows the video on the score and can play the
score at its tempo. It is tapped in Orchestrion (Advanced ▸ Sync video), which
saves it in its data folder; then

```sh
python3 tools/recordings/sync.py            # Orchestrion's syncs on this machine
python3 tools/recordings/sync.py PATH ...   # or these files or folders
```

checks each and copies it to `catalogue/sync/<score id>/<video id>.json`. The
build checks them again, publishes them as `sync/<score id>/<video id>.json`
and links each from its recording's entry in `index.json`
(`"sync": "sync/<id>/<video>.json"`). A sync is of a version of the score
(its bars are counted as played, repeats unrolled): if the score changes, the
build warns, and the sync is worth checking in the app.

## Licence

The code is GPL-3.0-or-later. Each score keeps the licence of its source
edition (most Mutopia editions are CC BY-SA, which requires the credits shown
on the score's page and in the file).
