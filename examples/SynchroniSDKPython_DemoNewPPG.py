import sys
import signal
from typing import List

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import QRunnable, QThreadPool

from sensor import *


SCAN_DEVICE_PERIOD_IN_MS   = 3000
PACKAGE_COUNT              = 32
POWER_REFRESH_PERIOD_IN_MS = 60000
PLOT_UPDATE_INTERVAL       = 50
BUFFER_SECONDS             = 5

matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
matplotlib.rcParams['lines.antialiased'] = False
matplotlib.rcParams['agg.path.chunksize'] = 10000

CHANNEL_LABELS = {
    DataType.NTF_ACC:        ["ACC-X", "ACC-Y", "ACC-Z"],
    DataType.NTF_GYRO:       ["GYRO-X", "GYRO-Y", "GYRO-Z"],
    DataType.NTF_EULER_DATA: ["Pitch(Y)", "Roll(X)", "Yaw(Z)"],
    DataType.NTF_QUATERNION: ["W", "X", "Y", "Z"],
}

DATA_TYPE_NAMES = {
    DataType.NTF_ACC:        "Acceleration (ACC)",
    DataType.NTF_GYRO:       "Gyroscope (GYRO)",
    DataType.NTF_EULER_DATA: "Euler Angle (Euler)",
    DataType.NTF_QUATERNION: "Quaternion (Quaternion)",
}

BIO_PLOT_CONFIG = [
    (DataType.NTF_EEG,  0, "fp1",   plt.cm.tab10(0)),
    (DataType.NTF_EEG,  1, "fp2",   plt.cm.tab10(1)),
    (DataType.NTF_PPG,  0, "red_led", plt.cm.tab10(2)),
    (DataType.NTF_PPG,  1, "ir_led",  plt.cm.tab10(3)),
    (DataType.NTF_SPO2, 0, "spo2",    plt.cm.tab10(4)),
    (DataType.NTF_SPO2, 1, "heart_rate", plt.cm.tab10(5)),
]

FIXED_Y_RANGES = {
    DataType.NTF_ACC: (-8, 8),
    DataType.NTF_GYRO: (-2000, 2000),
    DataType.NTF_EULER_DATA: (-180, 180),
    DataType.NTF_QUATERNION: (-1, 1),
}


class DataTask(QRunnable):
    def __init__(self, callback, data):
        super().__init__()
        self._cb = callback
        self._data = data

    def run(self):
        try:
            self._cb(self._data)
        except Exception as e:
            print(f"[DataTask] Exception: {e}")


