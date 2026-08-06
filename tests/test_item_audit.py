from league_cast_assist.data.static_data import (
    best_item_description,
    best_item_description_candidate,
    effect_text_has_nonzero_number,
    item_description_candidates,
    item_description_score,
    resolve_item_templates,
    resolve_item_text,
)
from league_cast_assist.tools.audit_item_text import (
    has_multiple_visible_tokens,
    has_passive_or_active,
    leftover_placeholders,
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
    best = best_item_description_candidate(candidates)

    assert best is not None
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


def test_scoring_prefers_resolved_numbers_over_zero_filled_legacy_text() -> None:
    raw_item = {
        "id": 3742,
        "description": (
            "<mainText><stats><attention>350</attention> Health<br>"
            "<attention>55</attention> Armor</stats><br><br>"
            "<passive>Shipwrecker</passive><br>While moving, build up to "
            "<speed>20 bonus Move Speed</speed>.</mainText>"
        ),
    }
    entries = {
        "generatedtip_item_3742_description": (
            "<section><attention>@FlatHPPoolMod@</attention> Health<br>"
            "<attention>@FlatArmorMod@</attention> Armor</section>"
            "<section><passive>Shipwrecker</passive><br>While moving, build up to "
            "<speed>@MaxMovementSpeed@ bonus Move Speed</speed>. Your next Attack "
            "discharges built up Move Speed to deal up to "
            "<physicalDamage>@MaxDamageCalc@ bonus physical damage</physicalDamage>.</section>"
        ),
        "game_item_description_3742": (
            "<stats>+@FlatHPPoolMod@ Health<br>+@FlatArmorMod@ Armor</stats><br><br>"
            "<unique>UNIQUE Passive - Dreadnought:</unique> While moving, build stacks "
            "of Momentum, increasing Move Speed by up to @Effect1Amount@ at "
            "@Effect2Amount@ stacks.<br><unique>UNIQUE Passive - Crushing Blow:</unique> "
            "Basic attacks deal @Effect3Amount@ magic damage per stack of Momentum. "
            "At max stacks, they also slow the target by @Effect4Amount*-100@% for "
            "@Effect5Amount@ second."
        ),
    }
    item_bin = {
        "mFlatHPPoolMod": 350.0,
        "mFlatArmorMod": 55.0,
        "mEffectAmount": [0.0, 0.0, 0.0, 0.0, 0.0],
        "mDataValues": [
            {"mName": "MaxMovementSpeed", "mValue": 20.0},
            {"mName": "MaxStacks", "mValue": 100.0},
            {"mName": "BonusDamagePerStack", "mValue": 0.4},
            {"mName": "MaxStacksADRatio", "mValue": 1.0},
        ],
        "mItemCalculations": {
            "MaxDamageCalc": {
                "mFormulaParts": [
                    {
                        "mStat": 2,
                        "mStatFormula": 1,
                        "mDataValue": "MaxStacksADRatio",
                        "__type": "StatByNamedDataValueCalculationPart",
                    },
                    {
                        "mPart1": {
                            "mDataValue": "BonusDamagePerStack",
                            "__type": "NamedDataValueCalculationPart",
                        },
                        "mPart2": {"mNumber": 100.0, "__type": "NumberCalculationPart"},
                        "__type": "ProductOfSubPartsCalculationPart",
                    },
                ],
                "__type": "GameCalculation",
            }
        },
    }

    description = best_item_description(raw_item, entries, item_bin)

    assert description is not None
    assert "100% AD + 40" in description
    assert "up to 0 at 0 stacks" not in description
    assert "deal 0 magic damage" not in description


def test_scoring_prefers_generated_numbers_over_raw_generic_text() -> None:
    raw_item = {
        "id": 3075,
        "description": (
            "<mainText><stats><attention>150</attention> Health<br>"
            "<attention>75</attention> Armor</stats><br><br><passive>Thorns</passive><br>"
            "When struck by an Attack, deal <magicDamage>magic damage</magicDamage> to "
            "the attacker and apply 40% <keyword>Wounds</keyword> for 3 seconds if they "
            "are a champion.</mainText>"
        ),
    }
    entries = {
        "generatedtip_item_3075_description": (
            "<section><attention>@FlatHPPoolMod@</attention> Health<br>"
            "<attention>@FlatArmorMod@</attention> Armor</section>"
            "<section><passive>Thorns</passive><br>When struck by an Attack, deal "
            "<magicDamage>@TotalDamage@ magic damage</magicDamage> to the attacker and "
            "apply <keyword>@GrievousAmount*100@% Wounds</keyword> for "
            "@GrievousDuration@ seconds if they are a champion.</section>"
        )
    }
    item_bin = {
        "mFlatHPPoolMod": 150.0,
        "mFlatArmorMod": 75.0,
        "mDataValues": [
            {"mName": "BaseDamage", "mValue": 20.0},
            {"mName": "BonusArmorDamageRatio", "mValue": 0.1},
            {"mName": "GrievousAmount", "mValue": 0.4},
            {"mName": "GrievousDuration", "mValue": 3.0},
        ],
        "mItemCalculations": {
            "TotalDamage": {
                "mFormulaParts": [
                    {"mDataValue": "BaseDamage", "__type": "NamedDataValueCalculationPart"},
                    {
                        "mStat": 1,
                        "mStatFormula": 2,
                        "mDataValue": "BonusArmorDamageRatio",
                        "__type": "StatByNamedDataValueCalculationPart",
                    },
                ],
                "__type": "GameCalculation",
            }
        },
    }

    description = best_item_description(raw_item, entries, item_bin)

    assert description is not None
    assert "20 + 10% bonus Armor magic damage" in description
    assert "deal <magicDamage>magic damage</magicDamage>" not in description


def test_melee_ranged_split_placeholder_resolves_in_item_text() -> None:
    raw_text = (
        "<passive>Firmament</passive><br>Your <keyword>Energized Attack</keyword> "
        "deals <physicalDamage>@PercentHPMeleeRangedSplit@%</physicalDamage> target's "
        "Current Health as <physicalDamage>bonus physical damage</physicalDamage> and "
        "grants you <scaleLethality>@LethalityBonusModMeleeRangedSplit@ "
        "Lethality</scaleLethality> for @LethalityBonusDuration@ seconds."
    )
    item_bin = {
        "mDataValues": [
            {"mName": "PercentCurrentHPMelee", "mValue": 9.0},
            {"mName": "PercentCurrentHPRanged", "mValue": 7.0},
            {"mName": "LethalityBonusModMelee", "mValue": 15.0},
            {"mName": "LethalityBonusModRanged", "mValue": 12.0},
            {"mName": "LethalityBonusDuration", "mValue": 4.0},
        ]
    }

    resolved = resolve_item_text(raw_text, item_bin)

    assert "9% Melee / 7% Ranged" in resolved
    assert "15 Melee / 12 Ranged Lethality" in resolved
    assert "@PercentHPMeleeRangedSplit@" not in resolved
    assert "@LethalityBonusModMeleeRangedSplit@" not in resolved
    assert "% Ranged%" not in resolved


def test_leftover_placeholders_detect_unresolved_markup() -> None:
    assert leftover_placeholders("<passive>Gain @AuraAttackSpeed@.</passive>") == [
        "@AuraAttackSpeed@"
    ]
    assert leftover_placeholders("Gain {{ Item_Unknown }}.") == ["{{ Item_Unknown }}"]
    assert leftover_placeholders("Gain 10% Attack Speed.") == []


def test_effect_text_has_nonzero_number_detects_missing_numbers() -> None:
    assert effect_text_has_nonzero_number(
        "<passive>Thorns</passive><br>deal <magicDamage>20 magic damage</magicDamage>"
    )
    assert not effect_text_has_nonzero_number(
        "<passive>Thorns</passive><br>deal <magicDamage>magic damage</magicDamage>"
    )
    assert not effect_text_has_nonzero_number(
        "<passive>Dreadnought</passive><br>up to 0 at 0 stacks"
    )
    assert not effect_text_has_nonzero_number("<stats>10 Attack Damage</stats>")
    assert not effect_text_has_nonzero_number(
        "<passive>Gouge</passive><br>Gain @Effect1Amount@ Lethality"
    )


def test_runtime_placeholders_resolve_to_question_mark() -> None:
    assert "Gain ? Ability Haste" in resolve_item_text(
        "<passive>Famine</passive><br>Gain @HasteFromAD@ Ability Haste.",
        {"mDataValues": [{"mName": "PercentTenacityItemMod", "mValue": 0.2}]},
    )
    assert "store ?% of" in resolve_item_text(
        "<passive>Thirst</passive><br>store @SelfHealAmount*100@% of your "
        "self-healing as Gore.",
        {"mDataValues": [{"mName": "PercentOmnivampMod", "mValue": 0.08}]},
    )
    assert "lasts ? seconds" in resolve_item_text(
        "<passive>Totem</passive><br>Places a Stealth Ward that lasts "
        "@Effect4Amount@ seconds (@Effect5Amount@ Second cooldown).",
        {},
    )


def test_effect_index_placeholder_resolves_from_tooltip_context() -> None:
    resolved = resolve_item_text(
        "<passive>Gouge</passive><br>Gain <scaleLethality>@Effect1Amount@ "
        "Lethality</scaleLethality>.",
        {"mDataValues": [{"mName": "LethalityAmount", "mValue": 10.0}]},
    )
    assert "10 Lethality" in resolved
    assert "@Effect1Amount@" not in resolved


def test_cooldown_placeholder_resolves_from_effect_amounts() -> None:
    resolved = resolve_item_text(
        "<active>Quicksilver:</active> Remove crowd control "
        "(@Effect3Amount@ Cooldown).",
        {"mEffectAmount": [0.5, 1.0, 90.0]},
    )
    assert "90 Cooldown" in resolved
    assert "@Effect3Amount@" not in resolved


def test_cooldown_placeholder_prefers_real_cooldown_over_cdr_key() -> None:
    resolved = resolve_item_text(
        "<active>Active:</active> Fire (@Cooldown@ second Cooldown).",
        {
            "mDataValues": [
                {"mName": "CooldownTime", "mValue": 90.0},
                {"mName": "CooldownReduction", "mValue": 15.0},
            ]
        },
    )
    assert "90 second Cooldown" in resolved
    assert "15" not in resolved


def test_cooldown_placeholder_ignores_multiplier_mod_keys() -> None:
    resolved = resolve_item_text(
        "<active>Active:</active> Barrage (@Cooldown@ second Cooldown).",
        {
            "mDataValues": [
                {"mName": "CooldownTime", "mValue": 12.0},
                {"mName": "CooldownTimeMultiplier", "mValue": 25.0},
            ]
        },
    )
    assert "12 second Cooldown" in resolved
    assert "25" not in resolved


def test_cooldown_placeholder_effect_fallback_bounds_magnitude() -> None:
    resolved = resolve_item_text(
        "<active>Active:</active> Revive (@Cooldown@ second Cooldown).",
        {"mEffectAmount": [0.5, 1.0, 240.0]},
    )
    assert "240 second Cooldown" in resolved


def test_effect_index_placeholder_resolves_magic_resist_context() -> None:
    resolved = resolve_item_text(
        "<passive>Bulwark</passive><br>Gain "
        "<scaleMR>@Effect1Amount@ Magic Resist</scaleMR>.",
        {"mDataValues": [{"mName": "FlatMagicResist", "mValue": 15.0}]},
    )
    assert "15 Magic Resist" in resolved
    assert "@Effect1Amount@" not in resolved
