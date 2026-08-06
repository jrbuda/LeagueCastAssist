from league_cast_assist.data.ability_math import SpellBinData


def test_melee_ranged_split_placeholder_resolves_both_variants() -> None:
    bin_data = SpellBinData(
        data_values={
            "PercentCurrentHPMelee": [9.0],
            "PercentCurrentHPRanged": [7.0],
            "LethalityBonusModMelee": [15.0],
            "LethalityBonusModRanged": [12.0],
        }
    )

    assert (
        bin_data.resolve_placeholder("PercentHPMeleeRangedSplit")
        == "9% Melee / 7% Ranged"
    )
    assert (
        bin_data.resolve_placeholder("LethalityBonusModMeleeRangedSplit")
        == "15 Melee / 12 Ranged"
    )


def test_melee_ranged_split_placeholder_unresolvable_without_pairs() -> None:
    bin_data = SpellBinData(
        data_values={
            "PercentCurrentHPMelee": [9.0],
            "LethalityBonusModRanged": [12.0],
        }
    )

    assert bin_data.resolve_placeholder("PercentHPMeleeRangedSplit") is None
    assert bin_data.resolve_placeholder("Unrelated") is None


def test_split_resolves_exact_prefix_pair() -> None:
    bin_data = SpellBinData(
        data_values={"SlowAmount": [0.25], "RangedSlowAmount": [0.125]}
    )

    assert (
        bin_data.resolve_placeholder("SlowAmountMeleeRangedSplit")
        == "25% Melee / 12.5% Ranged"
    )


def test_split_resolves_containment_pair() -> None:
    bin_data = SpellBinData(
        data_values={
            "BaseADBonusMelee": [10.0],
            "BaseADBonusRange": [5.0],
            "ADPerStatueMelee": [3.0],
            "ADPerStatueRange": [2.0],
            "BuffDurationMelee": [60.0],
            "BuffDurationRange": [45.0],
        }
    )

    assert bin_data.resolve_placeholder("BaseADSplit") == "10 Melee / 5 Ranged"
    assert bin_data.resolve_placeholder("BonusADPerStatueSplit") == "3 Melee / 2 Ranged"
    assert bin_data.resolve_placeholder("BuffDurationSplit") == "60 Melee / 45 Ranged"


def test_product_of_numbers_formats_numeric_product() -> None:
    bin_data = SpellBinData(
        data_values={"BonusDamagePerStack": [0.4]},
        calculations={
            "MaxDamageCalc": {
                "mFormulaParts": [
                    {
                        "mPart1": {
                            "mDataValue": "BonusDamagePerStack",
                            "__type": "NamedDataValueCalculationPart",
                        },
                        "mPart2": {"mNumber": 100.0, "__type": "NumberCalculationPart"},
                        "__type": "ProductOfSubPartsCalculationPart",
                    }
                ],
                "__type": "GameCalculation",
            }
        },
    )

    assert bin_data.format_calculation("MaxDamageCalc") == "40"


def test_product_of_numbers_keeps_join_for_non_numeric_operands() -> None:
    bin_data = SpellBinData(
        data_values={"Damage": [30.0]},
        calculations={
            "Calc": {
                "mFormulaParts": [
                    {
                        "mPart1": {
                            "mDataValue": "Damage",
                            "__type": "NamedDataValueCalculationPart",
                        },
                        "mPart2": {
                            "mCoefficient": 1.5,
                            "__type": "StatByCoefficientCalculationPart",
                            "mStat": 2,
                        },
                        "__type": "ProductOfSubPartsCalculationPart",
                    }
                ],
                "__type": "GameCalculation",
            }
        },
    )

    formatted = bin_data.format_calculation("Calc")
    assert formatted is not None
    assert " x " in formatted


def test_effect_amount_placeholder_applies_multiplier() -> None:
    bin_data = SpellBinData(effect_amounts={"4": [0.25]})

    assert bin_data.resolve_placeholder("Effect4Amount") == "0.25"
    assert bin_data.resolve_placeholder("Effect4Amount*-100") == "-25"


def test_effect_amount_placeholder_without_multiplier_unaffected() -> None:
    bin_data = SpellBinData(effect_amounts={"3": [90.0]})

    assert bin_data.resolve_placeholder("Effect3Amount") == "90"


def test_melee_ranged_split_of_durations_stays_numeric() -> None:
    bin_data = SpellBinData(
        data_values={
            "EffectDurationMelee": [0.5],
            "EffectDurationRanged": [0.25],
        }
    )

    assert (
        bin_data.resolve_placeholder("EffectDurationMeleeRangedSplit")
        == "0.5 Melee / 0.25 Ranged"
    )
