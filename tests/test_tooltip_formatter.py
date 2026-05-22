from league_cast_assist.data.ability_math import SpellBinData, resolve_tooltip_placeholders
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.match_state import (
    MatchStateReducer,
    ability_rank_count,
    loc_key_matches_slot,
    spell_candidate_score,
)
from league_cast_assist.data.static_data import (
    ChampionData,
    is_summoners_rift_item,
    slot_from_bin_key,
)
from league_cast_assist.data.tooltip_formatter import TooltipFormatter


def test_tooltip_formatter_converts_known_tags() -> None:
    formatter = TooltipFormatter()

    result = formatter.to_rich_text(
        "Deals <magicDamage>100 magic damage</magicDamage>.<br>@RawValue@"
    )

    assert "magic damage" in result
    assert "span" in result
    assert "@RawValue@" not in result
    assert "<br>" in result


def test_item_effect_heading_after_stats_starts_new_line() -> None:
    formatter = TooltipFormatter()

    result = formatter.to_rich_text(
        "<mainText><stats><attention>40</attention> Attack Damage<br>"
        "<attention>600</attention> Health</stats><passive>Cleave</passive><br>"
        "Attacks deal bonus damage.</mainText>"
    )

    assert "Health<br><span" in result
    assert "Health<span" not in result
    assert "Cleave" in result


def test_yorick_style_placeholders_resolve_scaling_and_linked_passive() -> None:
    q_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "DataValues": [
                    {"name": "BaseDamage", "values": [10, 30, 50, 70, 90, 110, 130]},
                    {"name": "BonusDamageAD", "values": [0.5, 0.5, 0.5, 0.5, 0.5]},
                ],
                "mSpellCalculations": {
                    "BonusDamage": {
                        "mFormulaParts": [
                            {"mDataValue": "BaseDamage", "__type": "NamedDataValueCalculationPart"},
                            {
                                "mStat": 2,
                                "mDataValue": "BonusDamageAD",
                                "__type": "StatByNamedDataValueCalculationPart",
                            },
                        ]
                    }
                },
            }
        }
    )
    passive_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "DataValues": [{"name": "YorickPassiveGhoulMax", "values": [4, 4, 4]}]
            }
        }
    )

    result = resolve_tooltip_placeholders(
        "Deals @BonusDamage@ damage, up to @Spell.YorickPassive:YorickPassiveGhoulMax@.",
        q_bin,
        {"yorickpassive": passive_bin},
    )

    assert "30/50/70/90/110 + 50% AD" in result
    assert "up to 4" in result


def test_effect_value_calculation_part_uses_effect_amounts() -> None:
    spell_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "mEffectAmount": [
                    {"value": [80, 80, 130, 180, 230, 280, 280]},
                    {"value": [1.6, 1.6, 1.7, 1.8, 1.9, 2.0, 2.0]},
                ],
                "mSpellCalculations": {
                    "TotalDamageTooltip": {
                        "mFormulaParts": [
                            {"mEffectIndex": 1, "__type": "EffectValueCalculationPart"},
                            {
                                "mCoefficient": 0.7,
                                "__type": "StatByCoefficientCalculationPart",
                            },
                        ]
                    }
                },
            }
        }
    )

    result = resolve_tooltip_placeholders(
        "Silences for @Effect2Amount@ seconds and deals @TotalDamageTooltip@ damage.",
        spell_bin,
    )

    assert "1.6/1.7/1.8/1.9/2 seconds" in result
    assert "80/130/180/230/280 + 70% AP" in result
    assert "?" not in result


