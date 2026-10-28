#!/usr/bin/env python3
"""Measures of how hard a score is to play in Orchestrion, from its MusicXML.

Two axes, after Orchestrion's two ways of playing:

- rhythm: the player gives each hand's rhythm (one hand per staff, its voices
  combined), pressing at onsets and releasing at offsets, each voice's
  separately; the score supplies the pitches.
- notes: the player plays the notes themselves.

Each axis gets a level on G. Henle Verlag's scale (1-9), from a model fitted
by tools/calibrate.py to Mutopia pieces Henle has graded
(tools/difficulty-calibration.tsv) and kept in tools/difficulty-model.json.
Henle grades a piece as a whole, so the model is fitted to that: level =
intercept + pace + rhythm + notes, where pace is how fast the piece goes (its
rate of actions for the rhythm axis, of notes for the notes axis) and rhythm
and notes are the other measures of each axis. A piece's rhythm level is then
intercept + pace + 2 rhythm (what its level would be if its notes were as hard
as its rhythm), and likewise for notes; their mean is the fitted level.

    python3 tools/difficulty.py _site/scores/*.mxl   # measures and levels

Prior work it draws on (see README): onset density at tempo, as in Nakamura et
al.'s performance-difficulty measures (2018); syncopation after Longuet-Higgins
& Lee (1984) and Gómez et al. (2005); duration variety after Toussaint's
surveys of rhythm complexity (2008, 2013); hand span, chords, leaps and
polyphony as in Sébastien et al.'s Score Analyzer (2012) and Ramoneda et al.
(2022, 2024). Weighting timed releases (offsets) is our own addition.
"""
import bisect
import json
import math
import sys
import zipfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

# Releases are actions of their own when they don't fall on one of the
# hand's presses, but they count less than a press. A note merely not held to
# the next counts little (played legato, few notice); a staccato counts much
# more, being part of the expression.
RELEASE_WEIGHT = 0.15
STACCATO_WEIGHT = 0.8
# How much of its value a staccato note sounds.
STACCATO_LENGTH = {"staccato": Fraction(1, 2), "spiccato": Fraction(1, 2),
                   "staccatissimo": Fraction(1, 4)}
# Window over which densities are taken, and the percentile kept: what the
# hardest passages ask, not the average.
WINDOW_SECONDS = 3.0
PERCENTILE = 0.9
DEFAULT_TEMPO = 60.0
# Notes within this time of each other are played from one hand position.
POSITION_SECONDS = 0.4
# Beats per minute a tempo word usually means, for scores without a metronome
# mark (many Mutopia sources): (word, beats per minute), first match wins.
TEMPO_WORDS = [
    ("prestissimo", 200), ("presto", 176), ("vivacissimo", 168), ("vivace", 152),
    ("vivo", 152), ("allegro molto", 150), ("allegro assai", 146), ("allegretto", 112),
    ("allegro moderato", 116), ("allegro", 132), ("agitato", 126), ("andantino", 88),
    ("andante", 80), ("adagietto", 72), ("adagio", 66), ("larghetto", 56),
    ("largo", 46), ("lento", 54), ("grave", 40), ("sostenuto", 66), ("moderato", 104),
    ("très lent", 44), ("lent", 54), ("modéré", 100), ("très vif", 168), ("vif", 150),
    ("animé", 132), ("langsam", 60), ("mässig", 100), ("massig", 100), ("lebhaft", 140),
    ("schnell", 156), ("rasch", 150), ("not fast", 76), ("slow", 60), ("fast", 140),
    ("tempo di valse", 160), ("tempo di minuetto", 120),
]
MODEL = Path(__file__).resolve().parent / "difficulty-model.json"
# How each measure enters the model (its range is the calibration set's).
TRANSFORMS = {"r_rate": math.log1p, "n_rate": math.log1p, "n_shift": math.log1p,
              "n_span": lambda x: x / 12}
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def musicxml_root(path):
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(e for e in container.iter() if e.tag.endswith("rootfile"))
        return ET.fromstring(archive.read(rootfile.get("full-path")))


class Note:
    __slots__ = ("staff", "voice", "start", "end", "pitch", "grace", "staccato")

    def __init__(self, staff, voice, start, end, pitch, grace):
        self.staff, self.voice, self.start, self.end = staff, voice, start, end
        self.pitch, self.grace = pitch, grace
        # The release of a staccato note, else None.
        self.staccato = None


