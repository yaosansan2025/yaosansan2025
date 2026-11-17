#!/usr/bin/env python3
"""从 Lanyard 生成包含应用和 Spotify 播放状态的 GitHub 卡片。"""

import base64
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import re
from time import time_ns
import urllib.request
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets" / "discord-status.svg"
APP_ICONS = {"chatgpt": ROOT / "assets" / "app-icons" / "chatgpt.png"}
STATUS = {
    "online": ("Online", "#3ba55d"),
    "idle": ("Idle", "#faa61a"),
    "dnd": ("Do not disturb", "#ed4245"),
    "offline": ("Offline", "#80848e"),
}


def fetch_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": "GitHub-Discord-Status"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read(), response.headers.get_content_type()


def image_data(url):
    content, mime = fetch_bytes(url)
    if not mime.startswith("image/"):
        raise ValueError("图片接口没有返回图像，保留上次生成的卡片。")
    return "data:" + mime + ";base64," + base64.b64encode(content).decode("ascii")


def local_image(path):
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def select_activity(data):
    # 离线时不展示可能残留的活动。
    if data.get("discord_status") == "offline":
        return None
    activities = data.get("activities", [])
    return next((item for item in activities if item.get("type") == 0), None) or next(
        (item for item in activities if item.get("type") == 2 and item.get("name") != "Spotify"),
        None,
    )


def spotify_playback(data):
    if data.get("discord_status") == "offline" or not data.get("listening_to_spotify"):
        return None
    spotify = data.get("spotify")
    return spotify if spotify and spotify.get("song") else None


def spotify_cover(spotify):
    url = (spotify or {}).get("album_art_url") or ""
    if not url.startswith("https://i.scdn.co/image/"):
        return None
    try:
        return image_data(url)
    except (OSError, ValueError):
        print("Spotify cover unavailable; displaying track details only.")
        return None


def activity_image(activity):
    if not activity:
        return None
    # 手动注册的应用没有 Discord 图片时，按名称使用本地应用图标。
    key = activity.get("name", "").strip().casefold()
    asset = (activity.get("assets") or {}).get("large_image")
    if not asset and key in APP_ICONS:
        return local_image(APP_ICONS[key])
    if not asset:
        return None
    if asset.startswith("mp:external/"):
        asset = re.sub(r"^mp:external/[^/]+/(https?)/", r"\1://", asset)
    elif not asset.startswith(("https://", "http://")):
        asset = "https://cdn.discordapp.com/app-assets/{}/{}.png".format(
            activity["application_id"], asset
        )
    return image_data(asset)


def avatar_image(user):
    if user.get("avatar"):
        url = "https://cdn.discordapp.com/avatars/{}/{}.png?size=128".format(
            user["id"], user["avatar"]
        )
    else:
        discriminator = user.get("discriminator", "0")
        index = (int(user["id"]) >> 22) % 6 if discriminator == "0" else int(discriminator) % 5
        url = "https://cdn.discordapp.com/embed/avatars/{}.png".format(index)
    return image_data(url)


def shorten(value, limit):
    return value if len(value) <= limit else value[: limit - 1] + "…"