class IMUQuaternionPPGDemo(QtWidgets.QWidget):
    data_received  = QtCore.pyqtSignal(object)
    add_device_sig = QtCore.pyqtSignal(str)
    lost_packet_signal = QtCore.pyqtSignal(str, int)
    device_disconnected_sig = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.discovered_devices = []
        self.current_sensor: SensorProfile = None
        self._pending_device = None
        self.sensor_controller = SensorController()
        self.thread_pool = QThreadPool.globalInstance()
        self._data_type_pools = {}

        self.active_data_type = DataType.NTF_ACC
        self.buffers = {}
        self.sample_rates = {}
        self._sample_index_buffers = {}
        self._buffer_indices = {}
        self._last_plotted_sample_indices = {}
        self.lines_2d = []

        self.current_quaternion = [1.0, 0.0, 0.0, 0.0]
        self.quaternion_lock = QtCore.QMutex()
        self._buffer_locks = {}
        self.cube_vertices = None
        self.cube_faces = None

        self.bio_buffers = {}
        self._bio_sample_index_buffers = {}
        self._bio_buffer_indices = {}
        self.bio_sample_rates = {}
        self.bio_lines = []
        self.bio_impedance = {}
        self._bio_buffer_lock = QtCore.QMutex()

        self._updating_ntf_controls = False
        self._updating_filter_controls = False
        self._debug_log_checkbox = None
        self._data_debug_log_checkbox = None
        self._ntf_checkboxes: dict = {}
        self._filter_checkboxes: dict = {}
        self._debug_log_enabled = True
        self._data_debug_log_enabled = False

        self._init_ui()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._update_plots)
        self.timer.start(PLOT_UPDATE_INTERVAL)

        self.add_device_sig.connect(self._add_device_item)
        self.data_received.connect(self._dispatch_data, type=QtCore.Qt.DirectConnection)
        self.lost_packet_signal.connect(self._update_lost_packet_display)
        self.device_disconnected_sig.connect(self._on_device_disconnected)

        self.lost_packet_counts = {}

        if not self.sensor_controller.hasDeviceFoundCallback:
            self.sensor_controller.onDeviceFoundCallback = self._on_device_found

    # ── UI ────────────────────────────────────────────────────────────────────

    def _init_ui(self):
        main_layout = QtWidgets.QHBoxLayout()

        left_layout = QtWidgets.QVBoxLayout()

        top_left_layout = QtWidgets.QVBoxLayout()
        self.figure_3d = plt.figure(figsize=(6, 6))
        self.canvas_3d = FigureCanvas(self.figure_3d)
        self.toolbar_3d = NavigationToolbar2QT(self.canvas_3d, self)
        top_left_layout.addWidget(QtWidgets.QLabel("3D Quaternion Visualization"))
        top_left_layout.addWidget(self.canvas_3d, stretch=18)
        top_left_layout.addWidget(self.toolbar_3d, stretch=1)

        self.ax_3d = self.figure_3d.add_subplot(111, projection='3d')
        self._setup_3d_plot()

        bottom_left_layout = QtWidgets.QVBoxLayout()
        self.figure_2d, self.ax_2d = plt.subplots()
        self.canvas_2d = FigureCanvas(self.figure_2d)
        bottom_left_layout.addWidget(QtWidgets.QLabel("2D Waveform (ACC/GYRO/Euler)"))
        bottom_left_layout.addWidget(self.canvas_2d)

        left_layout.addLayout(top_left_layout, stretch=1)
        left_layout.addLayout(bottom_left_layout, stretch=1)

        right_layout = QtWidgets.QVBoxLayout()

        controls_layout = QtWidgets.QVBoxLayout()

        self.btn_scan = QtWidgets.QPushButton("Start Scan")
        self.btn_scan.clicked.connect(self._start_scan)

        self.btn_stop_scan = QtWidgets.QPushButton("Stop Scan")
        self.btn_stop_scan.clicked.connect(self._stop_scan)
        self.btn_stop_scan.setEnabled(False)

        self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self._disconnect)
        self.btn_disconnect.setEnabled(False)

        button_layout = QtWidgets.QVBoxLayout()
        button_layout.addWidget(self.btn_scan)
        button_layout.addWidget(self.btn_stop_scan)
        button_layout.addWidget(self.btn_disconnect)
        button_layout.addStretch()

        self.device_list = QtWidgets.QListWidget()
        self.device_list.setMaximumHeight(80)
        self.device_list.itemClicked.connect(self._connect_device)

        device_layout = QtWidgets.QVBoxLayout()
        device_layout.addWidget(QtWidgets.QLabel("Discovered Devices:"))
        device_layout.addWidget(self.device_list)

        scan_layout = QtWidgets.QHBoxLayout()
        scan_layout.addLayout(button_layout)
        scan_layout.addLayout(device_layout, stretch=1)
        controls_layout.addLayout(scan_layout)

        type_layout = QtWidgets.QVBoxLayout()
        type_layout.addWidget(QtWidgets.QLabel("Bottom-left Display Data Type:"))
        self.type_combo = QtWidgets.QComboBox()
        for dt, name in DATA_TYPE_NAMES.items():
            self.type_combo.addItem(name, dt)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()

        self.value_labels: dict = {}
        self.value_box = QtWidgets.QGroupBox("Real-time Values")
        self.value_layout = QtWidgets.QVBoxLayout()
        self.value_box.setLayout(self.value_layout)

        self.lost_packet_label = QtWidgets.QLabel("Packet Loss Stats: None")
        self.lost_packet_label.setWordWrap(True)
        self.lost_packet_box = QtWidgets.QGroupBox("Packet Loss Stats")
        lost_packet_layout = QtWidgets.QVBoxLayout()
        lost_packet_layout.addWidget(self.lost_packet_label)
        self.lost_packet_box.setLayout(lost_packet_layout)

        status_layout = QtWidgets.QHBoxLayout()
        status_layout.addWidget(self.value_box, stretch=1)
        status_layout.addWidget(self.lost_packet_box, stretch=1)

        display_layout = QtWidgets.QHBoxLayout()
        display_layout.addLayout(type_layout)
        display_layout.addLayout(status_layout, stretch=1)
        controls_layout.addLayout(display_layout)

        self.status_label = QtWidgets.QLabel("Not Connected")
        controls_layout.addWidget(self.status_label)

        self.power_label = QtWidgets.QLabel("Power: --%")
        controls_layout.addWidget(self.power_label)

        debug_log_group = QtWidgets.QGroupBox("Debug Log")
        debug_log_layout = QtWidgets.QVBoxLayout()
        self._debug_log_checkbox = QtWidgets.QCheckBox("Enable SDK Debug Log")
        self._debug_log_checkbox.setChecked(True)
        self._debug_log_checkbox.stateChanged.connect(self._on_debug_log_toggled)
        debug_log_layout.addWidget(self._debug_log_checkbox)
        self._data_debug_log_checkbox = QtWidgets.QCheckBox("Enable Data Debug Log")
        self._data_debug_log_checkbox.stateChanged.connect(self._on_data_debug_log_toggled)
        debug_log_layout.addWidget(self._data_debug_log_checkbox)
        debug_log_group.setLayout(debug_log_layout)

        ntf_group = QtWidgets.QGroupBox("Data Notification")
        ntf_layout = QtWidgets.QHBoxLayout()
        self._ntf_checkboxes = {
            "NTF_EEG":  QtWidgets.QCheckBox("EEG"),
            "NTF_PPG":  QtWidgets.QCheckBox("PPG"),
            "NTF_SPO2": QtWidgets.QCheckBox("SpO2"),
        }
        for key, cb in self._ntf_checkboxes.items():
            cb.setChecked(True)
            cb.setEnabled(False)
            cb.stateChanged.connect(lambda state, k=key: self._on_ntf_toggled(k))
            ntf_layout.addWidget(cb)
        ntf_group.setLayout(ntf_layout)

        filter_group = QtWidgets.QGroupBox("Filter")
        filter_layout = QtWidgets.QVBoxLayout()
        self._filter_checkboxes = {
            "FILTER_50HZ": QtWidgets.QCheckBox("50Hz"),
            "FILTER_60HZ": QtWidgets.QCheckBox("60Hz"),
            "FILTER_HPF":  QtWidgets.QCheckBox("HPF"),
            "FILTER_LPF":  QtWidgets.QCheckBox("LPF"),
        }
        for key, cb in self._filter_checkboxes.items():
            cb.setChecked(True)
            cb.setEnabled(False)
            cb.stateChanged.connect(lambda state, k=key: self._on_filter_toggled(k))
            filter_layout.addWidget(cb)
        filter_group.setLayout(filter_layout)

        options_layout = QtWidgets.QHBoxLayout()
        options_layout.addWidget(debug_log_group, stretch=1)
        options_layout.addWidget(ntf_group, stretch=1)
        options_layout.addWidget(filter_group, stretch=1)
        controls_layout.addLayout(options_layout)

        controls_layout.addStretch()

        bio_layout = QtWidgets.QVBoxLayout()
        self.figure_bio, self.axes_bio = plt.subplots(6, 1, sharex=True, figsize=(8, 12))
        self.figure_bio.subplots_adjust(left=0.05, right=0.9, hspace=0.4)
        self.canvas_bio = FigureCanvas(self.figure_bio)
        bio_layout.addWidget(QtWidgets.QLabel("EEG + PPG + SpO2 Waveform"))
        bio_layout.addWidget(self.canvas_bio)

        right_layout.addLayout(controls_layout, stretch=1)
        right_layout.addLayout(bio_layout, stretch=4)

        main_layout.addLayout(left_layout, stretch=3)
        main_layout.addLayout(right_layout, stretch=7)
        self.setLayout(main_layout)
        self.setWindowTitle("SynchroniSDKPython IMU + Quaternion + EEG + PPG Demo")
        self.resize(1600, 900)
        self.show()

    # ── Scan / Connect ────────────────────────────────────────────────────────

    def _start_scan(self):
        if not self.sensor_controller.isEnable:
            self.status_label.setText("Please enable Bluetooth first")
            return
        if not self.sensor_controller.isScanning:
            self.sensor_controller.startScan(SCAN_DEVICE_PERIOD_IN_MS)
        self.btn_scan.setEnabled(False)
        self.btn_stop_scan.setEnabled(True)

    def _stop_scan(self):
        self.sensor_controller.stopScan()
        self.btn_scan.setEnabled(True)
        self.btn_stop_scan.setEnabled(False)

    def _on_device_found(self, device_list: List[BLEDevice]):
        self._stop_scan()
        filtered = filter(lambda x: x.Name.startswith("OB52") or x.Name.startswith("Ce"), device_list)
        for d in filtered:
            if d.Address not in [x.Address for x in self.discovered_devices]:
                self.discovered_devices.append(d)
                self.add_device_sig.emit(f"RSSI: {d.RSSI}, Name: {d.Name}, Address: {d.Address}")

    def _add_device_item(self, text: str):
        self.device_list.addItem(text)

    def _connect_device(self, item):
        text = item.text()
        addr = text.split("Address: ")[1].strip()
        device = next((d for d in self.discovered_devices if d.Address == addr), None)
        if device is None:
            return

        sensor = self.sensor_controller.requireSensor(device)
        if sensor is None:
            self.status_label.setText("Failed to create SensorProfile")
            return

        # 只允许同时连接一个设备，选择新设备时先断开旧设备
        if self.current_sensor is not None and self.current_sensor != sensor:
            self._pending_device = device
            self._disconnect()
            return

        if self.current_sensor is not None and self.current_sensor == sensor:
            return

        self._do_connect_device(device)

    def _do_connect_device(self, device: BLEDevice):
        """实际执行连接、初始化、启动数据流（要求当前无已连接设备）。"""
        sensor = self.sensor_controller.requireSensor(device)
        if sensor is None:
            self.status_label.setText("Failed to create SensorProfile")
            return

        sensor.onDataCallback  = self._on_data
        sensor.onStateChanged  = self._on_state_changed
        sensor.onErrorCallback = self._on_error
        sensor.onPowerChanged  = self._on_power_changed

        if sensor.deviceState != DeviceStateEx.Ready:
            if not sensor.connect():
                self.status_label.setText(f"Failed to connect to {device.Name}")
                return

        if not sensor.hasInited:
            if not sensor.init(PACKAGE_COUNT, POWER_REFRESH_PERIOD_IN_MS):
                self.status_label.setText(f"Failed to initialize {device.Name}")
                return

            info = sensor.getDeviceInfo()
            self._init_buffers(sensor, info)
            self.status_label.setText(
                f"Connected: {device.Name} | "
                f"ACC {info.AccChannelCount}ch @ {info.AccSampleRate}Hz | "
                f"Euler {info.EulerChannelCount}ch @ {info.EulerSampleRate}Hz | "
                f"Quat {info.QuatChannelCount}ch @ {info.QuatSampleRate}Hz | "
                f"EEG {info.EegChannelCount}ch @ {info.EegSampleRate}Hz | "
                f"PPG {info.PpgChannelCount}ch @ {info.PpgSampleRate}Hz | "
                f"SpO2 {info.Spo2ChannelCount}ch @ {info.Spo2SampleRate}Hz"
            )

        if not sensor.startDataNotification():
            self.status_label.setText("Failed to start data stream")
            return

        self.current_sensor = sensor
        self.btn_disconnect.setEnabled(True)
        for cb in self._ntf_checkboxes.values():
            cb.setEnabled(True)
        for cb in self._filter_checkboxes.values():
            cb.setEnabled(True)

        # 根据设备实际参数刷新 UI 开关状态
        self._refresh_control_states(sensor)

        # 根据全局 Debug Log 开关状态初始化当前设备的日志设置
        if self._debug_log_enabled:
            sensor.setParam("DEBUG_LOG_PATH", "True")
        if self._data_debug_log_enabled:
            sensor.setParam("DEBUG_BLE_DATA_PATH", "True")

        self._rebuild_2d_plot()
        self._rebuild_bio_plot()

    def _disconnect(self):
        if self.current_sensor:
            self.current_sensor.disconnect()
            self.btn_disconnect.setEnabled(False)
            for cb in self._ntf_checkboxes.values():
                cb.setEnabled(False)
            for cb in self._filter_checkboxes.values():
                cb.setEnabled(False)
            self.status_label.setText("Disconnecting...")
            self.power_label.setText("Power: --%")

    # ── Data Buffers ──────────────────────────────────────────────────────────

    def _init_buffers(self, sensor: SensorProfile, info: DeviceInfo):
        configs = [
            (DataType.NTF_ACC,        info.AccSampleRate,   info.AccChannelCount),
            (DataType.NTF_GYRO,       info.GyroSampleRate,  info.GyroChannelCount),
            (DataType.NTF_EULER_DATA, info.EulerSampleRate, info.EulerChannelCount),
            (DataType.NTF_QUATERNION, info.QuatSampleRate,  info.QuatChannelCount),
        ]
        for dt, sr, ch in configs:
            if sr > 0 and ch > 0:
                buf_len = max(sr * BUFFER_SECONDS, 1)
                self.buffers[dt]      = np.zeros((ch, buf_len))
                self._sample_index_buffers[dt] = np.zeros((ch, buf_len), dtype=np.int64)
                self._buffer_indices[dt] = 0
                self.sample_rates[dt] = sr

        bio_configs = [
            (DataType.NTF_EEG,  info.EegSampleRate,  info.EegChannelCount),
            (DataType.NTF_PPG,  info.PpgSampleRate,  info.PpgChannelCount),
            (DataType.NTF_SPO2, info.Spo2SampleRate, info.Spo2ChannelCount),
        ]
        for dt, sr, ch in bio_configs:
            if sr > 0 and ch > 0:
                buf_len = max(sr * BUFFER_SECONDS, 1)
                self.bio_buffers[dt] = np.zeros((ch, buf_len))
                self._bio_sample_index_buffers[dt] = np.zeros((ch, buf_len), dtype=np.int64)
                self._bio_buffer_indices[dt] = 0
                self.bio_sample_rates[dt] = sr
                self.bio_impedance[dt] = []

    def _on_data(self, sensor: SensorProfile, data: SensorData):
        if data and data.channelSamples:
            if data.dataType in self.buffers or data.dataType in self.bio_buffers:
                self.data_received.emit(data)
            if data.dataType == DataType.NTF_QUATERNION:
                self._update_quaternion(data)

    def _get_data_type_pool(self, data_type):
        pool = self._data_type_pools.get(data_type)
        if pool is None:
            pool = QThreadPool()
            pool.setMaxThreadCount(1)
            self._data_type_pools[data_type] = pool
        return pool

    def _get_buffer_lock(self, data_type):
        lock = self._buffer_locks.get(data_type)
        if lock is None:
            lock = QtCore.QMutex()
            self._buffer_locks[data_type] = lock
        return lock

    def _dispatch_data(self, data: SensorData):
        task = DataTask(self._append_to_buffer, data)
        pool = self._get_data_type_pool(data.dataType)
        pool.start(task)

    def closeEvent(self, event):
        for pool in self._data_type_pools.values():
            pool.waitForDone()
        try:
            self.sensor_controller.terminate()
        except Exception as e:
            print(f"[closeEvent] terminate error: {e}")
        event.accept()

    def _append_to_buffer(self, data: SensorData):
        if data.dataType in self.bio_buffers:
            self._bio_buffer_lock.lock()
            try:
                buf = self.bio_buffers.get(data.dataType)
                idx_buf = self._bio_sample_index_buffers.get(data.dataType)
                if buf is None or idx_buf is None or not data.channelSamples:
                    return
                buffer_size = buf.shape[1]
                n = len(data.channelSamples[0])
                if n == 0:
                    return
                if n > buffer_size:
                    n = buffer_size
                buf_idx = self._bio_buffer_indices.get(data.dataType, 0)
                write_start = buf_idx
                write_end = buf_idx + n

                # Circular-buffer write: avoid rolling the whole buffer on every packet.
                for ch_idx, ch_samples in enumerate(data.channelSamples):
                    if ch_idx >= buf.shape[0]:
                        break
                    new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                    new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                    if len(new_vals) == 0:
                        continue
                    if len(new_vals) > n:
                        new_vals = new_vals[-n:]
                        new_indices = new_indices[-n:]

                    if write_end <= buffer_size:
                        buf[ch_idx, write_start:write_end] = new_vals
                        idx_buf[ch_idx, write_start:write_end] = new_indices
                    else:
                        first_part = buffer_size - write_start
                        buf[ch_idx, write_start:] = new_vals[:first_part]
                        buf[ch_idx, :n - first_part] = new_vals[first_part:]
                        idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                        idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]

                    if data.dataType == DataType.NTF_EEG:
                        while len(self.bio_impedance[data.dataType]) <= ch_idx:
                            self.bio_impedance[data.dataType].append(0)
                        self.bio_impedance[data.dataType][ch_idx] = ch_samples[-1].impedance

                self._bio_buffer_indices[data.dataType] = (buf_idx + n) % buffer_size
            finally:
                self._bio_buffer_lock.unlock()
            return

        lock = self._get_buffer_lock(data.dataType)
        lock.lock()
        try:
            buf = self.buffers.get(data.dataType)
            idx_buf = self._sample_index_buffers.get(data.dataType)
            if buf is None or idx_buf is None or not data.channelSamples:
                return
            buffer_size = buf.shape[1]
            n = len(data.channelSamples[0])
            if n == 0:
                return
            if n > buffer_size:
                n = buffer_size
            buf_idx = self._buffer_indices.get(data.dataType, 0)
            write_start = buf_idx
            write_end = buf_idx + n

            # Circular-buffer write: avoid rolling the whole buffer on every packet.
            for ch_idx, ch_samples in enumerate(data.channelSamples):
                if ch_idx >= buf.shape[0]:
                    break
                new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                if len(new_vals) == 0:
                    continue
                if len(new_vals) > n:
                    new_vals = new_vals[-n:]
                    new_indices = new_indices[-n:]

                if write_end <= buffer_size:
                    buf[ch_idx, write_start:write_end] = new_vals
                    idx_buf[ch_idx, write_start:write_end] = new_indices
                else:
                    first_part = buffer_size - write_start
                    buf[ch_idx, write_start:] = new_vals[:first_part]
                    buf[ch_idx, :n - first_part] = new_vals[first_part:]
                    idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                    idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]

            self._buffer_indices[data.dataType] = (buf_idx + n) % buffer_size
        finally:
            lock.unlock()

    # ── Bottom-left 2D Waveform ───────────────────────────────────────────────

    def _on_type_changed(self, _):
        self.active_data_type = self.type_combo.currentData()
        self._rebuild_2d_plot()

    def _rebuild_2d_plot(self):
        self.ax_2d.cla()
        self.lines_2d = []
        dt = self.active_data_type
        labels = CHANNEL_LABELS.get(dt, [])

        for lbl in self.value_labels.values():
            lbl.setParent(None)
        self.value_labels.clear()

        lock = self._get_buffer_lock(dt)
        lock.lock()
        try:
            buf = self.buffers.get(dt)
            idx_buf = self._sample_index_buffers.get(dt)
            if buf is None or idx_buf is None:
                self.ax_2d.set_title(f"{DATA_TYPE_NAMES.get(dt, '')} (Device not supported or disabled)")
                self.canvas_2d.draw_idle()
                self._last_plotted_sample_indices.pop(dt, None)
                return
            buf_idx = self._buffer_indices.get(dt, 0)
            # Reassemble the circular buffer into chronological order for plotting.
            buf_copy = np.roll(buf.copy(), -buf_idx, axis=1)
            idx_buf_copy = idx_buf.copy()
        finally:
            lock.unlock()

        self._last_plotted_sample_indices[dt] = int(idx_buf_copy.max())

        t = np.linspace(-BUFFER_SECONDS, 0, buf_copy.shape[1])
        for ch in range(buf_copy.shape[0]):
            label = labels[ch] if ch < len(labels) else f"ch{ch}"
            (line,) = self.ax_2d.plot(t, buf_copy[ch], label=label)
            self.lines_2d.append(line)

            row = QtWidgets.QLabel(f"{label}: --")
            row.setStyleSheet("font-family: monospace; font-size: 13px;")
            self.value_layout.addWidget(row)
            self.value_labels[label] = row

        self.ax_2d.set_title(DATA_TYPE_NAMES.get(dt, ""))
        self.ax_2d.set_xlabel("Time (s)")
        self.ax_2d.set_ylabel("Value")
        self.ax_2d.legend(loc="upper right")
        self.canvas_2d.draw_idle()

    # ── Right-side Biosignal Waveform (2 EEG + 2 PPG + 2 SpO2) ────────────────

    def _rebuild_bio_plot(self):
        self._bio_buffer_lock.lock()
        try:
            buffers_copy = {}
            idx_buffers_copy = {}
            has_any_data = False
            for dt in (DataType.NTF_EEG, DataType.NTF_PPG, DataType.NTF_SPO2):
                buf = self.bio_buffers.get(dt)
                idx_buf = self._bio_sample_index_buffers.get(dt)
                if buf is not None and idx_buf is not None:
                    buf_idx = self._bio_buffer_indices.get(dt, 0)
                    # Reassemble the circular buffer into chronological order for plotting.
                    buffers_copy[dt] = np.roll(buf.copy(), -buf_idx, axis=1)
                    idx_buffers_copy[dt] = idx_buf.copy()
                    has_any_data = True

            if not has_any_data:
                for ax in self.axes_bio:
                    ax.cla()
                self.axes_bio[0].set_title("EEG + PPG + SpO2 (Device not supported or disabled)")
                self.canvas_bio.draw_idle()
                for dt in (DataType.NTF_EEG, DataType.NTF_PPG, DataType.NTF_SPO2):
                    self._last_plotted_sample_indices.pop(dt, None)
                return
        finally:
            self._bio_buffer_lock.unlock()

        for dt, idx_buf in idx_buffers_copy.items():
            self._last_plotted_sample_indices[dt] = int(idx_buf.max())

        self.bio_lines = []
        for plot_idx, (dt, ch_idx, title, color) in enumerate(BIO_PLOT_CONFIG):
            ax = self.axes_bio[plot_idx]
            ax.cla()

            buf_copy = buffers_copy.get(dt)
            if buf_copy is not None and ch_idx < buf_copy.shape[0]:
                t = np.linspace(-BUFFER_SECONDS, 0, buf_copy.shape[1])
                (line,) = ax.plot(t, buf_copy[ch_idx], color=color, linewidth=0.8)
            else:
                (line,) = ax.plot([], [], color=color, linewidth=0.8)

            self.bio_lines.append(line)
            ax.tick_params(axis='both', labelsize=7)
            ax.ticklabel_format(axis='y', style='plain', useOffset=False)
            ax.set_xlim(-BUFFER_SECONDS, 0)
            ax.set_ylabel(title, fontsize=8, color=color, rotation=0, va='center', ha='left', labelpad=10)
            ax.yaxis.set_label_position("right")
            for spine in ax.spines.values():
                spine.set_color(color)

        self.axes_bio[-1].set_xlabel("Time (s)", fontsize=8)
        self.canvas_bio.draw_idle()

    # ── 3D Quaternion ─────────────────────────────────────────────────────────

    def _setup_3d_plot(self):
        self.ax_3d.clear()
        self.ax_3d.set_xlim([-2, 2])
        self.ax_3d.set_ylim([-2, 2])
        self.ax_3d.set_zlim([-2, 2])
        self.ax_3d.set_xlabel('X')
        self.ax_3d.set_ylabel('Y')
        self.ax_3d.set_zlabel('Z')
        self.ax_3d.set_title('IMU Quaternion Visualization (3D Cube)')
        self._create_cube()

        R = self._quaternion_to_rotation_matrix(self.current_quaternion)
        rotated_vertices = np.dot(self.cube_vertices, R.T)
        rotated_faces = [
            [rotated_vertices[0], rotated_vertices[1], rotated_vertices[2], rotated_vertices[3]],
            [rotated_vertices[4], rotated_vertices[5], rotated_vertices[6], rotated_vertices[7]],
            [rotated_vertices[0], rotated_vertices[1], rotated_vertices[5], rotated_vertices[4]],
            [rotated_vertices[2], rotated_vertices[3], rotated_vertices[7], rotated_vertices[6]],
            [rotated_vertices[0], rotated_vertices[3], rotated_vertices[7], rotated_vertices[4]],
            [rotated_vertices[1], rotated_vertices[2], rotated_vertices[6], rotated_vertices[5]]
        ]

        face_colors = ['cyan', 'magenta', 'yellow', 'red', 'green', 'blue']
        self._cube_collection = Poly3DCollection(rotated_faces, facecolors=face_colors,
                                                 linewidths=1, edgecolors='black', alpha=1.0)
        self.ax_3d.add_collection3d(self._cube_collection)

        axis_length = 1.5
        self._quiver_arrows = (
            self.ax_3d.quiver(0, 0, 0, axis_length, 0, 0,
                              color='r', arrow_length_ratio=0.1, linewidth=2),
            self.ax_3d.quiver(0, 0, 0, 0, axis_length, 0,
                              color='g', arrow_length_ratio=0.1, linewidth=2),
            self.ax_3d.quiver(0, 0, 0, 0, 0, axis_length,
                              color='b', arrow_length_ratio=0.1, linewidth=2),
        )

        self._last_3d_update_time = 0
        self._last_drawn_quaternion = None

    def _create_cube(self):
        vertices = np.array([
            [-1, -1, -1],
            [ 1, -1, -1],
            [ 1,  1, -1],
            [-1,  1, -1],
            [-1, -1,  1],
            [ 1, -1,  1],
            [ 1,  1,  1],
            [-1,  1,  1]
        ])
        faces = [
            [vertices[0], vertices[1], vertices[2], vertices[3]],
            [vertices[4], vertices[5], vertices[6], vertices[7]],
            [vertices[0], vertices[1], vertices[5], vertices[4]],
            [vertices[2], vertices[3], vertices[7], vertices[6]],
            [vertices[0], vertices[3], vertices[7], vertices[4]],
            [vertices[1], vertices[2], vertices[6], vertices[5]]
        ]
        self.cube_vertices = vertices
        self.cube_faces = faces

    def _quaternion_to_rotation_matrix(self, q):
        w, x, y, z = q
        norm = np.sqrt(w*w + x*x + y*y + z*z)
        if norm > 0:
            w, x, y, z = w/norm, x/norm, y/norm, z/norm
        return np.array([
            [1 - 2*(y*y + z*z), 2*(x*y - w*z), 2*(x*z + w*y)],
            [2*(x*y + w*z), 1 - 2*(x*x + z*z), 2*(y*z - w*x)],
            [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x*x + y*y)]
        ])

    def _draw_cube(self):
        R = self._quaternion_to_rotation_matrix(self.current_quaternion)
        rotated_vertices = np.dot(self.cube_vertices, R.T)
        rotated_faces = [
            [rotated_vertices[0], rotated_vertices[1], rotated_vertices[2], rotated_vertices[3]],
            [rotated_vertices[4], rotated_vertices[5], rotated_vertices[6], rotated_vertices[7]],
            [rotated_vertices[0], rotated_vertices[1], rotated_vertices[5], rotated_vertices[4]],
            [rotated_vertices[2], rotated_vertices[3], rotated_vertices[7], rotated_vertices[6]],
            [rotated_vertices[0], rotated_vertices[3], rotated_vertices[7], rotated_vertices[4]],
            [rotated_vertices[1], rotated_vertices[2], rotated_vertices[6], rotated_vertices[5]]
        ]

        self._cube_collection.set_verts(rotated_faces)

    def _update_quaternion(self, data: SensorData):
        try:
            if data.dataType == DataType.NTF_QUATERNION:
                if len(data.channelSamples) == 4 and len(data.channelSamples[0]) > 0:
                    quaternion = [
                        data.channelSamples[0][0].data,
                        data.channelSamples[1][0].data,
                        data.channelSamples[2][0].data,
                        data.channelSamples[3][0].data,
                    ]
                    self.quaternion_lock.lock()
                    self.current_quaternion = quaternion
                    self.quaternion_lock.unlock()
        except Exception as e:
            print(f"Quaternion update exception: {e}")

    # ── Periodic Refresh ──────────────────────────────────────────────────────

    def _update_plots(self):
        # Skip rendering while the window is minimized to avoid DWM issues on Windows.
        if self.windowState() & QtCore.Qt.WindowMinimized:
            return

        dt  = self.active_data_type
        buf_copy = None
        idx_buf_copy = None
        lock = self._get_buffer_lock(dt)
        lock.lock()
        try:
            buf = self.buffers.get(dt)
            idx_buf = self._sample_index_buffers.get(dt)
            if buf is not None and idx_buf is not None:
                buf_idx = self._buffer_indices.get(dt, 0)
                # Reassemble the circular buffer into chronological order for plotting.
                buf_copy = np.roll(buf.copy(), -buf_idx, axis=1)
                idx_buf_copy = idx_buf.copy()
        finally:
            lock.unlock()

        if buf_copy is not None and idx_buf_copy is not None and self.lines_2d:
            current_last_idx = int(idx_buf_copy.max())
            last_plotted_idx = self._last_plotted_sample_indices.get(dt, -1)
            if current_last_idx != last_plotted_idx:
                labels = CHANNEL_LABELS.get(dt, [])
                for ch, line in enumerate(self.lines_2d):
                    if ch < buf_copy.shape[0]:
                        line.set_ydata(buf_copy[ch])
                        label = labels[ch] if ch < len(labels) else f"ch{ch}"
                        lbl_widget = self.value_labels.get(label)
                        if lbl_widget:
                            latest = buf_copy[ch, -1]
                            lbl_widget.setText(f"{label}: {latest:+.4f}")

                fixed_range = FIXED_Y_RANGES.get(dt)
                if fixed_range is not None:
                    self.ax_2d.set_ylim(fixed_range)
                else:
                    all_data = buf_copy.flatten()
                    mn, mx = all_data.min(), all_data.max()
                    margin = max((mx - mn) * 0.1, 0.01)
                    new_ylim = (mn - margin, mx + margin)
                    # Avoid tiny y-limit changes that force a full redraw.
                    cur_ylim = self.ax_2d.get_ylim()
                    y_range = cur_ylim[1] - cur_ylim[0]
                    if (abs(new_ylim[0] - cur_ylim[0]) > 0.05 * y_range or
                            abs(new_ylim[1] - cur_ylim[1]) > 0.05 * y_range):
                        self.ax_2d.set_ylim(new_ylim)
                self.canvas_2d.draw_idle()
                self._last_plotted_sample_indices[dt] = current_last_idx

        self._bio_buffer_lock.lock()
        try:
            buffers_copy = {}
            idx_buffers_copy = {}
            impedance_copy = {}
            for dt in (DataType.NTF_EEG, DataType.NTF_PPG, DataType.NTF_SPO2):
                buf = self.bio_buffers.get(dt)
                idx_buf = self._bio_sample_index_buffers.get(dt)
                if buf is not None and idx_buf is not None:
                    buf_idx = self._bio_buffer_indices.get(dt, 0)
                    # Reassemble the circular buffer into chronological order for plotting.
                    buffers_copy[dt] = np.roll(buf.copy(), -buf_idx, axis=1)
                    idx_buffers_copy[dt] = idx_buf.copy()
                    if dt in self.bio_impedance:
                        impedance_copy[dt] = self.bio_impedance[dt][:]
        finally:
            self._bio_buffer_lock.unlock()

        if buffers_copy and self.bio_lines:
            any_updated = False
            for plot_idx, (dt, ch_idx, title, color) in enumerate(BIO_PLOT_CONFIG):
                buf_copy = buffers_copy.get(dt)
                idx_buf_copy = idx_buffers_copy.get(dt)
                if buf_copy is None or idx_buf_copy is None:
                    continue

                current_last_idx = int(idx_buf_copy.max())
                last_plotted_idx = self._last_plotted_sample_indices.get(dt, -1)
                if current_last_idx == last_plotted_idx:
                    continue

                any_updated = True
                line = self.bio_lines[plot_idx]
                ax = self.axes_bio[plot_idx]
                if ch_idx < buf_copy.shape[0]:
                    line.set_ydata(buf_copy[ch_idx])
                    ch_data = buf_copy[ch_idx]
                    mn, mx = ch_data.min(), ch_data.max()
                    margin = max((mx - mn) * 0.1, 0.01)
                    if mn == mx:
                        mn -= 1
                        mx += 1
                    ax.set_ylim(mn - margin, mx + margin)

                    if dt == DataType.NTF_EEG:
                        imp_list = impedance_copy.get(dt, [])
                        if ch_idx < len(imp_list) and isinstance(imp_list[ch_idx], (int, float)):
                            current_impedance = imp_list[ch_idx] / 1000.0
                            if current_impedance <= 500:
                                imp_color = "green"
                            elif 500 < current_impedance <= 999:
                                imp_color = "orange"
                            else:
                                imp_color = "red"
                            ax.set_ylabel(
                                f"{title}\n{current_impedance:.2f} KΩ",
                                fontsize=8, color=imp_color, rotation=0,
                                va='center', ha='left', labelpad=10
                            )
                        else:
                            ax.set_ylabel(
                                title,
                                fontsize=8, color=color, rotation=0,
                                va='center', ha='left', labelpad=10
                            )
                    else:
                        ax.set_ylabel(
                            title,
                            fontsize=8, color=color, rotation=0,
                            va='center', ha='left', labelpad=10
                        )

            if any_updated:
                self.canvas_bio.draw_idle()
                for dt, idx_buf in idx_buffers_copy.items():
                    self._last_plotted_sample_indices[dt] = int(idx_buf.max())

        try:
            self.quaternion_lock.lock()
            w, x, y, z = self.current_quaternion
            self.quaternion_lock.unlock()

            now = QtCore.QDateTime.currentMSecsSinceEpoch()
            quaternion_changed = self._last_drawn_quaternion != [w, x, y, z]
            if now - self._last_3d_update_time >= PLOT_UPDATE_INTERVAL and quaternion_changed:
                self._draw_cube()
                self._last_3d_update_time = now
                self._last_drawn_quaternion = [w, x, y, z]
            self.canvas_3d.draw_idle()
        except Exception as e:
            print(f"3D update exception: {e}")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_state_changed(self, sensor: SensorProfile, state: DeviceStateEx):
        print(f"[State] {sensor.BLEDevice.Name}: {state}")
        if state == DeviceStateEx.Disconnected and self.current_sensor == sensor:
            self.device_disconnected_sig.emit()

    def _on_device_disconnected(self):
        if self._pending_device is not None:
            pending = self._pending_device
            self._pending_device = None
            self.current_sensor = None
            self._clear_ui_data()
            self._do_connect_device(pending)
            return

        if self.current_sensor is None:
            return
        self.current_sensor = None
        self.btn_disconnect.setEnabled(False)
        for cb in self._ntf_checkboxes.values():
            cb.setEnabled(False)
        for cb in self._filter_checkboxes.values():
            cb.setEnabled(False)
        self.status_label.setText("Disconnected (device)")
        self.power_label.setText("Power: --%")

    def _on_error(self, sensor: SensorProfile, reason: str):
        print(f"[Error] {sensor.BLEDevice.Name}: {reason}")
        try:
            parts = reason.split("|")
            if "LOST SAMPLE" in parts:
                lost_type = None
                lost_count = None
                for i, part in enumerate(parts):
                    if part == "TYPE" and i + 1 < len(parts):
                        lost_type = parts[i + 1]
                    elif part == "COUNT" and i + 1 < len(parts):
                        lost_count = int(parts[i + 1])
                if lost_type is not None and lost_count is not None:
                    self.lost_packet_signal.emit(lost_type, lost_count)
        except Exception as e:
            print(f"Error parsing packet loss info: {e}")

    def _lost_type_name(self, type_str: str) -> str:
        try:
            return DataType(int(type_str, 0)).name
        except (ValueError, KeyError):
            return f"TYPE {type_str}"

    def _update_lost_packet_display(self, lost_type: str, count: int):
        type_name = self._lost_type_name(lost_type)
        self.lost_packet_counts[type_name] = self.lost_packet_counts.get(type_name, 0) + count
        lines = [f"  {k}: {v}" for k, v in sorted(self.lost_packet_counts.items())]
        self.lost_packet_label.setText("Packet Loss Stats:\n" + "\n".join(lines))

    def _on_power_changed(self, sensor: SensorProfile, power: int):
        print(f"[Power] {sensor.BLEDevice.Name}: {power}%")
        self.power_label.setText(f"Power: {power}%")

    def _check_set_param_result(self, key: str, result: str) -> bool:
        """检查 setParam 结果，若报错则弹出 QMessageBox。返回 True 表示成功。"""
        if str(result).startswith("Error"):
            QtWidgets.QMessageBox.warning(self, "Set Parameter Failed", f"Failed to set {key}:\n{result}")
            return False
        return True

    def _on_debug_log_toggled(self, state: int):
        enabled = (state == QtCore.Qt.Checked)
        self._debug_log_enabled = enabled
        self.sensor_controller.setDebugEnabled(enabled)
        value = "True" if enabled else "False"
        for sensor in self.sensor_controller.getConnectedSensors():
            if sensor.deviceState == DeviceStateEx.Ready and sensor.hasInited:
                result = sensor.setParam("DEBUG_LOG_PATH", value)
                print(f"[Debug Log] setParam({sensor.BLEDevice.Address}, DEBUG_LOG_PATH, {value}) -> {result}")
                self._check_set_param_result("DEBUG_LOG_PATH", result)

    def _on_data_debug_log_toggled(self, state: int):
        enabled = (state == QtCore.Qt.Checked)
        self._data_debug_log_enabled = enabled
        value = "True" if enabled else "False"
        for sensor in self.sensor_controller.getConnectedSensors():
            if sensor.deviceState == DeviceStateEx.Ready and sensor.hasInited:
                result = sensor.setParam("DEBUG_BLE_DATA_PATH", value)
                print(f"[Data Debug Log] setParam({sensor.BLEDevice.Address}, DEBUG_BLE_DATA_PATH, {value}) -> {result}")
                self._check_set_param_result("DEBUG_BLE_DATA_PATH", result)

    def _on_ntf_toggled(self, key: str):
        if self.current_sensor is None or self.current_sensor.deviceState != DeviceStateEx.Ready:
            return
        if self._updating_ntf_controls:
            return
        cb = self._ntf_checkboxes.get(key)
        if cb is None:
            return
        value = "ON" if cb.isChecked() else "OFF"
        print(f"[NTF] setParam({key}, {value}) ...")
        result = self.current_sensor.setParam(key, value)
        print(f"[NTF] setParam({key}, {value}) -> {result}")
        if self._check_set_param_result(key, result):
            self._refresh_control_states(self.current_sensor)
            self._clear_ui_data()

    def _refresh_control_states(self, sensor: SensorProfile):
        """根据设备当前 NTF/FILTER 参数刷新 UI 开关状态。"""
        info = sensor.getDeviceInfo()
        channel_map = {
            "NTF_EMG":   info.EmgChannelCount if info else 0,
            "NTF_GEST":  info.EmgChannelCount if info else 0,
            "NTF_EEG":   info.EegChannelCount if info else 0,
            "NTF_ECG":   info.EcgChannelCount if info else 0,
            "NTF_PPG":   info.PpgChannelCount if info else 0,
            "NTF_SPO2":  info.Spo2ChannelCount if info else 0,
            "NTF_IMU":   max(info.AccChannelCount, info.GyroChannelCount) if info else 0,
            "NTF_BRTH":  info.BrthChannelCount if info else 0,
            "NTF_IMPEDANCE": info.ImpeChannelCount if info else 0,
            "NTF_MAG_ANGLE": info.MagAngleChannelCount if info else 0,
            "NTF_GFORCE_EULER": info.EulerChannelCount if info else 0,
            "NTF_GFORCE_QUAT": info.QuatChannelCount if info else 0,
            "NTF_GFORCE_ACC": info.AccChannelCount if info else 0,
            "NTF_GFORCE_GYRO": info.GyroChannelCount if info else 0,
        }

        ntf_result = sensor.getParam("NTF")
        print(f"[Refresh] getParam(NTF) -> {ntf_result}")
        if not str(ntf_result).startswith("Error"):
            items = str(ntf_result).split("|")
            self._updating_ntf_controls = True
            try:
                for i in range(0, len(items) - 1, 2):
                    key = items[i]
                    value = items[i + 1]
                    cb = self._ntf_checkboxes.get(key)
                    count = channel_map.get(key, 0)
                    if cb is not None:
                        cb.setEnabled(count > 0)
                        if count > 0:
                            cb.setChecked(value == "ON")
                        else:
                            cb.setChecked(False)
            finally:
                self._updating_ntf_controls = False

        filter_result = sensor.getParam("FILTER")
        print(f"[Refresh] getParam(FILTER) -> {filter_result}")
        has_filter = bool(filter_result) and not str(filter_result).startswith("Error")
        for cb in self._filter_checkboxes.values():
            cb.setEnabled(has_filter)
        self._updating_filter_controls = True
        try:
            if has_filter:
                items = str(filter_result).split("|")
                for i in range(0, len(items) - 1, 2):
                    key = items[i]
                    value = items[i + 1]
                    cb = self._filter_checkboxes.get(key)
                    if cb is not None:
                        cb.setChecked(value == "ON")
            else:
                for cb in self._filter_checkboxes.values():
                    cb.setChecked(False)
        finally:
            self._updating_filter_controls = False

    def _on_filter_toggled(self, key: str):
        if self.current_sensor is None or self.current_sensor.deviceState != DeviceStateEx.Ready:
            return
        if self._updating_filter_controls:
            return
        cb = self._filter_checkboxes.get(key)
        if cb is None:
            return
        value = "ON" if cb.isChecked() else "OFF"
        print(f"[Filter] setParam({key}, {value}) ...")
        result = self.current_sensor.setParam(key, value)
        print(f"[Filter] setParam({key}, {value}) -> {result}")
        if self._check_set_param_result(key, result):
            self._refresh_control_states(self.current_sensor)
            self._clear_ui_data()

    def _clear_ui_data(self):
        """清除 UI 数据缓冲区并重建图表，等待新数据。"""
        self._last_plotted_sample_indices.clear()

        for dt in list(self.buffers.keys()):
            lock = self._get_buffer_lock(dt)
            lock.lock()
            try:
                self.buffers[dt].fill(0)
                self._sample_index_buffers[dt].fill(0)
                self._buffer_indices[dt] = 0
            finally:
                lock.unlock()

        self._bio_buffer_lock.lock()
        try:
            for dt in list(self.bio_buffers.keys()):
                self.bio_buffers[dt].fill(0)
                self._bio_sample_index_buffers[dt].fill(0)
                self._bio_buffer_indices[dt] = 0
        finally:
            self._bio_buffer_lock.unlock()

        self._rebuild_2d_plot()
        self._rebuild_bio_plot()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = IMUQuaternionPPGDemo()

    def _sigint(sig, frame):
        app.quit()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    app.aboutToQuit.connect(lambda: window.sensor_controller.terminate())
    sys.exit(app.exec_())
