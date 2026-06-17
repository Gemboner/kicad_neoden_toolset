# Fabrication Tools

This repository centers on [`assembly_project_gui.py`](/home/cto/Desktop/fabrication_tools/assembly_project_gui.py), a PySide6 desktop tool for turning KiCad `.pos` exports into NeoDen project CSVs, editing feeder assignments, and managing per-job machine feedback.

## What The GUI Does

The GUI combines three existing scripts into one operator workflow:

- `kicad.pos_viewer_qt.py`
  - Base Qt viewer for KiCad `.pos` files and component selection.
- `kicad.pos_to_neoden_project.py`
  - Conversion logic that builds NeoDen project CSV output from a `.pos`, template CSV, feeder assignments, and global offsets.
- `feeder_inherit.py`
  - Feeder header parsing and normalized feeder assignment CSV support.

`assembly_project_gui.py` loads those modules dynamically at startup and adds project persistence around them.

## Dependencies And Launch

Required runtime:

- Python 3.11+ recommended
- `PySide6`

Launch the GUI from the repo root:

```bash
python assembly_project_gui.py
```

Useful options:

```bash
python assembly_project_gui.py --project path/to/assembly_project.json
python assembly_project_gui.py --pos path/to/board.pos --side top
python assembly_project_gui.py --neoden-project path/to/job.csv
python assembly_project_gui.py --smoke-test --pos path/to/board.pos
```

## Core Model

The GUI persists a `ProjectState` object into `assembly_project.json`. That manifest stores:

- job identity: `project_name`, `board_name`
- current source files: `.pos`, template CSV, feeder CSV, offset JSON
- machine anchor values: `chip1_x_mm`, `chip1_y_mm`
- generated output path: `neoden_project_csv`
- latest machine-edited CSV: `latest_feedback_neoden_csv`
- saved component groups and the active group
- operator notes

Paths are stored relative to the project folder when possible, so projects stay portable.

## Project Folder Layout

When you create or save a project, the GUI expects a folder with this shape:

```text
project_name/
  assembly_project.json
  generated/
  inputs/
    feeder_assignment.csv
  machine_feedback/
```

The GUI copies the feeder assignment CSV into `inputs/` so each project can carry its own local machine state.

## Main UI Areas

### 1. `Project / POS` tab

This is the inherited Qt position viewer with project-specific additions:

- loads a KiCad `.pos`
- previews feeder/nozzle/skip assignments before generation
- filters by ref, value, footprint, side, feeder, nozzle, and skip
- lets you select components visually or from the table
- lets you save a selected set of components as a named group

The preview assignments are computed live from the current template CSV plus the current feeder assignment CSV.

### 2. `NeoDen Project CSV` tab

This tab works directly on a generated or imported NeoDen project CSV:

- parses `stack` rows, header rows, and `comp` rows
- shows editable component rows in a table
- supports manual feeder/nozzle assignment
- supports bulk assign, clear, delete, and row reorder
- applies global X/Y offset to every component row
- rotates the full project around its current bounding-box center
- applies a bottom-side rotation mirror correction
- prunes the loaded CSV down to the active component group
- syncs feeder stack data from the latest machine feedback file

Edits are written back to the loaded NeoDen CSV in place.

### 3. `feeder_editor` tab

This tab edits the normalized feeder assignment CSV used during generation:

- loads a local project feeder CSV
- imports another feeder CSV as a working copy
- filters rows by feeder metadata
- supports row reorder and deletion
- can import `stack` rows from a NeoDen project CSV
- can assign footprint/value pairs from the current NeoDen project to selected feeder rows

This file is the main bridge between previous machine-approved jobs and future automatic assignment.

### 4. Project dock

The right-side dock is the project control surface:

- create, open, save, and save-as for project manifests
- choose the source `.pos`, template CSV, feeder CSV, and offset JSON
- enter `Chip_1` machine coordinates
- generate the NeoDen project CSV
- open or delete the generated output
- manage named groups
- view the current manifest, generated output, and latest feedback file

## Main Workflows

### POS-first workflow

1. Create or open a project.
2. Select a KiCad `.pos`.
3. Choose the template CSV, feeder CSV, and offset JSON.
4. Enter the machine `Chip_1` X/Y values.
5. Generate the NeoDen project CSV.
6. Review or edit the result in the `NeoDen Project CSV` tab.

Internally, generation:

- parses the `.pos`
- computes offset from the first valid placement row and the entered `Chip_1` coordinates
- applies local and global offsets
- updates template header rows such as mirror data
- matches components to feeders using the feeder CSV and template mappings
- writes the output to `generated/<pos-stem>_neoden_project.csv`

### NeoDen-first workflow

1. Open an existing NeoDen project CSV.
2. If no project exists yet, the GUI creates one around that file.
3. Edit feeder/nozzle assignments, reorder rows, rotate, offset, or prune as needed.
4. Save the manifest so the job state is repeatable.

### Machine feedback workflow

1. Import an operator-edited NeoDen CSV into `machine_feedback/`.
2. Mark it as `latest_feedback_neoden_csv`.
3. Run `Sync Feeders From Latest Feedback`.
4. The GUI extracts `stack` rows from the feedback CSV and merges them into the local feeder assignment CSV.

This preserves local offset tweaks while refreshing machine-controlled stack fields.

## Groups

Groups are saved selections of components from the POS view.

- Groups are stored in the manifest.
- Each component is keyed by `ref#occurrence`, so repeated references can still be distinguished.
- An active group can be reselected in the viewer or used to prune the loaded NeoDen project CSV down to just that subset.

This is useful for partial builds, panel subsets, or debugging a limited set of placements.

## Important Behaviors

- Saving a project also ensures the local `inputs/`, `generated/`, and `machine_feedback/` directories exist.
- The feeder editor is saved before the project manifest is written.
- Opening a project restores both the POS preview state and the NeoDen CSV state.
- If the source POS file is rotated, any previously generated NeoDen CSV is detached from the project state and must be regenerated.
- The latest machine feedback file is auto-detected from `machine_feedback/` if a specific file is not configured.

## Files Worth Reading With The GUI

- [`assembly_project_gui.py`](/home/cto/Desktop/fabrication_tools/assembly_project_gui.py)
- [`kicad.pos_to_neoden_project.py`](/home/cto/Desktop/fabrication_tools/kicad.pos_to_neoden_project.py)
- [`kicad.pos_viewer_qt.py`](/home/cto/Desktop/fabrication_tools/kicad.pos_viewer_qt.py)
- [`feeder_inherit.py`](/home/cto/Desktop/fabrication_tools/feeder_inherit.py)
- [`docs/repo_map_and_gui_merge_plan.md`](/home/cto/Desktop/fabrication_tools/docs/repo_map_and_gui_merge_plan.md)

## Git Hygiene

The GUI creates per-job folders containing live manufacturing state. Those should usually stay local:

- `assembly_project/`
- `*/assembly_project/`
- `*/generated/`
- `*/inputs/`
- `*/machine_feedback/`

The `.gitignore` in this repo now ignores those folders so future commits can focus on code, docs, and stable shared templates rather than job-specific outputs.
