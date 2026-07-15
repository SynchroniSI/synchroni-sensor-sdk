import sys
import signal
import time
import multiprocessing
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
BIO_BUFFER_SECONDS         = 1

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

EEG_CHANNEL_COLORS = plt.cm.tab10(np.linspace(0, 1, 8))

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


class IMUQuaternionEEGDemo(QtWidgets.QWidget):
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
        self._last_plotted_sample_indices = {}
        self._buffer_indices = {}
        self.lines_2d = []

        self.current_quaternion = [1.0, 0.0, 0.0, 0.0]
        self._last_drawn_quaternion = [1.0, 0.0, 0.0, 0.0]
        self._last_3d_update_time = 0.0
        self.quaternion_lock = QtCore.QMutex()
        self._buffer_locks = {}
        self._eeg_buffer_lock = QtCore.QMutex()
        self._ecg_buffer_lock = QtCore.QMutex()
        self._brth_buffer_lock = QtCore.QMutex()
        self.cube_vertices = None
        self.cube_faces = None

        self.eeg_buffer = None
        self._eeg_sample_index_buffer = None
        self.eeg_sample_rate = 0
        self.eeg_lines = []
        self.eeg_impedance = []
        self._eeg_display_channels = 0
        self.eeg_total_channels = 0
        self.eeg_page_index = 0
        self.eeg_channels_per_page = 8
        self.has_ecg = False

        self.ecg_buffer = None
        self._ecg_sample_index_buffer = None
        self.ecg_sample_rate = 0
        self.ecg_line = None
        self.ecg_impedance = []

        self.has_brth = False

        self.brth_buffer = None
        self._brth_sample_index_buffer = None
        self.brth_sample_rate = 0
        self.brth_line = None
        self.brth_impedance = []

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

        device_info_layout = QtWidgets.QHBoxLayout()
        self.model_label = QtWidgets.QLabel("Model: --")
        self.hw_version_label = QtWidgets.QLabel("HW Version: --")
        self.fw_version_label = QtWidgets.QLabel("FW Version: --")
        self.power_label = QtWidgets.QLabel("Power: --%")
        device_info_layout.addWidget(self.model_label)
        device_info_layout.addWidget(self.hw_version_label)
        device_info_layout.addWidget(self.fw_version_label)
        device_info_layout.addWidget(self.power_label)
        device_info_layout.addStretch()
        controls_layout.addLayout(device_info_layout)

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
            "NTF_ECG":  QtWidgets.QCheckBox("ECG"),
            "NTF_BRTH": QtWidgets.QCheckBox("BRTH"),
            "NTF_IMU":  QtWidgets.QCheckBox("IMU"),
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

        eeg_layout = QtWidgets.QVBoxLayout()

        page_controls_layout = QtWidgets.QHBoxLayout()
        self.btn_prev_page = QtWidgets.QPushButton("Prev")
        self.btn_prev_page.clicked.connect(self._prev_page)
        self.btn_prev_page.setEnabled(False)
        page_controls_layout.addWidget(self.btn_prev_page)

        self.page_label = QtWidgets.QLabel("Page 1 / 1")
        self.page_label.setAlignment(QtCore.Qt.AlignCenter)
        page_controls_layout.addWidget(self.page_label, stretch=1)

        self.btn_next_page = QtWidgets.QPushButton("Next")
        self.btn_next_page.clicked.connect(self._next_page)
        self.btn_next_page.setEnabled(False)
        page_controls_layout.addWidget(self.btn_next_page)

        eeg_layout.addLayout(page_controls_layout)

        self.figure_eeg, self.axes_eeg = plt.subplots(8, 1, sharex=True, figsize=(8, 12))
        self.figure_eeg.subplots_adjust(left=0.05, right=0.9, hspace=0.4)
        self.canvas_eeg = FigureCanvas(self.figure_eeg)
        eeg_layout.addWidget(QtWidgets.QLabel("EEG + ECG + BRTH Waveform"))
        eeg_layout.addWidget(self.canvas_eeg)

        right_layout.addLayout(controls_layout, stretch=1)
        right_layout.addLayout(eeg_layout, stretch=4)

        main_layout.addLayout(left_layout, stretch=3)
        main_layout.addLayout(right_layout, stretch=7)
        self.setLayout(main_layout)
        self.setWindowTitle("SynchroniSDKPython IMU + Quaternion + EEG + ECG + BRTH Demo")
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
        filtered = filter(lambda x: x.Name.startswith("OB") or x.Name.startswith("Sync") or x.Name.startswith("Orion"), device_list)
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
            self.model_label.setText(f"Model: {info.ModelName}")
            self.hw_version_label.setText(f"HW Version: {info.HardwareVersion}")
            self.fw_version_label.setText(f"FW Version: {info.FirmwareVersion}")
            self.status_label.setText(
                f"Connected: {device.Name} | "
                f"ACC {info.AccChannelCount}ch @ {info.AccSampleRate}Hz | "
                f"Euler {info.EulerChannelCount}ch @ {info.EulerSampleRate}Hz | "
                f"Quat {info.QuatChannelCount}ch @ {info.QuatSampleRate}Hz | "
                f"EEG {info.EegChannelCount}ch @ {info.EegSampleRate}Hz | "
                f"ECG {info.EcgChannelCount}ch @ {info.EcgSampleRate}Hz | "
                f"BRTH {info.BrthChannelCount}ch @ {info.BrthSampleRate}Hz"
            )

        if not sensor.isDataTransfering:
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
        self._rebuild_eeg_plot()

    def _disconnect(self):
        if self.current_sensor:
            self.current_sensor.disconnect()
            self.btn_disconnect.setEnabled(False)
            for cb in self._ntf_checkboxes.values():
                cb.setEnabled(False)
            for cb in self._filter_checkboxes.values():
                cb.setEnabled(False)
            self.status_label.setText("Disconnecting...")
            self.model_label.setText("Model: --")
            self.hw_version_label.setText("HW Version: --")
            self.fw_version_label.setText("FW Version: --")
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
                self.sample_rates[dt] = sr
                self._buffer_indices[dt] = 0

        if info.EegSampleRate > 0 and info.EegChannelCount > 0:
            self.eeg_sample_rate = info.EegSampleRate
            self.eeg_total_channels = info.EegChannelCount
            self.eeg_page_index = 0
            buf_len = max(info.EegSampleRate * BIO_BUFFER_SECONDS, 1)
            self.eeg_buffer = np.zeros((info.EegChannelCount, buf_len))
            self._eeg_sample_index_buffer = np.zeros((info.EegChannelCount, buf_len), dtype=np.int64)
            self._eeg_buffer_index = 0

        self.has_ecg = info.EcgSampleRate > 0 and info.EcgChannelCount > 0
        if self.has_ecg:
            self.ecg_sample_rate = info.EcgSampleRate
            buf_len = max(info.EcgSampleRate * BIO_BUFFER_SECONDS, 1)
            self.ecg_buffer = np.zeros((info.EcgChannelCount, buf_len))
            self._ecg_sample_index_buffer = np.zeros((info.EcgChannelCount, buf_len), dtype=np.int64)
            self._ecg_buffer_index = 0

        self.has_brth = info.BrthSampleRate > 0 and info.BrthChannelCount > 0
        if self.has_brth:
            self.brth_sample_rate = info.BrthSampleRate
            buf_len = max(info.BrthSampleRate * BIO_BUFFER_SECONDS, 1)
            self.brth_buffer = np.zeros((info.BrthChannelCount, buf_len))
            self._brth_sample_index_buffer = np.zeros((info.BrthChannelCount, buf_len), dtype=np.int64)
            self._brth_buffer_index = 0

        extra_axes = int(self.has_ecg) + int(self.has_brth)
        self.eeg_channels_per_page = len(self.axes_eeg) - extra_axes

    def _on_data(self, sensor: SensorProfile, data: SensorData):
        if data and data.channelSamples:
            if data.dataType in self.buffers or data.dataType in (DataType.NTF_EEG, DataType.NTF_ECG, DataType.NTF_BRTH):
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
        if data.lostPackageCount > 0:
            type_name = DataType(data.dataType).name if data.dataType is not None else "Unknown"
            self.lost_packet_signal.emit(type_name, data.lostPackageCount)

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
        if data.dataType == DataType.NTF_EEG:
            self._eeg_buffer_lock.lock()
            try:
                buf = self.eeg_buffer
                idx_buf = self._eeg_sample_index_buffer
                if buf is None or idx_buf is None:
                    return
                buf_len = buf.shape[1]
                n = 0
                for ch_idx, ch_samples in enumerate(data.channelSamples):
                    if ch_idx >= buf.shape[0]:
                        break
                    new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                    new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                    n = min(len(new_vals), buf_len)
                    if n == 0:
                        continue
                    write_start = self._eeg_buffer_index
                    write_end = write_start + n
                    new_vals = new_vals[-n:]
                    new_indices = new_indices[-n:]
                    if write_end <= buf_len:
                        buf[ch_idx, write_start:write_end] = new_vals
                        idx_buf[ch_idx, write_start:write_end] = new_indices
                    else:
                        first_part = buf_len - write_start
                        buf[ch_idx, write_start:] = new_vals[:first_part]
                        buf[ch_idx, :n - first_part] = new_vals[first_part:]
                        idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                        idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]
                    while len(self.eeg_impedance) <= ch_idx:
                        self.eeg_impedance.append(0)
                    if ch_samples:
                        self.eeg_impedance[ch_idx] = ch_samples[-1].impedance
                self._eeg_buffer_index = (self._eeg_buffer_index + n) % buf_len
            finally:
                self._eeg_buffer_lock.unlock()
            return

        if data.dataType == DataType.NTF_ECG:
            self._ecg_buffer_lock.lock()
            try:
                buf = self.ecg_buffer
                idx_buf = self._ecg_sample_index_buffer
                if buf is None or idx_buf is None:
                    return
                buf_len = buf.shape[1]
                n = 0
                for ch_idx, ch_samples in enumerate(data.channelSamples):
                    if ch_idx >= buf.shape[0]:
                        break
                    new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                    new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                    n = min(len(new_vals), buf_len)
                    if n == 0:
                        continue
                    write_start = self._ecg_buffer_index
                    write_end = write_start + n
                    new_vals = new_vals[-n:]
                    new_indices = new_indices[-n:]
                    if write_end <= buf_len:
                        buf[ch_idx, write_start:write_end] = new_vals
                        idx_buf[ch_idx, write_start:write_end] = new_indices
                    else:
                        first_part = buf_len - write_start
                        buf[ch_idx, write_start:] = new_vals[:first_part]
                        buf[ch_idx, :n - first_part] = new_vals[first_part:]
                        idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                        idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]
                    while len(self.ecg_impedance) <= ch_idx:
                        self.ecg_impedance.append(0)
                    if ch_samples:
                        self.ecg_impedance[ch_idx] = ch_samples[-1].impedance
                self._ecg_buffer_index = (self._ecg_buffer_index + n) % buf_len
            finally:
                self._ecg_buffer_lock.unlock()
            return

        if data.dataType == DataType.NTF_BRTH:
            self._brth_buffer_lock.lock()
            try:
                buf = self.brth_buffer
                idx_buf = self._brth_sample_index_buffer
                if buf is None or idx_buf is None:
                    return
                buf_len = buf.shape[1]
                n = 0
                for ch_idx, ch_samples in enumerate(data.channelSamples):
                    if ch_idx >= buf.shape[0]:
                        break
                    new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                    new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                    n = min(len(new_vals), buf_len)
                    if n == 0:
                        continue
                    write_start = self._brth_buffer_index
                    write_end = write_start + n
                    new_vals = new_vals[-n:]
                    new_indices = new_indices[-n:]
                    if write_end <= buf_len:
                        buf[ch_idx, write_start:write_end] = new_vals
                        idx_buf[ch_idx, write_start:write_end] = new_indices
                    else:
                        first_part = buf_len - write_start
                        buf[ch_idx, write_start:] = new_vals[:first_part]
                        buf[ch_idx, :n - first_part] = new_vals[first_part:]
                        idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                        idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]
                    while len(self.brth_impedance) <= ch_idx:
                        self.brth_impedance.append(0)
                    if ch_samples:
                        self.brth_impedance[ch_idx] = ch_samples[-1].impedance
                self._brth_buffer_index = (self._brth_buffer_index + n) % buf_len
            finally:
                self._brth_buffer_lock.unlock()
            return

        lock = self._get_buffer_lock(data.dataType)
        lock.lock()
        try:
            buf = self.buffers.get(data.dataType)
            idx_buf = self._sample_index_buffers.get(data.dataType)
            if buf is None or idx_buf is None:
                return
            buf_len = buf.shape[1]
            n = 0
            buffer_index = self._buffer_indices.get(data.dataType, 0)
            for ch_idx, ch_samples in enumerate(data.channelSamples):
                if ch_idx >= buf.shape[0]:
                    break
                new_vals = np.array([s.data for s in ch_samples], dtype=np.float32)
                new_indices = np.array([s.sampleIndex for s in ch_samples], dtype=np.int64)
                n = min(len(new_vals), buf_len)
                if n == 0:
                    continue
                write_start = buffer_index
                write_end = write_start + n
                new_vals = new_vals[-n:]
                new_indices = new_indices[-n:]
                if write_end <= buf_len:
                    buf[ch_idx, write_start:write_end] = new_vals
                    idx_buf[ch_idx, write_start:write_end] = new_indices
                else:
                    first_part = buf_len - write_start
                    buf[ch_idx, write_start:] = new_vals[:first_part]
                    buf[ch_idx, :n - first_part] = new_vals[first_part:]
                    idx_buf[ch_idx, write_start:] = new_indices[:first_part]
                    idx_buf[ch_idx, :n - first_part] = new_indices[first_part:]
            self._buffer_indices[data.dataType] = (buffer_index + n) % buf_len
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
            buf_copy = buf.copy()
            idx_buf_copy = idx_buf.copy()
            buffer_index = self._buffer_indices.get(dt, 0)
        finally:
            lock.unlock()

        self._last_plotted_sample_indices[dt] = int(idx_buf_copy.max())

        t = np.linspace(-BUFFER_SECONDS, 0, buf_copy.shape[1])
        for ch in range(buf_copy.shape[0]):
            label = labels[ch] if ch < len(labels) else f"ch{ch}"
            y_data = np.roll(buf_copy[ch], -buffer_index)
            (line,) = self.ax_2d.plot(t, y_data, label=label)
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

    # ── Right-side EEG + ECG Waveform ─────────────────────────────────────────

    def _eeg_page_count(self) -> int:
        if self.eeg_total_channels <= 0:
            return 1
        return max(1, (self.eeg_total_channels + self.eeg_channels_per_page - 1) // self.eeg_channels_per_page)

    def _eeg_page_range(self):
        page_count = self._eeg_page_count()
        self.eeg_page_index = max(0, min(self.eeg_page_index, page_count - 1))
        start = self.eeg_page_index * self.eeg_channels_per_page
        end = min(start + self.eeg_channels_per_page, self.eeg_total_channels)
        return start, end

    def _update_page_label(self):
        page_count = self._eeg_page_count()
        self.page_label.setText(f"Page {self.eeg_page_index + 1} / {page_count}")

    def _update_page_buttons(self):
        page_count = self._eeg_page_count()
        self.btn_prev_page.setEnabled(self.eeg_page_index > 0)
        self.btn_next_page.setEnabled(self.eeg_page_index < page_count - 1)

    def _prev_page(self):
        if self.eeg_page_index > 0:
            self.eeg_page_index -= 1
            self._rebuild_eeg_plot()
            self._update_page_label()
            self._update_page_buttons()

    def _next_page(self):
        page_count = self._eeg_page_count()
        if self.eeg_page_index < page_count - 1:
            self.eeg_page_index += 1
            self._rebuild_eeg_plot()
            self._update_page_label()
            self._update_page_buttons()

    def _rebuild_eeg_plot(self):
        self._eeg_buffer_lock.lock()
        self._ecg_buffer_lock.lock()
        self._brth_buffer_lock.lock()
        try:
            eeg_available = self.eeg_buffer is not None and self._eeg_sample_index_buffer is not None
            ecg_available = self.has_ecg and self.ecg_buffer is not None and self._ecg_sample_index_buffer is not None
            brth_available = self.has_brth and self.brth_buffer is not None and self._brth_sample_index_buffer is not None
            if not eeg_available:
                for ax in self.axes_eeg:
                    ax.cla()
                    ax.set_visible(True)
                self.axes_eeg[0].set_title("EEG + ECG + BRTH (Device not supported or disabled)")
                self.canvas_eeg.draw_idle()
                self._last_plotted_sample_indices.pop(DataType.NTF_EEG, None)
                self._last_plotted_sample_indices.pop(DataType.NTF_ECG, None)
                self._last_plotted_sample_indices.pop(DataType.NTF_BRTH, None)
                self.eeg_lines = []
                self.ecg_line = None
                self.brth_line = None
                return
            eeg_buffer_copy = self.eeg_buffer.copy()
            eeg_idx_buf_copy = self._eeg_sample_index_buffer.copy()
            eeg_buffer_index = self._eeg_buffer_index
            ecg_buffer_copy = self.ecg_buffer.copy() if ecg_available else None
            ecg_idx_buf_copy = self._ecg_sample_index_buffer.copy() if ecg_available else None
            ecg_buffer_index = self._ecg_buffer_index if ecg_available else 0
            brth_buffer_copy = self.brth_buffer.copy() if brth_available else None
            brth_idx_buf_copy = self._brth_sample_index_buffer.copy() if brth_available else None
            brth_buffer_index = self._brth_buffer_index if brth_available else 0
        finally:
            self._brth_buffer_lock.unlock()
            self._ecg_buffer_lock.unlock()
            self._eeg_buffer_lock.unlock()

        self._last_plotted_sample_indices[DataType.NTF_EEG] = int(eeg_idx_buf_copy.max())
        if ecg_available:
            self._last_plotted_sample_indices[DataType.NTF_ECG] = int(ecg_idx_buf_copy.max())
        else:
            self.ecg_line = None
        if brth_available:
            self._last_plotted_sample_indices[DataType.NTF_BRTH] = int(brth_idx_buf_copy.max())
        else:
            self.brth_line = None

        start_ch, end_ch = self._eeg_page_range()
        page_eeg_count = max(0, end_ch - start_ch)
        self._eeg_display_channels = page_eeg_count

        brth_axis_index = len(self.axes_eeg) - 1 if self.has_brth else None
        ecg_axis_index = len(self.axes_eeg) - 1 - int(self.has_brth) if self.has_ecg else None

        self.eeg_lines = []
        t = np.linspace(-BIO_BUFFER_SECONDS, 0, eeg_buffer_copy.shape[1])
        t_ecg = np.linspace(-BIO_BUFFER_SECONDS, 0, ecg_buffer_copy.shape[1]) if ecg_available else None
        t_brth = np.linspace(-BIO_BUFFER_SECONDS, 0, brth_buffer_copy.shape[1]) if brth_available else None

        for ch, ax in enumerate(self.axes_eeg):
            ax.cla()
            if ch < page_eeg_count:
                eeg_ch = start_ch + ch
                color = EEG_CHANNEL_COLORS[eeg_ch % len(EEG_CHANNEL_COLORS)]
                y_data = np.roll(eeg_buffer_copy[eeg_ch], -eeg_buffer_index)
                (line,) = ax.plot(t, y_data, color=color, linewidth=0.8)
                self.eeg_lines.append(line)
                ax.tick_params(axis='both', labelsize=7)
                ax.ticklabel_format(axis='y', style='plain', useOffset=False)
                ax.set_xlim(-BIO_BUFFER_SECONDS, 0)
                ax.set_ylabel(f"EEG-{eeg_ch + 1}", fontsize=8, color=color, rotation=0, va='center', ha='left', labelpad=10)
                ax.yaxis.set_label_position("right")
                for spine in ax.spines.values():
                    spine.set_color(color)
                ax.set_visible(True)
            elif ch == ecg_axis_index and ecg_available:
                color = plt.cm.tab10(7)
                y_data = np.roll(ecg_buffer_copy[0], -ecg_buffer_index)
                (line,) = ax.plot(t_ecg, y_data, color=color, linewidth=0.8)
                self.ecg_line = line
                ax.tick_params(axis='both', labelsize=7)
                ax.ticklabel_format(axis='y', style='plain', useOffset=False)
                ax.set_xlim(-BIO_BUFFER_SECONDS, 0)
                ax.set_ylabel("ECG", fontsize=8, color=color, rotation=0, va='center', ha='left', labelpad=10)
                ax.yaxis.set_label_position("right")
                for spine in ax.spines.values():
                    spine.set_color(color)
                ax.set_visible(True)
            elif ch == brth_axis_index and brth_available:
                color = plt.cm.tab10(6)
                y_data = np.roll(brth_buffer_copy[0], -brth_buffer_index)
                (line,) = ax.plot(t_brth, y_data, color=color, linewidth=0.8)
                self.brth_line = line
                ax.tick_params(axis='both', labelsize=7)
                ax.ticklabel_format(axis='y', style='plain', useOffset=False)
                ax.set_xlim(-BIO_BUFFER_SECONDS, 0)
                ax.set_ylabel("BRTH", fontsize=8, color=color, rotation=0, va='center', ha='left', labelpad=10)
                ax.yaxis.set_label_position("right")
                for spine in ax.spines.values():
                    spine.set_color(color)
                ax.set_visible(True)
            else:
                ax.set_visible(False)

        self.axes_eeg[-1].set_xlabel("Time (s)", fontsize=8)
        self._update_page_label()
        self._update_page_buttons()
        self.canvas_eeg.draw_idle()

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

        face_colors = ['cyan', 'magenta', 'yellow', 'red', 'green', 'blue']
        self._cube_collection = Poly3DCollection(self.cube_faces, facecolors=face_colors,
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
        self._draw_cube()

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
        if self.windowState() & QtCore.Qt.WindowMinimized:
            return

        dt  = self.active_data_type
        buf_copy = None
        idx_buf_copy = None
        buffer_index = 0
        lock = self._get_buffer_lock(dt)
        lock.lock()
        try:
            buf = self.buffers.get(dt)
            idx_buf = self._sample_index_buffers.get(dt)
            if buf is not None and idx_buf is not None:
                buf_copy = buf.copy()
                idx_buf_copy = idx_buf.copy()
                buffer_index = self._buffer_indices.get(dt, 0)
        finally:
            lock.unlock()

        if buf_copy is not None and idx_buf_copy is not None and self.lines_2d:
            current_last_idx = int(idx_buf_copy.max())
            last_plotted_idx = self._last_plotted_sample_indices.get(dt, -1)
            if current_last_idx != last_plotted_idx:
                # Reassemble the circular buffer once for all channels.
                buf_copy = np.roll(buf_copy, -buffer_index, axis=1)
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

        eeg_buffer_copy = None
        eeg_idx_buf_copy = None
        eeg_impedance_copy = None
        eeg_buffer_index = 0
        ecg_buffer_copy = None
        ecg_idx_buf_copy = None
        ecg_impedance_copy = None
        ecg_buffer_index = 0
        brth_buffer_copy = None
        brth_idx_buf_copy = None
        brth_impedance_copy = None
        brth_buffer_index = 0
        self._eeg_buffer_lock.lock()
        self._ecg_buffer_lock.lock()
        self._brth_buffer_lock.lock()
        try:
            if self.eeg_buffer is not None and self._eeg_sample_index_buffer is not None:
                eeg_buffer_copy = self.eeg_buffer.copy()
                eeg_idx_buf_copy = self._eeg_sample_index_buffer.copy()
                eeg_impedance_copy = list(self.eeg_impedance)
                eeg_buffer_index = self._eeg_buffer_index
            if self.has_ecg and self.ecg_buffer is not None and self._ecg_sample_index_buffer is not None:
                ecg_buffer_copy = self.ecg_buffer.copy()
                ecg_idx_buf_copy = self._ecg_sample_index_buffer.copy()
                ecg_impedance_copy = list(self.ecg_impedance)
                ecg_buffer_index = self._ecg_buffer_index
            if self.has_brth and self.brth_buffer is not None and self._brth_sample_index_buffer is not None:
                brth_buffer_copy = self.brth_buffer.copy()
                brth_idx_buf_copy = self._brth_sample_index_buffer.copy()
                brth_impedance_copy = list(self.brth_impedance)
                brth_buffer_index = self._brth_buffer_index
        finally:
            self._brth_buffer_lock.unlock()
            self._ecg_buffer_lock.unlock()
            self._eeg_buffer_lock.unlock()

        brth_axis_index = len(self.axes_eeg) - 1 if self.has_brth else None
        ecg_axis_index = len(self.axes_eeg) - 1 - int(self.has_brth) if self.has_ecg else None
        start_ch, _ = self._eeg_page_range()

        if eeg_buffer_copy is not None and eeg_idx_buf_copy is not None and self.eeg_lines:
            current_last_idx = int(eeg_idx_buf_copy.max())
            last_plotted_idx = self._last_plotted_sample_indices.get(DataType.NTF_EEG, -1)
            if current_last_idx != last_plotted_idx:
                for ch, line in enumerate(self.eeg_lines):
                    if ch >= self._eeg_display_channels:
                        continue
                    eeg_ch = start_ch + ch
                    if eeg_ch >= eeg_buffer_copy.shape[0]:
                        continue
                    y_data = np.roll(eeg_buffer_copy[eeg_ch], -eeg_buffer_index)
                    line.set_ydata(y_data)
                    ax = self.axes_eeg[ch]
                    if not ax.get_visible():
                        ax.set_visible(True)
                    ch_data = y_data
                    mn, mx = ch_data.min(), ch_data.max()
                    margin = max((mx - mn) * 0.1, 0.01)
                    if mn == mx:
                        mn -= 1
                        mx += 1
                    ax.set_ylim(mn - margin, mx + margin)

                    if eeg_ch < len(eeg_impedance_copy) and isinstance(eeg_impedance_copy[eeg_ch], (int, float)):
                        current_impedance = eeg_impedance_copy[eeg_ch] / 1000.0
                        if current_impedance <= 500:
                            color = "green"
                        elif 500 < current_impedance <= 999:
                            color = "orange"
                        else:
                            color = "red"
                        ax.set_ylabel(
                            f"EEG-{eeg_ch + 1}\n{current_impedance:.2f} KΩ",
                            fontsize=8, color=color, rotation=0,
                            va='center', ha='left', labelpad=10
                        )
                        ax.yaxis.set_label_position("right")

                self.canvas_eeg.draw_idle()
                self._last_plotted_sample_indices[DataType.NTF_EEG] = current_last_idx

        if brth_buffer_copy is not None and brth_idx_buf_copy is not None and self.brth_line is not None:
            current_last_idx = int(brth_idx_buf_copy.max())
            last_plotted_idx = self._last_plotted_sample_indices.get(DataType.NTF_BRTH, -1)
            if current_last_idx != last_plotted_idx:
                y_data = np.roll(brth_buffer_copy[0], -brth_buffer_index)
                self.brth_line.set_ydata(y_data)
                ax = self.axes_eeg[brth_axis_index]
                if not ax.get_visible():
                    ax.set_visible(True)
                ch_data = y_data
                mn, mx = ch_data.min(), ch_data.max()
                margin = max((mx - mn) * 0.1, 0.01)
                if mn == mx:
                    mn -= 1
                    mx += 1
                ax.set_ylim(mn - margin, mx + margin)
                self.canvas_eeg.draw_idle()
                self._last_plotted_sample_indices[DataType.NTF_BRTH] = current_last_idx

        if ecg_buffer_copy is not None and ecg_idx_buf_copy is not None and self.ecg_line is not None:
            current_last_idx = int(ecg_idx_buf_copy.max())
            last_plotted_idx = self._last_plotted_sample_indices.get(DataType.NTF_ECG, -1)
            if current_last_idx != last_plotted_idx:
                y_data = np.roll(ecg_buffer_copy[0], -ecg_buffer_index)
                self.ecg_line.set_ydata(y_data)
                ax = self.axes_eeg[ecg_axis_index]
                if not ax.get_visible():
                    ax.set_visible(True)
                ch_data = y_data
                mn, mx = ch_data.min(), ch_data.max()
                margin = max((mx - mn) * 0.1, 0.01)
                if mn == mx:
                    mn -= 1
                    mx += 1
                ax.set_ylim(mn - margin, mx + margin)

                if ecg_impedance_copy and len(ecg_impedance_copy) > 0 and isinstance(ecg_impedance_copy[0], (int, float)):
                    current_impedance = ecg_impedance_copy[0] / 1000.0
                    if current_impedance <= 500:
                        color = "green"
                    elif 500 < current_impedance <= 999:
                        color = "orange"
                    else:
                        color = "red"
                    ax.set_ylabel(
                        f"ECG\n{current_impedance:.2f} KΩ",
                        fontsize=8, color=color, rotation=0,
                        va='center', ha='left', labelpad=10
                    )
                    ax.yaxis.set_label_position("right")

                self.canvas_eeg.draw_idle()
                self._last_plotted_sample_indices[DataType.NTF_ECG] = current_last_idx

        try:
            self.quaternion_lock.lock()
            current_quaternion = self.current_quaternion[:]
            self.quaternion_lock.unlock()

            now = time.time()
            elapsed_ms = (now - self._last_3d_update_time) * 1000
            quaternion_changed = current_quaternion != self._last_drawn_quaternion

            if elapsed_ms >= PLOT_UPDATE_INTERVAL and quaternion_changed:
                self.current_quaternion = current_quaternion
                self._draw_cube()
                self._last_drawn_quaternion = current_quaternion[:]
                self._last_3d_update_time = now

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
            self._clear_lost_packet_stats()
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
        self.model_label.setText("Model: --")
        self.hw_version_label.setText("HW Version: --")
        self.fw_version_label.setText("FW Version: --")
        self.power_label.setText("Power: --%")
        self._clear_lost_packet_stats()

    def _on_error(self, sensor: SensorProfile, reason: str):
        print(f"[Error] {sensor.BLEDevice.Name}: {reason}")

    def _update_lost_packet_display(self, lost_type: str, count: int):
        self.lost_packet_counts[lost_type] = count
        lines = [f"  {k}: {v}" for k, v in sorted(self.lost_packet_counts.items())]
        self.lost_packet_label.setText("Packet Loss Stats:\n" + "\n".join(lines))

    def _clear_lost_packet_stats(self):
        self.lost_packet_counts.clear()
        self.lost_packet_label.setText("Packet Loss Stats: None")

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

        self._eeg_buffer_lock.lock()
        self._ecg_buffer_lock.lock()
        self._brth_buffer_lock.lock()
        try:
            if self.eeg_buffer is not None:
                self.eeg_buffer.fill(0)
                self._eeg_sample_index_buffer.fill(0)
                self._eeg_buffer_index = 0
            if self.ecg_buffer is not None:
                self.ecg_buffer.fill(0)
                self._ecg_sample_index_buffer.fill(0)
                self._ecg_buffer_index = 0
            if self.brth_buffer is not None:
                self.brth_buffer.fill(0)
                self._brth_sample_index_buffer.fill(0)
                self._brth_buffer_index = 0
        finally:
            self._brth_buffer_lock.unlock()
            self._ecg_buffer_lock.unlock()
            self._eeg_buffer_lock.unlock()

        self._rebuild_2d_plot()
        self._rebuild_eeg_plot()


if __name__ == "__main__":
    # PyInstaller 打包后，multiprocessing 子进程必须调用 freeze_support()
    multiprocessing.freeze_support()
    app = QtWidgets.QApplication(sys.argv)
    window = IMUQuaternionEEGDemo()

    def _sigint(sig, frame):
        app.quit()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    app.aboutToQuit.connect(lambda: window.sensor_controller.terminate())
    sys.exit(app.exec_())
