from __future__ import annotations

import argparse
import html
import math
import re
import sys
from collections import defaultdict
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtPrintSupport, QtWidgets
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
class PosPlacementRow:
    line_index: int
    parts: list[str]
    x: float
    y: float
    rotation: float


@dataclass
class RotatedPlacementRow:
    row: PosPlacementRow
    x: float
    y: float
    rotation: float


@dataclass
class PartsListEntry:
    quantity: int
    value: str
    footprint: str
    side: str


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
    componentClicked = QtCore.Signal(int, bool)
    originPicked = QtCore.Signal(float, float)
    hoverComponentChanged = QtCore.Signal(object)
    selectionRectSelected = QtCore.Signal(object, bool)
    contextMenuRequested = QtCore.Signal(object, object)

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
        self._hover_index: int | None = None
        self._selection_active = False
        self._selection_origin = QtCore.QPoint()
        self._selection_additive = False
        self._right_press_active = False
        self._right_press_pos = QtCore.QPoint()
        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self.viewport())
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
        if event.button() == QtCore.Qt.MiddleButton:
            self._pan_active = True
            self._last_pos = event.position().toPoint()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.RightButton:
            self._right_press_active = True
            self._right_press_pos = event.position().toPoint()
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            world_x, world_y = self.window.scene_to_world(scene_pos)
            if self.window.pick_pos_origin_mode:
                self.originPicked.emit(world_x, world_y)
                event.accept()
                return
            idx = self.window.find_component_near_scene(scene_pos, self.pick_radius_world())
            if idx is not None:
                additive = bool(
                    event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
                )
                self.componentClicked.emit(idx, additive)
                event.accept()
                return
            self._selection_active = True
            self._selection_origin = event.position().toPoint()
            self._selection_additive = bool(
                event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
            )
            self._rubber_band.setGeometry(QtCore.QRect(self._selection_origin, QtCore.QSize()))
            self._rubber_band.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._right_press_active and not self._pan_active:
            delta = event.position().toPoint() - self._right_press_pos
            if delta.manhattanLength() >= QtWidgets.QApplication.startDragDistance():
                self._pan_active = True
                self._last_pos = event.position().toPoint()
                self.setCursor(QtCore.Qt.ClosedHandCursor)
        if self._pan_active:
            delta = event.position().toPoint() - self._last_pos
            self._last_pos = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self.window.update_zoom_label()
            return
        if self._selection_active:
            rect = QtCore.QRect(self._selection_origin, event.position().toPoint()).normalized()
            self._rubber_band.setGeometry(rect)
            event.accept()
            return
        scene_pos = self.mapToScene(event.position().toPoint())
        idx = self.window.find_component_near_scene(scene_pos, self.pick_radius_world())
        if idx != self._hover_index:
            self._hover_index = idx
            self.hoverComponentChanged.emit(idx)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            global_pos = self.viewport().mapToGlobal(event.position().toPoint())
            was_panning = self._pan_active
            self._right_press_active = False
            if self._pan_active:
                self._pan_active = False
                self.window.sync_cursor()
            if not was_panning:
                self.contextMenuRequested.emit(scene_pos, global_pos)
            event.accept()
            return
        if self._pan_active and event.button() == QtCore.Qt.MiddleButton:
            self._pan_active = False
            self.window.sync_cursor()
            event.accept()
            return
        if self._selection_active and event.button() == QtCore.Qt.LeftButton:
            rect = self._rubber_band.geometry().normalized()
            self._rubber_band.hide()
            self._selection_active = False
            if rect.width() < 4 and rect.height() < 4:
                if not self._selection_additive:
                    self.window.clear_component_selection()
            else:
                scene_rect = self.mapToScene(rect).boundingRect()
                self.selectionRectSelected.emit(scene_rect, self._selection_additive)
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


