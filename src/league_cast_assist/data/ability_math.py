from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER_PATTERN = re.compile(r"@([A-Za-z0-9_{}:.+*\-/]+)@")
HASH_KEY_PATTERN = re.compile(r"^\{[0-9a-f]{8}\}$", re.IGNORECASE)

STAT_NAMES = {
    1: "Armor",
    2: "AD",
    3: "AP",
    4: "Attack Speed",
    5: "Move Speed",
    6: "Magic Resist",
    7: "Move Speed",
    8: "Critical Strike Chance",
    9: "Critical Strike Damage",
    12: "Health",
    18: "Life Steal",
    29: "Lethality",
    31: "Attack Range",
}

STAT_FORMULA_PREFIX = {
    2: "bonus ",
}


@dataclass(frozen=True)
class SpellBinData:
    data_values: dict[str, list[float]] = field(default_factory=dict)
    calculations: dict[str, Any] = field(default_factory=dict)
    effect_amounts: dict[str, list[float]] = field(default_factory=dict)
    cooldown: list[float] = field(default_factory=list)
    cost: list[float] = field(default_factory=list)
    range: list[float] = field(default_factory=list)
    object_name: str | None = None
    rank_count: int | None = None

    @classmethod
    def from_raw(
        cls,
        raw_spell: dict[str, Any] | None,
        rank_count: int | None = None,
    ) -> SpellBinData:
        if not isinstance(raw_spell, dict):
            return cls()

        spell = raw_spell.get("mSpell") if isinstance(raw_spell.get("mSpell"), dict) else raw_spell
        tooltip_data = tooltip_data_from_spell(spell)
        object_name = None
        if tooltip_data:
            object_name = string_or_none(tooltip_data.get("mObjectName"))
        object_name = object_name or string_or_none(raw_spell.get("ObjectName"))
        object_name = object_name or string_or_none(raw_spell.get("mScriptName"))
        data_values: dict[str, list[float]] = {}
        for raw_value in spell.get("DataValues") or []:
            if not isinstance(raw_value, dict):
                continue
            name = raw_value.get("name")
            values = raw_value.get("values")
            if isinstance(name, str) and isinstance(values, list):
                data_values[name] = [
                    float(value) for value in values if isinstance(value, int | float)
                ]

        calculations = spell.get("mSpellCalculations")
        return cls(
            data_values=data_values,
            calculations=calculations if isinstance(calculations, dict) else {},
            effect_amounts=effect_amounts_from_spell(spell),
            cooldown=number_list(spell.get("cooldownTime")),
            cost=number_list(spell.get("mana")),
            range=number_list(spell.get("castRangeDisplayOverride") or spell.get("castRange")),
            object_name=object_name,
            rank_count=(
                rank_count
                if rank_count is not None
                else rank_count_from_tooltip_data(tooltip_data)
            ),
        )

    def resolve_placeholder(self, placeholder: str) -> str | None:
        normalized = normalize_placeholder_name(placeholder)
        multiplier = 1.0
        if "*" in normalized:
            normalized, raw_multiplier = normalized.split("*", 1)
            try:
                multiplier = float(raw_multiplier)
            except ValueError:
                multiplier = 1.0

        key = normalized.split(":")[-1].split(".", 1)[0]
        effect_match = re.fullmatch(r"Effect(\d+)Amount", key, flags=re.IGNORECASE)
        if effect_match:
            values = self.effect_amounts.get(effect_match.group(1))
            if values:
                return format_series(
                    [value * multiplier for value in values],
                    self.rank_count,
                )
        calculation_key = lookup_key(self.calculations, key)
        if calculation_key:
            return self.format_calculation(calculation_key)
        data_key = lookup_key(self.data_values, key)
        if data_key:
            return self.format_series(
                [value * multiplier for value in self.data_values[data_key]]
            )
        split_value = self.melee_ranged_split_value(key)
        if split_value:
            return split_value
        if key.lower() == "cooldown" and self.cooldown:
            return self.format_series(self.cooldown)
        if key.lower() == "cost" and self.cost:
            return self.format_series(self.cost)
        if key.lower() == "range" and self.range:
            return self.format_series(self.range)
        return None

    def format_series(self, values: list[float]) -> str:
        return format_series(values, self.rank_count)

    def format_percent_series(self, values: list[float]) -> str:
        return format_percent_series(values, self.rank_count)

    def format_display_percent_series(self, values: list[float]) -> str:
        return format_display_percent_series(values, self.rank_count)

    def format_calculation(
        self,
        key: str,
        seen_keys: set[str] | None = None,
        depth: int = 0,
    ) -> str | None:
        if depth > 20:
            return None
        seen_keys = seen_keys or set()
        if key in seen_keys:
            return None
        seen_keys.add(key)
        calculation = self.calculations.get(key)
        if not isinstance(calculation, dict):
            return None

        if calculation.get("__type") == "GameCalculationModified":
            return self.format_modified_calculation(calculation, seen_keys, depth + 1)

        display_as_percent = calculation.get("mDisplayAsPercent") is True
        multiplier = self._format_subpart(
            calculation.get("mMultiplier"),
            display_as_percent,
            seen_keys,
            depth + 1,
        )
        parts = []
        for part in calculation.get("mFormulaParts") or []:
            formatted = self._format_formula_part(part, display_as_percent, seen_keys, depth + 1)
            if formatted:
                parts.append(formatted)
        result = " + ".join(parts) if parts else None
        if result and multiplier:
            return apply_multiplier_text(
                result,
                multiplier,
                display_as_percent=display_as_percent,
            )
        return result

    def format_modified_calculation(
        self,
        calculation: dict[str, Any],
        seen_keys: set[str] | None = None,
        depth: int = 0,
    ) -> str | None:
        base_key = string_or_none(calculation.get("mModifiedGameCalculation"))
        if not base_key:
            return None
        base = self.format_calculation(base_key, seen_keys, depth + 1)
        multiplier = self._format_subpart(
            calculation.get("mMultiplier"),
            calculation.get("mDisplayAsPercent") is True,
            seen_keys,
            depth + 1,
        )
        if base and multiplier:
            return apply_multiplier_text(
                base,
                multiplier,
                display_as_percent=calculation.get("mDisplayAsPercent") is True,
            )
        return base

    def melee_ranged_split_value(self, key: str) -> str | None:
        """Resolve ``<name>MeleeRangedSplit`` placeholders from paired data values.

        Riot stores melee/ranged variants as separate data values (e.g.
        ``PercentCurrentHPMelee`` / ``PercentCurrentHPRanged``) and references
        them from tooltips as a single ``@...MeleeRangedSplit@`` placeholder.
        The item bin carries no explicit link, so we match by name prefix and
        render both variants (``9% Melee / 7% Ranged``).
        """
        if key.endswith("MeleeRangedSplit"):
            prefix = key[: -len("MeleeRangedSplit")]
        elif key.endswith("Split"):
            prefix = key[: -len("Split")]
        else:
            return None
        if not prefix:
            return None
        melee_key, ranged_key = self._split_data_keys(prefix)
        if melee_key is None or ranged_key is None:
            return None
        melee_values = self.data_values[melee_key]
        ranged_values = self.data_values[ranged_key]
        if not melee_values or not ranged_values:
            return None
        formatter = (
            format_display_percent
            if self._split_value_is_percent(melee_key, melee_values)
            else format_number
        )
        return (
            f"{formatter(melee_values[0])} Melee / "
            f"{formatter(ranged_values[0])} Ranged"
        )

    def _split_data_keys(self, prefix: str) -> tuple[str | None, str | None]:
        prefix_lower = prefix.lower()
        melee_key: str | None = None
        ranged_key: str | None = None
        for data_key in self.data_values:
            lowered = data_key.lower()
            if lowered.endswith("melee"):
                base = lowered[: -len("melee")]
                kind = "melee"
            elif lowered.endswith("ranged"):
                base = lowered[: -len("ranged")]
                kind = "range"
            elif lowered.endswith("range"):
                base = lowered[: -len("range")]
                kind = "range"
            else:
                continue
            if not self._split_base_matches_prefix(base, prefix_lower):
                continue
            if kind == "melee" and (
                melee_key is None or len(data_key) > len(melee_key)
            ):
                melee_key = data_key
            elif kind == "range" and (
                ranged_key is None or len(data_key) > len(ranged_key)
            ):
                ranged_key = data_key
        if melee_key is None:
            for data_key in self.data_values:
                lowered = data_key.lower()
                if lowered == prefix_lower or (
                    lowered.startswith(prefix_lower)
                    and len(lowered) - len(prefix_lower) <= 2
                ):
                    melee_key = data_key
                    break
        if ranged_key is None:
            for data_key in self.data_values:
                if data_key.lower() in (f"ranged{prefix_lower}", f"range{prefix_lower}"):
                    ranged_key = data_key
                    break
        return melee_key, ranged_key

    @staticmethod
    def _split_base_matches_prefix(base: str, prefix_lower: str) -> bool:
        if not base or not prefix_lower or len(prefix_lower) < 4 or len(base) < 4:
            return False
        if (
            base == prefix_lower
            or base.startswith(prefix_lower)
            or prefix_lower in base
            or base in prefix_lower
        ):
            return True
        common_prefix = 0
        for left, right in zip(base, prefix_lower, strict=False):
            if left != right:
                break
            common_prefix += 1
        common_suffix = 0
        for left, right in zip(reversed(base), reversed(prefix_lower), strict=False):
            if left != right:
                break
            common_suffix += 1
        return common_prefix >= 4 and common_suffix >= 2

    def _split_value_is_percent(self, key: str, values: list[float]) -> bool:
        lowered = key.lower()
        if any(
            marker in lowered
            for marker in ("duration", "time", "delay", "cooldown", "second", "sec")
        ):
            return False
        return "percent" in lowered or (
            bool(values) and max(abs(value) for value in values) <= 1
        )

    def stat_lines(self) -> list[str]:
        lines = []
        for key, calculation in self.calculations.items():
            if not isinstance(calculation, dict):
                continue
            formatted = self.format_calculation(key)
            if formatted:
                lines.append(
                    f"{calculation_display_name(key, calculation, self.calculations)}: "
                    f"{formatted}"
                )

        if self.cooldown:
            lines.append(f"Cooldown: {self.format_series(self.cooldown)}s")
        if self.cost:
            lines.append(f"Cost: {self.format_series(self.cost)}")
        if self.range and any(value > 0 for value in self.range):
            lines.append(f"Range: {self.format_series(self.range)}")
        return lines

    def data_value_lines(self) -> list[str]:
        lines = []
        for key, values in self.data_values.items():
            if not values or should_skip_data_value(key):
                continue
            formatted = self.format_series(values)
            if formatted:
                lines.append(f"{friendly_name(key)}: {formatted}")
        return lines

    def _format_formula_part(
        self,
        part: Any,
        display_as_percent: bool = False,
        seen_keys: set[str] | None = None,
        depth: int = 0,
    ) -> str | None:
        if depth > 20:
            return None
        if not isinstance(part, dict):
            return None

        part_type = str(part.get("__type") or "")
        data_value = part.get("mDataValue")
        if part_type == "NamedDataValueCalculationPart" and isinstance(data_value, str):
            values = self.data_values.get(data_value, [])
            if display_as_percent:
                return self.format_display_percent_series(values)
            return self.format_series(values)

        if part_type == "EffectValueCalculationPart":
            effect_index = number_or_none(part.get("mEffectIndex"))
            if effect_index is None:
                return None
            values = self.effect_amounts.get(str(int(effect_index)), [])
            if display_as_percent:
                return self.format_display_percent_series(values)
            return self.format_series(values)

        if part_type == "BuffCounterByNamedDataValueCalculationPart" and isinstance(
            data_value, str
        ):
            values = self.data_values.get(data_value, [])
            if not values:
                return "per stack"
            formatted = (
                self.format_display_percent_series(values)
                if display_as_percent
                else self.format_series(values)
            )
            return f"{formatted} per stack"

        if part_type == "BuffCounterByCoefficientCalculationPart":
            coefficient = number_or_none(part.get("mCoefficient"))
            if coefficient is None:
                return "per stack"
            formatted = (
                format_display_percent(coefficient)
                if display_as_percent
                else format_number(coefficient)
            )
            return f"{formatted} per stack"

        if part_type == "StatByNamedDataValueCalculationPart" and isinstance(data_value, str):
            stat = stat_name(part.get("mStat"), part.get("mStatFormula"))
            values = self.data_values.get(data_value, [])
            if values:
                return f"{self.format_percent_series(values)} {stat}"

        if part_type == "StatByCoefficientCalculationPart":
            stat = stat_name(part.get("mStat"), part.get("mStatFormula"))
            coefficient = number_or_none(part.get("mCoefficient"))
            if coefficient is not None:
                return f"{format_percent(coefficient)} {stat}"

        if part_type == "StatBySubPartCalculationPart":
            stat = stat_name(part.get("mStat"), part.get("mStatFormula"))
            subpart = self._format_subpart(
                part.get("mSubpart"),
                display_as_percent,
                seen_keys,
                depth + 1,
            )
            return f"{subpart} {stat}" if subpart else None

        if part_type == "ByCharLevelBreakpointsCalculationPart":
            return format_breakpoint_part(part, display_as_percent)

        if part_type == "ByCharLevelInterpolationCalculationPart":
            start = number_or_none(part.get("mStartValue"))
            end = number_or_none(part.get("mEndValue"))
            if start is not None and end is not None:
                formatter = format_display_percent if display_as_percent else format_number
                return f"{formatter(start)}-{formatter(end)} by level"

        if part_type == "ByCharLevelFormulaCalculationPart":
            values = number_list(part.get("values"))
            if values:
                formatter = format_display_percent if display_as_percent else format_number
                return format_level_formula_values(values, formatter)

        if part_type == "NumberCalculationPart":
            number = number_or_none(part.get("mNumber"))
            if number is None:
                return None
            return format_display_percent(number) if display_as_percent else format_number(number)

        if part_type in {"SumOfSubPartsCalculationPart", "ProductOfSubPartsCalculationPart"}:
            return self._format_subpart(part, display_as_percent, seen_keys, depth + 1)

        return None

    def _format_subpart(
        self,
        part: Any,
        display_as_percent: bool = False,
        seen_keys: set[str] | None = None,
        depth: int = 0,
    ) -> str | None:
        if depth > 20:
            return None
        if not isinstance(part, dict):
            return None

        part_type = str(part.get("__type") or "")
        if part_type == "SumOfSubPartsCalculationPart":
            subparts = [
                self._format_subpart(subpart, display_as_percent, seen_keys, depth + 1)
                for subpart in part.get("mSubparts") or []
            ]
            return " + ".join(subpart for subpart in subparts if subpart)

        if part_type == "ProductOfSubPartsCalculationPart":
            subparts = []
            for key in ("mPart1", "mPart2"):
                formatted = self._format_subpart(
                    part.get(key),
                    display_as_percent,
                    seen_keys,
                    depth + 1,
                )
                if formatted:
                    subparts.append(formatted)
            for subpart in part.get("mSubparts") or []:
                formatted = self._format_subpart(subpart, display_as_percent, seen_keys, depth + 1)
                if formatted:
                    subparts.append(formatted)
            numeric_product = product_of_numbers(subparts)
            if numeric_product is not None:
                return numeric_product
            return " x ".join(subparts)

        if part_type == "GameCalculationPart":
            key = string_or_none(part.get("mSpellCalculationKey"))
            return self.format_calculation(key, seen_keys, depth + 1) if key else None

        if part_type == "{f3cbe7b2}":
            key = string_or_none(part.get("mSpellCalculationKey"))
            return self.format_calculation(key, seen_keys, depth + 1) if key else None

        return self._format_formula_part(part, display_as_percent, seen_keys, depth + 1)


