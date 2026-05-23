from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass
from typing import Any

import httpx

from league_cast_assist.config import cache_dir
from league_cast_assist.data.ability_math import SpellBinData, tooltip_data_from_spell
from league_cast_assist.data.asset_resolver import AssetResolver

COMMUNITY_DRAGON_DATA_BASE = (
    "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1"
)
COMMUNITY_DRAGON_GAME_BASE = "https://raw.communitydragon.org/latest/game"
STRING_TEMPLATE_PATTERN = re.compile(r"{{\s*([^}]+)\s*}}")
MAIN_TEXT_PATTERN = re.compile(r"<mainText>(.*?)</mainText>", re.IGNORECASE | re.DOTALL)
RAW_PLACEHOLDER_PATTERN = re.compile(r"@[A-Za-z0-9_:.+*\-/]+@")
ITEM_ICON_PATTERN = re.compile(r"%i:[^%]+%")
ITEM_RANGE_SPLIT_PATTERN = re.compile(
    r"{{\s*Item_Melee_Ranged_Split(?:_Dynamic)?(_[A-Z])?\s*}}",
    re.IGNORECASE,
)

_CDRAGON_METADATA_URL = (
    "https://raw.communitydragon.org/latest/content-metadata.json"
)
_CONCURRENT_DOWNLOADS = 12
LOGGER = logging.getLogger(__name__)


class StaticDataCancelled(Exception):
    """Raised when a user cancels a long static-data operation."""


@dataclass(frozen=True)
class ChampionData:
    champion_id: int
    name: str
    icon_path: str | None
    passive: dict[str, Any] | None
    spells: list[dict[str, Any]]
    bin_data: dict[str, Any] | None = None
    alias: str = ""
    passive_tooltip: str | None = None
    spell_tooltips: dict[str, str] | None = None


@dataclass(frozen=True)
class ChampionSummaryData:
    champion_id: int
    name: str
    alias: str
    icon_path: str | None


@dataclass(frozen=True)
class ItemData:
    item_id: int
    name: str
    icon_path: str | None
    description: str | None
    total_cost: int | None


@dataclass(frozen=True)
class ItemDescriptionCandidate:
    source_key: str
    text: str


@dataclass(frozen=True)
class PatchVersionStatus:
    live_version: str | None
    cached_version: str | None
    update_available: bool


