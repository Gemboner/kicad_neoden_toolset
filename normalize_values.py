from __future__ import print_function

import argparse
from pathlib import Path


def normalize_value(ref, value):
    if not value:
        return value
    ref_upper = ref.upper()
    val = value.strip()
    if ref_upper.startswith("C"):
        val_lower = val.lower()
        if val_lower.endswith("f"):
            return val
        if val_lower.endswith("n"):
            return val + "F"
    return val


def normalize_pos_file(path, output_path=None):
    src = Path(path)
    out = Path(output_path) if output_path else src.with_name(src.stem + "_normalized.pos")
    out_lines = []
    for line in src.read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            out_lines.append(line)
            continue
        ref = parts[0]
        value = parts[1] if len(parts) > 1 else ""
        new_value = normalize_value(ref, value)
        if new_value != value:
            parts[1] = new_value
            out_lines.append(" ".join(parts))
        else:
            out_lines.append(line)
    out.write_text("\n".join(out_lines) + "\n")
    return out


def main():
    parser = argparse.ArgumentParser(
        description="Normalize capacitor values in KiCad .pos files (e.g., 100n -> 100nF)."
    )
    parser.add_argument("pos_file", help="Input .pos file")
    parser.add_argument(
        "--output",
        help="Output .pos file path (default: <input>_normalized.pos)",
        default=None,
    )
    args = parser.parse_args()

    output = normalize_pos_file(args.pos_file, args.output)
    print("Wrote:", output)


if __name__ == "__main__":
    main()
