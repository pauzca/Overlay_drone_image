"""Alignment dialog for plugin.

Provides:
- QgsMapLayerComboBox for reference orthomosaic and DSM.
- Creates or Browses for the CVS of the coordinates of the images
- Folder browser for the Drone flight image directory.
- Map-canvas point picker that captures target_x / target_y.
- Run button that executes the full pipeline and adds the result as a virtual
  raster layer.
- Progress bar and status label for feedback.

This whole plugin was created with the help of AI, adapted from code that ran on the command console

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
from qgis.gui import QgsMapLayerComboBox, QgsMapToolEmitPoint, QgsProjectionSelectionWidget
from qgis.core import QgsMapLayerProxyModel
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal

from qgis.PyQt.QtWidgets import (
    QComboBox,
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

from .drone_raw_utils.MetadataReader import BaseMetadataReader, DJIMetadataReader, PhaseOneMetadataReader

log = logging.getLogger(__name__)

# Registry matching the QComboBox UI selections
READER_REGISTRY: dict[str, BaseMetadataReader] = {
    "Phase One": PhaseOneMetadataReader(),
    "DJI Mavic 3 Enterprise": DJIMetadataReader(),
}


def get_metadata_reader(drone_model_name: str) -> BaseMetadataReader:
    """Returns the matching metadata reader, defaulting to Phase One if unmapped."""
    return READER_REGISTRY.get(drone_model_name, PhaseOneMetadataReader())


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
        image_folder: str,
        target_x: float,
        target_y: float,
        epsg: str,
        output_path: str,
        drone_model: str = "Phase One",
    ):
        super().__init__()
        self.ortho_path = ortho_path
        self.dsm_path = dsm_path
        self.image_folder = image_folder
        self.target_x = target_x
        self.target_y = target_y
        self.epsg = epsg
        self.output_path = output_path,
        self.drone_model = drone_model

    def run(self):
        """Execute the three-step pipeline."""
        try:
            # Import here to avoid polluting QGIS at plugin load time.
            from .drone_raw_utils.find_drone_image import find_best_raw_drone_image
            from .drone_raw_utils.orthorectify import orthorectify_image
            from .drone_raw_utils.align_aux import align_to_ortho

            tmp_dir = tempfile.mkdtemp(prefix="raw_drone_align_")

            
            # set the reader of the raw images metadata based on the drone/camera model
            reader = get_metadata_reader(self.drone_model)


            # Steep 0 ── find or create the lookup csv file for the raw image coordinates ──────────────────────────────────────────────
            csv_coordinate_path = os.path.join(self.image_folder, "coordinates",
                                              "all_images_center_coordinates.csv")

            if os.path.exists(csv_coordinate_path):
                print("Loading precomputed coordinates")
            else:
                print("Creating coordinates file... The first time this can take a couple of minutes")
                csv_coordinate_path = reader.extract_gps_to_csv(image_folder=self.image_folder, output_csv=csv_coordinate_path)


            # Step 1 – find the best image
            self.progress.emit("Step 1/3 – Finding best Raw Drone image …")
            img_path = find_best_raw_drone_image(
                csv_file=self.csv_file,
                image_folder=self.image_folder,
                output_folder=tmp_dir,
                target_x=self.target_x,
                target_y=self.target_y,
                epsg=self.epsg,
                metadata_reader=reader,
            )
            
            img_name = os.path.splitext(os.path.basename(str(img_path)))[0]
            print(f"Drone image {img_name}")

            # Step 2 – orthoproject
            self.progress.emit("Step 2/3 – Projecting image …")

            geotiff_path = self.output_path.replace(".tif", "_temp.tif") #os.path.join(tmp_dir, img_name + ".tif")
            orthorectify_image(
                image_path=img_path,
                dsm_path=self.dsm_path,
                geotiff_path=geotiff_path,
                metadata_reader=reader
            )

            # Step 3 – align
            self.progress.emit("Step 3/3 – Aligning to reference orthomosaic …")
            align_to_ortho(
                orthorectified_path=geotiff_path,
                ortho_path=self.ortho_path,
                output_path=self.output_path,
                crop_size=25,
            )

            self.finished.emit(self.output_path)

        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())


# ── Dialog ────────────────────────────────────────────────────────────────────


class RawDroneAlignDialog(QDialog):
    """Main user interface for the Raw Drone Image Align plugin."""

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self._target_x: float | None = None
        self._target_y: float | None = None
        self._point_tool: QgsMapToolEmitPoint | None = None
        self._previous_tool = None
        self._worker: AlignWorker | None = None

        self.setWindowTitle("Raw Image Align")
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

        # ── Image folder ──────────────────────────────────────────────
        folder_group = QGroupBox("Raw Drone Image Folder")
        folder_layout = QHBoxLayout(folder_group)
        
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Folder containing Raw Drone Images JPEGs …")
        
        folder_browse = QPushButton("Browse …")
        folder_browse.clicked.connect(self._browse_folder)
        
        folder_layout.addWidget(self._folder_edit)
        folder_layout.addWidget(folder_browse)
        
        root.addWidget(folder_group)

        # ── Drone Model ───────────────────────────────────────────────
        drone_group = QGroupBox("Drone Information")
        drone_layout = QHBoxLayout(drone_group)
        
        drone_label = QLabel("Drone Model/Camera:")
        
        # Option A: Dropdown with common models (editable if custom model is typed)
        self._drone_model_combo = QComboBox()
        self._drone_model_combo.setEditable(True)  # Allows typing custom models
        self._drone_model_combo.addItems([
            "DJI Mavic 3 Enterprise",
            "Trinity",
            "Phase One",
            "Custom / Other"
        ])
        self._drone_model_combo.setPlaceholderText("Select or enter drone model …")
        
        drone_layout.addWidget(drone_label)
        drone_layout.addWidget(self._drone_model_combo)
        
        root.addWidget(drone_group)

        # ── Target point ──────────────────────────────────────────────

        self._crs_widget = QgsProjectionSelectionWidget()
        self._crs_widget.setCrs(QgsProject.instance().crs())
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


    def _browse_folder(self):
            """Handler for browsing and selecting the image folder."""
            folder = QFileDialog.getExistingDirectory(self, "Select Raw Drone Image Folder")
            if folder:
                self._folder_edit.setText(folder)

    def get_drone_model(self) -> str:
        """Helper to retrieve the selected or typed drone model."""
        return self._drone_model_combo.currentText().strip()

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
        if not self._folder_edit.text().strip():
            errors.append("• Provide the image folder.")
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
            image_folder= r"/media/paula/data/geotaggedImages/log_0440_Geotagged/P5 (80mm)", #self._folder_edit.text().strip(),
            target_x=float(self._x_edit.text()),
            target_y=float(self._y_edit.text()),
            epsg=epsg,
            output_path= r"/home/paula/Documentos/overlay_drone/Aligned/test.tif",#self._out_edit.text().strip(),
            drone_model=self.get_drone_model(),
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
        log.error("Orthorectified image align failed:\n%s", error)
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
