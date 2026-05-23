#!/usr/bin/env python3
"""
Single-site YouTube video generator â€” runs inside a site repo via GitHub Actions.

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

# â”€â”€ Required env vars (set as GitHub Actions secrets / vars) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
ANTHROPIC_KEY        = os.environ["ANTHROPIC_API_KEY"]
EL_KEY               = os.environ["ELEVENLABS_API_KEY"]
EL_VOICE_ID          = os.environ["ELEVENLABS_VOICE_ID"]
SHOTSTACK_KEY        = os.environ.get("SHOTSTACK_API_KEY", "4vmNf0jK4NPFeeVJarsLrnKvgUvRSgglLCiasYS8")
PEXELS_KEY           = os.environ["PEXELS_API_KEY"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
YOUTUBE_CHANNEL_ID   = os.environ["YOUTUBE_CHANNEL_ID"]
SITE_SLUG            = os.environ["SITE_NAME"]
SITE_DOMAIN          = os.environ["SITE_DOMAIN"]
SITE_NICHE           = os.environ.get("SITE_NICHE", "")
SITE_CHANNEL_NAME    = os.environ.get("SITE_CHANNEL_NAME", "") or " ".join(w.capitalize() for w in SITE_SLUG.split("-"))

SHOTSTACK_BASE = "https://api.shotstack.io/v1"
MUSIC_URL      = "https://cdn.pixabay.com/audio/2022/08/02/audio_884fe92c21.mp3"

DRY_RUN = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "").lower() == "true"


# â”€â”€ Retry helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def with_retry(fn, label="op", max_attempts=3, initial_delay=8):
    """
    Call fn() up to max_attempts times with exponential backoff.
    Delays: 8s â†’ 16s â†’ 32s. Raises the last exception if all attempts fail.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_attempts:
                print(f"  [{label}] All {max_attempts} attempts exhausted. Final error: {exc}")
                raise
            wait = initial_delay * (2 ** (attempt - 1))
            print(f"  [{label}] Attempt {attempt}/{max_attempts} failed: {exc}")
            print(f"  [{label}] Retrying in {wait}s...")
            time.sleep(wait)


# â”€â”€ Step 0: Shotstack credit pre-check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def check_shotstack_credits(min_required=3):
    """
    Check Shotstack billing before spending any credits.
    Aborts the run if confirmed balance < min_required.
    Non-blocking if the billing endpoint is unavailable (warn + continue).
    """
    try:
        r = requests.get(
            f"{SHOTSTACK_BASE}/billing",
            headers={"x-api-key": SHOTSTACK_KEY},
            timeout=10,
        )
        if r.status_code == 200:
            data     = r.json().get("response", {})
            credits  = (data.get("plan") or data.get("credits") or {})
            balance  = credits.get("remaining") if isinstance(credits, dict) else credits
            if balance is not None:
                print(f"  Shotstack balance: {balance} credits remaining")
                if float(balance) < min_required:
                    raise RuntimeError(
                        f"Shotstack balance critically low ({balance} credits, need {min_required}). "
                        "Aborting to preserve remaining credits."
                    )
            else:
                print(f"  [WARN] Could not parse credit balance from response: {r.text[:100]}")
        else:
            print(f"  [WARN] Shotstack billing check returned HTTP {r.status_code} â€” continuing anyway")
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"  [WARN] Credit pre-check failed (non-blocking): {exc}")


