from __future__ import annotations

from dataclasses import dataclass
import ctypes
import tkinter as tk

from gloss.log import log


@dataclass(frozen=True)
class OverlayGeometry:
    x: int
    y: int
    width: int
    height: int

    @classmethod
    def parse(cls, value: str) -> "OverlayGeometry":
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 4:
            raise ValueError("Overlay rect must be X,Y,WIDTH,HEIGHT.")
        x, y, width, height = (int(part) for part in parts)
        if width <= 0 or height <= 0:
            raise ValueError("Overlay width and height must be greater than zero.")
        return cls(x=x, y=y, width=width, height=height)


class OverlayError(RuntimeError):
    pass


def show_overlay_text(
    text: str,
    *,
    geometry: OverlayGeometry,
    duration_s: float = 6.0,
    opacity: float = 0.88,
) -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise OverlayError(str(exc)) from exc

    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", max(0.1, min(opacity, 1.0)))
    root.configure(bg="#111111")
    root.geometry(f"{geometry.width}x{geometry.height}+{geometry.x}+{geometry.y}")

    label = tk.Label(
        root,
        text=text,
        bg="#111111",
        fg="#f5f5f5",
        padx=20,
        pady=16,
        font=("Malgun Gothic", 18),
        justify="left",
        anchor="nw",
        wraplength=max(geometry.width - 40, 80),
    )
    label.pack(fill="both", expand=True)

    root.update_idletasks()
    _make_click_through(root)
    root.after(max(1, int(duration_s * 1000)), root.destroy)
    log("overlay shown", x=geometry.x, y=geometry.y, width=geometry.width, height=geometry.height)
    root.mainloop()


def _make_click_through(root: tk.Tk) -> None:
    hwnd = int(root.winfo_id())
    user32 = ctypes.windll.user32
    get_window_long = user32.GetWindowLongPtrW
    set_window_long = user32.SetWindowLongPtrW
    get_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    get_window_long.restype = ctypes.c_void_p
    set_window_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    set_window_long.restype = ctypes.c_void_p

    gwl_exstyle = -20
    ws_ex_layered = 0x00080000
    ws_ex_transparent = 0x00000020
    ws_ex_toolwindow = 0x00000080

    current = int(get_window_long(hwnd, gwl_exstyle))
    updated = current | ws_ex_layered | ws_ex_transparent | ws_ex_toolwindow
    set_window_long(hwnd, gwl_exstyle, updated)
