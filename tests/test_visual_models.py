import unittest

from gloss.overlay.tk_overlay import OverlayGeometry
from gloss.visual.models import Rect


class VisualModelsTest(unittest.TestCase):
    def test_rect_parse(self) -> None:
        rect = Rect.parse("10,20,300,120")

        self.assertEqual(rect.x, 10)
        self.assertEqual(rect.y, 20)
        self.assertEqual(rect.width, 300)
        self.assertEqual(rect.height, 120)

    def test_rect_rejects_non_positive_size(self) -> None:
        with self.assertRaises(ValueError):
            Rect.parse("0,0,0,100")

    def test_overlay_geometry_parse(self) -> None:
        geometry = OverlayGeometry.parse("80,720,1000,180")

        self.assertEqual(geometry.width, 1000)
        self.assertEqual(geometry.height, 180)


if __name__ == "__main__":
    unittest.main()