def format_breakpoint_part(part: dict[str, Any], display_as_percent: bool = False) -> str | None:
    base = number_or_none(part.get("mLevel1Value"))
    if base is None:
        return None

    formatter = format_display_percent if display_as_percent else format_number
    initial_bonus = number_or_none(part.get("mInitialBonusPerLevel"))
    breakpoints = [
        breakpoint
        for breakpoint in part.get("mBreakpoints") or []
        if isinstance(breakpoint, dict)
    ]
    level_values = values_by_level_from_breakpoints(base, initial_bonus, breakpoints)
    compact_levels = compact_level_values(level_values, formatter)
    if compact_levels:
        return compact_levels

    if initial_bonus is not None and initial_bonus != 0:
        operator = "-" if initial_bonus < 0 else "+"
        return f"{formatter(base)} {operator} {formatter(abs(initial_bonus))} per level"
    return formatter(base)


def product_of_numbers(subparts: list[str]) -> str | None:
    """Multiply numeric subparts together when every subpart is a plain number."""
    if not subparts:
        return None
    factors: list[float] = []
    for part in subparts:
        match = re.fullmatch(r"-?\d+(?:\.\d+)?", part)
        if not match:
            return None
        factors.append(float(part))
    result = 1.0
    for factor in factors:
        result *= factor
    return format_number(result)


