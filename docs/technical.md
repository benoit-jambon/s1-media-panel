# Technical reference

The Python package separates configuration, USB transport, LCD protocol, rendering, system monitoring, media input and service lifecycle into modules. The loop reads media state and chooses idle, playing or paused. A media provider implements `read()` and returns the view fields or `None`. Input errors degrade to idle and collection or rendering failures leave the last frame visible. USB failures close resources and back off before rediscovery; a new session begins with orientation, heartbeat and a full redraw.

## USB protocol

The USB transport verifies the HID-class interface 01 and interrupt OUT endpoint 02, then claims only that interface. Multiple matches require an explicit bus or port chain. Transfers must be 4,104 bytes. PyUSB short writes are errors. A full redraw is 27 serialized `55 A3` packets with an eight-byte header and big-endian RGB565 data. The first 26 contain 4,096 pixel bytes; the final one contains 2,304 with zero padding. Orientation uses `55 A1 F1`. Heartbeat uses `55 A1 F2` and local hour, minute and second.

The next heartbeat deadline advances only after successful sending, using monotonic time. The loop checks the deadline before and after drawing, limits its sleep to the next heartbeat, and never inserts other commands into a full redraw. Polling, collection, transfer time and pacing can delay the heartbeat. A 50 ms gap adds about 1.35 s to a sustained 27-transfer redraw. The 50 ms and 2,000 ms settings are experimental. Default values retain the original 1,000 ms timeout and omit inter-write pacing. Identical RGB frames are skipped. Optional landscape `A2` updates use a bounding rectangle up to 254 × 170 and 2,048 pixels.

## Manual brightness

The renderer draws the configured theme at full intensity. The service retains the RGB image and applies the user's level before passing it to the existing duplicate-frame detection, rectangular updates and RGB565 conversion. At 0% identical black frames are skipped and heartbeat scheduling continues. A brightness-only change can reuse the cached RGB image between scheduled renders. Synthetic previews always use full intensity. Old per-mode factors are accepted when parsing existing configuration files.

The detached overlay and command use two locks in the private session directory under `XDG_RUNTIME_DIR`. One serializes requests with expiry and the other stays with the single window process. A new request, including a repeated level, resets the two-second countdown. Run the test suite with the optional `gui` extra installed to include the Qt offscreen tests. See the [illustrated user guide](luminosite.md).

## Media and monitoring

MPRIS supplies state, title, duration and position. The panel looks up the current player owner without activating an inactive player. The Shim episode title convention provides series, season and episode when present. Configure a separate private directory inside `XDG_RUNTIME_DIR` with mode 0700 and a Unix socket owned by the same user with mode 0600. Point MPV's `input-ipc-server` at that socket and set `media.mpv_socket` in the panel's TOML. The player launch environment must make the directory accessible to MPV (including any sandbox). The panel only sends JSON `get_property` requests. Keep the socket private because MPV itself accepts control commands. Disable title display with `show_titles = false`.

Explicit MPV properties supply video and audio codec, video size, active decoder, transfer, Dolby Vision profile, track metadata and dropped frames. A PQ transfer alone does not establish Dolby Vision. Server delivery mode is unavailable through these sources.

psutil supplies CPU, RAM, disk usage, uptime, per-interface traffic counters and sensors. CPU temperature uses the Intel coretemp package sensor where available. Configure a single-entry sensor group for storage temperature. Network throughput requires two samples and a configured interface. GPU usage reads a single sysfs gpu_busy_percent counter when available. An optional user-configured HTTPS endpoint is checked asynchronously with TLS certificate and hostname validation, without credentials or redirects. A successful 2xx status describes that endpoint only.

## Diagnostics and tests

Run `s1-media-panel --check-config` and `s1-media-panel --preview DIRECTORY` before opening the LCD. Review user-unit logs with `journalctl --user -u s1-media-panel.service`. Check available descriptors and the ACL before diagnosing a transfer error. On an approved hardware test, stop the other LCD writer before starting this tool.

Run `PYTHONPATH=src python -m unittest discover -s tests -v` from the checkout after installing dependencies. Unit tests verify conversion, protocol headers and lengths, return values, timeout propagation, rendering and configuration. Integration tests use simulated USB and time to check initialization, heartbeat, transitions and reconnect. On Lubuntu 26.04 and Python 3.14, both distributions installed with dependency resolution and each passed 25 automated tests, then uninstalled successfully.

The earlier private integration showed idle and playback views on a Holtek USB controller (04d9:fd01, revision 0110). No physical testing or endurance testing was performed on this public alpha. Simulated checks exercise the heartbeat schedule and recovery logic. Physical heartbeat intervals, reconnect, portrait rendering and rectangular refresh require hardware reports. Rectangular refresh and custom pacing remain experimental. Community reports should identify the controller revision and software versions and include sanitized observations for idle, playback, pause and return to idle. Record the duration of an endurance run separately.
