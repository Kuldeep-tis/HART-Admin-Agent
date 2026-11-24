"""Is this computer set up to run the local model at full speed?

Laptops slow the CPU down to save energy. On the 8 GB test laptop, Ubuntu's "Power Saver"
mode made the model about 3x slower (first words 10.7 s instead of 4.4 s), so the app and
the benchmark check the power mode and the charger and warn when either will slow replies.
"""

import platform
import subprocess
from pathlib import Path

# Linux CPU energy setting (intel_pstate / amd-pstate) -> the names Ubuntu's Settings uses.
_EPP_TO_MODE = {
    "performance": "performance",
    "balance_performance": "balanced",
    "default": "balanced",
    "balance_power": "power-saver",
    "power": "power-saver",
}


def _run(*cmd):
    """Output of a command, or "" if it doesn't exist on this computer."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def power_source():
    """'charger', 'battery', 'desktop (no battery)' or 'unknown'."""
    system = platform.system()
    if system == "Linux":
        supplies = Path("/sys/class/power_supply")
        for online in supplies.glob("*/online") if supplies.exists() else []:
            try:
                if (online.parent / "type").read_text().strip() == "Mains":
                    return "charger" if online.read_text().strip() == "1" else "battery"
            except OSError:
                continue
        return "desktop (no battery)"
    if system == "Darwin":
        text = _run("pmset", "-g", "batt")
        return "charger" if "AC Power" in text else "battery" if "Battery Power" in text else "unknown"
    return "unknown"


def power_mode():
    """'performance', 'balanced', 'power-saver' or 'unknown'."""
    system = platform.system()
    if system == "Linux":
        # Ubuntu 22.04+ (Settings -> Power -> Power Mode)
        mode = _run("powerprofilesctl", "get")
        if mode in ("performance", "balanced", "power-saver"):
            return mode
        # Older systems (e.g. Ubuntu 20.04): read the CPU's energy setting directly.
        try:
            epp = Path("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference").read_text().strip()
            return _EPP_TO_MODE.get(epp, "unknown")
        except OSError:
            return "unknown"
    if system == "Darwin":
        # macOS Low Power Mode: "lowpowermode 1" in `pmset -g`.
        for line in _run("pmset", "-g").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "lowpowermode":
                return "power-saver" if parts[1] == "1" else "balanced"
    return "unknown"


def speed_warnings(mode=None, source=None):
    """Plain-language warnings when the power settings will slow the local model down."""
    mode = mode or power_mode()
    source = source or power_source()
    warnings = []
    if mode == "power-saver":
        warnings.append("Power Saver mode is on: the local model replies about 3x slower. "
                        "Switch to Performance (Settings -> Power, or: powerprofilesctl set performance).")
    if source == "battery":
        warnings.append("Running on battery: the local model is slower. Plug in the charger.")
    return warnings
