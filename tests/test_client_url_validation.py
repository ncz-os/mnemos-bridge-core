from __future__ import annotations

import traceback
from contextlib import asynccontextmanager

import pytest

import mnemos_bridge_core.client as client_module
from mnemos_bridge_core import McpClient


def test_plaintext_http_is_rejected_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MNEMOS_ALLOW_INSECURE_TRANSPORT", raising=False)

    with pytest.raises(ValueError, match="plaintext"):
        McpClient.from_url("http://192.168.207.67:5003/sse", token="mnemos_sk_test")


async def test_plaintext_http_is_rejected_before_network_io(monkeypatch) -> None:
    monkeypatch.delenv("MNEMOS_ALLOW_INSECURE_TRANSPORT", raising=False)
    transport_started = False

    @asynccontextmanager
    async def fake_sse_client(*args, **kwargs):
        nonlocal transport_started
        transport_started = True
        yield object(), object()

    monkeypatch.setattr(client_module, "sse_client", fake_sse_client)

    with pytest.raises(ValueError, match="plaintext"):
        await McpClient.open_from_url("http://192.168.207.67:5003/sse", token="mnemos_sk_test")

    assert transport_started is False


async def test_plaintext_lan_connection_works_with_explicit_opt_in(monkeypatch) -> None:
    connected_urls = []

    @asynccontextmanager
    async def fake_sse_client(url, **kwargs):
        connected_urls.append(url)
        yield object(), object()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            return None

    monkeypatch.setattr(client_module, "sse_client", fake_sse_client)
    monkeypatch.setattr(client_module, "ClientSession", lambda *args, **kwargs: FakeSession())

    with pytest.warns(UserWarning, match="trusted LAN"):
        client = await McpClient.open_from_url(
            "http://192.168.207.67:5003/sse",
            token="mnemos_sk_test",
            allow_insecure_transport=True,
        )
    await client.aclose()

    assert connected_urls == ["http://192.168.207.67:5003/sse"]
    assert client.allow_insecure_transport is True


def test_plaintext_lan_connection_accepts_environment_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("MNEMOS_ALLOW_INSECURE_TRANSPORT", "1")

    with pytest.warns(UserWarning, match="trusted LAN"):
        client = McpClient.from_url("http://192.168.207.67:5003/sse", token="mnemos_sk_test")

    assert client.allow_insecure_transport is True


def test_explicit_false_overrides_environment_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("MNEMOS_ALLOW_INSECURE_TRANSPORT", "1")

    with pytest.raises(ValueError, match="plaintext"):
        McpClient.from_url(
            "http://192.168.207.67:5003/sse",
            token="mnemos_sk_test",
            allow_insecure_transport=False,
        )


@pytest.mark.parametrize("url", ["ftp://mnemos.example.com/sse", "file:///etc/passwd", "mnemos.example.com/sse"])
def test_non_http_schemes_are_rejected_even_with_insecure_opt_in(url: str) -> None:
    with pytest.raises(ValueError, match="https://"):
        McpClient.from_url(url, token="mnemos_sk_test", allow_insecure_transport=True)


def test_url_without_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="host"):
        McpClient.from_url("https:///sse", token="mnemos_sk_test")


def test_insecure_transport_warning_redacts_url_secrets() -> None:
    url = "http://TEST_USER:TEST_PASSWORD@192.168.207.67:5003/sse?api_key=TEST_QUERY_SECRET"

    with pytest.warns(UserWarning) as recorded:
        McpClient.from_url(url, token="mnemos_sk_test", allow_insecure_transport=True)

    warning = str(recorded[0].message)
    assert "192.168.207.67:5003" in warning
    assert "TEST_USER" not in warning
    assert "TEST_PASSWORD" not in warning
    assert "TEST_QUERY_SECRET" not in warning


def test_malformed_url_error_traceback_redacts_url_secrets() -> None:
    url = "http://TEST_USER:TEST_PASSWORD@192.168.207.67:TEST_PORT_SECRET/sse?api_key=TEST_QUERY_SECRET"

    with pytest.raises(ValueError, match="invalid") as caught:
        McpClient.from_url(url, token="mnemos_sk_test", allow_insecure_transport=True)

    formatted = "".join(traceback.format_exception(caught.value))
    assert "TEST_USER" not in formatted
    assert "TEST_PASSWORD" not in formatted
    assert "TEST_PORT_SECRET" not in formatted
    assert "TEST_QUERY_SECRET" not in formatted


def test_from_url_preserves_legacy_subclass_constructor_signature() -> None:
    class LegacyClient(McpClient):
        def __init__(self, url: str, *, token: str, timeout: float = 30) -> None:
            super().__init__(url, token=token, timeout=timeout)

    client = LegacyClient.from_url("https://mnemos.example.com/sse", token="mnemos_sk_test")

    assert isinstance(client, LegacyClient)


async def test_open_from_url_preserves_legacy_subclass_factory_signature(monkeypatch) -> None:
    @asynccontextmanager
    async def fake_sse_client(*args, **kwargs):
        yield object(), object()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def initialize(self):
            return None

    class LegacyFactoryClient(McpClient):
        @classmethod
        def from_url(cls, url: str, *, token: str, timeout: float = 30):
            return cls(url, token=token, timeout=timeout)

    monkeypatch.setattr(client_module, "sse_client", fake_sse_client)
    monkeypatch.setattr(client_module, "ClientSession", lambda *args, **kwargs: FakeSession())

    client = await LegacyFactoryClient.open_from_url("https://mnemos.example.com/sse", token="mnemos_sk_test")
    await client.aclose()

    assert isinstance(client, LegacyFactoryClient)
