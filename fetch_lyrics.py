#!/usr/bin/env python3
"""Reads the requests sheet, finds lyrics on Jyrics for new songs, saves them to lyrics.json.
Standard library only. Never overwrites an existing lyrics.json entry."""
import csv, difflib, html, io, json, os, re, sys, time
import urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (chug-lyrics-bot)"}
RETRY_DAYS = 3     # wait this long before searching again for a song that wasn't found
DELAY = 2          # seconds between requests, to be polite to the site


def key(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())


def clean(t):
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"\s[-–—]\s.*$", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def load(name, default):
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


def save(name, data):
    with open(os.path.join(ROOT, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_sheet(text):
    songs = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or not re.search(r"\d", row[0]):
            continue                                   # header or blank row
        title = row[2].strip()
        if title and key(title) not in songs:
            songs[key(title)] = title
    return songs


def strip_tags(h):
    return html.unescape(re.sub(r"<[^>]+>", "", h))


def search(q):
    page = fetch("https://www.jyrics.com/?s=" + urllib.parse.quote(q))   # WordPress-style search
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(https://www\.jyrics\.com/lyrics/[^"#?]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        url, title = m.group(1), re.sub(r"\s+", " ", strip_tags(m.group(2))).strip()
        if title and url not in seen:
            seen.add(url)
            out.append((url, title))
    return out


def score(result_title, song_title):
    a = key(re.sub(r"[^\x00-\x7f]+", " ", result_title))   # Latin part of "Yesimcha – ישימך"
    b = key(clean(song_title))
    if not a or not b:
        return 0
    if a == b or (min(len(a), len(b)) >= 5 and (a in b or b in a)):
        return 1
    return difflib.SequenceMatcher(None, a, b).ratio()


def lyrics_from_page(page):
    text = ""
    i = page.lower().rfind("<h1")                      # lyrics follow the page's last <h1>
    if i >= 0:
        body = page[i:]
        body = body[body.find("</h1>") + 5:]
        m = re.search(r"<img|<ul|<footer|<nav|<script", body, re.I)
        if m:
            body = body[:m.start()]
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
        body = re.sub(r"</p>", "\n\n", body, flags=re.I)
        text = strip_tags(body)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:                                 # fallback: description tag (loses line breaks)
        m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', page, re.I)
        if m:
            text = html.unescape(m.group(1)).strip()
    return text


def find(title):
    words = clean(title).split()
    for q in dict.fromkeys([" ".join(words), " ".join(words[:2])]):
        if not q:
            continue
        results = search(q)
        time.sleep(DELAY)
        best = max(results, key=lambda r: score(r[1], title), default=None)
        if best and score(best[1], title) >= 0.75:      # better no lyrics than the wrong song
            text = lyrics_from_page(fetch(best[0]))
            time.sleep(DELAY)
            if text:
                return {"text": text, "source": "Jyrics", "url": best[0]}
    return None


def main():
    cfg = load("config.json", {})
    url = os.environ.get("SHEET_CSV_URL") or cfg.get("sheet_csv_url", "")
    if not url.startswith("http"):
        sys.exit("Set sheet_csv_url in config.json")
    songs = parse_sheet(fetch(url))
    db, misses = load("lyrics.json", {}), load("misses.json", {})
    have = {key(k) for k in db}
    now = datetime.now(timezone.utc)
    for k, title in songs.items():
        if k in have:
            continue
        last = misses.get(k)
        if last and now - datetime.fromisoformat(last) < timedelta(days=RETRY_DAYS):
            continue
        try:
            entry = find(title)
        except Exception as e:                          # network hiccup: try again next run
            print("skip", title, e)
            continue
        if entry:
            db[title] = entry
            misses.pop(k, None)
            print("added", title)
        else:
            misses[k] = now.isoformat()
            print("not found", title)
    save("lyrics.json", db)
    save("misses.json", misses)


if __name__ == "__main__":
    main()
