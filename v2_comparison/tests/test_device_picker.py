from v2_comparison.device_picker import PickableDevice, prompt_device_selection


def test_prompt_device_selection_auto_picks_single_device() -> None:
    device = PickableDevice(name="Sync-01", address="AA:BB:CC:DD:EE:FF", rssi=-60, payload=object())
    selected = prompt_device_selection([device])
    assert selected is device
