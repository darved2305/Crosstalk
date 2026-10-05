import asyncio
import datetime
import ipaddress
import json
import socket
from contextlib import suppress
from pathlib import Path

import h11
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from crosstalk.contracts.config import Mode
from crosstalk.loadgen.client import request
from crosstalk.loadgen.tls_client import TLSClient, client_context
from crosstalk.shaper.server import ShaperSettings, run, tls_context
from crosstalk.testing.mock_upstream import run as run_mock


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _certificate(tmp_path: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def test_tls_configuration(tmp_path: Path) -> None:
    cert, key = _certificate(tmp_path)
    context = tls_context(ShaperSettings(cert=cert, key=key))
    assert context.minimum_version == context.maximum_version
    assert context.num_tickets == 0


async def _wait_port(port: int) -> None:
    for _ in range(200):
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.01)
    raise AssertionError(f"Port {port} did not open")


@pytest.mark.asyncio
async def test_tls_passthrough_content_and_records(tmp_path: Path) -> None:
    cert, key = _certificate(tmp_path)
    mock_port = _port()
    shaper_port = _port()
    admin_port = _port()
    mock = asyncio.create_task(run_mock("127.0.0.1", mock_port))
    await _wait_port(mock_port)
    settings = ShaperSettings(
        host="127.0.0.1",
        port=shaper_port,
        admin_host="127.0.0.1",
        admin_port=admin_port,
        cert=cert,
        key=key,
        upstream_url=f"http://127.0.0.1:{mock_port}",
        oracle_dir=tmp_path / "oracle",
    )
    shaper = asyncio.create_task(run(settings))
    try:
        await _wait_port(admin_port)
        result = await request(
            "127.0.0.1",
            shaper_port,
            {
                "model": "mock",
                "messages": [{"role": "user", "content": "synthetic"}],
                "stream": True,
                "mock_events": 5,
                "mock_delay_ms": 1,
                "mock_text": "abc",
            },
            str(cert),
        )
        assert result.status == 200
        assert result.content == "abc" * 5
        assert len(result.events) == 6
        assert result.reads
        assert all(read.received_ns > result.t_sent_ns for read in result.reads)
        assert result.client_port > 0
        assert all(record.length <= 16_640 for record in result.records)
    finally:
        shaper.cancel()
        mock.cancel()
        for task in (shaper, mock):
            with suppress(asyncio.CancelledError):
                await task
    rows = [
        json.loads(line)
        for file in (tmp_path / "oracle").glob("*.ndjson")
        for line in file.read_text().splitlines()
    ]
    assert [row["kind"] for row in rows] == ["headers"] + ["content"] * 6 + ["terminator"]
    assert [row["seq"] for row in rows] == list(range(8))


@pytest.mark.asyncio
async def test_production_rejects_lab_headers(tmp_path: Path) -> None:
    cert, key = _certificate(tmp_path)
    shaper_port, admin_port = _port(), _port()
    settings = ShaperSettings(
        host="127.0.0.1",
        port=shaper_port,
        admin_host="127.0.0.1",
        admin_port=admin_port,
        cert=cert,
        key=key,
        mode=Mode.PROD,
        oracle_dir=tmp_path / "oracle",
    )
    shaper = asyncio.create_task(run(settings))
    try:
        await _wait_port(admin_port)
        async with TLSClient("127.0.0.1", shaper_port, client_context(str(cert))) as client:
            conn = h11.Connection(h11.CLIENT)
            payload = b'{"model":"mock","messages":[{"role":"user","content":"x"}],"stream":true}'
            raw = (
                conn.send(
                    h11.Request(
                        method=b"POST",
                        target=b"/v1/chat/completions",
                        headers=[
                            (b"Host", b"localhost"),
                            (b"Content-Type", b"application/json"),
                            (b"Content-Length", str(len(payload)).encode()),
                            (b"x-ct-arm", b"a0"),
                            (b"x-ct-rid", b"01ARZ3NDEKTSV4RRFFQ69G5FAV"),
                        ],
                    )
                )
                + conn.send(h11.Data(data=payload))
                + conn.send(h11.EndOfMessage())
            )
            await client.send(raw)
            response, _ = await client.recv()
            assert response.startswith(b"HTTP/1.1 400")
    finally:
        shaper.cancel()
        with suppress(asyncio.CancelledError):
            await shaper
