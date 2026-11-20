import json
from concurrent.futures import Future

import dbus
import tempfile
import stat
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import usb.core
from PIL import Image

from s1_media_panel.config import load
from s1_media_panel.media import Mpris, mpv_properties, playback
from s1_media_panel.monitor import Monitor
from s1_media_panel.protocol import BUFFER_SIZE, LCDProtocol, to_rgb565
from s1_media_panel.render import render
from s1_media_panel.service import Display, Service
from s1_media_panel.usb_transport import Transport


class ProtocolTests(unittest.TestCase):
    def test_rgb565(self):
        image = Image.new("RGB", (3, 1))
        image.putdata([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
        self.assertEqual(to_rgb565(image), bytes.fromhex("f80007e0001f"))

    def test_full_frame(self):
        frames = []
        lcd = LCDProtocol(lambda frame: frames.append(bytes(frame)))
        lcd.redraw(Image.new("RGB", lcd.size, "red"))
        self.assertEqual(len(frames), 27)
        self.assertTrue(all(len(frame) == BUFFER_SIZE for frame in frames))
        self.assertEqual(frames[0][:10].hex(), "55a3f00100000010f800")
        self.assertEqual(frames[16][:8].hex(), "55a3f11100000010")
        self.assertEqual(frames[-1][:8].hex(), "55a3f21b00a00009")
        self.assertEqual(frames[-1][2312:], bytes(1792))

    def test_rectangular_frame(self):
        send = Mock()
        lcd = LCDProtocol(send)
        image = Image.new("RGB", lcd.size, "red")
        self.assertTrue(lcd.update_region(image, (2, 3, 4, 4)))
        data = send.call_args.args[0]
        self.assertEqual(len(data), BUFFER_SIZE)
        self.assertEqual(data[:12].hex(), "55a2020003000201f800f800")
        self.assertFalse(lcd.update_region(image, (0, 0, 320, 170)))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.options = load()["usb"]
        self.transport = Transport(self.options)
        self.dev = Mock(bcdDevice=self.options["revision"])
        self.dev.get_active_configuration.return_value = {(1, 0): SimpleNamespace(bInterfaceClass=3)}
        self.dev.is_kernel_driver_active.return_value = False
        self.find = self.enterContext(patch("usb.core.find", return_value=[self.dev]))
        self.enterContext(patch("usb.util.find_descriptor", return_value=SimpleNamespace(bmAttributes=3)))
        self.claim = self.enterContext(patch("usb.util.claim_interface"))
        self.dispose = self.enterContext(patch("usb.util.dispose_resources"))
        self.dev.write.return_value = BUFFER_SIZE

    def test_complete_short_and_timeout(self):
        self.transport.open()
        self.claim.assert_called_once_with(self.dev, 1)
        self.transport.send(bytes(BUFFER_SIZE))
        self.dev.write.assert_called_once_with(2, bytes(BUFFER_SIZE), timeout=1000)
        self.dev.write.return_value = 64
        with self.assertRaisesRegex(OSError, "64/4104"):
            self.transport.send(bytes(BUFFER_SIZE))
        self.dev.write.side_effect = usb.core.USBTimeoutError("timeout")
        with self.assertRaises(usb.core.USBTimeoutError):
            self.transport.send(bytes(BUFFER_SIZE))

    def test_absent_and_ambiguous(self):
        for devices in ([], [self.dev, self.dev]):
            self.find.return_value = devices
            with self.assertRaises(OSError):
                self.transport.open()
        self.claim.assert_not_called()

    def test_missing_interface_cleanup(self):
        self.dev.get_active_configuration.return_value = {}
        with self.assertRaisesRegex(OSError, "interface 01 missing"):
            self.transport.open()
        self.dispose.assert_called_once_with(self.dev)

    def test_failed_claim_cleanup(self):
        self.claim.side_effect = usb.core.USBError("busy")
        with self.assertRaises(usb.core.USBError):
            self.transport.open()
        self.dispose.assert_called_once_with(self.dev)

    def test_pacing_and_custom_timeout(self):
        self.options.update(pace_ms=50, timeout_ms=2000)
        self.transport.open()
        with patch("s1_media_panel.usb_transport.time.monotonic", side_effect=[10, 10.02, 10.05]), patch(
            "s1_media_panel.usb_transport.time.sleep"
        ) as sleep:
            self.transport.send(bytes(BUFFER_SIZE))
            self.transport.send(bytes(BUFFER_SIZE))
        self.assertAlmostEqual(sleep.call_args.args[0], 0.03)
        self.assertEqual(self.dev.write.call_args.kwargs["timeout"], 2000)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.clock = lambda: self.now
        self.config = load()
        self.transport = Mock()
        self.display = Display(self.transport, self.config["usb"], self.clock, wait=Mock())

    def test_heartbeat_deadline_moves_only_on_success(self):
        self.display.open()
        self.assertEqual(self.display.next_heartbeat, 11)
        self.now = 10.99
        self.assertFalse(self.display.heartbeat())
        self.assertEqual(self.display.next_heartbeat, 11)
        self.now = 11
        self.transport.send.side_effect = OSError
        with self.assertRaises(OSError):
            self.display.heartbeat()
        self.assertEqual(self.display.next_heartbeat, 11)
        self.transport.send.side_effect = None
        self.assertTrue(self.display.heartbeat())
        self.assertEqual(self.display.next_heartbeat, 12)

    def test_failed_orientation_releases(self):
        self.transport.send.side_effect = OSError
        with self.assertRaises(OSError):
            self.display.open()
        self.transport.close.assert_called_once()

    def test_cleanup_failure_does_not_hide_open_error(self):
        original = usb.core.USBTimeoutError("timeout")
        self.transport.send.side_effect = original
        self.transport.close.side_effect = OSError("cleanup")
        with self.assertLogs(level="WARNING"), self.assertRaises(usb.core.USBTimeoutError) as raised:
            self.display.open()
        self.assertIs(raised.exception, original)

    def test_sleep_respects_heartbeat_and_retry_deadlines(self):
        service = Service(self.config, lambda: self.display, Mock(), Mock(), self.clock)
        self.config["service"]["poll"] = 20
        service.retry_at = 12
        self.assertEqual(service.wait_time(), 2)
        self.display.open()
        service.display = self.display
        self.assertEqual(service.wait_time(), 1)

    def test_identical_image_not_sent_twice(self):
        image = Image.new("RGB", self.display.size)
        self.display.paint(image)
        self.display.paint(image)
        self.assertEqual(self.transport.send.call_count, 27)

    def test_modes_and_reconnect(self):
        media, monitor = Mock(), Mock()
        media.read.return_value = None
        monitor.read.return_value = {}
        service = Service(self.config, lambda: self.display, media, monitor, self.clock)
        service.tick()
        self.assertEqual((service.mode, self.transport.send.call_count), ("idle", 29))
        media.read.return_value = {"mode": "playing"}
        service.tick()
        self.assertEqual(service.mode, "playing")
        media.read.return_value = {"mode": "paused"}
        service.tick()
        self.assertEqual(service.mode, "paused")
        media.read.return_value = None
        self.transport.send.side_effect = OSError("simulated")
        with self.assertLogs(level="WARNING"):
            service.tick()
        self.assertIsNone(service.display)
        self.now += self.config["usb"]["retry"] - 0.01
        service.tick()
        self.transport.open.assert_called_once()
        self.now += 0.01
        self.transport.send.side_effect = None
        service.tick()
        self.assertEqual((service.mode, self.transport.open.call_count), ("idle", 2))
        self.assertIsNotNone(self.display.previous)

    def test_render_failure_keeps_usb(self):
        media, monitor = Mock(), Mock()
        media.read.return_value = None
        monitor.read.side_effect = OSError
        service = Service(self.config, lambda: self.display, media, monitor, self.clock)
        with self.assertLogs(level="WARNING"):
            service.tick()
        self.assertIs(service.display, self.display)
        self.transport.close.assert_not_called()


class InputsAndRenderTests(unittest.TestCase):
    def test_titles(self):
        config = load()["display"]
        for title in ("Brief", "A long title " * 80, "Title\nEpisode", "Été", "朝の海 ☀", "", None):
            with self.subTest(title=title):
                image = render((320, 170), config, "playing", {"title": title})
                self.assertEqual(image.size, (320, 170))

    def test_playback_and_progress(self):
        data = playback({"PlaybackStatus": "Paused",
                         "Metadata": {"xesam:title": "Imaginary Series - s1e3 - The Tide"}},
                        3_000_000, {"duration": 10, "video-params": {"gamma": "pq"}})
        self.assertEqual((data["mode"], data["elapsed"], data["duration"]), ("paused", 3, 10))
        self.assertEqual((data["title"], data["series"], data["range"]),
                         ("The Tide", "Imaginary Series · S01E03", "HDR PQ"))
        image = render((320, 170), load()["display"], "paused", data)
        self.assertNotEqual(image.getpixel((20, 89)), image.getpixel((240, 89)))
        self.assertIsNone(playback({"PlaybackStatus": "Stopped"}, None, {}))

    def test_missing_metadata_and_sources(self):
        self.assertEqual(playback({"PlaybackStatus": "Playing", "Metadata": []}, None, {})["title"], "Media playback")
        self.assertIsNone(Mpris({"mpris_name": ""}).read())
        self.assertEqual(mpv_properties("/nonexistent/socket"), {})

    def test_private_mpv_ipc(self):
        owner = 1234
        parent = SimpleNamespace(st_uid=owner, st_mode=stat.S_IFDIR | 0o700)
        node = SimpleNamespace(st_uid=owner, st_mode=stat.S_IFSOCK | 0o600)
        ipc = Mock()
        ipc.recv.return_value = b"".join(
            (json.dumps({"request_id": index, "data": 5, "error": "success" if index == 1 else "unavailable"}) + "\n").encode()
            for index in range(1, 11)
        )
        with patch("s1_media_panel.media.os.getuid", return_value=owner), patch.object(
            Path, "stat", return_value=parent
        ), patch.object(Path, "lstat", return_value=node), patch("s1_media_panel.media.socket.socket") as factory:
            factory.return_value.__enter__.return_value = ipc
            values = mpv_properties("/run/user/example/player.sock")
            self.assertEqual(values["time-pos"], 5)
            self.assertIsNone(values["duration"])
            requests = [json.loads(line)["command"] for line in ipc.sendall.call_args.args[0].splitlines()]
            self.assertEqual(len(requests), 10)
            self.assertTrue(all(command[0] == "get_property" for command in requests))
            node.st_mode = stat.S_IFSOCK | 0o660
            self.assertEqual(mpv_properties("/run/user/example/player.sock"), {})
            factory.assert_called_once()

    def test_mpris_does_not_activate_missing_player(self):
        config = load()["media"]
        source = Mpris(config)
        with patch("dbus.SessionBus") as connection:
            bus = connection.return_value
            bus.call_blocking.side_effect = dbus.DBusException("missing player")
            self.assertIsNone(source.read())
            bus.get_object.assert_not_called()
            self.assertEqual(bus.call_blocking.call_args.args[3:6],
                             ("GetNameOwner", "s", (config["mpris_name"],)))
            self.assertEqual(bus.call_blocking.call_args.kwargs["timeout"], 0.25)

    def test_failed_probe_does_not_wedge_future(self):
        config = load()["monitor"]
        monitor = Monitor(config, [])
        monitor.pool = Mock()
        monitor.future = Future()
        monitor.future.set_exception(ValueError("invalid request"))
        with self.assertRaises(ValueError):
            monitor.read()
        self.assertIsNone(monitor.future)
        monitor.read()
        monitor.pool.submit.assert_called_once()
        monitor.close()

    def test_no_network_counters(self):
        config = load()["monitor"]
        config["network_interface"] = "example0"
        monitor = Monitor(config, ["network"])
        with patch("psutil.net_io_counters", return_value=None):
            self.assertNotIn("network", monitor.read())

    def test_indeterminate_metrics(self):
        config = load()["monitor"]
        monitor = Monitor(config, ["temperature", "disk_temperature", "network", "server"])
        with patch("psutil.sensors_temperatures", return_value={}):
            self.assertEqual(set(monitor.read()), {"time", "date"})
        monitor.close()

    def test_example_config(self):
        self.assertEqual(load(Path("config.example.toml")), load())

    def test_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.toml"
            for content in ('[usb]\nheartbeat = nan', '[usb]\nports = [true]', '[monitor]\nserver_url = "http://example.org"', '[display]\nghost = true',
                            '[display]\nfont = "nonexistent-panel-font.ttf"',
                            '[monitor]\nserver_url = "https://example.org/a b"',
                            '[service]\npoll = ' + '9' * 400):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        load(path)
