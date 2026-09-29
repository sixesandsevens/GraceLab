# 0.4.8: prevent repeated dashboard reboots

The test station exposed a pre-existing reboot race: `systemctl reboot --no-block`
can shut down the client before its completion report reaches the server. The
server keeps returning the command until completion, but the old client's replay
guard existed only in memory. A single dashboard request could reboot repeatedly.
On gracelab-07, one request at 20:00:03 UTC was not completed until 20:02:50 UTC.
Those server records support the race; workstation logs were not available here.

There was a second problem: admission checks returned `station_maintenance` for
pending reset/reboot commands. Clicking Begin Session could therefore enter the
maintenance flow even though staff had not requested maintenance.

## Changes

- Before invoking the reboot helper, atomically write and fsync a command receipt
  beside the station's session-state file (normally
  `/var/lib/gracelab-client/reboot-command.json`). Sync its parent directory too.
- Retain the receipt after reporting completion. When the same command arrives
  again, report its saved result instead of rebooting again.
- If shutdown interrupted the result write, compare Linux boot IDs. A new boot
  completes the old request; a same-boot process restart fails it safely. An
  administrator may issue a new command to explicitly retry.
- If the receipt cannot be saved or is corrupt, do not reboot. Report a fault.
- Retry a lost completion/failure report on later heartbeats without repeating
  the reboot helper.
- Block Begin Session and code entry while reboot is being processed.
- Return a distinct pending-command response; show a waiting screen instead of
  entering maintenance. Actual maintenance requests keep their existing behavior.

No schema, sudoers, root-helper, or installer changes are required. The existing
package builder includes the new `reboot_guard.py` module. Both old and new clients
can use the revised server; the replay fix requires client 0.4.8. Reset-command
replay across process restarts remains separate work.

## Rollout

Keep global automatic updates disabled. Deploy the server changes, publish the
0.4.8 package, then queue only gracelab-07. Do not expand the fleet rollout yet.
Preserve the 0.4.7 package and fresh server/database backup. Rolling back the client
restores the reboot bug; avoid dashboard reboot on older versions.

After the station reports 0.4.8, request exactly one reboot while idle. Wait for
it to return, then click Begin Session. It must start a session without a second
reboot or unexpected maintenance transition. End the session, verify cleanup,
and request a second, new reboot while idle to verify intentional requests still
work. Each request should produce one reboot. Physical acceptance is pending.

Automated regression tests simulate shutdown before helper return, a lost
completion report, same-boot process restart, failed helpers, corrupt receipts,
storage failure, repeated command IDs, new command IDs, and admission/UI behavior.
