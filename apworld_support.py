from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import re
import shutil
import sys
import types
import zipimport
from pathlib import Path
from zipfile import ZipFile

EXCLUDED_GAMES = {
    "Universal Tracker",
    "Archipelago Visual Tracker",
    "Mapping Preset Editor",
}

_ARCHIPELAGO_ROOT: Path | None = None
_ARCHIPELAGO_LAYOUT: str | None = None
_REGISTERED_APWORLD_MODULES: set[str] = set()
_RELEASE_PYD_SUFFIX = re.compile(r"\.cp\d+-win_amd64\.pyd$", re.IGNORECASE)


def _is_source_root(candidate: Path) -> bool:
    return (candidate / "worlds").is_dir() and (candidate / "Utils.py").is_file()


def _is_release_root(candidate: Path) -> bool:
    return (candidate / "lib" / "library.zip").is_file() and (candidate / "lib" / "worlds").is_dir()


def archipelago_layout(root: Path) -> str:
    if _is_source_root(root):
        return "source"
    if _is_release_root(root):
        return "release"
    raise RuntimeError(f"{root} is not a recognized Archipelago installation.")


def _candidate_roots() -> list[Path]:
    candidates: list[Path] = []

    env_path = os.environ.get("ARCHIPELAGO_PATH", "").strip().strip('"')
    if env_path:
        candidates.append(Path(env_path))

    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        candidates.extend((app_dir / "Archipelago", app_dir.parent / "Archipelago"))
    else:
        candidates.append(Path(__file__).resolve().parent.parent / "Archipelago")

    candidates.append(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Archipelago")
    return candidates


def find_archipelago_root() -> Path:
    global _ARCHIPELAGO_ROOT, _ARCHIPELAGO_LAYOUT
    if _ARCHIPELAGO_ROOT is not None:
        return _ARCHIPELAGO_ROOT

    seen: set[Path] = set()
    checked: list[str] = []
    for candidate in _candidate_roots():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        checked.append(str(resolved))
        if _is_source_root(resolved) or _is_release_root(resolved):
            _ARCHIPELAGO_ROOT = resolved
            _ARCHIPELAGO_LAYOUT = archipelago_layout(resolved)
            return _ARCHIPELAGO_ROOT

    checked_lines = "\n".join(f"  - {path}" for path in checked) or "  (none)"
    raise RuntimeError(
        "Could not find an Archipelago installation.\n\n"
        "Checked:\n"
        f"{checked_lines}\n\n"
        "Set ARCHIPELAGO_PATH to either:\n"
        "  - An installed copy (e.g. C:\\ProgramData\\Archipelago), or\n"
        "  - A source checkout (folder containing worlds\\ and Utils.py).\n\n"
        "If using the installed copy, rebuild this editor with Python 3.13 "
        "(same as the Archipelago release)."
    )


def _ensure_worlds_package(worlds_dir: Path) -> None:
    worlds_pkg = sys.modules.get("worlds")
    if worlds_pkg is None:
        worlds_pkg = types.ModuleType("worlds")
        sys.modules["worlds"] = worlds_pkg
    worlds_pkg.__path__ = [str(worlds_dir)]  # type: ignore[attr-defined]


def _preload_release_extensions(lib_dir: Path) -> None:
    """Preload native extensions shipped beside library.zip (e.g. bsdiff4.core)."""
    for pyd_path in sorted(lib_dir.glob("*.pyd")):
        module_name = _RELEASE_PYD_SUFFIX.sub("", pyd_path.name)
        if not module_name or module_name in sys.modules:
            continue
        try:
            loader = importlib.machinery.ExtensionFileLoader(module_name, str(pyd_path))
            sys.modules[module_name] = loader.load_module()
        except Exception:
            continue


def bootstrap_archipelago() -> Path:
    root = find_archipelago_root()
    layout = _ARCHIPELAGO_LAYOUT or archipelago_layout(root)

    if layout == "source":
        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
        worlds_dir = root / "worlds"
    else:
        lib_dir = root / "lib"
        for entry in (str(lib_dir / "library.zip"), str(lib_dir)):
            if entry not in sys.path:
                sys.path.insert(0, entry)
        _preload_release_extensions(lib_dir)
        worlds_dir = lib_dir / "worlds"

    _ensure_worlds_package(worlds_dir)

    try:
        from worlds.AutoWorld import AutoWorldRegister  # noqa: F401
    except ImportError as exc:
        message = str(exc).lower()
        if "bad magic number" in message or "bad magic" in message:
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            if layout == "release":
                hint = (
                    "This is a packaged Archipelago release, and its world files were built "
                    f"for a different Python than {py_ver}.\n\n"
                    "Fix options:\n"
                    "  1. Set ARCHIPELAGO_PATH to an Archipelago source checkout "
                    "(folder with worlds/ and Utils.py), then run this editor with "
                    "Python 3.11.9–3.13, or\n"
                    "  2. On Windows, rebuild/run the editor with Python 3.13 against "
                    "the official installed release, or\n"
                    "  3. On Linux, prefer a source checkout — the AppImage/tar.gz "
                    "release often uses a different embedded Python than your system Python."
                )
            else:
                hint = (
                    f"Python {py_ver} cannot load the world files at this path.\n\n"
                    "Use Python 3.11.9–3.13 with an Archipelago source checkout, "
                    "or match the Python version used to build that Archipelago install."
                )
            raise RuntimeError(
                f"Found Archipelago at {root}, but Python {py_ver} cannot load its world files.\n\n"
                f"{hint}"
            ) from exc
        raise

    return root


def custom_worlds_dir() -> Path:
    programdata = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Archipelago" / "custom_worlds"
    if programdata.parent.is_dir():
        programdata.mkdir(parents=True, exist_ok=True)
        return programdata

    path = find_archipelago_root() / "custom_worlds"
    path.mkdir(parents=True, exist_ok=True)
    return path


def visual_packs_dir() -> Path:
    programdata = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Archipelago" / "visual_packs"
    if programdata.parent.is_dir():
        programdata.mkdir(parents=True, exist_ok=True)
        return programdata

    path = find_archipelago_root() / "visual_packs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_apworld(apworld_path: Path) -> Path:
    apworld_path = apworld_path.resolve()
    destination = (custom_worlds_dir() / apworld_path.name).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if apworld_path == destination:
        return destination
    if destination.exists():
        return destination
    shutil.copy2(apworld_path, destination)
    return destination


def detect_world_module(apworld_path: Path) -> str:
    from mapping_preset_io import detect_apworld_world_module, read_apworld_manifest

    _game, world_id = read_apworld_manifest(apworld_path)
    with ZipFile(apworld_path) as archive:
        roots = sorted(
            {
                name.split("/", 1)[0]
                for name in archive.namelist()
                if (name.endswith("/__init__.py") or name.endswith("/__init__.pyc")) and name.count("/") == 1
            }
        )
    if world_id in roots:
        return world_id
    if len(roots) == 1:
        return roots[0]
    if apworld_path.stem in roots:
        return apworld_path.stem
    if roots:
        return roots[0]
    return world_id or detect_apworld_world_module(apworld_path)


def _game_from_world_module(world_module: str) -> str | None:
    from worlds.AutoWorld import AutoWorldRegister

    module_name = f"worlds.{world_module}"
    for game_name, world_type in AutoWorldRegister.world_types.items():
        if getattr(world_type, "__module__", "") == module_name:
            return game_name
    return None


def _register_apworld_module(apworld_path: Path, world_module: str) -> None:
    module_name = f"worlds.{world_module}"
    if module_name in sys.modules or world_module in _REGISTERED_APWORLD_MODULES:
        return

    importer = zipimport.zipimporter(str(apworld_path.resolve()))
    spec = importer.find_spec(module_name)
    if spec is None:
        raise ValueError(f"Could not find world module '{module_name}' in {apworld_path.name}.")

    class SingleAPWorldFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):  # noqa: ANN001
            if fullname == module_name:
                return spec
            return None

    sys.meta_path.insert(0, SingleAPWorldFinder())
    _REGISTERED_APWORLD_MODULES.add(world_module)


