"""
bot.py — Chaos Cafe Music Bot
Slash commands + Hades-style buttons (Pause · Skip · Shuffle · Stop · Like)
Supports: YouTube · Spotify · SoundCloud · Bandcamp · Mixcloud
          Deezer · Apple Music · Vimeo · Twitch · Direct URLs
"""

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import random
import shutil
import yt_dlp
import static_ffmpeg
static_ffmpeg.add_paths()
from collections import deque

from config import (
    DISCORD_TOKEN, DEFAULT_VOLUME,
    COLOR_PLAYING, COLOR_QUEUE, COLOR_ERROR, COLOR_SUCCESS,
)
from platform_resolver import resolve, resolve_stub, PLATFORM_EMOJIS

# ─────────────────────────────────────────────
#  FFmpeg — auto-find on Windows/Mac/Linux
# ─────────────────────────────────────────────
FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ─────────────────────────────────────────────
#  Bot setup
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────────────────────
#  Per-guild music state
# ─────────────────────────────────────────────
class GuildMusicState:
    def __init__(self):
        self.queue: deque                     = deque()
        self.current: dict | None             = None
        self.voice_client: discord.VoiceClient | None = None
        self.volume: float                    = DEFAULT_VOLUME / 100
        self.loop: bool                       = False
        self.loop_queue: bool                 = False
        self._history: list                   = []
        self.liked: list                      = []
        self.np_message: discord.Message | None = None  # track NP message for button updates

music_states: dict[int, GuildMusicState] = {}

def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in music_states:
        music_states[guild_id] = GuildMusicState()
    return music_states[guild_id]

# ─────────────────────────────────────────────
#  Utility helpers
# ─────────────────────────────────────────────
def fmt_duration(seconds) -> str:
    seconds = int(seconds or 0)
    mins, secs = divmod(seconds, 60)
    hrs, mins  = divmod(mins, 60)
    return f"{hrs}:{mins:02d}:{secs:02d}" if hrs else f"{mins}:{secs:02d}"

def platform_badge(track: dict) -> str:
    p = (track.get("platform") or "").lower()
    for key, emoji in PLATFORM_EMOJIS.items():
        if key in p:
            return emoji
    return "🎵"

def make_np_embed(track: dict, state: GuildMusicState) -> discord.Embed:
    badge = platform_badge(track)
    embed = discord.Embed(
        title="Now Playing",
        description=f"**[{track['title']}]({track.get('webpage_url', '')})**",
        color=COLOR_PLAYING,
    )
    embed.set_author(name=f"{badge} Chaos Cafe Music")
    embed.set_thumbnail(url=track.get("thumbnail", ""))
    embed.add_field(name="Duration",   value=fmt_duration(track.get("duration")))
    embed.add_field(name="Uploader",   value=track.get("uploader", "Unknown"))
    embed.add_field(name="Platform",   value=track.get("platform", "Unknown"))
    if state.queue:
        next_track = list(state.queue)[0]
        embed.add_field(name="Up Next", value=next_track["title"], inline=False)
    mode = "🔂 Loop" if state.loop else ("🔁 Queue Loop" if state.loop_queue else "▶ Normal")
    embed.set_footer(text=f"Mode: {mode}  •  Volume: {int(state.volume * 100)}%  •  Queue: {len(state.queue)} songs")
    return embed

def make_added_embed(track: dict, position: int, emoji: str, platform: str) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Enqueued Track",
        color=COLOR_SUCCESS,
    )
    embed.set_thumbnail(url=track.get("thumbnail", ""))
    embed.add_field(name="", value=f"**[{track['title']}]({track.get('webpage_url', '')})**", inline=False)
    embed.add_field(name="Duration",  value=fmt_duration(track.get("duration")))
    embed.add_field(name="Platform",  value=f"{emoji} {platform.title()}")
    embed.add_field(name="Position",  value=f"#{position}")
    return embed

