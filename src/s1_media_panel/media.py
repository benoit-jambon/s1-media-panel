"""Read-only MPRIS and optional local MPV input."""
import json
import os
import re
import socket
import stat
from pathlib import Path
from time import monotonic

import dbus

MPRIS = "org.mpris.MediaPlayer2"
PROPERTIES = (
    "time-pos", "duration", "video-params", "video-codec", "audio-codec",
    "hwdec-current", "current-tracks/audio", "current-tracks/sub",
    "frame-drop-count", "options/force-media-title",
)


def mapping(value):
    return value if isinstance(value, dict) else {}


def text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(value[:4096].split())


def title(value):
    value = text(value)
    return "" if "://" in value or value.startswith("/") else value


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 10**15:
        return float(value)
    return None


def language(track):
    return text(mapping(track).get("lang")).upper() or None


def playback(props, position, mpv, show_titles=True):
    state = props.get("PlaybackStatus")
    if state not in ("Playing", "Paused"):
        return None
    mode = "paused" if state == "Paused" else "playing"
    meta = mapping(props.get("Metadata"))
    name = (title(mpv.get("options/force-media-title")) or title(meta.get("xesam:title"))) if show_titles else ""
    episode = re.fullmatch(r"(.*?) - s(\d{1,4})e(\d{1,4}) - (.*)", name, flags=re.IGNORECASE)
    subtitle = ""
    if episode:
        series, season, episode_number, name = episode.groups()
        subtitle = f"{series} · S{int(season):02}E{int(episode_number):02}"
    video = mapping(mpv.get("video-params"))
    width, height = number(video.get("w")), number(video.get("h"))
    profile = number(video.get("dolby-vision-profile"))
    gamma = text(video.get("gamma"))
    dynamic = {"pq": "HDR PQ", "hlg": "HDR HLG", "bt.1886": "SDR",
               "bt.709": "SDR", "srgb": "SDR"}.get(gamma)
    decoder = text(mpv.get("hwdec-current"))
    elapsed, duration = number(mpv.get("time-pos")), number(mpv.get("duration"))
    position, length = number(position), number(meta.get("mpris:length"))
    dropped = number(mpv.get("frame-drop-count"))
    return {
        "mode": mode, "title": name or "Media playback", "series": subtitle,
        "elapsed": elapsed if elapsed is not None else (position / 1e6 if position is not None else None),
        "duration": duration or (length / 1e6 if length else None),
        "resolution": f"{width:.0f}×{height:.0f}" if width and height else None,
        "video_codec": text(mpv.get("video-codec")).upper(),
        "audio_codec": text(mpv.get("audio-codec")).upper(),
        "range": f"Dolby Vision P{profile:.0f}" if profile and profile.is_integer() else dynamic,
        "decoder": "Software" if decoder == "no" else decoder.upper(),
        "audio_language": language(mpv.get("current-tracks/audio")),
        "subtitle_language": language(mpv.get("current-tracks/sub")),
        "dropped": f"{dropped:.0f}" if dropped is not None else None,
    }


def mpv_properties(path):
    """A fresh socket correlates responses without retaining titles or playback history."""
    if not path:
        return {}
    result = {}
    try:
        path = Path(os.path.expandvars(path)).expanduser()
        parent, node = path.parent.stat(), path.lstat()
        if (not path.is_absolute() or os.getuid() != parent.st_uid or parent.st_mode & 0o077
                or os.getuid() != node.st_uid or node.st_mode & 0o077 or not stat.S_ISSOCK(node.st_mode)):
            return {}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as ipc:
            ipc.settimeout(0.25)
            ipc.connect(str(path))
            queries = [
                json.dumps({"command": ["get_property", prop], "request_id": index})
                for index, prop in enumerate(PROPERTIES, 1)
            ]
            ipc.sendall(("\n".join(queries) + "\n").encode())
            deadline = monotonic() + 0.25
            buffer = b""
            while len(result) < len(PROPERTIES):
                ipc.settimeout(max(0.001, deadline - monotonic()))
                if monotonic() >= deadline:
                    break
                data = ipc.recv(8192)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    reply = mapping(json.loads(line))
                    code = reply.get("request_id")
                    if type(code) is int and 1 <= code <= len(PROPERTIES):
                        value = reply.get("data") if reply.get("error") == "success" else None
                        result[PROPERTIES[code - 1]] = value
                if len(buffer) > 65536:
                    break
    except (OSError, ValueError):
        pass
    return result


class Mpris:
    def __init__(self, config):
        self.config = config
        self.bus = None

    def read(self):
        if not self.config["mpris_name"]:
            return None
        try:
            if self.bus is None:
                self.bus = dbus.SessionBus()
            owner = self.bus.call_blocking(
                dbus.BUS_DAEMON_NAME, dbus.BUS_DAEMON_PATH, dbus.BUS_DAEMON_IFACE,
                "GetNameOwner", "s", (self.config["mpris_name"],), timeout=0.25,
            )
            proxy = self.bus.get_object(owner, "/org/mpris/MediaPlayer2", introspect=False)
            interface = dbus.Interface(proxy, "org.freedesktop.DBus.Properties")
            props = interface.GetAll(f"{MPRIS}.Player", timeout=0.25)
            if props.get("PlaybackStatus") not in ("Playing", "Paused"):
                return None
            try:
                position = interface.Get(f"{MPRIS}.Player", "Position", timeout=0.25)
            except dbus.DBusException:
                position = None
        except dbus.DBusException:
            self.bus = None
            return None
        return playback(props, position, mpv_properties(self.config["mpv_socket"]), self.config["show_titles"])
