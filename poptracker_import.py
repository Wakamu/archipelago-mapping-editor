from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from zipfile import ZipFile, is_zipfile

from mapping_preset_io import MapTab, Marker, MappingPreset
from tab_tree import iter_leaf_tabs

_PROXY_SUFFIX_RE = re.compile(r" \[[^\]]+\]$")
_CANREACH_RE = re.compile(r"\$CanReach\|([^}\]|]+)")


@dataclass
class PoptrackerImportStats:
    tab_count: int = 0
    marker_count: int = 0
    location_names: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PoptrackerImportResult:
    preset: MappingPreset
    temp_dir: tempfile.TemporaryDirectory[str]
    stats: PoptrackerImportStats


@dataclass
class LayoutTabDef:
    name: str
    map_ids: list[str] = field(default_factory=list)
    children: list[LayoutTabDef] = field(default_factory=list)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def find_pack_root(root: Path) -> Path:
    if (root / "maps" / "maps.json").is_file() or (root / "locations").is_dir():
        return root
    for child in sorted(root.iterdir()):
        if child.is_dir() and ((child / "maps" / "maps.json").is_file() or (child / "locations").is_dir()):
            return child
    raise ValueError("Could not find a PopTracker pack (expected maps/maps.json or locations/).")


def resolve_pack_root(path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    path = path.resolve()
    if path.is_dir():
        return find_pack_root(path), None
    if is_zipfile(path):
        temp_dir = tempfile.TemporaryDirectory(prefix="poptracker_pack_")
        with ZipFile(path) as archive:
            archive.extractall(temp_dir.name)
        return find_pack_root(Path(temp_dir.name)), temp_dir
    raise ValueError("PopTracker pack must be a folder or zip archive.")


def read_pack_game_name(pack_root: Path) -> str:
    manifest_path = pack_root / "manifest.json"
    if not manifest_path.is_file():
        return ""
    manifest = _read_json(manifest_path)
    return str(manifest.get("game_name") or manifest.get("name") or "").strip()


def load_poptracker_locations(pack_root: Path) -> list[dict]:
    locations_dir = pack_root / "locations"
    if not locations_dir.is_dir():
        raise ValueError("PopTracker pack is missing a locations/ folder.")
    entries: list[dict] = []
    for path in sorted(locations_dir.rglob("*.json")):
        data = _read_json(path)
        if isinstance(data, list):
            entries.extend(data)
    if not entries:
        raise ValueError("PopTracker pack has no location entries.")
    return entries


def iter_location_nodes(entries: list[dict]):
    for entry in entries:
        yield entry
        for child in entry.get("children", []):
            yield from iter_location_nodes([child])


def load_poptracker_maps(pack_root: Path) -> dict[str, dict]:
    maps_path = pack_root / "maps" / "maps.json"
    if not maps_path.is_file():
        raise ValueError("PopTracker pack is missing maps/maps.json.")
    maps = _read_json(maps_path)
    if not isinstance(maps, list):
        raise ValueError("maps/maps.json must contain a list of maps.")
    return {entry["name"]: entry for entry in maps if entry.get("name")}


def _layout_tab_from_node(tab: dict) -> LayoutTabDef | None:
    if not isinstance(tab, dict):
        return None

    title = str(tab.get("title") or "").strip() or "Map"
    content = tab.get("content")
    if not isinstance(content, dict):
        return None

    content_type = content.get("type")
    if content_type == "map":
        map_ids = [str(map_id) for map_id in content.get("maps", []) if map_id]
        if not map_ids:
            return None
        return LayoutTabDef(name=title, map_ids=map_ids)

    if content_type == "tabbed":
        children: list[LayoutTabDef] = []
        for subtab in content.get("tabs", []):
            child = _layout_tab_from_node(subtab)
            if child is not None:
                children.append(child)
        if not children:
            return None
        return LayoutTabDef(name=title, children=children)

    nested = _layout_collect_tabs(content)
    if not nested:
        return None
    if len(nested) == 1:
        return nested[0]
    return LayoutTabDef(name=title, children=nested)


def _layout_collect_tabs(node: object) -> list[LayoutTabDef]:
    if not isinstance(node, dict):
        return []

    node_type = str(node.get("type") or "")
    if node_type == "tabbed":
        tabs: list[LayoutTabDef] = []
        for tab in node.get("tabs", []):
            parsed = _layout_tab_from_node(tab)
            if parsed is not None:
                tabs.append(parsed)
        return tabs

    results: list[LayoutTabDef] = []
    content = node.get("content")
    if isinstance(content, dict):
        results.extend(_layout_collect_tabs(content))
    elif isinstance(content, list):
        for item in content:
            results.extend(_layout_collect_tabs(item))

    for child in node.get("children", []):
        if isinstance(child, dict):
            results.extend(_layout_collect_tabs(child))

    return results


def load_layout_tab_tree(pack_root: Path) -> list[LayoutTabDef]:
    layouts_dir = pack_root / "layouts"
    if not layouts_dir.is_dir():
        return []

    for layout_name in ("tracker.json", "tracker_layouts.json"):
        layout_path = layouts_dir / layout_name
        if not layout_path.is_file():
            continue
        layout = _read_json(layout_path)
        root = layout.get("tracker_default", layout)
        tabs = _layout_collect_tabs(root)
        if tabs:
            return tabs
    return []


def load_map_layout_tab_defs(pack_root: Path) -> list[tuple[str, str, str]]:
    layout_path = pack_root / "tools" / "map_layout.json"
    if not layout_path.is_file():
        return []
    layout = _read_json(layout_path)
    tabs: list[tuple[str, str, str]] = []
    for map_def in layout.get("maps", []):
        map_id = str(map_def.get("id") or "").strip()
        if not map_id:
            continue
        title = str(map_def.get("title") or map_id).strip() or map_id
        image = str(map_def.get("image") or "").strip()
        tabs.append((map_id, title, image))
    return tabs


def extract_section_names(entry: dict) -> list[str]:
    names: list[str] = []
    for section in entry.get("sections", []):
        if "name" in section and section["name"]:
            names.append(str(section["name"]))
        elif "ref" in section and section["ref"]:
            ref = str(section["ref"])
            names.append(ref.split("/", 1)[1] if "/" in ref else ref)
        elif "hosted_item" in section and section["hosted_item"]:
            names.append(str(section["hosted_item"]))
        else:
            for rule in section.get("access_rules", []):
                match = _CANREACH_RE.search(str(rule))
                if match:
                    names.append(match.group(1).strip())
                    break
    return names


def marker_label(entry_name: str) -> str:
    return _PROXY_SUFFIX_RE.sub("", entry_name).strip()


def build_markers_by_map(locations: list[dict]) -> dict[str, list[dict]]:
    markers_by_map: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple] = set()

    for entry in iter_location_nodes(locations):
        names = extract_section_names(entry)
        if not names:
            continue
        label = marker_label(str(entry.get("name") or names[0])) if len(names) > 1 else None

        for map_loc in entry.get("map_locations", []):
            map_id = str(map_loc.get("map") or "").strip()
            if not map_id:
                continue
            marker_key = (
                map_id,
                int(map_loc["x"]),
                int(map_loc["y"]),
                tuple(sorted(names)),
                label,
                map_loc.get("size"),
            )
            if marker_key in seen:
                continue
            seen.add(marker_key)

            marker = {
                "x": int(map_loc["x"]),
                "y": int(map_loc["y"]),
                "locations": list(names),
                "label": label,
                "size": map_loc.get("size"),
            }
            markers_by_map[map_id].append(marker)

    return markers_by_map


