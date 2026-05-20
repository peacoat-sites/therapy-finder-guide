#!/usr/bin/env python3
"""
Single-site YouTube video generator — runs inside a site repo via GitHub Actions.

Reads the latest article from content/posts/ (local checkout),
generates a 60-second script via Claude + ElevenLabs + Shotstack,
renders BOTH a 9:16 Short AND a 16:9 Standard video,
then uploads both to YouTube and commits data/youtube.json back to the repo.

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
SHOTSTACK_KEY        = os.environ.get("SHOTSTACK_API_KEY", "4vmNf0jK4NPFeeVJarsLrnKvgUvRSgglLCiasYS8")
PEXELS_KEY           = os.environ["PEXELS_API_KEY"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
YOUTUBE_CHANNEL_ID   = os.environ["YOUTUBE_CHANNEL_ID"]
SITE_SLUG            = os.environ["SITE_NAME"]          # e.g. medicare-starter
SITE_DOMAIN          = os.environ["SITE_DOMAIN"]        # e.g. medicarestarter.com
SITE_NICHE           = os.environ.get("SITE_NICHE", "")
SITE_CHANNEL_NAME    = os.environ.get("SITE_CHANNEL_NAME", "") or " ".join(w.capitalize() for w in SITE_SLUG.split("-"))

SHOTSTACK_BASE = "https://api.shotstack.io/v1"
MUSIC_URL      = "https://cdn.pixabay.com/audio/2022/08/02/audio_884fe92c21.mp3"

DRY_RUN = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "").lower() == "true"


# ── Step 1: Read latest article from local checkout ───────────────────────────

def read_latest_article() -> dict | None:
    posts_dir = Path("content/posts")
    if not posts_dir.exists():
        print("  [WARN] content/posts/ not found")
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
        f"You are writing a punchy, high-retention 60-second YouTube Shorts script for a {SITE_NICHE} channel. "
        "Structure: Hook (pattern interrupt, loss framing or alarming stat) -> Problem -> 3 Insights -> Resolution. "
        "No filler. Every sentence advances the argument. Use authority language. "
        "Do NOT include calls to action, website URLs, channel names, or 'follow for more' language. "
        "Respond ONLY with valid JSON — no markdown fences, no commentary."
    )
    user = (
        f"Write a 60-second YouTube Shorts script based on this article:\n\n"
        f"Title: {article['title']}\n\n"
        f"Article (first 1500 chars):\n{article['body'][:1500]}\n\n"
        "Return JSON with EXACTLY these keys:\n"
        "{\n"
        '  "hook": "1-2 sentences, pattern-interrupt — open with a loss stat or alarming fact (under 25 words)",\n'
        '  "problem": "1 sentence naming the specific pain or mistake (under 20 words)",\n'
        '  "points": [\n'
        '    "First insight with a concrete number or comparison (under 30 words)",\n'
        '    "Second insight with a different angle (under 30 words)",\n'
        '    "Third actionable insight — the thing they can do today (under 25 words)"\n'
        "  ],\n"
        '  "resolution": "1-2 sentences calling back to the hook, stating the outcome (under 20 words)",\n'
        '  "callout_cards": [\n'
        '    "Short bold stat or key phrase for on-screen text (under 8 words)",\n'
        '    "Second key insight as a bold on-screen callout (under 8 words)",\n'
        '    "Third callout — most actionable phrase (under 8 words)"\n'
        "  ],\n"
        '  "title": "YouTube title under 80 chars — number or power word, curiosity gap",\n'
        '  "description": "2-3 educational sentences under 400 chars — no website links or CTAs",\n'
        '  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"]\n'
        "}\n\n"
        "Total spoken word count (hook + problem + all points + resolution) must be 130-155 words. "
        "No calls to action, no website references, no subscribe prompts."
    )

    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": user}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw).rstrip('`').strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
        else:
            raise ValueError(f"Could not parse Claude response: {raw[:300]}")

    # Ensure all keys exist
    data.setdefault("callout_cards", [])
    data.setdefault("problem", "")
    data.setdefault("resolution", "")
    return data


# ── Step 3: Synthesize audio ──────────────────────────────────────────────────

def synthesize_audio(script: dict) -> tuple[bytes, list]:
    """Returns (mp3_bytes, word_timestamps) using ElevenLabs with-timestamps endpoint."""
    parts = [script["hook"]]
    if script.get("problem"):
        parts.append(script["problem"])
    parts.extend(script.get("points", []))
    if script.get("resolution"):
        parts.append(script["resolution"])
    full_text = "  ".join(parts)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}/with-timestamps"
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
    data = r.json()
    import base64
    audio_bytes = base64.b64decode(data["audio_base64"])
    alignment = data.get("alignment", {})
    chars      = alignment.get("characters", [])
    char_starts = alignment.get("character_start_times_seconds", [])
    char_ends   = alignment.get("character_end_times_seconds", [])
    words = []
    if chars:
        current_word = ""
        word_start = None
        for ch, ts, te in zip(chars, char_starts, char_ends):
            if ch in (" ", "\n"):
                if current_word:
                    words.append({"word": current_word, "start": word_start, "end": te})
                    current_word = ""
                    word_start = None
            else:
                if word_start is None:
                    word_start = ts
                current_word += ch
        if current_word:
            words.append({"word": current_word, "start": word_start, "end": char_ends[-1]})
    return audio_bytes, words


# ── Step 4: Fetch Pexels clips ────────────────────────────────────────────────

def fetch_pexels_clips(count: int = 4, orientation: str = "portrait") -> list:
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
            params={"query": query, "per_page": 8, "orientation": orientation, "size": "medium"},
        )
        if r.status_code != 200:
            continue
        for vid in r.json().get("videos", []):
            if len(selected) >= count or vid["id"] in used_ids:
                continue
            files = vid.get("video_files", [])
            if orientation == "portrait":
                preferred = [f for f in files if f.get("width", 1) < f.get("height", 1) and f.get("height", 0) >= 720]
            else:
                preferred = [f for f in files if f.get("width", 1) > f.get("height", 1) and f.get("height", 0) >= 720]
            chosen = sorted(preferred or files, key=lambda f: f.get("height", 0), reverse=True)
            if chosen:
                selected.append(chosen[0]["link"])
                used_ids.add(vid["id"])

    return selected[:count]


# ── Step 5: Upload file ───────────────────────────────────────────────────────

def upload_file(data: bytes, filename: str, mime: str) -> str:
    """Upload audio to 0x0.st with transfer.sh fallback (catbox deprecated)."""
    try:
        r = requests.post(
            "https://0x0.st",
            files={"file": (filename, data, mime)},
            timeout=60,
        )
        if r.status_code == 200 and r.text.strip().startswith("https://"):
            return r.text.strip()
    except Exception as e:
        pass  # fall through to backup
    # Fallback: transfer.sh
    r2 = requests.put(
        f"https://transfer.sh/{filename}",
        data=data,
        headers={"Content-Type": mime, "Max-Days": "7"},
        timeout=60,
    )
    if r2.status_code == 200 and r2.text.strip().startswith("https://"):
        return r2.text.strip()
    raise RuntimeError(f"audio upload failed — 0x0.st and transfer.sh both failed")


def upload_audio(audio_bytes: bytes) -> str:
    return upload_file(audio_bytes, "narration.mp3", "audio/mpeg")


# ── Caption segmentation ──────────────────────────────────────────────────────

def segment_captions(words: list, audio_duration: float) -> list:
    """Max 6 words OR 3.0s per segment. Minimum 1.2s enforced."""
    segments = []
    if not words:
        return segments
    current_words = []
    seg_start = words[0]["start"]
    for i, w in enumerate(words):
        current_words.append(w["word"])
        seg_duration = w["end"] - seg_start
        is_last = (i == len(words) - 1)
        if len(current_words) >= 6 or seg_duration >= 3.0 or is_last:
            seg_end = w["end"]
            if seg_end - seg_start < 1.2:
                if not is_last:
                    continue
                seg_end = max(seg_start + 1.2, seg_end)
            segments.append({"text": " ".join(current_words), "start": seg_start, "end": seg_end})
            current_words = []
            if not is_last:
                seg_start = words[i + 1]["start"]
    if segments:
        segments[-1]["end"] = audio_duration
    return segments


# ── Steps 6-7: Shotstack render ───────────────────────────────────────────────

def build_and_render(script: dict, clips: list, audio_url: str,
                     words: list, audio_duration: float,
                     is_shorts: bool = True) -> str:
    """Build Shotstack payload, submit render, poll until done. Returns video URL."""
    label      = "Shorts 9:16" if is_shorts else "Standard 16:9"
    n_clips    = max(len(clips), 1)
    clip_dur   = (audio_duration + 1.0) / n_clips
    cap_width  = 900  if is_shorts else 1600
    card_width = 900  if is_shorts else 1400
    font_size  = "46px" if is_shorts else "48px"
    card_y     = 0.15 if is_shorts else 0.10

    # B-roll clips
    video_clips = []
    for i, url in enumerate(clips):
        start  = max(0.0, i * clip_dur - (0.4 if i > 0 else 0))
        length = clip_dur + (0.4 if i < n_clips - 1 else 1.0)
        video_clips.append({
            "asset": {"type": "video", "src": url, "volume": 0},
            "start": start, "length": length, "fit": "cover",
        })

    # Caption clips
    caption_clips = []
    for seg in segment_captions(words, audio_duration):
        seg_len = max(seg["end"] - seg["start"], 0.1)
        caption_clips.append({
            "asset": {
                "type": "html",
                "html": (
                    f'<p style="font-family:Montserrat,Arial,sans-serif;font-size:{font_size};font-weight:900;'
                    f'color:#FFFFFF;text-align:center;padding:12px 20px;line-height:1.25;'
                    f'word-wrap:break-word;white-space:normal;'
                    f'-webkit-text-stroke:1.5px rgba(0,0,0,0.8);'
                    f'text-shadow:2px 2px 6px rgba(0,0,0,1),0px 0px 18px rgba(0,0,0,0.85);">'
                    f'{seg["text"]}</p>'
                ),
                "width": cap_width, "height": 200, "css": "p{margin:0}",
            },
            "start": seg["start"], "length": seg_len,
            "position": "bottom", "offset": {"y": 0.10},
            "transition": {"in": "fadeFast", "out": "fadeFast"},
        })

    # Callout cards
    card_timings  = [0.20, 0.50, 0.75]
    card_duration = 2.8
    callout_clips = []
    for idx, card_text in enumerate(script.get("callout_cards", [])[:3]):
        card_start = max(3.0, min(audio_duration * card_timings[idx], audio_duration - card_duration - 2.0))
        callout_clips.append({
            "asset": {
                "type": "html",
                "html": (
                    f'<div style="background:rgba(0,0,0,0.72);border-radius:10px;padding:14px 24px;display:inline-block;">'
                    f'<p style="font-family:Montserrat,Arial,sans-serif;font-size:38px;font-weight:800;'
                    f'color:#FFE566;text-align:center;margin:0;line-height:1.2;'
                    f'text-shadow:1px 1px 4px rgba(0,0,0,0.9);text-transform:uppercase;letter-spacing:0.04em;">'
                    f'{card_text}</p></div>'
                ),
                "width": card_width, "height": 130, "css": "div{margin:0 auto}",
            },
            "start": card_start, "length": card_duration,
            "position": "center", "offset": {"y": card_y},
            "transition": {"in": "slideUp", "out": "fadeFast"},
        })

    # Watermark
    watermark_clips = [{
        "asset": {
            "type": "html",
            "html": (
                f'<div style="opacity:0.65;padding:8px 12px;">'
                f'<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:35px;font-weight:700;'
                f'color:#FFFFFF;margin:0;white-space:nowrap;letter-spacing:0.03em;'
                f'text-shadow:1px 1px 8px rgba(0,0,0,0.9);">{SITE_CHANNEL_NAME}</p></div>'
            ),
            "width": 520, "height": 75,
        },
        "start": 0, "length": audio_duration + 1.0,
        "position": "topLeft", "offset": {"x": 0.01, "y": -0.01},
    }]

    if is_shorts:
        output = {"format": "mp4", "resolution": "hd", "aspectRatio": "9:16",
                  "fps": 30, "size": {"width": 1080, "height": 1920}}
    else:
        output = {"format": "mp4", "resolution": "hd", "aspectRatio": "16:9",
                  "fps": 30, "size": {"width": 1920, "height": 1080}}

    payload = {
        "timeline": {
            "tracks": [
                {"clips": watermark_clips},
                {"clips": callout_clips},
                {"clips": caption_clips},
                {"clips": video_clips},
            ],
            "soundtrack": {"src": audio_url, "effect": "fadeOut", "volume": 1.0},
            "background": "#000000",
        },
        "output": output,
    }

    r = requests.post(
        f"{SHOTSTACK_BASE}/render",
        headers={"x-api-key": SHOTSTACK_KEY, "Content-Type": "application/json"},
        json=payload, timeout=30,
    )
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"Shotstack submit [{label}]: {data}")
    render_id = data["response"]["id"]
    print(f"  [{label}] Render ID: {render_id}")

    # Poll up to 15 min (90 × 10s)
    for i in range(90):
        time.sleep(10)
        r2 = requests.get(
            f"{SHOTSTACK_BASE}/render/{render_id}",
            headers={"x-api-key": SHOTSTACK_KEY}, timeout=15,
        )
        resp   = r2.json().get("response", {})
        status = resp.get("status")
        print(f"  [{label}] status={status} ({(i+1)*10}s)")
        if status == "done":
            return resp["url"]
        elif status in ("failed", "error"):
            raise RuntimeError(f"Shotstack [{label}] failed: {resp.get('error')}")

    raise TimeoutError(f"Shotstack [{label}] timed out after 15 min")


# ── Step 8: YouTube upload ────────────────────────────────────────────────────

def get_access_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    })
    d = r.json()
    if "access_token" not in d:
        raise RuntimeError(f"Token refresh failed: {d}")
    return d["access_token"]


def upload_to_youtube(video_bytes: bytes, script: dict, article: dict, is_shorts: bool = True) -> str:
    """Upload video to YouTube. Returns video ID. Retries 3× with backoff."""
    token = get_access_token()
    title = script["title"][:100]
    label = "Shorts" if is_shorts else "Standard"

    if is_shorts:
        desc = (script["description"] + "\n\n#Shorts #" +
                " #".join(t.replace(" ", "") for t in script["tags"][:5]))[:5000]
        tags = script["tags"][:15] + ["Shorts", SITE_NICHE]
    else:
        desc = script["description"][:5000]
        tags = script["tags"][:15] + [SITE_NICHE]

    meta = {
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": "27", "defaultLanguage": "en"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    for attempt in range(1, 4):
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
            if attempt < 3:
                print(f"  [YT-{label}] Init attempt {attempt} failed ({init_r.status_code}) — retrying...")
                time.sleep(15 * attempt)
                token = get_access_token()
                continue
            raise RuntimeError(f"YouTube init {init_r.status_code}: {init_r.text[:200]}")

        upload_url = init_r.headers["Location"]
        up_r = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                     "Content-Length": str(len(video_bytes))},
            data=video_bytes, timeout=300,
        )
        if up_r.status_code not in (200, 201):
            if "quotaExceeded" in up_r.text:
                raise RuntimeError(f"YouTube quota exhausted — resume tomorrow. ({label})")
            if attempt < 3:
                print(f"  [YT-{label}] Upload attempt {attempt} failed ({up_r.status_code}) — retrying...")
                time.sleep(15 * attempt)
                token = get_access_token()
                continue
            raise RuntimeError(f"YouTube upload {up_r.status_code}: {up_r.text[:200]}")
        return up_r.json()["id"]


# ── Step 9: Commit data/youtube.json ─────────────────────────────────────────

def commit_youtube_json(article: dict, script: dict, shorts_id: str, standard_id: str = None):
    data_dir  = Path("data")
    data_dir.mkdir(exist_ok=True)
    json_path = data_dir / "youtube.json"

    existing = json.loads(json_path.read_text()) if json_path.exists() else []
    entry = {
        "shorts_id":    shorts_id,
        "shorts_url":   f"https://www.youtube.com/shorts/{shorts_id}",
        "title":         script["title"],
        "article_slug":  article["article_slug"],
        "published_at":  datetime.now(timezone.utc).isoformat(),
    }
    if standard_id:
        entry["standard_id"]  = standard_id
        entry["standard_url"] = f"https://www.youtube.com/watch?v={standard_id}"

    existing.insert(0, entry)
    json_path.write_text(json.dumps(existing, indent=2))

    subprocess.run(["git", "config", "user.email", "pipeline@peacoat.dev"], check=True)
    subprocess.run(["git", "config", "user.name",  "Peacoat Pipeline"],     check=True)
    subprocess.run(["git", "add", "data/youtube.json"],                     check=True)
    subprocess.run(["git", "commit", "-m", f"Add YouTube videos: {script['title'][:55]}"], check=True)
    subprocess.run(["git", "push"],                                          check=True)
    print("  Committed data/youtube.json")


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
    audio, words = synthesize_audio(script)
    audio_duration = words[-1]["end"] if words else 60.0
    print(f"  Audio: {len(audio)/1024:.1f} KB, duration ~{audio_duration:.1f}s")

    if DRY_RUN:
        Path("narration.mp3").write_bytes(audio)
        Path("script.json").write_text(json.dumps(script, indent=2))
        print("[DRY RUN] Saved narration.mp3 and script.json — done.")
        return

    print("STEP 4: Fetching Pexels B-roll clips (portrait + landscape)...")
    portrait_clips  = fetch_pexels_clips(count=4, orientation="portrait")
    landscape_clips = fetch_pexels_clips(count=4, orientation="landscape")
    print(f"  Portrait: {len(portrait_clips)} clips  |  Landscape: {len(landscape_clips)} clips")

    print("STEP 5: Uploading audio to catbox.moe...")
    audio_url = upload_audio(audio)
    print(f"  URL: {audio_url[:60]}...")

    print("STEP 6-7a: Rendering Shorts (9:16) via Shotstack...")
    shorts_url = build_and_render(script, portrait_clips, audio_url, words, audio_duration, is_shorts=True)
    print(f"  Shorts render done: {shorts_url[:60]}...")

    print("STEP 6-7b: Rendering Standard (16:9) via Shotstack...")
    standard_url = build_and_render(script, landscape_clips, audio_url, words, audio_duration, is_shorts=False)
    print(f"  Standard render done: {standard_url[:60]}...")

    print("STEP 8a: Downloading Shorts MP4...")
    shorts_bytes = requests.get(shorts_url, timeout=120).content
    print(f"  {len(shorts_bytes)/1024/1024:.1f} MB")

    print("STEP 8b: Downloading Standard MP4...")
    standard_bytes = requests.get(standard_url, timeout=120).content
    print(f"  {len(standard_bytes)/1024/1024:.1f} MB")

    print("STEP 9a: Uploading Shorts to YouTube...")
    shorts_id = upload_to_youtube(shorts_bytes, script, article, is_shorts=True)
    print(f"  Published: https://www.youtube.com/shorts/{shorts_id}")

    print("STEP 9b: Uploading Standard to YouTube...")
    standard_id = upload_to_youtube(standard_bytes, script, article, is_shorts=False)
    print(f"  Published: https://www.youtube.com/watch?v={standard_id}")

    print("STEP 10: Committing youtube.json...")
    commit_youtube_json(article, script, shorts_id, standard_id)

    print(f"\n[DONE]")
    print(f"  Shorts:   https://www.youtube.com/shorts/{shorts_id}")
    print(f"  Standard: https://www.youtube.com/watch?v={standard_id}")


if __name__ == "__main__":
    main()
