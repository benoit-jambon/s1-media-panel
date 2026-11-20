"""PyUSB transport for the interrupt endpoint on the tested LCD interface."""
import logging
import time

import usb.core
import usb.util

from .protocol import BUFFER_SIZE, EP_OUT, INTERFACE


class Transport:
    def __init__(self, config):
        self.config = config
        self.dev = None
        self.last_write = None

    def open(self):
        config = self.config
        devices = usb.core.find(find_all=True, idVendor=config["vid"], idProduct=config["pid"])
        matches = [
            d for d in devices if d.bcdDevice == config["revision"]
            and (not config["bus"] or d.bus == config["bus"])
            and (not config["ports"] or tuple(d.port_numbers or ()) == tuple(config["ports"]))
        ]
        if len(matches) != 1:
            raise OSError(f"Expected one matching LCD controller, found {len(matches)}")
        self.dev = matches[0]
        try:
            try:
                interface = self.dev.get_active_configuration()[(INTERFACE, 0)]
            except (KeyError, IndexError) as error:
                raise OSError("LCD interface 01 missing") from error
            endpoint = usb.util.find_descriptor(interface, bEndpointAddress=EP_OUT)
            if (interface.bInterfaceClass != 3 or endpoint is None
                    or usb.util.endpoint_type(endpoint.bmAttributes) != usb.util.ENDPOINT_TYPE_INTR):
                raise OSError("Unexpected LCD USB interface or endpoint")
            if self.dev.is_kernel_driver_active(INTERFACE):
                raise OSError("LCD interface is owned by a kernel driver")
            usb.util.claim_interface(self.dev, INTERFACE)
        except BaseException:
            try:
                self.close()
            except (OSError, ValueError) as error:
                logging.getLogger(__name__).warning("USB cleanup failed (%s)", type(error).__name__)
            raise

    def send(self, frame):
        data = bytes(frame)
        if len(data) != BUFFER_SIZE:
            raise ValueError("Invalid LCD frame size")
        if self.last_write is not None:
            delay = self.config["pace_ms"] / 1000 - (time.monotonic() - self.last_write)
            if delay > 0:
                time.sleep(delay)
        count = self.dev.write(EP_OUT, data, timeout=self.config["timeout_ms"])
        self.last_write = time.monotonic()
        if count != len(data):
            raise OSError(f"Short LCD write: {count}/{len(data)} bytes, header={data[:8].hex()}")

    def close(self):
        dev, self.dev = self.dev, None
        self.last_write = None
        if dev is not None:
            usb.util.dispose_resources(dev)