def apply_multiplier_text(
    value: str,
    multiplier: str,
    display_as_percent: bool = False,
) -> str:
    if multiplier == "0.01":
        return value if display_as_percent else f"{value}%"
    if multiplier == "100":
        return f"{value}%"
    if multiplier == "1":
        return value
    return f"{value} x {multiplier}"


def values_by_level_from_breakpoints(
    base: float,
    initial_bonus: float | None,
    breakpoints: list[dict[str, Any]],
) -> list[float]:
    values = []
    current_value = base
    bonus_per_level = initial_bonus or 0.0
    breakpoint_lookup = {
        int(level): breakpoint
        for breakpoint in breakpoints
        if (level := number_or_none(breakpoint.get("mLevel"))) is not None
    }

    for level in range(1, 19):
        if level > 1:
            current_value += bonus_per_level

        breakpoint = breakpoint_lookup.get(level)
        if breakpoint is not None:
            additional = number_or_none(breakpoint.get("mAdditionalBonusAtThisLevel"))
            if additional is not None:
                current_value += additional

            bonus_at_and_after = number_or_none(breakpoint.get("mBonusPerLevelAtAndAfter"))
            if bonus_at_and_after is not None:
                bonus_per_level = bonus_at_and_after

        values.append(round(current_value, 6))

    return values


