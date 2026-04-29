from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path


@dataclass
class Component:
    ref: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float
    side: str


@dataclass
class PreviewComponent:
    component: Component
    rel_x: float
    rel_y: float
    width: float
    height: float


def parse_pos_file(path: Path, side_filter: str) -> list[Component]:
    components = []
    for raw in path.read_text().splitlines():
        parts = raw.split()
        if not parts or parts[0].startswith("#"):
            continue
        if len(parts) < 7:
            continue
        try:
            x = float(parts[-4])
            y = float(parts[-3])
            rotation = float(parts[-2])
        except ValueError:
            continue
        side = parts[-1].lower()
        if side_filter != "all" and side != side_filter:
            continue
        ref = parts[0]
        value = parts[1]
        footprint = " ".join(parts[2:-4]).strip()
        components.append(
            Component(
                ref=ref,
                value=value,
                footprint=footprint,
                x=x,
                y=y,
                rotation=rotation,
                side=side,
            )
        )
    return components


def infer_size_mm(component: Component) -> tuple[float, float]:
    text = " ".join([component.ref, component.value, component.footprint])
    lower_text = text.lower()

    if "fiducial" in lower_text:
        return 1.0, 1.0

    metric_codes = re.findall(r"(\d{4})Metric", component.footprint, flags=re.IGNORECASE)
    if metric_codes:
        code = metric_codes[-1]
        return max(int(code[:2]) / 10.0, 0.5), max(int(code[2:]) / 10.0, 0.5)

    pairs = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", component.footprint):
        raw_w = float(match.group(1))
        raw_h = float(match.group(2))
        if raw_w >= 50 and raw_h >= 50:
            width = raw_w / 100.0
            height = raw_h / 100.0
        elif raw_w >= 5 and raw_h >= 5:
            width = raw_w
            height = raw_h
        else:
            continue
        pairs.append((width, height))
    if pairs:
        return max(pairs, key=lambda item: item[0] * item[1])

    ref_upper = component.ref.upper()
    if ref_upper.startswith(("C", "R", "L", "FB")):
        return 1.6, 0.8
    if ref_upper.startswith("D"):
        return 1.6, 0.8
    if ref_upper.startswith(("IC", "U")):
        return 5.0, 5.0
    if ref_upper.startswith("J"):
        return 8.0, 3.0
    if ref_upper.startswith("Q"):
        return 4.0, 4.0
    return 2.5, 1.5


def normalize_components(components: list[Component]) -> tuple[Component, list[PreviewComponent]]:
    anchor = components[0]
    preview = []
    for component in components:
        width, height = infer_size_mm(component)
        preview.append(
            PreviewComponent(
                component=component,
                rel_x=component.x - anchor.x,
                rel_y=component.y - anchor.y,
                width=width,
                height=height,
            )
        )
    return anchor, preview


def rotated_extent(component: PreviewComponent) -> tuple[float, float]:
    angle = math.radians(component.component.rotation % 180.0)
    cos_a = abs(math.cos(angle))
    sin_a = abs(math.sin(angle))
    dx = (component.width * cos_a + component.height * sin_a) / 2.0
    dy = (component.width * sin_a + component.height * cos_a) / 2.0
    return dx, dy


def compute_bounds(
    components: list[PreviewComponent], fit_all: bool
) -> tuple[float, float, float, float, int, int]:
    min_x = 0.0 if not fit_all else float("inf")
    min_y = 0.0 if not fit_all else float("inf")
    max_x = 0.0
    max_y = 0.0
    clipped_left = 0
    clipped_bottom = 0

    for component in components:
        dx, dy = rotated_extent(component)
        comp_min_x = component.rel_x - dx
        comp_max_x = component.rel_x + dx
        comp_min_y = component.rel_y - dy
        comp_max_y = component.rel_y + dy
        if fit_all:
            min_x = min(min_x, comp_min_x)
            min_y = min(min_y, comp_min_y)
        else:
            if comp_min_x < 0:
                clipped_left += 1
            if comp_min_y < 0:
                clipped_bottom += 1
        max_x = max(max_x, comp_max_x)
        max_y = max(max_y, comp_max_y)

    if fit_all and min_x == float("inf"):
        min_x = 0.0
        min_y = 0.0

    return min_x, min_y, max_x, max_y, clipped_left, clipped_bottom


def color_for(component: Component) -> str:
    ref_upper = component.ref.upper()
    if "fiducial" in component.footprint.lower() or "fiducial" in component.value.lower():
        return "#111111"
    if ref_upper.startswith("C"):
        return "#2f6fdd"
    if ref_upper.startswith("R"):
        return "#2a9d55"
    if ref_upper.startswith(("IC", "U")):
        return "#c84d3a"
    if ref_upper.startswith("J"):
        return "#d1830f"
    if ref_upper.startswith("D"):
        return "#9c3fb3"
    return "#586174"


