from league_cast_assist.data.ability_math import SpellBinData, resolve_tooltip_placeholders
from league_cast_assist.data.match_state import loc_key_matches_slot, spell_candidate_score
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
