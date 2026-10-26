from __future__ import annotations

from PySide6.QtWidgets import QDialog

from league_cast_assist.ui.stream_device_dialog import VB_CABLE_URL, StreamDeviceHelpDialog


def test_help_points_at_a_free_option() -> None:
    assert VB_CABLE_URL.startswith("https://")
    assert "vb-audio" in VB_CABLE_URL


def test_recheck_without_a_device_keeps_the_dialog_open(qapp) -> None:  # noqa: ANN001
    dialog = StreamDeviceHelpDialog(None, refresh=lambda: 0)
    dialog._recheck()  # noqa: SLF001

    assert dialog.device_found is False
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "still no virtual audio device" in dialog._status.text().lower()  # noqa: SLF001


def test_recheck_accepts_after_the_device_appears(qapp) -> None:  # noqa: ANN001
    dialog = StreamDeviceHelpDialog(None, refresh=lambda: 2)
    dialog._recheck()  # noqa: SLF001

    assert dialog.device_found is True
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_recheck_without_a_callback_is_safe(qapp) -> None:  # noqa: ANN001
    dialog = StreamDeviceHelpDialog(None)
    dialog._recheck()  # noqa: SLF001

    assert dialog.device_found is False
