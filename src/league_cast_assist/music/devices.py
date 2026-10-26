"""Audio device classification.

OBS can only capture audio that is being rendered to an endpoint. To send music
to the stream without hearing it, the app must render to a *virtual* output
device (VB-Cable, Voicemod, NVIDIA Broadcast, Steam Streaming, ...) instead of
the speakers. These helpers spot such endpoints from their names so the UI can
offer a single "send to stream only" checkbox.
"""

from __future__ import annotations

import re

# Ordered by how unambiguous the hint is; earlier hints win when several
# virtual devices are present.
LOOPBACK_HINTS: tuple[str, ...] = (
    "cable input",
    "cable-a input",
    "cable b input",
    "vb-audio",
    "vb audio",
    "voicemod",
    "virtual audio",
    "virtual cable",
    "virtual device",
    "vbcable",
    "blackhole",
    "soundflower",
    "loopback",
    "nvidia broadcast",
    "steam streaming",
    "dante virtual",
)

_NORMALIZE = re.compile(r"[\s_]+")


def normalize_description(description: str | None) -> str:
    return _NORMALIZE.sub(" ", (description or "").strip().lower())


def is_loopback_description(description: str | None) -> bool:
    """True when the endpoint looks like a virtual/loopback audio device."""
    text = normalize_description(description)
    if not text:
        return False
    return any(hint in text for hint in LOOPBACK_HINTS)


def device_label(description: str | None, loopback: bool | None = None) -> str:
    """Dropdown label: marks virtual endpoints so they are easy to spot."""
    name = (description or "").strip() or "Unknown device"
    if loopback is None:
        loopback = is_loopback_description(name)
    return f"{name}  ·  virtual" if loopback else name


def pick_loopback_index(descriptions: list[str]) -> int | None:
    """Index of the most suitable virtual output, or ``None`` when absent."""
    normalized = [normalize_description(item) for item in descriptions]
    for hint in LOOPBACK_HINTS:
        for index, text in enumerate(normalized):
            if hint in text:
                return index
    return None


def device_id(device) -> str:  # noqa: ANN001 - QAudioDevice
    """Stable string id for a device.

    PySide6 returns a ``QByteArray`` from ``QAudioDevice.id()``; storing that in
    a settings model breaks JSON serialisation and never compares equal to a
    restored string.
    """
    raw = device.id()
    if isinstance(raw, str):
        return raw
    data = raw.data() if hasattr(raw, "data") else bytes(raw)
    return bytes(data).decode("utf-8", "replace")


def should_monitor(stream_only: bool, stream_device_is_virtual: bool) -> bool:
    """Whether the producer should also hear the music.

    One control drives both: "send to stream only" means silent locally. When it
    is off, music also plays through the default output — but only if the stream
    output is a virtual device, because a physical output is already audible.
    """
    return (not stream_only) and stream_device_is_virtual
