"""Alignment dialog for the PhaseOne Image Align plugin.

Provides:
- QgsMapLayerComboBox for reference orthomosaic and DSM.
- CSV file browser for the Phase One coordinates file.
- Folder browser for the Phase One image directory.
- Map-canvas point picker that captures target_x / target_y.
- Run button that executes the full pipeline and adds the result as a virtual
  raster layer.
- Progress bar and status label for feedback.
"""

from __future__ import annotations

import logging
import os
import tempfile
import traceback

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
)
from qgis.gui import QgsMapLayerComboBox, QgsMapToolEmitPoint
from qgis.core import QgsMapLayerProxyModel
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

log = logging.getLogger(__name__)

# ── Worker thread ─────────────────────────────────────────────────────────────


class AlignWorker(QThread):
    """Run the pipeline in a background thread to keep the UI responsive."""

    finished = pyqtSignal(str)          # emits output_path on success
    failed = pyqtSignal(str)            # emits error message on failure
    progress = pyqtSignal(str)          # emits a status string

    def __init__(
        self,
        ortho_path: str,
        dsm_path: str,
        csv_file: str,
        image_folder: str,
        target_x: float,
        target_y: float,
        epsg: str,
        output_path: str,
    ):
        super().__init__()
        self.ortho_path = ortho_path
        self.dsm_path = dsm_path
        self.csv_file = csv_file
        self.image_folder = image_folder
        self.target_x = target_x
        self.target_y = target_y
        self.epsg = epsg
        self.output_path = output_path

    def run(self):
        """Execute the three-step pipeline."""
        try:
            # Import here to avoid polluting QGIS at plugin load time.
            from .phaseone_image.find_phaseone import find_best_phaseone_image
            from .phaseone_image.orthorectify import orthorectify_image
            from .phaseone_image.align_aux import align_phaseone_to_ortho

            tmp_dir = tempfile.mkdtemp(prefix="phaseone_align_")

            # Step 1 – find the best image
            self.progress.emit("Step 1/3 – Finding best Phase One image …")
            img_path = find_best_phaseone_image(
                csv_file=self.csv_file,
                image_folder=self.image_folder,
                output_folder=tmp_dir,
                target_x=self.target_x,
                target_y=self.target_y,
                epsg=self.epsg,
            )
            
            img_name = os.path.splitext(os.path.basename(str(img_path)))[0]
            print(f"Phaseone image {img_name}")

            # Step 2 – orthorectify
            self.progress.emit("Step 2/3 – Orthorectifying image …")

            geotiff_path = self.output_path.replace(".tif", "_temp.tif") #os.path.join(tmp_dir, img_name + ".tif")
            orthorectify_image(
                image_path=img_path,
                dsm_path=self.dsm_path,
                geotiff_path=geotiff_path,
            )

            # Step 3 – align
            self.progress.emit("Step 3/3 – Aligning to orthomosaic …")
            align_phaseone_to_ortho(
                phaseone_path=geotiff_path,
                ortho_path=self.ortho_path,
                output_path=self.output_path,
                crop_size=25,
            )

            self.finished.emit(self.output_path)

        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())


# ── Dialog ────────────────────────────────────────────────────────────────────


