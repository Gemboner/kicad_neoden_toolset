from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE_PATH = REPO_ROOT / "template_project.csv"
DEFAULT_FEEDER_ASSIGNMENT_PATH = REPO_ROOT / "feeder_assignment.csv"
DEFAULT_GLOBAL_OFFSET_PATH = REPO_ROOT / "global_offset.json"
DEFAULT_MANIFEST_NAME = "assembly_project.json"


def load_python_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


viewer_mod = load_python_module("kicad_pos_viewer_qt_mod", REPO_ROOT / "kicad.pos_viewer_qt.py")
converter_mod = load_python_module(
    "kicad_pos_to_neoden_project_mod",
    REPO_ROOT / "kicad.pos_to_neoden_project.py",
)
feeder_inherit_mod = load_python_module("feeder_inherit_mod", REPO_ROOT / "feeder_inherit.py")


FEEDER_CSV_HEADER = list(feeder_inherit_mod.CSV_HEADER)


PATH_FIELDS = (
    "pos_file",
    "gerber_file",
    "template_file",
    "feeder_assignment_file",
    "global_offset_file",
    "neoden_project_csv",
)


def sanitize_folder_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "assembly_project"
    cleaned = []
    for char in text:
        if char.isalnum() or char in ("-", "_"):
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
    folder_name = "".join(cleaned).strip("._-")
    return folder_name or "assembly_project"


def serialize_path(value: str, base_dir: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_path(value: str, base_dir: Path) -> str:
    if not value:
        return ""
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return str(raw.resolve())
    return str((base_dir / raw).resolve())


@dataclass
class ProjectState:
    project_name: str = ""
    board_name: str = ""
    side: str = "all"
    entry_mode: str = "pos"
    pos_file: str = ""
    gerber_file: str = ""
    template_file: str = str(DEFAULT_TEMPLATE_PATH)
    feeder_assignment_file: str = str(DEFAULT_FEEDER_ASSIGNMENT_PATH)
    global_offset_file: str = str(DEFAULT_GLOBAL_OFFSET_PATH)
    chip1_x_mm: float = 0.0
    chip1_y_mm: float = 0.0
    neoden_project_csv: str = ""
    notes: str = ""

    def to_manifest_dict(self, base_dir: Path) -> dict[str, object]:
        payload = asdict(self)
        for field_name in PATH_FIELDS:
            payload[field_name] = serialize_path(str(payload[field_name]), base_dir)
        return payload

    @classmethod
    def from_manifest_dict(cls, payload: dict[str, object], base_dir: Path) -> "ProjectState":
        normalized = dict(payload)
        if not normalized.get("neoden_project_csv") and normalized.get("generated_project_csv"):
            normalized["neoden_project_csv"] = normalized["generated_project_csv"]
        for field_name in PATH_FIELDS:
            normalized[field_name] = resolve_path(str(normalized.get(field_name, "") or ""), base_dir)
        return cls(
            project_name=str(normalized.get("project_name", "") or ""),
            board_name=str(normalized.get("board_name", "") or ""),
            side=str(normalized.get("side", "all") or "all"),
            entry_mode=str(normalized.get("entry_mode", "pos") or "pos"),
            pos_file=str(normalized.get("pos_file", "") or ""),
            gerber_file=str(normalized.get("gerber_file", "") or ""),
            template_file=str(normalized.get("template_file", DEFAULT_TEMPLATE_PATH) or DEFAULT_TEMPLATE_PATH),
            feeder_assignment_file=str(
                normalized.get("feeder_assignment_file", DEFAULT_FEEDER_ASSIGNMENT_PATH)
                or DEFAULT_FEEDER_ASSIGNMENT_PATH
            ),
            global_offset_file=str(
                normalized.get("global_offset_file", DEFAULT_GLOBAL_OFFSET_PATH)
                or DEFAULT_GLOBAL_OFFSET_PATH
            ),
            chip1_x_mm=float(normalized.get("chip1_x_mm", 0.0) or 0.0),
            chip1_y_mm=float(normalized.get("chip1_y_mm", 0.0) or 0.0),
            neoden_project_csv=str(normalized.get("neoden_project_csv", "") or ""),
            notes=str(normalized.get("notes", "") or ""),
        )


@dataclass
class NeodenComponentRow:
    row_index: int
    feeder_id: str
    nozzle: str
    name: str
    value: str
    footprint: str
    x: float
    y: float
    rotation: float
    skip: str


@dataclass
class NeodenProjectData:
    path: Path
    stack_count: int
    comp_count: int
    other_header_count: int
    header_preview: list[str]
    rows: list[list[str]]
    components: list[NeodenComponentRow]


def parse_neoden_project_file(path: Path) -> NeodenProjectData:
    components: list[NeodenComponentRow] = []
    header_preview: list[str] = []
    rows: list[list[str]] = []
    stack_count = 0
    other_header_count = 0

    with path.open(newline="") as handle:
        for row_index, row in enumerate(csv.reader(handle)):
            rows.append(list(row))
            if not row:
                continue
            row_type = row[0].strip()
            raw_line = ",".join(row)
            if row_type == "stack":
                stack_count += 1
                if len(header_preview) < 20:
                    header_preview.append(raw_line)
                continue
            if row_type == "comp":
                if len(row) < 10:
                    continue
                try:
                    x = float(row[6])
                    y = float(row[7])
                    rotation = float(row[8])
                except ValueError:
                    continue
                components.append(
                    NeodenComponentRow(
                        row_index=row_index,
                        feeder_id=row[1].strip(),
                        nozzle=row[2].strip(),
                        name=row[3].strip(),
                        value=row[4].strip(),
                        footprint=row[5].strip(),
                        x=x,
                        y=y,
                        rotation=rotation,
                        skip=row[9].strip(),
                    )
                )
                continue
            other_header_count += 1
            if len(header_preview) < 20:
                header_preview.append(raw_line)

    return NeodenProjectData(
        path=path,
        stack_count=stack_count,
        comp_count=len(components),
        other_header_count=other_header_count,
        header_preview=header_preview,
        rows=rows,
        components=components,
    )


def default_feeder_row(feeder_id: str) -> dict[str, str]:
    row = feeder_inherit_mod.default_row(feeder_id)
    return {field: str(row.get(field, "") or "") for field in FEEDER_CSV_HEADER}


def normalize_feeder_row(row: dict[str, object]) -> dict[str, str]:
    normalized = {field: str(row.get(field, "") or "") for field in FEEDER_CSV_HEADER}
    feeder_id = normalized.get("feeder_id", "").strip()
    if feeder_id:
        normalized["feeder_id"] = feeder_id
    return normalized


def load_feeder_assignment_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            row = normalize_feeder_row(raw_row)
            if row["feeder_id"]:
                rows.append(row)
    return rows


def write_feeder_assignment_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FEEDER_CSV_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FEEDER_CSV_HEADER})


def parse_stack_rows_from_neoden_project(path: Path) -> list[dict[str, str]]:
    stack_rows: list[dict[str, str]] = []
    for line in path.read_text().splitlines():
        if not line.startswith("stack,"):
            continue
        parsed = feeder_inherit_mod.parse_stack_line(line)
        if parsed:
            stack_rows.append(normalize_feeder_row(parsed))
    return stack_rows