def playback_time(milliseconds):
    minutes, seconds = divmod(milliseconds // 1000, 60)
    return f"{minutes:02d}:{seconds:02d}"


def render_playback_progress(spotify):
    timestamps = spotify.get("timestamps") or {}
    start = timestamps.get("start")
    end = timestamps.get("end")
    if type(start) is not int or type(end) is not int or end <= start:
        return ""
    duration = end - start
    elapsed = min(duration, max(0, time_ns() // 1_000_000 - start))
    track_width = 246
    fill = track_width * elapsed / duration
    return f'''    <g id="spotify-progress" transform="translate(120 87)"
       aria-label="{playback_time(elapsed)} of {playback_time(duration)}">
      <text x="0" y="4" font-family="monospace" font-size="11" fill="#f0f6fc">{playback_time(elapsed)}</text>
      <rect x="45" y="-2" width="{track_width}" height="4" rx="2" fill="#41444b"/>
      <rect x="45" y="-2" width="{fill:.1f}" height="4" rx="2" fill="#f0f6fc"/>
      <text x="336" y="4" text-anchor="end" font-family="monospace" font-size="11" fill="#f0f6fc">{playback_time(duration)}</text>
    </g>
'''


def render_spotify(spotify, cover, top):
    title = escape(shorten(spotify["song"], 38))
    artist = escape(shorten((spotify.get("artist") or "Unknown artist").replace("; ", ", "), 46))
    cover_markup = (
        '<image x="24" y="18" width="80" height="80" clip-path="url(#spotify-cover)" href="{}"/>'.format(
            escape(cover, quote=True)
        )
        if cover
        else '<rect x="24" y="18" width="80" height="80" rx="10" fill="#30363d"/>'
        '<path d="M61 72V43l18-4v8l-14 3v20a5 5 0 1 1-4-5" fill="#1ed760"/>'
    )
    return '''<g transform="translate(0 {top})">
    <path d="M24 0h432" stroke="#30363d"/>
    <defs><clipPath id="spotify-cover"><rect x="24" y="18" width="80" height="80" rx="10"/></clipPath></defs>
    {cover}
    <svg x="120" y="20" width="336" height="55" overflow="hidden">
      <text x="0" y="11" font-size="11" font-weight="700" fill="#1ed760">LISTENING ON SPOTIFY</text>
      <text x="0" y="34" font-size="18" font-weight="600" fill="#f0f6fc">{title}</text>
      <text x="0" y="53" font-size="14" fill="#b5bac1">{artist}</text>
    </svg>
{progress}  </g>'''.format(
        top=top, cover=cover_markup, title=title, artist=artist, progress=render_playback_progress(spotify)
    )


def render_card(data, avatar, icon, spotify=None, cover=None):
    user = data["discord_user"]
    username = escape(shorten(user.get("global_name") or user["username"], 28))
    status, color = STATUS.get(data.get("discord_status"), STATUS["offline"])
    activity = select_activity(data)
    activity_name = escape(shorten(activity.get("name", "Activity"), 34)) if activity else "No activity"
    subtitle = "Currently active" if activity else "Not currently using an app"
    if activity and (activity.get("timestamps") or {}).get("start"):
        started = datetime.fromtimestamp(activity["timestamps"]["start"] / 1000, ZoneInfo("America/Phoenix"))
        # 显示开始时间，避免仅因计时变化而不断产生提交。
        subtitle = "Started at {} (UTC−07:00)".format(started.strftime("%H:%M"))
    icon_markup = (
        '<image x="24" y="112" width="56" height="56" href="{}"/>'.format(escape(icon, quote=True))
        if icon
        else '<rect x="24" y="112" width="56" height="56" rx="12" fill="#30363d"/>'
        '<path d="M39 128h26v22H39z M39 134h26 M45 128v22" fill="none" stroke="#8b949e" stroke-width="2"/>'
    )
    activity_markup = '''<path d="M24 96h432" stroke="#30363d"/>
    {icon}
    <text x="98" y="135" font-size="19" font-weight="600" fill="#f0f6fc">{name}</text>
    <text x="98" y="159" font-size="13" fill="#b5bac1">{subtitle}</text>'''.format(
        icon=icon_markup, name=activity_name, subtitle=escape(subtitle)
    ) if activity or not spotify else ""
    activity_desc = activity_name if activity_markup else ""
    if spotify:
        music_desc = escape(shorten(spotify["song"], 120))
        if spotify.get("artist"):
            music_desc += " by " + escape(shorten(spotify["artist"], 120))
        activity_desc += (" — " if activity_desc else "") + "Listening on Spotify: " + music_desc
    spotify_markup = render_spotify(spotify, cover, 192 if activity else 96) if spotify else ""
    height = (192 if activity else 96) + 116 if spotify else 192
    return '''<svg xmlns="http://www.w3.org/2000/svg" width="480" height="{height}" viewBox="0 0 480 {height}" role="img" aria-labelledby="title desc">
  <title id="title">Discord status</title>
  <desc id="desc">{username} — {status} — {activity_desc}</desc>
  <defs><clipPath id="avatar"><circle cx="52" cy="50" r="30"/></clipPath></defs>
  <rect x="0.5" y="0.5" width="479" height="{background_height}" rx="16" fill="#1b1c20" stroke="#30363d"/>
  <image x="22" y="20" width="60" height="60" clip-path="url(#avatar)" href="{avatar}"/>
  <circle cx="75" cy="72" r="10" fill="{color}" stroke="#1b1c20" stroke-width="4"/>
  <g font-family="Segoe UI, Arial, sans-serif">
    <text x="100" y="45" font-size="21" font-weight="700" fill="#f0f6fc">{username}</text>
    <text x="100" y="68" font-size="14" fill="#b5bac1">{status}</text>
    {activity_markup}{spotify_markup}
  </g>
</svg>
'''.format(
        username=username, status=status, activity_desc=activity_desc, avatar=escape(avatar, quote=True),
        color=color, height=height, background_height=height - 1, activity_markup=activity_markup,
        spotify_markup="\n    " + spotify_markup if spotify_markup else ""
    )


def main():
    user_id = os.environ["DISCORD_USER_ID"]
    content, _ = fetch_bytes("https://api.lanyard.rest/v1/users/" + user_id)
    payload = json.loads(content)
    if not payload.get("success"):
        raise RuntimeError("Lanyard 没有返回有效状态，保留上次生成的卡片。")
    data = payload["data"]
    spotify = spotify_playback(data)
    svg = render_card(
        data, avatar_image(data["discord_user"]), activity_image(select_activity(data)),
        spotify, spotify_cover(spotify)
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # 在所有请求成功后再写入，避免网络故障覆盖正常卡片。
    OUTPUT.write_text(svg, encoding="utf-8")
    print("Discord status card generated.")


if __name__ == "__main__":
    main()
