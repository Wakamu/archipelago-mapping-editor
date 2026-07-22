# Archipelago Mapping Preset Editor

Standalone desktop app for creating mapping preset zip files used by [Archipelago Visual Tracker](https://github.com/Wakamu/Archipelago).

## Requirements

- Python 3.11.9–3.13
- An [Archipelago](https://github.com/ArchipelagoMW/Archipelago) installation (source checkout or installed copy)
- Pillow (recommended, for PNG/JPEG/WebP map backgrounds)

## Setup

```bash
cd archipelago-mapping-editor
pip install -r requirements.txt
```

Set `ARCHIPELAGO_PATH` if the editor cannot find Archipelago automatically (for example when your Archipelago folder is not a sibling of this project):

```bash
set ARCHIPELAGO_PATH=C:\Users\User\Projects\Archipelago
```

On a typical Windows install the editor also checks `C:\ProgramData\Archipelago`.

### Linux / source checkout

Packaged Linux Archipelago releases often use a different Python than your system Python, so prefer a **source checkout**:

```bash
git clone https://github.com/ArchipelagoMW/Archipelago.git
cd Archipelago
python ModuleUpdate.py          # installs Archipelago deps (schema, etc.)
export ARCHIPELAGO_PATH="$PWD"
cd ../archipelago-mapping-editor
python editor.py
```

`ModuleUpdate.py` must be run with the **same** Python interpreter you use for the mapping editor.

## Run

```bash
python editor.py
```

Or double-click `run.bat` on Windows.

## Build a Windows `.exe`

The editor is **not** fully standalone: at runtime it still loads location data from an Archipelago install.

**Python version:** Build with **Python 3.13** so the exe can load worlds from the official Windows install at `C:\ProgramData\Archipelago`. (Pyenv 3.10 builds will not work against that install.)

Archipelago is auto-detected from, in order:

1. `ARCHIPELAGO_PATH` (installed copy or source checkout)
2. `Archipelago` next to the exe (or its parent folder)
3. `C:\ProgramData\Archipelago`

1. Install [Python 3.13+](https://www.python.org/downloads/) and open a terminal in this folder.
2. Run:

```bat
build_exe.bat
```

Or manually:

```bat
py -3.13 -m pip install -r requirements.txt pyinstaller
py -3.13 -m PyInstaller --noconfirm --clean mapping_editor.spec
```

3. Output: `dist\ArchipelagoMappingEditor.exe`

You can copy that exe anywhere. If auto-detect fails, set the environment variable before launching:

```bat
set ARCHIPELAGO_PATH=C:\ProgramData\Archipelago
dist\ArchipelagoMappingEditor.exe
```

For a **source checkout** instead of the installed copy:

```bat
set ARCHIPELAGO_PATH=C:\Users\User\Projects\Archipelago
dist\ArchipelagoMappingEditor.exe
```

## Usage

1. **Open APWorld…** — pick a `.apworld` file to load that game's location list (installs to `custom_worlds` and restarts if needed)
2. **New Preset** / **Load Preset…** — start fresh or open an existing mapping zip
3. **Manage tabs…** — add map tabs with background images
4. **Right-click the map** — add a location or group pin
5. **Drag pins** — move them; hold **Shift** to snap to a 16px grid
6. **Save Preset…** — writes a `.zip` for Visual Tracker (`mapping.json` + `images/`)

## Output format

Presets are zip archives compatible with Archipelago Visual Tracker:

```json
{
  "game": "Game Name",
  "tabs": [
    {
      "name": "World Map",
      "image": "images/world.png",
      "location_size": 32,
      "markers": [
        { "x": 100, "y": 200, "locations": ["Location Name"] },
        { "x": 300, "y": 400, "locations": ["A", "B"], "label": "Dungeon" }
      ]
    }
  ]
}
```