def read_score(root):
    """The notes (times in quarter notes, ties merged), the tempo map, and the
    bars (start, length, beat length, compound) of the first part's meter."""
    notes, tempos, bars, words = [], [], [], []
    for number, part in enumerate(root.findall("part")):
        divisions, time, beats, beat_type = 1, Fraction(0), 4, 4
        open_ties = {}  # (staff, pitch) -> the note a tie continues
        bar_number = 0
        last_start = Fraction(0)
        for measure in part.findall("measure"):
            bar_start, bar_end = time, time
            bar_number += 1
            for element in measure:
                if element.tag == "attributes":
                    if element.find("divisions") is not None:
                        divisions = int(element.findtext("divisions"))
                    if element.find("time/beats") is not None:
                        beats = int(element.findtext("time/beats").split("+")[0])
                        beat_type = int(element.findtext("time/beat-type"))
                elif element.tag in ("backup", "forward"):
                    step = Fraction(int(element.findtext("duration")), divisions)
                    time += -step if element.tag == "backup" else step
                elif element.tag in ("sound", "direction"):
                    if number == 0 and element.tag == "direction" and bar_number <= 2:
                        words.extend(w.text or "" for w in element.iter("words"))
                    sound = element if element.tag == "sound" else element.find("sound")
                    if number == 0 and sound is not None and sound.get("tempo"):
                        tempos.append((time, float(sound.get("tempo"))))
                elif element.tag == "note":
                    grace = element.find("grace") is not None
                    duration = Fraction(int(element.findtext("duration", "0")), divisions)
                    start = last_start if element.find("chord") is not None else time
                    if element.find("chord") is None and not grace:
                        time += duration
                    last_start = start
                    bar_end = max(bar_end, time)
                    pitch_element = element.find("pitch")
                    if pitch_element is None:
                        continue
                    pitch = (12 * (int(pitch_element.findtext("octave")) + 1)
                             + STEPS[pitch_element.findtext("step")]
                             + int(float(pitch_element.findtext("alter", "0"))))
                    staff = (number, int(element.findtext("staff", "1")))
                    voice = element.findtext("voice", "1")
                    ties = {t.get("type") for t in element.findall("tie")}
                    if "stop" in ties and (staff, pitch) in open_ties:
                        note = open_ties.pop((staff, pitch))
                        note.end = start + duration
                    else:
                        note = Note(staff, voice, start, start + duration, pitch, grace)
                        notes.append(note)
                    if "start" in ties:
                        open_ties[(staff, pitch)] = note
                    marks = {a.tag for a in element.findall("notations/articulations/*")}
                    lengths = [STACCATO_LENGTH[m] for m in marks if m in STACCATO_LENGTH]
                    note.staccato = start + duration * min(lengths) if lengths else None
            if number == 0:
                length = Fraction(beats * 4, beat_type)
                compound = beat_type == 8 and beats % 3 == 0 and beats > 3
                beat = Fraction(3 * 4, beat_type) if compound else Fraction(4, beat_type)
                bars.append((bar_start, max(bar_end - bar_start, Fraction(0)) or length,
                             beat, compound))
                time = bar_start + (bar_end - bar_start)
    tempos.sort()
    if not tempos or tempos[0][0] > 0 or tempos[0][1] == DEFAULT_TEMPO:
        # No metronome mark at the start, or LilyPond's default, which is what
        # lilyport writes when the source has none: go by the tempo word.
        estimate = word_tempo(" ".join(words), bars[0] if bars else None)
        if estimate:
            tempos = [(t, q) for t, q in tempos if t > 0]
            tempos.insert(0, (Fraction(0), estimate))
        elif not tempos or tempos[0][0] > 0:
            tempos.insert(0, (Fraction(0), DEFAULT_TEMPO))
    return notes, tempos, bars


def word_tempo(text, bar):
    """Quarter notes per minute that a tempo word suggests, or None."""
    text = text.lower()
    for word, bpm in TEMPO_WORDS:
        if word in text:
            beat = bar[2] if bar else Fraction(1)
            if not bar[3] and bar[1] == 4 and beat == 1 and "alla breve" in text:
                beat = Fraction(2)
            return float(bpm * beat)
    return None


class Clock:
    """Quarter-note times to seconds, along the tempo map (quarters/minute)."""

    def __init__(self, tempos):
        self.starts, self.seconds, self.rates = [], [], []
        elapsed = 0.0
        for i, (start, tempo) in enumerate(tempos):
            if i:
                elapsed += float(start - tempos[i - 1][0]) * 60 / tempos[i - 1][1]
            self.starts.append(start)
            self.seconds.append(elapsed)
            self.rates.append(60 / tempo)

    def __call__(self, time):
        i = bisect.bisect_right(self.starts, time) - 1
        return self.seconds[i] + float(time - self.starts[i]) * self.rates[i]


BINARY, TERNARY = (2, 4, 8), (3, 6, 12)


def bar_of(time, bars):
    return bars[max(bisect.bisect_right([b[0] for b in bars], time) - 1, 0)]


