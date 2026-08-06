from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass

import httpx

from league_cast_assist.data.static_data import (
    RAW_PLACEHOLDER_PATTERN,
    StaticDataService,
    best_item_description_candidate,
    effect_text_has_nonzero_number,
    item_description_candidates,
)


@dataclass(frozen=True)
class ItemAuditIssue:
    severity: str
    item_id: int
    item_name: str
    reason: str

    def format(self) -> str:
        return f"{self.severity} Item {self.item_id} {self.item_name}: {self.reason}"


async def audit_item_text(version: str = "latest") -> list[ItemAuditIssue]:
    static_data = StaticDataService(version=version, download_assets=False)
    await static_data.ensure_core_data()
    entries = static_data.string_table()
    item_lookup = static_data.item_lookup()
    item_bins = static_data.item_bin_lookup()
    raw_items = static_data.raw_items()

    issues = []
    raw_by_id = {
        raw_item["id"]: raw_item
        for raw_item in raw_items
        if isinstance(raw_item, dict) and isinstance(raw_item.get("id"), int)
    }
    for item_id in static_data.summoners_rift_item_ids():
        item = item_lookup.get(item_id)
        raw_item = raw_by_id.get(item_id)
        if item is None or raw_item is None:
            issues.append(ItemAuditIssue("ERROR", item_id, "Unknown Item", "missing item data"))
            continue

        selected_text = item.description or ""
        candidates = item_description_candidates(raw_item, entries, item_bins.get(item_id))
        best_candidate = best_item_description_candidate(candidates)
        if best_candidate is not None and tooltip_signature(
            best_candidate.text
        ) != tooltip_signature(selected_text):
            issues.append(
                ItemAuditIssue(
                    "ERROR",
                    item_id,
                    item.name,
                    f"selected description is not best candidate ({best_candidate.source_key})",
                )
            )

        unresolved = leftover_placeholders(selected_text)
        if unresolved:
            issues.append(
                ItemAuditIssue(
                    "ERROR",
                    item_id,
                    item.name,
                    f"unresolved placeholders: {' '.join(sorted(set(unresolved))[:5])}",
                )
            )

        visible_placeholders = visible_question_marks(selected_text)
        if visible_placeholders:
            issues.append(
                ItemAuditIssue(
                    "WARN",
                    item_id,
                    item.name,
                    "visible runtime placeholder remains (computed in-game)",
                )
            )

        if (
            not effect_text_has_nonzero_number(selected_text)
            and any(effect_text_has_nonzero_number(candidate.text) for candidate in candidates)
        ):
            issues.append(
                ItemAuditIssue(
                    "ERROR",
                    item_id,
                    item.name,
                    "effect text missing numbers available in another candidate",
                )
            )

        selected_has_passive = has_passive_or_active(selected_text)
        current_candidates = [
            candidate
            for candidate in candidates
            if not candidate.source_key.startswith("game_item_")
        ]
        if any(has_passive_or_active(candidate.text) for candidate in current_candidates):
            if not selected_has_passive:
                issues.append(
                    ItemAuditIssue(
                        "ERROR",
                        item_id,
                        item.name,
                        "current generated item text has a passive/active but selected "
                        "text does not",
                    )
                )

        visible_text = strip_markup(selected_text)
        if not has_multiple_visible_tokens(visible_text):
            issues.append(ItemAuditIssue("WARN", item_id, item.name, "very short visible text"))
    return issues


def has_passive_or_active(text: str) -> bool:
    lowered = text.lower()
    return "<passive" in lowered or "<active" in lowered or "unique passive" in lowered


def strip_markup(text: str) -> str:
    text = re.sub(r"%i:[^%]+%", "", text)
    text = re.sub(r"@[A-Za-z0-9_:.+*\-/]+@", "", text)
    text = re.sub(r"{{\s*[^}]+\s*}}", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def leftover_placeholders(text: str) -> list[str]:
    return RAW_PLACEHOLDER_PATTERN.findall(text) + re.findall(
        r"{{\s*[^}]+\s*}}",
        text,
    )


def visible_question_marks(text: str) -> list[str]:
    return re.findall(r"(?:^|[^A-Za-z0-9])\?([^A-Za-z0-9]|$)", text)


def has_multiple_visible_tokens(text: str) -> bool:
    return len(re.findall(r"[A-Za-z0-9]+", text)) >= 2


def tooltip_signature(text: str) -> str:
    return strip_markup(text).lower()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit rendered item descriptions against Riot item tooltip candidates."
    )
    parser.add_argument("--version", default="latest", help="CommunityDragon version to validate")
    parser.add_argument("--limit", type=int, default=200, help="Maximum issues to print")
    args = parser.parse_args()

    try:
        issues = asyncio.run(audit_item_text(version=args.version))
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"audit failed: {exc}")
        return 1
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    warnings = [issue for issue in issues if issue.severity != "ERROR"]
    print(f"issues={len(issues)} errors={len(errors)} warnings={len(warnings)}")
    for issue in issues[: args.limit]:
        print(issue.format())
    if len(issues) > args.limit:
        print(f"... {len(issues) - args.limit} more")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
