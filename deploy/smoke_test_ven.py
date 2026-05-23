#!/usr/bin/env python3
"""
Smoke-test client for the openleadr-vtn-au Fly.io deployment.

Talks to the VTN via OpenADR 3.0 over plain HTTPS using only `requests`
so the auth + payload shapes stay visible. Uses the bl-client (business
logic / admin) credentials loaded by fixtures/users.sql in upstream
openleadr-rs to create a Program and an Event, then re-reads them back.

Rotate those default credentials before pointing real devices at this VTN.

Usage:
    pip install requests
    python smoke_test_ven.py
    VTN_URL=https://openleadr-vtn-au.fly.dev python smoke_test_ven.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

VTN_URL = os.environ.get("VTN_URL", "https://openleadr-vtn-au.fly.dev").rstrip("/")
CLIENT_ID = os.environ.get("CLIENT_ID", "bl-client")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET", "bl-client")

TIMEOUT = 15


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===")


def get_token() -> str:
    banner(f"POST {VTN_URL}/auth/token  (client_id={CLIENT_ID})")
    r = requests.post(
        f"{VTN_URL}/auth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    tok = r.json()
    print(f"  token_type={tok.get('token_type')} expires_in={tok.get('expires_in')}")
    return tok["access_token"]


def get_json(path: str, token: str) -> object:
    banner(f"GET {VTN_URL}{path}")
    r = requests.get(
        f"{VTN_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    print(json.dumps(body, indent=2)[:800])
    return body


def post_json(path: str, body: dict, token: str) -> dict:
    banner(f"POST {VTN_URL}{path}")
    r = requests.post(
        f"{VTN_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=TIMEOUT,
    )
    if r.status_code >= 400:
        print(f"  HTTP {r.status_code}: {r.text[:500]}")
    r.raise_for_status()
    out = r.json()
    print(json.dumps(out, indent=2)[:800])
    return out


def main() -> int:
    print(f"VTN target: {VTN_URL}")

    # 0. /health is open (no auth required)
    try:
        h = requests.get(f"{VTN_URL}/health", timeout=TIMEOUT)
        print(f"GET /health → HTTP {h.status_code}")
    except requests.RequestException as e:
        print(f"  /health unreachable: {e}")
        return 1

    try:
        token = get_token()
    except requests.HTTPError as e:
        print(f"  auth failed: {e} — body: {e.response.text[:300]}")
        print("  Did you load fixtures/users.sql into the Fly Postgres?")
        return 1

    # 1. List programs (empty array on a fresh VTN)
    get_json("/programs", token)

    # 2. Create a CSIP-AUS-flavoured demo program.
    # Schema = openleadr-wire::program::ProgramRequest:
    #   programName (required, 1..=128 chars), intervalPeriod, programDescriptions,
    #   payloadDescriptors, attributes, targets.
    program = post_json(
        "/programs",
        {
            "objectType": "PROGRAM",
            "programName": "AU-Demo-Flex",
        },
        token,
    )
    program_id = program["id"]

    # 3. Create an event in that program, starting in ~5 min for 30 min,
    #    with a single PRICE interval. Schema = EventRequest.
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    event = post_json(
        "/events",
        {
            "programID": program_id,
            "eventName": "smoke-test-event",
            "priority": 0,
            "intervalPeriod": {
                "start": start.isoformat().replace("+00:00", "Z"),
                "duration": "PT30M",
            },
            "intervals": [
                {
                    "id": 0,
                    "payloads": [
                        {"type": "PRICE", "values": [0.42]},
                    ],
                }
            ],
        },
        token,
    )
    print(f"\nCreated program {program_id} and event {event['id']}.")

    # 4. List events filtered by program to confirm round-trip
    get_json(f"/events?programID={program_id}", token)

    print("\nSmoke test passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
