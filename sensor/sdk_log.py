"""Synchroni SDK 统一日志开关。

用法与 Android 端的 com.sensor.SdkLog 保持一致，并额外支持将日志写入文件：
    from sensor.sdk_log import SdkLog

    SdkLog.set_debug_enabled(False)          # 关闭调试日志
    SdkLog.set_log_path()                    # 启用文件日志，使用默认路径
    SdkLog.set_log_path("/tmp/my.log")       # 启用文件日志，自定义路径
    SdkLog.set_log_path("")                  # 禁用文件日志
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# 自定义 data 日志级别，位于 DEBUG 与 INFO 之间
DATA_LOG_LEVEL = 15
logging.addLevelName(DATA_LOG_LEVEL, "DATA")


class SdkLog:
    """SDK 全局日志开关。

    所有 SDK 内部调试日志都应通过本类输出，以便调用方统一控制日志噪音。
    支持将日志写入文件，默认路径为 ``~/Documents/sensorsdklog/sensor_sdk.log``。

    额外提供独立的 data 日志通道，用于记录原始蓝牙数据包与解析后的数据，
    默认关闭，通过 ``set_data_log_enabled`` 控制。
    """

    _debug_enabled = True
    _data_log_enabled = False
    _logger = logging.getLogger("sensor_sdk")
    _logger.setLevel(logging.DEBUG)
    _file_handler: Optional[logging.FileHandler] = None
    _log_path: Optional[str] = None

    # SensorController 专用日志（默认不开启）
    _controller_logger = logging.getLogger("sensor_sdk_controller")
    _controller_logger.setLevel(logging.DEBUG)
    _controller_handler: Optional[logging.FileHandler] = None

    @classmethod
    def _formatter(cls) -> logging.Formatter:
        return logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    @classmethod
    def get_default_log_path(cls, prefix: str = "") -> str:
        """返回默认 SDK 日志文件路径。

        若提供 ``prefix``，则文件名为 ``{prefix}_log_YYYYMMDD_HHMMSS.txt``，
        否则为 ``log_YYYYMMDD_HHMMSS.txt``。
        """
        docs = Path.home() / "Documents"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix_part = f"{prefix}_" if prefix else ""
        return str(docs / "sensorsdklog" / f"{prefix_part}log_{timestamp}.txt")

    # 保持向后兼容的旧名称
    _get_default_log_path = get_default_log_path

    @classmethod
    def get_default_data_log_path(cls, prefix: str = "") -> str:
        """返回默认 data 日志 CSV 文件路径。

        若提供 ``prefix``，则文件名为 ``{prefix}_data_YYYYMMDD_HHMMSS.csv``，
        否则为 ``data_YYYYMMDD_HHMMSS.csv``。
        """
        docs = Path.home() / "Documents"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix_part = f"{prefix}_" if prefix else ""
        return str(docs / "sensorsdklog" / f"{prefix_part}data_{timestamp}.csv")

    @classmethod
    def _ensure_dir(cls, path: str):
        if path:
            try:
                os.makedirs(path, exist_ok=True)
            except Exception:
                pass

    @classmethod
    def _remove_file_handler(cls):
        if cls._file_handler is not None:
            try:
                cls._logger.removeHandler(cls._file_handler)
                cls._file_handler.close()
            except Exception:
                pass
            cls._file_handler = None

    @classmethod
    def set_debug_enabled(cls, enabled: bool):
        """开启或关闭 SDK 调试日志。"""
        cls._debug_enabled = bool(enabled)

    @classmethod
    def is_debug_enabled(cls) -> bool:
        """返回当前是否开启调试日志。"""
        return cls._debug_enabled

    @classmethod
    def set_data_log_enabled(cls, enabled: bool):
        """开启或关闭 data 日志（记录蓝牙数据包与解析结果），默认关闭。"""
        cls._data_log_enabled = bool(enabled)
        os.environ["SENSORSKD_DATA_LOG_ENABLED"] = "1" if cls._data_log_enabled else "0"

    @classmethod
    def is_data_log_enabled(cls) -> bool:
        """返回当前是否开启 data 日志。"""
        return cls._data_log_enabled

    @classmethod
    def set_log_path(cls, path: Optional[str] = None):
        """设置 SDK 日志文件路径。

        Args:
            path: 日志文件路径。
                - ``None``：使用默认路径 ``~/Documents/sensorsdklog/sensor_sdk.log``。
                - ``""``：禁用文件日志。
                - 其他：使用指定路径。
        """
        cls._remove_file_handler()
        if path == "":
            cls._log_path = None
            return

        if path is None:
            path = cls.get_default_log_path()

        cls._log_path = path
        cls._ensure_dir(os.path.dirname(path))
        try:
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(cls._formatter())
            cls._logger.addHandler(handler)
            cls._file_handler = handler
        except Exception as e:
            cls._logger.warning(f"Failed to create log file {path}: {e}")

    @classmethod
    def get_log_path(cls) -> Optional[str]:
        """返回当前日志文件路径，若未启用文件日志则返回 None。"""
        return cls._log_path

    @classmethod
    def enable_file_log(cls, enabled: bool = True):
        """开启或关闭文件日志。开启时使用默认日志路径。"""
        if enabled:
            if cls._file_handler is None:
                cls.set_log_path()
        else:
            cls.set_log_path("")

    @classmethod
    def _controller_formatter(cls) -> logging.Formatter:
        return logging.Formatter(
            "%(asctime)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    @classmethod
    def _get_default_controller_log_path(cls) -> str:
        """返回 SensorController 默认日志文件路径：~/Documents/sensorsdklog/sensor_controller_log_YYYYMMDD_HHMMSS.txt。"""
        docs = Path.home() / "Documents"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(docs / "sensorsdklog" / f"sensor_controller_log_{timestamp}.txt")

    @classmethod
    def set_controller_log_path(cls, path: Optional[str] = None):
        """设置 SensorController 专用日志文件路径。

        Args:
            path: 日志文件路径。
                - ``None``：使用默认路径 ``~/Documents/sensorsdklog/sensor_controller_log_YYYYMMDD_HHMMSS.txt``。
                - ``""``：禁用 Controller 日志。
                - 其他：使用指定路径。
        """
        if cls._controller_handler is not None:
            try:
                cls._controller_logger.removeHandler(cls._controller_handler)
                cls._controller_handler.close()
            except Exception:
                pass
            cls._controller_handler = None

        if path == "":
            return

        if path is None:
            path = cls._get_default_controller_log_path()

        cls._ensure_dir(os.path.dirname(path))
        try:
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(cls._controller_formatter())
            cls._controller_logger.addHandler(handler)
            cls._controller_handler = handler
        except Exception as e:
            cls._logger.warning(f"Failed to create controller log file {path}: {e}")

    @classmethod
    def get_controller_log_path(cls) -> Optional[str]:
        """返回当前 SensorController 日志文件路径，若未启用则返回 None。"""
        return cls._controller_handler.baseFilename if cls._controller_handler is not None else None

    @classmethod
    def enable_controller_log(cls, enabled: bool = True):
        """开启或关闭 SensorController 专用日志。开启时使用默认路径。"""
        if enabled:
            if cls._controller_handler is None:
                cls.set_controller_log_path()
        else:
            cls.set_controller_log_path("")

    @classmethod
    def controller(cls, tag: str, msg: str):
        """输出 SensorController 专用日志。"""
        cls._controller_logger.info(f"[{tag}] {msg}")

    @classmethod
    def d(cls, tag: str, msg: str):
        """输出 debug 日志（可被 set_debug_enabled 关闭）。"""
        if cls._debug_enabled:
            cls._logger.debug(f"[{tag}] {msg}")

    @classmethod
    def data(cls, tag: str, msg: str):
        """输出 data 日志（记录蓝牙数据包与解析结果）。

        仅当 ``set_data_log_enabled(True)`` 时输出，默认关闭。
        """
        if cls._data_log_enabled:
            cls._logger.log(DATA_LOG_LEVEL, f"[{tag}] {msg}")

    @classmethod
    def i(cls, tag: str, msg: str):
        """输出 info 日志。"""
        cls._logger.info(f"[{tag}] {msg}")

    @classmethod
    def w(cls, tag: str, msg: str):
        """输出 warning 日志。"""
        cls._logger.warning(f"[{tag}] {msg}")

    @classmethod
    def e(cls, tag: str, msg: str):
        """输出 error 日志。"""
        cls._logger.error(f"[{tag}] {msg}")

    @classmethod
    def exception(cls, tag: str, msg: str = ""):
        """输出 exception 日志，包含当前异常堆栈。"""
        cls._logger.exception(f"[{tag}] {msg}")