class PartsListDialog(QtWidgets.QDialog):
    def __init__(
        self,
        title: str,
        entries: list[PartsListEntry],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report_title = title
        self.entries = entries
        self.setWindowTitle(title)
        self.resize(980, 720)

        layout = QtWidgets.QVBoxLayout(self)

        total_parts = sum(entry.quantity for entry in entries)
        summary_label = QtWidgets.QLabel(f"{len(entries)} part types | {total_parts} parts")
        layout.addWidget(summary_label)

        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Qty", "Value", "Footprint", "Side"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(True)
        self.table.setStyleSheet(
            "QTableWidget { color: black; gridline-color: black; }"
            "QHeaderView::section { color: black; background: white; border: 1px solid black; }"
        )
        self.table.setRowCount(len(entries))

        for row_index, entry in enumerate(entries):
            values = [
                str(entry.quantity),
                entry.value,
                entry.footprint,
                entry.side,
            ]
            for col_index, value in enumerate(values):
                self.table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(value))

        layout.addWidget(self.table, 1)

        button_row = QtWidgets.QHBoxLayout()
        self.save_pdf_btn = QtWidgets.QPushButton("Save PDF")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.save_pdf_btn.clicked.connect(self.save_pdf)
        self.close_btn.clicked.connect(self.accept)
        button_row.addWidget(self.save_pdf_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.close_btn)
        layout.addLayout(button_row)

    def build_html(self) -> str:
        rows = []
        for entry in self.entries:
            rows.append(
                "<tr>"
                f"<td>{entry.quantity}</td>"
                f"<td>{html.escape(entry.value)}</td>"
                f"<td>{html.escape(entry.footprint)}</td>"
                f"<td>{html.escape(entry.side)}</td>"
                "</tr>"
            )
        table_rows = "".join(rows)
        total_parts = sum(entry.quantity for entry in self.entries)
        return (
            "<html><head><meta charset='utf-8'>"
            "<style>"
            "body { font-family: Arial, sans-serif; color: black; }"
            "h1 { font-size: 18pt; margin-bottom: 4px; }"
            "p { color: black; margin-top: 0; }"
            "table { width: 100%; border-collapse: collapse; font-size: 10pt; }"
            "th, td { border: 1px solid black; padding: 6px 8px; text-align: left; }"
            "th { background: white; }"
            "</style></head><body>"
            f"<h1>{html.escape(self.report_title)}</h1>"
            f"<p>{len(self.entries)} part types | {total_parts} parts</p>"
            "<table>"
            "<thead><tr><th>Qty</th><th>Value</th><th>Footprint</th><th>Side</th></tr></thead>"
            f"<tbody>{table_rows}</tbody>"
            "</table></body></html>"
        )

    def save_pdf(self) -> None:
        default_name = sanitize_pdf_name(self.report_title) + ".pdf"
        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Parts List PDF",
            str(Path.cwd() / default_name),
            "PDF files (*.pdf)",
        )
        if not path_str:
            return

        output_path = Path(path_str)
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path.with_suffix(".pdf")

        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.HighResolution)
        printer.setOutputFormat(QtPrintSupport.QPrinter.PdfFormat)
        printer.setOutputFileName(str(output_path))
        printer.setPageOrientation(QtGui.QPageLayout.Portrait)
        printer.setPageMargins(QtCore.QMarginsF(12, 12, 12, 12), QtGui.QPageLayout.Millimeter)

        document = QtGui.QTextDocument(self)
        document.setHtml(self.build_html())
        print_document = getattr(document, "print_", None) or getattr(document, "print", None)
        if print_document is None:
            raise RuntimeError("Qt text document PDF printing is not available in this PySide6 build.")
        print_document(printer)

        QtWidgets.QMessageBox.information(
            self,
            "Save Parts List PDF",
            f"Saved PDF:\n{output_path}",
        )


def sanitize_pdf_name(value: str) -> str:
    text = (value or "").strip()
    cleaned = []
    for char in text:
        if char.isalnum() or char in ("-", "_"):
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
    result = "".join(cleaned).strip("._-")
    return result or "parts_list"