def test_spell_candidate_score_matches_name_based_spell_folders() -> None:
    champion = ChampionData(
        champion_id=31,
        name="Cho'Gath",
        icon_path=None,
        passive=None,
        spells=[
            {"spellKey": "q", "name": "Rupture"},
            {"spellKey": "w", "name": "Feral Scream"},
            {"spellKey": "e", "name": "Vorpal Spikes"},
            {"spellKey": "r", "name": "Feast"},
        ],
        alias="Chogath",
    )

    rupture = (
        "Characters/Chogath/Spells/RuptureAbility/Rupture",
        {
            "ObjectName": "Rupture",
            "mSpell": {
                "DataValues": [{"name": "BaseDamage", "values": [0, 80, 130]}],
                "mSpellCalculations": {"TotalDamageTooltip": {}},
                "mClientData": {
                    "mTooltipData": {
                        "mLocKeys": {"keyTooltip": "Spell_Rupture_Tooltip"}
                    }
                },
            },
        },
    )
    feast = (
        "Characters/Chogath/Spells/FeastAbility/Feast",
        {
            "ObjectName": "Feast",
            "mSpell": {
                "DataValues": [{"name": "RDamage", "values": [0, 300, 475]}],
                "mSpellCalculations": {"RDamage": {}},
                "mClientData": {
                    "mTooltipData": {"mLocKeys": {"keyTooltip": "Spell_Feast_Tooltip"}}
                },
            },
        },
    )

    assert spell_candidate_score(rupture, champion, "Q") > spell_candidate_score(
        feast,
        champion,
        "Q",
    )
    assert spell_candidate_score(feast, champion, "R") > spell_candidate_score(
        rupture,
        champion,
        "R",
    )


def test_passive_matching_does_not_treat_any_p_ability_as_passive() -> None:
    assert (
        slot_from_bin_key(
            "Characters/Xerath/Spells/XerathArcanopulseChargeUpAbility/"
            "XerathArcanopulseChargeUp",
            "xerath",
        )
        is None
    )
    assert not loc_key_matches_slot(
        {"keyTooltip": "Spell_CardmasterStack_Tooltip"},
        "twistedfate",
        "P",
    )
    assert loc_key_matches_slot(
        {"keyTooltip": "game_character_passiveTooltip_Xerath"},
        "xerath",
        "P",
    )


def test_summoners_rift_item_filter_excludes_non_sr_ranges() -> None:
    assert is_summoners_rift_item({"id": 1055, "name": "Doran's Blade", "inStore": True})
    assert not is_summoners_rift_item({"id": 9408, "name": "Carrot Crash", "inStore": True})
    assert not is_summoners_rift_item(
        {"id": 223031, "name": "Arena Infinity Edge", "inStore": True}
    )
    assert not is_summoners_rift_item(
        {"id": 3865, "name": "Senna Only", "inStore": True, "requiredChampion": "Senna"}
    )


def test_rank_count_trims_offset_spell_values() -> None:
    spell_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "DataValues": [{"name": "NumberOfShots", "values": [3, 4, 5, 6, 7, 8, 9]}]
            }
        },
        rank_count=3,
    )

    result = resolve_tooltip_placeholders(
        "Recast up to @NumberOfShots@ times.",
        spell_bin,
    )

    assert result == "Recast up to 4/5/6 times."


def test_aurelion_sol_passive_falls_back_from_runtime_placeholder_text() -> None:
    reducer = MatchStateReducer(None, AssetResolver(local_assets=False), TooltipFormatter())
    champion = ChampionData(
        champion_id=136,
        name="Aurelion Sol",
        icon_path=None,
        passive={
            "name": "Cosmic Creator",
            "description": "Clean passive text.",
        },
        spells=[],
        alias="AurelionSol",
        passive_tooltip="Improves Astral Flight by @f2.1@% and shows @f1@ Stardust.",
    )

    ability = reducer._abilities_from_champion(champion)[0]

    assert ability.full_description == "Clean passive text."


def test_rank_count_uses_tooltip_levelup_metadata_not_spell_slot() -> None:
    raw_spell = {
        "mSpell": {
            "mClientData": {
                "mTooltipData": {
                    "mLists": {
                        "LevelUp": {
                            "levelCount": 6,
                        }
                    }
                }
            }
        }
    }

    assert ability_rank_count("R", {"maxLevel": 0}, raw_spell) == 6


