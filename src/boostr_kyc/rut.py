"""Validación local de RUT chileno (módulo 11).

Se valida antes de llamar a Boostr: un RUT mal escrito no gasta consultas pagadas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLEAN = re.compile(r"[^0-9kK]")


class InvalidRut(ValueError):
    pass


@dataclass(frozen=True)
class Rut:
    body: str
    dv: str

    @property
    def formatted(self) -> str:
        return f"{self.body}-{self.dv}"

    @property
    def masked(self) -> str:
        """Versión para mostrar sin exponer el RUT completo (ej. 16.1XX.XXX-2)."""
        return f"{self.body[:3]}{'X' * (len(self.body) - 3)}-{self.dv}"


def compute_dv(body: str) -> str:
    total, factor = 0, 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    rest = 11 - (total % 11)
    return {11: "0", 10: "K"}.get(rest, str(rest))


def parse_rut(value: str) -> Rut:
    cleaned = _CLEAN.sub("", value or "").upper()
    if len(cleaned) < 2:
        raise InvalidRut("RUT vacío o demasiado corto")
    body, dv = cleaned[:-1].lstrip("0"), cleaned[-1]
    if not body.isdigit() or not 6 <= len(body) <= 8:
        raise InvalidRut("El cuerpo del RUT debe tener entre 6 y 8 dígitos")
    if compute_dv(body) != dv:
        raise InvalidRut("Dígito verificador incorrecto")
    return Rut(body=body, dv=dv)