def merge_feeder_rows(
    existing_rows: list[dict[str, str]],
    stack_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    existing_by_id: dict[str, dict[str, str]] = {}
    non_numeric_rows: list[dict[str, str]] = []
    max_id = 0

    for row in existing_rows:
        feeder_id = row.get("feeder_id", "").strip()
        normalized = normalize_feeder_row(row)
        if not feeder_id:
            continue
        if feeder_id.isdigit():
            max_id = max(max_id, int(feeder_id))
            existing_by_id[feeder_id] = normalized
        else:
            non_numeric_rows.append(normalized)

    stack_map = {row["feeder_id"]: normalize_feeder_row(row) for row in stack_rows if row.get("feeder_id")}
    merged_by_id = feeder_inherit_mod.merge_stack_rows(existing_by_id, stack_rows)
    for feeder_id in stack_map:
        if feeder_id.isdigit():
            max_id = max(max_id, int(feeder_id))

    merged_rows: list[dict[str, str]] = []
    if max_id > 0:
        for feeder_num in range(1, max_id + 1):
            feeder_id = str(feeder_num)
            merged_rows.append(normalize_feeder_row(merged_by_id.get(feeder_id, default_feeder_row(feeder_id))))

    non_numeric_rows.sort(key=lambda row: row.get("feeder_id", "").lower())
    merged_rows.extend(non_numeric_rows)
    return merged_rows


def read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as handle:
        return [list(row) for row in csv.reader(handle)]


def build_neoden_component_pairs(path: Path) -> list[tuple[str, str]]:
    data = parse_neoden_project_file(path)
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for component in data.components:
        pair = (component.footprint.strip(), component.value.strip())
        if not pair[0]:
            continue
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    pairs.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    return pairs


def display_component_pair(footprint: str, value: str) -> str:
    return f"{footprint} | {value}" if value else footprint


def normalize_component_pair_key(name: str, footprint: str, value: str) -> tuple[str, str]:
    norm_footprint = converter_mod.normalize_footprint(footprint)
    norm_value = converter_mod.normalize_value(name or "C", value)
    return norm_footprint, norm_value


class CsvPreviewDialog(QtWidgets.QDialog):
    def __init__(self, path: Path, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Preview: {path.name}")
        self.resize(1200, 700)
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel(str(path))
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QtWidgets.QTableWidget()
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        rows = read_csv_rows(path)
        column_count = max((len(row) for row in rows), default=0)
        table.setColumnCount(column_count)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class InteractiveTableWidget(QtWidgets.QTableWidget):
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


class PathField(QtWidgets.QWidget):
    browseRequested = QtCore.Signal()

    def __init__(self, placeholder: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.line_edit = QtWidgets.QLineEdit()
        self.line_edit.setPlaceholderText(placeholder)
        self.button = QtWidgets.QPushButton("Browse")
        self.button.clicked.connect(self.browseRequested.emit)
        layout.addWidget(self.line_edit, 1)
        layout.addWidget(self.button)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def setText(self, value: str) -> None:
        self.line_edit.setText(value)


class FeederEditorTab(QtWidgets.QWidget):
    statusMessage = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_path: Path | None = None
        self.current_neoden_path: Path | None = None
        self.current_rows: list[dict[str, str]] = []
        self.available_component_pairs: list[tuple[str, str]] = []
        self._visible_row_indexes: list[int] = []
        self._populating_table = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top_row = QtWidgets.QHBoxLayout()
        self.path_label = QtWidgets.QLabel("No feeder assignment CSV selected.")
        self.path_label.setWordWrap(True)
        self.summary_label = QtWidgets.QLabel("0 feeders")
        self.summary_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        top_row.addWidget(self.path_label, 1)
        top_row.addWidget(self.summary_label)
        layout.addLayout(top_row)

        control_row = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("Load CSV")
        self.reload_btn = QtWidgets.QPushButton("Reload")
        self.save_btn = QtWidgets.QPushButton("Save")
        self.clear_assignment_btn = QtWidgets.QPushButton("Clear Component Assignment")
        self.import_neoden_btn = QtWidgets.QPushButton("Import NeoDen CSV")
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Filter feeders by id, package, footprint, value, nozzle")
        self.load_btn.clicked.connect(self.load_external_file_into_editor)
        self.reload_btn.clicked.connect(self.reload_current_file)
        self.save_btn.clicked.connect(self.save_current_file)
        self.clear_assignment_btn.clicked.connect(self.clear_selected_component_assignments)
        self.import_neoden_btn.clicked.connect(self.import_from_neoden_dialog)
        self.load_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.save_btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.load_btn.customContextMenuRequested.connect(self.show_load_button_menu)
        self.save_btn.customContextMenuRequested.connect(self.show_save_button_menu)
        self.search_edit.textChanged.connect(self.populate_table)
        control_row.addWidget(self.load_btn)
        control_row.addWidget(self.reload_btn)
        control_row.addWidget(self.save_btn)
        control_row.addWidget(self.clear_assignment_btn)
        control_row.addWidget(self.import_neoden_btn)
        control_row.addWidget(self.search_edit, 1)
        layout.addLayout(control_row)

        self.table = InteractiveTableWidget()
        self.table.setColumnCount(len(FEEDER_CSV_HEADER))
        self.table.setHorizontalHeaderLabels(FEEDER_CSV_HEADER)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self.on_item_changed)
        self.table.rowsDropped.connect(self.on_table_rows_reordered)
        self.table.deleteRequested.connect(self.delete_selected_rows)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        layout.addWidget(self.table, 1)

    def clear(self) -> None:
        self.current_path = None
        self.current_rows = []
        self._visible_row_indexes = []
        self.path_label.setText("No feeder assignment CSV selected.")
        self.summary_label.setText("0 feeders")
        self.table.setRowCount(0)

    def set_neoden_project_path(self, path: Path | None) -> None:
        self.current_neoden_path = path.resolve() if path is not None else None
        self.available_component_pairs = []
        if self.current_neoden_path is not None and self.current_neoden_path.exists():
            try:
                self.available_component_pairs = build_neoden_component_pairs(self.current_neoden_path)
            except OSError:
                self.available_component_pairs = []

    def set_feeder_assignment_path(self, path: Path | None) -> None:
        if path is None:
            self.clear()
            return
        resolved = path.resolve()
        if self.current_path == resolved:
            self.load_file(resolved)
            return
        self.current_path = resolved
        self.load_file(resolved)

    def load_file(self, path: Path) -> None:
        self.current_path = path.resolve()
        if path.exists():
            self.current_rows = load_feeder_assignment_rows(path)
            status_text = f"{len(self.current_rows)} feeders"
        else:
            self.current_rows = []
            status_text = "new local feeder file"
        self.path_label.setText(str(self.current_path))
        self.summary_label.setText(status_text)
        self.populate_table()

    def choose_csv_file(self, title: str) -> Path | None:
        start_dir = str(self.current_path.parent) if self.current_path else str(REPO_ROOT)
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            title,
            start_dir,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path_str:
            return None
        return Path(path_str).resolve()

    def show_load_button_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        preview_action = menu.addAction("Open Preview...")
        chosen = menu.exec(self.load_btn.mapToGlobal(pos))
        if chosen == preview_action:
            self.open_external_preview_dialog()

    def show_save_button_menu(self, pos: QtCore.QPoint) -> None:
        menu = QtWidgets.QMenu(self)
        save_as_action = menu.addAction("Save As...")
        chosen = menu.exec(self.save_btn.mapToGlobal(pos))
        if chosen == save_as_action:
            self.save_current_file_as()

    def load_external_file_into_editor(self) -> None:
        if self.current_path is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Feeder Editor",
                "Create or open a project first so the feeder editor has a local project file.",
            )
            return
        source_path = self.choose_csv_file("Load Feeder Assignment CSV Copy")
        if source_path is None:
            return
        try:
            self.current_rows = load_feeder_assignment_rows(source_path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Feeder Editor",
                f"Failed to load feeder assignment CSV:\n{source_path}\n\n{exc}",
            )
            return
        self.populate_table()
        self.statusMessage.emit(
            f"Loaded {source_path.name} into editor. Save to update {self.current_path.name}"
        )

    def open_external_preview_dialog(self) -> None:
        source_path = self.choose_csv_file("Preview Feeder Assignment CSV")
        if source_path is None:
            return
        try:
            dialog = CsvPreviewDialog(source_path, self)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Feeder Editor",
                f"Failed to preview feeder assignment CSV:\n{source_path}\n\n{exc}",
            )
            return
        dialog.exec()

    def reload_current_file(self) -> None:
        if self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "Feeder Editor", "Select a feeder assignment CSV first.")
            return
        self.load_file(self.current_path)

    def populate_table(self) -> None:
        self._populating_table = True
        self.table.setRowCount(0)
        self._visible_row_indexes = []
        query = self.search_edit.text().strip().lower()

        for row_index, row in enumerate(self.current_rows):
            haystack = " ".join(row.get(field, "") for field in FEEDER_CSV_HEADER).lower()
            if query and query not in haystack:
                continue
            table_row = self.table.rowCount()
            self.table.insertRow(table_row)
            self._visible_row_indexes.append(row_index)
            for col, field in enumerate(FEEDER_CSV_HEADER):
                item = QtWidgets.QTableWidgetItem(row.get(field, ""))
                if field in ("feeder_id", "footprint", "value"):
                    item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                self.table.setItem(table_row, col, item)

        self.summary_label.setText(f"{len(self.current_rows)} feeders")
        self._populating_table = False

    def selected_row_indexes(self) -> list[int]:
        indexes = []
        for model_index in self.table.selectionModel().selectedRows():
            row = model_index.row()
            if 0 <= row < len(self._visible_row_indexes):
                indexes.append(self._visible_row_indexes[row])
        return sorted(set(indexes))

    def on_item_changed(self, item: QtWidgets.QTableWidgetItem) -> None:
        if self._populating_table:
            return
        row = item.row()
        col = item.column()
        if not (0 <= row < len(self._visible_row_indexes)):
            return
        data_row_index = self._visible_row_indexes[row]
        field_name = FEEDER_CSV_HEADER[col]
        if field_name == "feeder_id":
            return
        self.current_rows[data_row_index][field_name] = item.text().strip()

    def save_current_file(self) -> None:
        if self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "Feeder Editor", "Select a feeder assignment CSV first.")
            return
        try:
            write_feeder_assignment_rows(self.current_path, self.current_rows)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Feeder Editor",
                f"Failed to save feeder assignment CSV:\n{self.current_path}\n\n{exc}",
            )
            return
        self.statusMessage.emit(f"Saved feeder assignments: {self.current_path}")
        self.load_file(self.current_path)

    def save_current_file_as(self) -> None:
        if not self.current_rows:
            QtWidgets.QMessageBox.warning(self, "Feeder Editor", "There is no feeder data to save.")
            return
        start_dir = str(self.current_path.parent) if self.current_path else str(REPO_ROOT)
        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Feeder Assignment CSV As",
            start_dir,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path_str:
            return
        target_path = Path(path_str).resolve()
        try:
            write_feeder_assignment_rows(target_path, self.current_rows)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Feeder Editor",
                f"Failed to save feeder assignment CSV:\n{target_path}\n\n{exc}",
            )
            return
        self.statusMessage.emit(f"Saved feeder assignment copy: {target_path}")

    def import_from_neoden_dialog(self) -> None:
        if self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "Feeder Editor", "Select a feeder assignment CSV first.")
            return
        source_path = self.choose_csv_file("Select NeoDen Project CSV")
        if source_path is None:
            return
        self.import_from_neoden_project(source_path)

    def import_from_neoden_project(self, neoden_path: Path) -> None:
        if self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "Feeder Editor", "Select a feeder assignment CSV first.")
            return
        try:
            stack_rows = parse_stack_rows_from_neoden_project(neoden_path)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "Feeder Editor",
                f"Failed to read NeoDen project CSV:\n{neoden_path}\n\n{exc}",
            )
            return

        if not stack_rows:
            QtWidgets.QMessageBox.warning(
                self,
                "Feeder Editor",
                f"No stack rows were found in:\n{neoden_path}",
            )
            return

        self.current_rows = merge_feeder_rows(self.current_rows, stack_rows)
        self.populate_table()
        self.statusMessage.emit(
            f"Imported {len(stack_rows)} stack row(s) from {neoden_path.name}. Save to update {self.current_path.name}"
        )

    def show_table_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), index.parent()):
            self.table.clearSelection()
            self.table.selectRow(index.row())
        if not self.selected_row_indexes():
            return
        menu = QtWidgets.QMenu(self)
        choose_component_action = None
        if index.isValid() and FEEDER_CSV_HEADER[index.column()] in ("footprint", "value"):
            choose_component_action = menu.addAction("Choose Component From NeoDen...")
        clear_action = menu.addAction("Clear Component Assignment")
        delete_label = "Delete Feeders" if len(self.selected_row_indexes()) > 1 else "Delete Feeder"
        delete_action = menu.addAction(delete_label)
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if choose_component_action is not None and chosen == choose_component_action:
            self.assign_component_pair_from_neoden()
        elif chosen == clear_action:
            self.clear_selected_component_assignments()
        elif chosen == delete_action:
            self.delete_selected_rows()

    def assign_component_pair_from_neoden(self) -> None:
        if not self.available_component_pairs:
            QtWidgets.QMessageBox.warning(
                self,
                "Feeder Editor",
                "Load a NeoDen project CSV first so valid component pairs are available.",
            )
            return
        items = [display_component_pair(footprint, value) for footprint, value in self.available_component_pairs]
        selected_text, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "Choose Component Assignment",
            "Component",
            items,
            0,
            False,
        )
        if not accepted:
            return
        try:
            chosen_index = items.index(selected_text)
        except ValueError:
            return
        footprint, value = self.available_component_pairs[chosen_index]
        self.apply_component_pair_to_selected_rows(footprint, value)

    def apply_component_pair_to_selected_rows(self, footprint: str, value: str) -> None:
        selected_indexes = self.selected_row_indexes()
        if not selected_indexes:
            return
        for row_index in selected_indexes:
            if not (0 <= row_index < len(self.current_rows)):
                continue
            row = self.current_rows[row_index]
            row["footprint"] = footprint
            row["value"] = value
        self.populate_table()
        self.statusMessage.emit(
            f"Assigned NeoDen component pair to {len(selected_indexes)} feeder row(s). Save to persist changes."
        )

    def clear_selected_component_assignments(self) -> None:
        selected_indexes = self.selected_row_indexes()
        if not selected_indexes:
            return
        for row_index in selected_indexes:
            if not (0 <= row_index < len(self.current_rows)):
                continue
            row = self.current_rows[row_index]
            for field_name in ("package", "footprint", "value"):
                row[field_name] = ""
        self.populate_table()
        self.statusMessage.emit(
            f"Cleared component assignment for {len(selected_indexes)} feeder row(s). Save to persist changes."
        )

    def on_table_rows_reordered(self, selected_rows: list[int], target_row: int) -> None:
        if not selected_rows:
            return
        visible_rows = [self.current_rows[index] for index in self._visible_row_indexes]
        moving_rows = [visible_rows[row] for row in selected_rows if 0 <= row < len(visible_rows)]
        if not moving_rows:
            return
        moving_ids = {id(row) for row in moving_rows}
        target_row_probe = target_row
        target_row_obj = None
        while 0 <= target_row_probe < len(visible_rows):
            candidate = visible_rows[target_row_probe]
            if id(candidate) not in moving_ids:
                target_row_obj = candidate
                break
            target_row_probe += 1
        remaining_rows = [row for row in self.current_rows if id(row) not in moving_ids]
        if target_row_obj is None:
            insert_at = len(remaining_rows)
        else:
            insert_at = next(
                index for index, row in enumerate(remaining_rows) if id(row) == id(target_row_obj)
            )
        self.current_rows = remaining_rows[:insert_at] + moving_rows + remaining_rows[insert_at:]
        self.populate_table()
        self.statusMessage.emit("Reordered feeder rows in editor. Save to persist changes.")

    def delete_selected_rows(self) -> None:
        selected_indexes = self.selected_row_indexes()
        selected_ids = {
            id(self.current_rows[index])
            for index in selected_indexes
            if 0 <= index < len(self.current_rows)
        }
        if not selected_ids:
            return
        self.current_rows = [row for row in self.current_rows if id(row) not in selected_ids]
        self.populate_table()
        self.statusMessage.emit(
            f"Deleted {len(selected_indexes)} feeder row(s) in editor. Save to persist changes."
        )


