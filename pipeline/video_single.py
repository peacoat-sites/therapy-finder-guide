#!/usr/bin/env python3
"""
Single-site YouTube Shorts Generator — runs inside a site repo via GitHub Actions.

Reads the latest article from content/posts/ (local checkout),
generates a 60-second Short via Claude + ElevenLabs + Shotstack,
then uploads to YouTube and commits data/youtube.json back to the repo.

Called by .github/workflows/video.yml in each site repo.
All config comes from environment variables (GitHub Actions secrets/vars).
"""

import io, os, sys, json, time, re, subprocess
import random
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
        "Do NOT include any calls to action, website URLs, channel names, or promotional language. "
        "This is purely educational/informational content optimized for maximum views and watch time. "
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
        '  "title": "YouTube title under 80 chars with a number or power word",\n'
        '  "description": "2-3 sentence educational description under 400 chars — no website links or CTAs",\n'
        '  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"]\n'
        "}\n\n"
        "Keep each point to 1-2 short sentences. Total spoken word count must be under 150 words. "
        "No calls to action, no website references, no channel plugs."
    )

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
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

def synthesize_audio(script: dict) -> tuple[bytes, list]:
    """Returns (mp3_bytes, word_timestamps) using ElevenLabs with-timestamps endpoint."""
    parts = [script["hook"]] + script["points"]
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
    chars = alignment.get("characters", [])
    char_starts = alignment.get("character_start_times_seconds", [])
    char_ends = alignment.get("character_end_times_seconds", [])
    words = []
    if chars:
        # Reconstruct word timestamps from character alignment
        current_word = ""
        word_start = None
        for ch, ts, te in zip(chars, char_starts, char_ends):
            if ch == " " or ch == "\n":
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

def fetch_pexels_clips(count: int = 4, orientation: str = "portrait", topic: str = "") -> list:
    # Build article-specific queries first so B-roll matches the video topic.
    # Fallback to generic niche queries if we don't get enough clips.
    STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
                 "for", "of", "with", "by", "from", "is", "are", "was", "were",
                 "your", "you", "how", "why", "what", "when", "who", "will",
                 "that", "this", "these", "those", "can", "cant", "dont", "wont",
                 "not", "no", "vs", "its"}

    topic_words = []
    if topic:
        # Strip punctuation, drop stopwords, keep meaningful content words
        raw = re.sub(r"[^\w\s]", " ", topic.lower()).split()
        topic_words = [w for w in raw if w not in STOPWORDS and len(w) > 3]

    niche_words = SITE_NICHE.replace(" & ", " ").replace(",", "").split()

    queries = []
    # 1. Best 2-3 topic keywords (most specific)
    if len(topic_words) >= 2:
        queries.append(" ".join(topic_words[:3]))
        queries.append(" ".join(topic_words[:2]))
    elif topic_words:
        queries.append(topic_words[0])
    # 2. Niche + first topic word (mid-specificity)
    if topic_words:
        queries.append(f"{SITE_NICHE} {topic_words[0]}")
    # 3. Generic niche fallbacks
    queries.append(SITE_NICHE)
    queries.append(" ".join(niche_words[:2]) if len(niche_words) >= 2 else niche_words[0])
    queries.extend(["professional advice", "helping people"])

    candidates = []
    seen_ids = set()
    headers = {"Authorization": PEXELS_KEY}

    for query in queries:
        if len(candidates) >= count * 4:  # collect a wide pool, then sample
            break
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": query, "per_page": 15, "orientation": orientation, "size": "medium"},
        )
        if r.status_code != 200:
            continue
        for vid in r.json().get("videos", []):
            if vid["id"] in seen_ids:
                continue
            files = vid.get("video_files", [])
            if orientation == "portrait":
                preferred = [f for f in files if f.get("width", 1) < f.get("height", 1) and f.get("height", 0) >= 720]
            else:
                preferred = [f for f in files if f.get("width", 1) > f.get("height", 1) and f.get("height", 0) >= 720]
            chosen = sorted(preferred or files, key=lambda f: f.get("height", 0), reverse=True)
            if chosen:
                candidates.append(chosen[0]["link"])
                seen_ids.add(vid["id"])

    # Shuffle the pool so the FIRST clip (= auto-thumbnail frame) varies each run
    random.shuffle(candidates)
    return candidates[:count]


# ── Thumbnail generation ──────────────────────────────────────────────────────

