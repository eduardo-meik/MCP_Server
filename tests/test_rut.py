import pytest

from boostr_kyc.rut import InvalidRut, compute_dv, parse_rut


@pytest.mark.parametrize("value", ["16.163.631-2", "16163631-2", "161636312", " 16163631 - 2 "])
def test_accepts_common_formats(value):
    assert parse_rut(value).formatted == "16163631-2"


def test_dv_k_is_normalized_uppercase():
    body = next(str(n) for n in range(10_000_000, 10_001_000) if compute_dv(str(n)) == "K")
    assert parse_rut(f"{body}-k").dv == "K"


@pytest.mark.parametrize("value", ["16163631-3", "", "1-9", "abc", "123456789012-3"])
def test_rejects_invalid(value):
    with pytest.raises(InvalidRut):
        parse_rut(value)


def test_masked_hides_most_digits():
    assert parse_rut("16163631-2").masked == "161XXXXX-2"