class NeodenProjectTab(QtWidgets.QWidget):
    assignmentSaved = QtCore.Signal(str)
    autoAssignRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_path: Path | None = None
        self.current_data: NeodenProjectData | None = None
        self.feeder_assignment_path: Path | None = DEFAULT_FEEDER_ASSIGNMENT_PATH
        self._visible_component_indexes: list[int] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top_row = QtWidgets.QHBoxLayout()
        self.path_label = QtWidgets.QLabel("No NeoDen project CSV loaded.")
        self.path_label.setWordWrap(True)
        self.summary_label = QtWidgets.QLabel("0 components")
        self.summary_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        top_row.addWidget(self.path_label, 1)
        top_row.addWidget(self.summary_label)
        layout.addLayout(top_row)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Filter by feeder, ref, value, footprint, skip")
        self.search_edit.textChanged.connect(self.populate_table)
        layout.addWidget(self.search_edit)

        assignment_row = QtWidgets.QHBoxLayout()
        assignment_row.setSpacing(6)
        self.selection_label = QtWidgets.QLabel("0 selected")
        self.feeder_combo = QtWidgets.QComboBox()
        self.feeder_combo.setEditable(True)
        self.feeder_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.feeder_combo.setMinimumWidth(220)
        self.nozzle_combo = QtWidgets.QComboBox()
        self.nozzle_combo.setEditable(True)
        self.nozzle_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.nozzle_combo.setMinimumWidth(120)
        self.apply_feeder_btn = QtWidgets.QPushButton("Assign Feeder")
        self.clear_feeder_btn = QtWidgets.QPushButton("Clear Feeder")
        self.apply_nozzle_btn = QtWidgets.QPushButton("Assign Nozzle")
        self.apply_both_btn = QtWidgets.QPushButton("Assign Both")
        self.auto_reload_btn = QtWidgets.QPushButton("Auto Reload Feeder Assignments")
        self.apply_feeder_btn.clicked.connect(self.assign_selected_feeder_from_controls)
        self.clear_feeder_btn.clicked.connect(self.clear_selected_feeder_assignments)
        self.apply_nozzle_btn.clicked.connect(self.assign_selected_nozzle_from_controls)
        self.apply_both_btn.clicked.connect(self.assign_selected_feeder_and_nozzle_from_controls)
        self.auto_reload_btn.clicked.connect(self.autoAssignRequested.emit)
        assignment_row.addWidget(self.selection_label)
        assignment_row.addWidget(QtWidgets.QLabel("Feeder"))
        assignment_row.addWidget(self.feeder_combo)
        assignment_row.addWidget(QtWidgets.QLabel("Nozzle"))
        assignment_row.addWidget(self.nozzle_combo)
        assignment_row.addWidget(self.apply_feeder_btn)
        assignment_row.addWidget(self.clear_feeder_btn)
        assignment_row.addWidget(self.apply_nozzle_btn)
        assignment_row.addWidget(self.apply_both_btn)
        assignment_row.addWidget(self.auto_reload_btn)
        assignment_row.addStretch(1)
        layout.addLayout(assignment_row)

        self.table = InteractiveTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["Feeder", "Nozzle", "Name", "Value", "Footprint", "X", "Y", "Rot", "Skip"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        self.table.itemSelectionChanged.connect(self.update_selection_summary)
        self.table.rowsDropped.connect(self.on_table_rows_reordered)
        self.table.deleteRequested.connect(self.delete_selected_components)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        layout.addWidget(QtWidgets.QLabel("Header Preview"))
        self.header_preview = QtWidgets.QPlainTextEdit()
        self.header_preview.setReadOnly(True)
        self.header_preview.setMaximumHeight(160)
        self.header_preview.setStyleSheet(
            "QPlainTextEdit { background:#f8fafc; color:#0f172a; font-family:monospace; }"
        )
        layout.addWidget(self.header_preview)

    def clear(self) -> None:
        self.current_path = None
        self.current_data = None
        self._visible_component_indexes = []
        self.path_label.setText("No NeoDen project CSV loaded.")
        self.summary_label.setText("0 components")
        self.header_preview.clear()
        self.table.setRowCount(0)
        self.update_assignment_options()
        self.update_selection_summary()

    def set_feeder_assignment_path(self, path: Path | None) -> None:
        self.feeder_assignment_path = path
        self.update_assignment_options()

    def load_file(self, path: Path) -> None:
        data = parse_neoden_project_file(path)
        self.current_path = path
        self.current_data = data
        self.path_label.setText(str(path))
        self.summary_label.setText(
            f"{data.comp_count} comps | {data.stack_count} stack | {data.other_header_count} header"
        )
        self.header_preview.setPlainText("\n".join(data.header_preview))
        self.update_assignment_options()
        self.populate_table()

    def populate_table(self) -> None:
        self.table.setRowCount(0)
        self._visible_component_indexes = []
        if self.current_data is None:
            self.update_selection_summary()
            return
        query = self.search_edit.text().strip().lower()
        for component_index, component in enumerate(self.current_data.components):
            haystack = " ".join(
                [
                    component.feeder_id,
                    component.nozzle,
                    component.name,
                    component.value,
                    component.footprint,
                    component.skip,
                ]
            ).lower()
            if query and query not in haystack:
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)
            self._visible_component_indexes.append(component_index)
            values = [
                component.feeder_id,
                component.nozzle,
                component.name,
                component.value,
                component.footprint,
                f"{component.x:.2f}",
                f"{component.y:.2f}",
                f"{component.rotation:.2f}",
                component.skip,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.update_selection_summary()

    def _feeder_sort_key(self, value: str) -> tuple[int, int | str]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value.lower())

    def _display_feeder_label(self, feeder_id: str, footprint: str, value: str) -> str:
        parts = [feeder_id]
        details = [part for part in (footprint, value) if part]
        if details:
            parts.append(" / ".join(details))
        return " | ".join(parts)

    def _current_combo_value(self, combo: QtWidgets.QComboBox) -> str:
        data = combo.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        text = combo.currentText().strip()
        if "|" in text:
            return text.split("|", 1)[0].strip()
        return text

    def _set_combo_value(self, combo: QtWidgets.QComboBox, value: str) -> None:
        target = value.strip()
        if not target:
            combo.setEditText("")
            return
        for index in range(combo.count()):
            item_value = combo.itemData(index)
            item_text = combo.itemText(index)
            if item_value == target or item_text.split("|", 1)[0].strip() == target:
                combo.setCurrentIndex(index)
                return
        combo.setEditText(target)

    def update_assignment_options(self) -> None:
        current_feeder = self._current_combo_value(self.feeder_combo)
        current_nozzle = self._current_combo_value(self.nozzle_combo)
        feeder_labels: dict[str, str] = {}
        nozzle_values: set[str] = set()

        if self.feeder_assignment_path is not None and self.feeder_assignment_path.exists():
            with self.feeder_assignment_path.open(newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    feeder_id = (row.get("feeder_id") or "").strip()
                    if feeder_id:
                        feeder_labels.setdefault(
                            feeder_id,
                            self._display_feeder_label(
                                feeder_id,
                                (row.get("footprint") or "").strip(),
                                (row.get("value") or "").strip(),
                            ),
                        )
                    nozzle = (row.get("nozzle") or "").strip()
                    if nozzle:
                        nozzle_values.add(nozzle)

        if self.current_data is not None:
            for component in self.current_data.components:
                if component.feeder_id:
                    feeder_labels.setdefault(
                        component.feeder_id,
                        self._display_feeder_label(component.feeder_id, component.footprint, component.value),
                    )
                if component.nozzle:
                    nozzle_values.add(component.nozzle)

        self.feeder_combo.blockSignals(True)
        self.feeder_combo.clear()
        for feeder_id in sorted(feeder_labels, key=self._feeder_sort_key):
            self.feeder_combo.addItem(feeder_labels[feeder_id], feeder_id)
        self.feeder_combo.blockSignals(False)

        self.nozzle_combo.blockSignals(True)
        self.nozzle_combo.clear()
        for nozzle in sorted(nozzle_values, key=self._feeder_sort_key):
            self.nozzle_combo.addItem(nozzle, nozzle)
        self.nozzle_combo.blockSignals(False)

        self._set_combo_value(self.feeder_combo, current_feeder)
        self._set_combo_value(self.nozzle_combo, current_nozzle)

    def selected_component_indexes(self) -> list[int]:
        indexes = []
        for model_index in self.table.selectionModel().selectedRows():
            row = model_index.row()
            if 0 <= row < len(self._visible_component_indexes):
                indexes.append(self._visible_component_indexes[row])
        return sorted(set(indexes))

    def update_selection_summary(self) -> None:
        selected_count = len(self.selected_component_indexes()) if self.table.model() is not None else 0
        self.selection_label.setText(f"{selected_count} selected")

    def show_table_context_menu(self, pos: QtCore.QPoint) -> None:
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), index.parent()):
            self.table.clearSelection()
            self.table.selectRow(index.row())

        if not self.selected_component_indexes():
            return

        menu = QtWidgets.QMenu(self)
        assign_feeder_action = menu.addAction("Assign Feeder...")
        clear_feeder_action = menu.addAction("Clear Feeder Assignment")
        assign_nozzle_action = menu.addAction("Assign Nozzle...")
        assign_both_action = menu.addAction("Assign Feeder + Nozzle...")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Selected")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == assign_feeder_action:
            self.prompt_assign_selected_feeder()
        elif chosen == clear_feeder_action:
            self.clear_selected_feeder_assignments()
        elif chosen == assign_nozzle_action:
            self.prompt_assign_selected_nozzle()
        elif chosen == assign_both_action:
            self.prompt_assign_selected_feeder_and_nozzle()
        elif chosen == delete_action:
            self.delete_selected_components()

    def on_table_rows_reordered(self, selected_rows: list[int], target_row: int) -> None:
        if not selected_rows:
            return
        visible_components = [self.current_data.components[index] for index in self._visible_component_indexes] if self.current_data else []
        moving_components = [visible_components[row] for row in selected_rows if 0 <= row < len(visible_components)]
        if not moving_components:
            return
        moving_ids = {id(component) for component in moving_components}
        target_probe = target_row
        target_component = None
        while 0 <= target_probe < len(visible_components):
            candidate = visible_components[target_probe]
            if id(candidate) not in moving_ids:
                target_component = candidate
                break
            target_probe += 1
        self.reorder_component_rows(
            [component.row_index for component in moving_components],
            target_component.row_index if target_component else None,
        )

    def reorder_component_rows(self, moving_row_indexes: list[int], target_row_index: int | None) -> None:
        if self.current_data is None or self.current_path is None:
            return
        moving_set = set(moving_row_indexes)
        moving_rows = [self.current_data.rows[index] for index in sorted(moving_row_indexes)]
        remaining_rows = [row for index, row in enumerate(self.current_data.rows) if index not in moving_set]
        if target_row_index is None:
            insert_at = len(remaining_rows)
        else:
            insert_at = sum(1 for index in range(target_row_index) if index not in moving_set)
        self.current_data.rows = remaining_rows[:insert_at] + moving_rows + remaining_rows[insert_at:]
        if self.write_current_rows_to_disk():
            self.assignmentSaved.emit(f"Reordered NeoDen rows in {self.current_path.name}")
            self.load_file(self.current_path)

    def delete_selected_components(self) -> None:
        if self.current_data is None or self.current_path is None:
            return
        selected_indexes = self.selected_component_indexes()
        if not selected_indexes:
            return
        delete_row_indexes = {self.current_data.components[index].row_index for index in selected_indexes}
        self.current_data.rows = [
            row for row_index, row in enumerate(self.current_data.rows) if row_index not in delete_row_indexes
        ]
        if self.write_current_rows_to_disk():
            self.assignmentSaved.emit(
                f"Deleted {len(delete_row_indexes)} NeoDen row(s) from {self.current_path.name}"
            )
            self.load_file(self.current_path)

    def prompt_assign_selected_feeder(self) -> None:
        feeder_id = self.prompt_for_feeder_value()
        if feeder_id is not None:
            self.apply_assignments_to_selected(feeder_id=feeder_id)

    def prompt_assign_selected_nozzle(self) -> None:
        nozzle = self.prompt_for_nozzle_value()
        if nozzle is not None:
            self.apply_assignments_to_selected(nozzle=nozzle)

    def prompt_assign_selected_feeder_and_nozzle(self) -> None:
        feeder_id = self.prompt_for_feeder_value()
        if feeder_id is None:
            return
        nozzle = self.prompt_for_nozzle_value()
        if nozzle is None:
            return
        self.apply_assignments_to_selected(feeder_id=feeder_id, nozzle=nozzle)

    def prompt_for_feeder_value(self) -> str | None:
        items = [self.feeder_combo.itemText(index) for index in range(self.feeder_combo.count())]
        current = self._current_combo_value(self.feeder_combo)
        if current and current not in items:
            items.insert(0, current)
        value, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "Assign Feeder",
            "Feeder",
            items,
            0,
            True,
        )
        if not accepted:
            return None
        feeder_id = value.split("|", 1)[0].strip()
        self._set_combo_value(self.feeder_combo, feeder_id)
        return feeder_id

    def prompt_for_nozzle_value(self) -> str | None:
        items = [self.nozzle_combo.itemText(index) for index in range(self.nozzle_combo.count())]
        current = self._current_combo_value(self.nozzle_combo)
        if current and current not in items:
            items.insert(0, current)
        value, accepted = QtWidgets.QInputDialog.getItem(
            self,
            "Assign Nozzle",
            "Nozzle",
            items,
            0,
            True,
        )
        if not accepted:
            return None
        nozzle = value.strip()
        self._set_combo_value(self.nozzle_combo, nozzle)
        return nozzle

    def assign_selected_feeder_from_controls(self) -> None:
        self.apply_assignments_to_selected(feeder_id=self._current_combo_value(self.feeder_combo))

    def clear_selected_feeder_assignments(self) -> None:
        self.apply_assignments_to_selected(feeder_id="", allow_empty_feeder=True)

    def assign_selected_nozzle_from_controls(self) -> None:
        self.apply_assignments_to_selected(nozzle=self._current_combo_value(self.nozzle_combo))

    def assign_selected_feeder_and_nozzle_from_controls(self) -> None:
        self.apply_assignments_to_selected(
            feeder_id=self._current_combo_value(self.feeder_combo),
            nozzle=self._current_combo_value(self.nozzle_combo),
        )

    def apply_assignments_to_selected(
        self,
        feeder_id: str | None = None,
        nozzle: str | None = None,
        allow_empty_feeder: bool = False,
    ) -> None:
        if self.current_data is None or self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "NeoDen Project", "Load a NeoDen project CSV first.")
            return

        selected_indexes = self.selected_component_indexes()
        if not selected_indexes:
            QtWidgets.QMessageBox.warning(self, "NeoDen Project", "Select at least one component row first.")
            return

        feeder_value = feeder_id.strip() if feeder_id is not None else None
        nozzle_value = nozzle.strip() if nozzle is not None else None
        if feeder_value is None and nozzle_value is None:
            return
        if feeder_value == "" and nozzle_value is None and not allow_empty_feeder:
            QtWidgets.QMessageBox.warning(self, "NeoDen Project", "Choose a feeder value first.")
            return
        if nozzle_value == "" and feeder_value is None:
            QtWidgets.QMessageBox.warning(self, "NeoDen Project", "Choose a nozzle value first.")
            return

        for component_index in selected_indexes:
            component = self.current_data.components[component_index]
            row = self.current_data.rows[component.row_index]
            while len(row) < 10:
                row.append("")
            if feeder_value is not None:
                component.feeder_id = feeder_value
                row[1] = feeder_value
            if nozzle_value is not None:
                component.nozzle = nozzle_value
                row[2] = nozzle_value

        if not self.write_current_rows_to_disk():
            return

        self.assignmentSaved.emit(
            f"Updated {len(selected_indexes)} component(s) in {self.current_path.name}"
        )
        self.load_file(self.current_path)

    def write_current_rows_to_disk(self) -> bool:
        if self.current_path is None or self.current_data is None:
            return False
        try:
            with self.current_path.open("w", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerows(self.current_data.rows)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self,
                "NeoDen Project",
                f"Failed to save NeoDen project CSV:\n{self.current_path}\n\n{exc}",
            )
            return False
        return True

    def auto_assign_from_feeder_rows(self, feeder_rows: list[dict[str, str]]) -> None:
        if self.current_data is None or self.current_path is None:
            QtWidgets.QMessageBox.warning(self, "NeoDen Project", "Load a NeoDen project CSV first.")
            return
        exact_map: dict[tuple[str, str], tuple[str, str]] = {}
        footprint_map: dict[str, tuple[str, str]] = {}
        for row in feeder_rows:
            feeder_id = (row.get("feeder_id") or "").strip()
            if not feeder_id:
                continue
            footprint = (row.get("footprint") or "").strip()
            value = (row.get("value") or "").strip()
            nozzle = (row.get("nozzle") or "").strip()
            if not footprint:
                continue
            exact_key = normalize_component_pair_key("C", footprint, value)
            footprint_key = converter_mod.normalize_footprint(footprint)
            if value and exact_key not in exact_map:
                exact_map[exact_key] = (feeder_id, nozzle)
            elif not value and footprint_key not in footprint_map:
                footprint_map[footprint_key] = (feeder_id, nozzle)

        updated_count = 0
        cleared_count = 0
        for component in self.current_data.components:
            row = self.current_data.rows[component.row_index]
            while len(row) < 10:
                row.append("")
            exact_key = normalize_component_pair_key(component.name, component.footprint, component.value)
            footprint_key = converter_mod.normalize_footprint(component.footprint)
            match = exact_map.get(exact_key) or footprint_map.get(footprint_key)
            new_feeder = match[0] if match is not None else ""
            new_nozzle = match[1] if match is not None else ""
            if row[1].strip() != new_feeder or row[2].strip() != new_nozzle:
                if match is None:
                    cleared_count += 1
                else:
                    updated_count += 1
            component.feeder_id = new_feeder
            component.nozzle = new_nozzle
            row[1] = new_feeder
            row[2] = new_nozzle

        if not self.write_current_rows_to_disk():
            return
        self.assignmentSaved.emit(
            f"Auto reloaded feeder assignments for {updated_count} component(s); cleared {cleared_count} unmatched row(s) in {self.current_path.name}"
        )
        self.load_file(self.current_path)


