from boostr_kyc.client import CheckResult
from boostr_kyc.scoring import assess, name_match_ratio

FOUND_NAME = CheckResult("found", {"name": "GABRIEL BORIC FONT"})
CLEAN = {
    "masked_rut": "161XXXXX-2",
    "sii": FOUND_NAME,
    "deceased": CheckResult("found", {"is_deceased": 0}),
    "interpol": CheckResult("not_found", code="U-12"),
    "pep": CheckResult("not_found", code="U-12"),
}


def test_clean_profile_is_approved():
    result = assess(**CLEAN, declared_name="Gabriel Boric Font")
    assert result.decision == "aprobar"
    assert result.risk_score == 0
    assert result.signals == []


def test_deceased_rut_is_rejected():
    result = assess(**{**CLEAN, "deceased": CheckResult("found", {"is_deceased": 1})})
    assert result.decision == "rechazar"
    assert result.signals[0].code == "RUT_FALLECIDO"


def test_interpol_red_notice_is_rejected():
    result = assess(**{**CLEAN, "interpol": CheckResult("found", {"issuing_country": "AR"})})
    assert result.decision == "rechazar"


def test_pep_requires_enhanced_due_diligence():
    result = assess(**{**CLEAN, "pep": CheckResult("found", {"role": "ALCALDE"})})
    assert result.decision == "debida_diligencia_reforzada"


def test_name_mismatch_requires_manual_review():
    result = assess(**CLEAN, declared_name="Juan Pérez González")
    assert result.decision == "revision_manual"
    assert result.signals[0].code == "NOMBRE_NO_COINCIDE"


def test_unavailable_critical_source_is_never_approved():
    result = assess(**{**CLEAN, "interpol": CheckResult("unavailable", code="U-10")})
    assert result.decision == "revision_manual"
    assert result.incomplete_checks == ["interpol"]


def test_optional_service_not_contracted_does_not_block():
    result = assess(**CLEAN, phone=CheckResult("forbidden", code="U-06"))
    assert result.decision == "aprobar"


def test_invalid_id_card_raises_risk():
    result = assess(**CLEAN, id_card=CheckResult("found", {"is_valid": False}))
    assert result.decision == "revision_manual"


def test_name_matching_ignores_accents_and_case():
    assert name_match_ratio("josé  muñoz", "JOSE ANTONIO MUNOZ") == 1.0
    assert name_match_ratio("Ana Soto", "ANA ROJAS") == 0.5


def test_medium_fraud_signal_requires_review():
    result = assess(**CLEAN, phone=CheckResult("found", {"valid": False}))
    assert result.decision == "revision_manual"
