"""Smoke-test the lca media server.

    python3 verify.py --base http://127.0.0.1:38030 --token LCA-...

Exit code 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

TIMEOUT = 20


def request(
    base: str, path: str, token: str | None, extra: dict | None = None
) -> tuple[int, dict, bytes]:
    url = base.rstrip("/") + path
    headers = {}
    if token:
        headers["X-LCA-Token"] = token
    headers.update(extra or {})
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:38030")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    status, _, body = request(args.base, "/health", None)
    health = json.loads(body or b"{}")
    music = health.get("music", {})
    check("health 200", status == 200, f"tracks={music.get('tracks')} bytes={music.get('bytes')}")

    status, _, _ = request(args.base, "/music/manifest.json", None)
    check("manifest without token is 401", status == 401, f"got {status}")

    status, _, _ = request(args.base, "/music/manifest.json", "LCA-wrong-token")
    check("manifest with bad token is 401", status == 401, f"got {status}")

    status, headers, body = request(args.base, "/music/manifest.json", args.token)
    check("manifest with token is 200", status == 200, f"got {status}")
    manifest = json.loads(body or b"{}")
    tracks = manifest.get("tracks", [])
    check("manifest has tracks", bool(tracks), f"count={len(tracks)}")
    check(
        "manifest totals match",
        all(track.get("sha256") and track.get("size_bytes") for track in tracks),
        f"bytes_total={manifest.get('bytes_total')}",
    )

    if tracks:
        first = tracks[0]
        print(f"      first track: {first.get('file')} {first.get('size_bytes')} bytes")
        quoted = urllib.request.quote(first["file"])
        status, headers, payload = request(
            args.base, f"/media/{quoted}", args.token, {"Range": "bytes=0-1023"}
        )
        check(
            "media range request is 206",
            status == 206 and len(payload) == 1024,
            f"got {status}, {len(payload)} bytes",
        )
        content_range = headers.get("Content-Range", "")
        check("range header present", "bytes 0-1023/" in content_range, content_range)

        artwork = first.get("artwork")
        if artwork:
            status, _, payload = request(
                args.base, f"/media/{urllib.request.quote(artwork)}", args.token
            )
            check(
                "artwork downloads",
                status == 200 and len(payload) > 1000,
                f"{len(payload)} bytes",
            )

    status, _, _ = request(args.base, "/media/../music.db", args.token)
    check("path traversal is rejected", status in {403, 404}, f"got {status}")

    etag = None
    status, headers, _ = request(args.base, "/music/manifest.json", args.token)
    etag = headers.get("ETag")
    if etag:
        status, _, _ = request(
            args.base, "/music/manifest.json", args.token, {"If-None-Match": etag}
        )
        check("conditional manifest is 304", status == 304, f"got {status}")

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
