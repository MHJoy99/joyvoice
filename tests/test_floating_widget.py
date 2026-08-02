from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

# Initialize QApplication instance if not present
app = QApplication.instance() or QApplication(sys.argv)

from app.ui.floating_widget import FloatingWidget


class TestFloatingWidgetToast(unittest.TestCase):
    """Regression test for FloatingWidget toast attributes."""

    def test_show_toast_attributes(self):
        widget = FloatingWidget()
        created_toast: QWidget | None = None

        real_init = QWidget.__init__

        def mock_init(self, *args, **kwargs):
            nonlocal created_toast
            real_init(self, *args, **kwargs)
            if self is not widget and type(self) is QWidget:
                created_toast = self

        with patch.object(QWidget, "__init__", mock_init):
            widget.show_toast("Test notification message")

        self.assertIsNotNone(created_toast, "Toast QWidget should have been created")
        assert created_toast is not None

        self.assertTrue(
            created_toast.testAttribute(Qt.WA_ShowWithoutActivating),
            "Toast must have Qt.WA_ShowWithoutActivating set",
        )
        self.assertTrue(
            created_toast.testAttribute(Qt.WA_TransparentForMouseEvents),
            "Toast must have Qt.WA_TransparentForMouseEvents set",
        )

        widget.close()


if __name__ == "__main__":
    unittest.main()
