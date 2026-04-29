from __future__ import print_function

import argparse
from pathlib import Path


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


def build_pos_map(pos_lines, side_filter):
    pos_map = {}
    for line in pos_lines:
        if not line or line[0].startswith("#"):
            continue
        if len(line) < 6:
            continue
        if side_filter != "all" and line[-1].lower() != side_filter:
            continue
        name = line[0]
        value = line[1]
        footprint = line[2]
        try:
            x = float(line[3])
            y = float(line[4])
            rotation = transrotate(float(line[5]))
        except ValueError:
            continue
        key = (name, value, footprint)
        pos_map.setdefault(key, []).append((x, y, rotation))
    return pos_map


def update_project_positions(project_lines, pos_map):
    updated_lines = []
    counters = {}
    missing = []
    for line in project_lines:
        if not line.startswith("comp,"):
            updated_lines.append(line)
            continue
        parts = line.split(",")
        if len(parts) < 10:
            updated_lines.append(line)
            continue
        name = parts[3].strip()
        value = parts[4].strip()
        footprint = parts[5].strip()
        key = (name, value, footprint)
        idx = counters.get(key, 0)
        counters[key] = idx + 1
        if key not in pos_map or idx >= len(pos_map[key]):
            missing.append(key)
            updated_lines.append(line)
            continue
        x, y, rot = pos_map[key][idx]
        parts[6] = "{:.2f}".format(x)
        parts[7] = "{:.2f}".format(y)
        parts[8] = "{:.2f}".format(rot)
        updated_lines.append(",".join(parts))
    return updated_lines, missing


def main():
    parser = argparse.ArgumentParser(
        description="Update component positions in a Neoden project from a KiCad .pos file."
    )
    parser.add_argument("project_csv", help="Neoden project CSV to update")
    parser.add_argument("pos_file", help="KiCad .pos input file")
    parser.add_argument(
        "--side",
        choices=["top", "bottom", "all"],
        default="all",
        help="Filter components by side",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output project CSV path (default: <project>_updated.csv)",
    )
    args = parser.parse_args()

    project_path = Path(args.project_csv)
    if not project_path.exists():
        print("ERROR: Project CSV not found:", project_path)
        return
    pos_path = Path(args.pos_file)
    if not pos_path.exists():
        print("ERROR: POS file not found:", pos_path)
        return

    pos_lines = parse_pos_file(pos_path)

    print("\nWe will offset positions according to Chip_1 on Neoden")
    chip1xipos = input("Give Chip_1 X position: ")
    chip1yipos = input("Give Chip_1 Y position: ")
    print("Calculating Offset")
    offsetxi, offsetyi = compute_offsets(pos_lines, chip1xipos, chip1yipos)
    print("X_offset: " + str(offsetxi) + " -- Y_offset: " + str(offsetyi))
    apply_offsets(pos_lines, offsetxi, offsetyi)

    pos_map = build_pos_map(pos_lines, args.side)
    project_lines = project_path.read_text().splitlines()
    updated_lines, missing = update_project_positions(project_lines, pos_map)

    output_path = args.output
    if not output_path:
        output_path = str(project_path.with_name(project_path.stem + "_updated.csv"))
    Path(output_path).write_text("\n".join(updated_lines) + "\n")
    print("Successfully wrote:", output_path)
    if missing:
        print("WARNING: Components with no matching POS entry:")
        for key in sorted(set(missing)):
            print("  -", key[0], key[1], key[2])


if __name__ == "__main__":
    main()
