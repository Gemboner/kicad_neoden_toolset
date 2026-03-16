# kicad_neoden_toolset

`kicad_neoden_toolset` is a small Python toolchain for taking KiCad placement exports and turning them into files that are usable on a NeoDen4 machine.

The repository is meant to hold:

- the conversion scripts,
- the machine baseline files that those scripts depend on,
- feeder configuration that should be reused across jobs,
- documentation for the operator workflow.

It is not intended to permanently store every board-specific `.pos`, generated placement CSV, or edited machine project. Those job files should live in a separate working directory and be passed to the scripts by relative or absolute path.

## What the workflow does

The normal workflow is:

1. Export a `.pos` file from KiCad.
2. Normalize values if KiCad exported capacitor values in a form that does not match your feeder mapping.
3. Convert the `.pos` file into a NeoDen project CSV using the repository's baseline template and feeder assignment map.
4. Load that CSV on the machine and make any placement corrections directly on the NeoDen.
5. Pull those learned feeder stack settings or placement corrections back into the toolchain.
6. Regenerate the next board with the improved settings.

This repository exists to keep steps 2 through 6 repeatable.

## Repository files

### Scripts

- `kicad.pos_to_neoden_project.py`
  Converts a KiCad `.pos` export into a NeoDen project-style CSV. It preserves the machine header structure from `template_project.csv` and tries to assign feeders from `feeder_assignment.csv`.
- `kicad.pos_to_neoden_chip.py`
  Converts a KiCad `.pos` export into the simpler NeoDen chip CSV format.
- `normalize_values.py`
  Normalizes KiCad values before conversion. Right now it is mainly used to turn capacitor values such as `100n` into `100nF` when that is needed for feeder matching.
- `feeder_inherit.py`
  Reads stack lines from a working NeoDen project CSV and updates `feeder_assignment.csv` plus `feeder_config.json`.
- `update_neoden_positions.py`
  Rewrites component coordinates inside an existing NeoDen project CSV using a newer `.pos` export.
- `generate_neoden_discrepancy_report.py`
  Compares a generated NeoDen project CSV against an edited one and reports average offsets and outliers.
- `export_to_external_drive.py`
  Copies a chosen file to a mounted USB drive or other external media.

### Baseline data used by the scripts

- `template_project.csv`
  The baseline NeoDen project used as the header and machine-format template when generating new project CSVs.
- `feeder_assignment.csv`
  The main feeder lookup table. This is the file the conversion script uses to map footprint and value combinations onto feeder IDs.
- `feeder_config.json`
  A JSON export of the feeder information. It is regenerated from `feeder_assignment.csv` by `feeder_inherit.py`.
- `global_offset.json`
  A place to store machine-wide or footprint-specific offset corrections used when reviewing discrepancy reports.

## Important operating assumption

Most scripts use repository-local default files such as `template_project.csv`, `feeder_assignment.csv`, `feeder_config.json`, and `global_offset.json`.

Because of that, the safest way to run them is from the repository root:

```bash
cd /path/to/kicad_neoden_toolset
```

If you run the scripts from somewhere else, the defaults may no longer resolve to the files in this repository.

## Expected input files

The toolchain expects a KiCad `.pos` file with the usual columns for:

- reference designator,
- value,
- footprint,
- X,
- Y,
- rotation,
- side (`top` or `bottom`).

The scripts do not manage KiCad exports themselves. You export the `.pos` file from KiCad, place it wherever you want, then pass that path into the tool.

Example job layout outside this repository:

```text
jobs/
  bridge_interface/
    PanelBridgeInterface-top.pos
    PanelBridgeInterface-bottom.pos
    machine/
      PanelBridgeInterface-top_neoden_project.csv
      PanelBridgeInterface-top_neoden_project_edited.csv
```

## Typical usage

### 1. Normalize a `.pos` file if needed

If feeder matching depends on capacitor values being written as `100nF` instead of `100n`, normalize first:

```bash
cd /path/to/kicad_neoden_toolset
python3 normalize_values.py ../jobs/bridge_interface/PanelBridgeInterface-top.pos
```

This writes:

```text
../jobs/bridge_interface/PanelBridgeInterface-top_normalized.pos
```

If the original file already matches your feeder mapping, skip this step.

### 2. Generate a NeoDen project CSV

Run the main converter from the repository root and point it at the KiCad export using a relative path:

```bash
cd /path/to/kicad_neoden_toolset
python3 kicad.pos_to_neoden_project.py ../jobs/bridge_interface/PanelBridgeInterface-top.pos --side top
```

Or, if you normalized first:

```bash
cd /path/to/kicad_neoden_toolset
python3 kicad.pos_to_neoden_project.py ../jobs/bridge_interface/PanelBridgeInterface-top_normalized.pos --side top
```

The script will:

