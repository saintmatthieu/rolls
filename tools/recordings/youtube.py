"""YouTube, as tools/recordings asks it: a search's videos, and whether a
video shows ads and can be embedded. Scraped from YouTube's pages, signed out:
there is no API key involved, and YouTube may change its pages.

Standard library only.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "en-US",
}


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def search(query, count=12):
    """The first videos YouTube finds for the query: dicts of id, title,
    channel, length ("12:30") and views ("252,661 views")."""
    page = fetch("https://www.youtube.com/results?search_query=" +
                 urllib.parse.quote(query) + "&hl=en&gl=US")
    match = re.search(r"var ytInitialData = (\{.*?\});</script>", page)
    if not match:
        raise RuntimeError("YouTube's results page has no ytInitialData")
    videos = []

    def walk(node):
        if isinstance(node, dict):
            if "videoRenderer" in node:
                video = node["videoRenderer"]
                runs = lambda key: "".join(run.get("text", "") for run in video.get(key, {}).get("runs", []))
                videos.append({
                    "id": video.get("videoId"),
                    "title": runs("title"),
                    "channel": runs("ownerText"),
                    "length": video.get("lengthText", {}).get("simpleText", ""),
                    "views": video.get("viewCountText", {}).get("simpleText", ""),
                })
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(match.group(1)))
    return [video for video in videos if video["id"]][:count]


def check(video, retries=(2, 10, 30, 90)):
    """{"ads": bool, "embed": bool} for the video, from its watch page as
    YouTube serves it signed out, from the US: "adPlacements" in it means
    YouTube would show ads before or during it (Rudolf Buchbinder's K. 265,
    yHRQ8nIAiqw, has none; most label uploads have them). Ads also depend on
    the viewer and change over time (a copyright claim can add them): it's a
    good guide, not a promise. YouTube answers 429 to requests that come too
    fast: this waits and tries again; keep a second or two between calls."""
    for wait in (*retries, None):
        try:
            page = fetch("https://www.youtube.com/watch?v=" + video + "&hl=en&gl=US")
            return {"ads": '"adPlacements"' in page,
                    "embed": '"playableInEmbed":true' in page}
        except urllib.error.HTTPError as error:
            if error.code != 429 or wait is None:
                raise
            time.sleep(wait)
