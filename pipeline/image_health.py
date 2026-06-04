#!/usr/bin/env python3
"""Image health self-heal: scan this site's articles for empty, dead (404), OR duplicate
hero images and auto-retrofit each with a fresh Pexels image not already used elsewhere on
the site. Runs in the repo checkout (filesystem); the workflow commits any fixes.
Reads PEXELS_API_KEY from env.
"""
import os, re, glob, random, json, urllib.request, urllib.parse

PEXELS = os.environ.get("PEXELS_API_KEY", "").strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
# slugs that legitimately have no hero image (quizzes / data-table pages)
SKIP = ("right-for-you", "ready-to", "ready-for-a-new-pet", "are-you-ready", "what-it-takes",
        "help-right-now", "properly-protected", "go-solar-this", "diy-or-call", "talking-to-someone")
FILLER = set("best how to the a an for what is are do does i my your of in on and with vs can should why when guide tips explained 2026 2025 you".split())

def query_from_slug(slug):
    words = [w for w in slug.split("-") if w not in FILLER]
    return " ".join(words[:4]) or slug.replace("-", " ")

def img_ok(url):
    if not url or not url.startswith("http"):
        return False
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=12)
        return r.getcode() == 200 and "image" in r.headers.get("content-type", "")
    except Exception:
        return False

def photo_id(url):
    m = re.search(r"/photos/(\d+)/", url or "")
    return m.group(1) if m else None

def pexels(query, used):
    if not PEXELS:
        return None
    try:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode(
            {"query": query, "per_page": 30, "orientation": "landscape"})
        d = json.loads(urllib.request.urlopen(urllib.request.Request(
            url, headers={"Authorization": PEXELS, "User-Agent": UA}), timeout=12).read())
        photos = [p for p in d.get("photos", []) if str(p["id"]) not in used]
        if not photos:
            return None
        p = random.choice(photos[:8]); used.add(str(p["id"]))
        return p["src"]["large"]
    except Exception:
        return None

if not PEXELS:
    print("PEXELS_API_KEY not set - skipping image health (no retrofit possible)")
    raise SystemExit(0)

# Load every article once
articles = []
for path in sorted(glob.glob("content/posts/*.md")):
    slug = os.path.basename(path)[:-3]
    if any(s in slug for s in SKIP):
        continue
    text = open(path, encoding="utf-8").read()
    if text.count("---") < 2:
        continue
    fm = text.split("---")[1]
    m = re.search(r'image:\s*"?([^"\n]*)"?', fm)
    url = m.group(1).strip() if m else ""
    articles.append([path, slug, text, url])

# Pass 1: keep the first valid occurrence of each photo; flag empty/dead AND duplicates
used, seen_ids = set(), set()
checked = broken = dup = fixed = 0
todo = []
for a in articles:
    path, slug, text, url = a
    checked += 1
    pid = photo_id(url)
    ok = img_ok(url)
    if ok and pid:
        if pid in seen_ids:
            dup += 1; todo.append((a, "duplicate"))
        else:
            seen_ids.add(pid); used.add(pid)
    elif ok and not pid:
        pass  # valid non-Pexels image (e.g. Flux) - keep; can't dedupe by id
    else:
        broken += 1; todo.append((a, "broken/empty"))

# Pass 2: reassign each flagged article to a fresh, site-unique image
for a, reason in todo:
    path, slug, text, url = a
    new = pexels(query_from_slug(slug), used) or pexels(slug.split("-")[0], used)
    if not new:
        print(f"  {reason} (no replacement found): {slug}")
        continue
    if re.search(r"^image:.*$", text, re.M):
        text = re.sub(r"^image:.*$", f'image: "{new}"', text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(title:.*)$", r"\1" + f'\nimage: "{new}"', text, count=1, flags=re.M)
    open(path, "w", encoding="utf-8").write(text)
    print(f"  {reason} fixed: {slug}")
    fixed += 1

print(f"\nImage health: checked {checked}, broken/empty {broken}, duplicates {dup}, fixed {fixed}")
