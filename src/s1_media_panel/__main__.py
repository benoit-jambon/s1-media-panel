import argparse
import logging
import os
import signal
from pathlib import Path
from threading import Event

from . import __version__
from . import brightness
from .config import load
from .media import Mpris
from .monitor import Monitor
from .render import render
from .service import Display, Service


def preview(directory, config):
    directory.mkdir(parents=True, exist_ok=True)
    system = {"time": "18:42", "date": "2030-04-15", "temperature": "43 °C",
              "cpu": "17%", "gpu": "6%", "ram": "42%", "disk": "31%",
              "disk_temperature": "35 °C", "network": "↓12 ↑3 KiB/s",
              "uptime": "4h 13m", "server": "Reachable"}
    movie = {"title": "Atlas of Imaginary Tides", "series": "Fictional Series · S01E03",
             "elapsed": 754, "duration": 1530, "resolution": "3840×2160",
             "video_codec": "HEVC", "audio_codec": "AAC", "decoder": "VAAPI",
             "range": "HDR PQ", "audio_language": "EN", "subtitle_language": "FR",
             "dropped": "0"}
    size = (170, 320) if config["usb"]["orientation"].startswith("portrait") else (320, 170)
    for mode, data in (("idle", system), ("playing", movie), ("paused", movie)):
        render(size, config["display"], mode, data).save(directory / f"{mode}.png")


def main():
    parser = argparse.ArgumentParser(description="S1 Media Panel LCD dashboard")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--preview", type=Path, metavar="DIRECTORY")
    parser.add_argument("--check-config", action="store_true")
    commands = parser.add_subparsers(dest="command")
    brightness.add_commands(commands.add_parser("brightness", help="Luminosité logicielle du LCD"))
    args = parser.parse_args()
    if args.command == "brightness":
        return brightness.run(args)
    default = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "s1-media-panel/config.toml"
    try:
        if args.config is not None:
            config = load(args.config)
        else:
            try:
                config = load(default)
            except FileNotFoundError:
                config = load()
        if args.preview:
            preview(args.preview, config)
        if args.preview or args.check_config:
            return 0
    except (OSError, ValueError) as error:
        parser.error(f"configuration or preview failed ({type(error).__name__})")
    from .usb_transport import Transport

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    monitor = Monitor(config["monitor"], config["display"]["metrics"])
    service = Service(config, lambda: Display(Transport(config["usb"]), config["usb"]),
                      Mpris(config["media"]), monitor)
    try:
        while not stop.is_set():
            service.tick()
            stop.wait(service.wait_time())
    finally:
        service.close()
        monitor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
