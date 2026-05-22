from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER_PATTERN = re.compile(r"@([A-Za-z0-9_:.+*\-/]+)@")

STAT_NAMES = {
    1: "AP",
    2: "AD",
    3: "Attack Speed",
    5: "Move Speed",
    6: "Armor",
    7: "Magic Resist",
    8: "Critical Strike Chance",
    9: "Critical Strike Damage",
    12: "Health",
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

    @classmethod
    def from_raw(cls, raw_spell: dict[str, Any] | None) -> SpellBinData:
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
        effect_value = effect_amount_value(key, self.effect_amounts)
        if effect_value is not None:
            return effect_value
        calculation_key = lookup_key(self.calculations, key)
        if calculation_key:
            return self.format_calculation(calculation_key)
        data_key = lookup_key(self.data_values, key)
        if data_key:
            return format_series([value * multiplier for value in self.data_values[data_key]])
        if key.lower() == "cooldown" and self.cooldown:
            return format_series(self.cooldown)
        if key.lower() == "cost" and self.cost:
            return format_series(self.cost)
        if key.lower() == "range" and self.range:
            return format_series(self.range)
        return None

    def format_calculation(self, key: str) -> str | None:
        calculation = self.calculations.get(key)
        if not isinstance(calculation, dict):
            return None

        if calculation.get("__type") == "GameCalculationModified":
            return self.format_modified_calculation(calculation)

        display_as_percent = calculation.get("mDisplayAsPercent") is True
        multiplier = self._format_subpart(calculation.get("mMultiplier"), display_as_percent)
        parts = []
        for part in calculation.get("mFormulaParts") or []:
            formatted = self._format_formula_part(part, display_as_percent)
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

    def format_modified_calculation(self, calculation: dict[str, Any]) -> str | None:
        base_key = string_or_none(calculation.get("mModifiedGameCalculation"))
        if not base_key:
            return None
        base = self.format_calculation(base_key)
        multiplier = self._format_subpart(
            calculation.get("mMultiplier"),
            calculation.get("mDisplayAsPercent") is True,
        )
        if base and multiplier:
            return apply_multiplier_text(
                base,
                multiplier,
                display_as_percent=calculation.get("mDisplayAsPercent") is True,
            )
        return base

    def stat_lines(self) -> list[str]:
        lines = []
        for key, calculation in self.calculations.items():
            if not isinstance(calculation, dict):
                continue
            formatted = self.format_calculation(key)
            if formatted:
                lines.append(f"{friendly_name(key)}: {formatted}")

        if self.cooldown:
            lines.append(f"Cooldown: {format_series(self.cooldown)}s")
        if self.cost:
            lines.append(f"Cost: {format_series(self.cost)}")
        if self.range and any(value > 0 for value in self.range):
            lines.append(f"Range: {format_series(self.range)}")
        return lines

    def data_value_lines(self) -> list[str]:
        lines = []
        for key, values in self.data_values.items():
            if not values or should_skip_data_value(key):
                continue
            formatted = format_series(values)
            if formatted:
                lines.append(f"{friendly_name(key)}: {formatted}")
        return lines

    def _format_formula_part(self, part: Any, display_as_percent: bool = False) -> str | None:
        if not isinstance(part, dict):
            return None

        part_type = str(part.get("__type") or "")
        data_value = part.get("mDataValue")
        if part_type == "NamedDataValueCalculationPart" and isinstance(data_value, str):
            values = self.data_values.get(data_value, [])
            if display_as_percent:
                return format_display_percent_series(values)
            return format_series(values)

        if part_type == "EffectValueCalculationPart":
            effect_index = number_or_none(part.get("mEffectIndex"))
            if effect_index is None:
                return None
            values = self.effect_amounts.get(str(int(effect_index)), [])
            if display_as_percent:
                return format_display_percent_series(values)
            return format_series(values)

        if part_type == "BuffCounterByNamedDataValueCalculationPart" and isinstance(
            data_value, str
        ):
            values = self.data_values.get(data_value, [])
            if not values:
                return "per stack"
            formatted = (
                format_display_percent_series(values)
                if display_as_percent
                else format_series(values)
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
            stat = stat_name(part.get("mStat", 1), part.get("mStatFormula"))
            values = self.data_values.get(data_value, [])
            if values:
                return f"{format_percent_series(values)} {stat}"

        if part_type == "StatByCoefficientCalculationPart":
            stat = stat_name(part.get("mStat", 1), part.get("mStatFormula"))
            coefficient = number_or_none(part.get("mCoefficient"))
            if coefficient is not None:
                return f"{format_percent(coefficient)} {stat}"

        if part_type == "StatBySubPartCalculationPart":
            stat = stat_name(part.get("mStat", 1), part.get("mStatFormula"))
            subpart = self._format_subpart(part.get("mSubpart"), display_as_percent)
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
            return self._format_subpart(part, display_as_percent)

        return None

    def _format_subpart(self, part: Any, display_as_percent: bool = False) -> str | None:
        if not isinstance(part, dict):
            return None

        part_type = str(part.get("__type") or "")
        if part_type == "SumOfSubPartsCalculationPart":
            subparts = [
                self._format_subpart(subpart, display_as_percent)
                for subpart in part.get("mSubparts") or []
            ]
            return " + ".join(subpart for subpart in subparts if subpart)

        if part_type == "ProductOfSubPartsCalculationPart":
            subparts = []
            for key in ("mPart1", "mPart2"):
                formatted = self._format_subpart(part.get(key), display_as_percent)
                if formatted:
                    subparts.append(formatted)
            for subpart in part.get("mSubparts") or []:
                formatted = self._format_subpart(subpart, display_as_percent)
                if formatted:
                    subparts.append(formatted)
            return " x ".join(subparts)

        if part_type == "GameCalculationPart":
            key = string_or_none(part.get("mSpellCalculationKey"))
            return self.format_calculation(key) if key else None

        if part_type == "{f3cbe7b2}":
            key = string_or_none(part.get("mSpellCalculationKey"))
            return self.format_calculation(key) if key else None

        return self._format_formula_part(part, display_as_percent)


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
        "f3",
        "f4",
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


def effect_amount_value(key: str, effect_amounts: dict[str, list[float]]) -> str | None:
    match = re.fullmatch(r"Effect(\d+)Amount", key, flags=re.IGNORECASE)
    if not match:
        return None
    values = effect_amounts.get(match.group(1))
    return format_series(values) if values else None


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


def string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def stat_name(stat_id: Any, formula_id: Any = None) -> str:
    if not isinstance(stat_id, int | float):
        return "stat"
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


def format_series(values: list[float]) -> str:
    trimmed = trim_spell_values(values)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_number(trimmed[0])
    return "/".join(format_number(value) for value in trimmed)


def format_percent_series(values: list[float]) -> str:
    trimmed = trim_spell_values(values)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_percent(trimmed[0])
    return "/".join(format_percent(value) for value in trimmed)


def format_display_percent_series(values: list[float]) -> str:
    trimmed = trim_spell_values(values)
    if not trimmed:
        return ""
    if len(set(trimmed)) == 1:
        return format_display_percent(trimmed[0])
    return "/".join(format_display_percent(value) for value in trimmed)


def trim_spell_values(values: list[float]) -> list[float]:
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
