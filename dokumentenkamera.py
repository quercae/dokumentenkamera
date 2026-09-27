import sys
import json
import cv2
import time
from datetime import datetime
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSizePolicy, QComboBox, QSlider
)
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QThread, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QGuiApplication


# ------------------------------------------------------------------ #
#  Einstellungen (~/.config)                                         #
# ------------------------------------------------------------------ #

CONFIG_DIR = Path.home() / ".config" / "dokumentenkamera"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_settings(data: dict):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Fehler beim Speichern der Einstellungen: {e}")


# ------------------------------------------------------------------ #
#  Hilfsfunktion: Kameras erkennen                                     #
# ------------------------------------------------------------------ #

def find_cameras(max_index=16):
    cameras = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                cameras.append(i)
        cap.release()
    return cameras


# ------------------------------------------------------------------ #
#  Kamera-Thread                                                       #
# ------------------------------------------------------------------ #

class CameraThread(QThread):
    frame_ready = Signal(object)

    def __init__(self, cap):
        super().__init__()
        self.cap = cap
        self.running = True

    def run(self):
        while self.running:
            try:
                ret, frame = self.cap.read()
                if ret:
                    self.frame_ready.emit(frame)
                else:
                    self.msleep(10)
            except cv2.error:
                self.msleep(50)

    def stop(self):
        self.running = False
        self.wait()


# ------------------------------------------------------------------ #
#  Kamera-Widget                                                       #
# ------------------------------------------------------------------ #

