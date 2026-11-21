"""Réglage manuel partagé entre le service, le panneau et la CLI."""
import os
import stat
import sys
from fcntl import LOCK_EX, flock
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile

STEP = 5
FULL = 100


def state_path():
    root = Path(os.environ.get("XDG_STATE_HOME") or ".")
    if not root.is_absolute():
        root = Path.home() / ".local/state"
    return root / "s1-media-panel/brightness"


def validate(level):
    if type(level) is not int or not 0 <= level <= FULL or level % STEP:
        raise ValueError("Choisissez un multiple de 5 entre 0 et 100.")
    return level


def read_level(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return FULL
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("La préférence doit être un fichier ordinaire.")
        data = stream.read(17)
    if len(data) > 16 or not data.strip().isdigit():
        raise ValueError("Préférence de luminosité illisible.")
    return validate(int(data))


def change_level(path, action, level=None):
    if action == "set":
        validate(level)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(path.with_suffix(".lock"), flags, 0o600), "rb+") as lock:
        flock(lock, LOCK_EX)
        # Un réglage absolu permet de réparer une préférence malformée.
        if action != "set":
            delta = {"up": STEP, "down": -STEP}[action]
            current = read_level(path)
            level = max(0, min(FULL, current + delta))
            if level == current:
                return level
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            try:
                stream.write(f"{level}\n".encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temp, path)
                directory = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temp.unlink(missing_ok=True)
    return level


@lru_cache(maxsize=20)
def _channels(level):
    return [round(channel * level / FULL) for channel in range(256)] * 3


def dim(image, level):
    return image if level == FULL else image.point(_channels(level))


def add_commands(parser):
    parser.add_argument("--osd", action="store_true", help="Afficher le niveau pendant deux secondes")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("status", "up", "down", "panel"):
        actions.add_parser(action)
    actions.add_parser("set").add_argument("level", type=int, metavar="POURCENT")


def run(args):
    path = state_path()
    try:
        if args.action == "panel":
            if args.osd:
                raise ValueError("Choisissez le panneau ou une commande avec --osd.")
            from .brightness_ui import run_panel
            return run_panel(path)
        if args.action == "status":
            level = read_level(path)
        else:
            level = change_level(path, args.action, getattr(args, "level", None))
        print(f"{level} %", flush=True)
        if args.osd:
            from .brightness_osd import notify
            notify(path)
    except ImportError:
        print("L’interface graphique nécessite PyQt6 (option gui).", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Luminosité LCD : {error}", file=sys.stderr)
        return 1
    return 0