1. Prompt for `Chip_1` X and Y coordinates on the NeoDen.
2. Compute an offset between the first valid KiCad component and the machine reference point.
3. Apply that offset to all components.
4. Reuse the header and machine structure from `template_project.csv`.
5. Try to assign feeders from `feeder_assignment.csv`.
6. Write a file next to the input `.pos` file, typically ending in `_neoden_project.csv`.

Example output:

```text
../jobs/bridge_interface/PanelBridgeInterface-top_neoden_project.csv
```

### 3. Generate the simpler chip CSV

If you want the simpler chip-format CSV instead of the full project CSV:

```bash
cd /path/to/kicad_neoden_toolset
python3 kicad.pos_to_neoden_chip.py ../jobs/bridge_interface/PanelBridgeInterface-top.pos
```

This also prompts for the `Chip_1` reference coordinates and writes an output file next to the input, ending in `_neoden.csv`.

### 4. Learn feeder settings from an edited machine project

After the machine project has been adjusted on the NeoDen, feed that edited CSV back into the repository:

```bash
cd /path/to/kicad_neoden_toolset
python3 feeder_inherit.py ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project_edited.csv
```

This updates:

- `template_project.csv`
- `feeder_assignment.csv`
- `feeder_config.json`

The idea is simple: once you have a machine project with the right stack lines and feeder setup, that file becomes the new source of truth for future conversions.

### 5. Refresh component coordinates in an existing project

If the board changed in KiCad but you want to keep working from an existing NeoDen project:

```bash
cd /path/to/kicad_neoden_toolset
python3 update_neoden_positions.py \
  ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project_edited.csv \
  ../jobs/bridge_interface/PanelBridgeInterface-top.pos \
  --side top
```

This updates the `comp,...` lines while keeping the rest of the project CSV structure.

### 6. Compare generated vs edited projects

To understand how far the machine-edited project drifted from the generated one:

```bash
cd /path/to/kicad_neoden_toolset
python3 generate_neoden_discrepancy_report.py \
  --base ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project.csv \
  --edited ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project_edited.csv \
  --out ../jobs/bridge_interface/machine/neoden_discrepancy_report.txt
```

This report helps decide whether you need a global offset, a footprint-specific correction, or just a few manual fixes.

### 7. Copy a final file to external storage

```bash
cd /path/to/kicad_neoden_toolset
python3 export_to_external_drive.py ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project.csv
```

Or explicitly point it at a mounted drive:

```bash
cd /path/to/kicad_neoden_toolset
python3 export_to_external_drive.py \
  ../jobs/bridge_interface/machine/PanelBridgeInterface-top_neoden_project.csv \
  --drive-path /media/cto/MY_USB
```

## How feeder matching works

The main converter tries to assign feeders in this order:

1. Match by footprint and value using `feeder_assignment.csv`.
2. Match by footprint only using `feeder_assignment.csv`.
3. Fall back to the existing feeder information already present in `template_project.csv`.
4. If nothing matches, use the default feeder/nozzle/skip values supplied on the command line.

That means the quality of `feeder_assignment.csv` and `template_project.csv` directly controls how useful the generated project is.

## How paths should be used

The scripts are easiest to reason about if you treat this repository as the tool root and your actual board files as job data living elsewhere.

Good pattern:

```bash
cd /path/to/kicad_neoden_toolset
python3 kicad.pos_to_neoden_project.py ../jobs/my_board/MyBoard-top.pos --side top
```

Also fine:

```bash
python3 /path/to/kicad_neoden_toolset/kicad.pos_to_neoden_project.py /absolute/path/to/MyBoard-top.pos --side top
```

Less safe:

```bash
cd ../jobs/my_board
python3 /path/to/kicad_neoden_toolset/kicad.pos_to_neoden_project.py MyBoard-top.pos --side top
```

The last form can work, but the script defaults for `template_project.csv` and `feeder_assignment.csv` are relative to the current working directory, not relative to the script file, so it is easier to make mistakes.

## Recommended working practice

- Keep this repository clean and reusable.
- Keep machine/job outputs in a separate job directory.
- Commit changes to feeder mappings and baseline machine templates when they become generally useful.
- Do not commit one-off board exports unless you explicitly want them as fixtures or tests.
- Run the scripts from the repository root so the default support files resolve correctly.

## Limitations

- Several scripts are interactive and prompt for machine reference coordinates instead of taking them as arguments.
- The scripts rely on NeoDen CSV conventions that are encoded directly in the repository template and parser logic.
- There is no packaging, CLI wrapper, or automated test suite yet.

## Minimal quick start

```bash
cd /path/to/kicad_neoden_toolset
python3 normalize_values.py ../jobs/my_board/MyBoard-top.pos
python3 kicad.pos_to_neoden_project.py ../jobs/my_board/MyBoard-top_normalized.pos --side top
python3 feeder_inherit.py ../jobs/my_board/machine/MyBoard-top_neoden_project_edited.csv
```