class CameraWidget(QWidget):
    clipboard_copied = Signal()

    def __init__(self, camera_index=0, main_window=None):
        super().__init__()
        self.camera_index = camera_index
        self.main_window = main_window
        self.cap = cv2.VideoCapture(camera_index, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

        # Stream-Zustand
        self.frozen = False
        self.frozen_frame = None
        self.current_frame = None

        # Zoom
        self.zoom_factor = 1.0
        self.zoom_min = 1.0
        self.zoom_max = 5.0

        # Videoaufnahme
        self.recording = False
        self.video_writer = None
        self.recording_filename = ""
        self.recording_size = (1920, 1080)
        self.recording_fps = 30.0
        self.recording_start_time = 0.0
        self.recording_frames_written = 0

        # Auswahlrechteck
        self.selecting = False
        self.selection_start = QPoint()
        self.selection_end = QPoint()
        self.selection_rect = QRect()

        # Timer zum automatischen Ausblenden des Auswahlrechtecks
        self.selection_clear_timer = QTimer()
        self.selection_clear_timer.setSingleShot(True)
        self.selection_clear_timer.timeout.connect(self.clear_selection)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(640, 480)

        # Kamera-Thread starten
        self.thread = CameraThread(self.cap)
        self.thread.frame_ready.connect(self.on_frame_ready)
        self.thread.start()

    # ------------------------------------------------------------------ #
    #  Frame-Verarbeitung & Aufnahme                                       #
    # ------------------------------------------------------------------ #

    def on_frame_ready(self, frame):
        if not self.frozen:
            self.current_frame = frame
            self.update()

        # Falls Aufnahme läuft, Frame mit Zoomfaktor schreiben
        if self.recording and self.video_writer is not None:
            rec_src = self.frozen_frame if (self.frozen and self.frozen_frame is not None) else frame
            if rec_src is not None:
                h, w = rec_src.shape[:2]
                if self.zoom_factor > 1.0:
                    new_w = int(w / self.zoom_factor)
                    new_h = int(h / self.zoom_factor)
                    zx = (w - new_w) // 2
                    zy = (h - new_h) // 2
                    crop = rec_src[zy:zy + new_h, zx:zx + new_w]
                    rec_frame = cv2.resize(crop, self.recording_size, interpolation=cv2.INTER_LINEAR)
                else:
                    if (w, h) != self.recording_size:
                        rec_frame = cv2.resize(rec_src, self.recording_size, interpolation=cv2.INTER_LINEAR)
                    else:
                        rec_frame = rec_src
                self.video_writer.write(rec_frame)

    def start_recording(self):
        if self.recording:
            return self.recording_filename

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"aufnahme_{timestamp}.mp4"
        self.recording_filename = filename

        if self.current_frame is not None:
            h, w = self.current_frame.shape[:2]
        else:
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080

        self.recording_size = (w, h)
        self.recording_fps = 30.0  # Fester Ziel-FPS-Wert für gleichmäßige Wiedergabe
        self.recording_start_time = time.time()
        self.recording_frames_written = 0

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        self.video_writer = cv2.VideoWriter(filename, fourcc, self.recording_fps, (w, h))
        if not self.video_writer.isOpened():
            filename = f"aufnahme_{timestamp}.avi"
            self.recording_filename = filename
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            self.video_writer = cv2.VideoWriter(filename, fourcc, self.recording_fps, (w, h))

        self.recording = True
        return self.recording_filename

    def on_frame_ready(self, frame):
        if not self.frozen:
            self.current_frame = frame
            self.update()

        # Aufnahme mit Zeitstempel-Synchronisation
        if self.recording and self.video_writer is not None:
            rec_src = self.frozen_frame if (self.frozen and self.frozen_frame is not None) else frame
            if rec_src is not None:
                h, w = rec_src.shape[:2]
                
                # Zoom berücksichtigen
                if self.zoom_factor > 1.0:
                    new_w = int(w / self.zoom_factor)
                    new_h = int(h / self.zoom_factor)
                    zx = (w - new_w) // 2
                    zy = (h - new_h) // 2
                    crop = rec_src[zy:zy + new_h, zx:zx + new_w]
                    rec_frame = cv2.resize(crop, self.recording_size, interpolation=cv2.INTER_LINEAR)
                else:
                    if (w, h) != self.recording_size:
                        rec_frame = cv2.resize(rec_src, self.recording_size, interpolation=cv2.INTER_LINEAR)
                    else:
                        rec_frame = rec_src

                # Ermitteln, wie viele Frames bis zum jetzigen Zeitpunkt existieren müssten
                elapsed = time.time() - self.recording_start_time
                target_frames = max(1, int(elapsed * self.recording_fps))

                # Fehlende Frames durch Wiederholung auffüllen
                while self.recording_frames_written < target_frames:
                    self.video_writer.write(rec_frame)
                    self.recording_frames_written += 1

    def stop_recording(self):
        self.recording = False
        if self.video_writer is not None:
            self.video_writer.release()
            self.video_writer = None

    def get_frame_geometry(self):
        frame = self.frozen_frame if self.frozen else self.current_frame
        if frame is None:
            return None

        h, w = frame.shape[:2]

        zoom_x1, zoom_y1 = 0, 0
        if self.zoom_factor > 1.0:
            new_w = int(w / self.zoom_factor)
            new_h = int(h / self.zoom_factor)
            zoom_x1 = (w - new_w) // 2
            zoom_y1 = (h - new_h) // 2
            w, h = new_w, new_h

        scale = min(self.width() / w, self.height() / h)
        disp_w = int(w * scale)
        disp_h = int(h * scale)
        x_off = (self.width() - disp_w) // 2
        y_off = (self.height() - disp_h) // 2

        return frame, x_off, y_off, scale, zoom_x1, zoom_y1

    def get_display_frame(self):
        geo = self.get_frame_geometry()
        if geo is None:
            return None

        src_frame, x_off, y_off, scale, zoom_x1, zoom_y1 = geo
        h, w = src_frame.shape[:2]

        if self.zoom_factor > 1.0:
            new_w = int(w / self.zoom_factor)
            new_h = int(h / self.zoom_factor)
            src_frame = src_frame[zoom_y1:zoom_y1 + new_h, zoom_x1:zoom_x1 + new_w]
            h, w = src_frame.shape[:2]

        target_w = int(w * scale)
        target_h = int(h * scale)
        return cv2.resize(
            src_frame,
            (target_w, target_h),
            interpolation=cv2.INTER_LINEAR
        )

    # ------------------------------------------------------------------ #
    #  Zeichnen                                                            #
    # ------------------------------------------------------------------ #

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        frame = self.get_display_frame()

        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qi = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qi)
            x_off = (self.width() - pixmap.width()) // 2
            y_off = (self.height() - pixmap.height()) // 2
            painter.drawPixmap(x_off, y_off, pixmap)

        if not self.selection_rect.isNull():
            pen = QPen(QColor(0, 180, 255), 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self.selection_rect)

    # ------------------------------------------------------------------ #
    #  Maus-Events                                                         #
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selection_clear_timer.stop()
            self.selecting = True
            self.selection_start = event.position().toPoint()
            self.selection_end = self.selection_start
            self.selection_rect = QRect()
            self.update()
        elif event.button() == Qt.RightButton:
            if self.main_window:
                self.main_window.toggle_freeze()
        elif event.button() == Qt.MiddleButton:
            if self.main_window:
                self.main_window.trigger_autofocus()

    def mouseMoveEvent(self, event):
        if self.selecting:
            self.selection_end = event.position().toPoint()
            self.selection_rect = QRect(
                self.selection_start, self.selection_end
            ).normalized()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.selecting:
            self.selecting = False
            self.selection_end = event.position().toPoint()
            self.selection_rect = QRect(
                self.selection_start, self.selection_end
            ).normalized()
            self.update()
            self.copy_selection_to_clipboard()
            self.selection_clear_timer.start(500)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.main_window:
            self.main_window.toggle_fullscreen()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.zoom_factor = min(self.zoom_max, self.zoom_factor + 0.2)
        else:
            self.zoom_factor = max(self.zoom_min, self.zoom_factor - 0.2)
        self.zoom_factor = round(self.zoom_factor, 1)
        self.update()

    # ------------------------------------------------------------------ #
    #  Tastatur-Events                                                     #
    # ------------------------------------------------------------------ #

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            if self.main_window:
                self.main_window.toggle_freeze()
        elif event.key() == Qt.Key_F5:
            if self.main_window:
                self.main_window.trigger_autofocus()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.main_window:
                self.main_window.toggle_recording()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    #  Aktionen                                                            #
    # ------------------------------------------------------------------ #

    def clear_selection(self):
        self.selection_rect = QRect()
        self.update()

    def toggle_freeze(self):
        if not self.frozen:
            if self.current_frame is not None:
                self.frozen_frame = self.current_frame.copy()
            self.frozen = True
        else:
            self.frozen = False
            self.frozen_frame = None
            self.selection_rect = QRect()
        self.update()

    def switch_camera(self, index):
        if self.recording:
            self.stop_recording()
            if self.main_window and hasattr(self.main_window, "btn_record"):
                self.main_window.btn_record.setChecked(False)
                self.main_window.btn_record.setText("Aufnahme [Enter]")
                self.main_window.btn_record.setStyleSheet("height: 36px; padding: 0px 8px;")

        self.thread.stop()
        self.cap.release()

        self.camera_index = index
        self.frozen = False
        self.frozen_frame = None
        self.current_frame = None
        self.selection_rect = QRect()

        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        QThread.msleep(300)

        self.thread = CameraThread(self.cap)
        self.thread.frame_ready.connect(self.on_frame_ready)
        self.thread.start()
        self.update()

    def copy_selection_to_clipboard(self):
        if self.selection_rect.isNull():
            return
        geo = self.get_frame_geometry()
        if geo is None:
            return

        src_frame, x_off, y_off, scale, zoom_x1, zoom_y1 = geo

        def to_src(pt):
            x = int((pt.x() - x_off) / scale) + zoom_x1
            y = int((pt.y() - y_off) / scale) + zoom_y1
            return x, y

        x1, y1 = to_src(self.selection_rect.topLeft())
        x2, y2 = to_src(self.selection_rect.bottomRight())

        fh, fw = src_frame.shape[:2]
        x1 = max(0, min(x1, fw - 1))
        y1 = max(0, min(y1, fh - 1))
        x2 = max(0, min(x2, fw))
        y2 = max(0, min(y2, fh))

        if x2 <= x1 or y2 <= y1:
            return

        crop = src_frame[y1:y2, x1:x2]
        rgb  = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qi = QImage(rgb.data.tobytes(), w, h, ch * w, QImage.Format_RGB888)
        QGuiApplication.clipboard().setPixmap(QPixmap.fromImage(qi))
        self.clipboard_copied.emit()

    def closeEvent(self, event):
        if self.recording:
            self.stop_recording()
        self.thread.stop()
        self.cap.release()