def generate_thumbnail(title: str, domain: str) -> bytes:
    """Create a 1280x720 PNG thumbnail with the video title and domain."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), "#1a0702")
    draw = ImageDraw.Draw(img)

    # Vertical gradient: dark maroon at top → warm orange-red at bottom
    for y in range(H):
        t = y / H
        r_ch = int(0x1a + (0xc2 - 0x1a) * t)
        g_ch = int(0x07 + (0x41 - 0x07) * t)
        b_ch = int(0x02 + (0x0c - 0x02) * t)
        draw.line([(0, y), (W, y)], fill=(r_ch, g_ch, b_ch))

    # Try to load a bold system font; fall back to PIL default
    title_font = domain_font = None
    for fp in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]:
        if Path(fp).exists():
            title_font = ImageFont.truetype(fp, 80)
            domain_font = ImageFont.truetype(fp, 38)
            break
    if not title_font:
        title_font = domain_font = ImageFont.load_default()

    # Word-wrap title to fit within 1160px
    words = title.split()
    lines, cur = [], []
    for word in words:
        test = " ".join(cur + [word])
        bbox = draw.textbbox((0, 0), test, font=title_font)
        if bbox[2] > W - 120 and cur:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))

    # Draw title centered vertically (offset upward a bit for domain)
    line_h = 96
    total_h = len(lines) * line_h
    y = (H - total_h) // 2 - 30
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=title_font)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw.text((x + 3, y + 3), line, font=title_font, fill=(0, 0, 0, 160))
        draw.text((x, y), line, font=title_font, fill="#FFFFFF")
        y += line_h

    # Domain label at bottom
    bbox = draw.textbbox((0, 0), domain, font=domain_font)
    dx = (W - (bbox[2] - bbox[0])) // 2
    draw.text((dx + 1, H - 60 + 1), domain, font=domain_font, fill=(0, 0, 0, 120))
    draw.text((dx, H - 60), domain, font=domain_font, fill="#FFFFFFBB")

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ── Step 5: Upload file ───────────────────────────────────────────────────────

def upload_file(data: bytes, filename: str, mime: str) -> str:
    # Litterbox (catbox temp storage) — anonymous, 1h expiry, enough for Shotstack render
    r = requests.post("https://litterbox.catbox.moe/resources/internals/api.php",
        data={"reqtype": "fileupload", "time": "1h"},
        files={"fileToUpload": (filename, data, mime)}, timeout=60)
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise RuntimeError(f"litterbox upload failed: {r.text[:100]}")


def upload_audio(audio_bytes: bytes) -> str:
    return upload_file(audio_bytes, "narration.mp3", "audio/mpeg")


# ── Caption segmentation ──────────────────────────────────────────────────────

def segment_captions(words: list, audio_duration: float) -> list:
    """
    Segment word-timestamp list into caption clips.
    Max 6 words OR 3.0s per segment. Minimum 1.2s enforced.
    Returns list of {"text": str, "start": float, "end": float}.
    """
    segments = []
    if not words:
        return segments

    current_words = []
    seg_start = words[0]["start"]

    for i, w in enumerate(words):
        current_words.append(w["word"])
        seg_duration = w["end"] - seg_start
        is_last = (i == len(words) - 1)
        hit_max_words = len(current_words) >= 6
        hit_max_time = seg_duration >= 3.0

        if hit_max_words or hit_max_time or is_last:
            seg_end = w["end"]
            # Enforce minimum duration
            if seg_end - seg_start < 1.2:
                # Try to extend to next word if available
                if not is_last:
                    continue
                seg_end = max(seg_start + 1.2, seg_end)
            segments.append({
                "text": " ".join(current_words),
                "start": seg_start,
                "end": seg_end,
            })
            current_words = []
            if not is_last:
                seg_start = words[i + 1]["start"]

    # Flush: ensure last segment ends at audio_duration
    if segments:
        segments[-1]["end"] = audio_duration

    return segments


# ── Step 6 & 7: Shotstack render ─────────────────────────────────────────────

MUSIC_URL = "https://cdn.pixabay.com/audio/2022/08/02/audio_884fe92c21.mp3"


def build_and_render(script: dict, clips: list, audio_url: str,
                     words: list, audio_duration: float,
                     is_shorts: bool = True) -> str:
    video_clips = []
    clip_duration = audio_duration / max(len(clips), 1)

    for i, url in enumerate(clips):
        start = i * clip_duration
        video_clips.append({
            "asset": {"type": "video", "src": url, "volume": 0},
            "start": start, "length": clip_duration, "fit": "cover",
        })

    # Caption clips (track[1]) — html asset, word-wrapped
    font_size = "46px" if is_shorts else "52px"
    caption_clips = []
    segments = segment_captions(words, audio_duration)
    for seg in segments:
        seg_len = max(seg["end"] - seg["start"], 0.1)
        caption_clips.append({
            "asset": {
                "type": "html",
                "html": (
                    f'<p style="font-family:Arial,sans-serif;font-size:{font_size};font-weight:900;'
                    f'color:#FFFFFF;text-align:center;padding:16px 20px;line-height:1.3;'
                    f'word-wrap:break-word;white-space:normal;'
                    f'text-shadow:2px 2px 8px rgba(0,0,0,0.9),0px 0px 20px rgba(0,0,0,0.7);">'
                    f'{seg["text"]}</p>'
                ),
                "width": 900, "height": 200, "css": "p{margin:0}",
            },
            "start": seg["start"],
            "length": seg_len,
            "position": "bottom",
            "offset": {"y": 0.12},
        })

    # Watermark clip (track[0] — top layer), 65% opacity, serif font distinct from captions
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
        "start": 0,
        "length": audio_duration + 1.0,
        "position": "topLeft",
        "offset": {"x": 0.01, "y": -0.01},
    }]

    # Music clip (track[3])
    music_clip = {
        "asset": {"type": "audio", "src": MUSIC_URL, "volume": 0.07},
        "start": 0,
        "length": audio_duration + 1.0,
    }

    if is_shorts:
        output = {
            "format": "mp4", "resolution": "hd", "aspectRatio": "9:16",
            "fps": 30, "size": {"width": 1080, "height": 1920},
        }
    else:
        output = {
            "format": "mp4", "resolution": "hd", "aspectRatio": "16:9",
            "fps": 30, "size": {"width": 1920, "height": 1080},
        }

    payload = {
        "timeline": {
            "tracks": [
                {"clips": watermark_clips},   # track 0 — TOP
                {"clips": caption_clips},      # track 1
                {"clips": video_clips},        # track 2
                {"clips": [music_clip]},       # track 3
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
            return resp["url"], render_id
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
        + "\n\n#Shorts"
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


def set_youtube_thumbnail(video_id: str, png_bytes: bytes) -> bool:
    """Upload a custom thumbnail. Returns False (not an error) if channel not phone-verified."""
    token = get_access_token()
    r = requests.post(
        f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
        f"?videoId={video_id}&uploadType=media",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"},
        data=png_bytes, timeout=30,
    )
    if r.status_code == 403:
        print("  Thumbnail: channel not phone-verified — skipping (visit youtube.com/verify to enable)")
        return False
    if r.status_code in (200, 204):
        print("  Thumbnail: set OK")
        return True
    print(f"  Thumbnail: HTTP {r.status_code} — {r.text[:80]}")
    return False


def verify_upload_channel() -> None:
    """Abort early if the token maps to the wrong channel — prevents misrouted uploads."""
    token = get_access_token()
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
        headers={"Authorization": f"Bearer {token}"}, timeout=15,
    )
    items = r.json().get("items", [])
    if not items:
        print("[ABORT] Could not verify YouTube channel identity")
        sys.exit(1)
    actual = items[0]["id"]
    if actual != YOUTUBE_CHANNEL_ID:
        print(f"[ABORT] Token routes to {actual}, expected {YOUTUBE_CHANNEL_ID} — fix YOUTUBE_REFRESH_TOKEN secret")
        sys.exit(1)
    print(f"  Channel: {actual}")


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

    print("STEP 0: Verifying upload channel...")
    if not DRY_RUN:
        verify_upload_channel()

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

    print("STEP 2b: Generating thumbnail image...")
    try:
        thumbnail_png = generate_thumbnail(script["title"], SITE_DOMAIN)
        print(f"  Thumbnail: {len(thumbnail_png)//1024} KB PNG")
    except Exception as e:
        print(f"  Thumbnail generation failed: {e} — will skip thumbnail upload")
        thumbnail_png = None

    print("STEP 3: Synthesizing audio via ElevenLabs...")
    audio, words = synthesize_audio(script)
    audio_duration = words[-1]["end"] if words else 60.0
    print(f"  Audio: {len(audio)/1024:.1f} KB, duration ~{audio_duration:.1f}s")

    if DRY_RUN:
        Path("narration.mp3").write_bytes(audio)
        Path("script.json").write_text(json.dumps(script, indent=2))
        if thumbnail_png:
            Path("thumbnail.png").write_bytes(thumbnail_png)
        print("[DRY RUN] Saved narration.mp3, script.json, thumbnail.png — done.")
        return

    print("STEP 4: Fetching Pexels B-roll clips...")
    clips = fetch_pexels_clips(count=4, orientation="portrait", topic=article.get("title", ""))
    print(f"  Found {len(clips)} clips")

    print("STEP 5: Uploading audio...")
    audio_url = upload_audio(audio)
    print(f"  URL: {audio_url[:60]}...")

    print("STEP 6-7: Building + rendering via Shotstack...")
    video_url, render_id = build_and_render(script, clips, audio_url, words, audio_duration, is_shorts=True)
    print(f"  Done: {video_url[:60]}...")

    print("STEP 8: Downloading video...")
    video_bytes = requests.get(video_url, timeout=120).content
    print(f"  {len(video_bytes)/1024/1024:.1f} MB")

    # Delete render from Shotstack storage immediately — videos are on YouTube, no need to keep them
    try:
        requests.delete(f"{SHOTSTACK_BASE}/render/{render_id}", headers={"x-api-key": SHOTSTACK_KEY}, timeout=10)
        print(f"  Shotstack render {render_id[:8]}... deleted")
    except Exception:
        pass  # non-fatal

    print("STEP 9: Uploading to YouTube...")
    video_id = upload_to_youtube(video_bytes, script, article)
    print(f"  Published: https://www.youtube.com/shorts/{video_id}")

    if thumbnail_png:
        print("STEP 9b: Setting custom thumbnail...")
        set_youtube_thumbnail(video_id, thumbnail_png)

    print("STEP 10: Committing youtube.json...")
    commit_youtube_json(article, script, video_id)

    print(f"\n[DONE] Short live at https://www.youtube.com/shorts/{video_id}")


if __name__ == "__main__":
    main()
