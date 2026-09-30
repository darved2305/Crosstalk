import asyncio
import socket
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

from crosstalk.common.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ObservedRecord:
    content_type: int
    length: int
    received_ns: int


class TLSClient:
    def __init__(self, host: str, port: int, context: ssl.SSLContext) -> None:
        self.host = host
        self.port = port
        self.context = context
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.incoming = ssl.MemoryBIO()
        self.outgoing = ssl.MemoryBIO()
        self.tls = context.wrap_bio(self.incoming, self.outgoing, server_hostname=host)
        self._cipher_buffer = bytearray()
        self.records: list[ObservedRecord] = []
        self.t_connect_ns = 0
        self.t_tls_ns = 0
        self.client_port = 0

    async def __aenter__(self) -> "TLSClient":
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(loop.sock_connect(self.socket, (self.host, self.port)), timeout=5)
        self.t_connect_ns = time.monotonic_ns()
        self.client_port = self.socket.getsockname()[1]
        while True:
            try:
                self.tls.do_handshake()
                await self._flush()
                self.t_tls_ns = time.monotonic_ns()
                if (
                    self.tls.version() != "TLSv1.3"
                    or self.tls.selected_alpn_protocol() != "http/1.1"
                ):
                    raise ValidationError("Unexpected TLS protocol")
                return self
            except ssl.SSLWantReadError:
                await self._flush()
                await self._fill()
            except ssl.SSLWantWriteError:
                await self._flush()

    async def __aexit__(self, *_: object) -> None:
        self.socket.close()

    async def _flush(self) -> None:
        loop = asyncio.get_running_loop()
        while data := self.outgoing.read():
            await asyncio.wait_for(loop.sock_sendall(self.socket, data), timeout=30)

    async def _fill(self) -> None:
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(loop.sock_recv(self.socket, 65_536), timeout=30)
        if not raw:
            self.incoming.write_eof()
            return
        now = time.monotonic_ns()
        self._cipher_buffer.extend(raw)
        while len(self._cipher_buffer) >= 5:
            length = int.from_bytes(self._cipher_buffer[3:5], "big")
            if length > 16_640:
                raise ValidationError("Oversized TLS record observed")
            if len(self._cipher_buffer) < length + 5:
                break
            self.records.append(ObservedRecord(self._cipher_buffer[0], length, now))
            del self._cipher_buffer[: length + 5]
        self.incoming.write(raw)

    async def send(self, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            try:
                offset += self.tls.write(data[offset:])
                await self._flush()
            except ssl.SSLWantReadError:
                await self._flush()
                await self._fill()
            except ssl.SSLWantWriteError:
                await self._flush()

    async def recv(self) -> tuple[bytes, int]:
        while True:
            try:
                result = self.tls.read(65_536)
                return result, time.monotonic_ns()
            except ssl.SSLWantReadError:
                await self._flush()
                await self._fill()
            except ssl.SSLWantWriteError:
                await self._flush()


def client_context(ca_file: str | None = None, keylog_file: Path | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["http/1.1"])
    if keylog_file is not None:
        keylog_file.parent.mkdir(parents=True, exist_ok=True)
        if keylog_file.exists():
            raise ValidationError(f"Keylog already exists: {keylog_file}")
        context.keylog_filename = str(keylog_file)
    return context
