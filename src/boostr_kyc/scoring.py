"""Motor de reglas de riesgo para prospectos.

Funciones puras (sin I/O) para poder testearlas y auditarlas. El resultado es
una recomendación: la decisión final siempre la toma una persona.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field

from boostr_kyc.client import CheckResult

Severity = Literal["critica", "alta", "media", "baja", "info"]
Decision = Literal["rechazar", "revision_manual", "debida_diligencia_reforzada", "aprobar"]

_WEIGHTS: dict[Severity, int] = {"critica": 100, "alta": 40, "media": 20, "baja": 5, "info": 0}
NAME_MATCH_THRESHOLD = 0.75
PEP_CODE = "PERSONA_POLITICAMENTE_EXPUESTA"


class Signal(BaseModel):
    code: str
    severity: Severity
    source: str
    detail: str


class CheckSummary(BaseModel):
    check: str
    status: str
    detail: str | None = None


class Assessment(BaseModel):
    rut: str = Field(description="RUT enmascarado")
    decision: Decision
    risk_score: int = Field(ge=0, le=100)
    signals: list[Signal]
    checks: list[CheckSummary]
    incomplete_checks: list[str] = Field(
        description="Verificaciones sin respuesta concluyente; repetir antes de decidir"
    )
    disclaimer: str = (
        "Recomendación automática basada en fuentes públicas vía Boostr. "
        "No reemplaza la revisión de un analista ni constituye una decisión final."
    )


def normalize_name(value: str) -> list[str]:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).upper()
    return [t for t in re.split(r"[^A-Z]+", text) if len(t) > 1]


def name_match_ratio(declared: str, official: str) -> float:
    declared_tokens = normalize_name(declared)
    if not declared_tokens:
        return 0.0
    official_tokens = set(normalize_name(official))
    hits = sum(1 for t in declared_tokens if t in official_tokens)
    return hits / len(declared_tokens)


def assess(
    *,
    masked_rut: str,
    sii: CheckResult,
    deceased: CheckResult,
    interpol: CheckResult,
    pep: CheckResult,
    declared_name: str | None = None,
    id_card: CheckResult | None = None,
    phone: CheckResult | None = None,
) -> Assessment:
    signals: list[Signal] = []
    checks: list[CheckSummary] = []
    incomplete: list[str] = []

    def track(name: str, result: CheckResult, *, optional: bool = False) -> None:
        checks.append(CheckSummary(check=name, status=result.status, detail=result.message))
        # Un servicio opcional no contratado no bloquea la evaluación.
        if not result.conclusive and not (optional and result.status == "forbidden"):
            incomplete.append(name)

    # Defunción: una identidad de persona fallecida es la señal de fraude más fuerte.
    track("defuncion", deceased)
    if deceased.status == "found" and int((deceased.data or {}).get("is_deceased", 0)) == 1:
        signals.append(
            Signal(
                code="RUT_FALLECIDO",
                severity="critica",
                source="Registro Civil",
                detail=f"RUT inscrito como fallecido ({deceased.data.get('deceased_at')})",
            )
        )

    track("interpol", interpol)
    if interpol.status == "found":
        signals.append(
            Signal(
                code="NOTIFICACION_ROJA_INTERPOL",
                severity="critica",
                source="Interpol",
                detail=f"Notificación roja emitida por {interpol.data.get('issuing_country')}",
            )
        )

    track("pep", pep)
    if pep.status == "found":
        data = pep.data or {}
        signals.append(
            Signal(
                code=PEP_CODE,
                severity="media",
                source="InfoProbidad",
                detail=f"{data.get('role', 'Cargo')} en {data.get('entity', 'entidad pública')}",
            )
        )

    track("nombre_sii", sii)
    if declared_name:
        if sii.status == "found":
            ratio = name_match_ratio(declared_name, (sii.data or {}).get("name", ""))
            if ratio < NAME_MATCH_THRESHOLD:
                signals.append(
                    Signal(
                        code="NOMBRE_NO_COINCIDE",
                        severity="alta",
                        source="SII",
                        detail=f"El nombre declarado coincide en {ratio:.0%} con el registrado",
                    )
                )
        elif sii.status == "not_found":
            signals.append(
                Signal(
                    code="NOMBRE_NO_CONTRASTABLE",
                    severity="baja",
                    source="SII",
                    detail="Sin inicio de actividades en SII: no se pudo contrastar el nombre",
                )
            )

    if id_card is not None:
        track("cedula", id_card, optional=True)
        if id_card.status == "found" and not (id_card.data or {}).get("is_valid", False):
            signals.append(
                Signal(
                    code="CEDULA_NO_VIGENTE",
                    severity="alta",
                    source="Registro Civil",
                    detail="El número de documento no es válido o la cédula no está vigente",
                )
            )

    if phone is not None:
        track("telefono", phone, optional=True)
        if phone.status == "found" and not (phone.data or {}).get("valid", False):
            signals.append(
                Signal(
                    code="TELEFONO_INEXISTENTE",
                    severity="media",
                    source="Telefonía",
                    detail="El número no existe o no está activo",
                )
            )

    score = min(100, sum(_WEIGHTS[s.severity] for s in signals))
    critical_incomplete = {"defuncion", "interpol"} & set(incomplete)
    # PEP no es señal de fraude: exige debida diligencia, no revisión por sospecha.
    review_signals = [s for s in signals if s.severity in ("alta", "media") and s.code != PEP_CODE]

    decision: Decision
    if any(s.severity == "critica" for s in signals):
        decision = "rechazar"
    elif review_signals or critical_incomplete:
        decision = "revision_manual"
    elif any(s.code == PEP_CODE for s in signals):
        decision = "debida_diligencia_reforzada"
    elif incomplete:
        decision = "revision_manual"
    else:
        decision = "aprobar"

    return Assessment(
        rut=masked_rut,
        decision=decision,
        risk_score=score,
        signals=signals,
        checks=checks,
        incomplete_checks=incomplete,
    )
