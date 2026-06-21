import unittest

from gloss.overlay.interactive_overlay import InteractiveOverlay
from gloss.overlay.tk_overlay import OverlayGeometry


class InteractiveOverlayTest(unittest.TestCase):
    def test_toggle_lock_queues_tk_thread_work(self) -> None:
        class RootThatMustNotBeCalled:
            def after(self, *_args):
                raise AssertionError("toggle_lock must not call Tk from worker thread")

        overlay = InteractiveOverlay(
            geometry=OverlayGeometry(x=0, y=0, width=320, height=120)
        )
        overlay._root = RootThatMustNotBeCalled()

        overlay.toggle_lock()

        self.assertIs(overlay._queue.get_nowait(), overlay._TOGGLE_LOCK)


if __name__ == "__main__":
    unittest.main()
