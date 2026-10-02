from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from boostr_kyc.client import BoostrClient

Handler = Callable[[httpx.Request], httpx.Response]


def ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": data})


def err(code: str, message: str = "", status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json={"status": "error", "data": "", "code": code, "message": message}
    )


class FakeBoostr:
    """Simula la API de Boostr por ruta y registra cada llamada."""

    def __init__(self, routes: dict[str, httpx.Response | Handler]) -> None:
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return err("U-04", "no encontrado")
        return route(request) if callable(route) else route

    def client(self, **kwargs) -> BoostrClient:
        return BoostrClient(
            "test-key", transport=httpx.MockTransport(self), window_seconds=0.01, **kwargs
        )

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.calls if r.content]


@pytest.fixture
def fake_boostr() -> Callable[[dict], FakeBoostr]:
    return FakeBoostr
