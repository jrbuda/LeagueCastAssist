from __future__ import annotations

from league_cast_assist.models import PlayerState, TeamState
from league_cast_assist.ui.widgets import format_gold_amount, role_comparisons_by_role


def test_role_comparisons_pair_opposing_roles_by_item_value() -> None:
    blue_team = TeamState(
        side="blue",
        display_name="Blue Team",
        players=[
            PlayerState(
                stable_key="blue-top",
                display_name="Blue Top",
                position="TOP",
                item_value=1800,
            ),
            PlayerState(
                stable_key="blue-support",
                display_name="Blue Support",
                position="SUPPORT",
                item_value=600,
            ),
        ],
    )
    red_team = TeamState(
        side="red",
        display_name="Red Team",
        players=[
            PlayerState(
                stable_key="red-top",
                display_name="Red Top",
                position="TOP",
                item_value=1200,
            ),
            PlayerState(
                stable_key="red-support",
                display_name="Red Support",
                position="UTILITY",
                item_value=900,
            ),
        ],
    )

    comparisons = role_comparisons_by_role(blue_team, red_team)

    assert comparisons["TOP"].lead_side == "blue"
    assert comparisons["TOP"].lead_amount == 600
    assert comparisons["UTILITY"].lead_side == "red"
    assert comparisons["UTILITY"].lead_amount == 300


def test_format_gold_amount_compacts_thousands() -> None:
    assert format_gold_amount(999) == "999"
    assert format_gold_amount(1000) == "1k"
    assert format_gold_amount(1250) == "1.2k"
