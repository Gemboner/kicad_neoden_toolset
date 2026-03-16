from __future__ import print_function

# Translator for converting KiCAD .pos files to a NEODEN project-style .csv
# Uses an optional template file to preserve machine/project headers and feeder mappings.

import argparse
import os
from pathlib import Path
import csv
import json


DEFAULT_HEADER_LINES = [
    "#Feeder,Feeder ID,Type,Nozzle,X,Y,Angle,Footprint,Value,Pick height,Pick delay,Place Height,Place Delay,Vacuum detection,Threshold,Vision Alignment,Speed,",
    "pcb,Manual,Lock,100,100,350,150,Front,10,10,0,",
    "mark,Whole,Auto,0,0,0,0,0,0,0,0,",
    "markext,0,0.8,3,1,0,",
    "markext,1,0.8,3,1,0,",
    "markext,2,0.8,3,1,0,",
    "markext,3,0.8,3,1,0,",
    "test,No,",
    "mirror_create,1,1,0,0,0,0,0,0,0,",
    "#SMD,Feeder ID,Nozzle,Name,Value,Footprint,X,Y,Rotation,Skip",
]

DEFAULT_TEMPLATE_PATH = "template_project.csv"
DEFAULT_FEEDER_ASSIGNMENT_PATH = "feeder_assignment.csv"
FALLBACK_FEEDER_ASSIGNMENT_PATHS = []
DEFAULT_GLOBAL_OFFSET_PATH = "global_offset.json"


def transrotate(value):
    if value <= 180:
        return float(int(value))
    value -= 180
    return float(0 - (180 - value))


def parse_pos_file(path):
    pos_lines = []
    for raw in Path(path).read_text().splitlines():
        pos_lines.append(raw.strip("\n").split())
    return pos_lines


def compute_offsets(pos_lines, chip1xipos, chip1yipos):
    ref = None
    for line in pos_lines:
        if not line or line[0].startswith("#"):
            continue
        if len(line) < 6:
            continue
        try:
            float(line[3])
            float(line[4])
        except ValueError:
            continue
        ref = line
        break
    if ref is None:
        raise ValueError("No valid component row found to compute offsets.")
    offsetxi = float(chip1xipos) - float(ref[3])
    offsetyi = float(chip1yipos) - float(ref[4])
    return offsetxi, offsetyi


def apply_offsets(pos_lines, offsetxi, offsetyi):
    for line in pos_lines:
        if not line or line[0].startswith("#"):
            continue
        if len(line) < 6:
            continue
        try:
            x_val = float(line[3])
            y_val = float(line[4])
        except ValueError:
            continue
        line[3] = str("%.4f" % (offsetxi + x_val))
        line[4] = str("%.4f" % (offsetyi + y_val))


def read_template(template_path):
    if not template_path:
        return list(DEFAULT_HEADER_LINES), []
    lines = Path(template_path).read_text().splitlines()
    smd_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("#SMD"):
            smd_idx = idx
            break
    if smd_idx is None:
        return list(DEFAULT_HEADER_LINES), []
    header_lines = lines[: smd_idx + 1]
    comp_lines = lines[smd_idx + 1 :]
    return header_lines, comp_lines


def build_feeder_maps(comp_lines):
    by_ref = {}
    by_fp_val = {}
    by_fp = {}
    for line in comp_lines:
        if not line.startswith("comp,"):
            continue
        parts = line.split(",")
        if len(parts) < 10:
            continue
        _, feeder_id, nozzle, name, value, footprint, _, _, _, skip, *_ = parts
        key_ref = (name, value, footprint)
        if key_ref not in by_ref:
            by_ref[key_ref] = (feeder_id, nozzle, skip)
        key_fp_val = (footprint, value)
        if key_fp_val not in by_fp_val:
            by_fp_val[key_fp_val] = (feeder_id, nozzle, skip)
        if footprint and footprint not in by_fp:
            by_fp[footprint] = (feeder_id, nozzle, skip)
    return by_ref, by_fp_val, by_fp


def _fmt_float(value):
    try:
        return "{:.2f}".format(float(value))
    except (TypeError, ValueError):
        return ""


def normalize_value(ref, value):
    if value is None:
        return ""
    v = str(value).strip().lower()
    if not v:
        return ""
    if ref.upper().startswith("C"):
        if v.endswith("f") and any(ch.isdigit() for ch in v):
            v = v[:-1]
    return v


def normalize_footprint(footprint):
    if footprint is None:
        return ""
    return str(footprint).strip().lower()


def load_global_offset(path):
    if not path or not Path(path).exists():
        return {"dx": 0.0, "dy": 0.0, "drot": 0.0}
    try:
        payload = json.loads(Path(path).read_text())
    except Exception:
        return {"dx": 0.0, "dy": 0.0, "drot": 0.0}
    global_off = payload.get("global", {})
    return {
        "dx": float(global_off.get("dx", 0.0)),
        "dy": float(global_off.get("dy", 0.0)),
        "drot": float(global_off.get("drot", 0.0)),
    }


