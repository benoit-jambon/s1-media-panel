import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from s1_media_panel import brightness
from s1_media_panel.config import load
from s1_media_panel.render import render
from s1_media_panel.service import Display, Service

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from s1_media_panel.brightness_ui import Overlay, Panel
except ImportError:
    QApplication = None


class Isolated(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, HOME=root, XDG_STATE_HOME=root,
                                    XDG_RUNTIME_DIR=root, QT_QPA_PLATFORM="offscreen"))
        self.path = brightness.state_path()

    def cli(self, *args):
        return subprocess.run([sys.executable, "-m", "s1_media_panel", "brightness", *args],
                              capture_output=True, text=True, timeout=8)


class PreferenceTests(Isolated):
    def test_all_levels_and_pixels(self):
        original = Image.new("RGB", (256, 1))
        colors = [(i, 255-i, 127) for i in range(256)]
        original.putdata(colors)
        for level in range(0, 101, 5):
            with self.subTest(level=level):
                brightness.change_level(self.path, "set", level)
                self.assertEqual(brightness.read_level(self.path), level)
                expected = bytes(round(c * level / 100) for pixel in colors for c in pixel)
                self.assertEqual(brightness.dim(original, level).tobytes(), expected)
        self.assertIs(brightness.dim(original, 100), original)
        self.assertIsNone(brightness.dim(original, 0).getbbox())

    def test_invalid_levels_and_bounds(self):
        for invalid in (-5, 1, 99, 101, True, 5.0, None):
            with self.assertRaises(ValueError):
                brightness.change_level(self.path, "set", invalid)
        self.assertEqual(brightness.change_level(self.path, "up"), 100)
        brightness.change_level(self.path, "set", 0)
        self.assertEqual(brightness.change_level(self.path, "down"), 0)
        self.assertEqual(brightness.change_level(self.path, "up"), 5)

    def test_relative_xdg_falls_back(self):
        with patch.dict(os.environ, XDG_STATE_HOME="relative"):
            self.assertEqual(brightness.state_path(), Path.home() / ".local/state/s1-media-panel/brightness")

    def test_cli_and_persistence(self):
        for args, expected in [(("status",), 100), (("set", "0"), 0), (("up",), 5),
                               (("down",), 0), (("set", "100"), 100), (("status",), 100)]:
            result = self.cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), f"{expected} %")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        for value in ("7", "nan", "-5", "105"):
            self.assertNotEqual(self.cli("set", value).returncode, 0)
        self.assertEqual(brightness.read_level(self.path), 100)

    def test_corrupt_and_repair(self):
        self.path.parent.mkdir()
        for content in (b"", b"33", b"-5", b"105", b"\xff", b"1" * 17):
            self.path.write_bytes(content)
            with self.assertRaises(ValueError):
                brightness.read_level(self.path)
        self.assertNotEqual(self.cli("status").returncode, 0)
        self.assertNotEqual(self.cli("down").returncode, 0)
        self.assertEqual(self.cli("set", "50").returncode, 0)
        self.assertEqual(brightness.read_level(self.path), 50)

    def test_atomic_failure(self):
        brightness.change_level(self.path, "set", 40)
        with patch("os.replace", side_effect=OSError), self.assertRaises(OSError):
            brightness.change_level(self.path, "set", 80)
        self.assertEqual(brightness.read_level(self.path), 40)
        self.assertEqual(set(self.path.parent.iterdir()), {self.path, self.path.with_suffix(".lock")})

    def test_concurrent_relative_requests(self):
        brightness.change_level(self.path, "set", 0)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: brightness.change_level(self.path, "up"), range(20)))
        self.assertEqual(sorted(results), list(range(5, 101, 5)))
        self.assertEqual(brightness.read_level(self.path), 100)

    def test_osd_unavailable_keeps_saved_choice(self):
        with patch.dict(os.environ, XDG_RUNTIME_DIR="", DISPLAY="", WAYLAND_DISPLAY="", QT_QPA_PLATFORM=""):
            result = self.cli("--osd", "set", "30")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(brightness.read_level(self.path), 30)


