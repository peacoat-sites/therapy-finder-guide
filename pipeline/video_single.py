#!/usr/bin/env python3
"""
Single-site YouTube Shorts Generator — runs inside a site repo via GitHub Actions.

Reads the latest article from content/posts/ (local checkout),
generates a 60-second Short via Claude + ElevenLabs + Shotstack,
then uploads to YouTube and commits data/youtube.json back to the repo.

Called by .github/workflows/video.yml in each site repo.
All config comes from environment variables (GitHub Actions secrets/vars).
"""

import os, sys, json, time, re, subprocess
from pathlib import Path
from datetime import datetime, timezone

import requests

# ── Required env vars (set as GitHub Actions secrets / vars) ─────────────────
ANTHROPIC_KEY        = os.environ["ANTHROPIC_API_KEY"]
EL_KEY               = os.environ["ELEVENLABS_API_KEY"]
EL_VOICE_ID          = os.environ["ELEVENLABS_VOICE_ID"]
SHOTSTACK_KEY        = os.environ["SHOTSTACK_API_KEY"]
SHOTSTACK_ENV        = os.environ.get("SHOTSTACK_ENV", "sandbox")
PEXELS_KEY           = os.environ["PEXELS_API_KEY"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
YOUTUBE_CHANNEL_ID   = os.environ["YOUTUBE_CHANNEL_ID"]
SITE_SLUG            = os.environ["SITE_NAME"]          # e.g. medicare-starter
SITE_DOMAIN          = os.environ["SITE_DOMAIN"]        # e.g. medicarestarter.com
SITE_NICHE           = os.environ.get("SITE_NICHE", "")

SHOTSTACK_BASE = (
    "https://api.shotstack.io/stage" if SHOTSTACK_ENV == "sandbox"
    else "https://api.shotstack.io/v1"
)

DRY_RUN = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "").lower() == "true"


# ── Step 1: Read latest article from local checkout ───────────────────────────

def read_latest_article() -> dict | None:
    posts_dir = Path("content/posts")
    if not posts_dir.exists():
        print(f"  [WARN] content/posts/ not found")
        return None

    md_files = sorted(
        [f for f in posts_dir.glob("*.md") if f.name != "_index.md"],
        key=lambda f: f.name,
        reverse=True,
    )
    if not md_files:
        print("  [WARN] No posts found")
        return None

    latest = md_files[0]
    raw = latest.read_text(encoding="utf-8")

    # Parse title from TOML or YAML front matter
    title = "Untitled"
    m = re.search(r'^title\s*=\s*"(.+?)"', raw, re.MULTILINE)
    if not m:
        m = re.search(r'^title:\s*(.+)', raw, re.MULTILINE)
    if m:
        title = m.group(1).strip().strip('"')

    # Strip front matter
    body = re.sub(r'^\+\+\+.*?\+\+\+', '', raw, flags=re.DOTALL).strip()
    body = re.sub(r'^---.*?---', '', body, flags=re.DOTALL).strip()

    article_slug = latest.stem
    print(f"  Article file: {latest.name}")
    return {"title": title, "body": body, "article_slug": article_slug}


# ── Step 2: Generate script via Claude ───────────────────────────────────────

def generate_script(article: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    system = (
        f"You are writing a 60-second YouTube Shorts script for a {SITE_NICHE} channel. "
        "The content must be concise, punchy, and scroll-stopping. "
        "Respond ONLY with valid JSON — no markdown fences, no commentary."
    )
    user = (
        f"Write a YouTube Shorts script based on this article:\n\n"
        f"Title: {article['title']}\n\n"
        f"Article (first 1200 chars):\n{article['body'][:1200]}\n\n"
        "Return JSON with these exact keys:\n"
        '{\n'
        '  "hook": "2-sentence hook that grabs attention in 3 seconds",\n'
        '  "points": ["tip 1 (~15 words)", "tip 2 (~15 words)", "tip 3 (~15 words)"],\n'
        '  "cta": "Single sentence CTA mentioning the website",\n'
        '  "title": "YouTube title under 80 chars with a number or power word",\n'
        '  "description": "2-3 sentence YouTube description under 400 chars",\n'
        '  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"]\n'
        "}\n\n"
        f"The CTA should mention {SITE_DOMAIN} naturally. "
        "Total spoken word count must be under 160 words."
    )

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=800,
        system=system,
        messages=[{"role": "user", "content": user}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw).rstrip('`').strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Could not parse Claude response: {raw[:300]}")


# ── Step 3: Synthesize audio ──────────────────────────────────────────────────

def synthesize_audio(script: dict) -> bytes:
    parts = [script["hook"]] + script["points"] + [script["cta"]]
    full_text = "  ".join(parts)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}"
    r = requests.post(
        url,
        headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
        json={
            "text": full_text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True},
            "output_format": "mp3_44100_128",
        },
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
    return r.content


