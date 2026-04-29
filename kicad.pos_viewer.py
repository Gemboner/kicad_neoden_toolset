from __future__ import annotations

import argparse
import math
import re
import tempfile
import threading
import tkinter as tk
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

try:
    from pygerber.gerberx3.api.v2 import ColorScheme, GerberFile, PixelFormatEnum
    HAVE_PYGERBER = True
except Exception:
    HAVE_PYGERBER = False


GERBER_RENDER_DPMM = 24
GERBER_INTERACTION_REDRAW_DELAY_MS = 140
RENDER_THROTTLE_MS = 16
COMPONENT_CULL_MARGIN_PX = 24


@dataclass
class Component:
    index: int
    ref: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float
    side: str


@dataclass
class ViewComponent:
    component: Component
    board_x: float
    board_y: float
    width: float
    height: float


@dataclass
class GerberOverlay:
    path: Path
    png_path: Path
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width_mm: float
    height_mm: float
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0


def parse_pos_file(path: Path, side_filter: str) -> list[Component]:
    components = []
    row_index = 0
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
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
        components.append(
            Component(
                index=row_index,
                ref=parts[0],
                value=parts[1],
                footprint=" ".join(parts[2:-4]).strip(),
                x=x,
                y=y,
                rotation=rotation,
                side=side,
            )
        )
        row_index += 1
    return components


def infer_size_mm(component: Component) -> tuple[float, float]:
    text = " ".join([component.ref, component.value, component.footprint]).lower()
    if "fiducial" in text:
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
            pairs.append((raw_w / 100.0, raw_h / 100.0))
        elif raw_w >= 5 and raw_h >= 5:
            pairs.append((raw_w, raw_h))
    if pairs:
        return max(pairs, key=lambda item: item[0] * item[1])

    ref_upper = component.ref.upper()
    if ref_upper.startswith(("C", "R", "L", "FB", "D")):
        return 1.6, 0.8
    if ref_upper.startswith(("IC", "U")):
        return 5.0, 5.0
    if ref_upper.startswith("J"):
        return 8.0, 3.0
    if ref_upper.startswith("Q"):
        return 4.0, 4.0
    return 2.5, 1.5


def color_for(component: Component) -> str:
    ref_upper = component.ref.upper()
    text = f"{component.value} {component.footprint}".lower()
    if "fiducial" in text:
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


def rotated_extent(component: ViewComponent) -> tuple[float, float]:
    angle = math.radians(component.component.rotation % 180.0)
    cos_a = abs(math.cos(angle))
    sin_a = abs(math.sin(angle))
    dx = (component.width * cos_a + component.height * sin_a) / 2.0
    dy = (component.width * sin_a + component.height * cos_a) / 2.0
    return dx, dy


def choose_grid_step(scale: float) -> float:
    for step in (0.5, 1, 2, 5, 10, 20, 50, 100):
        if step * scale >= 80:
            return step
    return 200.0


