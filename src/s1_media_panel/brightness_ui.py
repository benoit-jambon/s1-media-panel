"""Panneau utilisateur et fenêtre temporaire, sans transport matériel."""
import os

from PyQt6.QtCore import QSignalBlocker, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from .brightness import FULL, STEP, change_level, read_level


class Panel(QWidget):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.setWindowTitle("Luminosité LCD")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Luminosité LCD"))
        self.percent = QLabel()
        self.percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.percent.font()
        font.setPointSize(24)
        self.percent.setFont(font)
        layout.addWidget(self.percent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setAccessibleName("Luminosité LCD par pas de 5 %")
        self.slider.setRange(0, FULL // STEP)
        self.slider.setPageStep(1)
        self.slider.setTracking(False)
        self.slider.sliderMoved.connect(lambda step: self.show_level(step * STEP))
        self.slider.valueChanged.connect(lambda step: self.change("set", step * STEP))
        layout.addWidget(self.slider)
        buttons = QHBoxLayout()
        self.down = QPushButton("−5 %")
        self.up = QPushButton("+5 %")
        self.reset = QPushButton("Réinitialiser à 100 %")
        self.down.clicked.connect(lambda: self.change("down"))
        self.up.clicked.connect(lambda: self.change("up"))
        self.reset.clicked.connect(lambda: self.change("set", FULL))
        for button in (self.down, self.up, self.reset):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.write_error = ""
        self.refresh()
        self.timer = QTimer(self, interval=500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def show_level(self, level):
        self.percent.setText(f"{level} %")

    def refresh(self):
        if self.slider.isSliderDown():
            return
        message = self.write_error
        try:
            level = read_level(self.path)
        except (OSError, ValueError) as error:
            self.percent.setText("—")
            message = message or f"{error} Utilisez Réinitialiser pour rétablir 100 %."
        else:
            with QSignalBlocker(self.slider):
                self.slider.setValue(level // STEP)
            self.show_level(level)
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def change(self, action, level=None):
        try:
            change_level(self.path, action, level)
            self.write_error = ""
        except (OSError, ValueError) as error:
            self.write_error = str(error)
        self.refresh()


def run_panel(path):
    if not any(os.environ.get(key) for key in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM")):
        raise OSError("Le panneau nécessite une session graphique.")
    app = QApplication([])
    panel = Panel(path)
    panel.show()
    return app.exec()


class Overlay(QWidget):
    def __init__(self):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                 | Qt.WindowType.WindowTransparentForInput)
        if QApplication.platformName() == "xcb":
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(300)
        self.setStyleSheet("""
            QWidget { background: #101825; color: #eef4ff; }
            QLabel#percent { font-size: 24px; font-weight: bold; }
            QProgressBar { background: #344254; border: 0; max-height: 7px; }
            QProgressBar::chunk { background: #58d6c5; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        title = QLabel("Luminosité LCD")
        self.percent = QLabel(objectName="percent")
        for label in (title, self.percent):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
        self.bar = QProgressBar()
        self.bar.setRange(0, FULL)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)

    def present(self, level):
        self.percent.setText(f"{level} %")
        self.bar.setValue(level)
        self.adjustSize()
        area = QApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.height() - 36)
        self.show()
        self.raise_()
