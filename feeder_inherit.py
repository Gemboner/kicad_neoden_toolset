from __future__ import print_function

import argparse
import csv
import json
from pathlib import Path


DEFAULT_PROJECT_PATH = "practice-board-panel-bot/PracticeBoard-bottom_neoden_project_edited.csv"
DEFAULT_TEMPLATE_PROJECT_PATH = "template_project.csv"
DEFAULT_FEEDER_ASSIGNMENT_PATHS = [
    "feeder_assignment.csv",
]
DEFAULT_FEEDER_JSON_PATHS = [
    "feeder_config.json",
]


CSV_HEADER = [
    "feeder_id",
    "type",
    "type_code",
    "nozzle",
    "x",
    "y",
    "angle",
    "package",
    "footprint",
    "value",
    "pick_height",
    "pick_delay",
    "place_height",
    "place_delay",
    "vacuum_detection",
    "threshold",
    "vision_alignment",
    "speed",
    "x_offset",
    "y_offset",
    "rotation_offset",
    "extra",
]


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_stack_line(line):
    parts = line.split(",")
    if len(parts) < 17:
        return None
    feeder_id = parts[1].strip()
    if not feeder_id:
        return None
    combined = parts[8].strip() if len(parts) > 8 else ""
    footprint = ""
    value = ""
    if combined and "/" in combined:
        footprint, value = combined.split("/", 1)
    extra = parts[17:]
    while extra and extra[-1] == "":
        extra.pop()
    return {
        "feeder_id": feeder_id,
        "type": "stack",
        "type_code": parts[2].strip(),
        "nozzle": parts[3].strip(),
        "x": parts[4].strip(),
        "y": parts[5].strip(),
        "angle": parts[6].strip(),
        "package": parts[7].strip(),
        "footprint": footprint.strip(),
        "value": value.strip(),
        "pick_height": parts[9].strip() if len(parts) > 9 else "",
        "pick_delay": parts[10].strip() if len(parts) > 10 else "",
        "place_height": parts[11].strip() if len(parts) > 11 else "",
        "place_delay": parts[12].strip() if len(parts) > 12 else "",
        "vacuum_detection": parts[13].strip() if len(parts) > 13 else "",
        "threshold": parts[14].strip() if len(parts) > 14 else "",
        "vision_alignment": parts[15].strip() if len(parts) > 15 else "",
        "speed": parts[16].strip() if len(parts) > 16 else "",
        "extra": "|".join(extra),
    }


def load_feeder_assignment_csv(path):
    rows = {}
    if not Path(path).exists():
        return rows
    with Path(path).open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            feeder_id = (row.get("feeder_id") or "").strip()
            if feeder_id:
                rows[feeder_id] = row
    return rows


def default_row(feeder_id):
    return {
        "feeder_id": feeder_id,
        "type": "stack",
        "type_code": "0",
        "nozzle": "",
        "x": "",
        "y": "",
        "angle": "",
        "package": "",
        "footprint": "",
        "value": "",
        "pick_height": "",
        "pick_delay": "",
        "place_height": "",
        "place_delay": "",
        "vacuum_detection": "",
        "threshold": "",
        "vision_alignment": "",
        "speed": "",
        "x_offset": "0",
        "y_offset": "0",
        "rotation_offset": "0",
        "extra": "",
    }


def merge_stack_rows(existing_rows, stack_rows):
    for stack_row in stack_rows:
        feeder_id = stack_row["feeder_id"]
        row = existing_rows.get(feeder_id, default_row(feeder_id))
        for key in CSV_HEADER:
            if key in stack_row and stack_row[key] != "":
                row[key] = stack_row[key]
        existing_rows[feeder_id] = row
    return existing_rows


def write_feeder_assignment_csv(path, rows, max_id):
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for fid in range(1, max_id + 1):
            key = str(fid)
            row = rows.get(key, default_row(key))
            writer.writerow({k: row.get(k, "") for k in CSV_HEADER})


def build_feeder_json(rows, source_path, max_id):
    feeders = {}
    for fid in range(1, max_id + 1):
        key = str(fid)
        row = rows.get(key)
        if not row:
            continue
        feeders[key] = {
            "type": row.get("type", "stack"),
            "type_code": row.get("type_code", "0"),
            "feeder_id": key,
            "nozzle": row.get("nozzle", ""),
            "x": _to_float(row.get("x")),
            "y": _to_float(row.get("y")),
            "angle": _to_float(row.get("angle")),
            "package": row.get("package", ""),
            "footprint": row.get("footprint", ""),
            "value": row.get("value", ""),
            "pick_height": _to_float(row.get("pick_height")),
            "pick_delay": _to_int(row.get("pick_delay")),
            "place_height": _to_float(row.get("place_height")),
            "place_delay": _to_int(row.get("place_delay")),
            "vacuum_detection": row.get("vacuum_detection", ""),
            "threshold": _to_int(row.get("threshold")),
            "vision_alignment": _to_int(row.get("vision_alignment")),
            "speed": _to_int(row.get("speed")),
            "x_offset": _to_float(row.get("x_offset")),
            "y_offset": _to_float(row.get("y_offset")),
            "rotation_offset": _to_float(row.get("rotation_offset")),
            "extra": (row.get("extra") or "").split("|") if row.get("extra") else [],
        }
    return {"source": str(source_path), "feeders": feeders}


def main():
    parser = argparse.ArgumentParser(
        description="Inherit feeder assignments and configs from a Neoden4 project CSV."
    )
    parser.add_argument(
        "project_csv",
        nargs="?",
        default=DEFAULT_PROJECT_PATH,
        help="Neoden4 project CSV to inherit from",
    )
    args = parser.parse_args()

    project_path = Path(args.project_csv)
    if not project_path.exists():
        print("ERROR: Project CSV not found:", project_path)
        return

    # Keep a local template project in ./ up to date with the provided project CSV.
    Path(DEFAULT_TEMPLATE_PROJECT_PATH).write_text(project_path.read_text())

    stack_rows = []
    for line in project_path.read_text().splitlines():
        if line.startswith("stack,"):
            row = parse_stack_line(line)
            if row:
                stack_rows.append(row)

    if not stack_rows:
        print("ERROR: No stack lines found in:", project_path)
        return

    for csv_path in DEFAULT_FEEDER_ASSIGNMENT_PATHS:
        rows = load_feeder_assignment_csv(csv_path)
        rows = merge_stack_rows(rows, stack_rows)
        max_id = 0
        for key in rows.keys():
            if str(key).isdigit():
                max_id = max(max_id, int(key))
        for row in stack_rows:
            if row.get("feeder_id", "").isdigit():
                max_id = max(max_id, int(row["feeder_id"]))
        if max_id < 1:
            max_id = 48
        write_feeder_assignment_csv(csv_path, rows, max_id)
        json_data = build_feeder_json(rows, project_path, max_id)
        for json_path in DEFAULT_FEEDER_JSON_PATHS:
            Path(json_path).write_text(json.dumps(json_data, indent=2, sort_keys=True))
            print("Updated:", json_path)
        print("Updated:", csv_path)


if __name__ == "__main__":
    main()
