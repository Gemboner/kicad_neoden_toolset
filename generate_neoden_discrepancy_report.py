from __future__ import print_function

import argparse
import csv
from math import hypot
from pathlib import Path


DEFAULT_BASE = "practice-board-panel-bot/PracticeBoard-bottom_neoden_project.csv"
DEFAULT_EDITED = "practice-board-panel-bot/PracticeBoard-bottom_neoden_project_edited.csv"
DEFAULT_OUT = "practice-board-panel-bot/neoden_discrepancy_report.txt"
DEFAULT_OFFSET = "global_offset.json"


def load_components(path):
    comps = {}
    with Path(path).open(newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].startswith("comp"):
                continue
            if len(row) < 10:
                continue
            name = row[3].strip()
            value = row[4].strip()
            footprint = row[5].strip()
            try:
                x = float(row[6])
                y = float(row[7])
                rot = float(row[8])
            except ValueError:
                continue
            key = (name, value, footprint)
            comps.setdefault(key, []).append((x, y, rot))
    return comps


def load_offsets(path):
    if not Path(path).exists():
        return {"global": {"dx": 0.0, "dy": 0.0, "drot": 0.0}, "per_footprint": {}}
    data = Path(path).read_text()
    try:
        import json

        payload = json.loads(data)
    except Exception:
        return {"global": {"dx": 0.0, "dy": 0.0, "drot": 0.0}, "per_footprint": {}}
    return {
        "global": payload.get("global", {"dx": 0.0, "dy": 0.0, "drot": 0.0}),
        "per_footprint": payload.get("per_footprint", {}),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate discrepancy report between base and edited Neoden project CSVs."
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="Base project CSV")
    parser.add_argument("--edited", default=DEFAULT_EDITED, help="Edited project CSV")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output report path")
    parser.add_argument(
        "--offset",
        default=DEFAULT_OFFSET,
        help="Global/per-footprint offset JSON to apply when comparing",
    )
    args = parser.parse_args()

    base_path = Path(args.base)
    edited_path = Path(args.edited)

    if not base_path.exists():
        print("ERROR: base file not found:", base_path)
        return
    if not edited_path.exists():
        print("ERROR: edited file not found:", edited_path)
        return

    base = load_components(base_path)
    edited = load_components(edited_path)
    offsets = load_offsets(args.offset)
    global_off = offsets.get("global", {})
    per_fp = offsets.get("per_footprint", {})

    report = []
    missing = []
    extra = []

    for key, base_list in base.items():
        edit_list = edited.get(key)
        if not edit_list:
            missing.append(key)
            continue
        for i, b in enumerate(base_list):
            if i >= len(edit_list):
                missing.append((key, "instance", i))
                continue
            e = edit_list[i]
            fp = key[2]
            fp_off = per_fp.get(fp, {"dx": 0.0, "dy": 0.0, "drot": 0.0})
            adj_x = b[0] + float(global_off.get("dx", 0.0)) + float(fp_off.get("dx", 0.0))
            adj_y = b[1] + float(global_off.get("dy", 0.0)) + float(fp_off.get("dy", 0.0))
            adj_r = b[2] + float(global_off.get("drot", 0.0)) + float(fp_off.get("drot", 0.0))
            dx = e[0] - adj_x
            dy = e[1] - adj_y
            dr = e[2] - adj_r
            report.append((key, i, b, e, dx, dy, dr, hypot(dx, dy)))

    for key, edit_list in edited.items():
        if key not in base:
            extra.append(key)
        elif len(edit_list) > len(base[key]):
            extra.append((key, "extra_instances", len(edit_list) - len(base[key])))

    if report:
        avg_dx = sum(r[4] for r in report) / len(report)
        avg_dy = sum(r[5] for r in report) / len(report)
        avg_dr = sum(r[6] for r in report) / len(report)
        avg_d = sum(r[7] for r in report) / len(report)
    else:
        avg_dx = avg_dy = avg_dr = avg_d = 0.0

    report_sorted = sorted(report, key=lambda r: r[7], reverse=True)

    out = []
    out.append(f"Base comps: {sum(len(v) for v in base.values())}")
    out.append(f"Edited comps: {sum(len(v) for v in edited.values())}")
    out.append(f"Matched instances: {len(report)}")
    out.append(f"Missing in edited: {len(missing)}")
    out.append(f"Extra in edited: {len(extra)}")
    out.append(
        f"Avg dx: {avg_dx:.4f} mm, Avg dy: {avg_dy:.4f} mm, "
        f"Avg dr: {avg_dr:.2f} deg, Avg |d|: {avg_d:.4f} mm"
    )
    out.append(
        f"Applied global offset: dX={float(global_off.get('dx',0.0)):.4f} "
        f"dY={float(global_off.get('dy',0.0)):.4f} dRot={float(global_off.get('drot',0.0)):.2f}"
    )

    out.append("\nTop 25 position deltas (by distance):")
    for r in report_sorted[:25]:
        (name, value, footprint), idx, b, e, dx, dy, dr, dist = r
        out.append(
            f"{name} {value} {footprint} #{idx+1}: "
            f"dX={dx:.4f} dY={dy:.4f} |d|={dist:.4f} dRot={dr:.2f} "
            f"(base {b[0]:.2f},{b[1]:.2f},{b[2]:.2f} -> "
            f"edit {e[0]:.2f},{e[1]:.2f},{e[2]:.2f})"
        )

    by_fp = {}
    for r in report:
        fp = r[0][2]
        by_fp.setdefault(fp, []).append(r)

    out.append("\nAverage offsets by footprint (min 5 parts):")
    for fp, items in sorted(by_fp.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 5:
            continue
        adx = sum(r[4] for r in items) / len(items)
        ady = sum(r[5] for r in items) / len(items)
        adr = sum(r[6] for r in items) / len(items)
        out.append(f"{fp}: n={len(items)} avg dX={adx:.4f} dY={ady:.4f} dRot={adr:.2f}")

    by_fp_val = {}
    for r in report:
        fpv = (r[0][2], r[0][1])
        by_fp_val.setdefault(fpv, []).append(r)

    out.append("\nAverage offsets by footprint+value (min 3 parts):")
    for (fp, val), items in sorted(by_fp_val.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 3:
            continue
        adx = sum(r[4] for r in items) / len(items)
        ady = sum(r[5] for r in items) / len(items)
        adr = sum(r[6] for r in items) / len(items)
        out.append(
            f"{fp} / {val}: n={len(items)} avg dX={adx:.4f} dY={ady:.4f} dRot={adr:.2f}"
        )

    out.append("\nPer-ref deltas (all instances):")
    for r in report_sorted:
        (name, value, footprint), idx, b, e, dx, dy, dr, dist = r
        out.append(
            f"{name} {value} {footprint} #{idx+1}: "
            f"dX={dx:.4f} dY={dy:.4f} |d|={dist:.4f} dRot={dr:.2f}"
        )

    out.append("\nProposed correction modifiers:")
    out.append("Global: subtract avg dX/dY and avg dRot from generated placement.")
    out.append(f"  dX={avg_dx:.4f} dY={avg_dy:.4f} dRot={avg_dr:.2f}")
    out.append(
        "Per-footprint (use avg dX/dY/dRot from footprint section above when available)."
    )
    out.append(
        "Per-footprint+value (use avg dX/dY/dRot from footprint+value section above when available)."
    )

    # Panic list for unknown footprints (not in offset map)
    unseen = set()
    if per_fp is not None:
        for key in base.keys():
            fp = key[2]
            if fp and fp not in per_fp:
                unseen.add(fp)
    if unseen:
        out.append("\nPANIC_UNKNOWN_FOOTPRINTS:")
        for fp in sorted(unseen):
            out.append(f"PANIC_UNKNOWN_FOOTPRINT {fp}")

    out_path = Path(args.out)
    out_path.write_text("\n".join(out) + "\n")
    print("Wrote", out_path)


if __name__ == "__main__":
    main()
