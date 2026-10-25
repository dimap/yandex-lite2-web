# X230 deployment

## Service

`station-lite2-web.service` runs as the `fleef` user from this checkout. It
uses the SDK virtual environment at `~/.local/share/station-lite2/venv` and the
verified SSH alias `station-lite2`. The listener is HTTPS-only at
`100.64.0.120:8766`; port 8765 belongs to another service and is left alone.
The X230 has `100.64.0.120/25` on `enp0s25`; this is a shared CGNAT address,
not evidence that every browser can route to it. `tailscaled` is inactive.

The UFW input policy denies new inbound connections. Deployment permits only
the already trusted peer `100.64.0.2` to reach TCP 8766 on `enp0s25`. No WAN
router rule, Nginx route, or existing firewall rule is changed. Verify the
client address before adding that rule on another installation.

```sh
sudo ufw allow in on enp0s25 from 100.64.0.2 to 100.64.0.120 port 8766 proto tcp
mkdir -p ~/.config/systemd/user
cp station-lite2-web.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now station-lite2-web.service
```

Open `https://100.64.0.120:8766/`. The initial admin password, if generated,
is stored in `~/.local/share/station-lite2/panel/admin-password.txt` with mode
0600. The server stores only the PBKDF2 password hash and session token hashes.
The local TLS CA is downloadable from Settings after login; install it only on
devices that should trust this panel. On first access the browser may warn
because this private CA is not yet trusted. On the trusted peer, proceed to
the panel at this exact IP, sign in, download `ca.crt`, install it, and reload.
The CA name constraints allow this X230 address and RFC1918 addresses only.
`/healthz` returns only `{"status":"ok"}`.

The panel applies Host and Origin checks, same-site HttpOnly cookies, a 12
character minimum password, per-IP login lockout and request rate limits. It
does not trust proxy headers. Do not put it behind a proxy without a separate
design review.

## Operations and rollback

```sh
systemctl --user status station-lite2-web.service
journalctl --user -u station-lite2-web.service --since today
systemctl --user restart station-lite2-web.service
systemctl --user stop station-lite2-web.service
```

To roll back this deployment, restore the previous unit from
`~/.local/share/station-lite2/panel/station-lite2-web.service.loopback-backup`
to `~/.config/systemd/user/station-lite2-web.service`, run
`systemctl --user daemon-reload`, then `systemctl --user restart
station-lite2-web.service`. Delete only the added UFW rule with:

```sh
sudo ufw delete allow in on enp0s25 from 100.64.0.2 to 100.64.0.120 port 8766 proto tcp
```

The previous loopback listener was `127.0.0.1:8876`. Generated TLS and panel
auth data stay in `~/.local/share/station-lite2/panel`; removing them revokes
the local panel CA, sessions, and password hash. No Station files or settings
are changed by deployment or rollback.

## Endpoint and capability map

| Route | Operation |
| --- | --- |
| `GET /healthz` | Minimal process health |
| `GET /api/status`, `/api/metrics` | SDK system/resource reads |
| `GET /api/capabilities`, `/api/profile` | SDK capabilities and Lite 2 profile |
| `GET /api/music` | SDK Glagol/media status |
| `GET /api/display`, `/api/display/schema` | Last operation and SDK canonical 36-channel schema |
| `POST /api/display` | One 1–30 second temporary static frame, max brightness 15% |
| `POST /api/ring` | Temporary RGB frame, 1–30 seconds, max brightness 15% |
| `POST /api/music/cmd`, `/api/volume` | Typed SDK player/Alice operations |
| `POST /api/conn/retry`, `/api/metrics/clear` | Recheck status and clear X230 memory history |
| `GET/POST /api/panel-auth*`, `GET /ca.crt` | Password, sessions, and public CA download |

Every other route returns 404. There is no shell, ADB, file editor, arbitrary
SSH operation, alarm, boot hook, or package-install route. Metrics are held in
memory on X230 at a 30 second sampling interval. CPU usage is calculated from
successive `/proc/stat` read-only samples; disk usage and live LED snapshots
are unavailable. The 3D model and display preview are local visualizations,
not device snapshots. Imported animation files are preview-only.

The upstream Docker image and launcher used the legacy monolithic server and
ADB, so those launch files were removed from this adapter branch. `start.bat`
only points Windows browsers to the X230 service.

## Upstream updates

The panel repository keeps `upstream` pointed at
`https://github.com/dimap/yandex-station-web.git`. Review upstream changes and
merge them into a separate feature branch; retain `sdk_server.py`,
`sdk_adapter.py`, the route allowlist, and this deployment unit. Do not copy the
upstream monolithic `server.py` into the SDK or expose it as the deployed
entrypoint. The SDK source is separately checked out at
`/home/fleef/.codex/worktrees/station-web-quasar/station-lite2` and referenced
by the service's `PYTHONPATH`.
