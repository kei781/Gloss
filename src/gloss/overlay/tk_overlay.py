from __future__ import annotations

from dataclasses import dataclass
import ctypes
import queue
import threading
from typing import TYPE_CHECKING, Callable

from gloss.log import log

if TYPE_CHECKING:
    import tkinter as tk


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
    root, _label = _build_overlay_window(text, geometry=geometry, opacity=opacity)
    root.after(max(1, int(duration_s * 1000)), root.destroy)
    log("overlay shown", x=geometry.x, y=geometry.y, width=geometry.width, height=geometry.height)
    root.mainloop()


class OverlayController:
    """Persistent click-through overlay updated from a worker thread.

    Tk must run on the main thread on Windows, so `run()` owns the Tk
    mainloop while `worker` runs on a daemon thread and pushes updates
    through the thread-safe `show()` / `close()` methods.
    """

    _CLOSE = object()

    def __init__(
        self,
        *,
        geometry: OverlayGeometry,
        opacity: float = 0.88,
        poll_ms: int = 100,
        initial_text: str = "Gloss watch...",
    ):
        self.geometry = geometry
        self.opacity = opacity
        self.poll_ms = max(10, poll_ms)
        self.initial_text = initial_text
        self._queue: "queue.Queue[object]" = queue.Queue()

    def show(self, text: str) -> None:
        self._queue.put(text)

    def close(self) -> None:
        self._queue.put(self._CLOSE)

    def run(self, worker: Callable[[], None]) -> None:
        root, label = _build_overlay_window(
            self.initial_text,
            geometry=self.geometry,
            opacity=self.opacity,
        )
        closed = threading.Event()

        def poll() -> None:
            # Re-arm in a finally: Tk swallows callback exceptions, and a
            # single failure must not stop the queue drain or the close path.
            try:
                while True:
                    item = self._queue.get_nowait()
                    if item is self._CLOSE:
                        closed.set()
                        root.destroy()
                        return
                    label.config(text=str(item))
            except queue.Empty:
                pass
            finally:
                if not closed.is_set():
                    root.after(self.poll_ms, poll)

        thread = threading.Thread(target=worker, daemon=True)
        root.after(self.poll_ms, poll)
        thread.start()
        log(
            "overlay controller started",
            x=self.geometry.x,
            y=self.geometry.y,
            width=self.geometry.width,
            height=self.geometry.height,
        )
        try:
            root.mainloop()
        except KeyboardInterrupt:
            if not closed.is_set():
                root.destroy()
            raise


def _build_overlay_window(
    text: str,
    *,
    geometry: OverlayGeometry,
    opacity: float,
) -> tuple["tk.Tk", "tk.Label"]:
    import tkinter as tk

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
    return root, label


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