def compact_level_values(values: list[float], formatter) -> str | None:  # noqa: ANN001
    if len(set(values)) <= 1:
        return None

    segments = []
    segment_start = 1
    previous = values[0]
    for index, value in enumerate(values[1:], start=2):
        if value == previous:
            continue
        segments.append((segment_start, index - 1, previous))
        segment_start = index
        previous = value
    segments.append((segment_start, len(values), previous))

    if len(segments) > 8:
        return None
    return " / ".join(
        f"{formatter(value)} at {format_level_range(start, end)}"
        for start, end, value in segments
    )


def format_level_range(start: int, end: int) -> str:
    if start == end:
        return f"level {start}"
    return f"levels {start}-{end}"


def resolve_tooltip_placeholders(
    raw_text: str | None,
    spell_bin: SpellBinData,
    linked_bins: dict[str, SpellBinData] | None = None,
) -> str:
    if not raw_text:
        return ""

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(1)
        runtime_value = runtime_placeholder_value(
            placeholder,
            raw_text,
            match.start(),
            match.end(),
            spell_bin,
        )
        if runtime_value is not None:
            return runtime_value
        referenced_bin = referenced_spell_bin(placeholder, linked_bins or {})
        resolved = referenced_bin.resolve_placeholder(placeholder) if referenced_bin else None
        if resolved is None:
            resolved = spell_bin.resolve_placeholder(placeholder)
        if resolved:
            return resolved
        if should_drop_placeholder(placeholder):
            return ""
        return "?"

    return clean_resolved_text(PLACEHOLDER_PATTERN.sub(replace, raw_text))