class AssemblyProjectWindow(viewer_mod.PosViewerQtWindow):
    def __init__(
        self,
        project_manifest: Path | None = None,
        pos_path: Path | None = None,
        gerber_path: Path | None = None,
        side: str = "all",
    ) -> None:
        self.project_dir: Path | None = None
        self.manifest_path: Path | None = None
        self.project_state = ProjectState(side=side)
        super().__init__(pos_path=None, gerber_path=None, side=side)
        self.setWindowTitle("Assembly Project Prototype")
        self._install_main_tabs()
        self._build_project_dock()
        self._build_menu()
        self._connect_project_signals()
        self.refresh_project_ui()

        if project_manifest is not None:
            self.load_project(project_manifest)
        else:
            if pos_path is not None:
                self.set_project_pos(pos_path, load_into_viewer=True)
            if gerber_path is not None:
                self.set_project_gerber(gerber_path, load_into_viewer=True)

    def _install_main_tabs(self) -> None:
        existing = self.centralWidget()
        if existing is None:
            raise RuntimeError("Expected the base viewer to create a central widget.")
        existing.setParent(None)
        self.main_tabs = QtWidgets.QTabWidget()
        self.project_pos_tab = existing
        self.neoden_tab = NeodenProjectTab()
        self.feeder_editor_tab = FeederEditorTab()
        self.neoden_tab.assignmentSaved.connect(
            lambda message: self.log_message(message, "#86efac")
        )
        self.neoden_tab.autoAssignRequested.connect(self.auto_assign_neoden_from_feeder_editor)
        self.feeder_editor_tab.statusMessage.connect(
            lambda message: self.log_message(message, "#86efac")
        )
        self.main_tabs.addTab(self.project_pos_tab, "Project / POS")
        self.main_tabs.addTab(self.neoden_tab, "NeoDen Project CSV")
        self.main_tabs.addTab(self.feeder_editor_tab, "feeder_editor")
        self.setCentralWidget(self.main_tabs)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Project")

        new_action = QtGui.QAction("New Project", self)
        new_action.triggered.connect(self.new_project_dialog)
        file_menu.addAction(new_action)

        open_action = QtGui.QAction("Open Project", self)
        open_action.triggered.connect(self.open_project_dialog)
        file_menu.addAction(open_action)

        open_neoden_action = QtGui.QAction("Open NeoDen Project CSV", self)
        open_neoden_action.triggered.connect(self.open_neoden_project_dialog)
        file_menu.addAction(open_neoden_action)

        save_action = QtGui.QAction("Save Project", self)
        save_action.triggered.connect(self.save_project)
        file_menu.addAction(save_action)

        save_as_action = QtGui.QAction("Save Project As", self)
        save_as_action.triggered.connect(self.save_project_as_dialog)
        file_menu.addAction(save_as_action)

    def _build_project_dock(self) -> None:
        dock = QtWidgets.QDockWidget("Project", self)
        dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        button_row = QtWidgets.QHBoxLayout()
        self.new_project_btn = QtWidgets.QPushButton("New")
        self.open_project_btn = QtWidgets.QPushButton("Open")
        self.save_project_btn = QtWidgets.QPushButton("Save")
        button_row.addWidget(self.new_project_btn)
        button_row.addWidget(self.open_project_btn)
        button_row.addWidget(self.save_project_btn)
        layout.addLayout(button_row)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)

        self.manifest_label = QtWidgets.QLabel("-")
        self.manifest_label.setWordWrap(True)
        form.addRow("Manifest", self.manifest_label)

        self.project_name_edit = QtWidgets.QLineEdit()
        self.board_name_edit = QtWidgets.QLineEdit()
        form.addRow("Project", self.project_name_edit)
        form.addRow("Board", self.board_name_edit)

        self.side_value_label = QtWidgets.QLabel(self.side_filter)
        form.addRow("Viewer Side", self.side_value_label)

        self.pos_field = PathField("Select KiCad .pos file")
        self.gerber_field = PathField("Optional Gerber overlay")
        self.template_field = PathField("NeoDen template project CSV")
        self.feeder_field = PathField("Feeder assignment CSV")
        self.feeder_field.line_edit.setReadOnly(True)
        self.feeder_field.button.setText("Import")
        self.offset_field = PathField("Global offset JSON")
        form.addRow("POS File", self.pos_field)
        form.addRow("Gerber", self.gerber_field)
        form.addRow("Template", self.template_field)
        form.addRow("Feeders", self.feeder_field)
        form.addRow("Offsets", self.offset_field)

        self.chip1_x_spin = QtWidgets.QDoubleSpinBox()
        self.chip1_x_spin.setDecimals(4)
        self.chip1_x_spin.setRange(-100000.0, 100000.0)
        self.chip1_x_spin.setSingleStep(0.1)
        self.chip1_y_spin = QtWidgets.QDoubleSpinBox()
        self.chip1_y_spin.setDecimals(4)
        self.chip1_y_spin.setRange(-100000.0, 100000.0)
        self.chip1_y_spin.setSingleStep(0.1)
        form.addRow("Chip_1 X", self.chip1_x_spin)
        form.addRow("Chip_1 Y", self.chip1_y_spin)

        self.output_label = QtWidgets.QLabel("-")
        self.output_label.setWordWrap(True)
        form.addRow("Generated", self.output_label)

        layout.addLayout(form)

        self.notes_edit = QtWidgets.QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Operator notes or job notes")
        self.notes_edit.setMaximumHeight(100)
        layout.addWidget(QtWidgets.QLabel("Notes"))
        layout.addWidget(self.notes_edit)

        action_row = QtWidgets.QHBoxLayout()
        self.generate_btn = QtWidgets.QPushButton("Generate Project CSV")
        self.open_generated_btn = QtWidgets.QPushButton("Open Generated Dir")
        action_row.addWidget(self.generate_btn)
        action_row.addWidget(self.open_generated_btn)
        layout.addLayout(action_row)

        self.summary_label = QtWidgets.QLabel(
            "Project manifests store source paths, machine anchor values, and generated outputs."
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("color:#475569;")
        layout.addWidget(self.summary_label)
        layout.addStretch(1)

        dock.setWidget(container)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
        self.project_dock = dock

    def _connect_project_signals(self) -> None:
        self.new_project_btn.clicked.connect(self.new_project_dialog)
        self.open_project_btn.clicked.connect(self.open_project_dialog)
        self.save_project_btn.clicked.connect(self.save_project)
        self.generate_btn.clicked.connect(self.generate_project_csv)
        self.open_generated_btn.clicked.connect(self.open_generated_dir)
        self.feeder_field.line_edit.textChanged.connect(self.on_feeder_assignment_path_changed)

        self.pos_field.browseRequested.connect(self.open_pos_dialog)
        self.gerber_field.browseRequested.connect(self.open_gerber_dialog)
        self.template_field.browseRequested.connect(
            lambda: self.select_generic_file(
                self.template_field,
                "Select NeoDen Template Project",
                "CSV files (*.csv);;All files (*.*)",
            )
        )
        self.feeder_field.browseRequested.connect(self.import_feeder_assignment_copy_dialog)
        self.offset_field.browseRequested.connect(
            lambda: self.select_generic_file(
                self.offset_field,
                "Select Offset JSON",
                "JSON files (*.json);;All files (*.*)",
            )
        )

        self.side_combo.currentTextChanged.connect(self.on_side_changed_for_project)

    def on_feeder_assignment_path_changed(self, _value: str) -> None:
        path_text = self.feeder_field.text()
        feeder_path = Path(path_text).expanduser() if path_text else None
        self.neoden_tab.set_feeder_assignment_path(feeder_path)
        self.feeder_editor_tab.set_feeder_assignment_path(feeder_path)

    def project_local_feeder_assignment_path(self) -> Path | None:
        if self.project_dir is None:
            return None
        return (self.project_dir / "inputs" / DEFAULT_FEEDER_ASSIGNMENT_PATH.name).resolve()

    def ensure_local_feeder_assignment_file(
        self,
        preferred_source: Path | None = None,
        overwrite: bool = False,
    ) -> Path | None:
        target = self.project_local_feeder_assignment_path()
        if target is None:
            return None
        self.ensure_project_dirs()
        candidates: list[Path] = []
        for candidate in (
            preferred_source,
            Path(self.project_state.feeder_assignment_file).expanduser()
            if self.project_state.feeder_assignment_file
            else None,
            DEFAULT_FEEDER_ASSIGNMENT_PATH,
        ):
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == target or not resolved.exists():
                continue
            if resolved not in candidates:
                candidates.append(resolved)

        source = candidates[0] if candidates else None
        if overwrite and source is not None:
            shutil.copy2(source, target)
        elif not target.exists():
            if source is not None:
                shutil.copy2(source, target)
            else:
                write_feeder_assignment_rows(target, [])

        self.project_state.feeder_assignment_file = str(target)
        return target

    def import_feeder_assignment_copy_dialog(self) -> None:
        if self.project_dir is None:
            QtWidgets.QMessageBox.warning(
                self,
                "Feeder Assignment",
                "Create or open a project first. The feeder assignment file is stored inside the project.",
            )
            return
        current_path = self.project_local_feeder_assignment_path()
        start_dir = str(current_path.parent) if current_path is not None else str(REPO_ROOT)
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Import Feeder Assignment CSV Copy",
            start_dir,
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path_str:
            return
        source_path = Path(path_str).resolve()
        target = self.ensure_local_feeder_assignment_file(preferred_source=source_path, overwrite=True)
        if target is None:
            return
        self.project_state.feeder_assignment_file = str(target)
        self.refresh_project_ui()
        self.log_message(f"Imported feeder assignment copy to {target}", "#86efac")

    def suggested_manifest_path(self) -> Path:
        if self.manifest_path is not None:
            return self.manifest_path
        folder_name = self.desired_project_folder_name()
        if folder_name:
            return Path.cwd() / folder_name / DEFAULT_MANIFEST_NAME
        return Path.cwd() / DEFAULT_MANIFEST_NAME

    def desired_project_folder_name(self) -> str:
        return sanitize_folder_name(
            self.project_name_edit.text().strip()
            or self.board_name_edit.text().strip()
            or self.project_state.project_name
            or self.project_state.board_name
        )

    def choose_project_parent_dir(self, title: str) -> Path | None:
        if self.project_dir is not None:
            start_dir = self.project_dir.parent
        elif self.manifest_path is not None:
            start_dir = self.manifest_path.parent.parent
        else:
            start_dir = Path.cwd()
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            title,
            str(start_dir),
        )
        if not selected:
            return None
        return Path(selected).resolve()

    def prepare_project_directory(self, parent_dir: Path) -> bool:
        folder_name = self.desired_project_folder_name()
        project_dir = (parent_dir / folder_name).resolve()
        if project_dir.exists():
            if not project_dir.is_dir():
                QtWidgets.QMessageBox.warning(
                    self,
                    "Invalid Project Directory",
                    f"Project path exists and is not a directory:\n{project_dir}",
                )
                return False
            if any(project_dir.iterdir()):
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Use Existing Folder",
                    f"The project folder already exists and is not empty:\n{project_dir}\n\nUse it anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    return False
        else:
            project_dir.mkdir(parents=True, exist_ok=True)

        self.project_dir = project_dir
        self.manifest_path = project_dir / DEFAULT_MANIFEST_NAME
        return True

    def ensure_project_dirs(self) -> None:
        if self.project_dir is None:
            return
        for dirname in ("generated", "inputs", "machine_feedback"):
            (self.project_dir / dirname).mkdir(parents=True, exist_ok=True)

    def collect_project_state(self) -> ProjectState:
        local_feeder_path = self.project_local_feeder_assignment_path()
        feeder_assignment_file = self.feeder_field.text()
        if not feeder_assignment_file:
            feeder_assignment_file = (
                str(local_feeder_path) if local_feeder_path is not None else str(DEFAULT_FEEDER_ASSIGNMENT_PATH)
            )
        return ProjectState(
            project_name=self.project_name_edit.text().strip(),
            board_name=self.board_name_edit.text().strip(),
            side=self.side_combo.currentText(),
            entry_mode=self.project_state.entry_mode,
            pos_file=self.pos_field.text(),
            gerber_file=self.gerber_field.text(),
            template_file=self.template_field.text() or str(DEFAULT_TEMPLATE_PATH),
            feeder_assignment_file=feeder_assignment_file,
            global_offset_file=self.offset_field.text() or str(DEFAULT_GLOBAL_OFFSET_PATH),
            chip1_x_mm=float(self.chip1_x_spin.value()),
            chip1_y_mm=float(self.chip1_y_spin.value()),
            neoden_project_csv=self.project_state.neoden_project_csv,
            notes=self.notes_edit.toPlainText().strip(),
        )

    def refresh_project_ui(self) -> None:
        state = self.project_state
        self.project_name_edit.setText(state.project_name)
        self.board_name_edit.setText(state.board_name)
        self.pos_field.setText(state.pos_file)
        self.gerber_field.setText(state.gerber_file)
        self.template_field.setText(state.template_file)
        display_feeder_path = state.feeder_assignment_file
        if self.project_dir is None and display_feeder_path == str(DEFAULT_FEEDER_ASSIGNMENT_PATH):
            display_feeder_path = ""
        self.feeder_field.setText(display_feeder_path)
        feeder_path = Path(state.feeder_assignment_file) if state.feeder_assignment_file else None
        self.neoden_tab.set_feeder_assignment_path(feeder_path)
        self.feeder_editor_tab.set_feeder_assignment_path(feeder_path)
        neoden_path = Path(state.neoden_project_csv) if state.neoden_project_csv else None
        self.feeder_editor_tab.set_neoden_project_path(neoden_path)
        self.offset_field.setText(state.global_offset_file)
        self.chip1_x_spin.setValue(state.chip1_x_mm)
        self.chip1_y_spin.setValue(state.chip1_y_mm)
        self.notes_edit.setPlainText(state.notes)
        self.side_value_label.setText(self.side_combo.currentText())
        self.manifest_label.setText(str(self.manifest_path) if self.manifest_path else "-")
        self.output_label.setText(state.neoden_project_csv or "-")
        self.summary_label.setText(self.build_project_summary())

    def build_project_summary(self) -> str:
        parts = []
        if self.project_dir is not None:
            parts.append(f"Project dir: {self.project_dir}")
        if self.project_state.pos_file:
            parts.append(f"POS: {Path(self.project_state.pos_file).name}")
        if self.project_state.neoden_project_csv:
            parts.append(f"NeoDen: {Path(self.project_state.neoden_project_csv).name}")
        if not parts:
            return "No project manifest yet. Create or save a project to persist the job state."
        return " | ".join(parts)

    def select_generic_file(self, field: PathField, title: str, filter_text: str) -> None:
        start_dir = str(Path(field.text()).parent) if field.text() else str(REPO_ROOT)
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, start_dir, filter_text)
        if not path_str:
            return
        field.setText(str(Path(path_str).resolve()))
        self.project_state = self.collect_project_state()
        self.refresh_project_ui()

    def on_side_changed_for_project(self, side: str) -> None:
        self.project_state.side = side
        self.side_value_label.setText(side)

    def open_pos_dialog(self) -> None:
        start_dir = (
            str(Path(self.project_state.pos_file).parent)
            if self.project_state.pos_file
            else str(Path.cwd())
        )
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open KiCad POS File",
            start_dir,
            "KiCad POS files (*.pos);;All files (*.*)",
        )
        if path_str:
            self.set_project_pos(Path(path_str), load_into_viewer=True)

    def open_gerber_dialog(self) -> None:
        start_dir = (
            str(Path(self.project_state.gerber_file).parent)
            if self.project_state.gerber_file
            else str(Path.cwd())
        )
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Gerber File",
            start_dir,
            "Gerber files (*.gbr *.gtl *.gbl *.gto *.gbo *.gm1 *.gm2 *.pho *.art);;All files (*.*)",
        )
        if path_str:
            self.set_project_gerber(Path(path_str), load_into_viewer=True)

    def open_neoden_project_dialog(self) -> None:
        start_dir = (
            str(Path(self.project_state.neoden_project_csv).parent)
            if self.project_state.neoden_project_csv
            else str(Path.cwd())
        )
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open NeoDen Project CSV",
            start_dir,
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path_str:
            return

        csv_path = Path(path_str).resolve()
        if self.manifest_path is None:
            if not self.project_name_edit.text().strip():
                self.project_name_edit.setText(csv_path.stem)
            if not self.board_name_edit.text().strip():
                self.board_name_edit.setText(csv_path.stem)
            parent_dir = self.choose_project_parent_dir("Select Parent Folder For NeoDen-Based Project")
            if parent_dir is None:
                return
            self.project_state = self.collect_project_state()
            if not self.prepare_project_directory(parent_dir):
                return
            self.ensure_local_feeder_assignment_file()

        self.set_neoden_project_csv(csv_path, primary=True)
        self.save_project()

    def set_project_pos(self, path: Path, load_into_viewer: bool) -> None:
        resolved = path.resolve()
        self.project_state.pos_file = str(resolved)
        if not self.project_state.neoden_project_csv:
            self.project_state.entry_mode = "pos"
        if not self.project_state.board_name:
            self.project_state.board_name = resolved.stem
        if not self.project_state.project_name:
            self.project_state.project_name = resolved.stem
        if load_into_viewer:
            super().load_pos_path(resolved)
        self.refresh_project_ui()

    def set_project_gerber(self, path: Path, load_into_viewer: bool) -> None:
        resolved = path.resolve()
        self.project_state.gerber_file = str(resolved)
        if load_into_viewer:
            super().load_gerber_path(resolved)
        self.refresh_project_ui()

    def set_neoden_project_csv(self, path: Path, primary: bool) -> None:
        resolved = path.resolve()
        self.project_state.neoden_project_csv = str(resolved)
        self.project_state.entry_mode = "neoden_project" if primary else self.project_state.entry_mode
        if not self.project_state.board_name:
            self.project_state.board_name = resolved.stem
        if not self.project_state.project_name:
            self.project_state.project_name = resolved.stem
        self.neoden_tab.load_file(resolved)
        self.feeder_editor_tab.set_neoden_project_path(resolved)
        if primary:
            self.main_tabs.setCurrentWidget(self.neoden_tab)
        self.refresh_project_ui()

    def clear_pos_view(self) -> None:
        self.current_pos_path = None
        self.pos_file_lines = []
        self.components = []
        self.selected_indexes.clear()
        self.visible_component_indexes = []
        self.overlap_groups = []
        self.anchor_component = None
        self.path_label.setText("-")
        self.table.setRowCount(0)
        self.selection_label.setText("No component selected.")
        self.log_text.clear()
        self.component_layer.prepareGeometryChange()
        self.component_layer.update()
        self.scene.setSceneRect(QtCore.QRectF(-50.0, -50.0, 100.0, 100.0))
        self.scene.update()
        self.restore_status()

    def clear_gerber_view(self) -> None:
        self.current_gerber_path = None
        self.gerber_overlay = None
        self.gerber_pixmap = None
        self.update_gerber_items()
        self.restore_status()

    def refresh_neoden_tab(self) -> None:
        neoden_path = self.project_state.neoden_project_csv
        if neoden_path and Path(neoden_path).exists():
            self.neoden_tab.load_file(Path(neoden_path))
            self.feeder_editor_tab.set_neoden_project_path(Path(neoden_path))
            return
        self.feeder_editor_tab.set_neoden_project_path(None)
        self.neoden_tab.clear()

    def auto_assign_neoden_from_feeder_editor(self) -> None:
        feeder_rows = self.feeder_editor_tab.current_rows
        if not feeder_rows:
            feeder_path = self.project_state.feeder_assignment_file
            if feeder_path and Path(feeder_path).exists():
                feeder_rows = load_feeder_assignment_rows(Path(feeder_path))
        if not feeder_rows:
            QtWidgets.QMessageBox.warning(
                self,
                "NeoDen Project",
                "No feeder configuration is loaded in the feeder editor.",
            )
            return
        self.neoden_tab.auto_assign_from_feeder_rows(feeder_rows)

    def apply_primary_tab_from_entry_mode(self) -> None:
        if (
            self.project_state.entry_mode == "neoden_project"
            and self.project_state.neoden_project_csv
            and Path(self.project_state.neoden_project_csv).exists()
        ):
            self.main_tabs.setCurrentWidget(self.neoden_tab)
            return
        self.main_tabs.setCurrentWidget(self.project_pos_tab)

    def new_project_dialog(self) -> None:
        parent_dir = self.choose_project_parent_dir("Select Parent Folder For New Project")
        if parent_dir is None:
            return
        self.project_state = self.collect_project_state()
        if not self.prepare_project_directory(parent_dir):
            return
        self.project_state = self.collect_project_state()
        if not self.project_state.project_name:
            self.project_state.project_name = self.project_dir.name
        self.ensure_project_dirs()
        self.ensure_local_feeder_assignment_file()
        self.refresh_project_ui()
        self.save_project()

    def open_project_dialog(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Assembly Project",
            str(self.suggested_manifest_path().parent),
            "Assembly Project (*.json);;All files (*.*)",
        )
        if path_str:
            self.load_project(Path(path_str))

    def save_project_as_dialog(self) -> None:
        parent_dir = self.choose_project_parent_dir("Select Parent Folder For Project Copy")
        if parent_dir is None:
            return
        self.project_state = self.collect_project_state()
        if not self.prepare_project_directory(parent_dir):
            return
        self.ensure_project_dirs()
        feeder_source = Path(self.project_state.feeder_assignment_file) if self.project_state.feeder_assignment_file else None
        self.ensure_local_feeder_assignment_file(preferred_source=feeder_source)
        self.save_project()

    def save_project(self) -> bool:
        if self.manifest_path is None:
            self.save_project_as_dialog()
            return self.manifest_path is not None
        self.project_state = self.collect_project_state()
        self.project_dir = self.manifest_path.parent.resolve()
        self.ensure_project_dirs()
        feeder_source = Path(self.project_state.feeder_assignment_file) if self.project_state.feeder_assignment_file else None
        self.ensure_local_feeder_assignment_file(preferred_source=feeder_source)
        payload = self.project_state.to_manifest_dict(self.project_dir)
        self.manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        self.log_message(f"Saved project manifest: {self.manifest_path}", "#86efac")
        self.refresh_project_ui()
        return True

    def load_project(self, manifest_path: Path) -> None:
        resolved = manifest_path.resolve()
        payload = json.loads(resolved.read_text())
        self.project_dir = resolved.parent
        self.manifest_path = resolved
        self.project_state = ProjectState.from_manifest_dict(payload, self.project_dir)
        feeder_source = Path(self.project_state.feeder_assignment_file) if self.project_state.feeder_assignment_file else None
        self.ensure_local_feeder_assignment_file(preferred_source=feeder_source)

        self.side_combo.blockSignals(True)
        self.side_combo.setCurrentText(self.project_state.side)
        self.side_combo.blockSignals(False)
        self.side_filter = self.project_state.side

        self.refresh_project_ui()
        self.log_message(f"Opened project manifest: {self.manifest_path}", "#dbe4f0", clear=True)

        if self.project_state.pos_file and Path(self.project_state.pos_file).exists():
            super().load_pos_path(Path(self.project_state.pos_file))
        else:
            self.clear_pos_view()
        if self.project_state.gerber_file and Path(self.project_state.gerber_file).exists():
            super().load_gerber_path(Path(self.project_state.gerber_file))
        else:
            self.clear_gerber_view()

        self.refresh_neoden_tab()
        self.refresh_project_ui()
        self.apply_primary_tab_from_entry_mode()

    def default_generated_output_path(self) -> Path:
        if self.project_dir is not None:
            generated_dir = self.project_dir / "generated"
            generated_dir.mkdir(parents=True, exist_ok=True)
            if self.project_state.pos_file:
                return generated_dir / f"{Path(self.project_state.pos_file).stem}_neoden_project.csv"
            return generated_dir / "generated_neoden_project.csv"
        if self.project_state.pos_file:
            pos_path = Path(self.project_state.pos_file)
            return pos_path.with_name(f"{pos_path.stem}_neoden_project.csv")
        return Path.cwd() / "generated_neoden_project.csv"

    def generate_project_csv(self) -> None:
        self.project_state = self.collect_project_state()
        pos_path = Path(self.project_state.pos_file) if self.project_state.pos_file else None
        if pos_path is None or not pos_path.exists():
            QtWidgets.QMessageBox.warning(self, "Missing POS File", "Select a KiCad .pos file first.")
            return

        template_path = Path(self.project_state.template_file)
        feeder_path = Path(self.project_state.feeder_assignment_file)
        offset_path = Path(self.project_state.global_offset_file)

        if not template_path.exists():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing Template",
                f"Template project not found:\n{template_path}",
            )
            return

        try:
            pos_lines = converter_mod.parse_pos_file(str(pos_path))
            offset_x, offset_y = converter_mod.compute_offsets(
                pos_lines,
                self.project_state.chip1_x_mm,
                self.project_state.chip1_y_mm,
            )
            converter_mod.apply_offsets(pos_lines, offset_x, offset_y)

            header_lines, comp_lines = converter_mod.read_template(str(template_path))
            maps = converter_mod.build_feeder_maps(comp_lines)
            defaults = ("1", "1", "No")

            if feeder_path.exists():
                csv_by_fp_val, csv_by_fp, stack_rows = converter_mod.load_feeder_assignment_csv(
                    str(feeder_path)
                )
            else:
                csv_by_fp_val, csv_by_fp, stack_rows = ({}, {}, [])

            header_lines = converter_mod.apply_feeder_csv_to_header(header_lines, stack_rows)
            header_lines = converter_mod.update_mirror_create(
                header_lines,
                self.project_state.chip1_x_mm,
                self.project_state.chip1_y_mm,
            )
            header_lines = converter_mod.update_mirror(
                header_lines,
                self.project_state.chip1_x_mm,
                self.project_state.chip1_y_mm,
            )

            if offset_path.exists():
                global_offset = converter_mod.load_global_offset(str(offset_path))
            else:
                global_offset = {"dx": 0.0, "dy": 0.0, "drot": 0.0}

            neoden_project, missing, coord_map = converter_mod.process_pos_lines(
                pos_lines,
                header_lines,
                maps,
                defaults,
                (csv_by_fp_val, csv_by_fp),
                self.side_combo.currentText(),
                global_offset,
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Generation Error", str(exc))
            return

        output_path = self.default_generated_output_path().resolve()
        output_path.write_text(neoden_project)
        self.project_state.neoden_project_csv = str(output_path)
        self.output_label.setText(str(output_path))
        self.log_message(
            f"Generated NeoDen project CSV: {output_path}",
            "#86efac",
        )
        self.log_message(
            "Applied offsets: chip anchor dX {:.4f} mm, dY {:.4f} mm | global dX {:.4f} dY {:.4f} dRot {:.2f}".format(
                offset_x,
                offset_y,
                float(global_offset.get("dx", 0.0)),
                float(global_offset.get("dy", 0.0)),
                float(global_offset.get("drot", 0.0)),
            ),
            "#94a3b8",
        )

        if missing:
            self.log_message(
                f"WARNING: {len(missing)} component(s) used the default feeder assignment.",
                "#fca5a5",
            )
            for name, value, footprint in missing[:25]:
                self.log_message(
                    f"DEFAULT FEEDER -> {name} {value} {footprint}",
                    "#fca5a5",
                )
            if len(missing) > 25:
                self.log_message("Additional missing feeder matches truncated in log.", "#fca5a5")
        else:
            self.log_message("All components matched feeder assignments.", "#86efac")

        duplicates = {key: items for key, items in coord_map.items() if len(items) > 1}
        if duplicates:
            self.log_message(
                f"WARNING: {len(duplicates)} duplicate coordinate group(s) generated.",
                "#fca5a5",
            )
        else:
            self.log_message("No duplicate coordinates generated.", "#86efac")

        self.refresh_neoden_tab()
        self.refresh_project_ui()
        self.save_project()

    def open_generated_dir(self) -> None:
        output = self.project_state.neoden_project_csv
        if output:
            target = Path(output).resolve().parent
        elif self.project_dir is not None:
            target = (self.project_dir / "generated").resolve()
        else:
            target = Path.cwd()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(target)))