# â”€â”€ Step 1: Read latest article from local checkout â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def read_latest_article() -> dict | None:
    posts_dir = Path("content/posts")
    if not posts_dir.exists():
        print("  [WARN] content/posts/ not found")
        return None

    md_files = sorted(
        [f for f in posts_dir.glob("*.md") if f.name != "_index.md"],
        key=lambda f: f.stat().st_mtime,   # sort by actual modification time, not filename
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


# â”€â”€ Step 1b: Duplicate article guard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def already_has_video(article_slug: str) -> bool:
    """Return True if this article slug already exists in data/youtube.json."""
    json_path = Path("data") / "youtube.json"
    if not json_path.exists():
        return False
    try:
        entries = json.loads(json_path.read_text())
        return any(e.get("article_slug") == article_slug for e in entries)
    except Exception:
        return False


# â”€â”€ Step 2: Generate script via Claude â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def generate_script(article: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    system = (
        f"You are writing a punchy, high-retention 60-second YouTube Shorts script for a {SITE_NICHE} channel. "
        "Structure: Hook (pattern interrupt, loss framing or alarming stat) -> Problem -> 3 Insights -> Resolution. "
        "No filler. Every sentence advances the argument. Use authority language. "
        "Do NOT include calls to action, website URLs, channel names, or 'follow for more' language. "
        "Respond ONLY with valid JSON â€” no markdown fences, no commentary."
    )
    user = (
        f"Write a 60-second YouTube Shorts script based on this article:\n\n"
        f"Title: {article['title']}\n\n"
        f"Article (first 1500 chars):\n{article['body'][:1500]}\n\n"
        "Return JSON with EXACTLY these keys:\n"
        "{\n"
        '  "hook": "1-2 sentences, pattern-interrupt â€” open with a loss stat or alarming fact (under 25 words)",\n'
        '  "problem": "1 sentence naming the specific pain or mistake (under 20 words)",\n'
        '  "points": [\n'
        '    "First insight with a concrete number or comparison (under 30 words)",\n'
        '    "Second insight with a different angle (under 30 words)",\n'
        '    "Third actionable insight â€” the thing they can do today (under 25 words)"\n'
        "  ],\n"
        '  "resolution": "1-2 sentences calling back to the hook, stating the outcome (under 20 words)",\n'
        '  "callout_cards": [\n'
        '    "Short bold stat or key phrase for on-screen text (under 8 words)",\n'
        '    "Second key insight as a bold on-screen callout (under 8 words)",\n'
        '    "Third callout â€” most actionable phrase (under 8 words)"\n'
        "  ],\n"
        '  "title": "YouTube title under 80 chars â€” number or power word, curiosity gap",\n'
        '  "description": "2-3 educational sentences under 400 chars â€” no website links or CTAs",\n'
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

    data.setdefault("callout_cards", [])
    data.setdefault("problem", "")
    data.setdefault("resolution", "")
    return data


# â”€â”€ Step 2b: Script quality gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

BANNED_PHRASES = [
    "subscribe", "follow for more", "click the link", "check out our website",
    "visit us at", "visit our website", "link in bio", "link in description",
    "for more info", "follow us", "like and subscribe",
]

def validate_script(script: dict) -> None:
    errors = []
    for field in ("title", "hook", "description"):
        if not script.get(field, "").strip():
            errors.append(f"'{field}' is empty")

    points = script.get("points", [])
    if len(points) < 3:
        errors.append(f"need 3 points, got {len(points)}")
    if not script.get("tags") or len(script["tags"]) < 3:
        errors.append(f"need â‰¥3 tags, got {len(script.get('tags', []))}")

    title_len = len(script.get("title", ""))
    if title_len > 100:
        errors.append(f"title too long ({title_len} chars, YT max is 100)")
    desc_len = len(script.get("description", ""))
    if desc_len > 400:
        errors.append(f"description too long ({desc_len} chars, target â‰¤400)")

    spoken_parts = [script.get("hook", ""), script.get("problem", "")]
    spoken_parts.extend(script.get("points", []))
    spoken_parts.append(script.get("resolution", ""))
    word_count = sum(len(p.split()) for p in spoken_parts if p)
    if word_count < 80:
        errors.append(f"script too short ({word_count} spoken words, min 80)")
    elif word_count > 220:
        errors.append(f"script too long ({word_count} spoken words, max 220)")

    all_text = " ".join(str(v) for v in script.values() if isinstance(v, str)).lower()
    for phrase in BANNED_PHRASES:
        if phrase in all_text:
            errors.append(f"banned phrase detected: '{phrase}'")

    if errors:
        raise ValueError("Script validation failed:\n  " + "\n  ".join(errors))

    print(f"  Script: âœ“ {word_count} spoken words | title {title_len} chars | {len(script['tags'])} tags")


# â”€â”€ Step 3: Synthesize audio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

    def _call():
        r = requests.post(
            url,
            headers={"xi-api-key": EL_KEY, "Content-Type": "application/json"},
            json={
                "text": full_text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True},
                "output_format": "mp3_44100_128",
            },
            timeout=60,
        )
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
        return r.json()

    data = with_retry(_call, label="ElevenLabs", max_attempts=3, initial_delay=10)

    import base64
    audio_bytes = base64.b64decode(data["audio_base64"])
    alignment   = data.get("alignment", {})
    chars       = alignment.get("characters", [])
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


