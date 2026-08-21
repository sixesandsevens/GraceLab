# GraceLab

Computer lab session management system for Grace Marketplace. Staff issue timed session codes at the front desk; the fullscreen kiosk client on each workstation validates them, manages the guest desktop, and resets the machine between sessions.

---

## Architecture

```
┌─────────────────────────┐        ┌──────────────────────────────┐
│  Server (Raspberry Pi)  │        │  Lab Station (Linux Mint)    │
│                         │        │                              │
│  Flask + SQLite         │◄──────►│  gracelab_client.py (kiosk)  │
│  gunicorn               │  HTTP  │  updater.py (auto-update)    │
│  /var/lib/gracelab/     │        │  run-client.sh (wrapper)     │
│    updates/             │        │  LightDM autologin → gracelab│
└─────────────────────────┘        │  dm-tool switches → guestlab │
                                   └──────────────────────────────┘
```

Two OS users on each station:

| User | Purpose |
|---|---|
| `gracelab` | Runs the kiosk client; fullscreen at all times |
| `guestlab` | Guest desktop; activated per session via `dm-tool` |

The kiosk client runs as `gracelab`. When a valid session code is entered, it calls `start_guest_session.sh` (via `sudo`) and switches the display to the `guestlab` desktop. When the session ends or expires, it calls `end_guest_session.sh`, switches back to `gracelab`, then calls `reset_guest_home.sh` to wipe the guest profile from a clean template.

---

## Repository layout

```
GraceLab/
├── server/                  Flask application (runs on the server)
│   ├── app.py               Application factory
│   ├── config.py            Configuration classes
│   ├── models.py            SQLAlchemy models
│   ├── api.py               Station REST API (/api/...)
│   ├── auth.py              Login / logout
│   ├── dashboard.py         Overview dashboard
│   ├── sessions.py          Session code management
│   ├── stations.py          Station management
│   ├── admin.py             Audit log + settings
│   ├── updates.py           Update packages + OTA API
│   ├── extensions.py        Flask extension singletons
│   ├── limiter.py           Flask-Limiter singleton
│   ├── audit.py             log_audit() helper
│   ├── requirements.txt
│   ├── scripts/
│   │   └── init_db.py       Re-runnable DB migration + seed
│   └── templates/
│
├── client/                  Kiosk client (installed on each station)
│   ├── gracelab_client.py   Main kiosk application (Tkinter)
│   ├── gracelab_apps.py     App launcher overlay
│   ├── gracelab_timer.py    Guest desktop countdown widget
│   ├── client_config.ini.example
│   ├── assets/              Static assets (background image, etc.)
│   ├── scripts/
│   │   ├── common.sh        Shared shell helpers
│   │   ├── start_guest_session.sh
│   │   ├── end_guest_session.sh
│   │   ├── reset_guest_home.sh
│   │   ├── run-client.sh    Client wrapper / restart loop
│   │   └── set-wallpaper.sh Dynamic wallpaper setter
│   ├── template-home/       Pristine guestlab home (rsync'd on reset)
│   └── updater/
│       ├── updater.py       Auto-update daemon
│       └── do-install.sh    Root-owned install helper (sudoers)
│
└── tools/
    ├── install-client.sh    Full-machine provisioner (run on each station)
    └── package-client.sh    Build a versioned client update package
```

---

## Server setup

### Requirements

- Python 3.10+
- A dedicated machine or Raspberry Pi reachable by all lab stations

### Install

