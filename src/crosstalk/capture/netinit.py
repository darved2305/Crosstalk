import json
import socket
import subprocess
from pathlib import Path

from crosstalk.common.artifacts import atomic_write
from crosstalk.common.errors import CaptureError


def configure(root: Path) -> None:
    interfaces = [name for _, name in socket.if_nameindex() if name != "lo"]
    if not interfaces:
        raise CaptureError("No non-loopback interface found")
    states: dict[str, str] = {}
    for interface in interfaces:
        subprocess.run(
            ["ethtool", "-K", interface, "tso", "off", "gso", "off", "gro", "off", "lro", "off"],
            check=True,
        )
        states[interface] = subprocess.run(
            ["ethtool", "-k", interface], capture_output=True, text=True, check=True
        ).stdout
    atomic_write(root / "offloads-shaper.json", json.dumps(states, indent=2).encode())


def impair(root: Path, interface: str) -> None:
    if interface == "lo" or interface not in {name for _, name in socket.if_nameindex()}:
        raise CaptureError("Impairment requires a non-loopback interface")
    command = [
        "tc",
        "qdisc",
        "replace",
        "dev",
        interface,
        "root",
        "netem",
        "loss",
        "1%",
        "delay",
        "10ms",
        "reorder",
        "5%",
    ]
    subprocess.run(command, check=True)
    atomic_write(
        root / "netem.json", json.dumps({"interface": interface, "command": command}).encode()
    )
