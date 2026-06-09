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

This creates `server/instance/gracelab.sqlite3`, all tables, and seeds default settings.

### Create the first admin user

```bash
FLASK_ENV=development python3 - <<'EOF'
from app import create_app
from extensions import db
from models import User
app = create_app("development")
with app.app_context():
    u = User(username="admin", role="admin")
    u.set_password("changeme")
    db.session.add(u)
    db.session.commit()
    print("Admin created.")
EOF
```

### Run (development)

```bash
FLASK_ENV=development python3 app.py
```

### Run (production — gunicorn)

```bash
SECRET_KEY="$(openssl rand -hex 32)" \
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

A systemd unit at `/etc/systemd/system/gracelab.service` manages this in production. Restart after server-side changes:

```bash
sudo systemctl restart gracelab
```

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
  --token      PASTE_TOKEN_HERE
```

The installer:
- Installs required packages (python3-tk, LibreOffice, accessibility tools, games, etc.)
- Creates `gracelab` and `guestlab` OS users (both password-locked)
- Adds both users to the `nopasswdlogin` group for passwordless autologin
- Installs client files to `/opt/gracelab-client/releases/0.2.0/`
- Creates `/opt/gracelab-client/current` symlink
- Writes `/etc/gracelab/client_config.ini`
- Installs `do-install.sh` to `/opt/gracelab-client/updater/` (root-owned)
- Writes `/etc/sudoers.d/gracelab-client` (four entries: the three lifecycle scripts + do-install.sh)
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
- Sudoers grants are limited to four scripts only. No broad `pkill`, `rsync`, `passwd`, or `loginctl` grants.
- All scripts granted via sudoers are root-owned and not writable by `gracelab`, preventing privilege escalation via script modification.
- The `do-install.sh` updater helper validates its version argument against a semver pattern and ensures the tarball path is inside the `downloads/` directory before extracting.
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

---

## Version history

| Version | Highlights |
|---|---|
| 0.1.0 | Initial release: session codes, kiosk client, station heartbeat |
| 0.2.0 | Extend-by-code, orphan recovery, session persistence, `dm-tool` switching, audit log, settings page, token rotation, rate limiting, Open Lab mode, Terms of Service |
| 0.3.0 | Grace Updater (self-hosted OTA), `run-client.sh` wrapper, `package-client.sh`, Firefox policies, approved app installer, wallpaper support |
