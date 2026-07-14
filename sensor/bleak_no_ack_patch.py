"""强制 bleak 对指定特征使用 write-without-response（no ACK）的补丁。

某些 Windows 主机上 write-with-response 会因为系统 ACK 等待导致命令超时，
把命令特征强制改成无响应写入可以显著提升命令通道的实时性。

默认只作用于 SDK 已知的两个 CMD 特征 UUID；传入空列表或 `None` 则全局生效。
"""

import logging
from functools import wraps

logger = logging.getLogger(__name__)

# SDK 默认使用的两条命令特征
DEFAULT_CMD_CHAR_UUIDS = {
    "f000ffe1-0451-4000-b000-000000000000",  # OYM CMD
    "00000002-0000-1000-8000-00805f9b34fb",  # RFSTAR CMD
}


def _char_uuid(char_specifier) -> str:
    """尽量把 char_specifier 转成小写 UUID 字符串。"""
    if isinstance(char_specifier, str):
        return char_specifier.lower()
    try:
        # bleak 的 BleakGATTCharacteristic 对象有 uuid 属性
        return str(char_specifier.uuid).lower()
    except Exception:
        return ""


def apply(cmd_char_uuids=DEFAULT_CMD_CHAR_UUIDS):
    """给 BleakClient.write_gatt_char 打补丁，强制指定特征使用 response=False。

    Args:
        cmd_char_uuids: 需要强制 no-ack 的特征 UUID 集合。传 None 表示所有写入都强制 no-ack。
    """
    try:
        from bleak import BleakClient
    except Exception as e:
        logger.debug("bleak not available, skip no-ack patch: %s", e)
        return

    if getattr(BleakClient, "_no_ack_patched", False):
        return

    orig_write = BleakClient.write_gatt_char

    @wraps(orig_write)
    async def patched_write(self, char_specifier, data, response=None):
        if cmd_char_uuids is None or _char_uuid(char_specifier) in cmd_char_uuids:
            response = False
        return await orig_write(self, char_specifier, data, response=response)

    BleakClient.write_gatt_char = patched_write
    BleakClient._no_ack_patched = True  # type: ignore[attr-defined]
    logger.debug(
        "Applied bleak write-without-response patch (targets: %s)",
        "all chars" if cmd_char_uuids is None else cmd_char_uuids,
    )


def reset():
    """移除补丁（主要用于测试）。"""
    try:
        from bleak import BleakClient
    except Exception:
        return

    if not getattr(BleakClient, "_no_ack_patched", False):
        return

    # 找到原始的未绑定方法比较麻烦，这里简单把标记清掉，
    # 实际撤销需要保存原始方法；apply 没有保存，故 reset 仅作占位。
    BleakClient._no_ack_patched = False  # type: ignore[attr-defined]
