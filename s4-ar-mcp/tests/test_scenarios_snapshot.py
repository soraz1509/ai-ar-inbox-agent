"""Demo scenarios 1-3 against the committed SAP sandbox snapshot + seeded items."""
import json
from pathlib import Path

from ar_matching import propose_clearing

MOCK = Path(__file__).resolve().parents[2] / "s4-ar-mock"
OPEN = json.loads((MOCK / "snapshot" / "open_items.json").read_text(encoding="utf-8"))
SEED = json.loads((MOCK / "seed" / "seed_items.json").read_text(encoding="utf-8"))["items"]
ALL_OPEN = OPEN + [{**s, "source": "seeded"} for s in SEED]


def open_items(customer):
    return [i for i in ALL_OPEN if i["Customer"] == customer]


def docs(r):
    return sorted(i["accounting_document"] for i in r["candidates"][0]["items"])


def test_scenario_1_clean_remittance_cash_app():
    r = propose_clearing(open_items("CASH_APP"), 12750.00, "EUR", value_date="2026-10-22",
                         references=["PAYADV7", "PAYADV8", "PAYADV9"])
    assert r["status"] == "MATCHED"
    assert docs(r) == ["1800003732", "1800003733", "1800003734"]


def test_scenario_2_promo_deduction_inlandskunde():
    r = propose_clearing(open_items("10100013"), 127135.40, "EUR", value_date="2026-10-22",
                         references=["1800000999", "1800001000", "1800001001"],
                         deductions=[{"amount": 2594.60, "reason_text": "Aktionsrabatt Oktober 2%"}])
    assert r["status"] == "MATCHED"
    assert docs(r) == ["1800000999", "1800001000", "1800001001"]


def test_scenario_3_po_numbers_resolve_to_seeded_invoices():
    r = propose_clearing(open_items("10100001"), 4165.00, "EUR", value_date="2026-10-22",
                         references=["4500017311", "4500017342", "4500017359"])
    assert r["status"] == "MATCHED"
    assert docs(r) == ["1800990001", "1800990002", "1800990003"]
    assert all(i["source"] == "seeded" for i in r["candidates"][0]["items"])