def render_svg(
    components: list[PreviewComponent],
    source_path: Path,
    output_path: Path,
    anchor: Component,
    canvas_width: int,
    canvas_height: int,
    margin: int,
    labels: bool,
    fit_all: bool,
    body_scale: float,
) -> tuple[int, int]:
    min_x, min_y, max_x, max_y, clipped_left, clipped_bottom = compute_bounds(
        components, fit_all=fit_all
    )

    plot_width_mm = max(max_x - min_x, 1.0)
    plot_height_mm = max(max_y - min_y, 1.0)

    header_height = 76
    plot_left = margin
    plot_right = canvas_width - margin
    plot_top = header_height
    plot_bottom = canvas_height - margin
    usable_width = max(canvas_width - (2 * margin), 1)
    usable_height = max(canvas_height - header_height - margin, 1)
    scale = min(usable_width / plot_width_mm, usable_height / plot_height_mm)
    origin_x = margin - (min_x * scale)
    origin_y = canvas_height - margin + (min_y * scale)

    grid_step_mm = 10
    grid_lines = []
    grid_start_x = math.floor(min_x / grid_step_mm) * grid_step_mm
    grid_end_x = math.ceil(max_x / grid_step_mm) * grid_step_mm
    grid_start_y = math.floor(min_y / grid_step_mm) * grid_step_mm
    grid_end_y = math.ceil(max_y / grid_step_mm) * grid_step_mm
    for value in range(int(grid_start_x), int(grid_end_x) + 1, grid_step_mm):
        x_px = origin_x + value * scale
        if plot_left <= x_px <= plot_right:
            grid_lines.append(
                '<line x1="{0:.2f}" y1="{1:.2f}" x2="{0:.2f}" y2="{2:.2f}" '
                'stroke="#e7ecf3" stroke-width="1" />'.format(x_px, plot_top, plot_bottom)
            )
    for value in range(int(grid_start_y), int(grid_end_y) + 1, grid_step_mm):
        y_px = origin_y - value * scale
        if plot_top <= y_px <= plot_bottom:
            grid_lines.append(
                '<line x1="{0:.2f}" y1="{1:.2f}" x2="{2:.2f}" y2="{1:.2f}" '
                'stroke="#e7ecf3" stroke-width="1" />'.format(plot_left, y_px, plot_right)
            )

    elements = []
    for item in components:
        component = item.component
        cx = origin_x + item.rel_x * scale
        cy = origin_y - item.rel_y * scale
        width_px = max(item.width * scale * body_scale, 3.0)
        height_px = max(item.height * scale * body_scale, 3.0)
        color = color_for(component)
        ref_label = escape(component.ref)
        title = escape(
            f"{component.ref} | {component.value} | {component.footprint} | "
            f"({component.x:.4f}, {component.y:.4f}) mm | {component.rotation:.1f} deg"
        )

        if "fiducial" in component.footprint.lower() or "fiducial" in component.value.lower():
            radius = max(min(width_px, height_px) / 2.0, 4.0)
            shape = """
<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{radius:.2f}" fill="#ffffff" stroke="{color}" stroke-width="1.6" />
<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{inner:.2f}" fill="{color}" opacity="0.95" />
""".format(cx=cx, cy=cy, radius=radius, inner=max(radius * 0.32, 2.0), color=color).strip()
        else:
            x_px = cx - width_px / 2.0
            y_px = cy - height_px / 2.0
            angle = math.radians(component.rotation)
            orient_len = max(min(max(width_px, height_px) * 0.6, 14.0), 6.0)
            orient_x = cx + math.cos(angle) * orient_len
            orient_y = cy - math.sin(angle) * orient_len
            dot_radius = max(min(min(width_px, height_px) * 0.22, 3.2), 1.8)
            shape = """
<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" rx="1.5" ry="1.5"
      fill="none" stroke="{color}" stroke-width="0.9" opacity="0.28"
      transform="rotate({rotation:.2f} {cx:.2f} {cy:.2f})" />
<line x1="{cx:.2f}" y1="{cy:.2f}" x2="{ox:.2f}" y2="{oy:.2f}"
      stroke="{color}" stroke-width="1.2" stroke-linecap="round" opacity="0.9" />
<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{dot_radius:.2f}" fill="{color}" opacity="0.92" />
""".format(
                x=x_px,
                y=y_px,
                width=width_px,
                height=height_px,
                color=color,
                rotation=component.rotation,
                cx=cx,
                cy=cy,
                ox=orient_x,
                oy=orient_y,
                dot_radius=dot_radius,
            ).strip()

        elements.append("<g><title>{}</title>{}</g>".format(title, shape))

        if labels:
            elements.append(
                '<text x="{:.2f}" y="{:.2f}" font-size="10" text-anchor="middle" '
                'fill="#1b2330">{}</text>'.format(cx, cy - (height_px / 2.0) - 4.0, ref_label)
            )

    anchor_x = origin_x
    anchor_y = origin_y
    anchor_marker = """
<line x1="{x:.2f}" y1="{y1:.2f}" x2="{x:.2f}" y2="{y2:.2f}" stroke="#0f1720" stroke-width="1.6" />
<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}" stroke="#0f1720" stroke-width="1.6" />
<circle cx="{x:.2f}" cy="{y:.2f}" r="4.5" fill="#ffffff" stroke="#0f1720" stroke-width="1.4" />
""".format(
        x=anchor_x,
        y=anchor_y,
        y1=plot_top,
        y2=plot_bottom,
        x1=plot_left,
        x2=plot_right,
    )

    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#fbfcfe" />
