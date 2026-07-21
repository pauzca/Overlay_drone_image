"""QGIS plugin entry point.

This module is loaded by QGIS when the plugin is installed.
"""


def classFactory(iface):  # noqa: N802  (QGIS naming convention)
    """Instantiate the plugin object.

    Called by QGIS when the plugin is loaded.

    Parameters
    ----------
    iface : QgisInterface
        Reference to the QGIS application interface.
    """
    from .plugin import PhaseOneImageAlignPlugin
    return PhaseOneImageAlignPlugin(iface)
