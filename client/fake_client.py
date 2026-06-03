#!/usr/bin/env python3
"""
Fake GraceLab workstation client for API testing.

Usage:
  python3 fake_client.py --server http://localhost:5000 \
                         --hostname LAB-PC-01 \
                         --token <station-token> \
                         --code 842-913

Walks through: heartbeat -> validate -> start -> (wait) -> end
"""
import argparse
import time
import sys
import json
try:
    import urllib.request
    import urllib.error
except ImportError:
    print("Python 3 required.")
    sys.exit(1)


def api(server, hostname, token, method, path, body=None):
    url = f"{server.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Station-ID": hostname,
            "Authorization": f"Bearer {token}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}", "raw": body.decode()}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def main():
    parser = argparse.ArgumentParser(description="GraceLab fake station client")
    parser.add_argument("--server", default="http://localhost:5000")
    parser.add_argument("--hostname", required=True, help="Station hostname (X-Station-ID)")
    parser.add_argument("--token", required=True, help="Station bearer token")
    parser.add_argument("--code", help="Session code to validate and start (e.g. 842-913)")
    parser.add_argument("--duration", type=int, default=5,
                        help="Seconds to run session before ending (default 5)")
    args = parser.parse_args()

    def step(label, result):
        status = "OK" if result.get("ok") else "FAIL"
        print(f"  [{status}] {label}")
        for k, v in result.items():
            if k != "ok":
                print(f"         {k}: {v}")
        if not result.get("ok"):
            print("Stopping — server returned an error.")
            sys.exit(1)
        return result

    print(f"\nGraceLab Fake Client — {args.hostname} -> {args.server}\n")

    # 1. Heartbeat
    print("1. Sending heartbeat...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/station/heartbeat",
            {"hostname": args.hostname, "status": "available", "current_session_id": None,
             "client_version": "fake-0.1"})
    step("heartbeat", r)

    if not args.code:
        print("\nNo --code provided. Heartbeat-only test complete.")
        return

    # 2. Validate code
    print(f"\n2. Validating code {args.code}...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/session/validate",
            {"code": args.code})
    r = step("validate", r)
    session_id = r["session_id"]

    # 3. Start session
    print(f"\n3. Starting session {session_id}...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/session/start",
            {"session_id": session_id, "hostname": args.hostname})
    step("start", r)
    print(f"   Session expires at: {r.get('expires_at')}")

    # 4. Run for a bit
    print(f"\n4. Running session for {args.duration} seconds...")
    for i in range(args.duration, 0, -1):
        print(f"   {i}s remaining...", end="\r")
        time.sleep(1)
    print()

    # 5. Send a warning event
    print("\n5. Sending session_warning event...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/session/event",
            {"session_id": session_id, "event_type": "session_warning",
             "message": "5-minute warning shown to guest."})
    step("event: session_warning", r)

    # 6. End session
    print("\n6. Ending session...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/session/end",
            {"session_id": session_id, "end_reason": "expired"})
    step("end", r)

    # 7. Final heartbeat showing available
    print("\n7. Final heartbeat (available)...")
    r = api(args.server, args.hostname, args.token, "POST", "/api/station/heartbeat",
            {"hostname": args.hostname, "status": "available", "current_session_id": None,
             "client_version": "fake-0.1"})
    step("heartbeat", r)

    print("\nDone. Check the dashboard to confirm session ended and station is available.\n")


if __name__ == "__main__":
    main()
