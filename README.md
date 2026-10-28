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
  credits, and where it comes from (for a Mutopia Project piece, its id, the
  source file and the MutopiaProject commit it is built from). The file name
  is the score's id, used in its URLs (`scores/<id>.mxl`, `s/<id>/`): chosen
  once, never renamed.
- `tools/build.py` fetches each source, updates it with `convert-ly`, converts
  it with [lilyport](https://github.com/saintmatthieu/lilyport), checks it
  (the licence its source declares, the MusicXML 4.0 schema), and writes the
  site into `_site/`: the scores, a page for each, the library page,
  `index.json`, and `sitemap.xml` and `robots.txt` for search engines.
- `index.json` is what Orchestrion and the web pages read. It has a
  `schemaVersion`; URLs in it are relative to it; each score's `version` is a
  hash of its MusicXML, so Orchestrion downloads a score again only when it
  changed.
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

## Adding a score

Add a record to `catalogue/` in a pull request; the workflow checks that it
converts and validates.

## Licence

The code is GPL-3.0-or-later. Each score keeps the licence of its source
edition (most Mutopia editions are CC BY-SA, which requires the credits shown
on the score's page and in the file).
