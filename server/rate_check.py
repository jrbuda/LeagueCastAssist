"""Fire enough requests to trip the per-IP limiter, then let it recover."""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:38030"
TOKEN = "LCA-BuSaUSFtSMRkHtYKYMv_rWOVgQe9BEwtQW8aN_6yTUo"


def hit(path: str) -> int:
    request = urllib.request.Request(
        BASE + path,
        headers={"X-LCA-Token": TOKEN, "User-Agent": "LCA-rate-check/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def main() -> int:
    counts: dict[int, int] = {}
    first_block = None
    for index in range(1, 321):
        status = hit("/music/manifest.json")
        counts[status] = counts.get(status, 0) + 1
        if status == 403 and first_block is None:
            first_block = index
    print("statuses:", dict(sorted(counts.items())), "| first 403 at request", first_block)

    if first_block is None:
        print("FAIL  limiter never engaged")
        return 1
    print("PASS  limiter engaged after", first_block, "requests (limit is 120 + 60 burst)")

    print("waiting 65 s for the window to clear ...")
    time.sleep(65)
    status = hit("/music/manifest.json")
    print("PASS  recovers after the window" if status == 200 else f"FAIL  still {status}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