def clean_resolved_text(text: str) -> str:
    text = text.replace("%i:scaleAH%", "")
    text = text.replace("+ -", "- ")
    text = text.replace(" + -", " - ")
    text = text.replace("AP% max Health", "AP max Health")
    text = text.replace("bonus Health Health", "bonus Health")
    text = text.replace("AD Attack Damage", "AD")
    text = text.replace(" x 0.01 max Health", " max Health")
    text = text.replace("% x 5 Health Regeneration", " Health Regeneration")
    text = text.replace(" + ?", "")
    text = text.replace(" ?%", " ?")
    text = re.sub(r"\s*\(%i:[^)]+\)", "", text)
    text = re.sub(r"<([A-Za-z][A-Za-z0-9]*)[^>]*>\s*</\1>", "?", text)
    return text


def should_drop_placeholder(placeholder: str) -> bool:
    normalized = normalize_placeholder_name(placeholder).lower()
    return normalized in {
        "spellmodifierdescriptionappend",
        "spellmodifierdescriptionprepend",
        "spellmodifierdescriptionappendtext",
        "f1",
        "f2",
        "f2.1",
        "f3",
        "f3.1",
        "f4",
        "f4.1",
    }


def referenced_spell_bin(
    placeholder: str,
    linked_bins: dict[str, SpellBinData],
) -> SpellBinData | None:
    normalized = normalize_placeholder_name(placeholder).split("*", 1)[0]
    if ":" not in normalized:
        return None
    prefix = normalized.split(":", 1)[0].lower()
    return linked_bins.get(prefix)


