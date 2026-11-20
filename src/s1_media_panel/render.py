"""Pillow renderer for landscape and portrait LCDs."""
from functools import lru_cache

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .media import number, text

LABELS = {
    "time": "Time", "date": "Date", "temperature": "CPU temp", "cpu": "CPU",
    "gpu": "GPU", "ram": "RAM", "disk": "Storage", "disk_temperature": "Disk temp",
    "network": "Network", "uptime": "Uptime", "server": "Server",
    "audio_language": "Audio", "subtitle_language": "Subtitles", "dropped": "Dropped",
}


@lru_cache(maxsize=16)
def font(path, size):
    return ImageFont.truetype(path, size)


def fit(value, face, width):
    clean = text(value)
    if face.getlength(clean) <= width:
        return clean
    left, right = 0, len(clean)
    while left < right:
        middle = (left + right + 1) // 2
        if face.getlength(clean[:middle] + "…") <= width:
            left = middle
        else:
            right = middle - 1
    return clean[:left] + "…"


def clock(seconds):
    if seconds is None:
        return "—"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02}:{seconds:02}"


def render(size, config, mode, data):
    width, height = size
    brightness = config[f"{mode}_brightness"]
    background, foreground, accent = [
        tuple(round(channel * brightness) for channel in ImageColor.getrgb(config[key]))
        for key in ("background", "foreground", "accent")
    ]
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    small = font(config["font"], 12)
    body = font(config["font"], 15)
    large = font(config["font"], 23)
    margin, usable = 12, width - 24

    def label(x, y, value, face=small, color=foreground, limit=usable):
        draw.text((x, y), fit(value, face, limit), fill=color, font=face, anchor="lt")

    if mode == "idle":
        label(margin, 12, "SYSTEM", body, accent)
        fields = [(key, data.get(key)) for key in config["metrics"] if data.get(key) is not None]
        columns = 2
        stride = usable // columns
        rows = (height - 40) // 21
        for index, (key, value) in enumerate(fields[:rows * columns]):
            x = margin + index % columns * stride
            y = 40 + index // columns * 21
            label(x, y, f"{LABELS[key]}  {value}", limit=stride - 4)
        return image

    label(margin, 12, "PAUSED" if mode == "paused" else "PLAYING", body, accent)
    name = text(data.get("title")) or "Media playback"
    fitted = fit(name, large, usable)
    if fitted == name:
        first = name
    else:
        prefix = fitted[:-1]
        boundary = prefix.rfind(" ")
        first = prefix[:boundary] if boundary > 0 else name.split(" ", 1)[0]
    label(margin, 35, first, large)
    rest = name[len(first):].strip()
    label(margin, 61, rest if rest and not data.get("series") else data.get("series") or rest, body)
    elapsed, duration = number(data.get("elapsed")), number(data.get("duration"))
    ratio = min(1, elapsed / duration) if elapsed is not None and duration else 0
    draw.rectangle((margin, 87, width - margin, 92), fill=foreground)
    if ratio:
        draw.rectangle((margin, 87, margin + int(ratio * usable), 92), fill=accent)
    remaining = max(0, duration - elapsed) if elapsed is not None and duration else None
    timeline = f"{clock(elapsed)} / {clock(duration)}"
    if remaining is not None:
        timeline += f"   −{clock(remaining)}"
    label(margin, 99, timeline)
    values = [f"{LABELS[key]} {data[key]}" if key in LABELS else str(data[key])
              for key in config["details"] if data.get(key) not in (None, "")]
    y, line = 118, ""
    for value in values:
        candidate = f"{line}  ·  {value}" if line else value
        if line and small.getlength(candidate) > usable:
            label(margin, y, line)
            y += 15
            line = value
        else:
            line = candidate
        if y + 13 >= height:
            return image
    label(margin, y, line)
    return image
