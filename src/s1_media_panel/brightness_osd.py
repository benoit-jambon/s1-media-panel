"""Une fenêtre par session. Les demandes et sa fermeture partagent un verrou."""
import os
import select
import stat
import subprocess
import sys
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .brightness import read_level, validate


def notify(preference):
    from importlib.util import find_spec

    if find_spec("PyQt6") is None:
        raise ImportError("PyQt6")

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or ".")
    if not runtime.is_absolute():
        raise OSError("L’OSD nécessite XDG_RUNTIME_DIR dans la session graphique.")
    if not any(os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM")):
        raise OSError("Aucun affichage graphique disponible pour l’OSD.")
    info = runtime.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("XDG_RUNTIME_DIR doit être un répertoire privé de l’utilisateur.")
    folder = runtime / "s1-media-panel-osd"
    folder.mkdir(mode=0o700, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("Le répertoire de l’OSD doit être privé.")
    request_path = folder / "request"
    with os.fdopen(os.open(request_path, os.O_CREAT | os.O_RDWR, 0o600), "r+b") as request:
        flock(request, LOCK_EX)
        level = read_level(preference)
        request.write(uuid4().bytes + bytes([level]))
        request.flush()
        with (folder / "instance").open("ab") as instance:
            try:
                flock(instance, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                return
            # Le processus graphique hérite du verrou, même au premier appui simultané.
            fd = instance.fileno()
            child = subprocess.Popen(
                [sys.executable, "-m", __name__, str(request_path), str(fd)],
                pass_fds=(fd,), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
    with child.stdout as ready:
        if not select.select([ready], [], [], 5)[0] or ready.read(1) != b"1":
            raise OSError("La fenêtre OSD n’a pas pu démarrer.")


class Session:
    def __init__(self, window, request_path, close, clock=monotonic):
        self.window = window
        self.request_path = request_path
        self.close = close
        self.clock = clock
        self.previous = None
        self.deadline = 0.0

    def poll(self):
        with self.request_path.open("rb") as request:
            flock(request, LOCK_EX)
            message = request.read(17)
            if len(message) == 17 and message != self.previous:
                level = validate(message[-1])
                self.window.present(level)
                self.previous = message
                self.deadline = self.clock() + 2
            elif self.clock() >= self.deadline:
                # Libérer le verrou sous celui des demandes évite de perdre le dernier appui.
                self.window.hide()
                self.close()


def run(request_path, instance):
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import QApplication

    from .brightness_ui import Overlay

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    timer = QTimer(interval=40, timerType=Qt.TimerType.PreciseTimer)

    def close():
        timer.stop()
        os.close(instance)
        app.quit()

    session = Session(Overlay(), request_path, close)

    def poll():
        try:
            session.poll()
        except (ValueError, OSError):
            close()

    timer.timeout.connect(poll)
    session.poll()
    timer.start()
    sys.stdout.buffer.write(b"1")
    sys.stdout.buffer.flush()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run(Path(sys.argv[1]), int(sys.argv[2])))