class PosViewerQtWindow(QtWidgets.QMainWindow):
    def __init__(self, pos_path: Path | None, side: str) -> None:
        super().__init__()
        self.setWindowTitle("KiCad POS Viewer Qt")
        self.resize(1680, 1040)

        self.current_pos_path: Path | None = None
        self.pos_file_lines: list[str] = []
        self.components: list[ViewComponent] = []
        self.selected_indexes: set[int] = set()
        self.visible_component_indexes: list[int] = []
        self.overlap_groups: list[tuple[tuple[float, float], list[Component]]] = []
        self.anchor_component: Component | None = None
        self.pos_origin_x_mm = 0.0
        self.pos_origin_y_mm = 0.0
        self.body_scale = 0.28
        self.pick_pos_origin_mode = False
        self.side_filter = side
        self._syncing_table_selection = False

        self._build_ui()
        self._connect_signals()
        self.sync_cursor()

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
        self.pick_origin_btn = QtWidgets.QToolButton()
        self.pick_origin_btn.setText("Pick POS 0,0")
        self.pick_origin_btn.setCheckable(True)
        self.reset_origin_btn = QtWidgets.QPushButton("Reset POS Origin")
        self.rotate_right_btn = QtWidgets.QPushButton("Rotate 90\u00b0 Right")
        self.fit_btn = QtWidgets.QPushButton("Fit View")
        self.center_btn = QtWidgets.QPushButton("Center Selected")
        self.side_combo = QtWidgets.QComboBox()
        self.side_combo.addItems(["all", "top", "bottom"])
        self.side_combo.setCurrentText(self.side_filter)
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search")
        self.zoom_label = QtWidgets.QLabel("1.00 px/mm")

        for widget in (
            self.open_pos_btn,
            self.pick_origin_btn,
            self.reset_origin_btn,
            self.rotate_right_btn,
            self.fit_btn,
            self.center_btn,
        ):
            toolbar.addWidget(widget)
        toolbar.addWidget(QtWidgets.QLabel("Side"))
        toolbar.addWidget(self.side_combo)
        toolbar.addWidget(QtWidgets.QLabel("Search"))
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.zoom_label)

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
        component_tools = QtWidgets.QHBoxLayout()
        self.component_summary_label = QtWidgets.QLabel("0 parts | 0 part types")
        self.parts_list_btn = QtWidgets.QPushButton("Parts List")
        self.sort_by_amount_btn = QtWidgets.QPushButton("Sort By Amount")
        component_tools.addWidget(self.component_summary_label)
        component_tools.addWidget(self.parts_list_btn)
        component_tools.addWidget(self.sort_by_amount_btn)
        component_tools.addStretch(1)
        left_layout.addLayout(component_tools)
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

        self.component_layer = ComponentLayerItem(self)
        self.scene.addItem(self.component_layer)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 1200])

        self.status_bar = self.statusBar()
        self.restore_status()

    def _connect_signals(self) -> None:
        self.open_pos_btn.clicked.connect(self.open_pos_dialog)
        self.pick_origin_btn.toggled.connect(self.set_pick_origin_mode)
        self.reset_origin_btn.clicked.connect(self.reset_pos_origin)
        self.rotate_right_btn.clicked.connect(self.rotate_pos_file_right_90)
        self.fit_btn.clicked.connect(self.fit_view)
        self.center_btn.clicked.connect(self.center_selected)
        self.parts_list_btn.clicked.connect(self.open_parts_list_dialog)
        self.sort_by_amount_btn.clicked.connect(self.sort_components_by_amount)
        self.side_combo.currentTextChanged.connect(self.change_side_filter)
        self.search_edit.textChanged.connect(self.populate_table)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.table.rowsDropped.connect(self.on_table_rows_reordered)
        self.table.deleteRequested.connect(self.delete_selected_components)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        self.view.componentClicked.connect(self.select_component_index)
        self.view.originPicked.connect(self.set_pos_origin)
        self.view.hoverComponentChanged.connect(self.on_hover_component_changed)
        self.view.selectionRectSelected.connect(self.select_components_in_scene_rect)
        self.view.contextMenuRequested.connect(self.show_view_context_menu)

    def log_message(self, message: str, color: str = "#dbe4f0", clear: bool = False) -> None:
        if clear:
            self.log_text.clear()
        self.log_text.append(f'<span style="color:{color}">{message}</span>')

    def restore_status(self) -> None:
        pos_part = f"POS origin {self.pos_origin_x_mm:.2f},{self.pos_origin_y_mm:.2f}"
        if self.current_pos_path is not None:
            pos_part = f"{len(self.components)} components from {self.current_pos_path.name} | {pos_part}"
        self.status_bar.showMessage(pos_part)
        self.update_zoom_label()

    def update_zoom_label(self) -> None:
        transform = self.view.transform()
        self.zoom_label.setText(f"{transform.m11():.2f} px/mm")

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

    def set_pick_origin_mode(self, enabled: bool) -> None:
        self.pick_pos_origin_mode = enabled
        self.sync_cursor()
        self.scene.update()

    def sync_cursor(self) -> None:
        if self.pick_pos_origin_mode:
            self.view.setCursor(QtCore.Qt.CrossCursor)
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

    def parse_pos_placement_rows(self) -> list[PosPlacementRow]:
        rows: list[PosPlacementRow] = []
        for line_index, raw in enumerate(self.pos_file_lines):
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
            rows.append(
                PosPlacementRow(
                    line_index=line_index,
                    parts=parts,
                    x=x,
                    y=y,
                    rotation=rotation,
                )
            )
        return rows

    def is_fiducial_placement_row(self, row: PosPlacementRow) -> bool:
        text = " ".join(
            [
                row.parts[0],
                row.parts[1],
                " ".join(row.parts[2:-4]),
            ]
        ).lower()
        return "fiducial" in text

    def source_anchor_placement_row(self, placement_rows: list[PosPlacementRow]) -> PosPlacementRow:
        for row in placement_rows:
            if self.is_fiducial_placement_row(row):
                return row
        return placement_rows[0]

    def normalize_pos_rotation(self, rotation: float) -> float:
        value = float(rotation)
        while value > 180.0:
            value -= 360.0
        while value <= -180.0:
            value += 360.0
        return value

    def format_pos_placement_line(
        self,
        ref: str,
        value: str,
        footprint: str,
        x: float,
        y: float,
        rotation: float,
        side: str,
    ) -> str:
        return (
            f"{ref:<16} "
            f"{value:<22} "
            f"{footprint:<32} "
            f"{x:10.4f} "
            f"{y:10.4f} "
            f"{rotation:9.4f}  "
            f"{side}"
        )

    def on_pos_file_rotated(self, path: Path) -> None:
        del path

    def rotate_pos_file_right_90(self, checked: bool = False, confirm: bool = True) -> bool:
        del checked
        if self.current_pos_path is None:
            return False
        if not self.pos_file_lines:
            self.pos_file_lines = self.current_pos_path.read_text(encoding="utf-8", errors="ignore").splitlines()

        placement_rows = self.parse_pos_placement_rows()
        if not placement_rows:
            QtWidgets.QMessageBox.warning(
                self,
                "Rotate POS",
                "No valid placement rows were found in the current POS file.",
            )
            return False

        if confirm:
            answer = QtWidgets.QMessageBox.question(
                self,
                "Rotate 90\u00b0 Right",
                (
                    "Rotate the full POS dataset 90\u00b0 clockwise around the board center?\n\n"
                    f"This overwrites the current POS file in place:\n{self.current_pos_path}\n\n"
                    "This includes the source fiducial, updates all component rotations, "
                    "and invalidates any previously generated NeoDen output."
                ),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return False

        min_x = min(row.x for row in placement_rows)
        max_x = max(row.x for row in placement_rows)
        min_y = min(row.y for row in placement_rows)
        max_y = max(row.y for row in placement_rows)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        rotated_rows: list[RotatedPlacementRow] = []
        for row in placement_rows:
            rotated_rows.append(
                RotatedPlacementRow(
                    row=row,
                    x=center_x + (row.y - center_y),
                    y=center_y - (row.x - center_x),
                    rotation=self.normalize_pos_rotation(row.rotation - 90.0),
                )
            )

        anchor_row = self.source_anchor_placement_row(placement_rows)
        anchor_rotated = next(item for item in rotated_rows if item.row.line_index == anchor_row.line_index)
        shift_x = anchor_rotated.x
        shift_y = anchor_rotated.y

        new_lines = list(self.pos_file_lines)
        for rotated in rotated_rows:
            row = rotated.row
            ref = row.parts[0]
            value = row.parts[1]
            footprint = " ".join(row.parts[2:-4]).strip()
            side = row.parts[-1]
            new_lines[row.line_index] = self.format_pos_placement_line(
                ref,
                value,
                footprint,
                rotated.x - shift_x,
                rotated.y - shift_y,
                rotated.rotation,
                side,
            )

        self.current_pos_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self.pos_file_lines = new_lines
        self.on_pos_file_rotated(self.current_pos_path)
        self.load_pos_path(self.current_pos_path)
        self.log_message(
            (
                f"Rotated {len(placement_rows)} POS placement row(s) 90\u00b0 clockwise around "
                f"board center X {center_x:.4f} mm, Y {center_y:.4f} mm and moved "
                f"{anchor_row.parts[0]} to POS origin (0,0) in {self.current_pos_path.name}"
            ),
            "#86efac",
        )
        return True

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

    def component_type_key(self, component: Component) -> tuple[str, str]:
        return (
            component.value.strip().lower(),
            component.footprint.strip().lower(),
        )

    def parts_list_group_key(self, component: Component) -> tuple[str, str, str]:
        return (
            component.value.strip(),
            component.footprint.strip(),
            component.side.strip(),
        )

    def update_component_summary(self) -> None:
        total_parts = len(self.components)
        total_types = len({self.component_type_key(view.component) for view in self.components})
        query_active = bool(self.search_edit.text().strip())

        if not query_active:
            self.component_summary_label.setText(f"{total_parts} parts | {total_types} part types")
            return

        visible_components = [self.components[idx].component for idx in self.visible_component_indexes]
        visible_parts = len(visible_components)
        visible_types = len({self.component_type_key(component) for component in visible_components})
        self.component_summary_label.setText(
            f"{visible_parts}/{total_parts} parts | {visible_types}/{total_types} part types"
        )

    def visible_components(self) -> list[Component]:
        return [self.components[idx].component for idx in self.visible_component_indexes]

    def grouped_parts_list_entries(self, components: list[Component]) -> list[PartsListEntry]:
        counts: Counter[tuple[str, str, str]] = Counter(
            self.parts_list_group_key(component) for component in components
        )
        entries = [
            PartsListEntry(
                quantity=quantity,
                value=value,
                footprint=footprint,
                side=side,
            )
            for (value, footprint, side), quantity in counts.items()
        ]
        entries.sort(
            key=lambda entry: (
                entry.value.lower(),
                entry.footprint.lower(),
                entry.side.lower(),
            )
        )
        return entries

    def open_parts_list_dialog(self) -> None:
        if not self.components:
            QtWidgets.QMessageBox.warning(
                self,
                "Parts List",
                "Load a POS file first.",
            )
            return

        components = self.visible_components()
        if not components:
            QtWidgets.QMessageBox.warning(
                self,
                "Parts List",
                "No components match the current search/filter.",
            )
            return

        source_name = self.current_pos_path.name if self.current_pos_path is not None else "POS"
        if self.search_edit.text().strip():
            title = f"Parts List - {source_name} (filtered)"
        else:
            title = f"Parts List - {source_name}"

        dialog = PartsListDialog(
            title=title,
            entries=self.grouped_parts_list_entries(components),
            parent=self,
        )
        dialog.exec()

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
        self.update_component_summary()
        self.sync_table_selection_from_indexes()
        self.update_selection_label()

    def sync_table_selection_from_indexes(self, scroll_to_first: bool = False) -> None:
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return
        self._syncing_table_selection = True
        try:
            blocker_table = QtCore.QSignalBlocker(self.table)
            blocker_selection = QtCore.QSignalBlocker(selection_model)
            selection_model.clearSelection()
            first_item: QtWidgets.QTableWidgetItem | None = None
            for row, component_index in enumerate(self.visible_component_indexes):
                if component_index not in self.selected_indexes:
                    continue
                model_index = self.table.model().index(row, 0)
                selection_model.select(
                    model_index,
                    QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                )
                if first_item is None:
                    first_item = self.table.item(row, 0)
            del blocker_table, blocker_selection
        finally:
            self._syncing_table_selection = False
        if scroll_to_first and first_item is not None:
            self.table.scrollToItem(first_item)

    def selected_component_indexes_from_table(self) -> list[int]:
        indexes = []
        for model_index in self.table.selectionModel().selectedRows():
            row = model_index.row()
            if 0 <= row < len(self.visible_component_indexes):
                indexes.append(self.visible_component_indexes[row])
        return sorted(set(indexes))

    def on_table_selection_changed(self) -> None:
        if self._syncing_table_selection:
            return
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

    def set_selected_indexes(self, indexes: set[int] | list[int], scroll_to_first: bool = False) -> None:
        self.selected_indexes = {
            idx
            for idx in indexes
            if 0 <= idx < len(self.components)
        }
        self.populate_table()
        self.sync_table_selection_from_indexes(scroll_to_first=scroll_to_first)
        self.update_selection_label()
        self.component_layer.update()

    def clear_component_selection(self) -> None:
        self.set_selected_indexes(set())

    def select_component_index(self, idx: int, additive: bool = False) -> None:
        if additive:
            selected = set(self.selected_indexes)
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
            self.set_selected_indexes(selected, scroll_to_first=idx in selected)
            return
        self.set_selected_indexes({idx}, scroll_to_first=True)

    def component_scene_rect(self, view: ViewComponent) -> QtCore.QRectF:
        sx, sy = self.component_scene_position(view)
        dx, dy = rotated_extent(view)
        return QtCore.QRectF(sx - dx, sy - dy, dx * 2.0, dy * 2.0)

    def select_components_in_scene_rect(self, scene_rect: QtCore.QRectF, additive: bool) -> None:
        matched_indexes = {
            idx
            for idx, view in enumerate(self.components)
            if self.component_scene_rect(view).intersects(scene_rect)
        }
        if additive:
            matched_indexes |= self.selected_indexes
        self.set_selected_indexes(matched_indexes)

    def show_view_context_menu(self, scene_pos: QtCore.QPointF, global_pos: QtCore.QPoint) -> None:
        idx = self.find_component_near_scene(scene_pos, self.view.pick_radius_world())
        if idx is not None and idx not in self.selected_indexes:
            self.set_selected_indexes({idx})

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

    def component_amount_key(self, component: Component) -> tuple[str, str, str]:
        return (
            component.value.strip().lower(),
            component.footprint.strip().lower(),
            component.side.strip().lower(),
        )

    def sort_components_by_amount(self) -> None:
        if self.current_pos_path is None or not self.pos_file_lines or not self.components:
            return

        ordered_components = [view.component for view in self.components]
        anchor_component = ordered_components[0]
        sortable_components = ordered_components[1:]
        if not sortable_components:
            return

        counts = Counter(self.component_amount_key(component) for component in ordered_components)
        sorted_components = sorted(
            sortable_components,
            key=lambda component: (
                -counts[self.component_amount_key(component)],
                self.component_amount_key(component)[0],
                self.component_amount_key(component)[1],
                self.component_amount_key(component)[2],
                component.source_line_index,
            ),
        )

        sorted_line_indexes = [anchor_component.source_line_index] + [
            component.source_line_index for component in sorted_components
        ]
        replacement_lines = [self.pos_file_lines[index] for index in sorted_line_indexes]
        affected_indexes = set(sorted_line_indexes)
        replacement_iter = iter(replacement_lines)
        new_lines = []
        for line_index, line in enumerate(self.pos_file_lines):
            if line_index in affected_indexes:
                new_lines.append(next(replacement_iter))
            else:
                new_lines.append(line)

        self.current_pos_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        self.load_pos_path(self.current_pos_path)
        self.log_message(
            f"Sorted {len(ordered_components)} POS component row(s) by amount in {self.current_pos_path.name} while keeping the anchor first.",
            "#86efac",
        )

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

    def combined_scene_rect(self) -> QtCore.QRectF:
        comp_rect = self.component_bounds_scene()
        if comp_rect is None:
            return QtCore.QRectF(-50, -50, 100, 100)
        return comp_rect.adjusted(-10, -10, 10, 10)

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
        description="Qt-based KiCad POS viewer."
    )
    parser.add_argument("pos_file", nargs="?", help="Optional KiCad POS file to open")
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
        return

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
