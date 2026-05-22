from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import MatchStateReducer
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.data.tooltip_formatter import TooltipFormatter


@dataclass(frozen=True)
class AbilityAuditIssue:
    severity: str
    champion: str
    first_slot: str
    first_name: str
    second_slot: str
    second_name: str
    reason: str
    score: float | None = None

    def format(self) -> str:
        score_text = f" score={self.score:.3f}" if self.score is not None else ""
        return (
            f"{self.severity} {self.champion} {self.first_slot} {self.first_name} <-> "
            f"{self.second_slot} {self.second_name}: {self.reason}{score_text}"
        )


async def audit_ability_text(
    version: str = "latest",
    similarity_threshold: float = 0.92,
) -> list[AbilityAuditIssue]:
    static_data = StaticDataService(version=version, download_assets=False)
    await static_data.ensure_core_data()
    reducer = MatchStateReducer(
        static_data=static_data,
        asset_resolver=AssetResolver(local_assets=True, version=version),
        tooltip_formatter=TooltipFormatter(),
    )

    issues = []
    for champion_id in sorted(static_data.champion_summary()):
        champion = await static_data.champion(champion_id)
        if champion is None:
            continue
        abilities = reducer._abilities_from_champion(champion)
        for index, first in enumerate(abilities):
            for second in abilities[index + 1 :]:
                first_text = normalized_tooltip_text(first.full_description or "")
                second_text = normalized_tooltip_text(second.full_description or "")
                if len(first_text) >= 120 and len(second_text) >= 120:
                    similarity = SequenceMatcher(None, first_text, second_text).ratio()
                    if first_text == second_text:
                        issues.append(
                            AbilityAuditIssue(
                                "ERROR",
                                champion.name,
                                first.slot,
                                first.name,
                                second.slot,
                                second.name,
                                "exact duplicate rendered tooltip",
                                similarity,
                            )
                        )
                    elif first_text in second_text or second_text in first_text:
                        issues.append(
                            AbilityAuditIssue(
                                "ERROR",
                                champion.name,
                                first.slot,
                                first.name,
                                second.slot,
                                second.name,
                                "one rendered tooltip contains the other",
                                similarity,
                            )
                        )
                    elif similarity >= similarity_threshold:
                        issues.append(
                            AbilityAuditIssue(
                                "WARN",
                                champion.name,
                                first.slot,
                                first.name,
                                second.slot,
                                second.name,
                                "near-duplicate rendered tooltip",
                                similarity,
                            )
                        )

                first_profile = ability_stat_profile(first)
                second_profile = ability_stat_profile(second)
                if first_profile and first_profile == second_profile:
                    issues.append(
                        AbilityAuditIssue(
                            "WARN",
                            champion.name,
                            first.slot,
                            first.name,
                            second.slot,
                            second.name,
                            "same rendered stat profile",
                        )
                    )
    return issues


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
    parser = argparse.ArgumentParser(
        description="Audit rendered champion ability text for duplicate or near-duplicate skills."
    )
    parser.add_argument("--version", default="latest", help="CommunityDragon version to validate")
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.92,
        help="Near-duplicate threshold from 0.0 to 1.0",
    )
    parser.add_argument("--limit", type=int, default=200, help="Maximum issues to print")
    args = parser.parse_args()

    issues = asyncio.run(
        audit_ability_text(
            version=args.version,
            similarity_threshold=args.similarity_threshold,
        )
    )
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
