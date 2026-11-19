# Finding recordings

How the catalogue's `recordings` (see *Adding a recording* in the top-level
README) are found: YouTube is searched for each piece, the candidates are
reviewed by hand on a page, and what was kept is written into the catalogue.

## What a recording must be

- **A human performance of the piece**: not a tutorial, a Synthesia or
  falling-notes video, a slow-practice video, a cover or a computer rendering.
  The channel *Bach Piano Scores* is out for that reason (`EXCLUDED_CHANNELS`
  in `find.py`).
- **Without ads.** Rudolf Buchbinder's K. 265 (`yHRQ8nIAiqw`) is the model:
  never an ad. `youtube.check()` reads it off the video's page, signed out:
  `"adPlacements"` in it means ads. Ads also depend on the viewer and can
  come later (a copyright claim), so it's a guide. Label uploads (Deutsche
  Grammophon, the "– Topic" channels, …) nearly always have them: famous
  pieces (Moonlight, Pathétique) have few ad-free videos.
- **Embeddable**: Orchestrion plays it in YouTube's embedded player.
- Then, by ear: the right piece (search results mix up pieces with close
  numbers), its repeats, few mistakes. Harpsichord and fortepiano are fine.

## Steps

1. **Find**: `python3 tools/recordings/find.py` searches the pieces that have
   no recordings and haven't been searched (`--only ID …` to search some
   again), and merges the result into `candidates.json`. It is paced for
   YouTube's rate limit (it answers 429 to requests that come too fast), and
   saves as it goes: stopped, it takes up where it was. Its checks are cached
   in `.cache/recordings/`.
2. **Review**: `python3 tools/recordings/review_page.py page.html`, then ask
   Claude to publish `page.html` to the review's artifact,
   **https://claude.ai/artifact/J7wDcFrULPt7FG8xBKgFqR** (publishing to that
   URL keeps the link and what was entered). On the page: tick the videos to
   keep, name the performer (it starts as the channel's name), give the start
   and end in the video where the piece is only a part of it, and write notes
   (a wrong piece, no repeats, …). It is all kept in the artifact's database,
   collection `review`, one document per piece id (`picked`: {video:
   {performer}}, `starts` and `ends`: {video: seconds}, `notes`: {video:
   text}, `note`, and `extra` for videos added by hand).
3. **Apply**: ask Claude to save the `review` collection to a directory
   (ArtifactData, `list` with `out_dir`), then
   `python3 tools/recordings/apply.py DIR` writes the ticked videos into the
   records, and `python3 tools/build.py --check-recordings` checks them.
   Where a piece has several, put the best first: it is the one Orchestrion
   shows and plays.

## Learnt along the way

- **A set recorded whole**: searched piece by piece, a video of a whole set
  (Laurens de Man's *Six Little Preludes, BWV 933-938*) came up for some of
  its pieces only. `find.py` offers such a video to every piece of the set,
  going by its title ("complete", "all movements", a range of numbers); each
  piece then gets its `t=` and `end=`. Going by the video coming up for several
  pieces doesn't work: for numbered exercises (Czerny, Hanon, Diabelli
  Op. 149) a video of one number comes up for many others.
- **Numbered exercises**: searches by number often return another number, or
  a selection. Check the number.
- **Duets**: Diabelli's Op. 149 and Op. 163 are four hands and the scores are
  the Primo: the videos are of both parts.
- **The variations of K. 265** aren't searched (`SKIP`): they are in the whole
  work's video, which only needs a `t=` and an `end=` per variation.
- One performer name per video, whatever the piece: the page keeps it so.
