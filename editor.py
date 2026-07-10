#!/usr/bin/env python3
"""Standalone mapping preset editor for Archipelago Visual Tracker."""

from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from apworld_support import (  # noqa: E402
    bootstrap_archipelago,
    custom_worlds_dir,
    ensure_game_loaded,
    get_world_locations,
    resolve_apworld,
    visual_packs_dir,
)
from mapping_preset_io import (  # noqa: E402
    MapTab,
    Marker,
    MappingPreset,
    build_location_aliases,
    canonicalize_location_name,
    load_preset,
    normalize_location_name,
    save_preset,
    suggest_preset_filename,
)
from tab_tree import (  # noqa: E402
    is_branch_tab,
    is_leaf_tab,
    path_from_tree_id,
    path_to_label,
    resolve_leaf_path,
    siblings_at_depth,
    tab_at_path,
    tree_id_from_path,
)

ZOOM_OPTIONS = (("12%", 8), ("25%", 4), ("50%", 2), ("100%", 1))
SNAP_GRID_SIZE = 16
GROUP_FILL = "#daa520"
GROUP_FILL_SELECTED = "#ff8c00"
SINGLE_FILL = "#40c4ff"
SINGLE_FILL_SELECTED = "#00bcd4"
PIN_OUTLINE = "#1a1a1a"
PIN_OUTLINE_SELECTED = "#ffffff"


