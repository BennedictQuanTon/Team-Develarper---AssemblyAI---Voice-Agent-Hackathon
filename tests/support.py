"""Shared isolation for the test suite.

The real `.env` holds live API keys and `config.py` reads it from the working directory, so every test
switches the settings file off, blanks the keys (which selects the stub providers) and blocks real
network access. Nothing here needs Ollama, AssemblyAI, Gemini or Cartesia.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
import tempfile
import unittest
from typing import Any
from unittest import mock

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

TEST_ENV: dict[str, str] = {
    "ASSEMBLYAI_API_KEY": "",
    "GEMINI_API_KEY": "",
    "CARTESIA_API_KEY": "",
    "AGENT_MODE": "waiter",
    "LLM_PROVIDER": "ollama",
    "OLLAMA_WARMUP": "false",
    "WAITER_TEMPLATE_REPLIES": "false",
    # langchain-core pulls in langsmith; make sure no test ever traces to it.
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
}


class NetworkBlocked(RuntimeError):
    """A test tried to reach a real network host."""


def _is_loopback(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    return host is None or str(host).strip("[]") in LOOPBACK_HOSTS


def _blocked_sync(self: Any, request: Any) -> Any:
    raise NetworkBlocked(f"real HTTP request in a test: {request.method} {request.url}")


async def _blocked_async(self: Any, request: Any) -> Any:
    raise NetworkBlocked(f"real HTTP request in a test: {request.method} {request.url}")


class NetworkGuard:
    """Fail loudly on real network access, while leaving mocks and loopback working.

    The real HTTP transports of both `httpx` (ollama client) and `httpx2` (starlette's TestClient,
    langsmith) are blocked; `MockTransport` and TestClient's in-process transport are separate classes and
    keep working. Name resolution and direct connects are refused for anything but loopback, which the
    Windows Proactor event loop needs for its internal socketpair.
    """

    def __init__(self) -> None:
        self._patches: list[Any] = []

    def start(self) -> None:
        targets: list[tuple[Any, str, Any]] = []
        for module_name in ("httpx", "httpx2"):
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            targets.append((module.HTTPTransport, "handle_request", _blocked_sync))
            targets.append((module.AsyncHTTPTransport, "handle_async_request", _blocked_async))
        for owner, attr, replacement in targets:
            self._patches.append(mock.patch.object(owner, attr, replacement))

        real_getaddrinfo = socket.getaddrinfo
        real_create_connection = socket.create_connection

        def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if _is_loopback(host):
                return real_getaddrinfo(host, *args, **kwargs)
            raise NetworkBlocked(f"test tried to resolve {host!r}")

        def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
            if _is_loopback(address[0]):
                return real_create_connection(address, *args, **kwargs)
            raise NetworkBlocked(f"test tried to connect to {address!r}")

        self._patches.append(mock.patch.object(socket, "getaddrinfo", guarded_getaddrinfo))
        self._patches.append(mock.patch.object(socket, "create_connection", guarded_create_connection))
        for patcher in self._patches:
            patcher.start()

    def stop(self) -> None:
        while self._patches:
            self._patches.pop().stop()


def reset_caches() -> None:
    """Clear every process-wide cache a test could leak into the next one."""
    from backend.app.config import get_settings
    from backend.app.pipeline import llm_live

    get_settings.cache_clear()
    if hasattr(llm_live._gemini_limiter, "_bucket"):
        del llm_live._gemini_limiter._bucket
    llm_ollama = sys.modules.get("backend.app.pipeline.llm_ollama")
    if llm_ollama is not None:
        llm_ollama.reset_model_cache()
        llm_ollama.set_last_probe(None)
    main = sys.modules.get("backend.app.main")
    if main is not None and hasattr(main, "get_orchestrator"):
        main.get_orchestrator.cache_clear()


def isolate(case: unittest.TestCase, **env: str) -> str:
    """Isolate one test from `.env`, live keys, shared caches and the network.

    Returns a temporary directory used as `METRICS_DIR`; it is removed when the test finishes.
    """
    from backend.app.config import Settings

    tmp = tempfile.TemporaryDirectory(prefix="lantern-test-")
    case.addCleanup(tmp.cleanup)
    patches = [
        mock.patch.dict(os.environ, {**TEST_ENV, "METRICS_DIR": tmp.name, **env}),
        mock.patch.dict(Settings.model_config, {"env_file": None}),
    ]
    for patcher in patches:
        patcher.start()
        case.addCleanup(patcher.stop)
    guard = NetworkGuard()
    guard.start()
    case.addCleanup(guard.stop)
    reset_caches()
    case.addCleanup(reset_caches)
    return tmp.name


class IsolatedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp_dir = isolate(self)


class IsolatedAsyncTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp_dir = isolate(self)


def fresh_store() -> Any:
    """A new store read from `data/lantern`, never the process-wide singleton.

    `set_available` on the singleton leaks a sold-out dish into every later test and every live session.
    """
    from backend.app.domain.lantern import LanternStore

    return LanternStore()