# ── Step 4: Fetch Pexels clips ────────────────────────────────────────────────

def fetch_pexels_clips(count: int = 4) -> list:
    niche_words = SITE_NICHE.replace(" & ", " ").replace(",", "").split()
    queries = [
        SITE_NICHE,
        " ".join(niche_words[:2]) if len(niche_words) >= 2 else niche_words[0],
        "professional advice",
        "helping people",
    ]
    selected, used_ids = [], set()
    headers = {"Authorization": PEXELS_KEY}

    for query in queries:
        if len(selected) >= count:
            break
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": query, "per_page": 8, "orientation": "portrait", "size": "medium"},
        )
        if r.status_code != 200:
            continue
        for vid in r.json().get("videos", []):
            if len(selected) >= count or vid["id"] in used_ids:
                continue
            files = vid.get("video_files", [])
            portrait = [f for f in files if f.get("width", 1) < f.get("height", 1) and f.get("height", 0) >= 720]
            chosen = sorted(portrait or files, key=lambda f: f.get("height", 0), reverse=True)
            if chosen:
                selected.append(chosen[0]["link"])
                used_ids.add(vid["id"])

    return selected[:count]


# ── Step 5: Upload audio ──────────────────────────────────────────────────────

def upload_audio(audio_bytes: bytes) -> str:
    r = requests.post(
        "https://tmpfiles.org/api/v1/upload",
        files={"file": ("narration.mp3", audio_bytes, "audio/mpeg")},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"tmpfiles {r.status_code}: {r.text[:150]}")
    url = r.json()["data"]["url"]
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")


# ── Step 6 & 7: Shotstack render ─────────────────────────────────────────────

def build_and_render(script: dict, clips: list, audio_url: str) -> str:
    clip_duration = 15.0
    text_clips, video_clips = [], []
    bg = "#000000CC"

    for i, url in enumerate(clips):
        start = i * clip_duration
        video_clips.append({
            "asset": {"type": "video", "src": url, "volume": 0},
            "start": start, "length": clip_duration, "fit": "cover",
        })
        label = ([script["hook"]] + script["points"] + [script["cta"]])[i]
        if len(label) > 120:
            label = label[:117] + "..."
        text_clips.append({
            "asset": {
                "type": "html",
                "html": (
                    f'<p style="font-family:Arial,sans-serif;font-size:36px;font-weight:bold;'
                    f'color:#FFFFFF;text-align:center;padding:20px;background:{bg};'
                    f'border-radius:12px;line-height:1.4;">{label}</p>'
                ),
                "width": 900, "height": 300, "css": "p{margin:0}",
            },
            "start": start, "length": clip_duration,
            "position": "bottom", "offset": {"y": 0.15},
            "transition": {"in": "fade", "out": "fade"},
        })

    payload = {
        "timeline": {
            "tracks": [{"clips": video_clips}, {"clips": text_clips}],
            "soundtrack": {"src": audio_url, "effect": "fadeOut", "volume": 0.0},
            "background": "#000000",
        },
        "output": {
            "format": "mp4", "resolution": "hd", "aspectRatio": "9:16",
            "fps": 30, "size": {"width": 1080, "height": 1920},
        },
    }

    r = requests.post(
        f"{SHOTSTACK_BASE}/render",
        headers={"x-api-key": SHOTSTACK_KEY, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"Shotstack submit: {data}")
    render_id = data["response"]["id"]
    print(f"  Render ID: {render_id}")

    # Poll
    for _ in range(30):  # up to 5 min
        time.sleep(10)
        r2 = requests.get(f"{SHOTSTACK_BASE}/render/{render_id}", headers={"x-api-key": SHOTSTACK_KEY}, timeout=15)
        resp = r2.json().get("response", {})
        status = resp.get("status")
        print(f"  Status: {status}")
        if status == "done":
            return resp["url"]
        elif status in ("failed", "error"):
            raise RuntimeError(f"Shotstack failed: {resp.get('error')}")

    raise TimeoutError("Shotstack timed out")


# ── Step 8: YouTube upload ────────────────────────────────────────────────────

def get_access_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    })
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Token refresh failed: {d}")
    return d["access_token"]


