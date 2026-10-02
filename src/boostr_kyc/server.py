"""MCP de prueba: evaluación de prospectos y prevención de fraude con Boostr."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse

from boostr_kyc.client import BoostrClient, CheckResult
from boostr_kyc.rut import InvalidRut, Rut, parse_rut
from boostr_kyc.scoring import Assessment, assess
from mcp_core import CoreSettings, create_server, current_identity, require_role

logger = logging.getLogger("boostr_kyc")

JUDICIAL_ROLE = "kyc.judicial"
MAX_CAUSES = 50

INSTRUCTIONS = """\
Servidor para evaluar prospectos (personas naturales chilenas) y detectar señales de fraude
usando fuentes públicas a través de Boostr.

Flujo recomendado:
1. `validar_rut` (gratis) antes de cualquier consulta pagada.
2. `evaluar_prospecto` para un informe completo con recomendación.
3. Herramientas individuales solo si necesitas profundizar en una fuente.
4. `iniciar_busqueda_causas` + `ver_busqueda_causas` para antecedentes judiciales
   (requiere rol autorizado; el proceso es asíncrono y puede tardar minutos).

Cada consulta tiene costo y queda auditada. Úsalas solo con un propósito legítimo
(evaluación comercial o prevención de fraude) y no las repitas sin necesidad.
La recomendación es orientativa: la decisión final es de una persona.
"""

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": True}


class BoostrSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOOSTR_", env_file=".env", extra="ignore")

    api_key: SecretStr
    base_url: str = "https://api.boostr.cl"
    cache_ttl_seconds: int = 3600
    # Secreto para el webhook de Poder Judicial y para firmar los IDs de búsqueda.
    webhook_secret: SecretStr = SecretStr("cambiar-en-produccion")


class CheckOutput(BaseModel):
    rut: str | None = Field(None, description="RUT enmascarado")
    status: str = Field(
        description="found | not_found | unavailable | invalid | forbidden | rate_limited | error"
    )
    data: dict[str, Any] | None = None
    message: str | None = None


class RutValidation(BaseModel):
    valid: bool
    rut: str | None = Field(None, description="RUT normalizado, sin puntos")
    error: str | None = None


class CourtSearchStarted(BaseModel):
    search_id: str = Field(description="Usar en ver_busqueda_causas")
    message: str


class CourtCase(BaseModel):
    court: str | None = None
    rit: str | None = None
    labeled: str | None = None
    date: str | None = None
    status: str | None = None


class CourtSearchStatus(BaseModel):
    status: str = Field(description="pending | processing | done")
    progress: int | None = None
    total: int | None = None
    causes: list[CourtCase]
    truncated: bool = False


def _rut_or_error(value: str) -> Rut:
    try:
        return parse_rut(value)
    except InvalidRut as exc:
        raise ToolError(f"RUT inválido: {exc}") from exc


def _normalize_phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit())
    if digits.startswith("56") and len(digits) == 11:
        digits = digits[2:]
    if len(digits) != 9:
        raise ToolError("El teléfono debe tener 9 dígitos (ej. 912345678), con o sin +56")
    return digits


def _to_output(rut: Rut, result: CheckResult) -> CheckOutput:
    if result.status == "rate_limited":
        raise ToolError("Boostr limitó las consultas. Espera un minuto y vuelve a intentar.")
    return CheckOutput(
        rut=rut.masked, status=result.status, data=result.data, message=result.message
    )


def _sign(secret: str, *parts: str) -> str:
    return hmac.new(secret.encode(), "|".join(parts).encode(), hashlib.sha256).hexdigest()[:24]


def build_server(
    core: CoreSettings,
    boostr: BoostrSettings,
    client: BoostrClient | None = None,
) -> FastMCP:
    webhook_secret = boostr.webhook_secret.get_secret_value()
    if core.environment != "local" and webhook_secret == "cambiar-en-produccion":
        raise ValueError("BOOSTR_WEBHOOK_SECRET debe definirse fuera de local")
    mcp = create_server(core, instructions=INSTRUCTIONS)
    api = client or BoostrClient(
        boostr.api_key.get_secret_value(),
        base_url=boostr.base_url,
        cache_ttl_seconds=boostr.cache_ttl_seconds,
    )

    # --- Verificaciones -----------------------------------------------------
    @mcp.tool(annotations={**READ_ONLY, "openWorldHint": False})
    def validar_rut(
        rut: Annotated[str, Field(description="RUT con dígito verificador, ej. 12.345.678-5")],
    ) -> RutValidation:
        """Valida formato y dígito verificador de un RUT. Es local y no tiene costo."""
        try:
            parsed = parse_rut(rut)
        except InvalidRut as exc:
            return RutValidation(valid=False, error=str(exc))
        return RutValidation(valid=True, rut=parsed.formatted)

    @mcp.tool(annotations=READ_ONLY)
    async def consultar_nombre_sii(rut: str) -> CheckOutput:
        """Nombre registrado en SII y actividades económicas. Solo personas con inicio de
        actividades; `not_found` no es señal de fraude por sí solo."""
        parsed = _rut_or_error(rut)
        return _to_output(parsed, await api.sii_name(parsed.formatted))

    @mcp.tool(annotations=READ_ONLY)
    async def verificar_pep(rut: str) -> CheckOutput:
        """Indica si la persona es Políticamente Expuesta (InfoProbidad). `not_found` = no es PEP."""
        parsed = _rut_or_error(rut)
        return _to_output(parsed, await api.pep(parsed.formatted))

    @mcp.tool(annotations=READ_ONLY)
    async def verificar_interpol(rut: str) -> CheckOutput:
        """Busca notificaciones rojas de Interpol. `not_found` = sin notificaciones."""
        parsed = _rut_or_error(rut)
        return _to_output(parsed, await api.interpol(parsed.formatted))

    @mcp.tool(annotations=READ_ONLY)
    async def verificar_defuncion(rut: str) -> CheckOutput:
        """Consulta si el RUT está inscrito como fallecido en el Registro Civil."""
        parsed = _rut_or_error(rut)
        return _to_output(parsed, await api.deceased(parsed.formatted))

    @mcp.tool(annotations=READ_ONLY)
    async def validar_cedula(
        rut: str,
        numero_documento: Annotated[
            str, Field(description="Número de documento/serie impreso en la cédula")
        ],
    ) -> CheckOutput:
        """Verifica que el número de documento corresponda al RUT y que la cédula esté vigente."""
        parsed = _rut_or_error(rut)
        serial = "".join(c for c in numero_documento if c.isalnum())
        return _to_output(parsed, await api.verify_id_card(parsed.formatted, serial))

    @mcp.tool(annotations=READ_ONLY)
    async def validar_telefono(
        telefono: Annotated[str, Field(description="Celular chileno, ej. +56 9 1234 5678")],
    ) -> CheckOutput:
        """Indica si un número telefónico chileno existe, está activo y su compañía."""
        phone = _normalize_phone(telefono)
        result = await api.phone_carrier(phone)
        if result.status == "rate_limited":
            raise ToolError("Boostr limitó las consultas. Espera un minuto y vuelve a intentar.")
        return CheckOutput(status=result.status, data=result.data, message=result.message)

    @mcp.tool(annotations=READ_ONLY)
    async def evaluar_prospecto(
        rut: str,
        nombre_declarado: Annotated[
            str | None, Field(description="Nombre completo que entregó el prospecto")
        ] = None,
        numero_documento: Annotated[
            str | None, Field(description="Número de documento de la cédula, si lo tienes")
        ] = None,
        telefono: Annotated[str | None, Field(description="Teléfono declarado")] = None,
    ) -> Assessment:
        """Informe de riesgo de un prospecto: defunción, Interpol, PEP, nombre en SII y,
        si se entregan, cédula y teléfono. Devuelve señales, puntaje 0-100 y una
        recomendación (aprobar, debida diligencia reforzada, revisión manual o rechazar).
        Ejecuta entre 4 y 6 consultas pagadas."""
        parsed = _rut_or_error(rut)
        phone = _normalize_phone(telefono) if telefono else None
        serial = "".join(c for c in numero_documento if c.isalnum()) if numero_documento else None

        tasks = {
            "sii": api.sii_name(parsed.formatted),
            "deceased": api.deceased(parsed.formatted),
            "interpol": api.interpol(parsed.formatted),
            "pep": api.pep(parsed.formatted),
        }
        if serial:
            tasks["id_card"] = api.verify_id_card(parsed.formatted, serial)
        if phone:
            tasks["phone"] = api.phone_carrier(phone)
        results = dict(zip(tasks, await asyncio.gather(*tasks.values()), strict=True))

        return assess(
            masked_rut=parsed.masked,
            declared_name=nombre_declarado,
            sii=results["sii"],
            deceased=results["deceased"],
            interpol=results["interpol"],
            pep=results["pep"],
            id_card=results.get("id_card"),
            phone=results.get("phone"),
        )

    # --- Poder Judicial (requiere rol) -----------------------------------
    judicial = require_role(JUDICIAL_ROLE, core)

    @mcp.tool(
        auth=judicial,
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def iniciar_busqueda_causas(
        rut: str,
        nombre: Annotated[str, Field(description="Nombres de la persona o razón social")],
        tipo: Literal["natural", "juridica"] = "natural",
        competencia: Literal["civil", "penal", "apelacion"] = "civil",
        apellido_paterno: str | None = None,
        apellido_materno: str | None = None,
        anio: Annotated[int | None, Field(ge=1990, le=2100)] = None,
    ) -> CourtSearchStarted:
        """Inicia una búsqueda de causas judiciales en tribunales chilenos (excluye familia y
        causas reservadas). Es asíncrona: consulta el avance con `ver_busqueda_causas`.
        Para causas penales el filtro es por nombre completo; entrega ambos apellidos."""
        parsed = _rut_or_error(rut)
        if tipo == "natural" and not apellido_paterno:
            raise ToolError("Para personas naturales el apellido paterno es obligatorio")
        payload: dict[str, Any] = {
            "document_number": parsed.formatted,
            "name": nombre,
            "last_name": apellido_paterno or "",
            "type": tipo,
            "context": competencia,
            "callback_url": f"{core.base_url}/webhooks/boostr/{webhook_secret}",
        }
        if apellido_materno:
            payload["second_last_name"] = apellido_materno
        if anio:
            payload["year"] = anio

        result = await api.start_court_search(payload)
        if result.status != "found":
            raise ToolError(f"No se pudo iniciar la búsqueda: {result.message or result.status}")
        operation_id = str(result.data["operation_id"])
        # El ID queda firmado con la identidad de quien lo creó: nadie más puede leerlo.
        subject = current_identity(core).subject
        return CourtSearchStarted(
            search_id=f"{operation_id}.{_sign(webhook_secret, subject, operation_id)}",
            message="Búsqueda iniciada. Puede tardar varios minutos.",
        )

    @mcp.tool(auth=judicial, annotations=READ_ONLY)
    async def ver_busqueda_causas(search_id: str) -> CourtSearchStatus:
        """Consulta el avance y los resultados de una búsqueda de causas judiciales."""
        operation_id, _, signature = search_id.partition(".")
        subject = current_identity(core).subject
        if not signature or not hmac.compare_digest(
            signature, _sign(webhook_secret, subject, operation_id)
        ):
            raise ToolError("search_id inválido o perteneciente a otro usuario")

        result = await api.court_search_status(operation_id)
        if result.status != "found":
            raise ToolError(f"No se pudo consultar la búsqueda: {result.message or result.status}")
        data = result.data or {}
        raw_causes = data.get("causes") or []
        causes = [
            CourtCase(
                court=c.get("court"),
                rit=c.get("rit"),
                labeled=c.get("labeled"),
                date=c.get("date"),
                status=c.get("status"),
            )
            for c in raw_causes[:MAX_CAUSES]
        ]
        return CourtSearchStatus(
            status=str(data.get("status", "processing")),
            progress=data.get("progress"),
            total=data.get("total"),
            causes=causes,
            truncated=len(raw_causes) > MAX_CAUSES,
        )

    @mcp.custom_route("/webhooks/boostr/{secret}", methods=["POST"], include_in_schema=False)
    async def boostr_webhook(request: Request) -> JSONResponse:
        # Boostr no firma los webhooks: el secreto en la URL es la autenticación.
        if not hmac.compare_digest(request.path_params.get("secret", ""), webhook_secret):
            return JSONResponse({"status": "forbidden"}, status_code=403)
        body = await request.json()
        operation_id = (body.get("data") or {}).get("operation_id")
        logger.info("Búsqueda judicial terminada operation_id=%s", operation_id)
        return JSONResponse({"status": "ok"})

    return mcp


def main() -> None:
    core = CoreSettings(server_name="boostr-kyc")
    mcp = build_server(core, BoostrSettings())
    # Sin estado: cualquier instancia de Cloud Run atiende cualquier request.
    mcp.run(transport="http", host="0.0.0.0", port=core.port, stateless_http=True)


if __name__ == "__main__":
    main()
