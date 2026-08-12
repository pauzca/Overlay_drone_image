"""Main QGIS plugin class

Registers a toolbar button and menu entry that open the alignment dialog.
"""

import os

from qgis.PyQt.QtWidgets import QMessageBox

from qgis.PyQt.QtGui import QAction, QIcon

from .drone_raw_utils.installer import ensure_plugin_dependencies

from .dialog import RawDroneAlignDockWidget

from qgis.PyQt.QtCore import Qt


class RawDroneImageAlignPlugin:
    """QGIS plugin that provides the Drone Image Align workflow."""

    def __init__(self, iface):
        """Initialise the plugin.

        Parameters
        ----------
        iface : QgisInterface
            Reference to the QGIS application interface.
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._action = None
        self._dialog = None


        REQUIRED = [
            ("PIL", "Pillow"),
            ("pandas", "pandas"),
            ("skimage", "scikit-image"),
            ("shapely", "Shapely"),
            ("affine", "affine"),
            ("scipy", "scipy"),
            ("pyproj", "pyproj"),
            ("cv2", "opencv-python")
        ]

        # Auto-install missing packages on plugin startup
        installed = ensure_plugin_dependencies(REQUIRED)

        if not installed:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Dependency Warning",
                "Some required Python packages could not be installed automatically. "
                "Check QGIS Log Messages for details.",
            )

    # ------------------------------------------------------------------
    # QGIS plugin life cycle
    # ------------------------------------------------------------------

    def initGui(self):  # noqa: N802  (QGIS naming convention)
        """Create the menu entry and toolbar button."""
        icon_path = os.path.join(self.plugin_dir, "icons", "plugin.png")
        self._action = QAction(
            QIcon(icon_path),
            "Raw Drone Image Align",
            self.iface.mainWindow(),
        )

        self._action.setToolTip("Align a Raw Drone image to the reference orthomosaic")
        self._action.triggered.connect(self._open_dialog)

        # Add to Plugins menu and toolbar
        self.iface.addPluginToMenu("Raw Drone Image Align", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self):
        """Remove the plugin menu entry and toolbar icon."""
        self.iface.removePluginMenu("Raw Drone Image Align", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


    def _open_dialog(self):
        """Show the alignment dock widget."""

        if self._dialog is None:
            self._dialog = RawDroneAlignDockWidget(self.iface)

            # Add the widget to QGIS's right dock area
            self.iface.addDockWidget(
                Qt.DockWidgetArea.RightDockWidgetArea,
                self._dialog,
            )

        self._dialog.show()
        self._dialog.raise_()