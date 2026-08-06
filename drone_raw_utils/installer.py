"""Automated dependency manager for QGIS plugins."""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def ensure_package(import_name: str, package_name: str | None = None) -> bool:
    """Check if a package is available using its python import name; install via PyPI if missing."""
    pkg_to_install = package_name or import_name

    # 1. Intentar importar la librería (e.g., import PIL)
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        log.info("Module '%s' missing in QGIS Python environment. Attempting install of '%s'...", import_name, pkg_to_install)

    # 2. Intentar instalación por pip con --break-system-packages (compatible con Ubuntu/Debian)
    cmd_pip = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        "--break-system-packages",
        pkg_to_install,
    ]

    try:
        subprocess.run(cmd_pip, capture_output=True, text=True, check=True)
        importlib.invalidate_caches()
        importlib.import_module(import_name)
        return True
    except Exception as err:
        log.error("Failed to auto-install '%s': %s", pkg_to_install, err)

    return False


def ensure_plugin_dependencies(required_packages: list[str | tuple[str, str]]) -> bool:
    """Iterates cleanly over a list of package requirements."""
    success = True
    for pkg in required_packages:
        if isinstance(pkg, tuple):
            import_name, pypi_name = pkg
        else:
            import_name, pypi_name = pkg, pkg

        # No dejamos que la excepción destruya el cargado de QGIS
        try:
            if not ensure_package(import_name, pypi_name):
                success = False
        except Exception as e:
            log.error("Error checking dependency %s: %s", import_name, e)
            success = False

    return success