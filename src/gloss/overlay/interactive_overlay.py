"""Interactive subtitle overlay: a borderless, always-on-top box the user can
move (drag the body), resize (drag the edges), fade (opacity slider) and lock.

Locking hides the controls and turns the box click-through so the app behind it
stays usable; because a click-through window can't receive the unlock click, the
host (gloss-hover) toggles the lock via a global hotkey.

Tk must own the main thread on Windows, so `run()` runs the Tk mainloop while a
daemon `worker` pushes translated text through the thread-safe `show()`/`close()`
methods. `toggle_lock()` is also thread-safe (marshalled onto the Tk thread).
"""

from __future__ import annotations

import ctypes
import queue
import threading
from typing import Callable

from gloss.log import log
from gloss.overlay.tk_overlay import OverlayError, OverlayGeometry


_EDGE = 12            # px from border counted as a resize zone
_MIN_W = 240
_MIN_H = 90
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_LWA_ALPHA = 0x00000002


class InteractiveOverlay:
    _CLOSE = object()

    def __init__(
        self,
        *,
        geometry: OverlayGeometry,
        opacity: float = 0.88,
        poll_ms: int = 80,
        initial_text: str = "Gloss",
        unlock_hint: str = "Ctrl+Alt+L",
    ):
        self.geometry = geometry
        self.opacity = max(0.2, min(opacity, 1.0))
        self.poll_ms = max(10, poll_ms)
        self.initial_text = initial_text
        self.unlock_hint = unlock_hint
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._locked = False
        self._mode: str | None = None
        self._sx = 0
        self._sy = 0
        self._geo0 = (0, 0, 0, 0)

    # --- thread-safe API ---
    def show(self, text: str) -> None:
        self._queue.put(text)

    def close(self) -> None:
        self._queue.put(self._CLOSE)

    def toggle_lock(self) -> None:
        root = getattr(self, "_root", None)
        if root is not None:
            root.after(0, self._toggle_lock)

    # --- main thread ---
    def run(self, worker: Callable[[], None]) -> None:
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise OverlayError(str(exc)) from exc
        self._root = root
        g = self.geometry
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", self.opacity)
        root.configure(bg="#0f0f0f")
        root.geometry(f"{g.width}x{g.height}+{g.x}+{g.y}")

        # Top control strip (own row, so it never overlaps the subtitle text).
        self._bar = tk.Frame(root, bg="#1c1c1c", height=30)
        self._bar.pack(side="top", fill="x")
        self._bar.pack_propagate(False)
        self._lock_btn = tk.Button(
            self._bar,
            text="🔒",
            font=("Segoe UI Emoji", 11),
            bg="#2a2a2a", fg="#f5f5f5",
            activebackground="#3a3a3a", activeforeground="#ffffff",
            relief="flat", bd=0, padx=8, pady=0,
            command=self._toggle_lock,
        )
        self._lock_btn.pack(side="right", padx=(4, 6), pady=3)
        self._slider = tk.Scale(
            self._bar,
            from_=20, to=100, orient="horizontal", length=130, showvalue=False,
            bg="#1c1c1c", fg="#cfcfcf", troughcolor="#3a3a3a",
            highlightthickness=0, bd=0, sliderlength=18, width=12,
            command=self._on_opacity,
        )
        self._slider.set(int(self.opacity * 100))
        self._slider.pack(side="right", padx=(0, 6), pady=3)

        # Subtitle text fills the rest, below the strip.
        self._label = tk.Label(
            root,
            text=self.initial_text,
            bg="#0f0f0f", fg="#f5f5f5",
            padx=18, pady=14,
            font=("Malgun Gothic", 18),
            justify="left", anchor="nw",
            wraplength=max(g.width - 36, 80),
        )
        self._label.pack(side="top", fill="both", expand=True)

        # Move/resize bindings live only on the body (root + label), never the
        # control strip, so dragging the slider can't move the window.
        for w in (root, self._label):
            w.bind("<Motion>", self._on_hover)
            w.bind("<Button-1>", self._on_press)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_release)

        closed = threading.Event()

        def poll() -> None:
            try:
                while True:
                    item = self._queue.get_nowait()
                    if item is self._CLOSE:
                        closed.set()
                        root.destroy()
                        return
                    self._label.config(text=str(item))
            except queue.Empty:
                pass
            finally:
                if not closed.is_set():
                    root.after(self.poll_ms, poll)

        thread = threading.Thread(target=worker, daemon=True)
        root.after(self.poll_ms, poll)
        thread.start()
        log(
            "interactive overlay started",
            x=g.x, y=g.y, width=g.width, height=g.height, opacity=self.opacity,
        )
        try:
            root.mainloop()
        except KeyboardInterrupt:
            if not closed.is_set():
                root.destroy()
            raise

    # --- controls ---
    def _on_opacity(self, value: str) -> None:
        if self._locked:
            return
        try:
            self.opacity = max(0.2, min(int(float(value)) / 100.0, 1.0))
            self._root.attributes("-alpha", self.opacity)
        except (ValueError, AttributeError):
            pass

    def _toggle_lock(self) -> None:
        self._locked = not self._locked
        if self._locked:
            self._bar.pack_forget()              # hide controls
            self._set_click_through(True)        # clicks pass to app behind
            self._root.config(cursor="arrow")
            log("overlay locked", click_through=True, unlock=self.unlock_hint)
        else:
            self._set_click_through(False)
            self._bar.pack(side="top", fill="x", before=self._label)
            self._lock_btn.config(text="🔒")
            log("overlay unlocked")

    # --- click-through (Win32) ---
    def _set_click_through(self, enabled: bool) -> None:
        try:
            hwnd = int(self._root.winfo_id())
            u = ctypes.windll.user32
            get_l = u.GetWindowLongPtrW
            set_l = u.SetWindowLongPtrW
            get_l.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_l.restype = ctypes.c_void_p
            set_l.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            set_l.restype = ctypes.c_void_p
            cur = int(get_l(hwnd, _GWL_EXSTYLE))
            if enabled:
                cur |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            else:
                cur &= ~_WS_EX_TRANSPARENT
            set_l(hwnd, _GWL_EXSTYLE, cur)
            # Re-assert layered alpha so the (now layered) window keeps painting.
            set_la = u.SetLayeredWindowAttributes
            set_la.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_uint]
            set_la.restype = ctypes.c_int
            set_la(hwnd, 0, int(self.opacity * 255), _LWA_ALPHA)
        except (AttributeError, OSError) as exc:
            log("click-through toggle failed", level="WARN", error=str(exc))

    # --- move / resize ---
    def _zone(self, event) -> str:
        gx, gy = self._root.winfo_x(), self._root.winfo_y()
        gw, gh = self._root.winfo_width(), self._root.winfo_height()
        rx, ry = event.x_root - gx, event.y_root - gy
        left, right = rx < _EDGE, rx > gw - _EDGE
        top, bottom = ry < _EDGE, ry > gh - _EDGE
        if right and bottom:
            return "se"
        if left and bottom:
            return "sw"
        if right and top:
            return "ne"
        if left and top:
            return "nw"
        if right:
            return "e"
        if left:
            return "w"
        if bottom:
            return "s"
        if top:
            return "n"
        return "move"

    _CURSORS = {
        "se": "size_nw_se", "nw": "size_nw_se",
        "ne": "size_ne_sw", "sw": "size_ne_sw",
        "e": "sb_h_double_arrow", "w": "sb_h_double_arrow",
        "n": "sb_v_double_arrow", "s": "sb_v_double_arrow",
        "move": "fleur",
    }

    def _on_hover(self, event) -> None:
        if self._locked:
            return
        cursor = self._CURSORS.get(self._zone(event), "fleur")
        try:
            self._root.config(cursor=cursor)
        except Exception:
            self._root.config(cursor="fleur")

    def _on_press(self, event) -> None:
        if self._locked:
            return
        self._mode = self._zone(event)
        self._sx, self._sy = event.x_root, event.y_root
        self._geo0 = (
            self._root.winfo_x(), self._root.winfo_y(),
            self._root.winfo_width(), self._root.winfo_height(),
        )

    def _on_drag(self, event) -> None:
        if self._locked or self._mode is None:
            return
        dx = event.x_root - self._sx
        dy = event.y_root - self._sy
        x0, y0, w0, h0 = self._geo0
        x, y, w, h = x0, y0, w0, h0
        m = self._mode
        if m == "move":
            x, y = x0 + dx, y0 + dy
        else:
            if "e" in m:
                w = max(_MIN_W, w0 + dx)
            if "s" in m:
                h = max(_MIN_H, h0 + dy)
            if "w" in m:  # keep right edge fixed
                w = max(_MIN_W, w0 - dx)
                x = x0 + (w0 - w)
            if "n" in m:  # keep bottom edge fixed
                h = max(_MIN_H, h0 - dy)
                y = y0 + (h0 - h)
        self._root.geometry(f"{int(w)}x{int(h)}+{int(x)}+{int(y)}")
        self._label.config(wraplength=max(int(w) - 36, 80))

    def _on_release(self, event) -> None:
        self._mode = None