# â”€â”€ Step 3b: Audio quality gate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def validate_audio(audio_bytes: bytes, words: list, audio_duration: float) -> None:
    errors = []
    min_kb = 60
    if len(audio_bytes) < min_kb * 1024:
        errors.append(f"audio too small ({len(audio_bytes) // 1024} KB, min {min_kb} KB)")
    if not words:
        errors.append("word alignment list is empty â€” captions cannot be generated")
    elif len(words) < 15:
        errors.append(f"only {len(words)} aligned words â€” suspiciously short narration")
    if audio_duration < 25:
        errors.append(f"audio too short ({audio_duration:.1f}s, min 25s)")
    elif audio_duration > 150:
        errors.append(f"audio too long ({audio_duration:.1f}s, max 150s)")
    if errors:
        raise RuntimeError("Audio validation failed:\n  " + "\n  ".join(errors))
    print(f"  Audio: âœ“ {len(audio_bytes) // 1024} KB | {len(words)} words aligned | {audio_duration:.1f}s")


# â”€â”€ Step 4: Fetch Pexels clips â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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

        def _pexels_query(q=query):
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q, "per_page": 8, "orientation": orientation, "size": "medium"},
                timeout=20,
            )
            if r.status_code == 429:
                raise RuntimeError("Pexels rate limited")
            if r.status_code != 200:
                raise RuntimeError(f"Pexels HTTP {r.status_code}")
            return r.json().get("videos", [])

        try:
            videos = with_retry(_pexels_query, label=f"Pexels:{query[:20]}", max_attempts=3, initial_delay=5)
        except Exception as exc:
            print(f"  [WARN] Pexels query '{query}' failed after retries: {exc} â€” skipping")
            continue

        for vid in videos:
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


# â”€â”€ URL accessibility check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def verify_url_accessible(url: str, label: str) -> None:
    try:
        r = requests.head(url, timeout=20, allow_redirects=True)
        if r.status_code not in (200, 206):
            raise RuntimeError(f"[{label}] URL not publicly accessible: HTTP {r.status_code}\n  {url[:100]}")
        print(f"  [{label}] âœ“ URL accessible (HTTP {r.status_code})")
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"[{label}] URL accessibility check failed: {exc}\n  {url[:100]}")


# â”€â”€ Step 5: Upload audio to public CDN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def upload_file(data: bytes, filename: str, mime: str) -> str:
    """
    Upload audio to a temporary public URL so Shotstack can fetch it during render.
    Tries 4 hosts in sequence. Files only need to survive ~15 min.
    Each host is retried up to 2 times before moving to the next.
    """
    errors = []

    def _try_host(name, fn):
        def _attempt():
            result = fn()
            if not result or not result.startswith("https://"):
                raise RuntimeError(f"Invalid URL returned: {result!r}")
            return result
        try:
            url = with_retry(_attempt, label=name, max_attempts=2, initial_delay=5)
            print(f"    Hosted on {name}")
            return url
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            return None

    # 1. uguu.se â€” anonymous, 48h expiry
    def _uguu():
        r = requests.post("https://uguu.se/upload", files={"files[]": (filename, data, mime)}, timeout=60)
        if r.status_code == 200:
            files = r.json().get("files", [])
            if files:
                return files[0].get("url", "")
        raise RuntimeError(f"status={r.status_code} body={r.text[:60]!r}")

    url = _try_host("uguu.se", _uguu)
    if url:
        return url

    # 2. tmpfiles.org â€” anonymous, 60-min expiry
    def _tmpfiles():
        r = requests.post("https://tmpfiles.org/api/v1/upload", files={"file": (filename, data, mime)}, timeout=60)
        if r.status_code == 200:
            url = r.json().get("data", {}).get("url", "")
            if url.startswith("https://tmpfiles.org/") and "/dl/" not in url:
                url = url.replace("https://tmpfiles.org/", "https://tmpfiles.org/dl/", 1)
            return url
        raise RuntimeError(f"status={r.status_code} body={r.text[:60]!r}")

    url = _try_host("tmpfiles.org", _tmpfiles)
    if url:
        return url

    # 3. file.io â€” anonymous, single-use download, 14-day expiry
    def _fileio():
        r = requests.post("https://file.io/?expires=1d", files={"file": (filename, data, mime)}, timeout=60)
        if r.status_code in (200, 201):
            link = r.json().get("link", "")
            return link
        raise RuntimeError(f"status={r.status_code} body={r.text[:60]!r}")

    url = _try_host("file.io", _fileio)
    if url:
        return url

    # 4. oshi.at â€” anonymous, 24h expiry
    def _oshi():
        r = requests.post("https://oshi.at", files={"f": (filename, data, mime)}, data={"expire": "60"}, timeout=60)
        link = next((line.split("=", 1)[1].strip() for line in r.text.splitlines() if line.startswith("DL=")), "")
        return link

    url = _try_host("oshi.at", _oshi)
    if url:
        return url

    raise RuntimeError("Audio upload failed â€” all 4 hosts exhausted:\n  " + "\n  ".join(errors))


