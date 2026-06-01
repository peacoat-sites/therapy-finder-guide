#!/usr/bin/env python3
"""Image health self-heal: scan this site's articles for empty or dead (404) hero images
and auto-retrofit them with a fresh Pexels image. Runs in the repo checkout (filesystem),
the workflow commits any fixes. Reads PEXELS_API_KEY from env.
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

def pexels(query, used):
    if not PEXELS:
        return None
    try:
        url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({"query": query, "per_page": 15, "orientation": "landscape"})
        d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": PEXELS, "User-Agent": UA}), timeout=12).read())
        photos = [p for p in d.get("photos", []) if str(p["id"]) not in used]
        if not photos:
            return None
        p = random.choice(photos[:6]); used.add(str(p["id"]))
        return p["src"]["large"]
    except Exception:
        return None

if not PEXELS:
    print("PEXELS_API_KEY not set — skipping image health (no retrofit possible)")
    raise SystemExit(0)

used, checked, broken, fixed = set(), 0, 0, 0
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
    checked += 1
    if img_ok(url):
        continue
    broken += 1
    new_url = pexels(query_from_slug(slug), used) or pexels(slug.split("-")[0], used)
    if not new_url:
        print(f"  BROKEN (no replacement found): {slug}")
        continue
    if re.search(r"^image:.*$", text, re.M):
        text = re.sub(r"^image:.*$", f'image: "{new_url}"', text, count=1, flags=re.M)
    else:
        text = re.sub(r"^(title:.*)$", r"\1" + f'\nimage: "{new_url}"', text, count=1, flags=re.M)
    open(path, "w", encoding="utf-8").write(text)
    print(f"  fixed: {slug}")
    fixed += 1

print(f"\nImage health: checked {checked}, broken {broken}, fixed {fixed}")
