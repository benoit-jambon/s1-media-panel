"""Serialized LCD output and recoverable application lifecycle."""
import logging
from time import monotonic, sleep

from PIL import Image, ImageChops

from .protocol import LCDProtocol
from .render import render

LOG = logging.getLogger(__name__)


class Display:
    def __init__(self, transport, options, clock=monotonic, wait=sleep):
        self.transport, self.options, self.clock, self.wait = transport, options, clock, wait
        self.protocol = LCDProtocol(transport.send, options["orientation"].startswith("portrait"))
        self.previous = self.next_heartbeat = None

    @property
    def size(self):
        return self.protocol.size

    def open(self):
        self.transport.open()
        try:
            self.protocol.set_orientation(self.protocol.portrait)
            self.wait(0.05)
            self.heartbeat()
        except BaseException:
            try:
                self.close()
            except (OSError, ValueError) as error:
                LOG.warning("LCD cleanup after initialization failed (%s)", type(error).__name__)
            raise

    def heartbeat(self):
        if self.next_heartbeat is not None and self.clock() < self.next_heartbeat:
            return False
        self.protocol.heartbeat()
        self.next_heartbeat = self.clock() + self.options["heartbeat"]
        return True

    def paint(self, image):
        if self.options["orientation"].endswith("-180"):
            image = image.transpose(Image.Transpose.ROTATE_180)
        if self.previous is not None:
            box = ImageChops.difference(image, self.previous).getbbox()
            if box is None:
                return
            if (self.options["partial"] and not self.protocol.portrait
                    and self.protocol.update_region(image, box)):
                self.previous = image.copy()
                return
        self.protocol.redraw(image)
        self.previous = image.copy()

    def close(self):
        self.previous = self.next_heartbeat = None
        self.transport.close()


class Service:
    def __init__(self, config, make_display, media, monitor, clock=monotonic):
        self.options, self.usb, self.colors = (config[section] for section in ("service", "usb", "display"))
        self.make_display, self.media, self.monitor, self.clock = make_display, media, monitor, clock
        self.display = self.mode = None
        self.retry_at = self.render_at = 0
        self.incidents = set()

    def warn(self, key, error):
        if key not in self.incidents:
            LOG.warning("%s (%s)", key, type(error).__name__)
            self.incidents.add(key)

    def tick(self):
        if self.display is None:
            if self.clock() < self.retry_at:
                return
            try:
                candidate = self.make_display()
                candidate.open()
                self.display = candidate
                self.mode = None
            except (OSError, ValueError) as error:
                self.warn("LCD unavailable", error)
                self.retry_at = self.clock() + self.usb["retry"]
                return
        try:
            self.display.heartbeat()
            self.update_view()
            self.display.heartbeat()
            self.incidents.discard("LCD unavailable")
            self.incidents.discard("LCD transfer failed")
        except (OSError, ValueError) as error:
            if "LCD transfer failed" not in self.incidents:
                LOG.warning("LCD transfer failed: %s", error)
                self.incidents.add("LCD transfer failed")
            self.close()
            self.retry_at = self.clock() + self.usb["retry"]

    def wait_time(self):
        deadline = self.display.next_heartbeat if self.display is not None else self.retry_at
        return min(self.options["poll"], max(0, deadline - self.clock()))

    def update_view(self):
        try:
            media = self.media.read() if self.options["mode"] == "auto" else None
            self.incidents.discard("Media source failure")
        except (OSError, ValueError, TypeError) as error:
            self.warn("Media source failure", error)
            media = None
        mode = media["mode"] if media else "idle"
        if mode == self.mode and self.clock() < self.render_at:
            return
        self.mode = mode
        try:
            data = media if media else self.monitor.read()
            image = render(self.display.size, self.colors, mode, data)
            self.incidents.discard("LCD render or monitoring failure")
        except (OSError, ValueError, TypeError) as error:
            self.warn("LCD render or monitoring failure", error)
        else:
            self.display.paint(image)
        self.render_at = self.clock() + self.options[mode]

    def close(self):
        if self.display is not None:
            display, self.display = self.display, None
            try:
                display.close()
            except (OSError, ValueError) as error:
                self.warn("LCD cleanup failed", error)
