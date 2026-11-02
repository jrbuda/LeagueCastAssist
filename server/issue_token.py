"""Create or rotate a media-server token.

    python3 issue_token.py --label "LCA 0.7.0"
    python3 issue_token.py --label "old" --disable           # retire one
    python3 issue_token.py --list

The plaintext token is printed once and never stored: only its sha256 goes into
tokens.json. Send SIGHUP to the service (or `systemctl reload lca-media`) after
editing; no redeploy is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parent / "tokens.json"


def load(path: Path) -> dict:
    if not path.exists():
        return {"tokens": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("tokens", [])
    return payload


def save(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage lca media server tokens")
    parser.add_argument("--file", default=DEFAULT_FILE, type=Path)
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--disable", action="store_true", help="disable the token with this label/id"
    )
    parser.add_argument("--list", action="store_true", help="show configured tokens")
    parser.add_argument("--issue", action="store_true", help="generate a new token")
    args = parser.parse_args()

    payload = load(args.file)

    if args.list:
        for entry in payload["tokens"]:
            state = "enabled " if entry.get("enabled", True) else "DISABLED"
            identifier = entry.get("id", "")
            label = entry.get("label", "")
            created = entry.get("created_at", "")
            print(f"{state}  {identifier:<24} {label:<20} {created}")
        return 0

    if args.disable:
        matched = 0
        for entry in payload["tokens"]:
            if args.label and args.label in {entry.get("id"), entry.get("label")}:
                entry["enabled"] = False
                matched += 1
        if not matched:
            print(f"error: no token matched {args.label!r}", file=sys.stderr)
            return 1
        save(args.file, payload)
        print(f"disabled {matched} token(s); reload the service to apply")
        return 0

    if not args.issue or not args.label:
        parser.print_help()
        return 2

    token = "LCA-" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    token_id = args.label.strip().lower().replace(" ", "-")
    payload["tokens"] = [entry for entry in payload["tokens"] if entry.get("id") != token_id]
    payload["tokens"].append(
        {
            "id": token_id,
            "label": args.label,
            "sha256": digest,
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "enabled": True,
        }
    )
    save(args.file, payload)

    print(f"token id : {token_id}")
    print(f"sha256   : {digest}")
    print(f"token    : {token}")
    print()
    print("Put this in the client's Settings -> Media Server token field / build default.")
    print("Reload the service to apply: systemctl reload lca-media")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