def upload_audio(audio_bytes: bytes) -> str:
    return upload_file(audio_bytes, "narration.mp3", "audio/mpeg")


# â”€â”€ Caption segmentation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def segment_captions(words: list, audio_duration: float) -> list:
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


# â”€â”€ Steps 6-7: Shotstack render â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def build_and_render(script: dict, clips: list, audio_url: str,
                     words: list, audio_duration: float,
                     is_shorts: bool = True) -> str:
    label      = "Shorts 9:16" if is_shorts else "Standard 16:9"
    n_clips    = max(len(clips), 1)
    clip_dur   = (audio_duration + 1.0) / n_clips
    cap_width  = 900  if is_shorts else 1600
    card_width = 900  if is_shorts else 1400
    font_size  = "46px" if is_shorts else "48px"
    card_y     = 0.15 if is_shorts else 0.10

    video_clips = []
    for i, url in enumerate(clips):
        start  = max(0.0, i * clip_dur - (0.4 if i > 0 else 0))
        length = clip_dur + (0.4 if i < n_clips - 1 else 1.0)
        video_clips.append({
            "asset": {"type": "video", "src": url, "volume": 0},
            "start": start, "length": length, "fit": "cover",
        })

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

    # Submit render with retry
    def _submit():
        r = requests.post(
            f"{SHOTSTACK_BASE}/render",
            headers={"x-api-key": SHOTSTACK_KEY, "Content-Type": "application/json"},
            json=payload, timeout=30,
        )
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Shotstack submit [{label}]: {data}")
        return data["response"]["id"]

    render_id = with_retry(_submit, label=f"Shotstack submit {label}", max_attempts=3, initial_delay=15)
    print(f"  [{label}] Render ID: {render_id}")

    # Poll up to 18 min (108 Ã— 10s)
    for i in range(108):
        time.sleep(10)
        try:
            r2     = requests.get(f"{SHOTSTACK_BASE}/render/{render_id}",
                                  headers={"x-api-key": SHOTSTACK_KEY}, timeout=15)
            resp   = r2.json().get("response", {})
            status = resp.get("status")
        except Exception as poll_exc:
            print(f"  [{label}] Poll attempt {i+1} error: {poll_exc} â€” retrying...")
            continue

        print(f"  [{label}] status={status} ({(i+1)*10}s)")
        if status == "done":
            return resp["url"]
        elif status in ("failed", "error"):
            raise RuntimeError(f"Shotstack [{label}] render failed: {resp.get('error')}")

    raise TimeoutError(f"Shotstack [{label}] timed out after 18 min")


