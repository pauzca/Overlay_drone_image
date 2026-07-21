"""Main QGIS plugin class for PhaseOne Image Align.

Registers a toolbar button and menu entry that open the alignment dialog.
"""

import os

from qgis.PyQt.QtGui import QAction, QIcon

from .dialog import PhaseOneAlignDialog


class PhaseOneImageAlignPlugin:
    """QGIS plugin that provides the PhaseOne Image Align workflow."""

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

    # ------------------------------------------------------------------
    # QGIS plugin life cycle
    # ------------------------------------------------------------------

    def initGui(self):  # noqa: N802  (QGIS naming convention)
        """Create the menu entry and toolbar button."""
        icon_path = os.path.join(self.plugin_dir, "icons", "plugin.png")
        self._action = QAction(
            QIcon(icon_path),
            "PhaseOne Image Align",
            self.iface.mainWindow(),
        )
        self._action.setToolTip("Align a Phase One image to the reference orthomosaic")
        self._action.triggered.connect(self._open_dialog)

        # Add to Plugins menu and toolbar
        self.iface.addPluginToMenu("PhaseOne Image Align", self._action)
        self.iface.addToolBarIcon(self._action)

    def unload(self):
        """Remove the plugin menu entry and toolbar icon."""
        self.iface.removePluginMenu("PhaseOne Image Align", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_dialog(self):
        """Show the alignment dialog (create it on first call)."""
        if self._dialog is None:
            self._dialog = PhaseOneAlignDialog(self.iface)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()
