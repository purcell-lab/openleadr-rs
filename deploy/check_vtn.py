#!/usr/bin/env python3
"""
Read-only post-deploy check for the openleadr-vtn-au VTN.

Needs no credentials while PUBLIC_READS=true. Checks:
  - /health returns 200;
  - unauthenticated writes are still refused (401);
  - every event was updated recently, has no null values, has intervals
    with no gaps or overlaps, and covers the current time.

Exits 0 when everything passes, 1 otherwise.

Usage:
    python check_vtn.py
    VTN_URL=https://openleadr-vtn-au.fly.dev MAX_AGE_MIN=15 python check_vtn.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

VTN_URL = os.environ.get("VTN_URL", "https://openleadr-vtn-au.fly.dev").rstrip("/")
MAX_AGE_MIN = float(os.environ.get("MAX_AGE_MIN", "15"))
# Programs whose events are allowed to be stale, such as the far-future smoke test.
IGNORE_STALE = {"AU-zSmoke-Test"}
PAGE = 50

failures: list[str] = []


def request(path: str, method: str = "GET") -> tuple[int, bytes, float]:
    req = urllib.request.Request(VTN_URL + path, method=method)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), time.monotonic() - start
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - start


def duration(iso: str) -> timedelta:
    m = re.fullmatch(
        r"P(?:\d+Y)?(?:\d+M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", iso
    )
    if not m:
        raise ValueError(f"unsupported duration {iso!r}")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


def check(ok: bool, msg: str) -> None:
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok:
        failures.append(msg)


def get_all(path: str) -> list[dict]:
    out: list[dict] = []
    skip = 0
    while True:
        code, body, _ = request(f"{path}?limit={PAGE}&skip={skip}")
        if code != 200:
            check(False, f"GET {path} returned {code}")
            return out
        page = json.loads(body)
        out += page
        if len(page) < PAGE:
            return out
        skip += PAGE


def main() -> int:
    code, _, t = request("/health")
    check(code == 200, f"/health {code} in {t * 1000:.0f} ms")

    code, _, _ = request("/programs", method="POST")
    check(code == 401, f"unauthenticated POST /programs refused ({code})")

    programs = get_all("/programs")
    names = {p["id"]: p["programName"] for p in programs}
    events = get_all("/events")
    print(f"     {len(programs)} programs, {len(events)} events")

    now = datetime.now(timezone.utc)
    for e in events:
        name = names.get(e["programID"], e["programID"])
        age = (now - datetime.fromisoformat(e["modificationDateTime"])).total_seconds() / 60
        if name in IGNORE_STALE:
            continue
        base = e.get("intervalPeriod") or {}
        spans = []
        nulls = 0
        for i in e.get("intervals", []):
            ip = i.get("intervalPeriod") or base
            start = datetime.fromisoformat(ip["start"])
            spans.append((start, start + duration(ip["duration"])))
            nulls += sum(
                v is None for p in i.get("payloads", []) for v in p.get("values", [])
            )
        gaps = sum(1 for a, b in zip(spans, spans[1:]) if a[1] != b[0])
        covers = any(s <= now < f for s, f in spans)
        ok = age <= MAX_AGE_MIN and nulls == 0 and gaps == 0 and covers
        check(
            ok,
            f"{name}: age {age:.1f} min, {len(spans)} intervals, "
            f"gaps/overlaps {gaps}, nulls {nulls}, covers now {covers}",
        )

    with_events = {e["programID"] for e in events}
    empty = sorted(names[p] for p in names if p not in with_events)
    if empty:
        print(f"NOTE programs with no event: {', '.join(empty)}")

    print(f"\n{'OK' if not failures else f'{len(failures)} check(s) failed'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
