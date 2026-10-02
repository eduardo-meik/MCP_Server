"""Cliente asíncrono de la API de Boostr (https://docs.boostr.cl).

- Autenticación con header X-API-KEY (secreto de Secret Manager, nunca del usuario).
- Respeta el límite público de 5 requests / 10 s para no quedar bloqueado.
- Traduce los códigos de error de Boostr a estados que el motor de riesgo entiende.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

CheckStatus = Literal[
    "found",  # la fuente respondió con datos
    "not_found",  # la fuente respondió que no hay registros (ej. no es PEP)
    "unavailable",  # la fuente original no respondió: resultado no concluyente
    "invalid",  # dato de entrada rechazado
    "forbidden",  # la API key no tiene contratado el servicio
    "rate_limited",
    "error",
]

# Códigos documentados por Boostr, por endpoint.
_NOT_FOUND_CODES = {"U-04", "U-12", "PJUD-03"}
_UNAVAILABLE_CODES = {"U-03", "U-10", "U-13", "PJUD-02"}
_INVALID_CODES = {"U-02", "U-20", "PJUD-01"}
_FORBIDDEN_CODES = {"U-06"}


@dataclass(frozen=True)
class CheckResult:
    status: CheckStatus
    data: dict[str, Any] | None = None
    code: str | None = None
    message: str | None = None

    @property
    def conclusive(self) -> bool:
        return self.status in ("found", "not_found")


@dataclass
class _SlidingWindow:
    max_requests: int
    window_seconds: float
    _calls: deque[float] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_requests:
                    self._calls.append(now)
                    return
                await asyncio.sleep(self.window_seconds - (now - self._calls[0]) + 0.05)


class BoostrClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.boostr.cl",
        timeout: float = 20.0,
        cache_ttl_seconds: int = 3600,
        max_requests: int = 5,
        window_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"X-API-KEY": api_key, "Accept": "application/json"},
            transport=transport,
        )
        self._limiter = _SlidingWindow(max_requests, window_seconds)
        self._cache_ttl = cache_ttl_seconds
        # Caché por instancia: evita pagar dos veces la misma consulta en una sesión.
        self._cache: dict[str, tuple[float, CheckResult]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Rutificador --------------------------------------------------------
    async def sii_name(self, rut: str) -> CheckResult:
        return await self._get(f"/rut/name/{rut}.json")

    async def pep(self, rut: str) -> CheckResult:
        return await self._get(f"/rut/pep/{rut}.json")

    async def interpol(self, rut: str) -> CheckResult:
        return await self._get(f"/rut/interpol/{rut}.json")

    async def deceased(self, rut: str) -> CheckResult:
        return await self._get(f"/rut/deceased/{rut}.json")

    async def verify_id_card(self, rut: str, serial_number: str) -> CheckResult:
        return await self._get(f"/rut/verify-serial/{rut}/{serial_number}.json")

    # --- Telefonía ----------------------------------------------------------
    async def phone_carrier(self, phone: str) -> CheckResult:
        return await self._get(f"/telephone/carrier_lookup/{phone}.json")

    # --- Poder Judicial (asíncrono) ----------------------------------------
    async def start_court_search(self, payload: dict[str, Any]) -> CheckResult:
        return await self._request("POST", "/poder_judicial/causes.json", json=payload)

    async def court_search_status(self, operation_id: str) -> CheckResult:
        return await self._request(
            "GET", "/poder_judicial/causes.json", params={"operation_id": operation_id}
        )

    # --- Interno ------------------------------------------------------------
    async def _get(self, path: str) -> CheckResult:
        cached = self._cache.get(path)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]
        result = await self._request("GET", path)
        if result.conclusive:
            self._cache[path] = (time.monotonic(), result)
        return result

    async def _request(self, method: str, path: str, **kwargs: Any) -> CheckResult:
        await self._limiter.acquire()
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            return CheckResult("unavailable", message=f"Boostr no respondió: {type(exc).__name__}")
        return parse_response(response)


def parse_response(response: httpx.Response) -> CheckResult:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 429 or body.get("code") == "RATELIMIT":
        return CheckResult("rate_limited", code="RATELIMIT", message="Límite de Boostr excedido")
    if body.get("status") == "success" and isinstance(body.get("data"), dict):
        return CheckResult("found", data=body["data"])

    code, message = body.get("code"), body.get("message")
    if response.status_code == 401 or code in _FORBIDDEN_CODES:
        return CheckResult("forbidden", code=code, message=message or "Servicio no contratado")
    if code in _NOT_FOUND_CODES:
        return CheckResult("not_found", code=code, message=message)
    if code in _UNAVAILABLE_CODES or response.status_code >= 500:
        return CheckResult("unavailable", code=code, message=message)
    if code in _INVALID_CODES or response.status_code in (400, 422):
        return CheckResult("invalid", code=code, message=message)
    return CheckResult("error", code=code, message=message or f"HTTP {response.status_code}")
