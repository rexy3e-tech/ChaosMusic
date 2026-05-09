# 🎵 Chaos Cafe Music Bot — Setup Guide

Supports: **YouTube · Spotify · SoundCloud · Bandcamp · Mixcloud · Deezer · Apple Music · Vimeo · Twitch · Direct URLs**

---

## 1. Install FFmpeg (REQUIRED)

**Windows:** `winget install ffmpeg`
**Mac:** `brew install ffmpeg`
**Linux:** `sudo apt install ffmpeg`

---

## 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## 3. Discord Bot Setup

1. Go to https://discord.com/developers/applications → New Application
2. Bot tab → Add Bot
3. Enable **Message Content Intent**
4. Copy your Token

---

## 4. Spotify Setup

1. Go to https://developer.spotify.com/dashboard → Create App
2. Copy Client ID and Client Secret

---

## 5. Fill in config.py

```python
DISCORD_TOKEN         = "your_discord_token"
SPOTIFY_CLIENT_ID     = "your_spotify_client_id"
SPOTIFY_CLIENT_SECRET = "your_spotify_client_secret"
```

---

## 6. Run

```bash
python bot.py
```

---

## Supported Platforms

| Platform | Example |
|---|---|
| YouTube | `!play song name` or YouTube URL |
| Spotify track/album/playlist | Spotify URL |
| SoundCloud | SoundCloud URL |
| Bandcamp | Bandcamp URL |
| Mixcloud | Mixcloud URL |
| Deezer | Deezer URL |
| Apple Music | Apple Music URL |
| Vimeo | Vimeo URL |
| Twitch | Twitch channel URL |
| Direct audio | .mp3 / .flac / .wav URL |

---

## Note on Spotify
Spotify audio is fetched via YouTube search (same method Rythm/Hades used). Playlist tracks load lazily — each one fetches audio only when it's about to play.
