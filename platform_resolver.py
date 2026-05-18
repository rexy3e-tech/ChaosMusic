"""
platform_resolver.py — Chaos Cafe Music Bot
Uses YouTube with PO token workaround + SoundCloud fallback
"""

import re
import asyncio
import aiohttp
import yt_dlp

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# ─────────────────────────────────────────────
#  yt-dlp opts — multiple fallback clients
# ─────────────────────────────────────────────
def make_ytdl_opts(cookiefile: str | None = None) -> dict:
    opts = {
        "format": "bestaudio/best",
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
        "extract_flat": False,
        "extractor_args": {
            "youtube": {
                "player_client": ["mweb", "tv_embedded", "ios"],
                "player_skip": ["webpage", "js"],
            }
        },
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    return opts

# Check for cookies file
import os
_COOKIE_FILE = None
for _path in ["cookies.txt", "/app/cookies.txt"]:
    if os.path.exists(_path):
        _COOKIE_FILE = _path
        print(f"✅ Using cookies from {_path}")
        break

YTDL_OPTS = make_ytdl_opts(_COOKIE_FILE)

# SoundCloud fallback opts (no auth needed)
SC_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",
    "source_address": "0.0.0.0",
    "noplaylist": True,
}

PATTERNS = {
    "youtube":     re.compile(r"(youtube\.com|youtu\.be)"),
    "spotify":     re.compile(r"open\.spotify\.com/(track|album|playlist|artist)/([A-Za-z0-9]+)"),
    "soundcloud":  re.compile(r"soundcloud\.com"),
    "bandcamp":    re.compile(r"bandcamp\.com"),
    "mixcloud":    re.compile(r"mixcloud\.com"),
    "deezer":      re.compile(r"deezer\.com"),
    "apple_music": re.compile(r"music\.apple\.com"),
    "vimeo":       re.compile(r"vimeo\.com"),
    "twitch":      re.compile(r"twitch\.tv"),
    "direct":      re.compile(r"\.(mp3|ogg|flac|wav|m4a|aac|opus|webm)(\?.*)?$", re.I),
}

PLATFORM_EMOJIS = {
    "youtube":     "🎬",
    "spotify":     "🟢",
    "soundcloud":  "🟠",
    "bandcamp":    "🔵",
    "mixcloud":    "🌀",
    "deezer":      "💜",
    "apple_music": "🍎",
    "vimeo":       "🎞",
    "twitch":      "💜",
    "direct":      "🔗",
    "generic":     "🌐",
    "search":      "🔍",
}

def detect_platform(query: str) -> str:
    for name, pattern in PATTERNS.items():
        if pattern.search(query):
            return name
    if query.startswith("http"):
        return "generic"
    return "search"

def _parse_entries(info: dict) -> list[dict]:
    entries = info.get("entries", [info]) if info else []
    results = []
    for e in entries:
        if not e:
            continue
        results.append({
            "title":       e.get("title", "Unknown"),
            "url":         e.get("url") or e.get("webpage_url"),
            "webpage_url": e.get("webpage_url", ""),
            "duration":    e.get("duration", 0),
            "thumbnail":   e.get("thumbnail", ""),
            "uploader":    e.get("uploader", "Unknown"),
            "platform":    e.get("extractor_key", "Unknown"),
        })
    return results

async def _ytdl_fetch(query: str, single: bool = True) -> list[dict]:
    """Try YouTube first, fallback to SoundCloud if blocked."""
    loop = asyncio.get_event_loop()

    # Try YouTube
    def _yt():
        q = f"ytsearch:{query}" if not query.startswith("http") else query
        opts = dict(YTDL_OPTS)
        if single:
            opts["noplaylist"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(q, download=False)

    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, _yt), timeout=20
        )
        results = _parse_entries(info)
        if results and results[0].get("url"):
            return results
    except Exception as e:
        print(f"⚠️ YouTube failed: {e}")

    # YouTube failed — fallback to SoundCloud
    print(f"⚠️ YouTube blocked, trying SoundCloud for: {query}")

    def _sc():
        q = f"scsearch:{query}" if not query.startswith("http") else query
        with yt_dlp.YoutubeDL(SC_OPTS) as ydl:
            return ydl.extract_info(q, download=False)

    try:
        info = await asyncio.wait_for(
            loop.run_in_executor(None, _sc), timeout=20
        )
        results = _parse_entries(info)
        if results:
            print(f"✅ SoundCloud fallback worked for: {query}")
            return results
    except Exception as e:
        raise ValueError(f"YouTube aur SoundCloud dono fail ho gaye: {e}")

    raise ValueError("Koi source kaam nahi kar raha!")

# ─────────────────────────────────────────────
#  Spotify — oEmbed (no API/Premium needed)
# ─────────────────────────────────────────────
async def _resolve_spotify(url: str) -> list[dict]:
    m = PATTERNS["spotify"].search(url)
    if not m:
        raise ValueError("Invalid Spotify URL")
    kind = m.group(1)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        oembed_url = f"https://open.spotify.com/oembed?url={url}"
        async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                raise ValueError(f"Spotify oEmbed failed: HTTP {resp.status}")
            data = await resp.json(content_type=None)

    title     = data.get("title", "")
    thumbnail = data.get("thumbnail_url", "")
    if not title:
        raise ValueError("Spotify se track info nahi mili")

    if kind == "track":
        tracks = await _ytdl_fetch(title, single=True)
        if tracks:
            tracks[0]["platform"]  = "Spotify"
            tracks[0]["thumbnail"] = tracks[0].get("thumbnail") or thumbnail
        return tracks

    return [{
        "title":       title,
        "url":         None,
        "_search":     title,
        "webpage_url": url,
        "duration":    0,
        "thumbnail":   thumbnail,
        "uploader":    "Spotify",
        "platform":    "Spotify",
    }]

# ─────────────────────────────────────────────
#  Apple Music
# ─────────────────────────────────────────────
async def _resolve_apple_music(url: str) -> list[dict]:
    try:
        return await _ytdl_fetch(url, single=True)
    except Exception:
        pass
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            html = await resp.text()
    mm = re.search(r'<meta property="og:title"\s+content="([^"]+)"', html)
    title = mm.group(1) if mm else url
    title = re.sub(r"\s*[-–]\s*(Single|EP|Album)$", "", title, flags=re.I)
    return await _ytdl_fetch(title, single=True)

# ─────────────────────────────────────────────
#  Lazy stub resolver
# ─────────────────────────────────────────────
async def resolve_stub(track: dict) -> dict:
    if track.get("url"):
        return track
    search = track.get("_search", track["title"])
    results = await _ytdl_fetch(search, single=True)
    if results:
        resolved = results[0]
        resolved["platform"] = track.get("platform", resolved["platform"])
        return resolved
    return track

# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────
async def resolve(query: str) -> tuple[list[dict], str, str]:
    platform = detect_platform(query)

    if platform == "spotify":
        tracks = await _resolve_spotify(query)
    elif platform == "apple_music":
        tracks = await _resolve_apple_music(query)
    elif platform in ("youtube", "soundcloud", "bandcamp", "mixcloud",
                      "deezer", "vimeo", "twitch", "generic", "direct"):
        tracks = await _ytdl_fetch(query, single=(platform != "youtube"))
    else:
        tracks = await _ytdl_fetch(query, single=True)

    emoji = PLATFORM_EMOJIS.get(platform, "🎵")
    return tracks, platform, emoji