def _tab_title_for_map(title: str, map_id: str, map_ids: list[str]) -> str:
    if len(map_ids) == 1:
        return title
    return f"{title} ({map_id})"


def _resolve_image_path(pack_root: Path, map_def: dict, map_layout_image: str | None) -> Path | None:
    candidates: list[Path] = []
    if map_layout_image:
        candidates.append(pack_root / map_layout_image)
    img = map_def.get("img")
    if img:
        candidates.append(pack_root / str(img))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _copy_image(source: Path, dest_dir: Path, used_names: dict[str, int]) -> Path:
    stem = source.stem or "map"
    suffix = source.suffix or ".png"
    used_names[stem] = used_names.get(stem, 0) + 1
    count = used_names[stem]
    filename = f"{stem}{'' if count == 1 else f'_{count}'}{suffix}"
    destination = dest_dir / filename
    shutil.copy2(source, destination)
    return destination


def _marker_from_dict(marker_dict: dict) -> Marker:
    locations = list(marker_dict["locations"])
    label = marker_dict.get("label")
    size = marker_dict.get("size")
    if size is not None:
        size = int(size)
    return Marker(
        x=int(marker_dict["x"]),
        y=int(marker_dict["y"]),
        locations=locations,
        label=label,
        size=size,
    )


def _build_leaf_map_tab(
    tab_name: str,
    map_id: str,
    *,
    pack_root: Path,
    maps_by_name: dict[str, dict],
    markers_by_map: dict[str, list[dict]],
    map_layout_defs: dict[str, tuple[str, str]],
    image_dir: Path,
    used_names: dict[str, int],
    stats: PoptrackerImportStats,
) -> MapTab | None:
    marker_dicts = markers_by_map.get(map_id)
    if not marker_dicts:
        return None

    map_def = maps_by_name.get(map_id)
    if map_def is None:
        stats.warnings.append(f"Skipped tab '{tab_name}': unknown map id '{map_id}'.")
        return None

    layout_image = map_layout_defs.get(map_id, ("", ""))[1] or None
    source_image = _resolve_image_path(pack_root, map_def, layout_image)
    if source_image is None:
        stats.warnings.append(f"Skipped tab '{tab_name}': image not found for map '{map_id}'.")
        return None

    local_image = _copy_image(source_image, image_dir, used_names)
    location_size = int(map_def.get("location_size") or 32)
    markers = [_marker_from_dict(marker_dict) for marker_dict in marker_dicts]
    for marker in markers:
        stats.location_names.update(marker.locations)

    return MapTab(
        name=tab_name,
        image_path=str(local_image),
        markers=markers,
        location_size=location_size,
    )