# â”€â”€ Step 7.5: Validate rendered MP4 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def validate_video_has_audio(video_bytes: bytes, label: str,
                              expected_duration: float = None, is_shorts: bool = None) -> None:
    import tempfile, subprocess as _sp, json as _json, os as _os

    min_bytes = 1 * 1024 * 1024
    if len(video_bytes) < min_bytes:
        raise RuntimeError(f"[{label}] Video too small ({len(video_bytes) // 1024} KB) â€” corrupt render")

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(video_bytes)
        tmp = f.name

    try:
        res = _sp.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", tmp],
                      capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise RuntimeError(f"[{label}] ffprobe failed: {res.stderr[:200]}")

        streams       = _json.loads(res.stdout).get("streams", [])
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        video_streams = [s for s in streams if s.get("codec_type") == "video"]

        if not video_streams:
            raise RuntimeError(f"[{label}] No video stream in MP4")
        if not audio_streams:
            raise RuntimeError(f"[{label}] No audio stream â€” soundtrack not embedded")

        audio_dur = float(audio_streams[0].get("duration", 0))
        if audio_dur < 5.0:
            raise RuntimeError(f"[{label}] Audio stream too short ({audio_dur:.1f}s)")

        vid_dur = float(video_streams[0].get("duration", 0))
        if vid_dur < 10.0:
            raise RuntimeError(f"[{label}] Video too short ({vid_dur:.1f}s)")
        if expected_duration is not None:
            delta_pct = abs(vid_dur - expected_duration) / max(expected_duration, 1)
            if delta_pct > 0.20:
                raise RuntimeError(f"[{label}] Duration mismatch: expected ~{expected_duration:.1f}s, got {vid_dur:.1f}s")

        if is_shorts is not None:
            exp_w, exp_h = (1080, 1920) if is_shorts else (1920, 1080)
            got_w = int(video_streams[0].get("width", 0))
            got_h = int(video_streams[0].get("height", 0))
            if got_w != exp_w or got_h != exp_h:
                raise RuntimeError(f"[{label}] Wrong resolution: got {got_w}Ã—{got_h}, expected {exp_w}Ã—{exp_h}")

        print(f"  [{label}] âœ“ {int(video_streams[0].get('width',0))}Ã—{int(video_streams[0].get('height',0))} | "
              f"video {vid_dur:.1f}s | audio {audio_dur:.1f}s | {len(video_bytes)//1024//1024} MB")
    finally:
        try:
            _os.unlink(tmp)
        except Exception:
            pass


# â”€â”€ Step 8: YouTube upload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
        "status":  {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    for attempt in range(1, 4):
        init_r = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "X-Upload-Content-Type": "video/mp4",
                     "X-Upload-Content-Length": str(len(video_bytes))},
            json=meta, timeout=30,
        )
        if init_r.status_code not in (200, 201):
            if attempt < 3:
                print(f"  [YT-{label}] Init attempt {attempt} failed ({init_r.status_code}) â€” retrying...")
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
                raise RuntimeError(f"YouTube quota exhausted â€” resume tomorrow. ({label})")
            if attempt < 3:
                print(f"  [YT-{label}] Upload attempt {attempt} failed ({up_r.status_code}) â€” retrying...")
                time.sleep(15 * attempt)
                token = get_access_token()
                continue
            raise RuntimeError(f"YouTube upload {up_r.status_code}: {up_r.text[:200]}")
        return up_r.json()["id"]


