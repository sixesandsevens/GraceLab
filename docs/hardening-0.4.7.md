# GraceLab 0.4.7: first hardening release

Status: prepared for one test workstation; physical acceptance is still required.
This is the first small release in the hardening plan, not the complete security
overhaul. No production server restart, package publication, station push, or
database migration is performed by building this release.

## Changes and compatibility

Server changes:

- Reject deactivated users on subsequent requests, including remember-cookie restoration.
- Restrict login redirects to local paths.
- Reject the documented placeholder production secret and remove the sample unit's fallback.
- Share admission checks across validate, start, and open-start; reject stations needing attention.
- Preserve a needs-attention fault when the station stops heartbeating.

Client changes:

- Require start/end/reset hooks in normal sessions, recovery, and maintenance exit.
- Report malformed hook configuration as a failure rather than crashing the worker.
- Verify guest processes have terminated before proceeding with cleanup.
- Show an unavailable-station message when the server refuses admission for a fault.

Existing 0.4.6 clients can use the changed server. The 0.4.7 package can be
installed by the existing updater; it changes no package format, signing rule,
database schema, configuration key, or sudoers grant. Deploy the server changes
first so failed workstations stay blocked consistently. Automatic rollback and
signed releases are not implemented in this release.

## Baseline preserved for this rollout

- Original source revision: `3316505`.
- Working branch: `hardening/0.4.7` in `/home/GraceAdmin/GraceLab-hardening`.
- Private baseline: `/home/GraceAdmin/gracelab-backups/hardening-baseline-20260929T193159Z/`.
- Candidate and rollback artifacts: `/home/GraceAdmin/gracelab-releases/0.4.7/`.
- The database snapshot reported nine stations on 0.4.6.

The private baseline contains a Git bundle, a consistent SQLite backup made
through SQLite's backup API, a restored copy, and a manifest. Both database
copies passed `PRAGMA integrity_check`. This verifies database restoration,
not a full service/disaster-recovery rehearsal. It does not include the server's
secret configuration or workstation files. Do not upload database snapshots
with client packages. Take a fresh backup at the actual deployment time.

The rollback folder contains the existing published 0.4.6 archive and checksum;
the checksum was verified. Preserve that package until the lab rollout is accepted.

## Preflight

1. Confirm independent administrator login on the test workstation. A kiosk
   failure must not prevent reaching the LightDM administrator account or SSH.
2. Confirm the station is on 0.4.6 and has no active guest before the first push.
3. Check the effective `client_config.ini` paths. Start, end, and reset must point
   to existing scripts, normally `sudo /opt/gracelab-client/current/scripts/...`.
   The existing configuration files are not replaced by an OTA package.
4. Check that the `gracelab` account's sudo rules allow those exact commands
   without a password. Do not execute end/reset as a preflight on an occupied station.
5. Verify a normal session and guest cleanup on the current version and record
   the result. Record the current global update toggle and published versions.
6. Confirm the production service has its real non-placeholder secret configured.
   Do not print it in test logs or replace it during this release.

## Server deployment

The live checkout is left unchanged. To bring in the prepared branch, first
confirm `/opt/gracelab/GraceLab` has no uncommitted work and still descends from
the recorded baseline. If it has changed, review/reconcile it before deploying.

```bash
git -C /opt/gracelab/GraceLab fetch /home/GraceAdmin/GraceLab-hardening hardening/0.4.7
git -C /opt/gracelab/GraceLab merge --ff-only FETCH_HEAD
sudo systemctl restart gracelab
```

Use a planned maintenance window for the server restart. Do not run `init_db.py`:
there is no schema change. The repository's sample systemd unit was updated, but
do not overwrite the installed service unit as part of this rollout; preserve its
existing paths and environment configuration.

Verify staff login, dashboard access, station heartbeats, one code session and
one open-lab session if used. With a disposable staff account, verify deactivation
blocks an already-open browser session. Do not deactivate your only admin account.

## Push to the test workstation only

Publishing a stable version alone can update the entire fleet. Use this sequence:

1. In Settings, **disable Enable Client Auto-Updates** and save. Keep it disabled
   through the test period. This is the server's global toggle, not a station's
   local `[updates] enabled` setting; leave the local updater running.
2. Check for any previously queued station updates and allow current installs
   to finish. Disabling the global toggle does not cancel station-specific pushes.
3. Upload `gracelab-client-0.4.7.tar.gz` in Client Updates. Keep the 0.4.6 package.
4. Set Published Stable Version to `0.4.7`, leaving global auto-updates disabled.
5. Queue **Push Update only on the test workstation**. Explicit station pushes
   still work while the global automatic-update toggle is off.