def run_smoke_test(
    window: AssemblyProjectWindow,
    sample_pos: Path | None,
    sample_neoden: Path | None,
) -> int:
    if window.manifest_path is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="assembly_project_gui_"))
        window.project_dir = tmp_dir
        window.manifest_path = tmp_dir / DEFAULT_MANIFEST_NAME
        if sample_pos is not None:
            window.set_project_pos(sample_pos, load_into_viewer=True)
        if sample_neoden is not None:
            window.set_neoden_project_csv(sample_neoden, primary=True)
        window.project_state.project_name = window.project_state.project_name or tmp_dir.name
        window.ensure_project_dirs()
        window.save_project()

    if sample_pos is not None and not window.project_state.neoden_project_csv:
        window.generate_project_csv()

    QtWidgets.QApplication.processEvents()

    print(f"manifest={window.manifest_path}")
    print(f"project_name={window.project_state.project_name}")
    print(f"components={len(window.components)}")
    print(f"neoden={window.project_state.neoden_project_csv}")
    print(f"active_tab={window.main_tabs.tabText(window.main_tabs.currentIndex())}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Project-based assembly GUI prototype.")
    parser.add_argument("--project", help="Assembly project manifest to open")
    parser.add_argument("--pos", help="Optional KiCad POS file to preload")
    parser.add_argument("--gerber", help="Optional Gerber file to preload")
    parser.add_argument("--neoden-project", help="Optional NeoDen project CSV to preload")
    parser.add_argument(
        "--side",
        choices=["all", "top", "bottom"],
        default="all",
        help="Initial side filter",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Create/load a project, optionally generate output, print a summary, then exit",
    )
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    window = AssemblyProjectWindow(
        project_manifest=Path(args.project).resolve() if args.project else None,
        pos_path=Path(args.pos).resolve() if args.pos else None,
        gerber_path=Path(args.gerber).resolve() if args.gerber else None,
        side=args.side,
    )
    if args.neoden_project:
        window.set_neoden_project_csv(Path(args.neoden_project).resolve(), primary=True)

    if args.smoke_test:
        raise SystemExit(
            run_smoke_test(
                window,
                Path(args.pos).resolve() if args.pos else None,
                Path(args.neoden_project).resolve() if args.neoden_project else None,
            )
        )

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
