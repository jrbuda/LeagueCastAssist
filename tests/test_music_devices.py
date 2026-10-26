from __future__ import annotations

from league_cast_assist.music.devices import (
    device_label,
    is_loopback_description,
    normalize_description,
    pick_loopback_index,
)


def test_common_virtual_devices_are_recognised() -> None:
    assert is_loopback_description("CABLE Input (VB-Audio Virtual Cable)")
    assert is_loopback_description("CABLE-A Input (VB-Audio Cable A)")
    assert is_loopback_description("Line (Voicemod Virtual Audio Device (WDM))")
    assert is_loopback_description("Speakers (NVIDIA Broadcast)")
    assert is_loopback_description("Speakers (Steam Streaming Speakers)")
    assert is_loopback_description("BlackHole 2ch")


def test_physical_outputs_are_not_flagged() -> None:
    assert not is_loopback_description("Speakers (2- Focusrite USB Audio)")
    assert not is_loopback_description("Headphones (Realtek(R) Audio)")
    assert not is_loopback_description("AMD HDMI Output (AMD High Definition Audio)")
    assert not is_loopback_description("")
    assert not is_loopback_description(None)


def test_names_are_normalised_before_matching() -> None:
    assert normalize_description("  CABLE_Input   (VB-Audio) ") == "cable input (vb-audio)"
    assert is_loopback_description("  CABLE_input  (VB-Audio)  ")


def test_labels_mark_virtual_devices() -> None:
    assert device_label("CABLE Input (VB-Audio Virtual Cable)") == (
        "CABLE Input (VB-Audio Virtual Cable)  ·  virtual"
    )
    assert device_label("Speakers (Realtek(R) Audio)") == "Speakers (Realtek(R) Audio)"
    assert device_label(None) == "Unknown device"


def test_pick_loopback_prefers_the_explicit_cable() -> None:
    descriptions = [
        "Speakers (2- Focusrite USB Audio)",
        "Speakers (NVIDIA Broadcast)",
        "CABLE Input (VB-Audio Virtual Cable)",
        "Line (Voicemod Virtual Audio Device (WDM))",
    ]
    assert pick_loopback_index(descriptions) == 2


def test_pick_loopback_falls_back_to_any_virtual_device() -> None:
    assert pick_loopback_index(
        ["Speakers (Realtek(R) Audio)", "Line (Voicemod Virtual Audio Device (WDM))"]
    ) == 1


def test_pick_loopback_returns_none_without_a_virtual_device() -> None:
    assert pick_loopback_index(["Speakers (Realtek(R) Audio)", "Headphones (USB Audio)"]) is None
    assert pick_loopback_index([]) is None

def test_device_id_normalises_qbytearray_to_plain_string() -> None:
    from PySide6.QtCore import QByteArray

    from league_cast_assist.music.devices import device_id

    class StubDevice:
        def id(self) -> QByteArray:
            return QByteArray(b"{0.0.0.00000000}.{voice-mod}")

    class StrDevice:
        def id(self) -> str:
            return "plain-id"

    assert device_id(StubDevice()) == "{0.0.0.00000000}.{voice-mod}"
    assert device_id(StrDevice()) == "plain-id"