def grid(time, bars):
    """A position's metrical depth (0 a downbeat, 1 a beat, 2-4 finer
    subdivisions, 5 none of these) and the beat subdivision it lies on
    ("binary", "ternary" or None), triplets being as regular as duplets."""
    start, _, beat, compound = bar_of(time, bars)
    x = time - start
    if x == 0:
        return 0, None
    if (x / beat).denominator == 1:
        return 1, None
    first, second = (TERNARY, BINARY) if compound else (BINARY, TERNARY)
    for kind in (first, second):
        for level, d in enumerate(kind, start=2):
            if (x / (beat / d)).denominator == 1:
                return level, "binary" if kind is BINARY else "ternary"
    return 5, None


def silent_strongest(a, b, bars, heard, below):
    """The strongest metrical depth, stronger than below, strictly between a
    and b at which no onset is heard (6 if none)."""
    i = max(bisect.bisect_right([bar[0] for bar in bars], a) - 1, 0)
    while i < len(bars) and bars[i][0] < b:
        start, length, beat, compound = bars[i]
        steps = [length, beat] + [beat / d for d in (TERNARY if compound else BINARY)]
        for level, step in enumerate(steps[:below]):
            k = math.floor((a - start) / step) + 1
            t = start + k * step
            while t < min(b, start + length):
                if t not in heard:
                    return level
                t += step
        i += 1
    return 6


def percentile(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(int(p * len(values)), len(values) - 1)]


def windowed_rate(events, total_seconds):
    """Weighted events per second, at the PERCENTILE of sliding windows."""
    events = sorted(events)
    times = [t for t, _ in events]
    rates, t = [], 0.0
    while t < max(total_seconds - WINDOW_SECONDS, 0) + 1e-9:
        lo = bisect.bisect_left(times, t)
        hi = bisect.bisect_left(times, t + WINDOW_SECONDS)
        rates.append(sum(w for _, w in events[lo:hi]) / WINDOW_SECONDS)
        t += WINDOW_SECONDS / 4
    return percentile(rates, PERCENTILE)


def hand_events(notes):
    """A hand's actions: its distinct onsets (weight 1; grace notes each
    count) and its releases: each staccato, and each other release that
    doesn't fall on one of its onsets (voices release separately; releases at
    the same moment are one action)."""
    onsets = sorted({n.start for n in notes if not n.grace})
    onset_set = set(onsets)
    events = [(t, 1.0) for t in onsets]
    events += [(n.start, 1.0) for n in notes if n.grace]
    releases = {}
    for n in notes:
        if n.grace:
            continue
        if n.staccato is not None:
            releases[n.staccato] = STACCATO_WEIGHT
        elif n.end not in onset_set:
            releases.setdefault(n.end, RELEASE_WEIGHT)
    last = max(releases, default=None)
    if last is not None and releases[last] == RELEASE_WEIGHT:
        del releases[last]  # the final release is free
    events += list(releases.items())
    return onsets, sorted(releases), events


def entropy(values):
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    total = sum(counts.values())
    return -sum(c / total * math.log2(c / total) for c in counts.values()) if total else 0.0


