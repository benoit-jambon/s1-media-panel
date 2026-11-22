# Changelog

## 0.2.0-alpha

- Manual LCD pixel brightness from 0 to 100% in 5-point steps, shared by idle, playing and paused views. Legacy per-mode configuration keys remain readable.
- Atomic user preference, brightness CLI, optional PyQt6 slider panel and two-second primary-screen overlay. Includes examples of user-defined LXQt shortcuts.
- Hardware-free tests for RGB output, concurrent updates, service transitions, panel controls and overlay lifetime.

## 0.1.0-alpha

- System dashboard and MPRIS-driven playback and pause views with optional local MPV details.
- USB short-write detection, cleanup, reconnect backoff and monotonic heartbeat scheduling.
- Configurable rendering and deterministic synthetic previews.
- Experimental rectangular updates disabled by default.
- 25 automated tests passed per distribution, including simulated USB and timing tests.
- Consolidation fixes for USB cleanup and pacing, passive MPRIS lookup, failed HTTPS probes and configuration validation.
- Wheel and source distribution installed and uninstalled with dependency resolution on Lubuntu 26.04, Python 3.14. Synthetic previews verified.
- Experimental release. Earlier hardware observations apply to the private integration. This alpha received no physical LCD or endurance testing. Community hardware reports are welcome.
