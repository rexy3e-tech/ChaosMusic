"""
platform_resolver.py — Chaos Cafe Music Bot
Spotify: uses free oEmbed API (no key, no Premium needed)
"""

import re
import json
import asyncio
import aiohttp
import yt_dlp

# ─────────────────────────────────────────────
#  yt-dlp setup
# ─────────────────────────────────────────────
YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# ─────────────────────────────────────────────
#  URL pattern matchers
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
#  yt-dlp extractor
# ─────────────────────────────────────────────
async def _ytdl_fetch(query: str, single: bool = True) -> list[dict]:
    loop = asyncio.get_event_loop()

    def _extract():
        q = f"ytsearch:{query}" if not query.startswith("http") else query
        opts = dict(YTDL_OPTS)
        if single:
            opts["noplaylist"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(q, download=False)
        return info

    info = await loop.run_in_executor(None, _extract)
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

# ─────────────────────────────────────────────
#  Spotify — oEmbed (free, no API key needed)
# ─────────────────────────────────────────────
async def _resolve_spotify(url: str) -> list[dict]:
    m = PATTERNS["spotify"].search(url)
    if not m:
        raise ValueError("Invalid Spotify URL")
    kind = m.group(1)

    async with aiohttp.ClientSession(headers=HEADERS) as session:

        if kind == "track":
            # oEmbed gives us title + artist for a single track
            oembed_url = f"https://open.spotify.com/oembed?url={url}"
            async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    raise ValueError(f"Spotify oEmbed failed: HTTP {resp.status}")
                data = await resp.json(content_type=None)

            title     = data.get("title", "")
            # title from oEmbed is usually "Song Name - Artist" or just "Song Name"
            thumbnail = data.get("thumbnail_url", "")
            if not title:
                raise ValueError("Spotify se track info nahi mili")

            tracks = await _ytdl_fetch(title, single=True)
            if tracks:
                tracks[0]["platform"]  = "Spotify"
                tracks[0]["thumbnail"] = tracks[0].get("thumbnail") or thumbnail
            return tracks

        elif kind in ("album", "playlist"):
            # For albums/playlists use oEmbed to get name, then search YouTube
            oembed_url = f"https://open.spotify.com/oembed?url={url}"
            async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None) if resp.status == 200 else {}

            name = data.get("title", "Spotify Playlist")
            # Return a stub that searches by name — best we can do without Premium
            return [{
                "title":       name,
                "url":         None,
                "_search":     name,
                "webpage_url": url,
                "duration":    0,
                "thumbnail":   data.get("thumbnail_url", ""),
                "uploader":    "Spotify",
                "platform":    "Spotify",
            }]

        elif kind == "artist":
            oembed_url = f"https://open.spotify.com/oembed?url={url}"
            async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None) if resp.status == 200 else {}
            artist = data.get("title", "")
            if artist:
                return await _ytdl_fetch(f"{artist} top songs", single=True)
            raise ValueError("Artist info nahi mili")

    raise ValueError("Unsupported Spotify URL type")

# ─────────────────────────────────────────────
#  Apple Music resolver
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
#  Main public function
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
