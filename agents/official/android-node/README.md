# AndroidNode

Operates an Android phone on the user's behalf from a distance: screen observation and automation, app install and launch, media and sensor capture, location, speech, clipboard, notifications, and BLE/USB peripherals. All results are delivered through chat.

## Install

```bash
acowork install com.acowork.android-node

Or download the signed `.agent` package from the hub and import it in
the Desktop App. The package is signed by the official channel key —
the app verifies the signature on install.

## What it does

- **app lifecycle** — App lifecycle at a distance: list and inspect installed packages, download an APK, drive the asynchronous install through the on-site confirmation dialog, launch and uninstall
- **ble communication** — BLE peripheral work: scan with filters, connect, discover services, negotiate MTU, subscribe to notifications, read and write characteristics, and recover from disconnects
- **media capture** — Media capture and delivery: photos, video, audio, and screen recordings land in the work dir and are brought back to the user through chat
- **remote control** — Remote screen control: observe (screenshot / accessibility tree / screen record), act (tap / swipe / type / node / global), verify the result, and fall back to clipboard paste when text injection fails
- **sensor logging** — Sensor and location logging: windowed sampling and streams, GPS tracks, artifacts written to the work dir, then read back and summarized

## Permissions

Read them before you install:

- `FilesystemRead`
- `FilesystemWrite`
- `Network`

## Skills

- `android-app-lifecycle`
- `android-ble-connect`
- `android-media-capture`
- `android-remote-control`
- `android-sensor-log`

## Provenance

- **Agent id**: `com.acowork.android-node`
- **Version**: `0.1.1`
- **Author**: ACowork Team
- **Source**: [`agents/android-node-agent`](https://github.com/acowork/ACowork/tree/master/agents/android-node-agent) in the ACowork main repo
- **License**: Apache-2.0

This package is a verbatim copy of the main repo's agent source,
produced by `tools/sync-official.py`; `hub.json` names the commit
it was copied from. The main repo is the source of truth — edit
it there, then re-run the sync. Changes made directly to this
directory are overwritten by the next sync and fail CI before
that happens.
