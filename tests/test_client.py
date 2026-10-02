import httpx

from tests.conftest import err, ok


async def test_sends_api_key_and_parses_success(fake_boostr):
    fake = fake_boostr({"/rut/pep/8847070-2.json": ok({"role": "SUBSECRETARIO"})})
    result = await fake.client().pep("8847070-2")
    assert result.status == "found"
    assert result.data == {"role": "SUBSECRETARIO"}
    assert fake.calls[0].headers["X-API-KEY"] == "test-key"


async def test_error_codes_map_to_statuses(fake_boostr):
    fake = fake_boostr(
        {
            "/rut/pep/1.json": err("U-12"),
            "/rut/pep/2.json": err("U-10"),
            "/rut/pep/3.json": err("U-06", status=401),
            "/rut/pep/4.json": err("U-02"),
            "/rut/pep/5.json": httpx.Response(429, text="429 Too Many Request"),
        }
    )
    client = fake.client()
    statuses = [(await client.pep(str(i))).status for i in range(1, 6)]
    assert statuses == ["not_found", "unavailable", "forbidden", "invalid", "rate_limited"]


async def test_caches_only_conclusive_results(fake_boostr):
    fake = fake_boostr({"/rut/pep/1.json": err("U-12"), "/rut/pep/2.json": err("U-10")})
    client = fake.client()
    for _ in range(2):
        await client.pep("1")
        await client.pep("2")
    paths = [c.url.path for c in fake.calls]
    assert paths.count("/rut/pep/1.json") == 1  # cacheado
    assert paths.count("/rut/pep/2.json") == 2  # no concluyente: se reintenta


async def test_network_error_is_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("timeout")

    from boostr_kyc.client import BoostrClient

    client = BoostrClient("k", transport=httpx.MockTransport(boom))
    assert (await client.deceased("1-9")).status == "unavailable"