def measures(path):
    notes, tempos, bars = read_score(musicxml_root(path))
    clock = Clock(tempos)
    total = clock(max(n.end for n in notes))
    hands = {}
    for note in notes:
        hands.setdefault(note.staff, []).append(note)

    rhythm_events, note_events, hand_info = [], [], {}
    sync, hand_sync, irregular, onset_count, ioi_entropy = 0.0, 0.0, 0, 0, []
    span90, chord_mean, shift_rate, held = [], [], [], 0
    heard = {n.start for n in notes if not n.grace}
    beat_kinds = {}  # (staff, beat start) -> the subdivisions used there
    for staff, hand in hands.items():
        onsets, releases, events = hand_events(hand)
        hand_info[staff] = ({t for t, _ in events}, events)
        rhythm_events += [(clock(t), w) for t, w in events]
        note_events += [(clock(n.start), 1.0) for n in hand]
        # Syncopation: an onset on a weak position, with a stronger position
        # before the hand's next action, is felt against the beat.
        actions = sorted(set(onsets) | set(releases))
        for t in onsets:
            d, kind = grid(t, bars)
            following = actions[bisect.bisect_right(actions, t):]
            nxt = following[0] if following else t
            # Against the hand's own rhythm, and as heard (no hand playing
            # the stronger position).
            hand_sync += max(d - silent_strongest(t, nxt, bars, set(onsets), d), 0)
            sync += max(d - silent_strongest(t, nxt, bars, heard, d), 0)
            irregular += d == 5
            onset_count += 1
            if kind:
                start, _, beat, _ = bar_of(t, bars)
                beat_start = start + math.floor((t - start) / beat) * beat
                beat_kinds.setdefault((staff, beat_start), set()).add(kind)
        iois = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
        ioi_entropy.append(entropy([str(x) for x in iois]))
        # Notes: chords and their span, how fast the hand moves, and notes
        # held while the same hand plays others.
        by_start = {}
        for n in hand:
            if not n.grace:
                by_start.setdefault(n.start, []).append(n.pitch)
        starts = sorted(by_start)
        chord_mean.append(sum(len(by_start[s]) for s in starts) / max(len(starts), 1))
        span90.append(percentile([max(p) - min(p) for p in by_start.values()], PERCENTILE))
        # Shifts: how far beyond an octave the notes played within
        # POSITION_SECONDS reach, i.e. how far the hand must move.
        shifts, seconds = [], [clock(s) for s in starts]
        for i, s in enumerate(starts):
            j = i
            while j > 0 and seconds[i] - seconds[j - 1] <= POSITION_SECONDS:
                j -= 1
            pitches = [p for k in range(j, i + 1) for p in by_start[starts[k]]]
            shifts.append((clock(s), max(max(pitches) - min(pitches) - 12, 0)))
        shift_rate.append(windowed_rate(shifts, total))
        ends = sorted((n.start, n.end) for n in hand if not n.grace)
        for s in starts:
            held += any(a < s < e for a, e in ends)

    # Independence: the weighted share of a hand's actions that the other hand
    # doesn't share.
    if len(hand_info) >= 2:
        shared_total, alone = 0.0, 0.0
        staffs = list(hand_info)
        for staff in staffs:
            others = set().union(*(hand_info[s][0] for s in staffs if s != staff))
            for t, w in hand_info[staff][1]:
                shared_total += w
                alone += w * (t not in others)
        independence = alone / shared_total
    else:
        independence = 0.0
    # Cross-rhythm: beats where the hands, or one hand, mix duplets and
    # triplets, among the beats with subdivisions.
    by_beat = {}
    for (staff, beat_start), kinds in beat_kinds.items():
        by_beat.setdefault(beat_start, set()).update(kinds)
    cross = sum(len(k) > 1 for k in by_beat.values()) / max(len(by_beat), 1)

    return {
        "tempo": tempos[0][1],
        "seconds": round(total),
        "r_rate": windowed_rate(rhythm_events, total),
        "r_indep": independence,
        "r_sync": sync / max(onset_count, 1),
        "r_hsync": hand_sync / max(onset_count, 1),
        "r_offgrid": irregular / max(onset_count, 1),
        "r_cross": cross,
        "r_ioi_h": max(ioi_entropy, default=0),
        "n_rate": windowed_rate(note_events, total),
        "n_chord": max(chord_mean, default=0),
        "n_span": max(span90, default=0),
        "n_shift": max(shift_rate, default=0),
        "n_held": held / max(sum(1 for n in notes if not n.grace), 1),
    }


def scaled(model, name, m):
    """A measure as the model takes it: transformed, then 0-1 over the range
    it had in the calibration set."""
    lo, hi = model["range"][name]
    x = TRANSFORMS.get(name, lambda v: v)(m[name])
    return (x - lo) / (hi - lo) if hi > lo else 0.0


def contributions(model, m):
    """The pace of each axis, and the sum of each axis' other terms."""
    pace = {axis: model["pace"] * scaled(model, rate, m)
            for axis, rate in (("rhythm", "r_rate"), ("notes", "n_rate"))}
    rest = {axis: sum(w * scaled(model, name, m) for name, w in model[axis].items())
            for axis in ("rhythm", "notes")}
    return pace, rest


def levels(m, model=None):
    """The rhythm and notes levels (1-9) of a score's measures."""
    model = model or json.loads(MODEL.read_text(encoding="utf-8"))
    pace, rest = contributions(model, m)
    return {axis: min(max(round(model["intercept"] + pace[axis] + 2 * rest[axis]), 1), 9)
            for axis in ("rhythm", "notes")}


def main():
    rows = [(Path(p).stem, measures(p)) for p in sys.argv[1:]]
    model = json.loads(MODEL.read_text(encoding="utf-8")) if MODEL.exists() else None
    keys = list(rows[0][1])
    print(f"{'score':30}" + "".join(f"{k:>9}" for k in keys) + ("   rhythm notes" if model else ""))
    for name, m in rows:
        line = f"{name:30}" + "".join(f"{m[k]:9.2f}" for k in keys)
        if model:
            lv = levels(m, model)
            line += f"   {lv['rhythm']:6} {lv['notes']:5}"
        print(line)


if __name__ == "__main__":
    main()