def test_hash_calculations_use_resolved_stat_line_labels() -> None:
    reducer = MatchStateReducer(None, AssetResolver(local_assets=False), TooltipFormatter())
    champion = ChampionData(
        champion_id=136,
        name="Aurelion Sol",
        icon_path=None,
        passive=None,
        spells=[
            {
                "spellKey": "r",
                "name": "Falling Star",
                "description": "Drops a star.",
                "dynamicDescription": "Deals @VisibleDamage@ damage.",
            }
        ],
        bin_data={
            "Characters/AurelionSol/Spells/AurelionSolRAbility/AurelionSolR": {
                "ObjectName": "AurelionSolR",
                "mSpell": {
                "DataValues": [
                    {"name": "BaseDamage", "values": [50, 150, 250, 350, 450, 550, 650]},
                ],
                "mClientData": {
                    "mTooltipData": {"mLists": {"LevelUp": {"levelCount": 3}}}
                },
                "mSpellCalculations": {
                        "VisibleDamage": {
                            "mFormulaParts": [
                                {
                                    "mDataValue": "BaseDamage",
                                    "__type": "NamedDataValueCalculationPart",
                                }
                            ]
                        },
                        "{1d7ce9ef}": {
                            "mMultiplier": {
                                "mDataValue": "InversePI",
                                "__type": "NamedDataValueCalculationPart",
                            },
                            "mFormulaParts": [
                                {
                                    "mPart1": {
                                        "mDataValue": "StartingRadius",
                                        "__type": "NamedDataValueCalculationPart",
                                    },
                                    "mPart2": {
                                        "mDataValue": "AreaPerPassiveStack",
                                        "__type": "BuffCounterByNamedDataValueCalculationPart",
                                    },
                                    "__type": "ProductOfSubPartsCalculationPart",
                                },
                                {
                                    "mDataValue": "BaseDamage",
                                    "__type": "NamedDataValueCalculationPart",
                                }
                            ]
                        },
                    },
                },
            }
        },
        alias="AurelionSol",
    )

    ability = reducer._abilities_from_champion(champion)[0]

    assert ability.full_description == "Deals 150/250/350 damage."
    assert "Visible Damage: 150/250/350" in ability.stat_lines
    assert any(
        line.startswith("Starting Radius / Area Per Passive Stack")
        for line in ability.stat_lines
    )
    assert not any("1d7ce9ef" in line for line in ability.stat_lines)


def test_stat_id_life_steal_renders_named_scaling() -> None:
    spell_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "DataValues": [
                    {"name": "Omnivamp_LifeStealScaling", "values": [0.5, 0.5, 0.5]},
                ],
                "mSpellCalculations": {
                    "Calc_Omnivamp": {
                        "mFormulaParts": [
                            {
                                "mStat": 18,
                                "mDataValue": "Omnivamp_LifeStealScaling",
                                "__type": "StatByNamedDataValueCalculationPart",
                            }
                        ]
                    }
                },
            }
        }
    )

    result = resolve_tooltip_placeholders("Gain @Calc_Omnivamp@ Omnivamp.", spell_bin)

    assert result == "Gain 50% Life Steal Omnivamp."
    assert "Omnivamp: 50% Life Steal" in spell_bin.stat_lines()


def test_missing_stat_id_defaults_to_ap_scaling() -> None:
    spell_bin = SpellBinData.from_raw(
        {
            "mSpell": {
                "mSpellCalculations": {
                    "Damage": {
                        "mFormulaParts": [
                            {"mCoefficient": 0.7, "__type": "StatByCoefficientCalculationPart"}
                        ]
                    }
                }
            }
        }
    )

    result = resolve_tooltip_placeholders("Deals @Damage@ damage.", spell_bin)

    assert result == "Deals 70% AP damage."
