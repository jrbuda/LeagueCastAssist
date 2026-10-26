from __future__ import annotations

import sys

from league_cast_assist.update import (
    can_install_downloaded_update,
    is_newer_version,
    is_portable_build,
    release_from_github_payload,
    version_key,
)


def test_version_key_extracts_semver_from_tag() -> None:
    assert version_key("v1.2.3") == (1, 2, 3)
    assert version_key("LeagueCastAssist-2.0") == (2, 0)


def test_is_newer_version_compares_padded_numeric_parts() -> None:
    assert is_newer_version("v0.4.1", "0.4.0")
    assert is_newer_version("v0.5", "0.4.9")
    assert not is_newer_version("v0.4.0", "0.4")
    assert not is_newer_version("v0.3.9", "0.4.0")


def test_release_from_github_payload_selects_main_exe_and_checksum() -> None:
    release = release_from_github_payload(
        {
            "tag_name": "v0.5.0",
            "html_url": "https://github.com/jrbuda/LeagueCastAssist/releases/tag/v0.5.0",
            "body": "Release notes",
            "assets": [
                {
                    "name": "LeagueCastAssist-debug.exe",
                    "browser_download_url": "https://example.test/debug.exe",
                    "size": 10,
                },
                {
                    "name": "LeagueCastAssist.exe",
                    "browser_download_url": "https://example.test/LeagueCastAssist.exe",
                    "size": 20,
                },
                {
                    "name": "LeagueCastAssist.exe.sha256",
                    "browser_download_url": "https://example.test/LeagueCastAssist.exe.sha256",
                    "size": 64,
                },
            ],
        }
    )

    assert release is not None
    assert release.version == "0.5.0"
    assert release.asset.name == "LeagueCastAssist.exe"
    assert release.asset.sha256_url == "https://example.test/LeagueCastAssist.exe.sha256"


def test_release_from_github_payload_uses_github_digest_when_available() -> None:
    release = release_from_github_payload(
        {
            "tag_name": "v0.5.0",
            "assets": [
                {
                    "name": "LeagueCastAssist.exe",
                    "browser_download_url": "https://example.test/LeagueCastAssist.exe",
                    "size": 20,
                    "digest": "sha256:"
                    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                }
            ],
        }
    )

    assert release is not None
    assert release.asset.sha256 == (
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )

def test_source_checkout_is_not_a_portable_build() -> None:
    assert is_portable_build() is False


def test_portable_build_detection_uses_meipass_location(monkeypatch, tmp_path) -> None:
    exe = tmp_path / "LeagueCastAssist-portable.exe"

    # onedir: _MEIPASS is <app>\_internal, i.e. beside the exe
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert is_portable_build() is True

    # onefile: _MEIPASS is an extraction dir under %TEMP%, outside the app folder
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "temp" / "_MEI123"), raising=False)
    assert is_portable_build() is False

    monkeypatch.delattr(sys, "frozen", raising=False)
    assert is_portable_build() is False


def test_portable_build_cannot_install_a_onefile_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "win32", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert can_install_downloaded_update() is False

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "tmp" / "_MEI9"), raising=False)
    if sys.platform == "win32":
        assert can_install_downloaded_update() is True
