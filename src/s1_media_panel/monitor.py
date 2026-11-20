"""Local metrics and optional certificate-validated HTTPS reachability."""
import http.client
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import psutil


def reachable(url):
    address = urlsplit(url)
    client = http.client.HTTPSConnection(address.hostname, address.port, timeout=2)
    try:
        client.request("GET", address.path or "/")
        return 200 <= client.getresponse().status < 300
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        client.close()


class Monitor:
    def __init__(self, config, metrics):
        self.config, self.metrics = config, set(metrics)
        self.future = self.server = self.network = None
        self.next_probe = 0
        self.pool = ThreadPoolExecutor(max_workers=1) if config["server_url"] and "server" in self.metrics else None
        if "cpu" in self.metrics:
            psutil.cpu_percent()

    def close(self):
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

    def read(self):
        now = datetime.now()
        result = {"time": now.strftime("%H:%M"), "date": now.strftime("%Y-%m-%d")}
        if "cpu" in self.metrics:
            result["cpu"] = f"{psutil.cpu_percent():.0f}%"
        if "ram" in self.metrics:
            result["ram"] = f"{psutil.virtual_memory().percent:.0f}%"
        if "disk" in self.metrics:
            try:
                result["disk"] = f"{psutil.disk_usage(self.config['disk_path']).percent:.0f}%"
            except OSError:
                pass
        if "uptime" in self.metrics:
            minutes = int(max(0, time.time() - psutil.boot_time()) // 60)
            result["uptime"] = f"{minutes // 60}h {minutes % 60:02}m"
        if self.metrics & {"temperature", "disk_temperature"}:
            sensors = psutil.sensors_temperatures()
            cpu = next((entry.current for entry in sensors.get("coretemp", ()) if entry.label.startswith("Package")), None)
            disks = sensors.get(self.config["storage_sensor"], ())
            if cpu is not None:
                result["temperature"] = f"{cpu:.0f} °C"
            if len(disks) == 1:
                result["disk_temperature"] = f"{disks[0].current:.0f} °C"
        if "gpu" in self.metrics:
            devices = list(Path("/sys/class/drm").glob("card[0-9]*/device/gpu_busy_percent"))
            if len(devices) == 1:
                try:
                    gpu = float(devices[0].read_text())
                    if 0 <= gpu <= 100:
                        result["gpu"] = f"{gpu:.0f}%"
                except (OSError, ValueError):
                    pass
        if "network" in self.metrics and self.config["network_interface"]:
            now = time.monotonic()
            counters = psutil.net_io_counters(pernic=True) or {}
            counter = counters.get(self.config["network_interface"])
            if counter is not None:
                if self.network and now > self.network[0]:
                    then, received, sent = self.network
                    if counter.bytes_recv >= received and counter.bytes_sent >= sent:
                        result["network"] = (
                            f"↓{(counter.bytes_recv - received) / (now - then) / 1024:.0f} "
                            f"↑{(counter.bytes_sent - sent) / (now - then) / 1024:.0f} KiB/s"
                        )
                self.network = now, counter.bytes_recv, counter.bytes_sent
            else:
                self.network = None
        if self.pool:
            now = time.monotonic()
            if self.future and self.future.done():
                completed, self.future = self.future, None
                self.server = completed.result()
            if self.future is None and now >= self.next_probe:
                self.future = self.pool.submit(reachable, self.config["server_url"])
                self.next_probe = now + self.config["server_interval"]
            if self.server is not None:
                result["server"] = "Reachable" if self.server else "Unavailable"
        return result
