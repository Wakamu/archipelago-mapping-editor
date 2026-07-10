from __future__ import annotations

import json
import pathlib
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from zipfile import ZipFile, is_zipfile

from tab_tree import is_branch_tab

_TAB_COUNT_SUFFIX_RE = re.compile(r" \(\d+ tabs?\)$", re.IGNORECASE)
_UNICODE_DASHES = ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212")
_FRACTION_SLASH = "\u2044"


@dataclass
class Marker:
    x: int
    y: int
    locations: list[str]
    label: str | None = None
    size: int | None = None


@dataclass
class MapTab:
    name: str
    image_path: str = ""
    markers: list[Marker] = field(default_factory=list)
    location_size: int = 32
    children: list[MapTab] = field(default_factory=list)


@dataclass
class MappingPreset:
    game: str
    tabs: list[MapTab] = field(default_factory=list)


def _marker_locations(marker: dict) -> list[str]:
    locations = marker.get("locations")
    if locations is None and "location" in marker:
        locations = [marker["location"]]
    return [location for location in (locations or []) if location]


def _slugify_file_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned.strip("_") or "mapping_preset"


def normalize_location_name(name: str) -> str:
    """Normalize location text for matching (unicode dashes, fraction slash, spacing)."""
    name = _TAB_COUNT_SUFFIX_RE.sub("", name.strip())
    name = unicodedata.normalize("NFKC", name)
    name = name.replace(_FRACTION_SLASH, "/")
    for dash in _UNICODE_DASHES:
        name = name.replace(dash, "-")
    return " ".join(name.split())