def load_feeder_assignment_csv(path):
    if not path or not Path(path).exists():
        return {}, {}, []
    map_by_fp_val = {}
    map_by_fp = {}
    stack_entries = []
    with Path(path).open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            feeder_id = (row.get("feeder_id") or "").strip()
            if not feeder_id:
                continue
            footprint = normalize_footprint(row.get("footprint"))
            value = normalize_value("C", row.get("value"))
            if footprint and value:
                map_by_fp_val[(footprint, value)] = feeder_id
            elif footprint:
                map_by_fp[footprint] = feeder_id
            stack_entries.append(row)
    return map_by_fp_val, map_by_fp, stack_entries


def choose_feeder(name, value, footprint, maps, defaults, csv_maps):
    by_ref, by_fp_val, by_fp = maps
    csv_by_fp_val, csv_by_fp = csv_maps
    norm_value = normalize_value(name, value)
    norm_footprint = normalize_footprint(footprint)
    if (norm_footprint, norm_value) in csv_by_fp_val:
        return csv_by_fp_val[(norm_footprint, norm_value)], "1", defaults[2]
    if norm_footprint in csv_by_fp:
        return csv_by_fp[norm_footprint], "1", defaults[2]
    if (name, value, footprint) in by_ref:
        feeder_id, nozzle, skip = by_ref[(name, value, footprint)]
        return feeder_id, "1", skip
    if (footprint, value) in by_fp_val:
        feeder_id, nozzle, skip = by_fp_val[(footprint, value)]
        return feeder_id, "1", skip
    if footprint in by_fp:
        feeder_id, nozzle, skip = by_fp[footprint]
        return feeder_id, "1", skip
    return defaults


def format_comp_line(name, value, footprint, x, y, rotation, feeder_id, nozzle, skip):
    return (
        "comp,{},{},{},{},{},{:.2f},{:.2f},{:.2f},{},"
    ).format(feeder_id, nozzle, name, value, footprint, x, y, rotation, skip)


def process_pos_lines(
    pos_lines,
    header_lines,
    maps,
    defaults,
    csv_maps,
    side_filter,
    global_offset,
):
    output_lines = list(header_lines)
    missing = []
    coord_map = {}
    for line in pos_lines:
        if not line or line[0].startswith("#"):
            continue
        if len(line) < 6:
            continue
        try:
            x_val = float(line[3])
            y_val = float(line[4])
        except ValueError:
            continue
        if side_filter != "all" and line[-1].lower() != side_filter:
            continue
        name = line[0]
        value = line[1]
        footprint = line[2]
        x = x_val
        y = y_val
        rotation = transrotate(float(line[5]))
        feeder_id, nozzle, skip = choose_feeder(
            name, value, footprint, maps, defaults, csv_maps
        )
        x += global_offset["dx"]
        y += global_offset["dy"]
        rotation += global_offset["drot"]
        if feeder_id == defaults[0]:
            missing.append((name, value, footprint))
        coord_key = ("{:.4f}".format(x), "{:.4f}".format(y))
        coord_map.setdefault(coord_key, []).append((name, value, footprint))
        output_lines.append(
            format_comp_line(
                name, value, footprint, x, y, rotation, feeder_id, nozzle, skip
            )
        )
    return "\n".join(output_lines), missing, coord_map


def build_stack_line(row):
    feeder_id = (row.get("feeder_id") or "").strip()
    if not feeder_id:
        return None
    footprint = (row.get("footprint") or "").strip()
    value = (row.get("value") or "").strip()
    combined = ""
    if footprint and value:
        combined = footprint + "/" + value
    entry = [
        "stack",
        feeder_id,
        (row.get("type_code") or "0").strip(),
        (row.get("nozzle") or "").strip(),
        _fmt_float(row.get("x")),
        _fmt_float(row.get("y")),
        _fmt_float(row.get("angle")),
        (row.get("package") or "").strip(),
        combined,
        _fmt_float(row.get("pick_height")),
        (row.get("pick_delay") or "").strip(),
        _fmt_float(row.get("place_height")),
        (row.get("place_delay") or "").strip(),
        (row.get("vacuum_detection") or "").strip(),
        (row.get("threshold") or "").strip(),
        (row.get("vision_alignment") or "").strip(),
        (row.get("speed") or "").strip(),
    ]
    extra = row.get("extra") or ""
    if extra:
        entry.extend(extra.split("|"))
    entry.append("")
    return ",".join(entry)


def apply_feeder_csv_to_header(header_lines, stack_rows):
    if not stack_rows:
        return header_lines
    stack_lines = []
    for row in stack_rows:
        if (row.get("type") or "stack").strip() != "stack":
            continue
        line = build_stack_line(row)
        if line:
            stack_lines.append(line)
    if not stack_lines:
        return header_lines
    new_header = []
    inserted = False
    for idx, line in enumerate(header_lines):
        if idx == 0:
            new_header.append(line)
            new_header.extend(stack_lines)
            inserted = True
            continue
        if line.startswith("stack,"):
            continue
        new_header.append(line)
    if not inserted:
        new_header = stack_lines + new_header
    return new_header


