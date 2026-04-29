# Fabrication Tools Repo Map And GUI Merge Plan

## What This Repo Is Today

This repository is not an application yet. It is a collection of operator utilities and generated board artifacts centered around KiCad placement files (`.pos`) and NeoDen project files (`*_neoden_project.csv`).

The current toolset covers four main jobs:

1. Convert KiCad placement exports into NeoDen-compatible outputs.
2. Reuse feeder assignments and machine header data across jobs.
3. Compare generated NeoDen output against operator-edited machine files.
4. Visualize placements and overlays for validation.

## Top-Level Repo Map

### Core scripts

- `kicad.pos_to_neoden_project.py`
  - Main converter from KiCad `.pos` to NeoDen project CSV.
  - Preserves header/template data.
  - Reuses feeder mappings.
  - Prompts the operator for `Chip_1` coordinates to compute offsets.

- `update_neoden_positions.py`
  - Updates an existing NeoDen project CSV from a `.pos` file.
  - Similar offset flow to the main converter.
  - Used after a project already exists and positions need regeneration.

- `kicad.pos_to_neoden_chip.py`
  - Older converter to the simpler NeoDen chip CSV format.
  - Uses hard-coded assumptions and older parsing style.
  - Likely a legacy path once project CSV generation exists.

- `feeder_inherit.py`
  - Extracts `stack` rows from a NeoDen project CSV.
  - Writes normalized feeder assignment CSV and feeder config JSON.
  - Keeps `template_project.csv` synchronized with a selected baseline project.

- `generate_neoden_discrepancy_report.py`
  - Compares generated project CSV vs edited machine CSV.
  - Produces delta analysis and candidate correction data.

- `normalize_values.py`
  - Normalizes capacitor values in `.pos` files before downstream matching.

- `export_to_external_drive.py`
  - Copies a selected output file to a mounted external drive.

- `kicad.pos_preview.py`
  - Generates static SVG preview of `.pos` data.

- `kicad.pos_viewer.py`
  - Tkinter interactive viewer with component browser and optional Gerber overlay.

- `kicad.pos_viewer_qt.py`
  - Qt/PySide6 viewer with similar goals but better foundation for a production GUI.

### Shared machine/config data

- `template_project.csv`
  - Baseline NeoDen project header and stack configuration.

- `feeder_assignment.csv`
  - Canonical feeder mapping table.

- `feeder_config.json`
  - JSON version of feeder assignments/configuration.

- `global_offset.json`
  - Current global placement correction values.

### Board sample folders

Each board folder is effectively a job snapshot:

- `bridge_intf_front/`
- `bridge_intf_back/`
- `sens_brain_front/`
- `sens_brain_back/`
- `practice-board-panel/`
- `practice-board-panel-bot/`

Typical contents:

- source KiCad placement export: `*.pos`
- generated simple NeoDen CSV: `*_neoden.csv`
- generated NeoDen project CSV: `*_neoden_project.csv`
- operator-edited or refreshed project CSV: `*_updated.csv` or `*_edited.csv`
- preview artifact: `*_preview.svg`
- optional Gerbers: `*.gbr`, `*.gbrjob`
- optional report artifact: `neoden_discrepancy_report.txt`

## Functional Map By Workflow

### 1. Placement Preparation

Input:

- KiCad `.pos`

Scripts:

- `normalize_values.py`
- `kicad.pos_to_neoden_project.py`
- `kicad.pos_to_neoden_chip.py`

Output:

- normalized `.pos`
- simple NeoDen CSV
- NeoDen project CSV

Notes:

- Value normalization directly affects feeder matching.
- Conversion currently depends on manual `Chip_1` entry.

### 2. Feeder And Template Reuse

Input:

- existing operator-approved NeoDen project CSV

Scripts:

- `feeder_inherit.py`

Output:

- `template_project.csv`
- `feeder_assignment.csv`
- `feeder_config.json`

Notes:

- This is how process knowledge is carried from one project to the next.
- It is critical for operator trust because it preserves machine-specific setup.

### 3. Position Refresh / Regeneration

Input:

- existing NeoDen project CSV
- new or changed `.pos`

Scripts:

- `update_neoden_positions.py`

Output:

- refreshed `*_updated.csv`

Notes:

- This overlaps heavily with the main converter and should become the same service in the new app.