def _build_map_tab_from_layout(
    tab_def: LayoutTabDef,
    *,
    pack_root: Path,
    maps_by_name: dict[str, dict],
    markers_by_map: dict[str, list[dict]],
    map_layout_defs: dict[str, tuple[str, str]],
    image_dir: Path,
    used_names: dict[str, int],
    stats: PoptrackerImportStats,
) -> MapTab | None:
    if tab_def.children:
        children: list[MapTab] = []
        for child_def in tab_def.children:
            child_tab = _build_map_tab_from_layout(
                child_def,
                pack_root=pack_root,
                maps_by_name=maps_by_name,
                markers_by_map=markers_by_map,
                map_layout_defs=map_layout_defs,
                image_dir=image_dir,
                used_names=used_names,
                stats=stats,
            )
            if child_tab is not None:
                children.append(child_tab)
        if not children:
            return None
        return MapTab(name=tab_def.name, children=children)

    map_ids = tab_def.map_ids
    if not map_ids:
        return None
    if len(map_ids) == 1:
        return _build_leaf_map_tab(
            tab_def.name,
            map_ids[0],
            pack_root=pack_root,
            maps_by_name=maps_by_name,
            markers_by_map=markers_by_map,
            map_layout_defs=map_layout_defs,
            image_dir=image_dir,
            used_names=used_names,
            stats=stats,
        )

    leaves: list[MapTab] = []
    for map_id in map_ids:
        leaf_name = _tab_title_for_map(tab_def.name, map_id, map_ids)
        leaf_tab = _build_leaf_map_tab(
            leaf_name,
            map_id,
            pack_root=pack_root,
            maps_by_name=maps_by_name,
            markers_by_map=markers_by_map,
            map_layout_defs=map_layout_defs,
            image_dir=image_dir,
            used_names=used_names,
            stats=stats,
        )
        if leaf_tab is not None:
            leaves.append(leaf_tab)
    if not leaves:
        return None
    if len(leaves) == 1:
        leaves[0].name = tab_def.name
        return leaves[0]
    return MapTab(name=tab_def.name, children=leaves)


def import_poptracker_pack(
    pack_path: Path,
    *,
    game: str = "",
    pack_temp: tempfile.TemporaryDirectory[str] | None = None,
) -> PoptrackerImportResult:
    pack_root, extracted_temp = resolve_pack_root(pack_path)
    preset_temp = pack_temp or tempfile.TemporaryDirectory(prefix="mapping_preset_")
    try:
        image_dir = Path(preset_temp.name)
        used_names: dict[str, int] = {}
        stats = PoptrackerImportStats()

        locations = load_poptracker_locations(pack_root)
        maps_by_name = load_poptracker_maps(pack_root)
        markers_by_map = build_markers_by_map(locations)

        layout_tab_tree = load_layout_tab_tree(pack_root)
        map_layout_defs = {
            map_id: (title, image) for map_id, title, image in load_map_layout_tab_defs(pack_root)
        }

        if not layout_tab_tree:
            if map_layout_defs:
                layout_tab_tree = [
                    LayoutTabDef(name=title, map_ids=[map_id])
                    for map_id, (title, _image) in map_layout_defs.items()
                ]
            else:
                layout_tab_tree = [
                    LayoutTabDef(name=map_id, map_ids=[map_id]) for map_id in maps_by_name
                ]

        build_kwargs = {
            "pack_root": pack_root,
            "maps_by_name": maps_by_name,
            "markers_by_map": markers_by_map,
            "map_layout_defs": map_layout_defs,
            "image_dir": image_dir,
            "used_names": used_names,
            "stats": stats,
        }

        preset_tabs: list[MapTab] = []
        for tab_def in layout_tab_tree:
            built_tab = _build_map_tab_from_layout(tab_def, **build_kwargs)
            if built_tab is not None:
                preset_tabs.append(built_tab)

        if not preset_tabs:
            raise ValueError("No map tabs with markers and images could be imported from this pack.")

        pack_game = read_pack_game_name(pack_root)
        if not game:
            game = pack_game

        leaf_tabs = list(iter_leaf_tabs(preset_tabs))
        stats.tab_count = len(leaf_tabs)
        stats.marker_count = sum(len(tab.markers) for tab in leaf_tabs)
        if pack_game and game and pack_game != game:
            stats.warnings.append(
                f"Pack game name '{pack_game}' differs from the open APWorld '{game}'. "
                "Location names will be canonicalized against the APWorld."
            )

        preset = MappingPreset(game=game, tabs=preset_tabs)
        return PoptrackerImportResult(preset=preset, temp_dir=preset_temp, stats=stats)
    finally:
        if extracted_temp is not None:
            extracted_temp.cleanup()