6. Observe downloading/installing/completion and then verify the workstation
   actually restarted on 0.4.7 and can run and clean up a guest session.
7. Confirm the other eight stations remain on 0.4.6.

Do not rely on the stored dashboard update-policy field to isolate this rollout;
its end-to-end enforcement is still pending. Use the global enable toggle and
explicit per-station pushes above. The current installer has no startup health
check, so an update-complete status alone is not acceptance.

## Physical acceptance checklist

Record pass/fail, station, time, and relevant logs for each item:

- [ ] Code admission, countdown/warning, expiry, extension, and staff termination.
- [ ] Open-lab admission and expiry, if that workflow is used.
- [ ] A disposable guest file and browser state are removed before the next guest.
- [ ] Guest processes are gone when cleanup finishes; no blank display is left behind.
- [ ] Maintenance entry/exit, return to service, remote reset, and idle reboot.
- [ ] Server outage and reconnection do not clear a needs-attention fault.
- [ ] On this isolated station, a temporarily misconfigured reset hook produces
      needs-attention, never profile-reset-success. Restore the original path,
      perform a successful reset, verify the guest home, then return it to service.
- [ ] A temporarily misconfigured start/end hook likewise fails visibly. Restore
      each setting before continuing. Do not modify root-owned scripts to simulate failure.
- [ ] Reboot after a successful session/reset and confirm the station is usable.
- [ ] Roll back to 0.4.6 using the updater, verify a session/reset, then reinstall
      0.4.7 using the same one-station process.
- [ ] Complete one normal operating day on the test workstation.

Failure of guest cleanup, unintended admission, or interrupted active use stops
the rollout. A fault can now remain visible while offline; staff must correct the
underlying problem, reset successfully, and explicitly return the station to service.

These acceptance checks are pending. Automated tests do not prove LightDM/XFCE
behavior or cleanup of a real browser profile on a physical workstation.

## Rollback

For an otherwise running test workstation, keep global automatic updates off,
set Published Stable Version back to `0.4.6`, and push only that workstation.
The existing updater compares version equality, so a different older target is
installable. Verify actual restart, session operation and cleanup afterward.

If the kiosk cannot start but the updater still runs, the same queued rollback
may work. If it cannot proceed (for example, a stale timer file keeps it busy),
use the independent administrator connection and confirm no guest is active.
Copy the preserved 0.4.6 archive into `/opt/gracelab-client/downloads/`, invoke
the existing root helper, and reboot the idle test station:

```bash
sudo /opt/gracelab-client/updater/do-install.sh 0.4.6 /opt/gracelab-client/downloads/gracelab-client-0.4.6.tar.gz
sudo reboot
```

Keep the station out of service until guest cleanup and a fresh session pass.
This is a manual recovery procedure to rehearse; it has not been executed here.
The 0.4.7 release does not change the installer, assets, or guest template.

For server rollback, with a clean deployed checkout and the rollout commits
preserved on the working branch, return the live source to `3316505` and restart
the service. No database restore is necessary for this release:

```bash
git -C /opt/gracelab/GraceLab switch --detach 3316505
sudo systemctl restart gracelab
```

Do not restore the old database merely to roll back source: doing so would lose
sessions and staff changes made since the backup.

## Remaining releases

Proceed after this test gate, with separate review and rollback for each:

1. Persistent cleanup/recovery state and startup preflight, including failures
   that happen while reports cannot reach the server.
2. Code-bound session admission and atomic code/station claims with a short,
   tracked compatibility window for the legacy ID-only start endpoint.
3. A shared update/lifecycle lock, staged installation, startup health reporting,
   and tested rollback.
4. Signed packages verified by the privileged helper and HTTPS. Bootstrap the
   helper/public key through the existing updater as agreed, explicitly trusting
   the current deployment during that transition. Enforce signing only after
   the bootstrap and recovery paths have passed on the test station.
5. Client decomposition, versioned migrations, CI, monitoring, retention, and
   completion/removal of ineffective settings.

The root-helper trust issue, ID-only session start, concurrency gaps, and
interrupted-cleanup persistence are still known limitations after 0.4.7.

## Build and automated validation

From the repository root with server dependencies and Tkinter available:

```bash
FLASK_ENV=testing python3 -m unittest discover -s server/tests
python3 -m unittest discover -s client/tests
bash tools/package-client.sh --output-dir /path/to/private/staging
```

The package builder supports a writable staging directory without root; its
default remains `/var/lib/gracelab/updates`. Building a package does not upload
it, change settings, or queue a station. Check the SHA256 sidecar before upload.