class ServiceTests(Isolated):
    def test_modes_keep_level_and_ignore_legacy_settings(self):
        config_file = Path.home() / "old.toml"
        config_file.write_text("[display]\nidle_brightness=0.3\nplaying_brightness=0.1\npaused_brightness=0.0")
        config = load(config_file)
        display = Mock(size=(320, 170))
        service = Service(config, lambda: display, Mock(), Mock())
        service.monitor.read.return_value = {"time": "12:30"}
        brightness.change_level(self.path, "set", 65)
        for mode in ("idle", "playing", "paused", "idle"):
            data = {"mode": mode, "title": "Exemple"} if mode != "idle" else service.monitor.read.return_value
            service.media.read.return_value = data if mode != "idle" else None
            service.tick()
            original = render(display.size, config["display"], mode, data)
            self.assertEqual(original.getpixel((0, 0)), (16, 24, 37))
            self.assertEqual(display.paint.call_args.args[0].tobytes(), brightness.dim(original, 65).tobytes())
            self.assertEqual(brightness.read_level(self.path), 65)

    def test_black_heartbeat_deduplication_and_restoration(self):
        config = load()
        now = Mock(return_value=0)
        transport, media, monitor = Mock(), Mock(), Mock()
        display = Display(transport, config["usb"], now, wait=Mock())
        frames = []
        transport.send.side_effect = lambda buf: frames.append(bytes(buf))
        service = Service(config, lambda: display, media, monitor, now)
        media.read.return_value = None
        monitor.read.return_value = {"time": "08:45"}
        service.tick()
        original = frames[-27:]
        frames.clear()
        brightness.change_level(self.path, "set", 0)
        service.tick()
        self.assertEqual(len(frames), 27)
        self.assertTrue(all(frame[:2] == b"\x55\xa3" and not any(frame[8:]) for frame in frames))
        frames.clear()
        now.return_value = 1.1
        media.read.return_value = {"mode": "paused"}
        service.tick()
        self.assertEqual([f[:3] for f in frames], [bytes.fromhex("55a1f2")])
        media.read.return_value = None
        brightness.change_level(self.path, "set", 100)
        service.tick()
        self.assertEqual(frames[1:], original)
        transport.close.assert_not_called()

    def test_brightness_during_monitor_failure(self):
        display = Mock(size=(320, 170))
        monitor, media = Mock(), Mock()
        media.read.return_value = None
        monitor.read.side_effect = OSError
        service = Service(load(), lambda: display, media, monitor)
        brightness.change_level(self.path, "set", 0)
        with self.assertLogs(level="WARNING"):
            service.tick()
        self.assertIsNone(display.paint.call_args.args[0].getbbox())
        display.close.assert_not_called()

    def test_bad_preference_does_not_interrupt_service(self):
        config, display = load(), Mock(size=(320, 170))
        service = Service(config, lambda: display, Mock(), Mock())
        service.media.read.return_value = {"mode": "playing"}
        brightness.change_level(self.path, "set", 40)
        service.tick()
        self.path.write_text("invalid")
        with self.assertLogs(level="WARNING") as messages:
            service.tick()
            service.tick()
        self.assertEqual(len(messages.output), 1)
        self.assertEqual(service.level, 40)
        brightness.change_level(self.path, "set", 80)
        service.tick()
        self.assertEqual(service.level, 80)
        display.close.assert_not_called()


@unittest.skipUnless(QApplication, "PyQt6 (option gui) nécessaire")
class GraphicalTests(Isolated):
    def setUp(self):
        super().setUp()
        self.app = QApplication.instance() or QApplication([])

    def test_panel_controls_and_external_changes(self):
        panel = Panel(self.path)
        for step in range(21):
            panel.slider.setValue(step)
            self.assertEqual(panel.percent.text(), f"{step * 5} %")
            self.assertEqual(brightness.read_level(self.path), step * 5)
        for button, expected in [(panel.down, 95), (panel.up, 100), (panel.down, 95), (panel.reset, 100)]:
            button.click()
            self.assertEqual(brightness.read_level(self.path), expected)
        brightness.change_level(self.path, "set", 25)
        panel.refresh()
        self.assertEqual(panel.slider.value(), 5)
        self.path.write_text("invalid")
        panel.refresh()
        self.assertTrue(panel.message.text())
        panel.reset.click()
        self.assertEqual(panel.percent.text(), "100 %")
        panel.close()
        self.assertEqual(self.cli("down").stdout.strip(), "95 %")

    def test_overlay_updates_without_focus_and_expires(self):
        from s1_media_panel.brightness_osd import Session

        request = Path.home() / "request"
        now, close = Mock(return_value=10), Mock()
        window = Overlay()
        session = Session(window, request, close, now)
        for index, (instant, level) in enumerate(((10, 0), (11.9, 100), (12.1, 100))):
            now.return_value = instant
            request.write_bytes(bytes([index]) * 16 + bytes([level]))
            session.poll()
            self.assertTrue(window.isVisible())
            self.assertEqual(window.percent.text(), f"{level} %")
            self.assertEqual(window.bar.value(), level)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowTransparentForInput)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        now.return_value = 14.09
        session.poll()
        close.assert_not_called()
        now.return_value = 14.1
        session.poll()
        self.assertFalse(window.isVisible())
        close.assert_called_once()

    def test_single_detached_osd_for_repeated_commands(self):
        from s1_media_panel.brightness_osd import notify

        real_launch = subprocess.Popen
        children = []

        def start(*args, **kwargs):
            children.append(real_launch(*args, **kwargs))
            return children[-1]

        with patch("subprocess.Popen", side_effect=start) as launch:
            notify(self.path)
            try:
                for _ in range(6):
                    notify(self.path)
                launch.assert_called_once()
                lock = Path.home() / "s1-media-panel-osd/instance"
                with lock.open("rb") as instance, self.assertRaises(BlockingIOError):
                    flock(instance, LOCK_EX | LOCK_NB)
                self.assertEqual(children[0].wait(timeout=5), 0)
                with lock.open("rb") as instance:
                    flock(instance, LOCK_EX | LOCK_NB)
            finally:
                if children[0].poll() is None:
                    children[0].terminate()
                    children[0].wait()
