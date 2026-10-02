import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr

from boostr_kyc.server import BoostrSettings, build_server
from mcp_core import CoreSettings
from tests.conftest import err, ok

RUT = "16.163.631-2"
ROUTES = {
    "/rut/name/16163631-2.json": ok({"name": "GABRIEL BORIC FONT"}),
    "/rut/deceased/16163631-2.json": ok({"is_deceased": 0}),
    "/rut/interpol/16163631-2.json": err("U-12"),
    "/rut/pep/16163631-2.json": err("U-12"),
    "/telephone/carrier_lookup/912345678.json": ok({"valid": False, "number": "912345678"}),
    "/poder_judicial/causes.json": lambda r: (
        ok({"operation_id": "op-123"})
        if r.method == "POST"
        else ok(
            {
                "status": "done",
                "progress": 100,
                "total": 1,
                "causes": [
                    {
                        "court": "1° Juzgado Civil",
                        "rit": "C-1-2024",
                        "litigants": [{"document_number": "11111111-1"}],
                    }
                ],
            }
        )
    ),
}


def make_server(fake, roles=None):
    core = CoreSettings(role_assignments={"local-dev": roles or []})
    boostr = BoostrSettings(api_key=SecretStr("k"), webhook_secret=SecretStr("s3cret"))
    return build_server(core, boostr, client=fake.client())


async def test_evaluar_prospecto_end_to_end(fake_boostr, capsys):
    fake = fake_boostr(ROUTES)
    async with Client(make_server(fake)) as client:
        result = await client.call_tool(
            "evaluar_prospecto",
            {"rut": RUT, "nombre_declarado": "Gabriel Boric", "telefono": "+56 9 1234 5678"},
        )
    report = result.structured_content
    assert report["decision"] == "revision_manual"
    assert [s["code"] for s in report["signals"]] == ["TELEFONO_INEXISTENTE"]
    assert report["rut"] == "161XXXXX-2"

    audit = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"log_type": "mcp-audit"' in line
    ]
    assert audit[-1]["tool"] == "evaluar_prospecto"
    assert audit[-1]["outcome"] == "ok"
    assert "16163631" not in json.dumps(audit)  # el RUT nunca queda en claro


async def test_invalid_rut_does_not_call_boostr(fake_boostr):
    fake = fake_boostr(ROUTES)
    async with Client(make_server(fake)) as client:
        with pytest.raises(ToolError, match="RUT inválido"):
            await client.call_tool("verificar_pep", {"rut": "16163631-3"})
    assert fake.calls == []


async def test_judicial_tools_hidden_without_role(fake_boostr):
    async with Client(make_server(fake_boostr(ROUTES))) as client:
        names = {t.name for t in await client.list_tools()}
    assert "iniciar_busqueda_causas" not in names
    assert "evaluar_prospecto" in names


async def test_judicial_search_flow_and_ownership(fake_boostr):
    fake = fake_boostr(ROUTES)
    async with Client(make_server(fake, roles=["kyc.judicial"])) as client:
        started = await client.call_tool(
            "iniciar_busqueda_causas",
            {"rut": RUT, "nombre": "Gabriel", "apellido_paterno": "Boric"},
        )
        search_id = started.structured_content["search_id"]
        status = await client.call_tool("ver_busqueda_causas", {"search_id": search_id})
        with pytest.raises(ToolError, match="otro usuario"):
            await client.call_tool("ver_busqueda_causas", {"search_id": "op-123.falsificado"})

    assert status.structured_content["causes"][0]["rit"] == "C-1-2024"
    assert "litigants" not in status.structured_content["causes"][0]
    sent = fake.bodies()[0]
    assert sent["callback_url"].endswith("/webhooks/boostr/s3cret")


async def test_webhook_requires_secret(fake_boostr):
    app = make_server(fake_boostr(ROUTES)).http_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        denied = await http.post("/webhooks/boostr/otro", json={})
        accepted = await http.post("/webhooks/boostr/s3cret", json={"data": {"operation_id": "x"}})
    assert denied.status_code == 403
    assert accepted.status_code == 200