def unresolved_placeholders(raw_text: str | None) -> list[str]:
    if not raw_text:
        return []
    return PLACEHOLDER_PATTERN.findall(raw_text)


def normalize_placeholder_name(value: str) -> str:
    value = value.replace("@", "")
    return re.sub(r"^spell\.", "", value, flags=re.IGNORECASE)


def number_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    return [float(item) for item in value if isinstance(item, int | float)]


def number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def runtime_placeholder_value(
    placeholder: str,
    raw_text: str,
    start: int,
    end: int,
    spell_bin: SpellBinData,
) -> str | None:
    key = normalize_placeholder_name(placeholder)
    match = re.fullmatch(r"f(\d+)(?:\.\d+)?", key, flags=re.IGNORECASE)
    if not match:
        return None
    context = raw_text[max(0, start - 80) : min(len(raw_text), end + 80)].lower()
    data_key = runtime_context_data_key(context, spell_bin.data_values)
    if data_key is None:
        return None
    values = spell_bin.data_values[data_key]
    multiplier = 100 if percent_like_data_value(data_key, values) else 1
    return format_series([value * multiplier for value in values], spell_bin.rank_count)


def runtime_context_data_key(context: str, data_values: dict[str, list[float]]) -> str | None:
    normalized_values = {key.lower(): key for key in data_values}
    if "attackspeed" in context or "attack speed" in context or "scaleas" in context:
        for candidate in ("asmod", "attackspeed", "attackspeedmod"):
            if candidate in normalized_values:
                return normalized_values[candidate]
    return None


def percent_like_data_value(key: str, values: list[float]) -> bool:
    lowered = key.lower()
    return (
        "ratio" in lowered
        or "mod" in lowered
        or "percent" in lowered
        or "amount" in lowered and values and max(abs(value) for value in values) <= 1
    )


def effect_amounts_from_spell(spell: dict[str, Any]) -> dict[str, list[float]]:
    raw_effects = spell.get("mEffectAmount")
    if not isinstance(raw_effects, list):
        return {}
    amounts = {}
    for index, raw_effect in enumerate(raw_effects, start=1):
        if not isinstance(raw_effect, dict):
            continue
        values = number_list(raw_effect.get("value"))
        if values:
            amounts[str(index)] = values
    return amounts


def lookup_key(mapping: dict[str, Any], key: str) -> str | None:
    if key in mapping:
        return key
    normalized = key.lower()
    folded = normalized.replace("tooltip", "")
    for candidate in mapping:
        if candidate.lower().replace("tooltip", "") == folded:
            return candidate
    return next((candidate for candidate in mapping if candidate.lower() == normalized), None)


def tooltip_data_from_spell(spell: dict[str, Any]) -> dict[str, Any] | None:
    client_data = spell.get("mClientData")
    if isinstance(client_data, dict) and isinstance(client_data.get("mTooltipData"), dict):
        return client_data["mTooltipData"]
    if isinstance(spell.get("mTooltipData"), dict):
        return spell["mTooltipData"]
    return None


