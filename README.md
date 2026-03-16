# kicad_neoden_toolset

Tools and reference data for converting KiCad `.pos` files into NeoDen4-friendly CSVs, inheriting feeder assignments, and reconciling edited machine projects back against generated output.

## Included scripts

- `kicad.pos_to_neoden_project.py`: convert a KiCad `.pos` file into a NeoDen project CSV using `template_project.csv` and feeder assignments.
- `kicad.pos_to_neoden_chip.py`: generate the simpler NeoDen chip placement CSV format.
- `feeder_inherit.py`: extract feeder stack information from an edited project CSV and refresh `feeder_assignment.csv` and `feeder_config.json`.
- `update_neoden_positions.py`: apply fresh KiCad coordinates to an existing NeoDen project CSV.
- `generate_neoden_discrepancy_report.py`: compare a generated project against an edited one and summarize offsets.
- `normalize_values.py`: normalize capacitor values in KiCad `.pos` exports.
- `export_to_external_drive.py`: copy generated files to a mounted external drive.

## Reference data

The repository also contains feeder configuration, template project files, and several board-specific example `.pos` and generated `.csv` files used during fabrication work.

## Usage

These scripts are plain Python entry points and can be run directly, for example:

```bash
python3 kicad.pos_to_neoden_project.py path/to/board.pos --side top
python3 feeder_inherit.py path/to/edited_project.csv
python3 generate_neoden_discrepancy_report.py --base a.csv --edited b.csv
```