def upload_to_youtube(video_bytes: bytes, script: dict, article: dict) -> str:
    token = get_access_token()
    title = script["title"][:100]
    desc = (
        script["description"]
        + f"\n\n Full article: https://{SITE_DOMAIN}/{article['article_slug']}/\n"
        + "#Shorts"
    )[:5000]
    tags = script["tags"][:15] + ["Shorts", SITE_NICHE]

    meta = {
        "snippet": {"title": title, "description": desc, "tags": tags, "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    init_r = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(len(video_bytes)),
        },
        json=meta, timeout=30,
    )
    if init_r.status_code not in (200, 201):
        raise RuntimeError(f"YouTube init {init_r.status_code}: {init_r.text[:200]}")

    upload_url = init_r.headers["Location"]
    up_r = requests.put(
        upload_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
        data=video_bytes, timeout=300,
    )
    if up_r.status_code not in (200, 201):
        raise RuntimeError(f"YouTube upload {up_r.status_code}: {up_r.text[:200]}")

    return up_r.json()["id"]


# ── Step 9: Commit data/youtube.json ─────────────────────────────────────────

def commit_youtube_json(article: dict, script: dict, video_id: str):
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    json_path = data_dir / "youtube.json"

    existing = json.loads(json_path.read_text()) if json_path.exists() else []
    entry = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/shorts/{video_id}",
        "title": script["title"],
        "article_slug": article["article_slug"],
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    existing.insert(0, entry)
    json_path.write_text(json.dumps(existing, indent=2))

    subprocess.run(["git", "config", "user.email", "pipeline@peacoat.dev"], check=True)
    subprocess.run(["git", "config", "user.name", "Peacoat Pipeline"], check=True)
    subprocess.run(["git", "add", "data/youtube.json"], check=True)
    subprocess.run(["git", "commit", "-m", f"Add YouTube Short: {script['title'][:60]}"], check=True)
    subprocess.run(["git", "push"], check=True)
    print(f"  Committed data/youtube.json")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"VIDEO PIPELINE: {SITE_SLUG}")
    print(f"{'='*60}")
    if DRY_RUN:
        print("[DRY RUN] Will stop after audio synthesis")

    print("STEP 1: Reading latest article...")
    article = read_latest_article()
    if not article:
        print("[ABORT] No article found")
        sys.exit(1)
    print(f"  Title: {article['title']}")

    print("STEP 2: Generating script via Claude...")
    script = generate_script(article)
    print(f"  YT Title: {script['title']}")
    print(f"  Hook: {script['hook'][:80]}...")

    print("STEP 3: Synthesizing audio via ElevenLabs...")
    audio = synthesize_audio(script)
    print(f"  Audio: {len(audio)/1024:.1f} KB")

    if DRY_RUN:
        Path("narration.mp3").write_bytes(audio)
        Path("script.json").write_text(json.dumps(script, indent=2))
        print("[DRY RUN] Saved narration.mp3 and script.json — done.")
        return

    print("STEP 4: Fetching Pexels B-roll clips...")
    clips = fetch_pexels_clips(count=4)
    print(f"  Found {len(clips)} clips")

    print("STEP 5: Uploading audio...")
    audio_url = upload_audio(audio)
    print(f"  URL: {audio_url[:60]}...")

    print("STEP 6-7: Building + rendering via Shotstack...")
    video_url = build_and_render(script, clips, audio_url)
    print(f"  Done: {video_url[:60]}...")

    print("STEP 8: Downloading video...")
    video_bytes = requests.get(video_url, timeout=120).content
    print(f"  {len(video_bytes)/1024/1024:.1f} MB")

    print("STEP 9: Uploading to YouTube...")
    video_id = upload_to_youtube(video_bytes, script, article)
    print(f"  Published: https://www.youtube.com/shorts/{video_id}")

    print("STEP 10: Committing youtube.json...")
    commit_youtube_json(article, script, video_id)

    print(f"\n[DONE] Short live at https://www.youtube.com/shorts/{video_id}")


if __name__ == "__main__":
    main()