def load_apworld_module(apworld_path: Path, world_module: str | None = None) -> None:
    apworld_path = apworld_path.resolve()
    world_module = world_module or detect_world_module(apworld_path)
    _register_apworld_module(apworld_path, world_module)
    importlib.import_module(f"worlds.{world_module}")


def get_world_locations(game: str) -> list[str]:
    from worlds.AutoWorld import AutoWorldRegister

    world = AutoWorldRegister.world_types.get(game)
    if world is None or not world.location_name_to_id:
        return []
    return sorted(world.location_name_to_id.keys(), key=str.lower)


def ensure_game_loaded(apworld_path: Path) -> str:
    from mapping_preset_io import read_apworld_manifest

    apworld_path = apworld_path.resolve()
    game, world_module = read_apworld_manifest(apworld_path)
    if game in EXCLUDED_GAMES:
        raise ValueError(f"'{game}' cannot be used for mapping presets.")

    from worlds.AutoWorld import AutoWorldRegister

    if game and game in AutoWorldRegister.world_types:
        return game

    load_error: Exception | None = None
    try:
        load_apworld_module(apworld_path, world_module)
    except Exception as exc:
        load_error = exc

    if game is None:
        game = _game_from_world_module(world_module)

    if game in AutoWorldRegister.world_types:
        return game

    message = (
        f"Could not load game '{game or world_module}' from {apworld_path.name}."
        if game
        else f"Could not load world module '{world_module}' from {apworld_path.name}."
    )
    if load_error is not None:
        raise ValueError(message) from load_error
    raise ValueError(message)


def resolve_apworld(apworld_path: Path) -> tuple[str, list[str]]:
    game = ensure_game_loaded(apworld_path)
    locations = get_world_locations(game)
    if not locations:
        raise ValueError(f"'{game}' has no Archipelago locations to map.")
    return game, locations