# â”€â”€ YouTube delete (rollback helper) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def delete_youtube_video(video_id: str) -> bool:
    try:
        token = get_access_token()
        r = requests.delete(
            f"https://www.googleapis.com/youtube/v3/videos?id={video_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 204:
            print(f"  [CLEANUP] âœ“ Deleted {video_id} from YouTube")
            return True
        print(f"  [CLEANUP] âœ— Could not delete {video_id}: HTTP {r.status_code} â€” remove manually")
        return False
    except Exception as exc:
        print(f"  [CLEANUP] âœ— Exception deleting {video_id}: {exc} â€” remove manually")
        return False


# â”€â”€ Step 9: Commit data/youtube.json â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    if len(article.get("body", "")) < 300:
        raise RuntimeError(f"Article body too short ({len(article['body'])} chars, min 300)")
    if already_has_video(article["article_slug"]):
        print(f"  [SKIP] '{article['article_slug']}' already has a video â€” nothing to do")
        sys.exit(0)

    print("STEP 2: Generating script via Claude...")
    script = generate_script(article)
    print(f"  YT Title: {script['title']}")
    print(f"  Hook: {script['hook'][:80]}...")
    validate_script(script)

    print("STEP 3: Synthesizing audio via ElevenLabs...")
    audio, words = synthesize_audio(script)
    audio_duration = words[-1]["end"] if words else 0.0
    print(f"  Audio: {len(audio)/1024:.1f} KB, duration ~{audio_duration:.1f}s")
    validate_audio(audio, words, audio_duration)

    if DRY_RUN:
        Path("narration.mp3").write_bytes(audio)
        Path("script.json").write_text(json.dumps(script, indent=2))
        print("[DRY RUN] Saved narration.mp3 and script.json â€” done.")
        return

    print("STEP 4: Fetching Pexels B-roll clips (portrait + landscape)...")
    portrait_clips  = fetch_pexels_clips(count=4, orientation="portrait")
    landscape_clips = fetch_pexels_clips(count=4, orientation="landscape")
    print(f"  Portrait: {len(portrait_clips)} clips  |  Landscape: {len(landscape_clips)} clips")
    if len(portrait_clips) < 2:
        raise RuntimeError(f"Not enough portrait clips ({len(portrait_clips)}) â€” need â‰¥2 for Shorts B-roll")
    if len(landscape_clips) < 2:
        raise RuntimeError(f"Not enough landscape clips ({len(landscape_clips)}) â€” need â‰¥2 for Standard B-roll")

    print("STEP 5: Uploading audio...")
    audio_url = upload_audio(audio)
    print(f"  URL: {audio_url[:60]}...")
    verify_url_accessible(audio_url, "audio upload")

    print("STEP 5b: Checking Shotstack credit balance...")
    check_shotstack_credits(min_required=3)

    print("STEP 6-7a: Rendering Shorts (9:16) via Shotstack...")
    shorts_url = build_and_render(script, portrait_clips, audio_url, words, audio_duration, is_shorts=True)
    print(f"  Shorts render done: {shorts_url[:60]}...")
    verify_url_accessible(shorts_url, "Shorts render URL")

    print("STEP 6-7b: Rendering Standard (16:9) via Shotstack...")
    standard_url = build_and_render(script, landscape_clips, audio_url, words, audio_duration, is_shorts=False)
    print(f"  Standard render done: {standard_url[:60]}...")
    verify_url_accessible(standard_url, "Standard render URL")

    print("STEP 8a: Downloading Shorts MP4...")
    dl_shorts = requests.get(shorts_url, timeout=120)
    if dl_shorts.status_code != 200:
        raise RuntimeError(f"Shorts download failed: HTTP {dl_shorts.status_code}")
    if "video" not in dl_shorts.headers.get("Content-Type", ""):
        raise RuntimeError(f"Shorts download wrong Content-Type: {dl_shorts.headers.get('Content-Type')}")
    shorts_bytes = dl_shorts.content
    print(f"  {len(shorts_bytes)/1024/1024:.1f} MB")
    validate_video_has_audio(shorts_bytes, "Shorts", expected_duration=audio_duration, is_shorts=True)

    print("STEP 8b: Downloading Standard MP4...")
    dl_standard = requests.get(standard_url, timeout=120)
    if dl_standard.status_code != 200:
        raise RuntimeError(f"Standard download failed: HTTP {dl_standard.status_code}")
    if "video" not in dl_standard.headers.get("Content-Type", ""):
        raise RuntimeError(f"Standard download wrong Content-Type: {dl_standard.headers.get('Content-Type')}")
    standard_bytes = dl_standard.content
    print(f"  {len(standard_bytes)/1024/1024:.1f} MB")
    validate_video_has_audio(standard_bytes, "Standard", expected_duration=audio_duration, is_shorts=False)

    print("STEP 9a: Uploading Shorts to YouTube...")
    shorts_id = upload_to_youtube(shorts_bytes, script, article, is_shorts=True)
    print(f"  Published: https://www.youtube.com/shorts/{shorts_id}")

    print("STEP 9b: Uploading Standard to YouTube...")
    try:
        standard_id = upload_to_youtube(standard_bytes, script, article, is_shorts=False)
    except Exception as upload_err:
        print(f"  [ERROR] Standard upload failed: {upload_err}")
        print(f"  Rolling back â€” deleting Shorts {shorts_id}...")
        delete_youtube_video(shorts_id)
        raise
    print(f"  Published: https://www.youtube.com/watch?v={standard_id}")

    print("STEP 10: Committing youtube.json...")
    commit_youtube_json(article, script, shorts_id, standard_id)

    print(f"\n[DONE]")
    print(f"  Shorts:   https://www.youtube.com/shorts/{shorts_id}")
    print(f"  Standard: https://www.youtube.com/watch?v={standard_id}")


if __name__ == "__main__":
    main()