def rotated_box_points(
    center_x: float,
    center_y: float,
    width: float,
    height: float,
    rotation_deg: float,
) -> list[float]:
    half_w = width / 2.0
    half_h = height / 2.0
    angle = math.radians(rotation_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    points = []
    for local_x, local_y in (
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
    ):
        px = center_x + (local_x * cos_a) - (local_y * sin_a)
        py = center_y + (local_x * sin_a) + (local_y * cos_a)
        points.extend([px, py])
    return points


def load_gerber_overlay(path: Path, dpmm: int = GERBER_RENDER_DPMM) -> GerberOverlay:
    if not HAVE_PYGERBER:
        raise RuntimeError(
            "PyGerber is not installed. Install it with:\n"
            "pip install pygerber"
        )

    gerber = GerberFile.from_file(path)
    parsed = gerber.parse()
    info = parsed.get_info()

    def pick(obj, *names: str) -> float:
        for name in names:
            if hasattr(obj, name):
                return float(getattr(obj, name))
        raise AttributeError(f"Gerber info object does not provide any of: {', '.join(names)}")

    tmp_dir = Path(tempfile.gettempdir()) / "kicad_pos_viewer_gerber"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    png_path = tmp_dir / f"{path.stem}.png"

    parsed.render_raster(
        str(png_path),
        dpmm=dpmm,
        color_scheme=ColorScheme.COPPER_ALPHA,
        pixel_format=PixelFormatEnum.RGBA,
    )

    return GerberOverlay(
        path=path,
        png_path=png_path,
        min_x=pick(info, "min_x_mm", "min_x"),
        min_y=pick(info, "min_y_mm", "min_y"),
        max_x=pick(info, "max_x_mm", "max_x"),
        max_y=pick(info, "max_y_mm", "max_y"),
        width_mm=pick(info, "width_mm", "width"),
        height_mm=pick(info, "height_mm", "height"),
    )


class PosViewerApp:
    def __init__(
        self,
        root: tk.Tk,
        initial_path: Path | None,
        initial_side: str,
        auto_open_dialog: bool = True,
    ) -> None:
        self.root = root
        self.root.title("KiCad POS Viewer + Gerber Overlay")
        self.root.geometry("1560x980")

        self.current_path: Path | None = None
        self.gerber_overlay: GerberOverlay | None = None
        self.gerber_base_image = None
        self.gerber_tk_image = None
        self.gerber_cache_key: tuple[int, int, str] | None = None
        self.gerber_redraw_job: str | None = None
        self.gerber_load_token = 0
        self.render_job: str | None = None
        self.render_interaction_pending = False
        self.hover_component_id: str | None = None
        self.visible_component_ids: list[str] = []
        self.gerber_canvas_image_id: int | None = None
        self.gerber_bbox_id: int | None = None
        self.gerber_hint_id: int | None = None

        self.side_filter_var = tk.StringVar(value=initial_side)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Open a KiCad .pos file to begin.")
        self.details_var = tk.StringVar(value="No component selected.")
        self.zoom_var = tk.StringVar(value="10.0 px/mm")
        self.gerber_visible = tk.BooleanVar(value=True)
        self.gerber_drag_mode = tk.BooleanVar(value=False)
        self.pick_pos_origin_mode = tk.BooleanVar(value=False)
        self.gerber_status_var = tk.StringVar(value="Gerber: none")
        self.gerber_scale_x_var = tk.StringVar(value="1.000")
        self.gerber_scale_y_var = tk.StringVar(value="1.000")

        self.components: list[ViewComponent] = []
        self.filtered_component_ids: list[str] = []
        self.component_by_id: dict[str, ViewComponent] = {}
        self.anchor_component: Component | None = None
        self.selected_ids: set[str] = set()
        self.overlap_groups: list[tuple[tuple[float, float], list[Component]]] = []

        self.scale = 10.0
        self.offset_x = 120.0
        self.offset_y = 860.0
        self.body_scale = 0.28
        self.last_pan: tuple[int, int] | None = None
        self.left_press_pos: tuple[int, int] | None = None
        self.dragging_gerber = False
        self.fit_on_next_resize = True
        self.pos_origin_x_mm = 0.0
        self.pos_origin_y_mm = 0.0

        self._build_ui()
        self._bind_events()
        self.update_canvas_cursor()

        if initial_path is not None:
            self.load_pos_file(initial_path)
        elif auto_open_dialog:
            self.root.after(80, self.open_file_dialog)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(10, 8))
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Open POS", command=self.open_file_dialog).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Open Gerber", command=self.open_gerber_dialog).pack(
            side="left", padx=(0, 8)
        )
        ttk.Checkbutton(
            toolbar,
            text="Drag Gerber",
            variable=self.gerber_drag_mode,
            command=lambda: (self.update_canvas_cursor(), self.request_render(immediate=True)),
        ).pack(side="left", padx=(0, 8))
        ttk.Checkbutton(
            toolbar,
            text="Pick POS 0,0",
            variable=self.pick_pos_origin_mode,
            command=lambda: (self.update_canvas_cursor(), self.request_render(immediate=True)),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Reset POS Origin", command=self.reset_pos_origin).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Reset Gerber Align", command=self.reset_gerber_alignment).pack(
            side="left", padx=(0, 8)
        )
        ttk.Label(toolbar, text="Gerber Scale X").pack(side="left")
        self.gerber_scale_x_spinbox = ttk.Spinbox(
            toolbar,
            from_=0.100,
            to=10.000,
            increment=0.010,
            textvariable=self.gerber_scale_x_var,
            width=7,
            command=self.apply_gerber_scales_from_ui,
        )
        self.gerber_scale_x_spinbox.pack(side="left", padx=(6, 8))
        ttk.Label(toolbar, text="Y").pack(side="left")
        self.gerber_scale_y_spinbox = ttk.Spinbox(
            toolbar,
            from_=0.100,
            to=10.000,
            increment=0.010,
            textvariable=self.gerber_scale_y_var,
            width=7,
            command=self.apply_gerber_scales_from_ui,
        )
        self.gerber_scale_y_spinbox.pack(side="left", padx=(6, 8))
        ttk.Button(toolbar, text="Guess Scale", command=self.guess_gerber_scale).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(toolbar, text="Reset Scale", command=self.reset_gerber_scale).pack(
            side="left", padx=(0, 16)
        )
        ttk.Button(toolbar, text="Fit View", command=self.fit_view).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Center Selected", command=self.center_selection).pack(
            side="left", padx=(0, 16)
        )

        ttk.Checkbutton(
            toolbar,
            text="Show Gerber",
            variable=self.gerber_visible,
            command=lambda: self.request_render(immediate=True),
        ).pack(side="left", padx=(0, 16))

        ttk.Label(toolbar, text="Side").pack(side="left")
        side_combo = ttk.Combobox(
            toolbar,
            values=("all", "top", "bottom"),
            textvariable=self.side_filter_var,
            state="readonly",
            width=8,
        )
        side_combo.pack(side="left", padx=(6, 16))

        ttk.Label(toolbar, text="Search").pack(side="left")
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=34)
        search_entry.pack(side="left", padx=(6, 8))
        self.search_entry = search_entry
        self.gerber_scale_x_spinbox.bind(
            "<Return>", lambda _event: self.apply_gerber_scales_from_ui()
        )
        self.gerber_scale_x_spinbox.bind(
            "<FocusOut>", lambda _event: self.apply_gerber_scales_from_ui()
        )
        self.gerber_scale_y_spinbox.bind(
            "<Return>", lambda _event: self.apply_gerber_scales_from_ui()
        )
        self.gerber_scale_y_spinbox.bind(
            "<FocusOut>", lambda _event: self.apply_gerber_scales_from_ui()
        )

        ttk.Label(toolbar, textvariable=self.gerber_status_var).pack(side="right", padx=(16, 0))
        ttk.Label(toolbar, textvariable=self.zoom_var, width=12).pack(side="right")

        content = ttk.PanedWindow(self.root, orient="horizontal")
        content.pack(fill="both", expand=True)

        left_frame = ttk.Frame(content, padding=(10, 0, 0, 10))
        right_frame = ttk.Frame(content, padding=(6, 0, 10, 10))
        content.add(left_frame, weight=0)
        content.add(right_frame, weight=1)

        path_frame = ttk.Frame(left_frame)
        path_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(path_frame, text="File", font=("", 10, "bold")).pack(anchor="w")
        self.path_label = ttk.Label(path_frame, text="-", wraplength=360, justify="left")
        self.path_label.pack(fill="x")

        ttk.Label(left_frame, text="Components", font=("", 10, "bold")).pack(anchor="w")

        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill="both", expand=True)

        columns = ("idx", "ref", "value", "x", "y", "rot", "side")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=30,
        )
        headings = {
            "idx": "#",
            "ref": "Ref",
            "value": "Value",
            "x": "X",
            "y": "Y",
            "rot": "Rot",
            "side": "Side",
        }
        widths = {"idx": 52, "ref": 70, "value": 92, "x": 76, "y": 76, "rot": 62, "side": 60}
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(name, width=widths[name], anchor="w", stretch=name in ("ref", "value"))
        self.tree.pack(side="left", fill="both", expand=True)

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        details_frame = ttk.Frame(left_frame)
        details_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(details_frame, text="Selection", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(
            details_frame,
            textvariable=self.details_var,
            wraplength=360,
            justify="left",
        ).pack(fill="x")

        log_frame = ttk.Frame(left_frame)
        log_frame.pack(fill="both", expand=False, pady=(10, 0))
        ttk.Label(log_frame, text="Info Terminal", font=("", 10, "bold")).pack(anchor="w")

        log_text_frame = ttk.Frame(log_frame)
        log_text_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            log_text_frame,
            height=12,
            wrap="word",
            background="#0f172a",
            foreground="#dbe4f0",
            insertbackground="#dbe4f0",
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_configure("info", foreground="#dbe4f0")
        self.log_text.tag_configure("success", foreground="#86efac")
        self.log_text.tag_configure("warn", foreground="#fca5a5")
        self.log_text.tag_configure("muted", foreground="#94a3b8")

        log_scroll = ttk.Scrollbar(log_text_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        canvas_frame = ttk.Frame(right_frame)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_frame,
            background="#fbfcfe",
            highlightthickness=0,
            takefocus=1,
        )
        self.canvas.pack(fill="both", expand=True)

        status_frame = ttk.Frame(self.root, padding=(10, 6))
        status_frame.pack(fill="x")
        ttk.Label(status_frame, textvariable=self.status_var, anchor="w").pack(fill="x")

    def _bind_events(self) -> None:
        self.search_var.trace_add("write", lambda *_: self.populate_tree())
        self.side_filter_var.trace_add("write", lambda *_: self.reload_current_file())
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self.center_selection())

        self.canvas.bind("<Configure>", self.on_canvas_configure)
        self.canvas.bind("<ButtonPress-1>", self.on_left_press)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        self.canvas.bind("<Leave>", self.on_canvas_leave)
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.do_pan)
        self.canvas.bind("<ButtonPress-3>", self.start_pan)
        self.canvas.bind("<B3-Motion>", self.do_pan)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", lambda event: self.zoom_about(event.x, event.y, 1.1))
        self.canvas.bind("<Button-5>", lambda event: self.zoom_about(event.x, event.y, 0.9))

        self.root.bind("<Control-o>", lambda _event: self.open_file_dialog())
        self.root.bind("<Control-g>", lambda _event: self.open_gerber_dialog())
        self.root.bind("f", lambda _event: self.fit_view())
        self.root.bind("<Escape>", lambda _event: self.clear_selection())

    def component_id(self, component: Component) -> str:
        return f"{component.index:05d}:{component.ref}:{component.side}"

    def pos_origin_world(self) -> tuple[float, float]:
        return self.pos_origin_x_mm, self.pos_origin_y_mm

    def component_world_position(self, view: ViewComponent) -> tuple[float, float]:
        return view.board_x + self.pos_origin_x_mm, view.board_y + self.pos_origin_y_mm

    def screen_to_world(self, x_px: float, y_px: float) -> tuple[float, float]:
        return (x_px - self.offset_x) / self.scale, -(y_px - self.offset_y) / self.scale

    def update_canvas_cursor(self) -> None:
        if self.pick_pos_origin_mode.get():
            self.canvas.configure(cursor="crosshair")
        elif self.gerber_drag_mode.get():
            self.canvas.configure(cursor="fleur")
        else:
            self.canvas.configure(cursor="")

    def gerber_world_bounds(self) -> tuple[float, float, float, float] | None:
        if self.gerber_overlay is None:
            return None
        overlay = self.gerber_overlay
        x0, y0 = self.gerber_transform_world_point(overlay.min_x, overlay.min_y)
        x1, y1 = self.gerber_transform_world_point(overlay.max_x, overlay.max_y)
        return (
            min(x0, x1),
            min(y0, y1),
            max(x0, x1),
            max(y0, y1),
        )

    def gerber_transform_world_point(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        if self.gerber_overlay is None:
            return x_mm, y_mm
        overlay = self.gerber_overlay
        translated_x = x_mm + overlay.offset_x_mm
        translated_y = y_mm + overlay.offset_y_mm
        scaled_x = self.pos_origin_x_mm + ((translated_x - self.pos_origin_x_mm) * overlay.scale_x)
        scaled_y = self.pos_origin_y_mm + ((translated_y - self.pos_origin_y_mm) * overlay.scale_y)
        return scaled_x, scaled_y

    def gerber_contains_screen_point(self, x_px: float, y_px: float) -> bool:
        bounds = self.gerber_world_bounds()
        if bounds is None or not self.gerber_visible.get():
            return False
        world_x, world_y = self.screen_to_world(x_px, y_px)
        min_x, min_y, max_x, max_y = bounds
        return min_x <= world_x <= max_x and min_y <= world_y <= max_y

    def clear_gerber_image_cache(self) -> None:
        self.gerber_tk_image = None
        self.gerber_cache_key = None

    def update_gerber_status_label(self) -> None:
        if self.gerber_overlay is None:
            self.gerber_status_var.set("Gerber: none")
            return
        overlay = self.gerber_overlay
        self.gerber_status_var.set(
            "Gerber: {} dX={:.2f} dY={:.2f} scaleX={:.3f} scaleY={:.3f}".format(
                overlay.path.name,
                overlay.offset_x_mm,
                overlay.offset_y_mm,
                overlay.scale_x,
                overlay.scale_y,
            )
        )

    def cancel_scheduled_gerber_redraw(self) -> None:
        if self.gerber_redraw_job is not None:
            try:
                self.root.after_cancel(self.gerber_redraw_job)
            except tk.TclError:
                pass
            self.gerber_redraw_job = None

    def cancel_scheduled_render(self) -> None:
        if self.render_job is not None:
            try:
                self.root.after_cancel(self.render_job)
            except tk.TclError:
                pass
            self.render_job = None

    def request_render(self, interaction: bool = False, immediate: bool = False) -> None:
        self.render_interaction_pending = self.render_interaction_pending or interaction
        if immediate:
            self.cancel_scheduled_render()
            pending_interaction = self.render_interaction_pending
            self.render_interaction_pending = False
            self.render_canvas(interaction=pending_interaction)
            return
        if self.render_job is not None:
            return
        delay_ms = RENDER_THROTTLE_MS if interaction else 1
        self.render_job = self.root.after(delay_ms, self._perform_requested_render)

    def _perform_requested_render(self) -> None:
        self.render_job = None
        pending_interaction = self.render_interaction_pending
        self.render_interaction_pending = False
        self.render_canvas(interaction=pending_interaction)

    def _perform_scheduled_gerber_redraw(self) -> None:
        self.gerber_redraw_job = None
        self.request_render(immediate=True)

    def schedule_high_quality_gerber_redraw(self) -> None:
        if self.gerber_overlay is None or not self.gerber_visible.get():
            return
        self.cancel_scheduled_gerber_redraw()
        self.gerber_redraw_job = self.root.after(
            GERBER_INTERACTION_REDRAW_DELAY_MS,
            self._perform_scheduled_gerber_redraw,
        )

    def log_gerber_offset(self, prefix: str = "Gerber offset") -> None:
        if self.gerber_overlay is None:
            return
        self.update_gerber_status_label()
        self.log_message(
            "{}: dX {:.4f} mm, dY {:.4f} mm, scaleX {:.4f}, scaleY {:.4f}".format(
                prefix,
                self.gerber_overlay.offset_x_mm,
                self.gerber_overlay.offset_y_mm,
                self.gerber_overlay.scale_x,
                self.gerber_overlay.scale_y,
            ),
            "muted",
        )

    def sync_gerber_scale_vars(self) -> None:
        if self.gerber_overlay is None:
            scale_x = 1.0
            scale_y = 1.0
        else:
            scale_x = self.gerber_overlay.scale_x
            scale_y = self.gerber_overlay.scale_y
        self.gerber_scale_x_var.set(f"{scale_x:.3f}")
        self.gerber_scale_y_var.set(f"{scale_y:.3f}")

    def apply_gerber_scales_from_ui(self) -> None:
        if self.gerber_overlay is None:
            self.sync_gerber_scale_vars()
            return
        try:
            scale_x = float(self.gerber_scale_x_var.get())
            scale_y = float(self.gerber_scale_y_var.get())
        except (ValueError, tk.TclError):
            self.sync_gerber_scale_vars()
            return
        scale_x = max(scale_x, 0.01)
        scale_y = max(scale_y, 0.01)
        if (
            abs(scale_x - self.gerber_overlay.scale_x) < 1e-9
            and abs(scale_y - self.gerber_overlay.scale_y) < 1e-9
        ):
            self.sync_gerber_scale_vars()
            return
        self.gerber_overlay.scale_x = scale_x
        self.gerber_overlay.scale_y = scale_y
        self.clear_gerber_image_cache()
        self.sync_gerber_scale_vars()
        self.update_gerber_status_label()
        self.log_gerber_offset("Gerber scale updated")
        self.restore_status()
        self.request_render(immediate=True)

    def pos_component_bbox(self) -> tuple[float, float, float, float] | None:
        if not self.components:
            return None
        xs = [view.board_x for view in self.components]
        ys = [view.board_y for view in self.components]
        return min(xs), min(ys), max(xs), max(ys)

    def guess_gerber_scale(self) -> None:
        if self.gerber_overlay is None:
            self.sync_gerber_scale_vars()
            return
        pos_bbox = self.pos_component_bbox()
        if pos_bbox is None:
            self.log_message("Load a POS file before guessing Gerber scale.", "warning")
            return
        min_x, min_y, max_x, max_y = pos_bbox
        pos_width = max(max_x - min_x, 1e-6)
        pos_height = max(max_y - min_y, 1e-6)
        gerber_width = max(self.gerber_overlay.max_x - self.gerber_overlay.min_x, 1e-6)
        gerber_height = max(self.gerber_overlay.max_y - self.gerber_overlay.min_y, 1e-6)
        self.gerber_overlay.scale_x = pos_width / gerber_width
        self.gerber_overlay.scale_y = pos_height / gerber_height
        self.clear_gerber_image_cache()
        self.sync_gerber_scale_vars()
        self.update_gerber_status_label()
        self.log_message(
            "Guessed Gerber scale from POS bbox: X {:.4f}, Y {:.4f}".format(
                self.gerber_overlay.scale_x,
                self.gerber_overlay.scale_y,
            ),
            "info",
        )
        self.restore_status()
        self.request_render(immediate=True)

    def log_pos_origin(self, prefix: str = "POS origin") -> None:
        self.log_message(
            f"{prefix}: X {self.pos_origin_x_mm:.4f} mm, Y {self.pos_origin_y_mm:.4f} mm",
            "muted",
        )

    def set_pos_origin(self, world_x: float, world_y: float) -> None:
        self.pos_origin_x_mm = world_x
        self.pos_origin_y_mm = world_y
        self.log_pos_origin("POS origin set")
        self.restore_status()
        self.request_render(immediate=True)

    def reset_pos_origin(self) -> None:
        self.pos_origin_x_mm = 0.0
        self.pos_origin_y_mm = 0.0
        self.pick_pos_origin_mode.set(False)
        self.update_canvas_cursor()
        self.log_message("POS origin reset to exported 0,0.", "info")
        self.log_pos_origin()
        self.restore_status()
        self.request_render(immediate=True)

    def reset_gerber_alignment(self) -> None:
        if self.gerber_overlay is None:
            return
        self.gerber_overlay.offset_x_mm = 0.0
        self.gerber_overlay.offset_y_mm = 0.0
        self.clear_gerber_image_cache()
        self.update_gerber_status_label()
        self.log_message("Gerber alignment reset to exported origin.", "info")
        self.restore_status()
        self.request_render(immediate=True)

    def reset_gerber_scale(self) -> None:
        if self.gerber_overlay is None:
            self.sync_gerber_scale_vars()
            return
        self.gerber_overlay.scale_x = 1.0
        self.gerber_overlay.scale_y = 1.0
        self.clear_gerber_image_cache()
        self.sync_gerber_scale_vars()
        self.update_gerber_status_label()
        self.log_message("Gerber scale reset to X=1.000, Y=1.000.", "info")
        self.restore_status()
        self.request_render(immediate=True)

    def log_message(self, message: str, tag: str = "info", clear: bool = False) -> None:
        self.log_text.configure(state="normal")
        if clear:
            self.log_text.delete("1.0", "end")
        self.log_text.insert("end", message.rstrip() + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def detect_overlaps(
        self, components: list[Component]
    ) -> list[tuple[tuple[float, float], list[Component]]]:
        groups: dict[tuple[float, float], list[Component]] = defaultdict(list)
        for component in components:
            groups[(component.x, component.y)].append(component)
        overlaps = []
        for coords, items in groups.items():
            if len(items) > 1:
                overlaps.append((coords, sorted(items, key=lambda comp: comp.index)))
        overlaps.sort(key=lambda item: (item[0][1], item[0][0]))
        return overlaps

    def log_overlaps(self) -> None:
        if not self.overlap_groups:
            self.log_message("No exact coordinate overlaps detected.", "success")
            return
        self.log_message(
            f"WARNING: {len(self.overlap_groups)} overlapping coordinate group(s) detected.",
            "warn",
        )
        for (x_coord, y_coord), items in self.overlap_groups:
            refs = ", ".join(f"{item.ref}[{item.index + 1}]" for item in items)
            self.log_message(
                f"OVERLAP at X {x_coord:.4f} mm, Y {y_coord:.4f} mm -> {refs}",
                "warn",
            )

    def log_pos_bbox(self) -> None:
        if not self.components:
            return
        world_points = [self.component_world_position(view) for view in self.components]
        xs = [point[0] for point in world_points]
        ys = [point[1] for point in world_points]
        self.log_message(
            "POS bbox: min=({:.4f}, {:.4f}) mm max=({:.4f}, {:.4f}) mm".format(
                min(xs), min(ys), max(xs), max(ys)
            ),
            "muted",
        )

    def load_pos_file(self, path: Path) -> None:
        try:
            parsed = parse_pos_file(path, self.side_filter_var.get())
        except Exception as exc:
            messagebox.showerror("POS Load Error", str(exc))
            return

        if not parsed:
            messagebox.showwarning(
                "No Components Found",
                f"No valid placement rows were found in:\n{path}",
            )
            return

        self.current_path = path
        self.path_label.configure(text=str(path))
        self.anchor_component = parsed[0]
        self.components = []
        self.component_by_id = {}
        self.overlap_groups = self.detect_overlaps(parsed)

        for component in parsed:
            width, height = infer_size_mm(component)
            view = ViewComponent(
                component=component,
                board_x=component.x,
                board_y=component.y,
                width=width,
                height=height,
            )
            comp_id = self.component_id(component)
            self.components.append(view)
            self.component_by_id[comp_id] = view

        self.selected_ids = set()
        self.fit_on_next_resize = True
        self.log_message(
            f"Loaded {len(parsed)} components from {path.name}",
            "info",
            clear=True,
        )
        self.log_message(
            f"Anchor reference: {self.anchor_component.ref} at X {self.anchor_component.x:.4f} mm, Y {self.anchor_component.y:.4f} mm",
            "muted",
        )
        self.log_pos_origin()
        self.log_message(f"Side filter: {self.side_filter_var.get()}", "muted")
        self.log_pos_bbox()
        self.log_overlaps()
        if self.gerber_overlay is not None:
            bounds = self.gerber_world_bounds()
            assert bounds is not None
            gmin_x, gmin_y, gmax_x, gmax_y = bounds
            self.log_message(
                "Gerber bbox: min=({:.4f}, {:.4f}) mm max=({:.4f}, {:.4f}) mm size=({:.4f} x {:.4f}) mm".format(
                    gmin_x,
                    gmin_y,
                    gmax_x,
                    gmax_y,
                    self.gerber_overlay.width_mm,
                    self.gerber_overlay.height_mm,
                ),
                "muted",
            )
        self.populate_tree()
        self.fit_view()
        self.restore_status()

    def reload_current_file(self) -> None:
        if self.current_path is not None:
            self.load_pos_file(self.current_path)

    def open_file_dialog(self) -> None:
        initial_dir = str(self.current_path.parent) if self.current_path else str(Path.cwd())
        chosen = filedialog.askopenfilename(
            title="Open KiCad POS File",
            initialdir=initial_dir,
            filetypes=(("KiCad POS files", "*.pos"), ("All files", "*.*")),
        )
        if chosen:
            self.load_pos_file(Path(chosen))

    def _load_gerber_sync(self, path: Path) -> tuple[GerberOverlay, Image.Image]:
        overlay = load_gerber_overlay(path, dpmm=GERBER_RENDER_DPMM)
        base_image = Image.open(overlay.png_path).convert("RGBA")
        return overlay, base_image

    def _finish_gerber_load(
        self,
        token: int,
        path: Path,
        result: tuple[GerberOverlay, Image.Image] | None,
        error: Exception | None,
    ) -> None:
        if token != self.gerber_load_token:
            return
        if error is not None:
            self.gerber_status_var.set("Gerber: load failed")
            messagebox.showerror("Gerber Load Error", str(error))
            return
        assert result is not None
        overlay, base_image = result
        self.gerber_overlay = overlay
        self.gerber_base_image = base_image
        self.clear_gerber_image_cache()
        self.sync_gerber_scale_vars()
        self.update_gerber_status_label()
        self.log_message(f"Loaded Gerber overlay: {overlay.path.name}", "success")
        self.log_message(
            "Gerber bbox: min=({:.4f}, {:.4f}) mm max=({:.4f}, {:.4f}) mm size=({:.4f} x {:.4f}) mm".format(
                overlay.min_x,
                overlay.min_y,
                overlay.max_x,
                overlay.max_y,
                overlay.width_mm,
                overlay.height_mm,
            ),
            "muted",
        )
        self.log_gerber_offset()
        self.fit_view()

    def load_gerber_path(self, path: Path, async_load: bool = True) -> None:
        if not HAVE_PIL:
            messagebox.showerror(
                "Missing Dependency",
                "Pillow is required for Gerber image display.\n\nInstall it with:\npip install pillow"
            )
            return
        if not HAVE_PYGERBER:
            messagebox.showerror(
                "Missing Dependency",
                "PyGerber is required for Gerber image display.\n\nInstall it with:\npip install pygerber"
            )
            return

        self.gerber_load_token += 1
        token = self.gerber_load_token
        self.gerber_status_var.set(f"Gerber: loading {path.name}...")
        self.log_message(f"Loading Gerber overlay: {path.name}", "info")

        if not async_load:
            try:
                result = self._load_gerber_sync(path)
                self._finish_gerber_load(token, path, result, None)
            except Exception as exc:
                self._finish_gerber_load(token, path, None, exc)
            return

        def worker() -> None:
            try:
                result = self._load_gerber_sync(path)
                self.root.after(0, self._finish_gerber_load, token, path, result, None)
            except Exception as exc:
                self.root.after(0, self._finish_gerber_load, token, path, None, exc)

        threading.Thread(target=worker, daemon=True).start()

    def open_gerber_dialog(self) -> None:
        initial_dir = str(self.current_path.parent) if self.current_path else str(Path.cwd())
        chosen = filedialog.askopenfilename(
            title="Open Gerber File",
            initialdir=initial_dir,
            filetypes=(
                ("Gerber files", "*.gbr *.gtl *.gbl *.gto *.gbo *.gm1 *.gm2 *.pho *.art"),
                ("All files", "*.*"),
            ),
        )
        if not chosen:
            return
        self.load_gerber_path(Path(chosen), async_load=True)

    def populate_tree(self) -> None:
        selected_before = set(self.selected_ids)
        for item in self.tree.get_children():
            self.tree.delete(item)

        query = self.search_var.get().strip().lower()
        self.filtered_component_ids = []
        for view in self.components:
            component = view.component
            comp_id = self.component_id(component)
            haystack = " ".join(
                [
                    component.ref,
                    component.value,
                    component.footprint,
                    component.side,
                    str(component.index + 1),
                ]
            ).lower()
            if query and query not in haystack:
                continue
            self.filtered_component_ids.append(comp_id)
            self.tree.insert(
                "",
                "end",
                iid=comp_id,
                values=(
                    component.index + 1,
                    component.ref,
                    component.value,
                    f"{component.x:.3f}",
                    f"{component.y:.3f}",
                    f"{component.rotation:.1f}",
                    component.side,
                ),
            )

        restored = [comp_id for comp_id in selected_before if self.tree.exists(comp_id)]
        if restored:
            self.tree.selection_set(restored)
        else:
            self.selected_ids = set()
            self.update_details()
            self.request_render(immediate=True)

    def update_details(self) -> None:
        if not self.selected_ids:
            self.details_var.set("No component selected.")
            return
        if len(self.selected_ids) > 1:
            refs = [self.component_by_id[comp_id].component.ref for comp_id in sorted(self.selected_ids)]
            self.details_var.set(
                f"{len(self.selected_ids)} components selected: "
                + ", ".join(refs[:10])
                + (" ..." if len(refs) > 10 else "")
            )
            return

        comp_id = next(iter(self.selected_ids))
        component = self.component_by_id[comp_id].component
        self.details_var.set(
            "{} | {} | {} | X {:.4f} mm | Y {:.4f} mm | Rot {:.1f} deg | {}".format(
                component.ref,
                component.value,
                component.footprint,
                component.x,
                component.y,
                component.rotation,
                component.side,
            )
        )

    def on_tree_select(self, _event=None) -> None:
        self.selected_ids = set(self.tree.selection())
        self.update_details()
        self.request_render(immediate=True)

    def clear_selection(self) -> None:
        self.selected_ids = set()
        self.tree.selection_remove(self.tree.selection())
        self.update_details()
        self.request_render(immediate=True)

    def on_canvas_configure(self, _event=None) -> None:
        if self.fit_on_next_resize and (self.components or self.gerber_overlay is not None):
            self.fit_view()
            return
        self.request_render()

    def on_canvas_motion(self, event) -> None:
        if self.pick_pos_origin_mode.get() or self.dragging_gerber:
            return
        comp_id = self.find_component_at(event.x, event.y)
        if comp_id == self.hover_component_id:
            return
        self.hover_component_id = comp_id
        if comp_id is None:
            self.restore_status()
            return
        component = self.component_by_id[comp_id].component
        self.status_var.set(
            f"{component.ref} | {component.value} | {component.footprint} | "
            f"X {component.x:.4f} mm | Y {component.y:.4f} mm | Rot {component.rotation:.1f} deg | {component.side}"
        )

    def on_canvas_leave(self, _event=None) -> None:
        self.hover_component_id = None
        self.restore_status()

    def on_left_press(self, event) -> None:
        self.left_press_pos = (event.x, event.y)
        if self.pick_pos_origin_mode.get():
            self.dragging_gerber = False
            return
        self.dragging_gerber = bool(
            self.gerber_drag_mode.get()
            and self.gerber_overlay is not None
            and self.gerber_visible.get()
            and self.gerber_contains_screen_point(event.x, event.y)
        )

    def on_left_drag(self, event) -> None:
        if self.pick_pos_origin_mode.get():
            return
        if not self.dragging_gerber or self.left_press_pos is None or self.gerber_overlay is None:
            return
        dx_px = event.x - self.left_press_pos[0]
        dy_px = event.y - self.left_press_pos[1]
        scale_x = max(abs(self.gerber_overlay.scale_x), 1e-6)
        scale_y = max(abs(self.gerber_overlay.scale_y), 1e-6)
        self.gerber_overlay.offset_x_mm += dx_px / (self.scale * scale_x)
        self.gerber_overlay.offset_y_mm -= dy_px / (self.scale * scale_y)
        self.left_press_pos = (event.x, event.y)
        self.fit_on_next_resize = False
        self.restore_status()
        self.request_render(interaction=True)

    def on_left_release(self, event) -> None:
        if self.pick_pos_origin_mode.get():
            world_x, world_y = self.screen_to_world(event.x, event.y)
            self.pick_pos_origin_mode.set(False)
            self.update_canvas_cursor()
            self.set_pos_origin(world_x, world_y)
            return
        if self.dragging_gerber:
            self.dragging_gerber = False
            self.left_press_pos = None
            self.log_gerber_offset("Gerber aligned")
            self.restore_status()
            self.request_render(immediate=True)
            return
        self.left_press_pos = None
        self.on_canvas_click(event)

    def start_pan(self, event) -> None:
        self.last_pan = (event.x, event.y)

    def do_pan(self, event) -> None:
        if self.last_pan is None:
            return
        dx = event.x - self.last_pan[0]
        dy = event.y - self.last_pan[1]
        self.offset_x += dx
        self.offset_y += dy
        self.last_pan = (event.x, event.y)
        self.fit_on_next_resize = False
        self.request_render(interaction=True)

    def on_mousewheel(self, event) -> None:
        factor = 1.1 if event.delta > 0 else 0.9
        self.zoom_about(event.x, event.y, factor)

    def zoom_about(self, x: float, y: float, factor: float) -> None:
        if not self.components and self.gerber_overlay is None:
            return
        factor = max(min(factor, 2.0), 0.2)
        world_x = (x - self.offset_x) / self.scale
        world_y = -(y - self.offset_y) / self.scale
        self.scale = max(min(self.scale * factor, 800.0), 1.0)
        self.offset_x = x - world_x * self.scale
        self.offset_y = y + world_y * self.scale
        self.fit_on_next_resize = False
        self.request_render(interaction=True)

    def fit_view(self) -> None:
        if not self.components and self.gerber_overlay is None:
            return
        self.cancel_scheduled_gerber_redraw()

        self.root.update_idletasks()
        width = max(self.canvas.winfo_width(), 300)
        height = max(self.canvas.winfo_height(), 300)

        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")

        for view in self.components:
            dx, dy = rotated_extent(view)
            world_x, world_y = self.component_world_position(view)
            min_x = min(min_x, world_x - dx)
            min_y = min(min_y, world_y - dy)
            max_x = max(max_x, world_x + dx)
            max_y = max(max_y, world_y + dy)

        if self.gerber_overlay is not None and self.gerber_visible.get():
            gerber_bounds = self.gerber_world_bounds()
            if gerber_bounds is not None:
                gmin_x, gmin_y, gmax_x, gmax_y = gerber_bounds
                min_x = min(min_x, gmin_x)
                min_y = min(min_y, gmin_y)
                max_x = max(max_x, gmax_x)
                max_y = max(max_y, gmax_y)

        if not math.isfinite(min_x):
            return

        pad_px = 60
        plot_w = max(max_x - min_x, 1.0)
        plot_h = max(max_y - min_y, 1.0)
        self.scale = min((width - 2 * pad_px) / plot_w, (height - 2 * pad_px) / plot_h)
        self.scale = max(self.scale, 1.0)

        self.offset_x = pad_px - min_x * self.scale
        self.offset_y = height - pad_px + min_y * self.scale
        self.fit_on_next_resize = True
        self.request_render(immediate=True)

    def center_selection(self) -> None:
        if not self.selected_ids:
            return
        width = max(self.canvas.winfo_width(), 300)
        height = max(self.canvas.winfo_height(), 300)
        sum_x = 0.0
        sum_y = 0.0
        for comp_id in self.selected_ids:
            view = self.component_by_id[comp_id]
            world_x, world_y = self.component_world_position(view)
            sum_x += world_x
            sum_y += world_y
        avg_x = sum_x / len(self.selected_ids)
        avg_y = sum_y / len(self.selected_ids)
        self.offset_x = width / 2.0 - avg_x * self.scale
        self.offset_y = height / 2.0 + avg_y * self.scale
        self.fit_on_next_resize = False
        self.request_render(immediate=True)

    def world_to_screen(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        return self.offset_x + x_mm * self.scale, self.offset_y - y_mm * self.scale

    def component_visible_on_screen(
        self,
        cx: float,
        cy: float,
        width_px: float,
        height_px: float,
        canvas_width: int,
        canvas_height: int,
    ) -> bool:
        half_w = width_px / 2.0 + COMPONENT_CULL_MARGIN_PX
        half_h = height_px / 2.0 + COMPONENT_CULL_MARGIN_PX
        return not (
            cx + half_w < 0
            or cx - half_w > canvas_width
            or cy + half_h < 0
            or cy - half_h > canvas_height
        )

    def hide_gerber_overlay_items(self) -> None:
        for item_id in (self.gerber_canvas_image_id, self.gerber_bbox_id, self.gerber_hint_id):
            if item_id is not None:
                self.canvas.itemconfigure(item_id, state="hidden")

    def render_canvas(self, interaction: bool = False) -> None:
        if not interaction:
            self.cancel_scheduled_gerber_redraw()
        width = max(self.canvas.winfo_width(), 300)
        height = max(self.canvas.winfo_height(), 300)
        self.canvas.delete("background")
        self.canvas.delete("grid")
        self.canvas.delete("axes")
        self.canvas.delete("component")
        self.canvas.delete("background_message")
        self.visible_component_ids = []
        self.hover_component_id = None
        self.canvas.create_rectangle(0, 0, width, height, fill="#fbfcfe", outline="", tags=("background",))

        if not self.components and self.gerber_overlay is None:
            self.hide_gerber_overlay_items()
            self.canvas.create_text(
                width / 2.0,
                height / 2.0,
                text="Open a KiCad .pos file or Gerber file",
                fill="#64748b",
                font=("TkDefaultFont", 18),
                tags=("background_message",),
            )
            return

        self.draw_grid(width, height)
        self.draw_axes(width, height)

        if self.gerber_overlay is not None and self.gerber_visible.get():
            self.draw_gerber_overlay(fast=interaction)
        else:
            self.hide_gerber_overlay_items()

        for view in self.components:
            self.draw_component(view, width, height)

        # Persistent Gerber items survive between redraws, so restore layer order
        # after recreating background/grid/axes/component items.
        self.canvas.tag_lower("background")
        self.canvas.tag_raise("grid")
        self.canvas.tag_raise("axes")
        self.canvas.tag_raise("gerber")
        self.canvas.tag_raise("gerber_bbox")
        self.canvas.tag_raise("gerber_hint")
        self.canvas.tag_raise("component")

        self.zoom_var.set(f"{self.scale:.1f} px/mm")
        if interaction:
            self.schedule_high_quality_gerber_redraw()

    def draw_grid(self, width: int, height: int) -> None:
        step_mm = choose_grid_step(self.scale)
        world_left = (0 - self.offset_x) / self.scale
        world_right = (width - self.offset_x) / self.scale
        world_top = (self.offset_y - 0) / self.scale
        world_bottom = (self.offset_y - height) / self.scale
        origin_x, origin_y = self.pos_origin_world()

        start_x = math.floor(world_left / step_mm) * step_mm
        end_x = math.ceil(world_right / step_mm) * step_mm
        start_y = math.floor(world_bottom / step_mm) * step_mm
        end_y = math.ceil(world_top / step_mm) * step_mm

        x_value = start_x
        while x_value <= end_x:
            x_px, _ = self.world_to_screen(x_value, 0.0)
            self.canvas.create_line(x_px, 0, x_px, height, fill="#e7ecf3", tags=("grid",))
            if self.scale >= 3:
                self.canvas.create_text(
                    x_px + 4,
                    14,
                    text=f"{x_value - origin_x:.0f}",
                    fill="#94a3b8",
                    anchor="w",
                    font=("TkDefaultFont", 8),
                    tags=("grid",),
                )
            x_value += step_mm

        y_value = start_y
        while y_value <= end_y:
            _, y_px = self.world_to_screen(0.0, y_value)
            self.canvas.create_line(0, y_px, width, y_px, fill="#e7ecf3", tags=("grid",))
            if self.scale >= 3:
                self.canvas.create_text(
                    6,
                    y_px - 2,
                    text=f"{y_value - origin_y:.0f}",
                    fill="#94a3b8",
                    anchor="sw",
                    font=("TkDefaultFont", 8),
                    tags=("grid",),
                )
            y_value += step_mm

    def draw_axes(self, width: int, height: int) -> None:
        world_origin_x, world_origin_y = self.pos_origin_world()
        origin_x, origin_y = self.world_to_screen(world_origin_x, world_origin_y)
        self.canvas.create_line(origin_x, 0, origin_x, height, fill="#0f1720", width=1, tags=("axes",))
        self.canvas.create_line(0, origin_y, width, origin_y, fill="#0f1720", width=1, tags=("axes",))
        self.canvas.create_oval(
            origin_x - 4,
            origin_y - 4,
            origin_x + 4,
            origin_y + 4,
            fill="#ffffff",
            outline="#0f1720",
            width=1.3,
            tags=("axes",),
        )
        self.canvas.create_text(
            origin_x + 8,
            origin_y - 8,
            text="POS origin (0,0)",
            fill="#0f1720",
            anchor="sw",
            font=("TkDefaultFont", 9, "bold"),
            tags=("axes",),
        )

    def draw_gerber_overlay(self, fast: bool = False) -> None:
        overlay = self.gerber_overlay
        if overlay is None or self.gerber_base_image is None or not HAVE_PIL:
            return
        bounds = self.gerber_world_bounds()
        if bounds is None:
            return
        min_x, min_y, max_x, max_y = bounds

        x0, y_top = self.world_to_screen(min_x, max_y)
        x1, y_bottom = self.world_to_screen(max_x, min_y)

        draw_w = max(int(round(x1 - x0)), 1)
        draw_h = max(int(round(y_bottom - y_top)), 1)

        quality = "fast" if fast else "hq"
        cache_key = (draw_w, draw_h, quality)
        if self.gerber_cache_key != cache_key:
            resample = Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS
            resized = self.gerber_base_image.resize((draw_w, draw_h), resample)
            self.gerber_tk_image = ImageTk.PhotoImage(resized)
            self.gerber_cache_key = cache_key

        if self.gerber_tk_image is not None:
            if self.gerber_canvas_image_id is None:
                self.gerber_canvas_image_id = self.canvas.create_image(
                    x0,
                    y_top,
                    image=self.gerber_tk_image,
                    anchor="nw",
                    tags=("gerber",),
                )
            else:
                self.canvas.coords(self.gerber_canvas_image_id, x0, y_top)
                self.canvas.itemconfigure(self.gerber_canvas_image_id, image=self.gerber_tk_image, state="normal")

        if self.gerber_bbox_id is None:
            self.gerber_bbox_id = self.canvas.create_rectangle(
                x0,
                y_top,
                x1,
                y_bottom,
                outline="#94a3b8",
                width=1,
                dash=(4, 4),
                tags=("gerber_bbox",),
            )
        else:
            self.canvas.coords(self.gerber_bbox_id, x0, y_top, x1, y_bottom)
            self.canvas.itemconfigure(self.gerber_bbox_id, state="normal")
        self.canvas.itemconfigure(
            self.gerber_bbox_id,
            outline="#f59e0b" if self.gerber_drag_mode.get() else "#94a3b8",
            width=2 if self.gerber_drag_mode.get() else 1,
        )

        if self.gerber_drag_mode.get():
            if self.gerber_hint_id is None:
                self.gerber_hint_id = self.canvas.create_text(
                    x0 + 8,
                    y_top + 8,
                    text="GERBER DRAG MODE",
                    fill="#b45309",
                    anchor="nw",
                    font=("TkDefaultFont", 9, "bold"),
                    tags=("gerber_hint",),
                )
            else:
                self.canvas.coords(self.gerber_hint_id, x0 + 8, y_top + 8)
                self.canvas.itemconfigure(self.gerber_hint_id, state="normal")
        elif self.gerber_hint_id is not None:
            self.canvas.itemconfigure(self.gerber_hint_id, state="hidden")

    def draw_component(self, view: ViewComponent, canvas_width: int, canvas_height: int) -> None:
        component = view.component
        comp_id = self.component_id(component)
        world_x, world_y = self.component_world_position(view)
        cx, cy = self.world_to_screen(world_x, world_y)
        width_px = max(view.width * self.scale * self.body_scale, 4.0)
        height_px = max(view.height * self.scale * self.body_scale, 4.0)
        if not self.component_visible_on_screen(
            cx, cy, width_px, height_px, canvas_width, canvas_height
        ):
            return
        self.visible_component_ids.append(comp_id)
        color = color_for(component)
        selected = comp_id in self.selected_ids

        if "fiducial" in f"{component.value} {component.footprint}".lower():
            radius = max(min(width_px, height_px) / 2.0, 5.0)
            if selected:
                self.canvas.create_oval(
                    cx - radius - 5,
                    cy - radius - 5,
                    cx + radius + 5,
                    cy + radius + 5,
                    outline="#f59e0b",
                    width=3,
                    tags=("component", comp_id),
                )
            self.canvas.create_oval(
                cx - radius,
                cy - radius,
                cx + radius,
                cy + radius,
                outline=color,
                width=2 if selected else 1.4,
                fill="#ffffff",
                tags=("component", comp_id),
            )
            self.canvas.create_oval(
                cx - radius * 0.35,
                cy - radius * 0.35,
                cx + radius * 0.35,
                cy + radius * 0.35,
                outline="",
                fill=color,
                tags=("component", comp_id),
            )
        else:
            angle = math.radians(component.rotation)
            orient_len = max(min(max(width_px, height_px) * 0.75, 18.0), 8.0)
            orient_x = cx + math.cos(angle) * orient_len
            orient_y = cy - math.sin(angle) * orient_len

            if selected:
                self.canvas.create_oval(
                    cx - 12,
                    cy - 12,
                    cx + 12,
                    cy + 12,
                    outline="#f59e0b",
                    width=3,
                    tags=("component", comp_id),
                )

            self.canvas.create_line(
                cx,
                cy,
                orient_x,
                orient_y,
                fill=color,
                width=3 if selected else 1.4,
                capstyle=tk.ROUND,
                tags=("component", comp_id),
            )
            self.canvas.create_oval(
                cx - 3.5,
                cy - 3.5,
                cx + 3.5,
                cy + 3.5,
                outline="",
                fill=color,
                tags=("component", comp_id),
            )
            if self.scale >= 6:
                self.canvas.create_polygon(
                    rotated_box_points(cx, cy, width_px, height_px, component.rotation),
                    outline=color,
                    width=2 if selected else 1,
                    dash=() if selected else (3, 4),
                    fill="",
                    tags=("component", comp_id),
                )

        if selected:
            self.canvas.create_text(
                cx + 8,
                cy - 8,
                text=component.ref,
                fill="#111827" if selected else "#475569",
                anchor="sw",
                font=("TkDefaultFont", 9, "bold" if selected else "normal"),
                tags=("component", comp_id),
            )

    def restore_status(self) -> None:
        if self.current_path is None and self.gerber_overlay is None:
            self.status_var.set("Open a KiCad .pos file to begin.")
            return

        pos_part = "No POS loaded"
        if self.current_path is not None:
            pos_part = (
                f"{len(self.components)} components from {self.current_path.name} "
                f"(origin {self.pos_origin_x_mm:.2f},{self.pos_origin_y_mm:.2f})"
            )
        else:
            pos_part = f"No POS loaded (origin {self.pos_origin_x_mm:.2f},{self.pos_origin_y_mm:.2f})"

        gerber_part = "no gerber"
        if self.gerber_overlay is not None:
            gerber_part = (
                f"gerber {self.gerber_overlay.path.name} "
                f"({self.gerber_overlay.width_mm:.2f} x {self.gerber_overlay.height_mm:.2f} mm, "
                f"dX {self.gerber_overlay.offset_x_mm:.2f}, dY {self.gerber_overlay.offset_y_mm:.2f}, "
                f"scaleX {self.gerber_overlay.scale_x:.3f}, scaleY {self.gerber_overlay.scale_y:.3f})"
            )

        self.status_var.set(f"{pos_part} | {gerber_part}")

    def find_component_at(self, x: float, y: float) -> str | None:
        nearest_id = None
        nearest_dist = 14.0
        ids = self.visible_component_ids or list(self.component_by_id.keys())
        for comp_id in ids:
            view = self.component_by_id[comp_id]
            world_x, world_y = self.component_world_position(view)
            cx, cy = self.world_to_screen(world_x, world_y)
            dist = math.hypot(cx - x, cy - y)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_id = comp_id
        return nearest_id

    def on_canvas_click(self, event) -> None:
        comp_id = self.find_component_at(event.x, event.y)
        if comp_id is None:
            self.clear_selection()
            return
        self.selected_ids = {comp_id}
        if self.tree.exists(comp_id):
            self.tree.selection_set((comp_id,))
            self.tree.focus(comp_id)
            self.tree.see(comp_id)
        self.update_details()
        self.request_render(immediate=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Interactive KiCad .pos viewer with component list and optional Gerber background overlay."
    )
    parser.add_argument(
        "pos_file",
        nargs="?",
        help="Optional KiCad .pos file to open on startup",
    )
    parser.add_argument(
        "--side",
        choices=["all", "top", "bottom"],
        default="all",
        help="Initial side filter",
    )
    parser.add_argument(
        "--gerber",
        help="Optional Gerber file to open on startup",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Load the UI, render once, print a summary, then exit",
    )
    args = parser.parse_args()

    initial_path = Path(args.pos_file).resolve() if args.pos_file else None
    initial_gerber = Path(args.gerber).resolve() if args.gerber else None

    root = tk.Tk()
    app = PosViewerApp(
        root,
        initial_path=initial_path,
        initial_side=args.side,
        auto_open_dialog=False,
    )

    if initial_gerber is not None:
        app.load_gerber_path(initial_gerber, async_load=not args.smoke_test)

    if args.smoke_test:
        root.update_idletasks()
        root.update()
        if app.current_path is not None:
            print(f"loaded_pos={app.current_path}")
            print(f"components={len(app.components)}")
            print(f"overlap_groups={len(app.overlap_groups)}")
            if app.anchor_component is not None:
                print(
                    "anchor={} {:.4f} {:.4f}".format(
                        app.anchor_component.ref,
                        app.anchor_component.x,
                        app.anchor_component.y,
                    )
                )
        if app.gerber_overlay is not None:
            print(f"loaded_gerber={app.gerber_overlay.path}")
            print(
                "gerber_bbox={:.4f},{:.4f},{:.4f},{:.4f}".format(
                    app.gerber_overlay.min_x,
                    app.gerber_overlay.min_y,
                    app.gerber_overlay.max_x,
                    app.gerber_overlay.max_y,
                )
            )
        root.destroy()
        return

    root.mainloop()


if __name__ == "__main__":
    main()