### 4. Validation And Calibration

Input:

- generated project CSV
- operator-edited machine CSV
- optional global offsets JSON

Scripts:

- `generate_neoden_discrepancy_report.py`

Output:

- discrepancy report text
- implied corrections for global or per-footprint tuning

Notes:

- This is the feedback loop that improves future generation quality.

### 5. Visualization

Input:

- `.pos`
- optional Gerber

Scripts:

- `kicad.pos_preview.py`
- `kicad.pos_viewer.py`
- `kicad.pos_viewer_qt.py`

Output:

- SVG preview
- interactive visual inspection

Notes:

- The Qt viewer is the best starting point for the unified application.

### 6. Delivery / Handoff

Input:

- chosen output file

Scripts:

- `export_to_external_drive.py`

Output:

- copied file on removable media

## Actual Data Flow In The Repo

The practical job lifecycle looks like this:

1. Export `.pos` from KiCad.
2. Optionally normalize values.
3. Enter machine anchor (`Chip_1`) coordinates.
4. Generate a NeoDen project CSV using the current template and feeder assignments.
5. Inspect with SVG or viewer.
6. On the machine, operators adjust or approve positions/feeders.
7. Pull that approved machine file back into the repo using `feeder_inherit.py`.
8. Compare base vs edited files using discrepancy reporting.
9. Apply global offsets or feeder/template improvements for the next run.
10. Export the final file to removable storage.

## Overlap And Duplication

These are the main reasons the repo should be merged into one app and one library:

- `.pos` parsing is duplicated in multiple scripts.
- coordinate offset logic is duplicated in `kicad.pos_to_neoden_project.py` and `update_neoden_positions.py`
- rotation conversion logic is duplicated
- value normalization exists in more than one form
- viewer parsing and component sizing logic are duplicated between Tk and Qt versions
- conversion rules, feeder rules, and operator prompts are spread across unrelated entry points
- file naming conventions are implicit and inconsistent

## Current Problems To Fix In The Merge

### Operator experience

- Multiple scripts must be run manually in the correct order.
- Important inputs are collected through terminal `input()` prompts.
- Generated artifacts are scattered across folders with no project state.
- There is no single place to see source inputs, generated outputs, feeder mappings, preview, and validation.

### Code structure

- Business logic and UI logic are mixed.
- Scripts depend on global files in the repo root.
- The repo behaves like a working directory, not a product.
- There is no testable domain model for jobs, boards, sides, feeders, offsets, or outputs.

### Process risk

- Legacy and newer converters coexist.
- Manual machine edits are not modeled as first-class project state.
- Offset calibration is report-only rather than integrated into the workflow.
- There is no persistent project manifest tying all artifacts together.

## Recommended Unified App Direction

Use `PySide6` and treat `kicad.pos_viewer_qt.py` as the GUI seed, not the Tk app.

Reason:

- Qt is already present in the repo.
- The viewer already solves the hardest interaction problem: board visualization.
- A desktop GUI fits assembly staff better than CLI chaining.
- PySide6 gives tables, forms, split views, dialogs, threading, and future packaging options.

## Recommended Product Model

The new app should be project-based.

One project represents one assembly job and stores:

- project name
- board name
- side: `top`, `bottom`, or both
- source `.pos` files
- optional Gerber files
- selected template baseline
- feeder assignment snapshot
- global/per-footprint offsets
- generated NeoDen outputs
- edited machine outputs
- discrepancy report history
- export history

Suggested project file:

- `assembly_project.json`

Suggested folder layout per job:

```text
jobs/
  PanelBridgeInterface_top/
    assembly_project.json
    inputs/
      PanelBridgeInterface-top.pos
      PanelBridgeInterface-TOP.gbr
    generated/
      PanelBridgeInterface-top_neoden_project.csv
      PanelBridgeInterface-top_preview.svg
      discrepancy_report.txt
    machine_feedback/
      PanelBridgeInterface-top_neoden_project_edited.csv
    snapshots/
      feeder_assignment.csv
      feeder_config.json
      global_offset.json
```

## Recommended App Modules

### Domain layer

- `pos_parser.py`
- `neoden_project_parser.py`
- `feeder_model.py`
- `offset_model.py`
- `project_model.py`

Responsibilities:

- typed models for components, feeders, stacks, headers, projects
- parse and serialize all supported file types

