from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from mapping_preset_io import MapTab


def is_branch_tab(tab: MapTab) -> bool:
    return not tab.image_path


def is_leaf_tab(tab: MapTab) -> bool:
    return bool(tab.image_path)


def tab_at_path(tabs: list[MapTab], path: list[int]) -> MapTab | None:
    current: list[MapTab] = tabs
    tab: MapTab | None = None
    for index in path:
        if index < 0 or index >= len(current):
            return None
        tab = current[index]
        current = tab.children
    return tab


def siblings_at_depth(tabs: list[MapTab], path: list[int], depth: int) -> list[MapTab]:
    if depth == 0:
        return tabs
    parent = tab_at_path(tabs, path[:depth])
    if parent is None:
        return []
    return parent.children


def resolve_leaf_path(tabs: list[MapTab], path: list[int]) -> list[int]:
    resolved = list(path)
    while True:
        tab = tab_at_path(tabs, resolved)
        if tab is None or is_leaf_tab(tab):
            return resolved
        if not tab.children:
            return resolved
        resolved.append(0)


def iter_leaf_tabs(tabs: list[MapTab]) -> Iterator[MapTab]:
    for tab in tabs:
        if is_branch_tab(tab):
            yield from iter_leaf_tabs(tab.children)
        else:
            yield tab


def path_to_label(tabs: list[MapTab], path: list[int]) -> str:
    parts: list[str] = []
    for depth in range(len(path)):
        sibling_list = siblings_at_depth(tabs, path, depth)
        index = path[depth]
        if 0 <= index < len(sibling_list):
            parts.append(sibling_list[index].name)
    return " / ".join(parts)


def validate_tab_tree(tabs: list[MapTab], *, parent_label: str = "preset") -> None:
    names = [tab.name.strip() for tab in tabs]
    if any(not name for name in names):
        raise ValueError(f"Tab names cannot be empty under {parent_label}.")
    if len(names) != len(set(names)):
        raise ValueError(f"Sibling tab names must be unique under {parent_label}.")

    for tab in tabs:
        if is_branch_tab(tab):
            if tab.markers:
                raise ValueError(f"Folder tab '{tab.name}' cannot have a map image or markers.")
            if not tab.children:
                raise ValueError(f"Folder tab '{tab.name}' must contain at least one child tab.")
            validate_tab_tree(tab.children, parent_label=tab.name)
        elif not tab.image_path:
            raise ValueError(
                f"Tab '{tab.name}' must be either a folder with child tabs or a map with a background image."
            )


def path_from_tree_id(tree_id: str) -> list[int]:
    if not tree_id:
        return []
    return [int(part) for part in tree_id.split("/")]


def tree_id_from_path(path: list[int]) -> str:
    return "/".join(str(index) for index in path)