def build_location_aliases(locations: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for location in locations:
        aliases[location] = location
        aliases[normalize_location_name(location)] = location
    return aliases


def canonicalize_location_name(name: str, locations: list[str]) -> str:
    aliases = build_location_aliases(locations)
    if name in aliases:
        return aliases[name]
    return aliases.get(normalize_location_name(name), name)


def _load_tab_from_manifest(
    manifest_tab: dict,
    archive: ZipFile,
    base: pathlib.Path,
    *,
    fallback_name: str,
) -> MapTab:
    name = manifest_tab.get("name", fallback_name)
    child_manifests = manifest_tab.get("tabs")
    if child_manifests is not None:
        children = [
            _load_tab_from_manifest(child, archive, base, fallback_name=f"Tab {index}")
            for index, child in enumerate(child_manifests, start=1)
        ]
        return MapTab(name=name, children=children)

    image_arc = manifest_tab.get("image", "")
    if not image_arc:
        raise ValueError(f"Tab '{name}' is missing an image.")
    image_name = pathlib.Path(image_arc).name or "tab.png"
    local_image = base / image_name
    if image_name in {path.name for path in base.iterdir()}:
        stem = pathlib.Path(image_name).stem
        suffix = pathlib.Path(image_name).suffix or ".png"
        counter = 2
        while (base / f"{stem}_{counter}{suffix}").exists():
            counter += 1
        local_image = base / f"{stem}_{counter}{suffix}"
    local_image.write_bytes(archive.read(image_arc))
    markers = [
        Marker(
            x=int(marker["x"]),
            y=int(marker["y"]),
            locations=_marker_locations(marker),
            label=marker.get("label"),
            size=marker.get("size"),
        )
        for marker in manifest_tab.get("markers", [])
        if _marker_locations(marker)
    ]
    return MapTab(
        name=name,
        image_path=str(local_image),
        markers=markers,
        location_size=int(manifest_tab.get("location_size") or 32),
    )


def load_preset(path: pathlib.Path) -> tuple[MappingPreset, tempfile.TemporaryDirectory[str]]:
    if not is_zipfile(path):
        raise ValueError("Mapping preset must be a zip archive.")

    temp_dir = tempfile.TemporaryDirectory(prefix="mapping_preset_")
    base = pathlib.Path(temp_dir.name)

    with ZipFile(path) as archive:
        manifest_name = None
        for candidate in ("mapping.json", "preset.json"):
            try:
                archive.getinfo(candidate)
                manifest_name = candidate
                break
            except KeyError:
                continue
        if manifest_name is None:
            raise ValueError("Mapping preset archive must contain mapping.json or preset.json.")

        manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
        tabs = [
            _load_tab_from_manifest(tab, archive, base, fallback_name=f"Tab {index}")
            for index, tab in enumerate(manifest.get("tabs", []), start=1)
        ]

    return MappingPreset(game=manifest.get("game", ""), tabs=tabs), temp_dir


def _archive_image_name(path: list[int], suffix: str) -> str:
    stem = "-".join(str(index) for index in path)
    return f"images/{stem}{suffix}"


def _assign_leaf_archive_images(
    tabs: list[MapTab],
    path_prefix: list[int],
    image_paths: dict[int, str],
    image_bytes_by_path: dict[str, bytes],
) -> None:
    for index, tab in enumerate(tabs, start=1):
        path = [*path_prefix, index]
        if is_branch_tab(tab):
            _assign_leaf_archive_images(tab.children, path, image_paths, image_bytes_by_path)
            continue
        image_source = pathlib.Path(tab.image_path)
        if not image_source.is_file():
            raise FileNotFoundError(f"Tab '{tab.name}' is missing a background image.")
        suffix = image_source.suffix or ".png"
        archive_name = _archive_image_name(path, suffix)
        image_paths[id(tab)] = archive_name
        image_bytes_by_path[archive_name] = image_source.read_bytes()


def _tab_to_manifest(tab: MapTab, image_paths: dict[int, str]) -> dict:
    if is_branch_tab(tab):
        return {
            "name": tab.name,
            "tabs": [_tab_to_manifest(child, image_paths) for child in tab.children],
        }

    return {
        "name": tab.name,
        "image": image_paths[id(tab)],
        "location_size": tab.location_size,
        "markers": [
            {
                "x": marker.x,
                "y": marker.y,
                **({"label": marker.label} if marker.label else {}),
                "locations": list(marker.locations),
                **({"size": marker.size} if marker.size is not None else {}),
            }
            for marker in tab.markers
        ],
    }


def save_preset(preset: MappingPreset, path: pathlib.Path) -> None:
    from tab_tree import validate_tab_tree

    if not preset.tabs:
        raise ValueError("Create at least one tab before saving.")
    validate_tab_tree(preset.tabs)

    save_path = path if path.suffix.lower() == ".zip" else path.with_suffix(".zip")
    image_paths: dict[int, str] = {}
    image_bytes_by_path: dict[str, bytes] = {}
    _assign_leaf_archive_images(preset.tabs, [], image_paths, image_bytes_by_path)

    manifest = {
        "game": preset.game,
        "tabs": [_tab_to_manifest(tab, image_paths) for tab in preset.tabs],
    }

    with ZipFile(save_path, "w") as archive:
        archive.writestr("mapping.json", json.dumps(manifest, indent=2))
        for archive_name, image_bytes in image_bytes_by_path.items():
            archive.writestr(archive_name, image_bytes)


def _find_manifest_path(archive: ZipFile, apworld_path: pathlib.Path) -> str | None:
    candidates = [
        name
        for name in archive.namelist()
        if name.endswith("archipelago.json") and not name.startswith("__MACOSX")
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    root_manifest = "archipelago.json"
    if root_manifest in candidates:
        return root_manifest
    stem_manifest = f"{apworld_path.stem}/archipelago.json"
    if stem_manifest in candidates:
        return stem_manifest
    return sorted(candidates, key=len)[0]


def detect_apworld_world_module(apworld_path: pathlib.Path) -> str:
    """Infer the worlds.* module name from archive layout (manifest optional)."""
    with ZipFile(apworld_path) as archive:
        init_roots = sorted(
            {
                name.split("/", 1)[0]
                for name in archive.namelist()
                if (name.endswith("/__init__.py") or name.endswith("/__init__.pyc")) and name.count("/") == 1
            }
        )
    stem = apworld_path.stem
    if stem in init_roots:
        return stem
    if len(init_roots) == 1:
        return init_roots[0]
    if init_roots:
        return init_roots[0]
    return stem


def read_apworld_manifest(apworld_path: pathlib.Path) -> tuple[str | None, str]:
    """Return (game name or None, world module id).

    Legacy APWorlds without archipelago.json return game=None; resolve the game
    name by loading the world module (same approach Archipelago uses pre-0.7).
    """
    world_module = detect_apworld_world_module(apworld_path)
    with ZipFile(apworld_path) as archive:
        manifest_path = _find_manifest_path(archive, apworld_path)
        if manifest_path is None:
            return None, world_module
        manifest = json.loads(archive.read(manifest_path).decode("utf-8-sig"))
    game = manifest.get("game")
    world_id = manifest.get("id") or pathlib.Path(manifest_path).parent.name or world_module
    return game, world_id if world_id else world_module


def suggest_preset_filename(game: str) -> str:
    return f"{_slugify_file_name(game or 'mapping')}.zip"
