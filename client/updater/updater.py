#!/usr/bin/env python3
"""
GraceLab client auto-updater.

Runs as a long-lived background process alongside the kiosk client.
Polls the server for available updates, downloads and verifies the package,
calls the root-owned do-install.sh helper to install it, then signals the
client wrapper to restart.

Config: /etc/gracelab/client_config.ini  [updates] section
"""

import configparser
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

INSTALL_BASE    = "/opt/gracelab-client"
DO_INSTALL_SH   = f"{INSTALL_BASE}/updater/do-install.sh"
UPDATE_READY_FLAG = "/tmp/gracelab-update-ready"
GUEST_TIMER_FILE  = "/tmp/gracelab-session.json"

_CONFIG_PATHS = [
    "/etc/gracelab/client_config.ini",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "client_config.ini"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gracelab-updater] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("gracelab.updater")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config():
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "server":  {"url": "http://localhost:5000"},
        "station": {"hostname": "LAB-PC-01", "token": ""},
        "updates": {
            "enabled":                "true",
            "channel":                "stable",
            "check_interval_seconds": "300",
            "install_policy":         "idle_only",
        },
    })
    cfg.read(_CONFIG_PATHS)
    return cfg


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def get_current_version():
    client_py = os.path.join(INSTALL_BASE, "current", "gracelab_client.py")
    try:
        with open(client_py) as f:
            m = re.search(r'^CLIENT_VERSION\s*=\s*["\'](.+?)["\']',
                          f.read(), re.MULTILINE)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _headers(cfg):
    return {
        "Content-Type": "application/json",
        "X-Station-ID": cfg.get("station", "hostname"),
        "Authorization": "Bearer " + cfg.get("station", "token"),
    }


def check_for_update(cfg, current_version):
    url = cfg.get("server", "url").rstrip("/") + "/api/station/update-check"
    channel = cfg.get("updates", "channel", fallback="stable")
    body = json.dumps({"current_version": current_version,
                       "channel": channel}).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(cfg), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.warning("Update check failed: %s", e)
        return None


def report_status(cfg, status, version="", error=""):
    url = cfg.get("server", "url").rstrip("/") + "/api/station/update-status"
    body = json.dumps({"status": status, "version": version,
                       "error": error}).encode()
    req = urllib.request.Request(url, data=body, headers=_headers(cfg), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        log.warning("Status report failed: %s", e)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_file(url, dest_path, cfg, expected_sha256):
    req = urllib.request.Request(url, headers=_headers(cfg), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        raise RuntimeError(f"Download failed: {e}")

    h = hashlib.sha256()
    with open(dest_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected_sha256:
        raise RuntimeError(
            f"Checksum mismatch: expected {expected_sha256}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Installation (delegates to root-owned do-install.sh via sudo)
# ---------------------------------------------------------------------------

def install_update(tarball_path, version):
    if not os.path.isfile(DO_INSTALL_SH):
        raise RuntimeError(f"do-install.sh not found at {DO_INSTALL_SH}")

    result = subprocess.run(
        ["sudo", DO_INSTALL_SH, version, tarball_path],
        timeout=120,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        log.info("do-install: %s", result.stdout.strip())
    if result.returncode != 0:
        raise RuntimeError(
            f"do-install.sh failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------

def is_session_active():
    return os.path.exists(GUEST_TIMER_FILE)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    cfg = load_config()

    if not cfg.getboolean("updates", "enabled", fallback=True):
        log.info("Updates disabled in config — exiting.")
        return

    if not cfg.get("station", "token"):
        log.error("No station token in config — updater cannot authenticate.")
        return

    policy = cfg.get("updates", "install_policy", fallback="idle_only")
    channel = cfg.get("updates", "channel", fallback="stable")
    check_interval = cfg.getint("updates", "check_interval_seconds", fallback=300)

    log.info("Updater started. channel=%s  policy=%s  interval=%ds",
             channel, policy, check_interval)

    while True:
        time.sleep(check_interval)

        current_version = get_current_version()
        result = check_for_update(cfg, current_version)

        if not result or not result.get("ok"):
            continue

        if not result.get("update_available"):
            log.debug("No update available (current=%s).", current_version)
            continue

        available     = result["available_version"]
        download_url  = result["download_url"]
        checksum      = result["checksum_sha256"]

        log.info("Update available: %s → %s", current_version, available)

        if policy == "disabled":
            log.info("Policy is disabled — skipping.")
            continue

        if policy == "idle_only" and is_session_active():
            log.info("Session active — deferring update to next check.")
            continue

        # Proceed
        downloads_dir = os.path.join(INSTALL_BASE, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        tarball = os.path.join(downloads_dir,
                               f"gracelab-client-{available}.tar.gz")

        report_status(cfg, "downloading", version=available)

        try:
            log.info("Downloading %s …", download_url)
            download_file(download_url, tarball, cfg, checksum)
            log.info("Download complete and checksum verified.")
        except RuntimeError as e:
            log.error("Download error: %s", e)
            report_status(cfg, "failed", version=available, error=str(e))
            try:
                os.unlink(tarball)
            except Exception:
                pass
            continue

        report_status(cfg, "installing", version=available)

        try:
            install_update(tarball, available)
            log.info("Install complete.")
        except Exception as e:
            log.error("Install error: %s", e)
            report_status(cfg, "failed", version=available, error=str(e))
            continue

        report_status(cfg, "complete", version=available)

        # Write flag so the client wrapper restarts against the new version
        try:
            with open(UPDATE_READY_FLAG, "w") as fh:
                fh.write(available)
            log.info("Wrote restart flag — client will restart shortly.")
        except Exception as e:
            log.warning("Could not write restart flag: %s", e)

        # Clean up tarball
        try:
            os.unlink(tarball)
        except Exception:
            pass

        # Sleep full interval before next check
        time.sleep(check_interval)


if __name__ == "__main__":
    run()
