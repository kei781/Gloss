"""Physical-pixel coordinates shared by capture and overlay frontends."""

from __future__ import annotations

import ctypes
import sys

from gloss.log import log
from gloss.overlay.tk_overlay import OverlayGeometry


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        log("could not enable DPI awareness", level="WARN")


def default_overlay_geometry() -> OverlayGeometry:
    if sys.platform != "win32":
        return OverlayGeometry(x=80, y=720, width=1000, height=180)
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    band_width = min(1200, max(400, width - 160))
    band_height = 200
    return OverlayGeometry(
        x=(width - band_width) // 2,
        y=height - band_height - 80,
        width=band_width,
        height=band_height,
    )