class PhaseOneAlignDialog(QDialog):
    """Main user interface for the PhaseOne Image Align plugin."""

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._target_x: float | None = None
        self._target_y: float | None = None
        self._point_tool: QgsMapToolEmitPoint | None = None
        self._previous_tool = None
        self._worker: AlignWorker | None = None

        self.setWindowTitle("PhaseOne Image Align")
        self.setMinimumWidth(520)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── Input layers ──────────────────────────────────────────────
        layer_group = QGroupBox("Reference Layers")
        layer_form = QFormLayout(layer_group)

        self._ortho_combo = QgsMapLayerComboBox()
        self._ortho_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        layer_form.addRow("Reference Orthomosaic:", self._ortho_combo)

        self._dsm_combo = QgsMapLayerComboBox()
        self._dsm_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        layer_form.addRow("Reference DSM:", self._dsm_combo)

        root.addWidget(layer_group)

        # ── CSV file ──────────────────────────────────────────────
        csv_group = QGroupBox("Phase One GPS Coordinates CSV")
        csv_outer = QVBoxLayout(csv_group)
        csv_note = QLabel(
            "Auto-detected as <image_folder>/phaseone_coordinates.csv. "
            "Override by browsing to a different file."
        )
        csv_note.setWordWrap(True)
        csv_note.setStyleSheet("color: grey; font-size: 10px;")
        csv_outer.addWidget(csv_note)
        csv_layout = QHBoxLayout()
        self._csv_edit = QLineEdit()
        self._csv_edit.setPlaceholderText("Auto-filled when image folder is selected …")
        csv_browse = QPushButton("Browse …")
        csv_browse.clicked.connect(self._browse_csv)
        #csv_layout.addWidget(self._csv_edit)
        #csv_layout.addWidget(csv_browse)
        #csv_outer.addLayout(csv_layout)
        #root.addWidget(csv_group)

        # ── Image folder ──────────────────────────────────────────────
        folder_group = QGroupBox("Phase One Image Folder")
        folder_layout = QHBoxLayout(folder_group)
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Folder containing Phase One JPEGs …")
        folder_browse = QPushButton("Browse …")
        folder_browse.clicked.connect(self._browse_folder)
        #folder_layout.addWidget(self._folder_edit)
        #folder_layout.addWidget(folder_browse)
        root.addWidget(folder_group)

        # ── Target point ──────────────────────────────────────────────
        point_group = QGroupBox("Target Point (map CRS)")
        point_layout = QHBoxLayout(point_group)

        self._x_edit = QLineEdit()
        self._x_edit.setPlaceholderText("X (easting)")
        self._x_edit.setFixedWidth(130)
        self._y_edit = QLineEdit()
        self._y_edit.setPlaceholderText("Y (northing)")
        self._y_edit.setFixedWidth(130)

        self._pick_btn = QPushButton("Pick point on map")
        self._pick_btn.setCheckable(True)
        self._pick_btn.toggled.connect(self._toggle_point_tool)

        point_layout.addWidget(QLabel("X:"))
        point_layout.addWidget(self._x_edit)
        point_layout.addWidget(QLabel("Y:"))
        point_layout.addWidget(self._y_edit)
        point_layout.addStretch()
        point_layout.addWidget(self._pick_btn)
        root.addWidget(point_group)

        # ── Output ────────────────────────────────────────────────────
        out_group = QGroupBox("Output")
        out_layout = QHBoxLayout(out_group)
        self._out_edit = QLineEdit()
        self._out_edit.setPlaceholderText("Output aligned GeoTIFF path …")
        out_browse = QPushButton("Browse …")
        out_browse.clicked.connect(self._browse_output)
        #out_layout.addWidget(self._out_edit)
        #out_layout.addWidget(out_browse)
        #root.addWidget(out_group)

        # ── Progress ──────────────────────────────────────────────────
        self._status_label = QLabel("Ready.")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Buttons ───────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self._run_btn = btn_box.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self._run_btn.clicked.connect(self._run)
        close_btn = btn_box.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        close_btn.clicked.connect(self.close)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Phase One GPS CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if path:
            self._csv_edit.setText(path)

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Phase One image folder")
        if path:
            self._folder_edit.setText(path)
            # Auto-populate the CSV path if it hasn't been set manually
            auto_csv = os.path.join(path, "phaseone_coordinates.csv")
            if not self._csv_edit.text().strip():
                self._csv_edit.setText(auto_csv)
            elif self._csv_edit.text().strip() == os.path.join(
                os.path.dirname(self._folder_edit.text()), "phaseone_coordinates.csv"
            ):
                # Update if it was previously auto-filled from a different folder
                self._csv_edit.setText(auto_csv)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save aligned GeoTIFF", "", "GeoTIFF (*.tif *.tiff);;All files (*)"
        )
        if path:
            if not path.lower().endswith((".tif", ".tiff")):
                path += ".tif"
            self._out_edit.setText(path)

    # ------------------------------------------------------------------
    # Point picker
    # ------------------------------------------------------------------

    def _toggle_point_tool(self, checked: bool):
        canvas = self.iface.mapCanvas()
        if checked:
            self._previous_tool = canvas.mapTool()
            self._point_tool = QgsMapToolEmitPoint(canvas)
            self._point_tool.canvasClicked.connect(self._on_canvas_clicked)
            canvas.setMapTool(self._point_tool)
            self._pick_btn.setText("Click on the map …")
            self.showMinimized()
        else:
            if self._previous_tool is not None:
                canvas.setMapTool(self._previous_tool)
            self._pick_btn.setText("Pick point on map")

    def _on_canvas_clicked(self, point, button):
        """Store the clicked map coordinate as the target point."""
        self._target_x = point.x()
        self._target_y = point.y()
        self._x_edit.setText(f"{self._target_x:.4f}")
        self._y_edit.setText(f"{self._target_y:.4f}")

        # Deactivate the tool and restore dialog
        self._pick_btn.setChecked(False)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> bool:
        """Check that all required inputs are filled; show a message if not."""
        errors = []

        if self._ortho_combo.currentLayer() is None:
            errors.append("• Select a Reference Orthomosaic layer.")
        if self._dsm_combo.currentLayer() is None:
            errors.append("• Select a Reference DSM layer.")
        #if not self._csv_edit.text().strip():
        #    errors.append("• Provide the Phase One GPS coordinates CSV file.")
        #if not self._folder_edit.text().strip():
        #    errors.append("• Provide the Phase One image folder.")
        if not self._x_edit.text().strip() or not self._y_edit.text().strip():
            errors.append("• Pick a target point on the map (or enter X/Y manually).")
        else:
            try:
                float(self._x_edit.text())
                float(self._y_edit.text())
            except ValueError:
                errors.append("• X and Y coordinates must be numeric.")
        #if not self._out_edit.text().strip():
        #    errors.append("• Specify an output path for the aligned GeoTIFF.")

        if errors:
            QMessageBox.warning(
                self,
                "Missing inputs",
                "Please fix the following before running:\n\n" + "\n".join(errors),
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run(self):
        if not self._validate():
            return

        # Gather EPSG from the DSM layer's CRS
        dsm_layer = self._dsm_combo.currentLayer()
        epsg = dsm_layer.crs().authid()  # e.g. "EPSG:32617"

        params = dict(
            ortho_path=self._ortho_combo.currentLayer().source(),
            dsm_path=dsm_layer.source(),
            csv_file=  r"C:\Users\UzcateguiP\Documents\data_explore\closeup-ortho\src\phaseone_coordinates.csv",#self._csv_edit.text().strip(),
            image_folder= r"F:\geotaggedImages\log_0440_Geotagged\P5 (80mm)", #self._folder_edit.text().strip(),
            target_x=float(self._x_edit.text()),
            target_y=float(self._y_edit.text()),
            epsg=epsg,
            output_path= r"D:\uzcateguipaula\test_geotagg\projected\Aligned\test.tif"#self._out_edit.text().strip(),
        )

        # UI feedback
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status_label.setText("Running pipeline …")

        self._worker = AlignWorker(**params)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, message: str):
        self._status_label.setText(message)

    def _on_finished(self, output_path: str):
        """Load the aligned raster as a virtual layer and notify the user."""
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status_label.setText("Done! Loading virtual layer …")

        layer_name = os.path.splitext(os.path.basename(output_path))[0]
        rl = QgsRasterLayer(output_path, layer_name)

        if rl.isValid():
            QgsProject.instance().addMapLayer(rl)
            self._status_label.setText(f"✓ Layer '{layer_name}' added to the project.")
            QMessageBox.information(
                self,
                "Success",
                f"Aligned image loaded as layer:\n{layer_name}",
            )
        else:
            self._status_label.setText("⚠ Output raster could not be loaded.")
            QMessageBox.warning(
                self,
                "Layer load failed",
                f"The aligned raster was written to:\n{output_path}\n\n"
                "But QGIS could not load it as a raster layer. "
                "Try opening it manually via Layer → Add Layer → Add Raster Layer.",
            )

    def _on_failed(self, error: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status_label.setText("✗ Processing failed — see error details.")
        log.error("PhaseOne align failed:\n%s", error)
        QMessageBox.critical(
            self,
            "Processing Error",
            f"The pipeline encountered an error:\n\n{error[:800]}"
            + ("\n…(truncated)" if len(error) > 800 else ""),
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """Restore the previous map tool when the dialog is closed."""
        if self._pick_btn.isChecked():
            self._pick_btn.setChecked(False)
        if self._worker is not None and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait()
        super().closeEvent(event)
