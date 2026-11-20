"""Strict TOML configuration with defaults independent of any installation."""
import copy
import tomllib
from urllib.parse import urlsplit

from PIL import ImageColor, ImageFont

METRICS = ("time", "date", "temperature", "cpu", "gpu", "ram", "disk",
           "disk_temperature", "network", "uptime", "server")
DETAILS = ("resolution", "video_codec", "audio_codec", "decoder", "range",
           "audio_language", "subtitle_language", "dropped")
DEFAULTS = {
    "usb": {"vid": 0x04D9, "pid": 0xFD01, "revision": 0x0110, "bus": 0,
            "ports": [], "orientation": "landscape", "pace_ms": 0, "timeout_ms": 1000,
            "heartbeat": 1.0, "retry": 5.0, "partial": False},
    "display": {"font": "DejaVuSans.ttf", "background": "#101825", "foreground": "#eef4ff",
                "accent": "#58d6c5", "idle_brightness": 1.0, "playing_brightness": 0.85,
                "paused_brightness": 0.9, "metrics": list(METRICS), "details": list(DETAILS)},
    "service": {"mode": "auto", "idle": 5.0, "playing": 1.0, "paused": 2.0, "poll": 0.5},
    "media": {"mpris_name": "org.mpris.MediaPlayer2.com.github.iwalton3.jellyfin-mpv-shim",
              "mpv_socket": "", "show_titles": True},
    "monitor": {"disk_path": "/", "storage_sensor": "", "network_interface": "",
                "server_url": "", "server_interval": 60.0},
}


def load(path=None):
    result = copy.deepcopy(DEFAULTS)
    if path is not None:
        with path.open("rb") as stream:
            sections = tomllib.load(stream)
        for section, values in sections.items():
            if section not in result or not isinstance(values, dict):
                raise ValueError(f"Unknown configuration section: {section}")
            for key, value in values.items():
                default = result[section].get(key)
                allowed = (int, float) if isinstance(default, float) else (type(default),)
                if default is None or type(value) not in allowed:
                    raise ValueError(f"Unknown or invalid setting: {section}.{key}")
                result[section][key] = value

    usb, display, service = (result[name] for name in ("usb", "display", "service"))
    for key in ("vid", "pid", "revision"):
        if not 0 <= usb[key] <= 65535:
            raise ValueError(f"usb.{key} must be a 16-bit value")
    if not 0 <= usb["bus"] <= 255 or any(type(p) is not int or not 1 <= p <= 255 for p in usb["ports"]):
        raise ValueError("Invalid USB bus or port path")
    if usb["orientation"] not in ("landscape", "landscape-180", "portrait", "portrait-180"):
        raise ValueError("Invalid USB orientation")
    if not 0 <= usb["pace_ms"] <= 1000 or not 1 <= usb["timeout_ms"] <= 10000:
        raise ValueError("Invalid USB pacing or timeout")
    if service["mode"] not in ("auto", "idle"):
        raise ValueError("Invalid service mode")
    for value in (usb["heartbeat"], usb["retry"], service["poll"], service["idle"],
                  service["playing"], service["paused"], result["monitor"]["server_interval"]):
        if not 0.1 <= value <= 86400:
            raise ValueError("Intervals must be between 0.1 and 86400 seconds")
    for key in ("idle_brightness", "playing_brightness", "paused_brightness"):
        if not 0 <= display[key] <= 1:
            raise ValueError("Brightness must be between zero and one")
    for key in ("background", "foreground", "accent"):
        if len(ImageColor.getrgb(display[key])) != 3:
            raise ValueError("Display colors must be RGB")
    try:
        ImageFont.truetype(display["font"], 12)
    except (OSError, ValueError) as error:
        raise ValueError("display.font cannot be opened") from error
    for key, choices in (("metrics", METRICS), ("details", DETAILS)):
        if any(item not in choices for item in display[key]):
            raise ValueError(f"Unknown display field in {key}")
    url = result["monitor"]["server_url"]
    if url:
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
            raise ValueError("HTTPS probe URL contains whitespace or control characters")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment):
            raise ValueError("The server probe requires an HTTPS URL without credentials, query or fragment")
        if parsed.port == 0:
            raise ValueError("HTTPS probe port cannot be zero")
    return result