class StaticDataService:
    """Downloads and reads CommunityDragon metadata."""

    def __init__(self, version: str = "latest", download_assets: bool = True) -> None:
        self._version = version
        self._download_assets = download_assets
        self._cache_root = cache_dir() / "communitydragon" / version
        self._asset_resolver = AssetResolver(local_assets=True, version=version)
        self._champion_summary_cache: dict[int, ChampionSummaryData] | None = None
        self._champion_name_to_id: dict[str, int] | None = None
        self._champion_cache: dict[int, ChampionData] = {}
        self._item_cache: dict[int, ItemData] | None = None
        self._item_bin_cache: dict[str, Any] | None = None
        self._string_table_cache: dict[str, str] | None = None
        self._tooltip_text_index_cache: dict[str, StringTableMatch] | None = None
        self._progress_callback = None
        self._cancel_callback = None

    def set_progress_callback(self, callback) -> None:  # noqa: ANN001
        self._progress_callback = callback

    def set_cancel_callback(self, callback) -> None:  # noqa: ANN001
        self._cancel_callback = callback

    async def ensure_core_data(
        self,
        version_status: PatchVersionStatus | None = None,
    ) -> None:
        self._cache_root.mkdir(parents=True, exist_ok=True)

        version_status = version_status or await self.patch_version_status()
        live_version = version_status.live_version
        cached_version = version_status.cached_version
        if live_version and live_version != cached_version:
            self._invalidate_json_cache()
            if self._download_assets:
                self._invalidate_asset_cache()

        self._check_cancelled()
        await self._download_if_missing("champion-summary.json")
        self._check_cancelled()
        await self._download_if_missing("items.json")
        self._check_cancelled()
        await self._download_game_if_missing("items.cdtb.bin.json")
        self._check_cancelled()
        await self._load_string_table()

        if live_version and live_version != cached_version:
            self._write_cached_version(live_version)

        self._item_cache = None
        self.champion_summary()
        self.item_lookup()

        if self._download_assets:
            self._check_cancelled()
            await self._pre_download_all_icons()

    async def patch_version_status(self) -> PatchVersionStatus:
        live_version = await self._fetch_live_version()
        cached_version = self._read_cached_version()
        return PatchVersionStatus(
            live_version=live_version,
            cached_version=cached_version,
            update_available=is_newer_cdragon_version(live_version, cached_version),
        )

    async def ensure_all_in_game_data(
        self,
        version_status: PatchVersionStatus | None = None,
    ) -> None:
        """Download all CDragon data needed to render any in-game champion."""
        try:
            await self.ensure_core_data(version_status)

            champion_ids = sorted(self.champion_summary())
            if not champion_ids:
                return

            total = len(champion_ids)
            completed = [0]
            sem = asyncio.Semaphore(_CONCURRENT_DOWNLOADS)
            should_download_assets = self._download_assets

            async def load_champion(champion_id: int) -> ChampionData | None:
                self._check_cancelled()
                async with sem:
                    champion = await self.champion(champion_id)
                self._check_cancelled()
                completed[0] += 1
                self._report_progress("Loading champion ability data", completed[0], total)
                return champion

            self._report_progress("Loading champion ability data", 0, total)
            self._download_assets = False
            try:
                champions = await asyncio.gather(
                    *[load_champion(champion_id) for champion_id in champion_ids]
                )
            finally:
                self._download_assets = should_download_assets

            if should_download_assets:
                paths: list[str] = []
                for champion in champions:
                    self._check_cancelled()
                    if champion is not None:
                        paths.extend(asset_paths_for_champion(champion))
                await self.ensure_assets(paths)
        finally:
            self._report_progress("", 0, 0)

    def champion_summary(self) -> dict[int, ChampionSummaryData]:
        if self._champion_summary_cache is not None:
            return self._champion_summary_cache

        data = self._read_json("champion-summary.json")
        if not isinstance(data, list):
            self._champion_summary_cache = {}
            self._champion_name_to_id = {}
            return {}

        champions: dict[int, ChampionSummaryData] = {}
        name_to_id: dict[str, int] = {}
        for raw_champion in data:
            if not isinstance(raw_champion, dict) or "id" not in raw_champion:
                continue

            try:
                champion_id = int(raw_champion["id"])
            except (TypeError, ValueError):
                continue
            if champion_id <= 0 or champion_id >= 60000:
                continue

            champion = ChampionSummaryData(
                champion_id=champion_id,
                name=str(raw_champion.get("name") or "Unknown Champion"),
                alias=str(raw_champion.get("alias") or ""),
                icon_path=raw_champion.get("squarePortraitPath"),
            )
            champions[champion_id] = champion
            for key in (champion.name, champion.alias):
                normalized = normalize_lookup_key(key)
                if normalized:
                    name_to_id[normalized] = champion_id

        self._champion_summary_cache = champions
        self._champion_name_to_id = name_to_id
        return champions

    def champion_id_for_name(self, name: str | None) -> int | None:
        if not name:
            return None

        if self._champion_name_to_id is None:
            self.champion_summary()

        assert self._champion_name_to_id is not None
        normalized = normalize_lookup_key(name)
        if normalized in self._champion_name_to_id:
            return self._champion_name_to_id[normalized]

        if "_" in name:
            tail = name.rsplit("_", 1)[-1]
            normalized_tail = normalize_lookup_key(tail)
            return self._champion_name_to_id.get(normalized_tail)

        return None

    async def champion(self, champion_id: int) -> ChampionData | None:
        if champion_id in self._champion_cache:
            return self._champion_cache[champion_id]

        try:
            await self._download_if_missing(f"champions/{champion_id}.json")
        except (httpx.HTTPError, OSError, UnicodeDecodeError, ValueError):
            summary = self.champion_summary().get(champion_id)
            if summary is None:
                return None
            return ChampionData(
                champion_id=summary.champion_id,
                name=summary.name,
                icon_path=summary.icon_path,
                passive=None,
                spells=[],
                bin_data=None,
            )

        data = self._read_json(f"champions/{champion_id}.json")
        if not isinstance(data, dict):
            return None

        alias = str(data.get("alias") or "")
        bin_data = await self._load_champion_bin(alias)
        spells = list(data.get("spells") or [])
        passive_tooltip, spell_tooltips = await self._load_champion_tooltips(
            alias,
            bin_data,
            spells,
        )

        try:
            parsed_champion_id = int(data["id"])
        except (KeyError, TypeError, ValueError):
            return None

        champion = ChampionData(
            champion_id=parsed_champion_id,
            name=str(data.get("name") or "Unknown Champion"),
            icon_path=data.get("squarePortraitPath"),
            passive=data.get("passive"),
            spells=spells,
            bin_data=bin_data,
            alias=alias,
            passive_tooltip=passive_tooltip,
            spell_tooltips=spell_tooltips,
        )
        self._champion_cache[champion_id] = champion

        if self._download_assets:
            await self.ensure_assets(asset_paths_for_champion(champion))

        return champion

    def item_lookup(self) -> dict[int, ItemData]:
        if self._item_cache is not None:
            return self._item_cache

        data = self._read_json("items.json")
        if not isinstance(data, list):
            return {}

        entries = self._string_table_cache or {}
        item_bins = self._item_bin_lookup()
        items: dict[int, ItemData] = {}
        for raw_item in data:
            if not isinstance(raw_item, dict) or "id" not in raw_item:
                continue
            try:
                item_id = int(raw_item["id"])
            except (TypeError, ValueError):
                continue
            price = raw_item.get("price")
            total_cost = raw_item.get("priceTotal")
            if not isinstance(total_cost, int):
                total_cost = price if isinstance(price, int) else raw_item.get("totalPrice")

            items[item_id] = ItemData(
                item_id=item_id,
                name=str(raw_item.get("name") or "Unknown Item"),
                icon_path=raw_item.get("iconPath"),
                description=best_item_description(
                    raw_item,
                    entries,
                    item_bins.get(item_id),
                ),
                total_cost=total_cost if isinstance(total_cost, int) else None,
            )

        self._item_cache = items
        return items

    def summoners_rift_item_ids(self) -> list[int]:
        data = self._read_json("items.json")
        if not isinstance(data, list):
            return []

        item_ids = []
        for raw_item in data:
            if not isinstance(raw_item, dict):
                continue
            item_id = raw_item.get("id")
            if not isinstance(item_id, int):
                continue
            if not is_summoners_rift_item(raw_item):
                continue
            item_ids.append(item_id)
        return sorted(set(item_ids))

    async def ensure_item_assets(self, item_ids: set[int]) -> None:
        if not self._download_assets:
            return

        item_lookup = self.item_lookup()
        paths = [item_lookup[item_id].icon_path for item_id in item_ids if item_id in item_lookup]
        await self.ensure_assets([path for path in paths if path])

    async def _pre_download_all_icons(self) -> None:
        """Download every champion portrait and every SR item icon concurrently."""
        paths: list[str] = [
            c.icon_path for c in self.champion_summary().values() if c.icon_path
        ]
        paths += [item.icon_path for item in self.item_lookup().values() if item.icon_path]
        await self.ensure_assets(paths)

    async def _fetch_live_version(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(_CDRAGON_METADATA_URL)
                response.raise_for_status()
                data = response.json()
                version = data.get("version")
                return str(version) if version else None
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    def _read_cached_version(self) -> str | None:
        path = self._cache_root / ".cdragon-version"
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except (OSError, UnicodeDecodeError):
            LOGGER.warning("Failed to read cached CommunityDragon version", exc_info=True)
            return None

    def _write_cached_version(self, version: str) -> None:
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._write_text_atomic(self._cache_root / ".cdragon-version", version)

    def _invalidate_json_cache(self) -> None:
        """Delete all cached JSON metadata so it is re-downloaded on the new patch."""
        if self._cache_root.exists():
            try:
                shutil.rmtree(self._cache_root)
            except OSError:
                LOGGER.warning("Failed to invalidate CommunityDragon cache", exc_info=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._string_table_cache = None
        self._champion_summary_cache = None
        self._champion_name_to_id = None
        self._item_cache = None
        self._item_bin_cache = None
        self._tooltip_text_index_cache = None
        self._champion_cache = {}

    def _invalidate_asset_cache(self) -> None:
        asset_cache_dir = self._asset_resolver.asset_cache_dir
        if asset_cache_dir.exists():
            try:
                shutil.rmtree(asset_cache_dir)
            except OSError:
                LOGGER.warning("Failed to invalidate CommunityDragon asset cache", exc_info=True)
        asset_cache_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_assets(self, asset_paths: list[str]) -> None:
        if not self._download_assets:
            return

        self._check_cancelled()
        unique_paths = sorted(set(asset_paths))
        to_download = []
        for path in unique_paths:
            try:
                target = self._asset_resolver.local_path(path)
            except ValueError:
                LOGGER.warning("Skipping invalid asset path: %s", path)
                continue
            if not asset_file_looks_valid(target):
                to_download.append(path)
        if not to_download:
            return

        total = len(to_download)
        self._report_progress("Loading item/champion icons", 0, total)
        completed = [0]
        sem = asyncio.Semaphore(_CONCURRENT_DOWNLOADS)

        async def download_one(client: httpx.AsyncClient, asset_path: str) -> None:
            self._check_cancelled()
            try:
                target = self._asset_resolver.local_path(asset_path)
            except ValueError:
                LOGGER.warning("Skipping invalid asset path: %s", asset_path)
                completed[0] += 1
                self._report_progress("Loading item/champion icons", completed[0], total)
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            async with sem:
                try:
                    response = await client.get(self._asset_resolver.remote_url(asset_path))
                    response.raise_for_status()
                    if asset_bytes_look_valid(target, response.content):
                        self._check_cancelled()
                        self._write_bytes_atomic(target, response.content)
                    else:
                        LOGGER.warning("Downloaded invalid asset bytes: %s", asset_path)
                except (httpx.HTTPError, OSError, ValueError):
                    LOGGER.warning("Asset download failed: %s", asset_path, exc_info=True)
            completed[0] += 1
            self._report_progress("Loading item/champion icons", completed[0], total)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await asyncio.gather(*[download_one(client, p) for p in to_download])
        finally:
            self._report_progress("", 0, 0)

    def _report_progress(self, message: str, current: int, total: int) -> None:
        self._check_cancelled()
        if self._progress_callback is not None:
            self._progress_callback(message, current, total)

    def _check_cancelled(self) -> None:
        if self._cancel_callback is not None and self._cancel_callback():
            raise StaticDataCancelled("Static data operation cancelled")

    async def _download_if_missing(self, relative_path: str) -> None:
        self._check_cancelled()
        target = self._cache_root / relative_path
        if self._read_json(relative_path) is not None:
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self._data_base()}/{relative_path}")
            response.raise_for_status()
            validate_json_bytes(response.content)
            self._check_cancelled()
            self._write_bytes_atomic(target, response.content)

    async def _download_game_if_missing(self, relative_path: str) -> None:
        self._check_cancelled()
        target = self._cache_root / "game" / relative_path
        if self._read_json(f"game/{relative_path}") is not None:
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        url = COMMUNITY_DRAGON_GAME_BASE.replace("/latest/", f"/{self._version}/")
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{url}/{relative_path}")
            response.raise_for_status()
            validate_json_bytes(response.content)
            self._check_cancelled()
            self._write_bytes_atomic(target, response.content)

    async def _load_champion_bin(self, alias: str) -> dict[str, Any] | None:
        if not alias:
            return None

        self._check_cancelled()
        normalized_alias = alias.lower()
        relative_path = f"bin/{normalized_alias}.json"
        target = self._cache_root / relative_path
        data = self._read_json(relative_path)
        if data is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            url = (
                f"https://raw.communitydragon.org/{self._version}/game/data/characters/"
                f"{normalized_alias}/{normalized_alias}.bin.json"
            )
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    validate_json_bytes(response.content)
                self._write_bytes_atomic(target, response.content)
            except (httpx.HTTPError, OSError, UnicodeDecodeError, ValueError):
                return None
            data = self._read_json(relative_path)

        return data if isinstance(data, dict) else None

    async def _load_champion_tooltips(
        self,
        alias: str,
        bin_data: dict[str, Any] | None,
        spells: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, dict[str, str]]:
        if not alias:
            return None, {}

        self._check_cancelled()
        entries = await self._load_string_table()
        if not entries:
            return None, {}

        spell_names = spell_names_by_slot_from_champion_spells(spells or [])
        bin_tooltip_keys = tooltip_keys_from_bin(bin_data, alias, spell_names)
        tooltip_text_index = self._tooltip_text_index(entries)
        passive_keys = bin_tooltip_keys.get("P", [])
        spell_keys = {slot: bin_tooltip_keys.get(slot, []) for slot in ("Q", "W", "E", "R")}
        key_alias = normalize_string_key(alias)
        passive_match = first_string_table_match_with_key(
            entries,
            passive_keys
            + passive_fallback_keys(entries, key_alias)
            + [
                f"generatedtip_passive_{key_alias}passive_tooltipextended",
                f"generatedtip_passive_{key_alias}passive_tooltip",
                f"generatedtip_passive_{key_alias}p_tooltipextended",
                f"generatedtip_passive_{key_alias}p_tooltip",
                f"spell_{key_alias}p_tooltipextended",
                f"spell_{key_alias}p_tooltip",
                f"spell_{key_alias}passive_tooltip",
                f"spell_{key_alias}passive_summary",
            ],
        )
        passive = (
            TooltipText(passive_match.text, passive_match.source_key)
            if passive_match
            else None
        )
        spell_tooltips = {
            slot.upper(): TooltipText(tooltip.text, tooltip.source_key)
            for slot in ("q", "w", "e", "r")
            if (
                tooltip := first_string_table_match_with_key(
                    entries,
                    spell_keys.get(slot.upper(), [])
                    + [
                        f"generatedtip_spell_{key_alias}{slot}_tooltipcontent",
                        f"spell_{key_alias}{slot}_tooltip",
                    ],
                )
            )
        }
        for spell in spells or []:
            if not isinstance(spell, dict):
                continue
            slot = str(spell.get("spellKey") or "").upper()
            if slot not in {"Q", "W", "E", "R"}:
                continue
            raw_text = string_or_none(spell.get("dynamicDescription"))
            if raw_text is None:
                continue
            match = tooltip_text_index.get(tooltip_text_signature(raw_text))
            if match:
                spell_tooltips[slot] = TooltipText(match.text, match.source_key)
            elif slot not in spell_tooltips:
                spell_tooltips[slot] = TooltipText(raw_text)
        return passive, spell_tooltips

    def _tooltip_text_index(self, entries: dict[str, str]) -> dict[str, StringTableMatch]:
        if self._tooltip_text_index_cache is None:
            self._tooltip_text_index_cache = build_tooltip_text_index(entries)
        return self._tooltip_text_index_cache

    async def _load_string_table(self) -> dict[str, str]:
        if self._string_table_cache is not None:
            return self._string_table_cache

        self._check_cancelled()
        self._report_progress("Loading tooltip text", 0, 1)
        relative_path = "game/en_us/data/menu/en_us/lol.stringtable.json"
        target = self._cache_root / relative_path
        data = self._read_json(relative_path)
        if data is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            url = (
                COMMUNITY_DRAGON_GAME_BASE.replace("/latest/", f"/{self._version}/")
                + "/en_us/data/menu/en_us/lol.stringtable.json"
            )
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    validate_json_bytes(response.content)
                self._write_bytes_atomic(target, response.content)
            except (httpx.HTTPError, OSError, UnicodeDecodeError, ValueError):
                self._string_table_cache = {}
                self._report_progress("Loading tooltip text", 1, 1)
                return {}
            data = self._read_json(relative_path)

        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            self._string_table_cache = {}
            return {}

        self._string_table_cache = {
            str(key).lower(): str(value)
            for key, value in entries.items()
            if isinstance(value, str)
        }
        self._report_progress("Loading tooltip text", 1, 1)
        return self._string_table_cache

    def _read_json(self, relative_path: str) -> Any:
        path = self._cache_root / relative_path
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            LOGGER.warning("Ignoring corrupt cached JSON: %s", path, exc_info=True)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _item_bin_lookup(self) -> dict[int, dict[str, Any]]:
        if self._item_bin_cache is None:
            data = self._read_json("game/items.cdtb.bin.json")
            self._item_bin_cache = data if isinstance(data, dict) else {}
        lookup: dict[int, dict[str, Any]] = {}
        for value in self._item_bin_cache.values():
            if not isinstance(value, dict):
                continue
            item_id = value.get("itemID")
            if isinstance(item_id, int):
                lookup[item_id] = value
        return lookup

    def _data_base(self) -> str:
        return COMMUNITY_DRAGON_DATA_BASE.replace("/latest/", f"/{self._version}/")

    def _write_bytes_atomic(self, target: Any, data: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.tmp")
        try:
            temp.write_bytes(data)
            temp.replace(target)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _write_text_atomic(self, target: Any, text: str) -> None:
        self._write_bytes_atomic(target, text.encode("utf-8"))


def normalize_lookup_key(value: str | None) -> str:
    if not value:
        return ""
    return "".join(character.lower() for character in value if character.isalnum())


def normalize_string_key(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def asset_paths_for_champion(champion: ChampionData) -> list[str]:
    paths = [champion.icon_path]
    if champion.passive:
        paths.append(champion.passive.get("abilityIconPath"))
    for spell in champion.spells:
        paths.append(spell.get("abilityIconPath"))
    return [path for path in paths if isinstance(path, str)]


def is_newer_cdragon_version(live_version: str | None, cached_version: str | None) -> bool:
    if not live_version or not cached_version:
        return False

    live_key = cdragon_version_key(live_version)
    cached_key = cdragon_version_key(cached_version)
    if live_key and cached_key:
        return live_key > cached_key

    return live_version != cached_version


def cdragon_version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def validate_json_bytes(data: bytes) -> None:
    json.loads(data.decode("utf-8"))


def asset_file_looks_valid(path: Any) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return asset_header_looks_valid(path, header)


def asset_bytes_look_valid(path: Any, data: bytes) -> bool:
    if not data:
        return False
    return asset_header_looks_valid(path, data[:16])


def asset_header_looks_valid(path: Any, header: bytes) -> bool:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8")
    if suffix == ".webp":
        return header.startswith(b"RIFF") and b"WEBP" in header
    return bool(header)


def tooltip_keys_from_bin(
    bin_data: dict[str, Any] | None,
    alias: str,
    spell_names_by_slot: dict[str, set[str]] | None = None,
) -> dict[str, list[str]]:
    if not isinstance(bin_data, dict):
        return {}

    keys_by_slot: dict[str, list[tuple[tuple[int, int, int], int, list[str]]]] = {
        "P": [],
        "Q": [],
        "W": [],
        "E": [],
        "R": [],
    }
    normalized_alias = normalize_string_key(alias)
    spell_names_by_slot = normalized_spell_names_by_slot(spell_names_by_slot or {})
    for order, (key, value) in enumerate(bin_data.items()):
        if not isinstance(key, str) or not isinstance(value, dict):
            continue

        spell = value.get("mSpell")
        if not isinstance(spell, dict):
            continue
        tooltip_data = tooltip_data_from_spell(spell)
        if not tooltip_data:
            continue
        slot = slot_from_tooltip_data(
            tooltip_data,
            normalized_alias,
            spell_names_by_slot,
        ) or slot_from_bin_key(key, normalized_alias, spell_names_by_slot)
        if slot is None:
            continue
        keys: list[str] = []
        append_tooltip_keys(keys, tooltip_data)
        score = tooltip_candidate_score(
            key,
            value,
            tooltip_data,
            normalized_alias,
            slot,
            spell_names_by_slot,
        )
        keys_by_slot[slot].append((score, -order, keys))

    flattened: dict[str, list[str]] = {"P": [], "Q": [], "W": [], "E": [], "R": []}
    for slot, candidates in keys_by_slot.items():
        for _, _, keys in sorted(candidates, reverse=True):
            for key in keys:
                add_unique(flattened[slot], key)
    return flattened


def slot_from_bin_key(
    key: str,
    normalized_alias: str = "",
    spell_names_by_slot: dict[str, set[str]] | None = None,
) -> str | None:
    lowered = key.lower()
    segments = lowered.split("/")
    normalized_segments = [normalize_string_key(segment) for segment in segments]
    for slot, spell_names in (spell_names_by_slot or {}).items():
        if any(segment_matches_spell_name(segment, spell_names) for segment in normalized_segments):
            return slot

    if any(segment.endswith("passiveability") for segment in segments) or "hemo" in lowered:
        return "P"
    if any(segment in passive_folder_segments(normalized_alias) for segment in normalized_segments):
        return "P"
    for slot in ("q", "w", "e", "r"):
        expected_segments = {f"{slot}ability", f"{slot}wrapperability"}
        if normalized_alias:
            expected_segments.update(
                {
                    f"{normalized_alias}{slot}ability",
                    f"{normalized_alias}{slot}wrapperability",
                }
            )
        if any(segment in expected_segments for segment in normalized_segments):
            return slot.upper()
    return None


def slot_from_tooltip_data(
    tooltip_data: dict[str, Any],
    normalized_alias: str,
    spell_names_by_slot: dict[str, set[str]] | None = None,
) -> str | None:
    loc_keys = tooltip_data.get("mLocKeys")
    if not isinstance(loc_keys, dict):
        return None
    normalized_loc = normalize_string_key(" ".join(str(value) for value in loc_keys.values()))
    if loc_key_matches_passive(loc_keys, normalized_alias):
        return "P"
    for slot in ("q", "w", "e", "r"):
        if f"spell{normalized_alias}{slot}" in normalized_loc:
            return slot.upper()
    for slot, spell_names in (spell_names_by_slot or {}).items():
        if any(f"spell{spell_name}" in normalized_loc for spell_name in spell_names):
            return slot
    return None


def append_tooltip_keys(keys: list[str], tooltip_data: dict[str, Any]) -> None:
    loc_keys = tooltip_data.get("mLocKeys")
    if isinstance(loc_keys, dict):
        for loc_key_name in (
            "keyTooltipExtended",
            "keyTooltip",
            "keyTooltipExtendedBelowLine",
            "keySummary",
        ):
            loc_key = loc_keys.get(loc_key_name)
            if isinstance(loc_key, str) and loc_key.strip():
                add_unique(keys, loc_key)

    object_name = tooltip_data.get("mObjectName")
    if isinstance(object_name, str) and object_name.strip():
        normalized = normalize_string_key(object_name)
        add_unique(keys, f"generatedtip_spell_{normalized}_tooltipcontent")
        add_unique(keys, f"generatedtip_spell_{normalized}_tooltip")
        add_unique(keys, f"generatedtip_passive_{normalized}_tooltipcontent")
        add_unique(keys, f"generatedtip_passive_{normalized}_tooltipextended")
        add_unique(keys, f"generatedtip_passive_{normalized}_tooltip")
        add_unique(keys, f"spell_{normalized}_tooltip")


def loc_key_matches_passive(loc_keys: dict[str, Any], normalized_alias: str) -> bool:
    normalized_values = [
        normalize_string_key(value)
        for value in loc_keys.values()
        if isinstance(value, str)
    ]
    return any(
        value in {
            f"spell{normalized_alias}pname",
            f"spell{normalized_alias}ptooltip",
            f"spell{normalized_alias}ptooltipextended",
            f"spell{normalized_alias}psummary",
            f"spell{normalized_alias}passivename",
            f"spell{normalized_alias}passivetooltip",
            f"spell{normalized_alias}passivetooltipextended",
            f"spell{normalized_alias}passivesummary",
            f"gamecharacterpassivename{normalized_alias}",
            f"gamecharacterpassivetooltip{normalized_alias}",
            f"gamecharacterpassivedescription{normalized_alias}",
        }
        or value.startswith("generatedtippassive")
        or value.startswith("buff") and "passive" in value
        for value in normalized_values
    )


def tooltip_candidate_score(
    key: str,
    value: dict[str, Any],
    tooltip_data: dict[str, Any],
    normalized_alias: str,
    slot: str,
    spell_names_by_slot: dict[str, set[str]] | None = None,
) -> tuple[int, int, int]:
    object_name = (
        value.get("ObjectName")
        or value.get("mScriptName")
        or tooltip_data.get("mObjectName")
    )
    normalized_object = normalize_string_key(object_name) if isinstance(object_name, str) else ""
    loc_keys = tooltip_data.get("mLocKeys")
    loc_text = (
        " ".join(str(value) for value in loc_keys.values())
        if isinstance(loc_keys, dict)
        else ""
    )
    normalized_loc = normalize_string_key(loc_text)
    spell_names = (spell_names_by_slot or {}).get(slot, set())
    expected = f"{normalized_alias}{slot.lower()}"
    if slot == "P":
        exact_names = {f"{normalized_alias}p", f"{normalized_alias}passive"}
        exact_loc = (
            f"spell{normalized_alias}p" in normalized_loc
            or f"spell{normalized_alias}passive" in normalized_loc
        )
    else:
        exact_names = {expected, *spell_names}
        exact_loc = f"spell{expected}" in normalized_loc or any(
            f"spell{spell_name}" in normalized_loc for spell_name in spell_names
        )
    exact_object = normalized_object in exact_names
    child_penalty = any(marker in normalized_object for marker in ("passive", "buff", "missile"))
    folder_match = folder_key_matches_slot(key, slot, normalized_alias, spell_names)
    return (
        2 if exact_object else 1 if exact_loc else 0,
        1 if folder_match else 0,
        0 if child_penalty and not exact_object else 1,
    )


def folder_key_matches_slot(
    key: str,
    slot: str,
    normalized_alias: str = "",
    spell_names: set[str] | None = None,
) -> bool:
    lowered = key.lower()
    segments = lowered.split("/")
    normalized_segments = [normalize_string_key(segment) for segment in segments]
    if any(
        segment_matches_spell_name(segment, spell_names or set())
        for segment in normalized_segments
    ):
        return True
    if slot == "P":
        return (
            any(segment.endswith("passiveability") for segment in segments)
            or any(
                segment in passive_folder_segments(normalized_alias)
                for segment in normalized_segments
            )
        )
    slot_lower = slot.lower()
    expected_segments = {f"{slot_lower}ability", f"{slot_lower}wrapperability"}
    if normalized_alias:
        expected_segments.update(
            {
                f"{normalized_alias}{slot_lower}ability",
                f"{normalized_alias}{slot_lower}wrapperability",
            }
        )
    return any(segment in expected_segments for segment in normalized_segments)


def passive_folder_segments(normalized_alias: str = "") -> set[str]:
    segments = {"pability"}
    if normalized_alias:
        segments.add(f"{normalized_alias}pability")
    return segments


def spell_names_by_slot_from_champion_spells(
    spells: list[dict[str, Any]],
) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {"Q": set(), "W": set(), "E": set(), "R": set()}
    for spell in spells:
        if not isinstance(spell, dict):
            continue
        slot = str(spell.get("spellKey") or "").upper()
        if slot not in names:
            continue
        for key in ("name", "spellName", "scriptName", "mScriptName"):
            value = spell.get(key)
            if isinstance(value, str) and (normalized := normalize_string_key(value)):
                names[slot].add(normalized)
    return names


def normalized_spell_names_by_slot(
    spell_names_by_slot: dict[str, set[str]],
) -> dict[str, set[str]]:
    return {
        slot.upper(): {
            normalized
            for value in values
            if (normalized := normalize_string_key(value))
        }
        for slot, values in spell_names_by_slot.items()
        if slot.upper() in {"Q", "W", "E", "R"}
    }


def segment_matches_spell_name(segment: str, spell_names: set[str]) -> bool:
    for spell_name in spell_names:
        if segment in {
            spell_name,
            f"{spell_name}ability",
            f"{spell_name}wrapperability",
        }:
            return True
        if segment.endswith(f"{spell_name}ability") or segment.endswith(
            f"{spell_name}wrapperability"
        ):
            return True
    return False


def add_unique(values: list[str], value: str) -> None:
    normalized = value.lower()
    if normalized not in {existing.lower() for existing in values}:
        values.append(value)


def passive_fallback_keys(entries: dict[str, str], key_alias: str) -> list[str]:
    candidates = []
    prefixes = (
        f"generatedtip_passive_{key_alias}",
            f"spell_{key_alias}p_",
        f"spell_{key_alias}passive",
    )
    for key in entries:
        if not key.startswith(prefixes):
            continue
        if not any(marker in key for marker in ("tooltip", "tooltipextended")):
            continue
        if any(marker in key for marker in ("summary", "description", "simple")):
            continue
        add_unique(candidates, key)

    return sorted(
        candidates,
        key=lambda key: (
            0 if "tooltipextended" in key else 1,
            0 if key.startswith("generatedtip_passive_") else 1,
            len(key),
        ),
    )


def first_string_table_match(entries: dict[str, str], keys: list[str]) -> str | None:
    match = first_string_table_match_with_key(entries, keys)
    return match.text if match else None


def first_string_table_match_with_key(
    entries: dict[str, str],
    keys: list[str],
) -> StringTableMatch | None:
    for key in keys:
        raw_text = entries.get(key.lower())
        if not raw_text:
            continue
        resolved = resolve_string_templates(raw_text, entries)
        main_text = main_text_from_tooltip(resolved)
        if main_text and has_real_tooltip_text(main_text):
            return StringTableMatch(main_text, key)
    return None


def build_tooltip_text_index(entries: dict[str, str]) -> dict[str, StringTableMatch]:
    index = {}
    for key, value in entries.items():
        if "tooltip" not in key:
            continue
        resolved = resolve_string_templates(value, entries)
        main_text = main_text_from_tooltip(resolved)
        signature = tooltip_text_signature(main_text)
        if signature and signature not in index:
            index[signature] = StringTableMatch(main_text, key)
    return index


def tooltip_text_signature(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip()).lower()


def string_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def is_summoners_rift_item(raw_item: dict[str, Any]) -> bool:
    item_id = raw_item.get("id")
    if not isinstance(item_id, int):
        return False
    if not 1000 <= item_id < 9000:
        return False
    if raw_item.get("inStore") is not True:
        return False
    if raw_item.get("isEnchantment") is True:
        return False
    if string_or_none(raw_item.get("requiredChampion")):
        return False
    if string_or_none(raw_item.get("requiredAlly")):
        return False
    if raw_item.get("displayInItemSets") is not True:
        return False
    name = string_or_none(raw_item.get("name")) or ""
    if "Healthbar" in name or name.startswith("Health Potion Placeholder"):
        return False
    return True


def best_item_description(
    raw_item: dict[str, Any],
    entries: dict[str, str],
    item_bin: dict[str, Any] | None = None,
) -> str | None:
    candidates = item_description_candidates(raw_item, entries, item_bin)
    if not candidates:
        return string_or_none(raw_item.get("description"))
    return max(candidates, key=lambda candidate: item_description_score(candidate.text)).text


def item_description_candidates(
    raw_item: dict[str, Any],
    entries: dict[str, str],
    item_bin: dict[str, Any] | None = None,
) -> list[ItemDescriptionCandidate]:
    item_id = raw_item.get("id")
    if not isinstance(item_id, int):
        return []

    candidates = []
    raw_description = string_or_none(raw_item.get("description"))
    if raw_description:
        candidates.append(
            ItemDescriptionCandidate("items.json", clean_item_description(raw_description))
        )

    for key in (
        f"generatedtip_item_{item_id}_externaldescription",
        f"generatedtip_item_{item_id}_description",
        f"item_{item_id}_tooltipexternal",
        f"item_{item_id}_tooltip",
        f"game_item_tooltip_{item_id}",
        f"game_item_description_{item_id}",
    ):
        raw_text = entries.get(key.lower())
        if not raw_text:
            continue
        templated_text = resolve_item_templates(raw_text, entries)
        resolved = resolve_item_text(templated_text, item_bin)
        main_text = clean_item_description(main_text_from_tooltip(resolved) or resolved)
        main_text = merge_item_stats_with_effect_text(main_text, candidates)
        if not has_real_tooltip_text(main_text):
            continue
        candidates.append(ItemDescriptionCandidate(key, main_text))

    deduplicated = []
    seen = set()
    for candidate in candidates:
        signature = tooltip_text_signature(candidate.text)
        if signature in seen:
            continue
        seen.add(signature)
        deduplicated.append(candidate)
    return deduplicated


def item_description_score(text: str) -> tuple[int, int, int, int, int, int, int]:
    return (
        1 if not has_generic_effect_text(text) else 0,
        1 if has_item_stats(text) else 0,
        1 if not RAW_PLACEHOLDER_PATTERN.search(text) else 0,
        1 if has_effect_text(text) else 0,
        1 if "melee" in text.lower() and "ranged" in text.lower() else 0,
        len(re.findall(r"<[^/!][^>]*>", text)),
        len(text),
    )


def has_real_tooltip_text(text: str) -> bool:
    stripped = re.sub(r"@[A-Za-z0-9_:.+*\-/]+@", "", text)
    stripped = re.sub(r"<[^>]+>", "", stripped)
    return bool(stripped.strip())


def has_generic_effect_text(text: str) -> bool:
    visible_text = re.sub(r"<[^>]+>", " ", text).lower()
    visible_text = re.sub(r"\s+", " ", visible_text)
    return any(
        generic_text in visible_text
        for generic_text in (
            "bonus magic damage",
            "bonus physical damage",
            "deals magic damage",
            "deals physical damage",
            "restores health",
            "damage they take is reduced.",
            "take increased damage",
            "gain move speed",
            "for seconds",
        )
    )


def has_item_stats(text: str) -> bool:
    lowered = text.lower()
    if "<stats" in lowered:
        return True
    if "<attention" not in lowered:
        return False
    visible_text = re.sub(r"<[^>]+>", " ", text).lower()
    return any(
        stat_name in visible_text
        for stat_name in (
            "attack damage",
            "ability power",
            "health",
            "armor",
            "magic resist",
            "lethality",
            "move speed",
            "gold per 10",
        )
    )


def clean_item_description(text: str) -> str:
    text = re.sub(
        r"(\d+(?:\.\d+)?)\s+Melee\s*/\s*(\d+(?:\.\d+)?)\s+Ranged%",
        r"\1% Melee / \2% Ranged",
        text,
    )
    text = re.sub(
        r"<section>\s*<rules\b.*?</section>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<section>\s*<flavorText\b.*?</section>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<section>\s*</section>", "", text, flags=re.IGNORECASE)
    return text.strip()


def merge_item_stats_with_effect_text(
    text: str,
    existing_candidates: list[ItemDescriptionCandidate],
) -> str:
    if has_item_stats(text):
        return text
    if not has_effect_text(text):
        return text
    if RAW_PLACEHOLDER_PATTERN.search(text):
        return text
    stats_text = best_item_stats_text(existing_candidates)
    if not stats_text:
        return text
    if redundant_item_effect(text, stats_text):
        return text
    return f"{stats_text}<br><br>{text}"


def redundant_item_effect(text: str, stats_text: str) -> bool:
    text_visible = re.sub(r"<[^>]+>", " ", text).lower()
    text_visible = re.sub(r"\s+", " ", text_visible)
    stats_visible = re.sub(r"<[^>]+>", " ", stats_text).lower()
    stats_visible = re.sub(r"\s+", " ", stats_visible)
    return "omnivamp" in stats_visible and "damage dealt as health" in text_visible


def has_effect_text(text: str) -> bool:
    lowered = text.lower()
    return "<passive" in lowered or "<active" in lowered


def best_item_stats_text(candidates: list[ItemDescriptionCandidate]) -> str | None:
    stats_candidates = []
    for candidate in candidates:
        stats_text = item_stats_text(candidate.text)
        if stats_text:
            stats_candidates.append(stats_text)
    if not stats_candidates:
        return None
    return max(stats_candidates, key=len)


def item_stats_text(text: str) -> str | None:
    main_text = main_text_from_tooltip(text) or text
    stats_match = re.search(
        r"<stats\b[^>]*>.*?</stats>",
        main_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if stats_match:
        return stats_match.group(0)
    section_match = re.search(
        r"<section\b[^>]*>.*?</section>",
        main_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if section_match and has_item_stats(section_match.group(0)):
        return section_match.group(0)
    return None


def resolve_item_text(text: str, item_bin: dict[str, Any] | None) -> str:
    if not isinstance(item_bin, dict):
        return text

    resolved = expand_item_range_split_templates(text)
    data_values = item_data_values(item_bin)
    calculations = resolved_item_calculations(item_calculations(item_bin), data_values)
    spell_bin = SpellBinData(
        data_values=data_values,
        calculations=calculations,
        effect_amounts=item_effect_amounts(item_bin),
    )

    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(0).strip("@")
        resolved_placeholder = spell_bin.resolve_placeholder(placeholder)
        return resolved_placeholder if resolved_placeholder else match.group(0)

    resolved = RAW_PLACEHOLDER_PATTERN.sub(replace, resolved)
    return ITEM_ICON_PATTERN.sub("", resolved)


def expand_item_range_split_templates(text: str) -> str:
    def _replacer(match: re.Match[str]) -> str:
        suffix = (match.group(1) or "").upper().lstrip("_")  # "B", "C", "D", or ""
        melee_key = f"MeleeItemCalcValue{suffix}"
        ranged_key = f"RangedItemCalcValue{suffix}"
        return f"@{melee_key}@ Melee / @{ranged_key}@ Ranged"

    return ITEM_RANGE_SPLIT_PATTERN.sub(_replacer, text)


def resolve_item_templates(text: str, entries: dict[str, str]) -> str:
    text = expand_item_range_split_templates(text)
    return expand_item_range_split_templates(resolve_string_templates(text, entries))


def item_data_values(item_bin: dict[str, Any]) -> dict[str, list[float]]:
    data_values: dict[str, list[float]] = {}
    for key, value in item_bin.items():
        if not isinstance(key, str) or not isinstance(value, int | float):
            continue
        data_values[key] = [float(value)]
        if key.startswith("m") and len(key) > 1:
            data_values[key.removeprefix("m")] = [float(value)]
    if "PercentOmnivampMod" in data_values:
        data_values.setdefault("HealMultiplier", data_values["PercentOmnivampMod"])
        data_values.setdefault("HealingReduction", [0.33])
    for raw_value in item_bin.get("mDataValues") or []:
        if not isinstance(raw_value, dict):
            continue
        name = raw_value.get("mName")
        value = raw_value.get("mValue")
        if isinstance(name, str) and isinstance(value, int | float):
            data_values[name] = [float(value)]
    return data_values


def item_effect_amounts(item_bin: dict[str, Any]) -> dict[str, list[float]]:
    raw_effects = item_bin.get("mEffectAmount")
    if not isinstance(raw_effects, list):
        return {}
    amounts = {}
    for index, raw_effect in enumerate(raw_effects, start=1):
        if isinstance(raw_effect, int | float):
            amounts[str(index)] = [float(raw_effect)]
        elif isinstance(raw_effect, dict):
            value = raw_effect.get("value")
            if isinstance(value, int | float):
                amounts[str(index)] = [float(value)]
    return amounts


def item_calculations(item_bin: dict[str, Any]) -> dict[str, Any]:
    calculations = item_bin.get("mItemCalculations")
    return calculations if isinstance(calculations, dict) else {}


def resolved_item_calculations(
    calculations: dict[str, Any],
    data_values: dict[str, list[float]],
) -> dict[str, Any]:
    resolved = {}
    for key, calculation in calculations.items():
        resolved[key] = resolve_item_calculation_value(calculation, key, data_values)
    return resolved


def resolve_item_calculation_value(
    value: Any,
    calculation_key: str,
    data_values: dict[str, list[float]],
) -> Any:
    if isinstance(value, list):
        return [
            resolve_item_calculation_value(item, calculation_key, data_values)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    resolved = {
        key: resolve_item_calculation_value(child, calculation_key, data_values)
        for key, child in value.items()
    }
    data_value = resolved.get("mDataValue")
    if isinstance(data_value, str) and re.fullmatch(r"\{[0-9a-f]{8}\}", data_value):
        replacement = item_data_value_for_calculation(calculation_key, data_values)
        if replacement:
            resolved["mDataValue"] = replacement
    return resolved


def item_data_value_for_calculation(
    calculation_key: str,
    data_values: dict[str, list[float]],
) -> str | None:
    candidates = (
        calculation_key,
        f"{calculation_key}ndv",
        f"Base{calculation_key}",
    )
    normalized_values = {key.lower(): key for key in data_values}
    for candidate in candidates:
        key = normalized_values.get(candidate.lower())
        if key:
            return key
    return None


def resolve_string_templates(text: str, entries: dict[str, str], depth: int = 0) -> str:
    if depth >= 5:
        return text

    def replace(match: re.Match[str]) -> str:
        key = normalize_template_key(match.group(1).strip()).lower()
        replacement = entries.get(key)
        if not replacement:
            return ""
        return resolve_string_templates(replacement, entries, depth + 1)

    resolved = STRING_TEMPLATE_PATTERN.sub(replace, text)
    return re.sub(r"{{\s*([^}]+)\s*}}", replace, resolved)


def normalize_template_key(key: str) -> str:
    return re.sub(r"@[A-Za-z0-9_:.+*\-/]+@", "1", key)


def main_text_from_tooltip(text: str) -> str | None:
    match = MAIN_TEXT_PATTERN.search(text)
    if match:
        text = match.group(1)
    text = text.strip()
    return text or None


class TooltipText(str):
    def __new__(cls, value: str, source_key: str | None = None) -> TooltipText:
        instance = str.__new__(cls, value)
        instance.source_key = source_key
        return instance

    source_key: str | None


@dataclass(frozen=True)
class StringTableMatch:
    text: str
    source_key: str