# ------------------------------------------------------------------ #
#  Hauptfenster                                                        #
# ------------------------------------------------------------------ #

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dokumentenkamera")
        self.resize(1250, 757)

        self.settings = load_settings()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Verfügbare Kameras ermitteln
        self.available_cameras = find_cameras()
        saved_cam = self.settings.get("camera_index", None)
        if saved_cam in self.available_cameras:
            start_index = saved_cam
        else:
            start_index = self.available_cameras[0] if self.available_cameras else 0

        # Kamera-Widget
        self.camera = CameraWidget(start_index, main_window=self)
        layout.addWidget(self.camera)

        # ---- Button-Leiste ------------------------------------------- #
        BTN_STYLE = "height: 36px; padding: 0px 8px;"

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        btn_layout.setContentsMargins(4, 6, 4, 6)
        layout.addLayout(btn_layout)

        # Kameraauswahl
        self.camera_box = QComboBox()
        self.camera_box.setStyleSheet(BTN_STYLE)
        self.camera_box.setFixedHeight(36)
        for i in self.available_cameras:
            self.camera_box.addItem(f"Kamera {i}")
        if saved_cam in self.available_cameras:
            self.camera_box.setCurrentIndex(self.available_cameras.index(saved_cam))
        self.camera_box.currentIndexChanged.connect(self.change_camera)
        btn_layout.addWidget(self.camera_box)

        # Auflösungsauswahl
        self.resolution_box = QComboBox()
        self.resolution_box.setStyleSheet(BTN_STYLE)
        self.resolution_box.setFixedHeight(36)
        self.resolutions = [
            ("640 × 480",    640,   480),
            ("960 × 540",    960,   540),
            ("1280 × 720",  1280,   720),
            ("1600 × 1200", 1600,  1200),
            ("1920 × 1080", 1920,  1080),
            ("2560 × 1440", 2560,  1440),
            ("2592 × 1944", 2592,  1944),
            ("3264 × 2448", 3264,  2448),
            ("3840 × 2160", 3840,  2160),
            ("3840 × 2880", 3840,  2880),
        ]
        for label, _, __ in self.resolutions:
            self.resolution_box.addItem(label)

        saved_res = self.settings.get("resolution_index", 0)
        if 0 <= saved_res < len(self.resolutions):
            self.resolution_box.setCurrentIndex(saved_res)
            _, w, h = self.resolutions[saved_res]
            self.camera.thread.stop()
            self.camera.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            self.camera.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            self.camera.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            QThread.msleep(300)
            self.camera.thread = CameraThread(self.camera.cap)
            self.camera.thread.frame_ready.connect(self.camera.on_frame_ready)
            self.camera.thread.start()

        self.resolution_box.currentIndexChanged.connect(self.change_resolution)
        btn_layout.addWidget(self.resolution_box)

        # Anhalten
        self.btn_freeze = QPushButton("Anhalten  [Space / Rechtsklick]")
        self.btn_freeze.setStyleSheet(BTN_STYLE)
        self.btn_freeze.setFixedWidth(210)
        self.btn_freeze.clicked.connect(self.toggle_freeze)
        btn_layout.addWidget(self.btn_freeze)

        # Autofokus
        self.btn_focus = QPushButton("Autofokus  [F5 / Mittelklick]")
        self.btn_focus.setStyleSheet(BTN_STYLE)
        self.btn_focus.clicked.connect(self.trigger_autofocus)
        btn_layout.addWidget(self.btn_focus)

        # Aufnahme
        self.btn_record = QPushButton("Aufnahme [Enter]")
        self.btn_record.setCheckable(True)
        self.btn_record.setStyleSheet(BTN_STYLE)
        self.btn_record.clicked.connect(self.toggle_recording)
        btn_layout.addWidget(self.btn_record)

        # Vollbild
        self.btn_fullscreen = QPushButton("Vollbild  [F11 / Doppelklick]")
        self.btn_fullscreen.setStyleSheet(BTN_STYLE)
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        btn_layout.addWidget(self.btn_fullscreen)

        # Flexibler Abstand
        btn_layout.addStretch()

        # Zoom-Wert
        self.zoom_value_label = QLabel("1.0×")
        self.zoom_value_label.setStyleSheet(BTN_STYLE)
        self.zoom_value_label.setFixedWidth(48)
        self.zoom_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        btn_layout.addWidget(self.zoom_value_label)

        # Zoom-Slider
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setFixedWidth(120)
        self.zoom_slider.setMinimum(10)
        self.zoom_slider.setMaximum(50)
        self.zoom_slider.setValue(10)
        self.zoom_slider.setTickInterval(10)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.valueChanged.connect(self.on_zoom_slider)
        btn_layout.addWidget(self.zoom_slider)

        # Status-Timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(200)

    # ------------------------------------------------------------------ #
    #  Aufnahme                                                            #
    # ------------------------------------------------------------------ #

    def toggle_recording(self):
        if not self.camera.recording:
            filename = self.camera.start_recording()
            self.btn_record.setChecked(True)
            self.btn_record.setText(f"Stop [{filename}] [Enter]")
            self.btn_record.setStyleSheet(
                "height: 36px; padding: 0px 8px; color: red; font-weight: bold;")
        else:
            self.camera.stop_recording()
            self.btn_record.setChecked(False)
            self.btn_record.setText("Aufnahme [Enter]")
            self.btn_record.setStyleSheet("height: 36px; padding: 0px 8px;")

    # ------------------------------------------------------------------ #
    #  Vollbild / Freeze / Autofokus                                       #
    # ------------------------------------------------------------------ #

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_fullscreen.setText("Vollbild  [F11 / Doppelklick]")
        else:
            self.showFullScreen()
            self.btn_fullscreen.setText("Vollbild beenden  [F11 / Doppelklick]")

    def toggle_freeze(self):
        self.camera.toggle_freeze()
        if self.camera.frozen:
            self.btn_freeze.setText("Fortsetzen [Space / Rechtsklick]")
        else:
            self.btn_freeze.setText("Anhalten [Space / Rechtsklick]")

    def trigger_autofocus(self):
        index = self.resolution_box.currentIndex()
        self.change_resolution(index)

    # ------------------------------------------------------------------ #
    #  Zoom-Slider                                                         #
    # ------------------------------------------------------------------ #

    def on_zoom_slider(self, value):
        self.camera.zoom_factor = round(value / 10.0, 1)
        self.camera.update()

    # ------------------------------------------------------------------ #
    #  Kamera / Auflösung                                                  #
    # ------------------------------------------------------------------ #

    def change_camera(self, index):
        cam_index = self.available_cameras[index]
        self.camera.switch_camera(cam_index)
        res_index = self.resolution_box.currentIndex()
        _, w, h = self.resolutions[res_index]
        self.camera.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.camera.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.camera.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)

    def change_resolution(self, index):
        _, w, h = self.resolutions[index]
        self.camera.thread.stop()
        self.camera.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        self.camera.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.camera.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        QThread.msleep(300)
        self.camera.thread = CameraThread(self.camera.cap)
        self.camera.thread.frame_ready.connect(self.camera.on_frame_ready)
        self.camera.thread.start()

    # ------------------------------------------------------------------ #
    #  Einstellungen speichern                                            #
    # ------------------------------------------------------------------ #

    def save_current_settings(self):
        if not self.available_cameras:
            return
        cam_idx = self.camera_box.currentIndex()
        if 0 <= cam_idx < len(self.available_cameras):
            data = {
                "camera_index":     self.available_cameras[cam_idx],
                "resolution_index": self.resolution_box.currentIndex(),
            }
            save_settings(data)

    # ------------------------------------------------------------------ #
    #  Status                                                              #
    # ------------------------------------------------------------------ #

    def update_status(self):
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(int(self.camera.zoom_factor * 10))
        self.zoom_slider.blockSignals(False)
        self.zoom_value_label.setText(f"{self.camera.zoom_factor:.1f}×")

    # ------------------------------------------------------------------ #
    #  Tastatur / Schließen                                                #
    # ------------------------------------------------------------------ #

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.toggle_recording()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.save_current_settings()
        if self.camera.recording:
            self.camera.stop_recording()
        self.camera.thread.stop()
        self.camera.cap.release()
        event.accept()


# ------------------------------------------------------------------ #
#  Start                                                               #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())