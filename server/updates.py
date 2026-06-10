import hashlib
import os
import re
from datetime import datetime, timezone

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)
from flask_login import current_user, login_required

from audit import log_audit
from extensions import csrf, db
from models import Setting, Station

updates_bp = Blueprint("updates", __name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _auth_station():
    station_id = request.headers.get("X-Station-ID")
    auth_header = request.headers.get("Authorization", "")
    if not station_id or not auth_header.startswith("Bearer "):
        return None, (jsonify({"ok": False, "error": "missing_credentials"}), 401)
    token = auth_header[len("Bearer "):]
    station = Station.query.filter_by(hostname=station_id).first()
    if not station or not station.verify_token(token):
        return None, (jsonify({"ok": False, "error": "invalid_credentials"}), 401)
    return station, None


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _package_filename(version):
    return f"gracelab-client-{version}.tar.gz"


def _get_or_compute_checksum(filepath):
    """Return SHA256 for a package file, caching it in a .sha256 sidecar."""
    sha_path = filepath + ".sha256"
    if os.path.exists(sha_path):
        try:
            with open(sha_path) as f:
                return f.read().strip().split()[0]
        except Exception:
            pass
    checksum = _sha256_of(filepath)
    try:
        with open(sha_path, "w") as f:
            f.write(checksum + "\n")
    except Exception:
        pass
    return checksum


def _admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Station API — update check
# ---------------------------------------------------------------------------

@updates_bp.route("/api/station/update-check", methods=["POST"])
@csrf.exempt
def update_check():
    station, err = _auth_station()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    channel = data.get("channel", "stable")
    reported_version = data.get("current_version") or station.client_version or ""

    # Determine target version: per-station override takes priority. A manual
    # station push should still work even if the global automatic-update toggle
    # is off; otherwise the UI can say "queued" while clients always receive
    # updates_disabled.
    forced_target = station.desired_client_version or ""
    if forced_target:
        target_version = forced_target
    elif not Setting.get_bool("client_updates_enabled", False):
        station.last_update_check_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({"ok": True, "update_available": False, "reason": "updates_disabled"})
    elif channel == "beta":
        target_version = Setting.get("client_beta_version", "")
    else:
        target_version = Setting.get("client_stable_version", "")

    station.last_update_check_at = datetime.now(timezone.utc)
    db.session.commit()

    if not target_version:
        return jsonify({"ok": True, "update_available": False, "reason": "no_version_configured"})

    if reported_version == target_version:
        return jsonify({"ok": True, "update_available": False,
                        "current_version": reported_version})

    updates_dir = current_app.config["UPDATES_DIR"]
    filename = _package_filename(target_version)
    filepath = os.path.join(updates_dir, filename)

    if not os.path.isfile(filepath):
        return jsonify({"ok": True, "update_available": False,
                        "reason": "package_not_found",
                        "target_version": target_version})

    checksum = _get_or_compute_checksum(filepath)
    download_url = f"{request.url_root.rstrip('/')}/updates/download/{filename}"

    return jsonify({
        "ok": True,
        "update_available": True,
        "current_version": reported_version,
        "available_version": target_version,
        "download_url": download_url,
        "checksum_sha256": checksum,
        "forced": bool(station.desired_client_version),
    })


# ---------------------------------------------------------------------------
# Station API — update status reporting
# ---------------------------------------------------------------------------

@updates_bp.route("/api/station/update-status", methods=["POST"])
@csrf.exempt
def update_status():
    station, err = _auth_station()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    status = data.get("status", "")
    version = data.get("version", "")
    error = data.get("error", "")

    allowed = {"checking", "downloading", "installing", "complete", "failed"}
    if status not in allowed:
        return jsonify({"ok": False, "error": "invalid_status"}), 400

    now = datetime.now(timezone.utc)
    station.client_update_status = status
    station.client_update_error = error or None

    if status == "downloading":
        station.last_update_started_at = now
    elif status in ("complete", "failed"):
        station.last_update_finished_at = now
        if status == "complete" and version:
            station.client_version = version[:32]
            station.desired_client_version = None

    db.session.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Package download (station-authenticated)
# ---------------------------------------------------------------------------

@updates_bp.route("/updates/download/<filename>")
@csrf.exempt
def download_package(filename):
    station, err = _auth_station()
    if err:
        return err

    if "/" in filename or "\\" in filename or ".." in filename:
        return jsonify({"ok": False, "error": "invalid_filename"}), 400
    if not filename.endswith(".tar.gz"):
        return jsonify({"ok": False, "error": "invalid_filename"}), 400

    updates_dir = current_app.config["UPDATES_DIR"]
    return send_from_directory(updates_dir, filename, as_attachment=True)


# ---------------------------------------------------------------------------
# Admin — package management
# ---------------------------------------------------------------------------

def _list_packages(updates_dir):
    packages = []
    if not os.path.isdir(updates_dir):
        return packages
    for fname in sorted(os.listdir(updates_dir)):
        if not fname.endswith(".tar.gz"):
            continue
        m = re.match(r'^gracelab-client-(.+)\.tar\.gz$', fname)
        if not m:
            continue
        filepath = os.path.join(updates_dir, fname)
        stat = os.stat(filepath)
        size = stat.st_size
        size_str = (f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024
                    else f"{size / 1024:.0f} KB")
        packages.append({
            "filename": fname,
            "version": m.group(1),
            "size_str": size_str,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "checksum": _get_or_compute_checksum(filepath),
        })
    return packages


@updates_bp.route("/admin/updates")
@login_required
@_admin_required
def updates_admin():
    updates_dir = current_app.config["UPDATES_DIR"]
    packages = _list_packages(updates_dir)
    return render_template("updates_admin.html", packages=packages)


@updates_bp.route("/admin/updates/upload", methods=["POST"])
@login_required
@_admin_required
def upload_package_route():
    f = request.files.get("package")
    if not f or not f.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("updates.updates_admin"))

    filename = os.path.basename(f.filename)
    if not re.match(r'^gracelab-client-[0-9]+\.[0-9]+\.[0-9]+[-.a-z0-9]*\.tar\.gz$', filename):
        flash("Filename must match gracelab-client-<version>.tar.gz", "danger")
        return redirect(url_for("updates.updates_admin"))

    updates_dir = current_app.config["UPDATES_DIR"]
    os.makedirs(updates_dir, exist_ok=True)
    dest = os.path.join(updates_dir, filename)
    f.save(dest)

    # Invalidate any cached checksum
    sha_path = dest + ".sha256"
    if os.path.exists(sha_path):
        os.unlink(sha_path)
    checksum = _get_or_compute_checksum(dest)

    log_audit("update_package_uploaded",
              details={"filename": filename, "checksum": checksum})
    db.session.commit()

    flash(f"{filename} uploaded (SHA256: {checksum[:16]}…)", "success")
    return redirect(url_for("updates.updates_admin"))


@updates_bp.route("/admin/updates/<filename>/delete", methods=["POST"])
@login_required
@_admin_required
def delete_package(filename):
    if "/" in filename or "\\" in filename or ".." in filename:
        flash("Invalid filename.", "danger")
        return redirect(url_for("updates.updates_admin"))

    updates_dir = current_app.config["UPDATES_DIR"]
    filepath = os.path.join(updates_dir, filename)
    if os.path.isfile(filepath):
        os.unlink(filepath)
        sha_path = filepath + ".sha256"
        if os.path.exists(sha_path):
            os.unlink(sha_path)
        log_audit("update_package_deleted", details={"filename": filename})
        db.session.commit()
        flash(f"{filename} deleted.", "success")
    else:
        flash("File not found.", "danger")

    return redirect(url_for("updates.updates_admin"))