# ─────────────────────────────────────────────
#  Now Playing buttons (Hades-style)
# ─────────────────────────────────────────────
class NowPlayingView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _state(self) -> GuildMusicState:
        return get_state(self.guild_id)

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️")
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.pause()
            button.label = "Resume"
            button.emoji  = discord.PartialEmoji.from_str("▶️")
            button.style  = discord.ButtonStyle.success
            await interaction.response.edit_message(view=self)
        elif state.voice_client and state.voice_client.is_paused():
            state.voice_client.resume()
            button.label = "Pause"
            button.emoji  = discord.PartialEmoji.from_str("⏸️")
            button.style  = discord.ButtonStyle.secondary
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
            await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)

    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, emoji="🔀")
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if len(state.queue) < 2:
            await interaction.response.send_message("❌ Shuffle ke liye 2+ songs chahiye!", ephemeral=True)
            return
        q = list(state.queue)
        random.shuffle(q)
        state.queue = deque(q)
        await interaction.response.send_message("🔀 Queue shuffle ho gayi!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.voice_client:
            state.queue.clear()
            state.current = None
            state.voice_client.stop()
            await interaction.response.send_message("⏹️ Stopped!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)

    @discord.ui.button(label="Like", style=discord.ButtonStyle.success, emoji="❤️")
    async def like_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self._state()
        if state.current:
            title = state.current["title"]
            if title not in [t["title"] for t in state.liked]:
                state.liked.append(state.current)
                await interaction.response.send_message(f"❤️ **{title}** liked!", ephemeral=True)
            else:
                await interaction.response.send_message(f"Already liked **{title}**!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)

# ─────────────────────────────────────────────
#  Playback engine
# ─────────────────────────────────────────────
async def play_next(interaction_or_channel, state: GuildMusicState):
    """Play next track. interaction_or_channel can be a channel or interaction."""
    # Get a sendable channel
    if isinstance(interaction_or_channel, discord.TextChannel):
        channel = interaction_or_channel
    elif hasattr(interaction_or_channel, "channel"):
        channel = interaction_or_channel.channel
    else:
        channel = interaction_or_channel

    if state.loop and state.current:
        state.queue.appendleft(state.current)
    if state.loop_queue and state.current:
        state.queue.append(state.current)

    if not state.queue:
        state.current = None
        embed = discord.Embed(description="✅ Queue khatam! `/play` karo aur songs daalo 🎶", color=COLOR_QUEUE)
        await channel.send(embed=embed)
        return

    track = state.queue.popleft()

    # Resolve stub (Spotify playlist tracks)
    if not track.get("url"):
        try:
            track = await resolve_stub(track)
        except Exception as e:
            await channel.send(f"❌ `{track['title']}` skip — resolve nahi hua: `{e}`")
            return await play_next(channel, state)

    state.current = track
    state._history.append(track)
    if len(state._history) > 20:
        state._history.pop(0)

    try:
        source = discord.FFmpegPCMAudio(
            track["url"],
            executable=FFMPEG_PATH,
            **FFMPEG_OPTIONS,
        )
        source = discord.PCMVolumeTransformer(source, volume=state.volume)
    except Exception as e:
        await channel.send(f"❌ Audio error: `{e}`")
        return await play_next(channel, state)

    def after_cb(error):
        if error:
            print(f"[Player Error] {error}")
        asyncio.run_coroutine_threadsafe(play_next(channel, state), bot.loop)

    state.voice_client.play(source, after=after_cb)

    embed = make_np_embed(track, state)
    view  = NowPlayingView(guild_id=channel.guild.id if hasattr(channel, 'guild') else channel.guild.id)
    msg   = await channel.send(embed=embed, view=view)
    state.np_message = msg

# ─────────────────────────────────────────────
#  Voice helper
# ─────────────────────────────────────────────
async def ensure_voice(interaction: discord.Interaction, state: GuildMusicState) -> bool:
    if not interaction.user.voice:
        await interaction.followup.send("❌ Pehle kisi voice channel mein join karo!", ephemeral=True)
        return False
    if not state.voice_client or not state.voice_client.is_connected():
        state.voice_client = await interaction.user.voice.channel.connect()
    elif state.voice_client.channel != interaction.user.voice.channel:
        await state.voice_client.move_to(interaction.user.voice.channel)
    return True

# ─────────────────────────────────────────────
#  Events
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online — Chaos Cafe ready!")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="/play | Chaos Cafe 🎶")
    )

# ─────────────────────────────────────────────
#  Autocomplete — fast YouTube suggestions via scrape
# ─────────────────────────────────────────────
import aiohttp

async def play_autocomplete(interaction: discord.Interaction, current: str):
    if not current or len(current) < 2:
        return []
    if current.startswith("http"):
        return [app_commands.Choice(name=current[:100], value=current[:100])]
    try:
        url = "https://suggestqueries.google.com/complete/search"
        params = {"client": "youtube", "ds": "yt", "q": current}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                import json
                text = await resp.text()
                # Response is JSONP: window.google.ac.h([...])
                data = json.loads(text[text.index("["):text.rindex("]") + 1])
                suggestions = [item[0] for item in data[1] if isinstance(item, list)][:8]
                return [app_commands.Choice(name=s[:100], value=s[:100]) for s in suggestions]
    except Exception:
        return []

# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

@bot.tree.command(name="play", description="Koi bhi song ya URL play karo (YouTube, Spotify, SoundCloud...)")
@app_commands.describe(query="Song ka naam ya link")
@app_commands.autocomplete(query=play_autocomplete)
async def play(interaction: discord.Interaction, query: str):
    try:
        await interaction.response.defer()
    except Exception:
        return  # interaction already expired
    state = get_state(interaction.guild.id)
    if not await ensure_voice(interaction, state):
        return

    try:
        tracks, platform, emoji = await resolve(query)
    except Exception as e:
        return await interaction.followup.send(f"❌ Track nahi mila: `{e}`")

    if not tracks:
        return await interaction.followup.send("❌ Koi result nahi mila!")

    for t in tracks:
        state.queue.append(t)

    count = len(tracks)
    if count > 1:
        embed = discord.Embed(
            title=f"{emoji} Playlist Added — {count} songs",
            description=f"**{count} songs** queue mein aa gaye!",
            color=COLOR_SUCCESS,
        )
        embed.add_field(name="Pehla Track", value=tracks[0]["title"])
        embed.add_field(name="Platform", value=f"{emoji} {platform.title()}")
        await interaction.followup.send(embed=embed)
    else:
        track = tracks[0]
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            embed = make_added_embed(track, len(state.queue), emoji, platform)
            await interaction.followup.send(embed=embed)

    if not state.voice_client.is_playing() and not state.voice_client.is_paused():
        await play_next(interaction.channel, state)
    elif count == 1 and not (state.voice_client.is_playing() or state.voice_client.is_paused()):
        pass  # already handled above


@bot.tree.command(name="skip", description="Current song skip karo")
async def skip(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if not state.voice_client or not state.voice_client.is_playing():
        return await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)
    state.voice_client.stop()
    await interaction.response.send_message("⏭️ Skipped!")


@bot.tree.command(name="pause", description="Playback pause karo")
async def pause(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.pause()
        await interaction.response.send_message("⏸️ Paused.")
    else:
        await interaction.response.send_message("❌ Kuch chal nahi raha!", ephemeral=True)


@bot.tree.command(name="resume", description="Playback resume karo")
async def resume(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client and state.voice_client.is_paused():
        state.voice_client.resume()
        await interaction.response.send_message("▶️ Resumed!")
    else:
        await interaction.response.send_message("❌ Kuch paused nahi hai!", ephemeral=True)


@bot.tree.command(name="stop", description="Sab band karo aur queue clear karo")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client:
        state.queue.clear()
        state.current = None
        state.voice_client.stop()
        await interaction.response.send_message("⏹️ Stopped!")
    else:
        await interaction.response.send_message("❌ Kuch chal hi nahi raha!", ephemeral=True)


@bot.tree.command(name="queue", description="Current queue dekho")
async def queue_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    embed = discord.Embed(title="🎵 Chaos Cafe — Queue", color=COLOR_QUEUE)

    if state.current:
        badge = platform_badge(state.current)
        embed.add_field(
            name="🎶 Ab Chal Raha Hai",
            value=f"{badge} **{state.current['title']}** `{fmt_duration(state.current.get('duration'))}`",
            inline=False,
        )
    if state.queue:
        lines = []
        for i, t in enumerate(list(state.queue)[:15], 1):
            badge = platform_badge(t)
            lines.append(f"`{i}.` {badge} {t['title']} `{fmt_duration(t.get('duration'))}`")
        if len(state.queue) > 15:
            lines.append(f"*...aur {len(state.queue) - 15} songs*")
        embed.add_field(name=f"Aage ke Songs ({len(state.queue)})", value="\n".join(lines), inline=False)
    elif not state.current:
        embed.description = "Queue khaali hai! `/play` karo 🎶"

    modes = []
    if state.loop:       modes.append("🔂 Track")
    if state.loop_queue: modes.append("🔁 Queue")
    if not modes:        modes.append("▶ Normal")
    embed.set_footer(text=f"Mode: {' | '.join(modes)}  •  Volume: {int(state.volume * 100)}%")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="Ab kya chal raha hai")
async def nowplaying(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if not state.current:
        return await interaction.response.send_message("❌ Abhi kuch nahi chal raha!", ephemeral=True)
    view = NowPlayingView(guild_id=interaction.guild.id)
    await interaction.response.send_message(embed=make_np_embed(state.current, state), view=view)


@bot.tree.command(name="volume", description="Volume set karo (0-100)")
@app_commands.describe(level="Volume level (0-100)")
async def volume(interaction: discord.Interaction, level: int):
    if not 0 <= level <= 100:
        return await interaction.response.send_message("❌ 0 se 100 ke beech honi chahiye!", ephemeral=True)
    state = get_state(interaction.guild.id)
    state.volume = level / 100
    if state.voice_client and state.voice_client.source:
        state.voice_client.source.volume = state.volume
    await interaction.response.send_message(f"🔊 Volume **{level}%**!")


@bot.tree.command(name="shuffle", description="Queue shuffle karo")
async def shuffle(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if len(state.queue) < 2:
        return await interaction.response.send_message("❌ 2+ songs chahiye!", ephemeral=True)
    q = list(state.queue)
    random.shuffle(q)
    state.queue = deque(q)
    await interaction.response.send_message("🔀 Queue shuffle ho gayi!")


@bot.tree.command(name="loop", description="Current track loop karo")
async def loop_track(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    state.loop = not state.loop
    if state.loop: state.loop_queue = False
    await interaction.response.send_message(f"Track loop: **{'🔂 ON' if state.loop else 'OFF'}**")


@bot.tree.command(name="loopqueue", description="Poori queue loop karo")
async def loop_queue_cmd(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    state.loop_queue = not state.loop_queue
    if state.loop_queue: state.loop = False
    await interaction.response.send_message(f"Queue loop: **{'🔁 ON' if state.loop_queue else 'OFF'}**")


@bot.tree.command(name="remove", description="Queue se ek song hatao")
@app_commands.describe(position="Song ka queue number")
async def remove(interaction: discord.Interaction, position: int):
    state = get_state(interaction.guild.id)
    if not 1 <= position <= len(state.queue):
        return await interaction.response.send_message(f"❌ Queue mein {len(state.queue)} songs hain.", ephemeral=True)
    q = list(state.queue)
    removed = q.pop(position - 1)
    state.queue = deque(q)
    await interaction.response.send_message(f"🗑️ **{removed['title']}** remove ho gaya.")


@bot.tree.command(name="skipto", description="Queue ke kisi bhi position pe jump karo")
@app_commands.describe(position="Jump karne ki position")
async def skipto(interaction: discord.Interaction, position: int):
    state = get_state(interaction.guild.id)
    if not 1 <= position <= len(state.queue):
        return await interaction.response.send_message(f"❌ Queue mein {len(state.queue)} songs hain.", ephemeral=True)
    for _ in range(position - 1):
        state.queue.popleft()
    state.voice_client.stop()
    await interaction.response.send_message(f"⏩ Position **{position}** pe jump!")


@bot.tree.command(name="clear", description="Poori queue saaf karo")
async def clear(interaction: discord.Interaction):
    get_state(interaction.guild.id).queue.clear()
    await interaction.response.send_message("🗑️ Queue saaf!")


@bot.tree.command(name="liked", description="Tumhare liked songs dekho")
async def liked(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if not state.liked:
        return await interaction.response.send_message("❌ Abhi tak koi song like nahi kiya!", ephemeral=True)
    embed = discord.Embed(title="❤️ Liked Songs", color=0xFF6B6B)
    lines = [f"`{i}.` {platform_badge(t)} {t['title']}" for i, t in enumerate(state.liked[-15:], 1)]
    embed.description = "\n".join(lines)
    embed.set_footer(text="Chaos Cafe 🎶")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="history", description="Haal mein bajaaye songs dekho")
async def history(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if not state._history:
        return await interaction.response.send_message("❌ Abhi tak koi song nahi baja!", ephemeral=True)
    embed = discord.Embed(title="🕓 Recently Played", color=COLOR_QUEUE)
    lines = [f"`{i}.` {platform_badge(t)} {t['title']}" for i, t in enumerate(reversed(state._history[-10:]), 1)]
    embed.description = "\n".join(lines)
    embed.set_footer(text="Chaos Cafe 🎶")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leave", description="Bot ko voice channel se hatao")
async def leave(interaction: discord.Interaction):
    state = get_state(interaction.guild.id)
    if state.voice_client and state.voice_client.is_connected():
        state.queue.clear()
        state.current = None
        await state.voice_client.disconnect()
        state.voice_client = None
        await interaction.response.send_message("👋 Phir milenge Chaos Cafe mein! 🎶")
    else:
        await interaction.response.send_message("❌ Main kisi channel mein hoon hi nahi!", ephemeral=True)


@bot.tree.command(name="help", description="Saare commands dekho")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎵 Chaos Cafe Music Bot",
        description=(
            "**Supported Platforms:**\n"
            "🎬 YouTube  🟢 Spotify  🟠 SoundCloud  🔵 Bandcamp\n"
            "🌀 Mixcloud  💜 Deezer  🍎 Apple Music  🎞 Vimeo  💜 Twitch  🔗 Direct URL"
        ),
        color=COLOR_QUEUE,
    )
    fields = {
        "▶ Playback": [
            ("/play `<song/URL>`", "Kisi bhi platform se play karo"),
            ("/pause · /resume",   "Ruk jao / Dobara shuru karo"),
            ("/skip",              "Agla song"),
            ("/skipto `<#>`",      "Seedha kisi number pe jaao"),
            ("/stop",              "Sab band karo"),
            ("/volume `<0-100>`",  "Volume set karo"),
        ],
        "📋 Queue": [
            ("/queue",             "Poori queue dekho"),
            ("/nowplaying",        "Ab kya chal raha hai (buttons ke saath)"),
            ("/shuffle",           "Queue shuffle karo"),
            ("/loop",              "Ek track repeat karo"),
            ("/loopqueue",         "Poori queue repeat karo"),
            ("/remove `<#>`",      "Ek song queue se hatao"),
            ("/clear",             "Poori queue saaf karo"),
        ],
        "❤️ Other": [
            ("/liked",             "Liked songs dekho"),
            ("/history",           "Pichle songs dekho"),
            ("/leave",             "Bot ko hatao"),
        ],
    }
    for section, cmds in fields.items():
        val = "\n".join(f"`{c}` — {d}" for c, d in cmds)
        embed.add_field(name=section, value=val, inline=False)
    embed.set_footer(text="Chaos Cafe 🎶 — Har platform, ek hi bot!")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
#  Run
# ─────────────────────────────────────────────
bot.run(DISCORD_TOKEN)
