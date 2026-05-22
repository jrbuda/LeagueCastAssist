from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import MatchStateReducer
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter

RAW_MARKUP_PATTERN = re.compile(
    r"(@[A-Za-z0-9_:.+*\-/]+@|{{\s*[^}]+\s*}}|<[^>]*mainText|\bSpellModifierDescriptionAppend\b)",
    re.IGNORECASE,
)
VISIBLE_PLACEHOLDER_PATTERN = re.compile(r"(^|[^A-Za-z0-9])\?([^A-Za-z0-9]|$)")


@dataclass
class ValidationResult:
    champions_checked: int = 0
    abilities_checked: int = 0
    items_checked: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


async def validate_static_data(
    version: str = "latest",
    include_items: bool = True,
    strict_placeholders: bool = False,
) -> ValidationResult:
    static_data = StaticDataService(version=version, download_assets=False)
    await static_data.ensure_core_data()
    reducer = MatchStateReducer(
        static_data=static_data,
        asset_resolver=AssetResolver(local_assets=True, version=version),
        tooltip_formatter=TooltipFormatter(),
    )
    result = ValidationResult()

    for champion_id in sorted(static_data.champion_summary()):
        champion = await static_data.champion(champion_id)
        if champion is None:
            result.errors.append(f"Champion {champion_id}: missing champion data")
            continue

        result.champions_checked += 1
        abilities = reducer._abilities_from_champion(champion)
        if len(abilities) != 5:
            result.errors.append(f"{champion.name}: expected 5 abilities, got {len(abilities)}")

        for ability in abilities:
            result.abilities_checked += 1
            label = f"{champion.name} {ability.slot} {ability.name}"
            text = (
                ability.full_description
                or ability.tooltip_html
                or ability.short_description
                or ""
            )
            if not ability.name or ability.name == "Unknown Ability":
                result.errors.append(f"{label}: unknown ability name")
            if not text.strip():
                result.errors.append(f"{label}: empty tooltip")
            if RAW_MARKUP_PATTERN.search(text):
                result.errors.append(f"{label}: unresolved Riot markup")
            if has_unbalanced_spans(ability.tooltip_html):
                result.errors.append(f"{label}: unbalanced rich-text spans")
            if VISIBLE_PLACEHOLDER_PATTERN.search(text):
                message = f"{label}: visible placeholder remains"
                if strict_placeholders:
                    result.errors.append(message)
                else:
                    result.warnings.append(message)

        overlap_errors, overlap_warnings = ability_overlap_issues(champion.name, abilities)
        result.errors.extend(overlap_errors)
        result.warnings.extend(overlap_warnings)

    if include_items:
        item_lookup = static_data.item_lookup()
        formatter = TooltipFormatter()
        for item_id in static_data.summoners_rift_item_ids():
            item = item_lookup.get(item_id)
            if item is None:
                result.errors.append(f"Item {item_id}: missing item data")
                continue
            result.items_checked += 1
            label = f"Item {item.item_id} {item.name}"
            if not item.name or item.name == "Unknown Item":
                result.errors.append(f"{label}: unknown item name")
            if item.total_cost is None:
                result.warnings.append(f"{label}: missing total cost")
            if not item.description:
                result.warnings.append(f"{label}: missing description")
                continue
            rich_text = formatter.to_rich_text(item.description)
            if RAW_MARKUP_PATTERN.search(rich_text):
                result.errors.append(f"{label}: unresolved Riot markup")
            if has_unbalanced_spans(rich_text):
                result.errors.append(f"{label}: unbalanced rich-text spans")

    return result


def has_unbalanced_spans(text: str) -> bool:
    if not text:
        return False
    return text.count("<span") != text.count("</span>")


def ability_overlap_issues(champion_name: str, abilities) -> tuple[list[str], list[str]]:  # noqa: ANN001
    errors = []
    warnings = []
    for index, first in enumerate(abilities):
        for second in abilities[index + 1 :]:
            first_text = normalized_tooltip_text(first.full_description or "")
            second_text = normalized_tooltip_text(second.full_description or "")
            if len(first_text) >= 120 and len(second_text) >= 120:
                if first_text == second_text:
                    errors.append(
                        ability_pair_message(
                            champion_name,
                            first,
                            second,
                            "exact duplicate rendered tooltip",
                        )
                    )
                elif first_text in second_text or second_text in first_text:
                    errors.append(
                        ability_pair_message(
                            champion_name,
                            first,
                            second,
                            "one rendered tooltip contains the other",
                        )
                    )
                else:
                    similarity = SequenceMatcher(None, first_text, second_text).ratio()
                    if similarity >= 0.92:
                        warnings.append(
                            ability_pair_message(
                                champion_name,
                                first,
                                second,
                                f"near-duplicate rendered tooltip score={similarity:.3f}",
                            )
                        )

            first_profile = ability_stat_profile(first)
            second_profile = ability_stat_profile(second)
            if first_profile and first_profile == second_profile:
                warnings.append(
                    ability_pair_message(
                        champion_name,
                        first,
                        second,
                        "same rendered stat profile",
                    )
                )
    return errors, warnings


def ability_pair_message(champion_name: str, first, second, reason: str) -> str:  # noqa: ANN001
    return (
        f"{champion_name} {first.slot} {first.name} <-> "
        f"{second.slot} {second.name}: {reason}"
    )


def ability_stat_profile(ability) -> tuple[str | None, str | None, str | None, tuple[str, ...]]:  # noqa: ANN001
    meaningful_stat_lines = [
        line
        for line in ability.stat_lines
        if line != "Cooldown: 0s" and not line.startswith("Range: ")
    ]
    if not meaningful_stat_lines and not ability.cost:
        return ()
    return (ability.cooldown, ability.cost, ability.range, tuple(meaningful_stat_lines))


def normalized_tooltip_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\bDetailed values:\b.*$", " ", text, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate static LeagueCastAssist data rendering.")
    parser.add_argument("--version", default="latest", help="CommunityDragon version to validate")
    parser.add_argument("--skip-items", action="store_true", help="Skip Summoner's Rift item scan")
    parser.add_argument(
        "--strict-placeholders",
        action="store_true",
        help="Treat visible '?' placeholders as errors instead of warnings",
    )
    parser.add_argument("--limit", type=int, default=200, help="Maximum issues to print per class")
    args = parser.parse_args()

    result = asyncio.run(
        validate_static_data(
            version=args.version,
            include_items=not args.skip_items,
            strict_placeholders=args.strict_placeholders,
        )
    )
    print(
        f"champions={result.champions_checked} abilities={result.abilities_checked} "
        f"items={result.items_checked} errors={len(result.errors)} warnings={len(result.warnings)}"
    )
    for issue in result.errors[: args.limit]:
        print(f"ERROR {issue}")
    for issue in result.warnings[: args.limit]:
        print(f"WARN {issue}")
    if len(result.errors) > args.limit:
        print(f"ERROR ... {len(result.errors) - args.limit} more")
    if len(result.warnings) > args.limit:
        print(f"WARN ... {len(result.warnings) - args.limit} more")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