def update_mirror_create(header_lines, chip1x, chip1y):
    updated = []
    for line in header_lines:
        if line.startswith("mirror_create,"):
            parts = line.split(",")
            # mirror_create,1,1,<x>,<y>,0,0,0,0,0,
            if len(parts) >= 5:
                parts[3] = "{:.2f}".format(float(chip1x))
                parts[4] = "{:.2f}".format(float(chip1y))
                line = ",".join(parts)
        updated.append(line)
    return updated


def update_mirror(header_lines, chip1x, chip1y):
    updated = []
    for line in header_lines:
        if line.startswith("mirror,"):
            parts = line.split(",")
            # mirror,<x>,<y>,0,No,
            if len(parts) >= 3:
                parts[1] = "{:.2f}".format(float(chip1x))
                parts[2] = "{:.2f}".format(float(chip1y))
                line = ",".join(parts)
        updated.append(line)
    return updated


def main():
    parser = argparse.ArgumentParser(
        description="Convert KiCAD .pos to a NEODEN project-style .csv file."
    )
    parser.add_argument("pos_file", help="KiCAD .pos input file")
    # Template is fixed to the known Neoden project baseline.
    parser.add_argument(
        "--output",
        help="Output .csv file path",
        default=None,
    )
    parser.add_argument(
        "--side",
        choices=["top", "bottom", "all"],
        default="all",
        help="Filter components by side",
    )
    parser.add_argument(
        "--feeder-assignment-csv",
        help="CSV mapping footprint/value to feeder setup and assignments",
        default=DEFAULT_FEEDER_ASSIGNMENT_PATH,
    )
    parser.add_argument("--default-feeder-id", default="1")
    parser.add_argument("--default-nozzle", default="1")
    parser.add_argument("--default-skip", default="No")
    args = parser.parse_args()

    if not args.pos_file.endswith(".pos"):
        print("WARNING: Input file doesn't have expected '.pos' extension")

    pos_lines = parse_pos_file(args.pos_file)

    print("Parsing " + args.pos_file)
    print("\nWe will offset positions according to Chip_1 on Neoden")
    chip1xipos = input("Give Chip_1 X position: ")
    chip1yipos = input("Give Chip_1 Y position: ")
    print("Calculating Offset")
    offsetxi, offsetyi = compute_offsets(pos_lines, chip1xipos, chip1yipos)
    print("X_offset: " + str(offsetxi) + " -- Y_offset: " + str(offsetyi))
    apply_offsets(pos_lines, offsetxi, offsetyi)

    template_path = DEFAULT_TEMPLATE_PATH
    if not Path(template_path).exists():
        print("ERROR: Default template not found:", template_path)
        return
    header_lines, comp_lines = read_template(template_path)
    maps = build_feeder_maps(comp_lines)
    defaults = (
        args.default_feeder_id,
        args.default_nozzle,
        args.default_skip,
    )
    feeder_map_path = args.feeder_assignment_csv
    if feeder_map_path and not Path(feeder_map_path).exists():
        for candidate in FALLBACK_FEEDER_ASSIGNMENT_PATHS:
            if Path(candidate).exists():
                feeder_map_path = candidate
                break
        else:
            feeder_map_path = None
    csv_by_fp_val, csv_by_fp, stack_rows = load_feeder_assignment_csv(
        feeder_map_path
    )
    header_lines = apply_feeder_csv_to_header(header_lines, stack_rows)
    header_lines = update_mirror_create(header_lines, chip1xipos, chip1yipos)
    header_lines = update_mirror(header_lines, chip1xipos, chip1yipos)
    csv_maps = (csv_by_fp_val, csv_by_fp)
    neoden_project, missing, coord_map = process_pos_lines(
        pos_lines,
        header_lines,
        maps,
        defaults,
        csv_maps,
        args.side,
        {"dx": 0.0, "dy": 0.0, "drot": 0.0},
    )

    output_file = args.output
    if not output_file:
        stem = os.path.splitext(args.pos_file)[0]
        output_file = stem + "_neoden_project.csv"

    Path(output_file).write_text(neoden_project)
    print("Successfully wrote:", output_file)
    if missing:
        print("WARNING: Components with no feeder match (assigned default feeder):")
        for name, value, footprint in missing:
            print("  -", name, value, footprint)
    dupes = {k: v for k, v in coord_map.items() if len(v) > 1}
    if dupes:
        print("WARNING: Duplicate coordinates detected:")
        for (x_str, y_str), items in sorted(dupes.items()):
            print("  -", x_str, y_str)
            for name, value, footprint in items:
                print("    *", name, value, footprint)


if __name__ == "__main__":
    main()
