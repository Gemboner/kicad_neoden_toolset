from __future__ import annotations

import argparse
import math
import re
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from pygerber.gerberx3.api.v2 import ColorScheme, GerberFile, PixelFormatEnum

    HAVE_PYGERBER = True
except Exception:
    HAVE_PYGERBER = False


GERBER_RENDER_DPMM = 24
COMPONENT_PICK_RADIUS_PX = 14


@dataclass
class Component:
    index: int
    source_line_index: int
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


def parse_pos_file(path: Path, side_filter: str) -> list[Component]:
    components = []
    row_index = 0
    for line_index, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines()):
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
                source_line_index=line_index,
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


def color_for(component: Component) -> QtGui.QColor:
    ref_upper = component.ref.upper()
    text = f"{component.value} {component.footprint}".lower()
    if "fiducial" in text:
        return QtGui.QColor("#111111")
    if ref_upper.startswith("C"):
        return QtGui.QColor("#2f6fdd")
    if ref_upper.startswith("R"):
        return QtGui.QColor("#2a9d55")
    if ref_upper.startswith(("IC", "U")):
        return QtGui.QColor("#c84d3a")
    if ref_upper.startswith("J"):
        return QtGui.QColor("#d1830f")
    if ref_upper.startswith("D"):
        return QtGui.QColor("#9c3fb3")
    return QtGui.QColor("#586174")


def rotated_extent(component: ViewComponent) -> tuple[float, float]:
    angle = math.radians(component.component.rotation % 180.0)
    cos_a = abs(math.cos(angle))
    sin_a = abs(math.sin(angle))
    dx = (component.width * cos_a + component.height * sin_a) / 2.0
    dy = (component.width * sin_a + component.height * cos_a) / 2.0
    return dx, dy


def load_gerber_overlay(path: Path, dpmm: int = GERBER_RENDER_DPMM) -> GerberOverlay:
    if not HAVE_PYGERBER:
        raise RuntimeError("PyGerber is not installed. Install it with: pip install pygerber")

    gerber = GerberFile.from_file(path)
    parsed = gerber.parse()
    info = parsed.get_info()

    def pick(obj, *names: str) -> float:
        for name in names:
            if hasattr(obj, name):
                return float(getattr(obj, name))
        raise AttributeError(f"Gerber info object does not provide any of: {', '.join(names)}")

    tmp_dir = Path(tempfile.gettempdir()) / "kicad_pos_viewer_qt_gerber"
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


class GerberLoadWorker(QtCore.QObject):
    finished = QtCore.Signal(object, object)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @QtCore.Slot()
    def run(self) -> None:
        try:
            overlay = load_gerber_overlay(self.path)
            image = QtGui.QImage(str(overlay.png_path))
            if image.isNull():
                raise RuntimeError(f"Failed to load rendered Gerber image: {overlay.png_path}")
            self.finished.emit((overlay, image), None)
        except Exception as exc:
            self.finished.emit(None, exc)


class ComponentLayerItem(QtWidgets.QGraphicsItem):
    def __init__(self, window: "PosViewerQtWindow") -> None:
        super().__init__()
        self.window = window
        self.setZValue(20)

    def boundingRect(self) -> QtCore.QRectF:
        rect = self.window.component_bounds_scene()
        if rect is None:
            return QtCore.QRectF(0.0, 0.0, 1.0, 1.0)
        margin = 5.0
        return rect.adjusted(-margin, -margin, margin, margin)

    def paint(
        self,
        painter: QtGui.QPainter,
        option: QtWidgets.QStyleOptionGraphicsItem,
        widget: QtWidgets.QWidget | None = None,
    ) -> None:
        del widget
        exposed = option.exposedRect
        pixel_scale = self.window.pixel_scale_mm()
        visible_ids: list[int] = []
        for idx, view in enumerate(self.window.components):
            sx, sy = self.window.component_scene_position(view)
            width_px = max(view.width * pixel_scale * self.window.body_scale, 4.0)
            height_px = max(view.height * pixel_scale * self.window.body_scale, 4.0)
            half_w_scene = (width_px / pixel_scale) / 2.0
            half_h_scene = (height_px / pixel_scale) / 2.0
            rect = QtCore.QRectF(
                sx - half_w_scene - 0.6,
                sy - half_h_scene - 0.6,
                (2 * half_w_scene) + 1.2,
                (2 * half_h_scene) + 1.2,
            )
            if not rect.intersects(exposed):
                continue
            visible_ids.append(idx)
            self._paint_component(painter, view, idx, pixel_scale)
        self.window.visible_component_indexes = visible_ids

    def _paint_component(
        self,
        painter: QtGui.QPainter,
        view: ViewComponent,
        idx: int,
        pixel_scale: float,
    ) -> None:
        component = view.component
        sx, sy = self.window.component_scene_position(view)
        selected = idx in self.window.selected_indexes
        color = color_for(component)
        width_px = max(view.width * pixel_scale * self.window.body_scale, 4.0)
        height_px = max(view.height * pixel_scale * self.window.body_scale, 4.0)
        width_scene = width_px / pixel_scale
        height_scene = height_px / pixel_scale

        painter.save()
        painter.translate(sx, sy)
        painter.scale(1.0, -1.0)

        if "fiducial" in f"{component.value} {component.footprint}".lower():
            radius = max(min(width_scene, height_scene) / 2.0, 1.0)
            if selected:
                pen = QtGui.QPen(QtGui.QColor("#f59e0b"))
                pen.setWidthF(0.5)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(0.0, 0.0), radius + 1.2, radius + 1.2)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.35 if selected else 0.22)
            painter.setPen(pen)
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
            painter.drawEllipse(QtCore.QPointF(0.0, 0.0), radius, radius)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(color))
            painter.drawEllipse(QtCore.QPointF(0.0, 0.0), radius * 0.35, radius * 0.35)
        else:
            if selected:
                pen = QtGui.QPen(QtGui.QColor("#f59e0b"))
                pen.setWidthF(0.4)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(0.0, 0.0), 2.8, 2.8)

            angle = math.radians(component.rotation)
            orient_len = max(min(max(width_scene, height_scene) * 0.8, 3.5), 1.4)
            pen = QtGui.QPen(color)
            pen.setWidthF(0.42 if selected else 0.24)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QtCore.QPointF(0.0, 0.0),
                QtCore.QPointF(math.cos(angle) * orient_len, math.sin(angle) * orient_len),
            )
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QBrush(color))
            dot_radius = max(min(min(width_scene, height_scene) * 0.22, 0.75), 0.35)
            painter.drawEllipse(QtCore.QPointF(0.0, 0.0), dot_radius, dot_radius)
            if pixel_scale >= 6:
                painter.save()
                painter.rotate(component.rotation)
                pen = QtGui.QPen(color)
                pen.setWidthF(0.28 if selected else 0.18)
                if not selected:
                    pen.setStyle(QtCore.Qt.DashLine)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRect(
                    QtCore.QRectF(
                        -width_scene / 2.0,
                        -height_scene / 2.0,
                        width_scene,
                        height_scene,
                    )
                )
                painter.restore()

        painter.restore()


