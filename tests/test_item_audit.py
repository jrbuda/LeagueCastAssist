from league_cast_assist.data.static_data import (
    best_item_description,
    item_description_candidates,
    item_description_score,
    resolve_item_templates,
)
from league_cast_assist.tools.audit_item_text import (
    has_multiple_visible_tokens,
    has_passive_or_active,
    strip_markup,
)


def test_item_description_score_prefers_current_passive_text() -> None:
    stats_only = "<mainText><stats><attention>75</attention> Attack Damage</stats></mainText>"
    with_passive = (
        "<mainText><stats><attention>75</attention> Attack Damage</stats>"
        "<passive>Death</passive><br>Executes low-health enemies.</mainText>"
    )

    assert item_description_score(with_passive) > item_description_score(stats_only)
    assert has_passive_or_active(with_passive)
    assert not has_passive_or_active(stats_only)
    assert has_multiple_visible_tokens("80 Ability Power")
    assert not has_multiple_visible_tokens("Boots")
    assert strip_markup(with_passive) == "75 Attack Damage Death Executes low-health enemies."


def test_item_template_resolution_preserves_range_split_template() -> None:
    entries = {
        "generatedtip_item_4633_description": (
            "<mainText><passive>Void Corruption</passive><br>Gain "
            "{{ Item_Melee_Ranged_Split_Dynamic }} Omnivamp.</mainText>"
        ),
        "item_melee_ranged_split_dynamic": "This fallback should not erase the split.",
    }

    result = resolve_item_templates(entries["generatedtip_item_4633_description"], entries)

    assert "@MeleeItemCalcValue@ Melee / @RangedItemCalcValue@ Ranged" in result
    assert "fallback" not in result


def test_riftmaker_range_split_resolves_from_item_bin_values() -> None:
    raw_item = {
        "id": 4633,
        "description": (
            "<mainText><stats><attention>70</attention> Ability Power</stats></mainText>"
        ),
    }
    entries = {
        "generatedtip_item_4633_description": (
            "<mainText><stats><attention>@FlatMagicDamageMod@</attention> Ability Power</stats>"
            "<br><passive>Void Corruption</passive><br>Gain "
            "{{ Item_Melee_Ranged_Split_Dynamic }} Omnivamp.</mainText>"
        )
    }
    item_bin = {
        "mFlatMagicDamageMod": 70.0,
        "VampAmountMelee": 0.10000000149011612,
        "VampAmountRanged": 0.05999999865889549,
        "mItemCalculations": {
            "MeleeItemCalcValue": {
                "mDisplayAsPercent": True,
                "mFormulaParts": [
                    {
                        "mDataValue": "VampAmountMelee",
                        "__type": "NamedDataValueCalculationPart",
                    }
                ],
            },
            "RangedItemCalcValue": {
                "mDisplayAsPercent": True,
                "mFormulaParts": [
                    {
                        "mDataValue": "VampAmountRanged",
                        "__type": "NamedDataValueCalculationPart",
                    }
                ],
            },
        },
    }

    candidates = item_description_candidates(raw_item, entries, item_bin)
    best = max(candidates, key=lambda candidate: item_description_score(candidate.text))

    assert best.source_key == "generatedtip_item_4633_description"
    assert "<attention>70</attention> Ability Power" in best.text
    assert "10% Melee / 6% Ranged Omnivamp" in best.text
    assert "@FlatMagicDamageMod@" not in best.text
    assert "@MeleeItemCalcValue@" not in best.text
    assert "{{ Item_Melee_Ranged_Split_Dynamic }}" not in best.text


def test_item_description_prefers_resolved_effect_text_over_generic_text() -> None:
    raw_item = {
        "id": 3871,
        "description": (
            "<mainText><stats><attention>200</attention> Health</stats><br>"
            "<passive>Void Explosion</passive><br>Deals "
            "<magicDamage>magic damage</magicDamage>.</mainText>"
        ),
    }
    entries = {
        "generatedtip_item_3871_description": (
            "<mainText><stats><attention>@mFlatHPPoolMod@</attention> Health</stats>"
            "<section><passive>Void Explosion</passive><br>Deals "
            "<magicDamage>@TooltipDamage@ magic damage</magicDamage>.</section></mainText>"
        )
    }
    item_bin = {
        "mFlatHPPoolMod": 200.0,
        "mDataValues": [
            {"mName": "BaseDamage", "mValue": 10.0},
            {"mName": "APRatio", "mValue": 0.15},
        ],
        "mItemCalculations": {
            "TooltipDamage": {
                "mFormulaParts": [
                    {"mDataValue": "BaseDamage", "__type": "NamedDataValueCalculationPart"},
                    {"mDataValue": "APRatio", "__type": "StatByNamedDataValueCalculationPart"},
                ],
            }
        },
    }

    description = best_item_description(raw_item, entries, item_bin)

    assert description is not None
    assert "10 + 15% AP magic damage" in description
    assert "Deals <magicDamage>magic damage</magicDamage>" not in description


def test_item_description_keeps_stats_ahead_of_separate_effect_text() -> None:
    raw_item = {
        "id": 1055,
        "description": (
            "<mainText><stats><attention>10</attention> Attack Damage<br>"
            "<attention>80</attention> Health</stats></mainText>"
        ),
    }
    entries = {
        "item_1055_tooltipexternal": (
            "<passive>Life Draining</passive><br>Return @HealMultiplier*100@% "
            "of damage dealt as <healing>Health</healing>."
        )
    }
    item_bin = {"HealMultiplier": 0.025}

    description = best_item_description(raw_item, entries, item_bin)

    assert description is not None
    assert "<stats>" in description
    assert "Attack Damage" in description
    assert "Life Draining" in description
    assert "2.5% of damage" in description