def normalize_bin_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def loc_key_matches_passive(loc_keys: dict[str, Any], normalized_alias: str) -> bool:
    normalized_values = [
        normalize_bin_key(value)
        for value in loc_keys.values()
        if isinstance(value, str)
    ]
    return any(
        value in {
            f"spell{normalized_alias}pname",
            f"spell{normalized_alias}ptooltip",
            f"spell{normalized_alias}ptooltipextended",
            f"spell{normalized_alias}psummary",
            f"spell{normalized_alias}passivename",
            f"spell{normalized_alias}passivetooltip",
            f"spell{normalized_alias}passivetooltipextended",
            f"spell{normalized_alias}passivesummary",
            f"gamecharacterpassivename{normalized_alias}",
            f"gamecharacterpassivetooltip{normalized_alias}",
            f"gamecharacterpassivedescription{normalized_alias}",
        }
        or value.startswith("generatedtippassive")
        or value.startswith("buff") and "passive" in value
        for value in normalized_values
    )


def passive_folder_segments(normalized_alias: str = "") -> set[str]:
    segments = {"pability"}
    if normalized_alias:
        segments.add(f"{normalized_alias}pability")
    return segments


def segment_matches_spell_name(segment: str, spell_names: set[str]) -> bool:
    for spell_name in spell_names:
        if segment in {
            spell_name,
            f"{spell_name}ability",
            f"{spell_name}wrapperability",
        }:
            return True
        if segment.endswith(f"{spell_name}ability") or segment.endswith(
            f"{spell_name}wrapperability"
        ):
            return True
    return False


def folder_matches_slot(
    key: str,
    slot: str,
    normalized_alias: str = "",
    spell_names: set[str] | None = None,
) -> bool:
    lowered = key.lower()
    segments = lowered.split("/")
    normalized_segments = [normalize_bin_key(segment) for segment in segments]
    if any(
        segment_matches_spell_name(segment, spell_names or set())
        for segment in normalized_segments
    ):
        return True
    if slot == "P":
        return (
            any(segment.endswith("passiveability") for segment in segments)
            or any(
                segment in passive_folder_segments(normalized_alias)
                for segment in normalized_segments
            )
            or "hemo" in lowered
        )
    slot_lower = slot.lower()
    expected_segments = {f"{slot_lower}ability", f"{slot_lower}wrapperability"}
    if normalized_alias:
        expected_segments.update(
            {
                f"{normalized_alias}{slot_lower}ability",
                f"{normalized_alias}{slot_lower}wrapperability",
            }
        )
    return any(segment in expected_segments for segment in normalized_segments)


def rank_count_from_tooltip_data(tooltip_data: dict[str, Any] | None) -> int | None:
    if not isinstance(tooltip_data, dict):
        return None
    lists = tooltip_data.get("mLists")
    if not isinstance(lists, dict):
        return None
    for key, value in lists.items():
        if not isinstance(key, str) or key.lower() != "levelup":
            continue
        if not isinstance(value, dict):
            continue
        rank_count = value.get("levelCount")
        if isinstance(rank_count, int | float) and rank_count > 0:
            return int(rank_count)
    return None


def string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def stat_name(stat_id: Any, formula_id: Any = None) -> str:
    if not isinstance(stat_id, int | float):
        return "AP"
    prefix = ""
    if isinstance(formula_id, int | float):
        prefix = STAT_FORMULA_PREFIX.get(int(formula_id), "")
    return f"{prefix}{STAT_NAMES.get(int(stat_id), 'stat')}"


def friendly_name(value: str) -> str:
    value = re.sub(r"^Calc_?", "", value)
    value = value.replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return value.strip()


def calculation_display_name(
    key: str,
    calculation: dict[str, Any],
    calculations: dict[str, Any] | None = None,
) -> str:
    if not HASH_KEY_PATTERN.fullmatch(key):
        return friendly_name(key)

    names = calculation_component_names(calculation, calculations)
    if names:
        return " / ".join(names[:4])
    return "Internal Calculation"