class ViewerScene(QtWidgets.QGraphicsScene):
    def __init__(self, window: "PosViewerQtWindow") -> None:
        super().__init__()
        self.window = window


class ViewerView(QtWidgets.QGraphicsView):
    componentClicked = QtCore.Signal(int)
    originPicked = QtCore.Signal(float, float)
    gerberDragged = QtCore.Signal(float, float, bool)
    hoverComponentChanged = QtCore.Signal(object)

    def __init__(self, window: "PosViewerQtWindow") -> None:
        super().__init__()
        self.window = window
        self.setRenderHints(
            QtGui.QPainter.Antialiasing
            | QtGui.QPainter.TextAntialiasing
            | QtGui.QPainter.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.BoundingRectViewportUpdate)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self._pan_active = False
        self._last_pos = QtCore.QPoint()
        self._drag_gerber_active = False
        self._hover_index: int | None = None
        self.setMouseTracking(True)

    def drawBackground(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        painter.fillRect(rect, QtGui.QColor("#fbfcfe"))
        self.window.draw_background_grid(painter, rect)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        self.scale(factor, factor)
        self.window.update_zoom_label()
        self.window.component_layer.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() in (QtCore.Qt.MiddleButton, QtCore.Qt.RightButton):
            self._pan_active = True
            self._last_pos = event.position().toPoint()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            world_x, world_y = self.window.scene_to_world(scene_pos)
            if self.window.pick_pos_origin_mode:
                self.originPicked.emit(world_x, world_y)
                event.accept()
                return
            if self.window.drag_gerber_mode and self.window.gerber_contains_scene_point(scene_pos):
                self._drag_gerber_active = True
                self._last_pos = event.position().toPoint()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                event.accept()
                return
            idx = self.window.find_component_near_scene(scene_pos, self.pick_radius_world())
            if idx is not None:
                self.componentClicked.emit(idx)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pan_active:
            delta = event.position().toPoint() - self._last_pos
            self._last_pos = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.window.update_zoom_label()
            return
        if self._drag_gerber_active:
            current = event.position().toPoint()
            prev_scene = self.mapToScene(self._last_pos)
            curr_scene = self.mapToScene(current)
            delta_scene = curr_scene - prev_scene
            self._last_pos = current
            self.gerberDragged.emit(delta_scene.x(), -delta_scene.y(), False)
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        idx = self.window.find_component_near_scene(scene_pos, self.pick_radius_world())
        if idx != self._hover_index:
            self._hover_index = idx
            self.hoverComponentChanged.emit(idx)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._pan_active and event.button() in (QtCore.Qt.MiddleButton, QtCore.Qt.RightButton):
            self._pan_active = False
            self.window.sync_cursor()
            event.accept()
            return
        if self._drag_gerber_active and event.button() == QtCore.Qt.LeftButton:
            self._drag_gerber_active = False
            self.window.sync_cursor()
            self.gerberDragged.emit(0.0, 0.0, True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hover_index = None
        self.hoverComponentChanged.emit(None)
        super().leaveEvent(event)

    def pick_radius_world(self) -> float:
        p0 = self.mapToScene(QtCore.QPoint(0, 0))
        p1 = self.mapToScene(QtCore.QPoint(COMPONENT_PICK_RADIUS_PX, 0))
        return max(abs(p1.x() - p0.x()), 0.5)


class ReorderableTableWidget(QtWidgets.QTableWidget):
    rowsDropped = QtCore.Signal(list, int)
    deleteRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if event.source() is self:
            selected_rows = sorted({index.row() for index in self.selectedIndexes()})
            target_row = self.indexAt(event.position().toPoint()).row()
            if target_row < 0:
                target_row = self.rowCount()
            event.acceptProposedAction()
            self.rowsDropped.emit(selected_rows, target_row)
            return
        super().dropEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PosViewerQtWindow(QtWidgets.QMainWindow):
    def __init__(self, pos_path: Path | None, gerber_path: Path | None, side: str) -> None:
        super().__init__()
        self.setWindowTitle("KiCad POS Viewer Qt")
        self.resize(1680, 1040)

        self.current_pos_path: Path | None = None
        self.current_gerber_path: Path | None = None
        self.pos_file_lines: list[str] = []
        self.components: list[ViewComponent] = []
        self.selected_indexes: set[int] = set()
        self.visible_component_indexes: list[int] = []
        self.overlap_groups: list[tuple[tuple[float, float], list[Component]]] = []
        self.anchor_component: Component | None = None
        self.gerber_overlay: GerberOverlay | None = None
        self.gerber_pixmap: QtGui.QPixmap | None = None
        self.pos_origin_x_mm = 0.0
        self.pos_origin_y_mm = 0.0
        self.body_scale = 0.28
        self.show_gerber = True
        self.drag_gerber_mode = False
        self.pick_pos_origin_mode = False
        self.side_filter = side
        self.gerber_thread: QtCore.QThread | None = None
        self.gerber_worker: GerberLoadWorker | None = None

        self._build_ui()
        self._connect_signals()
        self.sync_cursor()

        if gerber_path is not None:
            self.load_gerber_path(gerber_path)
        if pos_path is not None:
            self.load_pos_path(pos_path)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        toolbar = QtWidgets.QHBoxLayout()
        root.addLayout(toolbar)

        self.open_pos_btn = QtWidgets.QPushButton("Open POS")
        self.open_gerber_btn = QtWidgets.QPushButton("Open Gerber")
        self.pick_origin_btn = QtWidgets.QToolButton()
        self.pick_origin_btn.setText("Pick POS 0,0")
        self.pick_origin_btn.setCheckable(True)
        self.drag_gerber_btn = QtWidgets.QToolButton()
        self.drag_gerber_btn.setText("Drag Gerber")
        self.drag_gerber_btn.setCheckable(True)
        self.reset_origin_btn = QtWidgets.QPushButton("Reset POS Origin")
        self.reset_gerber_btn = QtWidgets.QPushButton("Reset Gerber Align")
        self.fit_btn = QtWidgets.QPushButton("Fit View")
        self.center_btn = QtWidgets.QPushButton("Center Selected")
        self.show_gerber_cb = QtWidgets.QCheckBox("Show Gerber")
        self.show_gerber_cb.setChecked(True)
        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItems(["all", "top", "bottom"])
        self.side_combo.setCurrentText(self.side_filter)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search")
        self.zoom_label = QtWidgets.QLabel("1.00 px/mm")
        self.gerber_status_label = QtWidgets.QLabel("Gerber: none")

        for widget in (
            self.open_pos_btn,
            self.open_gerber_btn,
            self.pick_origin_btn,
            self.drag_gerber_btn,
            self.reset_origin_btn,
            self.reset_gerber_btn,
            self.fit_btn,
            self.center_btn,
            self.show_gerber_cb,
        ):
            toolbar.addWidget(widget)
        toolbar.addWidget(QtWidgets.QLabel("Side"))
        toolbar.addWidget(self.side_combo)
        toolbar.addWidget(QtWidgets.QLabel("Search"))
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.zoom_label)
        toolbar.addWidget(self.gerber_status_label)

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        splitter.addWidget(left)

        left_layout.addWidget(QtWidgets.QLabel("<b>File</b>"))
        self.path_label = QtWidgets.QLabel("-")
        self.path_label.setWordWrap(True)
        left_layout.addWidget(self.path_label)

        left_layout.addWidget(QtWidgets.QLabel("<b>Components</b>"))
        self.table = ReorderableTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["#", "Ref", "Value", "X", "Y", "Rot", "Side"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.horizontalHeader().setStretchLastSection(True)
        left_layout.addWidget(self.table, 1)

        left_layout.addWidget(QtWidgets.QLabel("<b>Selection</b>"))
        self.selection_label = QtWidgets.QLabel("No component selected.")
        self.selection_label.setWordWrap(True)
        left_layout.addWidget(self.selection_label)

        left_layout.addWidget(QtWidgets.QLabel("<b>Info Terminal</b>"))
        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setStyleSheet(
            "QTextEdit { background:#0f172a; color:#dbe4f0; font-family:monospace; }"
        )
        left_layout.addWidget(self.log_text)

        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(right)

        self.scene = ViewerScene(self)
        self.view = ViewerView(self)
        self.view.setScene(self.scene)
        right_layout.addWidget(self.view, 1)

        self.gerber_item = QtWidgets.QGraphicsPixmapItem()
        self.gerber_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self.gerber_item.setZValue(10)
        self.scene.addItem(self.gerber_item)

        self.gerber_bbox_item = QtWidgets.QGraphicsRectItem()
        self.gerber_bbox_item.setZValue(11)
        self.scene.addItem(self.gerber_bbox_item)

        self.gerber_hint_item = QtWidgets.QGraphicsSimpleTextItem("GERBER DRAG MODE")
        self.gerber_hint_item.setBrush(QtGui.QBrush(QtGui.QColor("#b45309")))
        font = self.gerber_hint_item.font()
        font.setBold(True)
        self.gerber_hint_item.setFont(font)
        self.gerber_hint_item.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.gerber_hint_item.setZValue(12)
        self.scene.addItem(self.gerber_hint_item)

        self.component_layer = ComponentLayerItem(self)
        self.scene.addItem(self.component_layer)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 1200])

        self.status_bar = self.statusBar()
        self.restore_status()

    def _connect_signals(self) -> None:
        self.open_pos_btn.clicked.connect(self.open_pos_dialog)
        self.open_gerber_btn.clicked.connect(self.open_gerber_dialog)
        self.pick_origin_btn.toggled.connect(self.set_pick_origin_mode)
        self.drag_gerber_btn.toggled.connect(self.set_drag_gerber_mode)
        self.reset_origin_btn.clicked.connect(self.reset_pos_origin)
        self.reset_gerber_btn.clicked.connect(self.reset_gerber_alignment)
        self.fit_btn.clicked.connect(self.fit_view)
        self.center_btn.clicked.connect(self.center_selected)
        self.show_gerber_cb.toggled.connect(self.set_show_gerber)
        self.side_combo.currentTextChanged.connect(self.change_side_filter)
        self.search_edit.textChanged.connect(self.populate_table)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.table.rowsDropped.connect(self.on_table_rows_reordered)
        self.table.deleteRequested.connect(self.delete_selected_components)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        self.view.componentClicked.connect(self.select_component_index)
        self.view.originPicked.connect(self.set_pos_origin)
        self.view.gerberDragged.connect(self.on_gerber_dragged)
        self.view.hoverComponentChanged.connect(self.on_hover_component_changed)

    def log_message(self, message: str, color: str = "#dbe4f0", clear: bool = False) -> None:
        if clear:
            self.log_text.clear()
        self.log_text.append(f'<span style="color:{color}">{message}</span>')

    def restore_status(self) -> None:
        pos_part = f"POS origin {self.pos_origin_x_mm:.2f},{self.pos_origin_y_mm:.2f}"
        if self.current_pos_path is not None:
            pos_part = f"{len(self.components)} components from {self.current_pos_path.name} | {pos_part}"
        gerber_part = "no gerber"
        if self.gerber_overlay is not None:
            gerber_part = (
                f"gerber {self.gerber_overlay.path.name} "
                f"dX {self.gerber_overlay.offset_x_mm:.2f} dY {self.gerber_overlay.offset_y_mm:.2f}"
            )
        self.status_bar.showMessage(f"{pos_part} | {gerber_part}")
        self.update_zoom_label()
        self.update_gerber_status()

    def update_zoom_label(self) -> None:
        transform = self.view.transform()
        self.zoom_label.setText(f"{transform.m11():.2f} px/mm")

    def update_gerber_status(self) -> None:
        if self.gerber_overlay is None:
            self.gerber_status_label.setText("Gerber: none")
            return
        self.gerber_status_label.setText(
            f"Gerber: {self.gerber_overlay.path.name} dX={self.gerber_overlay.offset_x_mm:.2f} dY={self.gerber_overlay.offset_y_mm:.2f}"
        )

    def pixel_scale_mm(self) -> float:
        return abs(self.view.transform().m11())

    def world_to_scene(self, x_mm: float, y_mm: float) -> QtCore.QPointF:
        return QtCore.QPointF(x_mm, -y_mm)

    def scene_to_world(self, point: QtCore.QPointF) -> tuple[float, float]:
        return point.x(), -point.y()

    def component_scene_position(self, view: ViewComponent) -> tuple[float, float]:
        world_x = view.board_x + self.pos_origin_x_mm
        world_y = view.board_y + self.pos_origin_y_mm
        scene = self.world_to_scene(world_x, world_y)
        return scene.x(), scene.y()

    def component_bounds_scene(self) -> QtCore.QRectF | None:
        if not self.components:
            return None
        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for view in self.components:
            dx, dy = rotated_extent(view)
            world_x = view.board_x + self.pos_origin_x_mm
            world_y = view.board_y + self.pos_origin_y_mm
            min_x = min(min_x, world_x - dx)
            max_x = max(max_x, world_x + dx)
            min_y = min(min_y, world_y - dy)
            max_y = max(max_y, world_y + dy)
        tl = self.world_to_scene(min_x, max_y)
        br = self.world_to_scene(max_x, min_y)
        return QtCore.QRectF(tl, br).normalized()

    def gerber_world_bounds(self) -> tuple[float, float, float, float] | None:
        if self.gerber_overlay is None:
            return None
        overlay = self.gerber_overlay
        return (
            overlay.min_x + overlay.offset_x_mm,
            overlay.min_y + overlay.offset_y_mm,
            overlay.max_x + overlay.offset_x_mm,
            overlay.max_y + overlay.offset_y_mm,
        )

    def gerber_contains_scene_point(self, scene_pos: QtCore.QPointF) -> bool:
        bounds = self.gerber_world_bounds()
        if bounds is None or not self.show_gerber:
            return False
        world_x, world_y = self.scene_to_world(scene_pos)
        min_x, min_y, max_x, max_y = bounds
        return min_x <= world_x <= max_x and min_y <= world_y <= max_y

    def set_pick_origin_mode(self, enabled: bool) -> None:
        self.pick_pos_origin_mode = enabled
        if enabled and self.drag_gerber_mode:
            self.drag_gerber_btn.setChecked(False)
        self.sync_cursor()
        self.scene.update()

    def set_drag_gerber_mode(self, enabled: bool) -> None:
        self.drag_gerber_mode = enabled
        if enabled and self.pick_pos_origin_mode:
            self.pick_origin_btn.setChecked(False)
        self.sync_cursor()
        self.update_gerber_items()

    def set_show_gerber(self, enabled: bool) -> None:
        self.show_gerber = enabled
        self.update_gerber_items()
        self.restore_status()

    def sync_cursor(self) -> None:
        if self.pick_pos_origin_mode:
            self.view.setCursor(QtCore.Qt.CrossCursor)
        elif self.drag_gerber_mode:
            self.view.setCursor(QtCore.Qt.OpenHandCursor)
        else:
            self.view.setCursor(QtCore.Qt.ArrowCursor)

    def set_pos_origin(self, world_x: float, world_y: float) -> None:
        self.pos_origin_x_mm = world_x
        self.pos_origin_y_mm = world_y
        self.pick_origin_btn.setChecked(False)
        self.log_message(
            f"POS origin set: X {world_x:.4f} mm, Y {world_y:.4f} mm",
            "#94a3b8",
        )
        self.component_layer.prepareGeometryChange()
        self.component_layer.update()
        self.scene.update()
        self.restore_status()

    def reset_pos_origin(self) -> None:
        self.pos_origin_x_mm = 0.0
        self.pos_origin_y_mm = 0.0
        self.pick_origin_btn.setChecked(False)
        self.log_message("POS origin reset to exported 0,0.", "#dbe4f0")
        self.component_layer.prepareGeometryChange()
        self.component_layer.update()
        self.scene.update()
        self.restore_status()

    def reset_gerber_alignment(self) -> None:
        if self.gerber_overlay is None:
            return
        self.gerber_overlay.offset_x_mm = 0.0
        self.gerber_overlay.offset_y_mm = 0.0
        self.log_message("Gerber alignment reset to exported origin.", "#dbe4f0")
        self.update_gerber_items()
        self.restore_status()

    def on_gerber_dragged(self, dx_mm: float, dy_mm: float, finished: bool) -> None:
        if self.gerber_overlay is None:
            return
        if not finished:
            self.gerber_overlay.offset_x_mm += dx_mm
            self.gerber_overlay.offset_y_mm += dy_mm
            self.update_gerber_items()
            self.restore_status()
            return
        self.log_message(
            "Gerber aligned: dX {:.4f} mm, dY {:.4f} mm".format(
                self.gerber_overlay.offset_x_mm, self.gerber_overlay.offset_y_mm
            ),
            "#94a3b8",
        )
        self.restore_status()

    def change_side_filter(self, side: str) -> None:
        self.side_filter = side
        if self.current_pos_path is not None:
            self.load_pos_path(self.current_pos_path)

    def detect_overlaps(
        self, components: list[Component]
    ) -> list[tuple[tuple[float, float], list[Component]]]:
        groups: dict[tuple[float, float], list[Component]] = defaultdict(list)
        for component in components:
            groups[(component.x, component.y)].append(component)
        overlaps = []
        for coords, items in groups.items():
            if len(items) > 1:
                overlaps.append((coords, sorted(items, key=lambda item: item.index)))
        overlaps.sort(key=lambda item: (item[0][1], item[0][0]))
        return overlaps

    def log_overlaps(self) -> None:
        if not self.overlap_groups:
            self.log_message("No exact coordinate overlaps detected.", "#86efac")
            return
        self.log_message(
            f"WARNING: {len(self.overlap_groups)} overlapping coordinate group(s) detected.",
            "#fca5a5",
        )
        for (x_coord, y_coord), items in self.overlap_groups:
            refs = ", ".join(f"{item.ref}[{item.index + 1}]" for item in items)
            self.log_message(
                f"OVERLAP at X {x_coord:.4f} mm, Y {y_coord:.4f} mm -> {refs}",
                "#fca5a5",
            )

    def open_pos_dialog(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open KiCad POS File",
            str(self.current_pos_path.parent if self.current_pos_path else Path.cwd()),
            "KiCad POS files (*.pos);;All files (*.*)",
        )
        if path_str:
            self.load_pos_path(Path(path_str))

    def open_gerber_dialog(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Gerber File",
            str(self.current_gerber_path.parent if self.current_gerber_path else Path.cwd()),
            "Gerber files (*.gbr *.gtl *.gbl *.gto *.gbo *.gm1 *.gm2 *.pho *.art);;All files (*.*)",
        )
        if path_str:
            self.load_gerber_path(Path(path_str))

    def load_pos_path(self, path: Path) -> None:
        parsed = parse_pos_file(path, self.side_filter)
        if not parsed:
            QtWidgets.QMessageBox.warning(self, "No Components Found", f"No valid placement rows in:\n{path}")
            return
        self.current_pos_path = path
        self.pos_file_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        self.anchor_component = parsed[0]
        self.components = [
            ViewComponent(component=component, board_x=component.x, board_y=component.y, width=size[0], height=size[1])
            for component in parsed
            for size in [infer_size_mm(component)]
        ]
        self.overlap_groups = self.detect_overlaps(parsed)
        self.selected_indexes.clear()
        self.path_label.setText(str(path))
        self.log_message(f"Loaded {len(parsed)} components from {path.name}", "#dbe4f0", clear=True)
        self.log_message(
            f"Anchor reference: {self.anchor_component.ref} at X {self.anchor_component.x:.4f} mm, Y {self.anchor_component.y:.4f} mm",
            "#94a3b8",
        )
        self.log_message(
            f"POS origin: X {self.pos_origin_x_mm:.4f} mm, Y {self.pos_origin_y_mm:.4f} mm",
            "#94a3b8",
        )
        self.log_overlaps()
        self.populate_table()
        self.component_layer.prepareGeometryChange()
        self.component_layer.update()
        self.fit_view()
        self.restore_status()

    def load_gerber_path(self, path: Path) -> None:
        if not HAVE_PYGERBER:
            QtWidgets.QMessageBox.critical(self, "Missing Dependency", "PyGerber is required. Install it with: pip install pygerber")
            return
        self.current_gerber_path = path
        self.update_gerber_status()
        self.log_message(f"Loading Gerber overlay: {path.name}", "#dbe4f0")
        self.gerber_status_label.setText(f"Gerber: loading {path.name}...")

        if self.gerber_thread is not None:
            self.gerber_thread.quit()
            self.gerber_thread.wait()

        self.gerber_thread = QtCore.QThread(self)
        self.gerber_worker = GerberLoadWorker(path)
        self.gerber_worker.moveToThread(self.gerber_thread)
        self.gerber_thread.started.connect(self.gerber_worker.run)
        self.gerber_worker.finished.connect(self.on_gerber_loaded)
        self.gerber_worker.finished.connect(self.gerber_thread.quit)
        self.gerber_thread.finished.connect(self.gerber_worker.deleteLater)
        self.gerber_thread.start()

    @QtCore.Slot(object, object)
    def on_gerber_loaded(self, result: object, error: object) -> None:
        if error is not None:
            QtWidgets.QMessageBox.critical(self, "Gerber Load Error", str(error))
            self.gerber_status_label.setText("Gerber: load failed")
            return
        assert result is not None
        overlay, image = result
        self.gerber_overlay = overlay
        self.gerber_pixmap = QtGui.QPixmap.fromImage(image)
        self.log_message(
            "Gerber bbox: min=({:.4f}, {:.4f}) mm max=({:.4f}, {:.4f}) mm size=({:.4f} x {:.4f}) mm".format(
                overlay.min_x,
                overlay.min_y,
                overlay.max_x,
                overlay.max_y,
                overlay.width_mm,
                overlay.height_mm,
            ),
            "#94a3b8",
        )
        self.log_message("Gerber offset: dX 0.0000 mm, dY 0.0000 mm", "#94a3b8")
        self.update_gerber_items()
        self.fit_view()
        self.restore_status()

    def populate_table(self) -> None:
        query = self.search_edit.text().strip().lower()
        self.table.setRowCount(0)
        self.visible_component_indexes = []
        for idx, view in enumerate(self.components):
            component = view.component
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
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.visible_component_indexes.append(idx)
            values = [
                str(component.index + 1),
                component.ref,
                component.value,
                f"{component.x:.3f}",
                f"{component.y:.3f}",
                f"{component.rotation:.1f}",
                component.side,
            ]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, idx)
                self.table.setItem(row, col, item)
                if idx in self.selected_indexes:
                    item.setSelected(True)
        self.update_selection_label()

    def selected_component_indexes_from_table(self) -> list[int]:
        indexes = []
        for model_index in self.table.selectionModel().selectedRows():
            row = model_index.row()
            if 0 <= row < len(self.visible_component_indexes):
                indexes.append(self.visible_component_indexes[row])
        return sorted(set(indexes))

    def on_table_selection_changed(self) -> None:
        self.selected_indexes = set(self.selected_component_indexes_from_table())
        self.update_selection_label()
        self.component_layer.update()

    def update_selection_label(self) -> None:
        if not self.selected_indexes:
            self.selection_label.setText("No component selected.")
            return
        if len(self.selected_indexes) > 1:
            refs = [self.components[idx].component.ref for idx in sorted(self.selected_indexes)]
            self.selection_label.setText(
                f"{len(self.selected_indexes)} components selected: "
                + ", ".join(refs[:10])
                + (" ..." if len(refs) > 10 else "")
            )
            return
        idx = next(iter(self.selected_indexes))
        component = self.components[idx].component
        self.selection_label.setText(
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

    def select_component_index(self, idx: int) -> None:
        self.selected_indexes = {idx}
        self.populate_table()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.data(QtCore.Qt.UserRole) == idx:
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                break
        self.component_layer.update()

    def show_table_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), index.parent()):
            self.table.clearSelection()
            self.table.selectRow(index.row())
        if not self.selected_component_indexes_from_table():
            return
        menu = QtWidgets.QMenu(self)
        delete_action = menu.addAction("Delete Selected")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == delete_action:
            self.delete_selected_components()

    def on_table_rows_reordered(self, selected_rows: list[int], target_row: int) -> None:
        if not selected_rows:
            return
        self.move_selected_components(selected_rows, target_row)

    def move_selected_components(self, selected_rows: list[int], target_row: int) -> None:
        if self.current_pos_path is None or not self.pos_file_lines:
            return
        visible_components = [self.components[idx].component for idx in self.visible_component_indexes]
        if not visible_components:
            return
        selected_rows = sorted(set(selected_rows))
        moving_components = [visible_components[row] for row in selected_rows if 0 <= row < len(visible_components)]
        if not moving_components:
            return

        moving_ids = {id(component) for component in moving_components}
        target_component = None
        target_probe = target_row
        while 0 <= target_probe < len(visible_components):
            candidate = visible_components[target_probe]
            if id(candidate) not in moving_ids:
                target_component = candidate
                break
            target_probe += 1

        moving_line_indexes = [component.source_line_index for component in moving_components]
        self.reorder_pos_source_lines(moving_line_indexes, target_component.source_line_index if target_component else None)

    def reorder_pos_source_lines(self, moving_line_indexes: list[int], target_line_index: int | None) -> None:
        if self.current_pos_path is None:
            return
        moving_set = set(moving_line_indexes)
        moving_lines = [self.pos_file_lines[index] for index in sorted(moving_line_indexes)]
        remaining_lines = [line for index, line in enumerate(self.pos_file_lines) if index not in moving_set]
        if target_line_index is None:
            insert_at = len(remaining_lines)
        else:
            insert_at = sum(1 for index in range(target_line_index) if index not in moving_set)
        new_lines = remaining_lines[:insert_at] + moving_lines + remaining_lines[insert_at:]
        self.current_pos_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self.load_pos_path(self.current_pos_path)
        self.log_message(f"Reordered {len(moving_lines)} POS row(s) in {self.current_pos_path.name}", "#86efac")

    def delete_selected_components(self) -> None:
        if self.current_pos_path is None or not self.pos_file_lines:
            return
        selected_components = [
            self.components[idx].component
            for idx in self.selected_component_indexes_from_table()
            if 0 <= idx < len(self.components)
        ]
        if not selected_components:
            return
        delete_indexes = {component.source_line_index for component in selected_components}
        new_lines = [line for index, line in enumerate(self.pos_file_lines) if index not in delete_indexes]
        self.current_pos_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self.load_pos_path(self.current_pos_path)
        self.log_message(f"Deleted {len(delete_indexes)} POS row(s) from {self.current_pos_path.name}", "#fca5a5")

    def on_hover_component_changed(self, idx: int | None) -> None:
        if idx is None:
            self.restore_status()
            return
        component = self.components[idx].component
        self.status_bar.showMessage(
            f"{component.ref} | {component.value} | {component.footprint} | "
            f"X {component.x:.4f} mm | Y {component.y:.4f} mm | Rot {component.rotation:.1f} deg | {component.side}"
        )

    def draw_background_grid(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        pixel_scale = self.pixel_scale_mm()
        step = self.choose_grid_step(pixel_scale)
        pen = QtGui.QPen(QtGui.QColor("#e7ecf3"))
        pen.setWidthF(0.0)
        painter.setPen(pen)
        start_x = math.floor(rect.left() / step) * step
        end_x = math.ceil(rect.right() / step) * step
        start_y = math.floor(rect.top() / step) * step
        end_y = math.ceil(rect.bottom() / step) * step
        x = start_x
        while x <= end_x:
            painter.drawLine(QtCore.QPointF(x, rect.top()), QtCore.QPointF(x, rect.bottom()))
            x += step
        y = start_y
        while y <= end_y:
            painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
            y += step

        origin_scene = self.world_to_scene(self.pos_origin_x_mm, self.pos_origin_y_mm)
        axis_pen = QtGui.QPen(QtGui.QColor("#0f1720"))
        axis_pen.setWidthF(0.0)
        painter.setPen(axis_pen)
        painter.drawLine(QtCore.QPointF(origin_scene.x(), rect.top()), QtCore.QPointF(origin_scene.x(), rect.bottom()))
        painter.drawLine(QtCore.QPointF(rect.left(), origin_scene.y()), QtCore.QPointF(rect.right(), origin_scene.y()))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        painter.drawEllipse(origin_scene, 0.9, 0.9)

        painter.save()
        painter.resetTransform()
        font = painter.font()
        font.setPointSizeF(9.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QtGui.QPen(QtGui.QColor("#0f1720")))
        view_point = self.view.mapFromScene(origin_scene)
        painter.drawText(view_point + QtCore.QPoint(8, -8), "POS origin (0,0)")
        painter.restore()

    def choose_grid_step(self, pixel_scale: float) -> float:
        for step in (0.5, 1, 2, 5, 10, 20, 50, 100):
            if step * pixel_scale >= 80:
                return step
        return 200.0

    def update_gerber_items(self) -> None:
        if self.gerber_overlay is None or self.gerber_pixmap is None or not self.show_gerber:
            self.gerber_item.setVisible(False)
            self.gerber_bbox_item.setVisible(False)
            self.gerber_hint_item.setVisible(False)
            return

        min_x, min_y, max_x, max_y = self.gerber_world_bounds()
        top_left = self.world_to_scene(min_x, max_y)
        rect = QtCore.QRectF(
            top_left.x(),
            top_left.y(),
            max_x - min_x,
            max_y - min_y,
        )
        pix_rect = self.gerber_pixmap.rect()
        self.gerber_item.setPixmap(self.gerber_pixmap)
        self.gerber_item.setVisible(True)
        self.gerber_item.setPos(rect.left(), rect.top())
        self.gerber_item.setScale(rect.width() / max(pix_rect.width(), 1))
        self.gerber_item.setTransform(QtGui.QTransform.fromScale(rect.width() / max(pix_rect.width(), 1), rect.height() / max(pix_rect.height(), 1)))
        self.gerber_item.setPos(rect.left(), rect.top())

        pen = QtGui.QPen(QtGui.QColor("#f59e0b" if self.drag_gerber_mode else "#94a3b8"))
        pen.setWidthF(0.0)
        pen.setStyle(QtCore.Qt.DashLine)
        self.gerber_bbox_item.setPen(pen)
        self.gerber_bbox_item.setRect(rect)
        self.gerber_bbox_item.setVisible(True)

        self.gerber_hint_item.setVisible(self.drag_gerber_mode)
        if self.drag_gerber_mode:
            self.gerber_hint_item.setPos(rect.left() + 2.0, rect.top() + 2.0)

        if self.component_bounds_scene() is None:
            self.scene.setSceneRect(rect.adjusted(-10, -10, 10, 10))
        else:
            self.scene.setSceneRect(self.combined_scene_rect())
        self.scene.update()

    def combined_scene_rect(self) -> QtCore.QRectF:
        rects: list[QtCore.QRectF] = []
        comp_rect = self.component_bounds_scene()
        if comp_rect is not None:
            rects.append(comp_rect)
        if self.gerber_overlay is not None:
            min_x, min_y, max_x, max_y = self.gerber_world_bounds()
            rects.append(QtCore.QRectF(self.world_to_scene(min_x, max_y), self.world_to_scene(max_x, min_y)).normalized())
        if not rects:
            return QtCore.QRectF(-50, -50, 100, 100)
        out = rects[0]
        for rect in rects[1:]:
            out = out.united(rect)
        return out.adjusted(-10, -10, 10, 10)

    def fit_view(self) -> None:
        rect = self.combined_scene_rect()
        if rect.isNull():
            return
        self.scene.setSceneRect(rect)
        self.view.fitInView(rect, QtCore.Qt.KeepAspectRatio)
        self.restore_status()
        self.component_layer.update()

    def center_selected(self) -> None:
        if not self.selected_indexes:
            return
        sum_x = 0.0
        sum_y = 0.0
        for idx in self.selected_indexes:
            sx, sy = self.component_scene_position(self.components[idx])
            sum_x += sx
            sum_y += sy
        center = QtCore.QPointF(sum_x / len(self.selected_indexes), sum_y / len(self.selected_indexes))
        self.view.centerOn(center)
        self.restore_status()

    def find_component_near_scene(self, scene_pos: QtCore.QPointF, radius_world: float) -> int | None:
        nearest = None
        nearest_dist = radius_world
        indexes = self.visible_component_indexes or list(range(len(self.components)))
        for idx in indexes:
            sx, sy = self.component_scene_position(self.components[idx])
            dist = math.hypot(scene_pos.x() - sx, scene_pos.y() - sy)
            if dist < nearest_dist:
                nearest = idx
                nearest_dist = dist
        return nearest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qt-based KiCad POS and Gerber viewer."
    )
    parser.add_argument("pos_file", nargs="?", help="Optional KiCad POS file to open")
    parser.add_argument("--gerber", help="Optional Gerber file to open")
    parser.add_argument(
        "--side",
        choices=["all", "top", "bottom"],
        default="all",
        help="Initial side filter",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Open, process, print summary, and exit",
    )
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    window = PosViewerQtWindow(
        pos_path=Path(args.pos_file).resolve() if args.pos_file else None,
        gerber_path=Path(args.gerber).resolve() if args.gerber else None,
        side=args.side,
    )

    if args.smoke_test:
        QtCore.QTimer.singleShot(2500, app.quit)
        app.exec()
        if window.current_pos_path is not None:
            print(f"loaded_pos={window.current_pos_path}")
            print(f"components={len(window.components)}")
            print(f"overlap_groups={len(window.overlap_groups)}")
            if window.anchor_component is not None:
                print(
                    "anchor={} {:.4f} {:.4f}".format(
                        window.anchor_component.ref,
                        window.anchor_component.x,
                        window.anchor_component.y,
                    )
                )
        if window.gerber_overlay is not None:
            print(f"loaded_gerber={window.gerber_overlay.path}")
            print(
                "gerber_bbox={:.4f},{:.4f},{:.4f},{:.4f}".format(
                    window.gerber_overlay.min_x,
                    window.gerber_overlay.min_y,
                    window.gerber_overlay.max_x,
                    window.gerber_overlay.max_y,
                )
            )
        return

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
