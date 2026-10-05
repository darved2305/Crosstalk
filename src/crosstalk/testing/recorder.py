"""Test-only capture substitute for hosts without dumpcap.

A TCP relay between the load generator and the shaper records every ciphertext chunk with
a nanosecond timestamp, then synthesises a pcapng (Ethernet/IPv4/TCP, MSS-sized segments,
SYN and FIN) that the production reconstruction code parses. It exercises the full
observer pipeline end to end, but it is not packet capture: segment boundaries are
synthetic and sessions recorded this way are never reportable.
"""

import asyncio
import ipaddress
import struct
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from crosstalk.common.errors import CaptureError
from crosstalk.loadgen.provenance import CaptureMetadata

SERVER_PORT = 8443
CLIENT_IP = ipaddress.IPv4Address("10.0.0.2").packed
SERVER_IP = ipaddress.IPv4Address("10.0.0.3").packed
CLIENT_ISN = 1_000
SERVER_ISN = 500_000
SYN, FIN, PSH_ACK, SYN_ACK, ACK, FIN_ACK = 0x02, 0x01, 0x18, 0x12, 0x10, 0x11


@dataclass(slots=True)
class _Connection:
    client_port: int
    opened_ns: int
    chunks: list[tuple[int, int, bytes]] = field(default_factory=lambda: [])
    eof_ns: dict[int, int] = field(default_factory=lambda: {})
    done: asyncio.Event = field(default_factory=asyncio.Event)


class RecordingRelay:
    def __init__(self, target_host: str, target_port: int, root: Path, mss: int = 1448) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.root = root
        self.mss = mss
        self.connections: list[_Connection] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def serve(self, host: str = "127.0.0.1") -> int:
        self.server = await asyncio.start_server(self._accept, host, 0)
        self.port = int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = _Connection(int(writer.get_extra_info("peername")[1]), time.time_ns())
        self.connections.append(connection)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                self.target_host, self.target_port
            )
            await asyncio.gather(
                self._pump(reader, upstream_writer, connection, -1),
                self._pump(upstream_reader, writer, connection, 1),
            )
        except OSError:
            writer.close()
        finally:
            connection.done.set()

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        connection: _Connection,
        direction: int,
    ) -> None:
        try:
            while data := await reader.read(65_536):
                connection.chunks.append((time.time_ns(), direction, data))
                writer.write(data)
                await writer.drain()
        except OSError:
            # Windows reports some peer closes as plain OSError (e.g. WinError 64).
            pass
        finally:
            connection.eof_ns[direction] = time.time_ns()
            with suppress(OSError):
                if writer.can_write_eof():
                    writer.write_eof()
                else:
                    writer.close()

    async def start(self, session_id: str) -> CaptureMetadata:
        self.connections = []
        return self._metadata()

    async def stop(self, session_id: str) -> CaptureMetadata:
        try:
            async with asyncio.timeout(30):
                for connection in list(self.connections):
                    await connection.done.wait()
        except TimeoutError:
            pending = [
                {
                    "client_port": c.client_port,
                    "chunks": len(c.chunks),
                    "closed": sorted(c.eof_ns),
                }
                for c in self.connections
                if not c.done.is_set()
            ]
            raise CaptureError(f"Relay connections did not close: {pending}") from None
        target = self.root / "sessions" / session_id / "pcap" / "cap.pcapng"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise CaptureError(f"Capture already exists: {target}")
        target.write_bytes(self.pcapng())
        return self._metadata()

    def _metadata(self) -> CaptureMetadata:
        return CaptureMetadata(
            interface="recording-relay",
            mtu=self.mss + 52,
            offloads="synthetic segmentation (test relay)",
            command=["crosstalk.testing.recorder"],
            kernel_drops=0,
        )

    def pcapng(self) -> bytes:
        frames: list[tuple[int, int, bytes]] = []
        for number, connection in enumerate(self.connections):
            frames.extend(self._frames(number, connection))
        frames.sort(key=lambda frame: (frame[0], frame[1]))
        blocks = [_section_header(), _interface()]
        blocks += [_packet(timestamp, frame) for timestamp, _, frame in frames]
        return b"".join(blocks)

    def _frames(self, number: int, connection: _Connection) -> list[tuple[int, int, bytes]]:
        port = connection.client_port
        start = connection.opened_ns
        order = number * 1_000_000
        frames = [
            (start, order, _tcp(port, -1, CLIENT_ISN, SYN, b"")),
            (start + 1, order + 1, _tcp(port, 1, SERVER_ISN, SYN_ACK, b"")),
            (start + 2, order + 2, _tcp(port, -1, CLIENT_ISN + 1, ACK, b"")),
        ]
        offsets = {-1: CLIENT_ISN + 1, 1: SERVER_ISN + 1}
        sequence = order + 3
        for timestamp, direction, data in connection.chunks:
            for index in range(0, len(data), self.mss):
                segment = data[index : index + self.mss]
                frames.append(
                    (
                        timestamp,
                        sequence,
                        _tcp(port, direction, offsets[direction], PSH_ACK, segment),
                    )
                )
                offsets[direction] += len(segment)
                sequence += 1
        for direction in (-1, 1):
            if direction in connection.eof_ns:
                frames.append(
                    (
                        connection.eof_ns[direction],
                        sequence,
                        _tcp(port, direction, offsets[direction], FIN_ACK, b""),
                    )
                )
                sequence += 1
        return frames


def _tcp(client_port: int, direction: int, sequence: int, flags: int, payload: bytes) -> bytes:
    source, destination = (
        (client_port, SERVER_PORT) if direction == -1 else (SERVER_PORT, client_port)
    )
    addresses = CLIENT_IP + SERVER_IP if direction == -1 else SERVER_IP + CLIENT_IP
    tcp = struct.pack(
        "!HHIIBBHHH",
        source,
        destination,
        sequence & 0xFFFFFFFF,
        0,
        5 << 4,
        flags,
        65_535,
        0,
        0,
    )
    ip = struct.pack("!BBHHHBBH", 0x45, 0, 20 + len(tcp) + len(payload), 0, 0x4000, 64, 6, 0)
    ethernet = b"\x02\x00\x00\x00\x00\x02\x02\x00\x00\x00\x00\x01\x08\x00"
    return ethernet + ip + addresses + tcp + payload


def _block(kind: int, body: bytes) -> bytes:
    padding = b"\0" * ((-len(body)) % 4)
    length = 12 + len(body) + len(padding)
    return struct.pack("<II", kind, length) + body + padding + struct.pack("<I", length)


def _section_header() -> bytes:
    return _block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))


def _interface() -> bytes:
    # if_tsresol = 9: nanosecond timestamps; then opt_endofopt.
    options = struct.pack("<HHB3x", 9, 1, 9) + struct.pack("<HH", 0, 0)
    return _block(1, struct.pack("<HHI", 1, 0, 0) + options)


def _packet(timestamp_ns: int, frame: bytes) -> bytes:
    body = struct.pack(
        "<IIIII", 0, timestamp_ns >> 32, timestamp_ns & 0xFFFFFFFF, len(frame), len(frame)
    )
    return _block(6, body + frame)