def calculation_component_names(
    value: Any,
    calculations: dict[str, Any] | None = None,
    seen_keys: set[str] | None = None,
) -> list[str]:
    names: list[str] = []
    seen_keys = seen_keys or set()

    def add_name(raw_name: Any) -> None:
        if not isinstance(raw_name, str):
            return
        if HASH_KEY_PATTERN.fullmatch(raw_name):
            if calculations and raw_name not in seen_keys:
                referenced = calculations.get(raw_name)
                if isinstance(referenced, dict):
                    seen_keys.add(raw_name)
                    collect(referenced)
            return
        name = friendly_name(raw_name)
        if name.lower().replace(" ", "") in {"pi", "inversepi"}:
            return
        if name and name not in names:
            names.append(name)

    def collect(raw_value: Any) -> None:
        if isinstance(raw_value, list):
            for item in raw_value:
                collect(item)
            return
        if not isinstance(raw_value, dict):
            return

        names_before = len(names)
        add_name(raw_value.get("mDataValue"))
        add_name(raw_value.get("mSpellCalculationKey"))
        add_name(raw_value.get("mModifiedGameCalculation"))
        add_name(raw_value.get("mDefaultGameCalculation"))
        add_name(raw_value.get("mConditionalGameCalculation"))
        for child_key in (
            "mFormulaParts",
            "mMultiplier",
            "mPart1",
            "mPart2",
            "mSubparts",
            "mSubpart",
        ):
            collect(raw_value.get(child_key))
        if len(names) == names_before:
            add_formula_component_name(raw_value)

    def add_formula_component_name(raw_value: dict[str, Any]) -> None:
        part_type = str(raw_value.get("__type") or "")
        if part_type in {
            "StatByCoefficientCalculationPart",
            "StatByNamedDataValueCalculationPart",
            "StatBySubPartCalculationPart",
        }:
            stat = stat_name(raw_value.get("mStat"), raw_value.get("mStatFormula"))
            add_component_name(f"{stat} Scaling")
        elif part_type in {
            "ByCharLevelBreakpointsCalculationPart",
            "ByCharLevelInterpolationCalculationPart",
            "ByCharLevelFormulaCalculationPart",
        }:
            add_component_name("Level Scaling")
        elif part_type in {
            "BuffCounterByCoefficientCalculationPart",
            "BuffCounterByNamedDataValueCalculationPart",
        }:
            add_component_name("Stack Scaling")
        elif part_type == "EffectValueCalculationPart":
            add_component_name("Effect Value")
        elif part_type == "NumberCalculationPart":
            add_component_name("Flat Value")

    def add_component_name(name: str) -> None:
        if name and name not in names:
            names.append(name)

    collect(value)
    return names


def should_skip_data_value(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"movementspeed", "attackspeed"}:
        return False
    return any(
        marker in lowered
        for marker in (
            "duration",
            "radius",
            "range",
            "delay",
            "width",
            "height",
            "angle",
            "speed",
        )
    )


def format_series(values: list[float], rank_count: int | None = None) -> str:
    trimmed = trim_spell_values(values, rank_count)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_number(trimmed[0])
    return "/".join(format_number(value) for value in trimmed)


def format_percent_series(values: list[float], rank_count: int | None = None) -> str:
    trimmed = trim_spell_values(values, rank_count)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_percent(trimmed[0])
    return "/".join(format_percent(value) for value in trimmed)


def format_display_percent_series(
    values: list[float],
    rank_count: int | None = None,
) -> str:
    trimmed = trim_spell_values(values, rank_count)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_display_percent(trimmed[0])
    return "/".join(format_display_percent(value) for value in trimmed)


def trim_spell_values(values: list[float], rank_count: int | None = None) -> list[float]:
    if rank_count is not None and rank_count > 0:
        if len(values) >= 7 or len(values) > rank_count and values[0] == 0:
            return values[1 : rank_count + 1]
        return values[:rank_count]
    if len(values) > 6:
        values = values[1:6]
    elif len(values) == 6:
        values = values[:5]
    return values


def format_level_formula_values(values: list[float], formatter) -> str:  # noqa: ANN001
    level_values = values[1:19] if len(values) > 18 and values[0] == 0 else values[:18]
    if not level_values:
        return ""
    if len(set(level_values)) == 1:
        return formatter(level_values[0])
    if len(set(level_values)) > 8:
        return f"{formatter(level_values[0])}-{formatter(level_values[-1])} by level"
    compact = compact_level_values(level_values, formatter)
    return compact or f"{formatter(level_values[0])}-{formatter(level_values[-1])} by level"


def format_percent(value: float) -> str:
    return f"{format_number(value * 100)}%"


def format_display_percent(value: float) -> str:
    if abs(value) > 1:
        return f"{format_number(value)}%"
    return format_percent(value)


def format_number(value: float) -> str:
    rounded = round(value, 4)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:g}"