<rect x="{margin}" y="{header}" width="{plot_w}" height="{plot_h}" fill="#ffffff" stroke="#cfd7e3" stroke-width="1.2" />
<text x="{margin}" y="28" font-size="20" font-family="monospace" fill="#111827">KiCad POS Preview</text>
<text x="{margin}" y="48" font-size="12" font-family="monospace" fill="#3f4956">source: {source}</text>
<text x="{margin}" y="64" font-size="12" font-family="monospace" fill="#3f4956">anchor: {anchor_ref} at ({anchor_x_mm:.4f}, {anchor_y_mm:.4f}) mm | output: {output}</text>
{grid}
{anchor_marker}
{elements}
</svg>
""".format(
        width=canvas_width,
        height=canvas_height,
        margin=margin,
        header=header_height,
        plot_w=canvas_width - (2 * margin),
        plot_h=canvas_height - header_height - margin,
        source=escape(str(source_path)),
        output=escape(str(output_path)),
        anchor_ref=escape(anchor.ref),
        anchor_x_mm=anchor.x,
        anchor_y_mm=anchor.y,
        grid="\n".join(grid_lines),
        anchor_marker=anchor_marker.strip(),
        elements="\n".join(elements),
    )

    output_path.write_text(svg)
    return clipped_left, clipped_bottom


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Render an approximate SVG placement preview from a KiCad .pos file. "
            "By default the first valid component row is used as the anchor and is "
            "drawn at the bottom-left origin."
        )
    )
    parser.add_argument("pos_file", help="Input KiCad .pos file")
    parser.add_argument(
        "--output",
        default=None,
        help="Output SVG path (default: <input>_preview.svg)",
    )
    parser.add_argument(
        "--side",
        choices=["top", "bottom", "all"],
        default="all",
        help="Only render one side of the .pos file",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1400,
        help="SVG canvas width in pixels",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=1000,
        help="SVG canvas height in pixels",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=50,
        help="Outer margin in pixels",
    )
    parser.add_argument(
        "--labels",
        action="store_true",
        help="Draw reference labels next to each component",
    )
    parser.add_argument(
        "--fit-all",
        action="store_true",
        help="Shift the preview if needed to keep components left/below the anchor visible",
    )
    parser.add_argument(
        "--body-scale",
        type=float,
        default=0.42,
        help="Scale factor for the approximate outline bodies (default: 0.42)",
    )
    args = parser.parse_args()

    source_path = Path(args.pos_file)
    if not source_path.exists():
        print("ERROR: POS file not found:", source_path)
        raise SystemExit(1)

    output_path = Path(args.output) if args.output else source_path.with_name(
        source_path.stem + "_preview.svg"
    )

    components = parse_pos_file(source_path, side_filter=args.side)
    if not components:
        print("ERROR: No valid components found in:", source_path)
        raise SystemExit(1)

    anchor, preview = normalize_components(components)
    clipped_left, clipped_bottom = render_svg(
        preview,
        source_path=source_path,
        output_path=output_path,
        anchor=anchor,
        canvas_width=args.width,
        canvas_height=args.height,
        margin=args.margin,
        labels=args.labels,
        fit_all=args.fit_all,
        body_scale=max(args.body_scale, 0.05),
    )

    print("Wrote:", output_path)
    print("Components rendered:", len(preview))
    print(
        "Anchor component:",
        "{} at ({:.4f}, {:.4f}) mm".format(anchor.ref, anchor.x, anchor.y),
    )
    if not args.fit_all and (clipped_left or clipped_bottom):
        print(
            "WARNING: {} component(s) extend left of the anchor and {} extend below it. "
            "Re-run with --fit-all to include them.".format(clipped_left, clipped_bottom)
        )


if __name__ == "__main__":
    main()