def load_photo(path: Path, subsample: int = 1) -> tuple[tk.PhotoImage, int, int]:
    """Return a display photo, plus the source image's full pixel width and height."""
    try:
        from PIL import Image, ImageTk

        image = Image.open(path)
        width, height = image.size
        if subsample > 1:
            display_size = (max(1, width // subsample), max(1, height // subsample))
            image = image.resize(display_size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image), width, height
    except Exception:
        source = tk.PhotoImage(file=str(path))
        width, height = source.width(), source.height()
        photo = source.subsample(subsample, subsample) if subsample > 1 else source
        return photo, width, height


class ManageTabsDialog(tk.Toplevel):
    def __init__(self, parent: MappingPresetEditor) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Manage map tabs")
        self.geometry("560x420")
        self.transient(parent)
        self.grab_set()

        ttk.Label(
            self,
            text="Folders group nested map tabs. Map tabs hold a background image and location pins.",
            wraplength=520,
        ).pack(anchor=tk.W, padx=10, pady=(10, 6))

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True, padx=10)
        self.tree = ttk.Treeview(frame, selectmode="browse")
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.refresh()

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(buttons, text="Add map tab...", command=self.add_map_tab).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Add folder...", command=self.add_folder).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Add child...", command=self.add_child).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Rename", command=self.rename_tab).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Move up", command=self.move_tab_up).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Move down", command=self.move_tab_down).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Change image...", command=self.change_image).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Remove", command=self.remove_tab).pack(side=tk.LEFT, padx=6)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side=tk.RIGHT)

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())

        def insert_tabs(parent_id: str, tabs: list[MapTab], path_prefix: list[int]) -> None:
            for index, tab in enumerate(tabs):
                path = [*path_prefix, index]
                tree_id = tree_id_from_path(path)
                prefix = "Folder" if is_branch_tab(tab) else "Map"
                self.tree.insert(parent_id, "end", iid=tree_id, text=f"{prefix}: {tab.name}", open=True)
                if tab.children:
                    insert_tabs(tree_id, tab.children, path)

        insert_tabs("", self.parent.preset.tabs, [])

    def _selected_path(self) -> list[int] | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return path_from_tree_id(selection[0])

    def _parent_tabs(self, path: list[int]) -> list[MapTab]:
        if not path:
            return self.parent.preset.tabs
        parent = tab_at_path(self.parent.preset.tabs, path[:-1])
        if parent is None:
            return self.parent.preset.tabs
        return parent.children

    def _after_structure_change(self, path: list[int] | None = None) -> None:
        if path is not None:
            tab = tab_at_path(self.parent.preset.tabs, path)
            if tab and is_branch_tab(tab) and tab.children:
                self.parent.active_tab_path = resolve_leaf_path(self.parent.preset.tabs, path)
            else:
                self.parent.active_tab_path = path
        else:
            tab = tab_at_path(self.parent.preset.tabs, self.parent.active_tab_path)
            if tab and is_branch_tab(tab) and tab.children:
                self.parent.active_tab_path = resolve_leaf_path(
                    self.parent.preset.tabs,
                    self.parent.active_tab_path,
                )
        self.parent.selected_marker = None
        self.parent.dirty = True
        self.parent._refresh_tab_selectors()
        self.parent._load_active_map_image()
        self.parent._redraw()
        self.refresh()

    def add_map_tab(self) -> None:
        title = simpledialog.askstring("Add map tab", "Tab name:", parent=self)
        if not title or not title.strip():
            return
        image_path = filedialog.askopenfilename(
            parent=self,
            title="Select background image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
        )
        if not image_path:
            return
        self.parent.preset.tabs.append(MapTab(name=title.strip(), image_path=image_path))
        self._after_structure_change([len(self.parent.preset.tabs) - 1])

    def add_folder(self) -> None:
        title = simpledialog.askstring("Add folder", "Folder name:", parent=self)
        if not title or not title.strip():
            return
        self.parent.preset.tabs.append(MapTab(name=title.strip(), children=[]))
        self._after_structure_change([len(self.parent.preset.tabs) - 1])

    def add_child(self) -> None:
        path = self._selected_path()
        if path is None:
            messagebox.showwarning("Add child", "Select a folder tab first.", parent=self)
            return
        parent_tab = tab_at_path(self.parent.preset.tabs, path)
        if parent_tab is None or not is_branch_tab(parent_tab):
            messagebox.showwarning("Add child", "Select a folder tab to add a child under.", parent=self)
            return
        child_kind = messagebox.askyesno(
            "Add child",
            "Add a map tab with a background image?\n\nChoose No to add another folder instead.",
            parent=self,
        )
        title = simpledialog.askstring("Add child", "Child name:", parent=self)
        if not title or not title.strip():
            return
        if child_kind:
            image_path = filedialog.askopenfilename(
                parent=self,
                title="Select background image",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
            )
            if not image_path:
                return
            parent_tab.children.append(MapTab(name=title.strip(), image_path=image_path))
        else:
            parent_tab.children.append(MapTab(name=title.strip(), children=[]))
        child_path = [*path, len(parent_tab.children) - 1]
        self._after_structure_change(child_path)

    def rename_tab(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        tab = tab_at_path(self.parent.preset.tabs, path)
        if tab is None:
            return
        title = simpledialog.askstring("Rename tab", "Tab name:", initialvalue=tab.name, parent=self)
        if not title or not title.strip():
            return
        tab.name = title.strip()
        self._after_structure_change()

    def change_image(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        tab = tab_at_path(self.parent.preset.tabs, path)
        if tab is None or not is_leaf_tab(tab):
            messagebox.showwarning("Change image", "Select a map tab with a background image.", parent=self)
            return
        image_path = filedialog.askopenfilename(
            parent=self,
            title="Select background image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
        )
        if not image_path:
            return
        tab.image_path = image_path
        self._after_structure_change(path)

    def _remap_active_path(self, parent_path: list[int], old_index: int, new_index: int) -> None:
        active = self.parent.active_tab_path
        if len(active) <= len(parent_path) or active[: len(parent_path)] != parent_path:
            return
        sibling_index = active[len(parent_path)]
        if sibling_index == old_index:
            self.parent.active_tab_path = [*active[: len(parent_path)], new_index, *active[len(parent_path) + 1 :]]
        elif sibling_index == new_index:
            self.parent.active_tab_path = [*active[: len(parent_path)], old_index, *active[len(parent_path) + 1 :]]

    def _move_tab(self, direction: int) -> None:
        path = self._selected_path()
        if path is None:
            return
        siblings = self._parent_tabs(path)
        index = path[-1]
        new_index = index + direction
        if new_index < 0 or new_index >= len(siblings):
            return
        siblings[index], siblings[new_index] = siblings[new_index], siblings[index]
        parent_path = path[:-1]
        self._remap_active_path(parent_path, index, new_index)
        new_path = [*parent_path, new_index]
        self._after_structure_change(new_path)
        self.tree.selection_set(tree_id_from_path(new_path))
        self.tree.focus(tree_id_from_path(new_path))

    def move_tab_up(self) -> None:
        self._move_tab(-1)

    def move_tab_down(self) -> None:
        self._move_tab(1)

    def remove_tab(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        tab = tab_at_path(self.parent.preset.tabs, path)
        if tab is None:
            return
        if not messagebox.askyesno("Remove tab", f"Remove tab '{tab.name}'?", parent=self):
            return
        siblings = self._parent_tabs(path)
        siblings.pop(path[-1])
        if not self.parent.preset.tabs:
            self.parent.active_tab_path = []
        else:
            parent_path = path[:-1]
            next_index = min(path[-1], max(0, len(siblings) - 1))
            self.parent.active_tab_path = resolve_leaf_path(
                self.parent.preset.tabs,
                [*parent_path, next_index] if siblings else parent_path or [0],
            )
        self._after_structure_change()


class LocationPickerDialog(tk.Toplevel):
    def __init__(self, parent: MappingPresetEditor, title: str, locations: list[str], *, group: bool = False) -> None:
        super().__init__(parent)
        self.parent = parent
        self.result: Marker | None = None
        self.group = group
        self.tab_usage = parent.location_tab_counts()
        self._filtered_locations: list[str] = []
        self.title(title)
        self.geometry("520x520")
        self.transient(parent)
        self.grab_set()

        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(10, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())
        ttk.Entry(self, textvariable=self.search_var).pack(fill=tk.X, padx=10, pady=(0, 8))

        if group:
            ttk.Label(self, text="Group label (optional):").pack(anchor=tk.W, padx=10)
            self.label_var = tk.StringVar()
            ttk.Entry(self, textvariable=self.label_var).pack(fill=tk.X, padx=10, pady=(0, 8))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        if group:
            self.listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, activestyle="none")
        else:
            self.listbox = tk.Listbox(list_frame, activestyle="none")
        scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.locations = locations
        self._render()

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=10, pady=10)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Add", command=self._confirm).pack(side=tk.RIGHT, padx=(0, 8))
        if not group:
            self.listbox.bind("<Double-Button-1>", lambda _e: self._confirm())

    @staticmethod
    def _format_location_label(location: str, tab_count: int) -> str:
        tab_word = "tab" if tab_count == 1 else "tabs"
        return f"{location} ({tab_count} {tab_word})"

    def _render(self) -> None:
        self.listbox.delete(0, tk.END)
        self._filtered_locations = []
        needle = self.search_var.get().lower()
        for location in self.locations:
            if needle and needle not in location.lower():
                continue
            tab_count = self.tab_usage.get(location, 0)
            self.listbox.insert(tk.END, self._format_location_label(location, tab_count))
            self._filtered_locations.append(location)

    def _confirm(self) -> None:
        if self.group:
            selected = [self._filtered_locations[i] for i in self.listbox.curselection()]
            if not selected:
                messagebox.showwarning("Add group", "Select at least one location.", parent=self)
                return
            label = self.label_var.get().strip() or f"{len(selected)} locations"
            self.result = Marker(x=0, y=0, locations=selected, label=label)
        else:
            selection = self.listbox.curselection()
            if not selection:
                return
            location = self._filtered_locations[selection[0]]
            self.result = Marker(x=0, y=0, locations=[location])
        self.destroy()


class MappingPresetEditor(tk.Tk):
    def __init__(self, game: str | None, locations: list[str], apworld_path: Path | None) -> None:
        super().__init__()
        self.title("Archipelago Mapping Preset Editor")
        self.geometry("1400x900")
        self.minsize(1000, 700)

        self.apworld_path = apworld_path
        self.locations = locations
        self.preset = MappingPreset(game=game or "")
        self.active_tab_path: list[int] = []
        self._tab_selector_vars: list[tk.StringVar] = []
        self.selected_marker: int | None = None
        self.dirty = False
        self._preset_temp = None

        self.map_width = 1
        self.map_height = 1
        self.subsample = 4
        self.scale = 0.25
        self.photo: tk.PhotoImage | None = None
        self.drag_marker: int | None = None
        self.drag_offset = (0.0, 0.0)

        self._build_ui()
        self._update_game_label()
        if self.preset.tabs:
            self._load_active_map_image()
            self._redraw()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @property
    def active_tab(self) -> MapTab | None:
        if not self.preset.tabs:
            return None
        leaf_path = resolve_leaf_path(self.preset.tabs, self.active_tab_path)
        tab = tab_at_path(self.preset.tabs, leaf_path)
        if tab is None or not is_leaf_tab(tab):
            return None
        return tab

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="Open APWorld...", command=self.open_apworld).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        self.new_preset_button = ttk.Button(toolbar, text="New Preset", command=self.new_preset)
        self.new_preset_button.pack(side=tk.LEFT, padx=4)
        self.load_preset_button = ttk.Button(toolbar, text="Load Preset...", command=self.load_preset_dialog)
        self.load_preset_button.pack(side=tk.LEFT, padx=4)
        self.save_preset_button = ttk.Button(toolbar, text="Save Preset...", command=self.save_preset_dialog)
        self.save_preset_button.pack(side=tk.LEFT, padx=4)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(toolbar, text="Tab:").pack(side=tk.LEFT)
        self.tab_selectors_frame = ttk.Frame(toolbar)
        self.tab_selectors_frame.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Button(toolbar, text="Manage tabs...", command=self._manage_tabs).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(toolbar, text="Zoom:").pack(side=tk.LEFT)
        self.zoom_var = tk.StringVar(value="25%")
        zoom_box = ttk.Combobox(
            toolbar,
            textvariable=self.zoom_var,
            values=[label for label, _ in ZOOM_OPTIONS],
            state="readonly",
            width=8,
        )
        zoom_box.pack(side=tk.LEFT, padx=(4, 12))
        zoom_box.bind("<<ComboboxSelected>>", self._on_zoom_changed)

        self.game_label_var = tk.StringVar(value="No APWorld loaded")
        ttk.Label(toolbar, textvariable=self.game_label_var).pack(side=tk.LEFT, padx=(8, 0))

        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        map_frame = ttk.Frame(body)
        body.add(map_frame, weight=4)
        self.canvas = tk.Canvas(map_frame, background="#202020", highlightthickness=0)
        x_scroll = ttk.Scrollbar(map_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        y_scroll = ttk.Scrollbar(map_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        map_frame.rowconfigure(0, weight=1)
        map_frame.columnconfigure(0, weight=1)

        side = ttk.Frame(body, padding=(8, 0))
        body.add(side, weight=1)
        ttk.Label(side, text="Selection", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        self.detail_var = tk.StringVar(
            value="Open an APWorld, then right-click the map to add locations or groups.\n"
            "Drag pins to move. Hold Shift while dragging to snap to a 16px grid."
        )
        ttk.Label(side, textvariable=self.detail_var, wraplength=280, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 10))
        ttk.Button(side, text="Delete selected pin", command=self.delete_selected_marker).pack(anchor=tk.W)

        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)
        self.bind("<Left>", lambda _e: self._nudge(-1, 0))
        self.bind("<Right>", lambda _e: self._nudge(1, 0))
        self.bind("<Up>", lambda _e: self._nudge(0, -1))
        self.bind("<Down>", lambda _e: self._nudge(0, 1))
        self._refresh_tab_selectors()
        self._update_preset_controls()

    def _count_tab_locations(self, tab: MapTab, aliases: dict[str, str], counts: dict[str, int]) -> None:
        seen_on_tab: set[str] = set()
        for marker in tab.markers:
            for location in marker.locations:
                canonical = aliases.get(location) or aliases.get(normalize_location_name(location))
                if canonical:
                    seen_on_tab.add(canonical)
        for location in seen_on_tab:
            counts[location] += 1
        for child in tab.children:
            self._count_tab_locations(child, aliases, counts)

    def location_tab_counts(self) -> dict[str, int]:
        aliases = build_location_aliases(self.locations)
        counts: dict[str, int] = dict.fromkeys(self.locations, 0)
        for tab in self.preset.tabs:
            self._count_tab_locations(tab, aliases, counts)
        return counts

    def _canonicalize_tab_locations(self, tab: MapTab) -> None:
        for marker in tab.markers:
            marker.locations = [
                canonicalize_location_name(location, self.locations)
                for location in marker.locations
            ]
        for child in tab.children:
            self._canonicalize_tab_locations(child)

    def canonicalize_preset_locations(self) -> None:
        if not self.locations:
            return
        for tab in self.preset.tabs:
            self._canonicalize_tab_locations(tab)

    def _update_game_label(self) -> None:
        if self.preset.game:
            suffix = f" ({len(self.locations)} locations)" if self.locations else ""
            self.game_label_var.set(f"Game: {self.preset.game}{suffix}")
        else:
            self.game_label_var.set("No APWorld loaded")
        self._update_preset_controls()

    def _update_preset_controls(self) -> None:
        state = "normal" if self.preset.game and self.locations else "disabled"
        self.new_preset_button.configure(state=state)
        self.load_preset_button.configure(state=state)
        self.save_preset_button.configure(state=state)

    def _refresh_tab_selectors(self) -> None:
        for child in self.tab_selectors_frame.winfo_children():
            child.destroy()
        self._tab_selector_vars = []
        if not self.preset.tabs:
            return

        leaf_path = resolve_leaf_path(self.preset.tabs, self.active_tab_path)
        for depth in range(len(leaf_path)):
            siblings = siblings_at_depth(self.preset.tabs, leaf_path, depth)
            if not siblings:
                continue
            index = leaf_path[depth]
            var = tk.StringVar(value=siblings[index].name)
            selector = ttk.Combobox(
                self.tab_selectors_frame,
                textvariable=var,
                values=[tab.name for tab in siblings],
                state="readonly",
                width=18,
            )
            selector.pack(side=tk.LEFT, padx=(0, 4))
            selector.bind("<<ComboboxSelected>>", lambda _event, d=depth: self._on_tab_depth_changed(d))
            self._tab_selector_vars.append(var)

    def _on_tab_depth_changed(self, depth: int) -> None:
        if depth >= len(self._tab_selector_vars):
            return
        name = self._tab_selector_vars[depth].get()
        siblings = siblings_at_depth(self.preset.tabs, self.active_tab_path, depth)
        for index, tab in enumerate(siblings):
            if tab.name == name:
                new_path = [*self.active_tab_path[:depth], index]
                self.active_tab_path = resolve_leaf_path(self.preset.tabs, new_path)
                break
        self.selected_marker = None
        self._refresh_tab_selectors()
        self._load_active_map_image()
        self._redraw()

    def _manage_tabs(self) -> None:
        if not self.preset.game:
            messagebox.showwarning("Manage tabs", "Open an APWorld first.")
            return
        ManageTabsDialog(self)

    def _display_size(self) -> tuple[int, int]:
        return int(self.map_width * self.scale), int(self.map_height * self.scale)

    def _load_active_map_image(self) -> None:
        tab = self.active_tab
        if tab is None:
            self.photo = None
            self.canvas.delete("all")
            branch = tab_at_path(self.preset.tabs, self.active_tab_path)
            if branch and is_branch_tab(branch):
                self.canvas.create_text(
                    200,
                    80,
                    text=f"Folder: {branch.name}\n\nUse Manage tabs to add child map tabs.",
                    fill="#cccccc",
                    font=("Segoe UI", 11),
                    anchor=tk.NW,
                    tags=("map",),
                )
                self.canvas.configure(scrollregion=(0, 0, 800, 200))
            return
        path = Path(tab.image_path)
        if not path.is_file():
            messagebox.showerror("Missing map", f"Background not found:\n{path}")
            return
        self.photo, self.map_width, self.map_height = load_photo(path, self.subsample)
        self.canvas.delete("map")
        width, height = self._display_size()
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW, tags=("map",))
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _redraw(self) -> None:
        self.canvas.delete("marker")
        tab = self.active_tab
        if tab is None:
            self._update_detail()
            return
        for index, marker in enumerate(tab.markers):
            is_group = len(marker.locations) > 1 or marker.label
            size = marker.size or tab.location_size
            sx, sy = marker.x * self.scale, marker.y * self.scale
            ds = max(8, size * self.scale)
            x0, y0 = sx - ds / 2, sy - ds / 2
            selected = index == self.selected_marker
            fill = (GROUP_FILL_SELECTED if is_group else SINGLE_FILL_SELECTED) if selected else (
                GROUP_FILL if is_group else SINGLE_FILL
            )
            self.canvas.create_rectangle(
                x0,
                y0,
                x0 + ds,
                y0 + ds,
                fill=fill,
                outline=PIN_OUTLINE_SELECTED if selected else PIN_OUTLINE,
                width=2 if selected else 1,
                tags=("marker", f"marker:{index}"),
            )
            if marker.label:
                label = marker.label
            elif len(marker.locations) == 1:
                label = marker.locations[0]
            else:
                label = f"{len(marker.locations)} locations"
            short = label if len(label) <= 30 else label[:27] + "..."
            self.canvas.create_text(
                sx,
                y0 + ds + 8,
                text=short,
                fill="#ffffff",
                font=("Segoe UI", 8, "bold"),
                anchor=tk.N,
                tags=("marker",),
            )
        self._update_detail()

    def _update_detail(self) -> None:
        tab = self.active_tab
        branch = tab_at_path(self.preset.tabs, self.active_tab_path)
        if tab is None:
            if branch and is_branch_tab(branch):
                child_count = len(branch.children)
                self.detail_var.set(
                    f"Folder: {branch.name}\n"
                    f"Child tabs: {child_count}\n\n"
                    "Use Manage tabs to add map tabs inside this folder."
                )
            else:
                self.detail_var.set("Create a preset tab to begin.")
            return
        lines = [f"Tab: {path_to_label(self.preset.tabs, self.active_tab_path)}", f"Markers: {len(tab.markers)}"]
        if self.selected_marker is not None and 0 <= self.selected_marker < len(tab.markers):
            marker = tab.markers[self.selected_marker]
            lines.append("")
            if marker.label:
                lines.append(f"Group: {marker.label}")
            lines.append(f"Position: {marker.x}, {marker.y}")
            lines.append("Locations:")
            for location in marker.locations:
                lines.append(f"  - {location}")
        self.detail_var.set("\n".join(lines))

    def _clamp_map_pos(self, x: int, y: int) -> tuple[int, int]:
        return max(0, min(self.map_width, x)), max(0, min(self.map_height, y))

    def _snap_map_pos(self, x: int, y: int) -> tuple[int, int]:
        return self._clamp_map_pos(
            int(round(x / SNAP_GRID_SIZE)) * SNAP_GRID_SIZE,
            int(round(y / SNAP_GRID_SIZE)) * SNAP_GRID_SIZE,
        )

    def _canvas_to_map(self, cx: float, cy: float, *, snap: bool = False) -> tuple[int, int]:
        x = int(round(cx / self.scale))
        y = int(round(cy / self.scale))
        if snap:
            return self._snap_map_pos(x, y)
        return self._clamp_map_pos(x, y)

    def _view_center_map(self) -> tuple[int, int]:
        x0 = self.canvas.canvasx(0)
        y0 = self.canvas.canvasy(0)
        x1 = self.canvas.canvasx(max(1, self.canvas.winfo_width()))
        y1 = self.canvas.canvasy(max(1, self.canvas.winfo_height()))
        return self._clamp_map_pos(int((x0 + x1) / 2 / self.scale), int((y0 + y1) / 2 / self.scale))

    def _select_marker(self, index: int | None, *, focus: bool = False) -> None:
        self.selected_marker = index
        self._redraw()
        if index is None or not focus:
            return
        tab = self.active_tab
        if tab is None or index >= len(tab.markers):
            return
        marker = tab.markers[index]
        sx, sy = marker.x * self.scale, marker.y * self.scale
        width, height = self._display_size()
        self.canvas.xview_moveto(max(0.0, min(1.0, (sx - 200) / max(1, width))))
        self.canvas.yview_moveto(max(0.0, min(1.0, (sy - 200) / max(1, height))))

    def _on_canvas_press(self, event: tk.Event) -> None:
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(cx, cy, cx, cy)):
            for tag in self.canvas.gettags(item):
                if tag.startswith("marker:"):
                    index = int(tag.split(":", 1)[1])
                    self._select_marker(index)
                    marker = self.active_tab.markers[index]
                    self.drag_marker = index
                    self.drag_offset = (cx - marker.x * self.scale, cy - marker.y * self.scale)
                    return
        self._select_marker(None)

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self.drag_marker is None or self.active_tab is None:
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        snap = bool(event.state & 0x0001) or "Shift" in self.state()
        x, y = self._canvas_to_map(cx - self.drag_offset[0], cy - self.drag_offset[1], snap=snap)
        marker = self.active_tab.markers[self.drag_marker]
        marker.x, marker.y = x, y
        self.dirty = True
        self._redraw()

    def _on_canvas_release(self, _event: tk.Event) -> None:
        self.drag_marker = None

    def _on_canvas_right_click(self, event: tk.Event) -> None:
        if not self.preset.game:
            messagebox.showwarning("Add marker", "Open an APWorld first.")
            return
        if self.active_tab is None:
            messagebox.showwarning(
                "Add marker",
                "Select a map tab with a background image. Folders cannot hold markers directly.",
            )
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x, y = self._canvas_to_map(cx, cy)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Add location...", command=lambda: self._add_location_at(x, y))
        menu.add_command(label="Add group...", command=lambda: self._add_group_at(x, y))
        menu.tk_popup(event.x_root, event.y_root)

    def _add_location_at(self, x: int, y: int) -> None:
        dialog = LocationPickerDialog(self, "Add location", self.locations, group=False)
        self.wait_window(dialog)
        if dialog.result is None or self.active_tab is None:
            return
        dialog.result.x, dialog.result.y = x, y
        self.active_tab.markers.append(dialog.result)
        self.dirty = True
        self._select_marker(len(self.active_tab.markers) - 1, focus=True)

    def _add_group_at(self, x: int, y: int) -> None:
        dialog = LocationPickerDialog(self, "Add group", self.locations, group=True)
        self.wait_window(dialog)
        if dialog.result is None or self.active_tab is None:
            return
        dialog.result.x, dialog.result.y = x, y
        self.active_tab.markers.append(dialog.result)
        self.dirty = True
        self._select_marker(len(self.active_tab.markers) - 1, focus=True)

    def delete_selected_marker(self) -> None:
        tab = self.active_tab
        if tab is None or self.selected_marker is None:
            return
        if 0 <= self.selected_marker < len(tab.markers):
            tab.markers.pop(self.selected_marker)
            self.selected_marker = None
            self.dirty = True
            self._redraw()

    def _nudge(self, dx: int, dy: int) -> None:
        tab = self.active_tab
        if tab is None or self.selected_marker is None:
            return
        shift = "Shift" in self.state()
        step = SNAP_GRID_SIZE if shift else 1
        marker = tab.markers[self.selected_marker]
        x, y = marker.x + dx * step, marker.y + dy * step
        if shift:
            x, y = self._snap_map_pos(x, y)
        else:
            x, y = self._clamp_map_pos(x, y)
        marker.x, marker.y = x, y
        self.dirty = True
        self._redraw()

    def _on_zoom_changed(self, _event: tk.Event | None = None) -> None:
        for label, subsample in ZOOM_OPTIONS:
            if label == self.zoom_var.get():
                self.subsample = subsample
                self.scale = 1.0 / subsample
                break
        self._load_active_map_image()
        self._redraw()

    def open_apworld(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open APWorld",
            initialdir=str(custom_worlds_dir()),
            filetypes=[("APWorld", "*.apworld"), ("All files", "*.*")],
        )
        if not path:
            return
        self._load_apworld(Path(path))

    def _load_apworld(self, apworld_path: Path) -> None:
        apworld_path = Path(apworld_path).resolve()
        try:
            game = ensure_game_loaded(apworld_path)
        except Exception as exc:
            messagebox.showerror("Open APWorld", f"Could not load APWorld:\n{exc}")
            return

        self.apworld_path = apworld_path
        self.preset.game = game
        self.locations = get_world_locations(game)
        self.canonicalize_preset_locations()
        if not self.locations:
            messagebox.showerror("Open APWorld", f"'{game}' has no Archipelago locations to map.")
            return
        self._update_game_label()

    def new_preset(self) -> None:
        if not self.preset.game:
            messagebox.showwarning("New preset", "Open an APWorld first.")
            return
        if self.dirty and not messagebox.askyesno("New preset", "Discard unsaved changes?"):
            return
        image_path = filedialog.askopenfilename(
            parent=self,
            title="Select background image for first tab",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")],
        )
        if not image_path:
            return
        if self._preset_temp is not None:
            self._preset_temp.cleanup()
            self._preset_temp = None
        self.preset.tabs = [MapTab(name="Tab 1", image_path=image_path)]
        self.active_tab_path = [0]
        self.selected_marker = None
        self.dirty = True
        self._refresh_tab_selectors()
        self._load_active_map_image()
        self._redraw()

    def load_preset_dialog(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Load mapping preset",
            initialdir=str(visual_packs_dir()),
            filetypes=[("Mapping preset", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return
        if self.dirty and not messagebox.askyesno("Load preset", "Discard unsaved changes?"):
            return
        try:
            preset, temp_dir = load_preset(Path(path))
        except Exception as exc:
            messagebox.showerror("Load preset", str(exc))
            return

        if self._preset_temp is not None:
            self._preset_temp.cleanup()
        self._preset_temp = temp_dir
        self.preset = preset
        self.active_tab_path = resolve_leaf_path(preset.tabs, [0]) if preset.tabs else []
        self.selected_marker = None
        self.dirty = False

        from worlds.AutoWorld import AutoWorldRegister

        if preset.game and preset.game in AutoWorldRegister.world_types:
            self.locations = get_world_locations(preset.game)
            self.preset.game = preset.game
            self.canonicalize_preset_locations()
        elif preset.game:
            messagebox.showwarning(
                "Game not installed",
                f"Preset targets '{preset.game}', which is not installed. "
                "Open its APWorld before editing markers.",
            )
        self._update_game_label()
        self._refresh_tab_selectors()
        self._load_active_map_image()
        self._redraw()

    def save_preset_dialog(self) -> None:
        if not self.preset.tabs:
            messagebox.showwarning("Save preset", "Create a preset before saving.")
            return
        if not self.preset.game:
            messagebox.showwarning("Save preset", "Open an APWorld first so the preset knows which game it targets.")
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save mapping preset",
            initialdir=str(visual_packs_dir()),
            defaultextension=".zip",
            initialfile=suggest_preset_filename(self.preset.game),
            filetypes=[("Mapping preset", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            save_preset(self.preset, Path(path))
        except Exception as exc:
            messagebox.showerror("Save preset", str(exc))
            return
        self.dirty = False
        messagebox.showinfo("Save preset", f"Saved mapping preset to:\n{path}")

    def _on_close(self) -> None:
        if self.dirty and not messagebox.askyesno("Quit", "Discard unsaved changes?"):
            return
        if self._preset_temp is not None:
            self._preset_temp.cleanup()
        self.destroy()


def application_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return Path(__file__).resolve()


def resolve_apworld_for_startup(apworld_path: Path) -> tuple[str, list[str]]:
    return resolve_apworld(apworld_path)


def main(argv: list[str] | None = None) -> None:
    try:
        bootstrap_archipelago()
    except RuntimeError as exc:
        if getattr(sys, "frozen", False):
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Archipelago Mapping Editor", str(exc))
            root.destroy()
        else:
            print(exc, file=sys.stderr)
        raise SystemExit(1) from exc

    parser = argparse.ArgumentParser(description="Archipelago mapping preset editor")
    parser.add_argument("--apworld", type=Path, help="APWorld to load on startup")
    args = parser.parse_args(argv)

    game: str | None = None
    locations: list[str] = []
    apworld_path: Path | None = None

    if args.apworld:
        apworld_path = args.apworld.resolve()
        game, locations = resolve_apworld_for_startup(apworld_path)

    app = MappingPresetEditor(game, locations, apworld_path)
    if not game:
        app.after(100, app.open_apworld)
    app.mainloop()


if __name__ == "__main__":
    main()