```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Database initialisation

Run once (and again after any schema change — it is idempotent):

```bash
FLASK_ENV=development python3 scripts/init_db.py
```

This creates `server/instance/gracelab.sqlite3`, all tables, and seeds default settings (60-minute sessions, 24-hour code expiry, 5-minute warning, 90-second offline threshold, batch max 36, updates disabled). If a key already exists it is left unchanged, so re-running is always safe.

### Create the first admin user

```bash
FLASK_ENV=development python3 scripts/create_admin.py
```

The script prompts for a username and password interactively and handles both create and reset (if the user already exists it updates the password and role).

### Run (development)

```bash
FLASK_ENV=development python3 app.py
```

### Run (production — gunicorn + systemd)

> **Single worker required.** Flask-Limiter uses in-memory storage. Running multiple workers gives each worker its own counter, so the effective limit is multiplied by the worker count. Use `-w 1` until the limiter is backed by Redis or Memcached.

```bash
SECRET_KEY="$(openssl rand -hex 32)" \
gunicorn -w 1 -b 127.0.0.1:5000 app:app
```

A sample systemd unit is in `tools/gracelab.service`. Install it:

```bash
sudo cp tools/gracelab.service /etc/systemd/system/gracelab.service
# Edit the file to set the correct User, WorkingDirectory, and SECRET_KEY
sudo systemctl daemon-reload
sudo systemctl enable --now gracelab
```

Restart after server-side changes:

```bash
sudo systemctl restart gracelab
```

### Networking

GraceLab is designed to run on a **private LAN only**. Do not expose port 5000 directly to the internet — there is no HTTPS, and the station tokens would be visible in transit.

For remote staff access, put nginx in front:

```nginx
server {
    listen 80;
    server_name gracelab.internal;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Add TLS via Certbot or a self-signed cert if you need access outside the LAN.

---

## Station provisioning

### Prerequisites

Each station needs:
- Linux Mint (XFCE edition recommended) with LightDM
- Network access to the GraceLab server
- `git` installed (`sudo apt install git`)

### 1. Register the station in the dashboard

**Stations → Register Station** — enter a hostname (e.g. `gracelab-01`) and display name. Copy the one-time token shown after registration.

### 2. Clone the repo

```bash
git clone https://github.com/sixesandsevens/GraceLab.git /opt/gracelab/GraceLab
cd /opt/gracelab/GraceLab
```

### 3. Run the installer

```bash
sudo ./tools/install-client.sh \
  --server-url http://192.168.1.246:5000 \
  --hostname   gracelab-01 \
  --token      PASTE_TOKEN_HERE \
  --admin-user local-admin-account   # optional, see "Maintenance mode" below
```

The installer:
- Installs required packages (python3-tk, LibreOffice, accessibility tools, games, etc.)
- Creates `gracelab` and `guestlab` OS users (both password-locked)
- Adds both users to the `nopasswdlogin` group for passwordless autologin
- Installs client files to `/opt/gracelab-client/releases/0.2.0/`
- Creates `/opt/gracelab-client/current` symlink
- Writes `/etc/gracelab/client_config.ini`
- Installs `do-install.sh` to `/opt/gracelab-client/updater/` (root-owned)
- Writes `/etc/sudoers.d/gracelab-client` (lifecycle scripts, do-install.sh, and the
  maintenance-mode/reboot helper scripts — see "Maintenance mode" below)
- Creates the `gracelab-admin` group (local "Return to GraceLab" convenience)
- Configures LightDM to autologin as `gracelab`
- Hides `guestlab` from the greeter via AccountsService
- Writes XFCE kiosk lockdown settings (no panel, blocked shortcuts)
- Disables Ctrl+Alt+Backspace
- Locks Firefox homepage to `guestdesk.info` and blocks extension installs
- Installs desktop files for the Apps launcher and End Session button
- Suppresses the Linux Mint welcome screen

**Reboot after running the installer.**

---

## Day-to-day operations

### Issuing session codes

**New Code** — single code with configurable duration.  
**Batch Print** — up to 36 codes at once (configurable), rendered as a printable sheet.

Codes are a 6-digit number formatted as `XXX-XXX`. Guests enter them on the kiosk idle screen.

### Extending a session

A guest on the warning screen can enter a second unused code to extend their current session by that code's duration.

### Ending a session from the dashboard

Open the session detail page and click **End Session**. The kiosk polls the server every 12 seconds and will pick up the change.

### Station statuses

| Status | Meaning |
|---|---|
| `available` | Idle, ready for a code |
| `in_use` | Active session running |
| `offline` | No heartbeat within threshold (default 90 s) |
| `needs_attention` | Script failure or staff-flagged |
| `out_of_service` | Manually taken offline by staff |

### Admin recovery on a stuck station

Press **Escape** on the kiosk screen when it shows `needs_attention` — this exits fullscreen so you can interact with the desktop. Fix the issue, then clear the station status from the dashboard.

### Maintenance mode & remote recovery

The Stations page has four admin actions for local troubleshooting and remote recovery, none of which require physical TTY access:

- **Enter Maintenance** — queues maintenance mode. If the station is idle it switches away almost immediately; if a guest session is active, that session finishes normally first ("Maintenance Pending"), then the station switches away instead of returning to Start Session. New sessions can't start once maintenance is requested, at any point in that flow.
- **Return to GraceLab** / **Cancel Maintenance Request** — clears the request. If maintenance was already active, the station resets the guest home and returns to normal operation, unless another lock (an out_of_service/needs_attention flag, or a still-pending update) takes over instead.
- **Reset GraceLab** — force-recovers a stuck station: ends any active session immediately, clears local session state, resets the guest home, and switches the display back to GraceLab. More forceful than Enter Maintenance — it does not wait for a session to end.
- **Reboot** — queues `systemctl reboot` via a narrowly-scoped sudo helper. Rejected while a guest session is active; the admin action returns a clear error rather than interrupting anyone.

**Where maintenance actually switches to**: if `[maintenance] admin_user` is set in `client_config.ini` (via `--admin-user` at install time, or edited directly), the station switches to that account's graphical session. If unset, or if that account has no session running, it falls back to the LightDM greeter so someone can log in normally. GraceLab never stores or checks that account's password — authentication stays ordinary Linux/LightDM auth.

**Local "Return to GraceLab"**, for the administrator sitting at the console instead of the dashboard:
```bash
sudo /opt/gracelab-client/current/scripts/request_maintenance_exit.sh
```
Runnable by any account in the `gracelab-admin` group (add one with `usermod -aG gracelab-admin <username>`; `--admin-user` at install time does this automatically if the account already exists). Wire it to a launcher or menu entry in that account's session as you prefer.

Reset/reboot are delivered through a small one-shot command channel (not a general remote-shell mechanism): the admin action stamps a UUID command id on the station, the client picks it up on its next heartbeat, and the server only clears that id once the client reports back — so a stale or duplicated heartbeat can never replay a reboot or reset.

---

## Grace Updater (OTA client updates)

### Build a package

Run on the server after updating client code:

```bash
sudo ./tools/package-client.sh
# Output: /var/lib/gracelab/updates/gracelab-client-0.2.0.tar.gz
```

Or upload the `.tar.gz` via **Updates** in the admin nav.

### Publish the update

1. **Settings → Client Updates** — enable updates, set **Published Stable Version** to the new version string (e.g. `0.2.0`).
2. Stations check every 5 minutes (configurable). When idle, the updater downloads the package, verifies the SHA256, installs it via `sudo do-install.sh`, and writes `/tmp/gracelab-update-ready`.
3. The kiosk client detects the flag on its next heartbeat (≤30 s) and exits cleanly. The `run-client.sh` wrapper relaunches it against the new version.

### Update policies

| Policy | Behaviour |
|---|---|
| `idle_only` | Only update when no session is active (recommended) |
| `force` | Update immediately regardless of session state |
| `manual` | Never auto-install; dashboard-triggered only |
| `disabled` | No update checking |

---

## Settings reference

All settings are live (no restart needed) and editable at **Settings** in the admin nav.

| Key | Default | Description |
|---|---|---|
| `default_session_minutes` | 60 | Duration of new session codes |
| `code_expiration_minutes` | 1440 | How long an unused code stays valid |
| `warning_minutes` | 5 | Warning screen shown this many minutes before expiry |
| `batch_code_max_count` | 36 | Maximum codes in a batch print |
| `station_offline_after_seconds` | 90 | Heartbeat gap before station marked offline |
| `organization_name` | Grace Marketplace | Shown on the kiosk idle screen |
| `ticket_footer` | — | Printed at the bottom of session tickets |
| `open_lab_mode` | false | Guests start sessions without a code |
| `open_session_duration_minutes` | 120 | Duration of open-lab sessions |
| `tos_text` | — | Terms of service shown before each session (leave blank to skip) |
| `client_updates_enabled` | false | Enable OTA updates |
| `client_update_policy` | idle_only | See update policies above |
| `client_update_channel` | stable | `stable` or `beta` |
| `client_stable_version` | — | Version to advertise on the stable channel |
| `client_beta_version` | — | Version to advertise on the beta channel |
| `client_min_supported_version` | — | Versions below this are flagged in the dashboard |

---

## Security notes

- The `gracelab` user is a normal (non-system) OS account with a locked password. It autologins via the `nopasswdlogin` PAM group.
- Sudoers grants are limited to specific, narrowly-scoped scripts only (lifecycle hooks, the updater helper, and the maintenance/reboot helpers below) — no broad `pkill`, `rsync`, `passwd`, `loginctl`, or generic `systemctl` grants.
- All scripts granted via sudoers are root-owned and not writable by `gracelab`, preventing privilege escalation via script modification.
- The `do-install.sh` updater helper validates its version argument against a semver pattern and ensures the tarball path is inside the `downloads/` directory before extracting.
- `/run/gracelab-admin-override` (maintenance mode's kiosk-enforcement suppression flag) lives directly under `/run`, not the shared `/run/gracelab/` IPC directory guestlab can also write to — a guest process has no path to create or modify it. Only `enable_admin_override.sh`/`disable_admin_override.sh`, invoked via sudo, can touch it.
- The local "Return to GraceLab" script (`request_maintenance_exit.sh`) is grantable via sudo to the `gracelab-admin` group rather than a specific username, so provisioning it doesn't require hardcoding a personal account into the installer.
- Maintenance mode never stores or checks the local administrator's password — it only switches the display toward that account's session or the LightDM greeter; authentication stays ordinary Linux/LightDM auth.
- Station tokens are hashed with Werkzeug's `generate_password_hash` (bcrypt). Tokens are shown once at registration; if lost, rotate from the Stations page.
- The admin panel requires the `admin` role. Staff accounts use the `staff` role and cannot access audit logs, settings, or token rotation.
- Login attempts are rate-limited to 10/minute and 30/hour via Flask-Limiter.
- Session code entry at the kiosk is throttled to 5 failures per station in a 5-minute window before a 30-second cooldown.

---

## File locations (on a provisioned station)

| Path | Contents |
|---|---|
| `/opt/gracelab-client/current/` | Symlink → active client version |
| `/opt/gracelab-client/releases/<ver>/` | Installed client versions |
| `/opt/gracelab-client/downloads/` | Temporary download scratch space |
| `/opt/gracelab-client/updater/do-install.sh` | Root-owned install helper |
| `/opt/gracelab-client/template-home/` | Pristine guestlab home directory |
| `/etc/gracelab/client_config.ini` | Station config (token, server URL) |
| `/var/log/gracelab/` | Client and wrapper logs |
| `/tmp/gracelab-session.json` | Active session state (survives reboot) |
| `/tmp/gracelab-guest-logout` | IPC flag: guest clicked End Session |
| `/tmp/gracelab-update-ready` | IPC flag: updater installed new version |
| `/run/gracelab-admin-override` | Maintenance mode: suppresses watchdog/lockdown enforcement (root-owned, not guest-writable) |
| `/run/gracelab-maintenance-exit-requested` | Local "Return to GraceLab" request flag (root-owned) |

---

## Version history

| Version | Highlights |
|---|---|
| 0.1.0 | Initial release: session codes, kiosk client, station heartbeat |
| 0.2.0 | Extend-by-code, orphan recovery, session persistence, `dm-tool` switching, audit log, settings page, token rotation, rate limiting, Open Lab mode, Terms of Service |
| 0.3.0 | Grace Updater (self-hosted OTA), `run-client.sh` restart wrapper, `do-install.sh` sudoers helper, `package-client.sh`, Firefox policies, approved app installer, wallpaper, delete station |
| 0.4.0 | Open-mode session timer fix; safer session teardown with verified/retried `dm-tool` display switching; update-lock session admission (a queued update blocks new sessions until installed, with safe reconciliation of lost reports and mismatched versions); station maintenance mode with a verified admin override and automatic lease refresh; remote GraceLab reset and reboot via a replay-safe one-shot command channel |