### Service layer

- `conversion_service.py`
- `feeder_service.py`
- `calibration_service.py`
- `preview_service.py`
- `export_service.py`

Responsibilities:

- normalize values
- compute anchor offsets
- generate project CSV
- refresh positions in existing project CSV
- inherit feeder/template data
- run discrepancy analysis
- create SVG preview
- manage removable-drive export

### GUI layer

- `main_window.py`
- `project_browser.py`
- `job_editor.py`
- `viewer_panel.py`
- `feeder_panel.py`
- `generation_panel.py`
- `calibration_panel.py`
- `export_panel.py`

## Recommended Main Screens

### 1. Project Browser

- create/open assembly job
- list recent jobs
- show job status

### 2. Inputs Screen

- load `.pos`
- load Gerber
- choose side
- normalize values preview
- detect anchor component

### 3. Viewer Screen

- interactive board view
- search/filter components
- show duplicate coordinates
- align Gerber overlay
- show selected component metadata

### 4. Feeder Setup Screen

- inspect feeder assignment table
- assign or override feeder by footprint/value
- import feeder setup from approved machine project
- save feeder library snapshot

### 5. Generation Screen

- enter or select anchor reference
- enter machine `Chip_1` coordinates
- generate NeoDen project CSV
- refresh an existing project CSV
- highlight missing feeder mappings before export

### 6. Calibration Screen

- import edited machine CSV
- run discrepancy report
- view per-component and per-footprint deltas in the UI
- promote suggested offsets into project settings

### 7. Export Screen

- export selected output
- copy to removable drive
- keep an export log

## Merge Strategy

### Phase 1. Extract shared logic

Move these behaviors out of scripts first:

- `.pos` parsing
- NeoDen project parsing/serialization
- feeder assignment loading and writing
- offset computation
- value normalization
- discrepancy comparison

Result:

- one library, many thin entry points

### Phase 2. Choose one viewer base

Keep:

- Qt viewer

Retire after parity:

- Tk viewer
- static preview script as a standalone tool

### Phase 3. Build project state

Introduce:

- `assembly_project.json`
- per-job folder structure
- persistent settings for paths, offsets, and outputs

### Phase 4. Wrap workflows in GUI actions

Replace terminal prompting with forms for:

- source file selection
- side selection
- anchor coordinate entry
- feeder overrides
- discrepancy review
- export

### Phase 5. Add safety rails

- validation for missing feeder mappings
- validation for duplicate coordinates
- preview before export
- warnings when a generated file differs from operator-edited baseline

## Immediate Implementation Priorities

1. Create a shared library package and move conversion/parsing code into it.
2. Standardize on one canonical component model and one canonical NeoDen project model.
3. Use the Qt viewer as the first shell for the unified GUI.
4. Add project manifests so each board/job becomes self-contained.
5. Rebuild current CLI scripts as wrappers around the new shared services.

## Suggested First Deliverable

The first practical merged version should not try to solve everything at once.

It should support this end-to-end path:

1. Open or create a job.
2. Load `.pos` and optional Gerber.
3. Preview placements in the Qt viewer.
4. Enter machine anchor coordinates.
5. Generate NeoDen project CSV using feeder assignments and template.
6. Show missing feeder mappings and duplicate coordinates.
7. Save/export the generated project.

That gives assembly staff one usable GUI quickly while preserving the current conversion value.

## Files Most Likely To Seed The New App

- `kicad.pos_viewer_qt.py`
- `kicad.pos_to_neoden_project.py`
- `feeder_inherit.py`
- `generate_neoden_discrepancy_report.py`

## Files Most Likely To Be Retired Later

- `kicad.pos_viewer.py`
- `kicad.pos_to_neoden_chip.py`
- `normalize_values.py` as a standalone script
- `kicad.pos_preview.py` as a standalone script

## Main Risks During Refactor

- preserving exact NeoDen CSV formatting expected by the machine
- not breaking feeder inheritance behavior from approved machine files
- handling repeated reference designators from panelized layouts correctly
- carrying forward operator edits and calibration data without hiding them
- avoiding silent changes in normalization and feeder matching behavior

## Recommended Next Step

Build a new package skeleton around the Qt app and extract the current converter into reusable services before changing behavior. That keeps the first GUI milestone narrow and verifiable.
