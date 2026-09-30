import asyncio
import json
import re
import signal
import socket
from pathlib import Path

import h11

from crosstalk.capture.pcap import read_interface_stats
from crosstalk.common.artifacts import atomic_write
from crosstalk.common.errors import CaptureError
from crosstalk.common.ids import is_ulid


class CaptureSupervisor:
    def __init__(self, root: Path, interface: str | None = None) -> None:
        self.root = root
        self.interface = interface or self._interface()
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None
        self.command: list[str] = []
        self.offloads = ""
        self.mtu = 0
        self.capture_file: Path | None = None

    @staticmethod
    def _interface() -> str:
        names = [name for _, name in socket.if_nameindex() if name != "lo"]
        if len(names) != 1:
            raise CaptureError("Specify the measured interface explicitly")
        return names[0]

    async def _command(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
        if process.returncode != 0:
            raise CaptureError(f"{' '.join(args)} failed: {stderr.decode(errors='replace')}")
        return stdout.decode(errors="replace")

    async def _quiescent(self) -> bool:
        interface = Path("/sys/class/net") / self.interface / "statistics"
        previous: tuple[int, int] | None = None
        stable = 0
        for _ in range(40):
            current = (
                int((interface / "rx_packets").read_text().strip()),
                int((interface / "tx_packets").read_text().strip()),
            )
            stable = stable + 1 if current == previous else 0
            if stable >= 4:
                return True
            previous = current
            await asyncio.sleep(0.05)
        return False

    async def start(
        self, session_id: str, bpf: str = "tcp port 8443", ring_mb: int = 512
    ) -> dict[str, object]:
        if not is_ulid(session_id):
            raise CaptureError("Session ID must be a ULID")
        if bpf != "tcp port 8443" or not 64 <= ring_mb <= 4096:
            raise CaptureError("Invalid capture filter or ring size")
        if self.process is not None:
            raise CaptureError("Capture already running")
        await self._command(
            "ethtool", "-K", self.interface, "gro", "off", "lro", "off", "tso", "off", "gso", "off"
        )
        self.offloads = await self._command("ethtool", "-k", self.interface)
        self.mtu = int((await self._command("cat", f"/sys/class/net/{self.interface}/mtu")).strip())
        output = self.root / "sessions" / session_id / "pcap"
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise CaptureError("Capture directory is not empty")
        self.capture_file = output / "cap.pcapng"
        self.command = [
            "dumpcap",
            "-i",
            self.interface,
            "-f",
            bpf,
            "-s",
            "0",
            "-B",
            "64",
            "-n",
            "-w",
            str(self.capture_file),
            "-b",
            f"filesize:{ring_mb * 1024}",
            "-b",
            "files:2",
            "-q",
        ]
        self.process = await asyncio.create_subprocess_exec(
            *self.command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        async def ready() -> None:
            while not any(output.glob("cap*.pcapng")):
                if self.process is None or self.process.returncode is not None:
                    raise CaptureError("dumpcap exited at start")
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(ready(), timeout=5)
        except (TimeoutError, CaptureError):
            if self.process.returncode is None:
                self.process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self.process.communicate(), timeout=5)
            self.process = None
            self.capture_file = None
            raise
        self.session_id = session_id
        return {"interface": self.interface, "mtu": self.mtu}

    async def stop(self, session_id: str) -> dict[str, object]:
        if self.process is None or self.session_id != session_id or self.capture_file is None:
            raise CaptureError("No matching active capture")
        try:
            quiescent = await self._quiescent()
        except OSError:
            quiescent = False
        if self.process.returncode is None:
            self.process.send_signal(signal.SIGINT)
        forced_stop = False
        try:
            _, stderr = await asyncio.wait_for(self.process.communicate(), timeout=30)
        except TimeoutError:
            self.process.kill()
            _, stderr = await self.process.communicate()
            forced_stop = True
        status = self.process.returncode
        stderr_text = stderr.decode(errors="replace")
        files = sorted(self.capture_file.parent.glob("cap*.pcapng"))
        stats = [read_interface_stats(path) for path in files]
        reported = re.search(
            r"Packets received/dropped on interface [^:]+:\s*\d+/(\d+)\s*\(([^)]*)\)",
            stderr_text,
        )
        components = (
            {name: int(value) for name, value in re.findall(r"([a-z_]+):(\d+)", reported.group(2))}
            if reported
            else {}
        )
        stat_ifdrop = [item.interface_drops for item in stats if item.interface_drops is not None]
        stat_osdrop = [item.os_drops for item in stats if item.os_drops is not None]
        result: dict[str, object] = {
            "session_id": session_id,
            "interface": self.interface,
            "mtu": self.mtu,
            "offloads": self.offloads,
            "command": self.command,
            "exit_code": status,
            "forced_stop": forced_stop,
            "stderr": stderr_text,
            "files": [str(path) for path in files],
            "ring_full": len(files) >= 2,
            "interface_quiescent": quiescent,
            "kernel_drops": int(reported.group(1)) if reported else None,
            "interface_drops": sum(stat_ifdrop) if stat_ifdrop else components.get("ps_ifdrop"),
            "os_drops": sum(stat_osdrop)
            if stat_osdrop
            else sum(components[name] for name in ("pcap", "dumpcap", "flushed"))
            if all(name in components for name in ("pcap", "dumpcap", "flushed"))
            else None,
        }
        atomic_write(
            self.root / "sessions" / session_id / "capture.json",
            json.dumps(result, indent=2).encode(),
        )
        self.process = None
        self.session_id = None
        self.capture_file = None
        return result


async def serve_control(root: Path, interface: str | None = None) -> None:
    supervisor = CaptureSupervisor(root, interface)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = h11.Connection(h11.SERVER)
        request: h11.Request | None = None
        body = bytearray()
        result: dict[str, object] = {}
        status = 400
        try:
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    data = await asyncio.wait_for(reader.read(4096), timeout=10)
                    if not data:
                        raise CaptureError("Incomplete control request")
                    connection.receive_data(data)
                elif isinstance(event, h11.Request):
                    request = event
                elif isinstance(event, h11.Data):
                    body.extend(event.data)
                    if len(body) > 4096:
                        raise CaptureError("Control request too large")
                elif isinstance(event, h11.EndOfMessage):
                    break
            if request is None or request.method != b"POST":
                raise CaptureError("POST required")
            data = json.loads(body)
            session_id = data["session_id"]
            if request.target == b"/capture/start":
                result = await supervisor.start(
                    session_id,
                    bpf=data.get("bpf", "tcp port 8443"),
                    ring_mb=data.get("ring_mb", 512),
                )
            elif request.target == b"/capture/stop":
                result = await supervisor.stop(session_id)
            else:
                raise CaptureError("Unknown control path")
            status = 200
        except (CaptureError, KeyError, OSError, ValueError, TimeoutError) as error:
            result = {"error": str(error)}
            status = 400
        finally:
            response = json.dumps(result).encode()
            writer.write(
                connection.send(
                    h11.Response(
                        status_code=status,
                        headers=[
                            (b"Content-Type", b"application/json"),
                            (b"Content-Length", str(len(response)).encode()),
                            (b"Connection", b"close"),
                        ],
                    )
                )
            )
            writer.write(connection.send(h11.Data(data=response)))
            writer.write(connection.send(h11.EndOfMessage()))
            await writer.drain()
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 9200)
    async with server:
        await server.serve_forever()
