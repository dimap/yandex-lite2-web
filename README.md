# Station Lite 2 SDK panel

This checkout adapts the upstream Station Web interface to `plum_sdk`. The web
service runs on X230; the Station remains a hardware endpoint reached through
the verified `station-lite2` SSH alias. The SDK owns fixed SSH operations,
Glagol requests, and TTL-bounded LED writes.

The deployed surface includes real device status and telemetry, media/player
controls, Alice text commands, the Lite 2 3D model, a 36-channel display
editor, preview-only animation import, and temporary RGB control. Display and
RGB snapshots and native Quasar ownership are unavailable, so the UI labels
them as unsupported. The evidence registry and decision record are maintained
in the separate `station-lite2` SDK checkout.

The deployed server is `sdk_server.py`; its route allowlist returns 404 for
unsupported operations. It does not expose a shell, filesystem editor, ADB,
YAM, alarms, boot hooks, or arbitrary SSH. `server.py` remains upstream source
and is not a supported launch target for this adaptation.
The old Docker and Windows launchers that invoked it have been removed; the
Windows helper now points to the X230-hosted panel.

## Deployment

The panel is served at `https://100.64.0.120:8766/` on X230, with a password,
Host/Origin checks, request limits, and a peer-restricted host firewall rule.
The address is CGNAT space, so reachability depends on the trusted client
network. Follow [the X230 deployment and rollback guide](docs/x230-deployment.md)
for setup, health checks, endpoints, and upstream update handling.

## Development checks

Panel routes and device behavior are tested with fake backends. No automated
tests invoke Station hardware, audio, or voice operations.

```sh
PYTHONPATH=/path/to/station-lite2/src:$PWD python3 -m pytest -q
node tests/test_paths.mjs
```

Keep panel credentials, generated TLS keys, device dumps, media, and test
artifacts outside Git. The existing MIT and 3D vendor notices remain in place.
