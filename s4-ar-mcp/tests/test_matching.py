"""Unit tests for the deterministic matching engine."""
from ar_matching import propose_clearing


def item(doc, amount, refs=None, cur="EUR", fy="2026", due="2026-10-15", pct=0.0, cd_due=None):
    row = {"CompanyCode": "1010", "FiscalYear": fy, "AccountingDocument": doc, "AccountingDocumentItem": "1",
           "Customer": "C1", "AmountInTransactionCurrency": f"{amount:.2f}", "TransactionCurrency": cur,
           "NetDueDate": due, "DocumentReferenceID": "", "AssignmentReference": "", "BillingDocument": "",
           "SalesDocument": "", "CashDiscount1Percent": f"{pct:.3f}", "CashDiscount1DueDate": cd_due}
    row.update(refs or {})
    return row


def docs(r, n=0):
    return sorted(i["accounting_document"] for i in r["candidates"][n]["items"])


CASH_APP = [
    item("1800003732", 2550, {"DocumentReferenceID": "PAYADV7"}, fy="2023"),
    item("1800003733", 4250, {"DocumentReferenceID": "PAYADV8"}, fy="2023"),
    item("1800003734", 5950, {"DocumentReferenceID": "PAYADV9"}, fy="2023"),
    item("1800000149", 2550, {"DocumentReferenceID": "PAYADV7"}, cur="USD"),
    item("1800000150", 4250, {"DocumentReferenceID": "PAYADV8"}, cur="USD"),
    item("1800000151", 5950, {"DocumentReferenceID": "PAYADV9"}, cur="USD"),
]
REFS = ["PAYADV7", "PAYADV8", "PAYADV9"]
DE_INVOICES = [item("1800000999", 38919), item("1800001000", 51892), item("1800001001", 38919)]
DE_REFS = ["1800000999", "1800001000", "1800001001"]


def test_reference_match_uses_payment_currency():
    eur = propose_clearing(CASH_APP, 12750.00, "EUR", references=REFS)
    usd = propose_clearing(CASH_APP, 12750.00, "USD", references=REFS)
    assert eur["status"] == "MATCHED" and eur["method"] == "reference" and not eur["needs_confirmation"]
    assert docs(eur) == ["1800003732", "1800003733", "1800003734"]
    assert docs(usd) == ["1800000149", "1800000150", "1800000151"]


def test_leading_zeros_spaces_and_case_are_normalised():
    items = [item("90027528", 20.88, {"DocumentReferenceID": "0090027528", "BillingDocument": "90027528"})]
    assert propose_clearing(items, 20.88, "EUR", references=["90 027 528"])["status"] == "MATCHED"


def test_stated_deduction_explains_short_payment():
    r = propose_clearing(DE_INVOICES, 127135.40, "EUR", references=DE_REFS,
                         deductions=[{"amount": 2594.60, "reason_text": "Aktionsrabatt Oktober"}])
    assert r["status"] == "MATCHED"
    assert r["candidates"][0]["difference"] == 0


def test_unexplained_short_payment_is_flagged():
    r = propose_clearing(DE_INVOICES, 127135.40, "EUR", references=DE_REFS)
    c = r["candidates"][0]
    assert r["status"] == "MATCHED_WITH_DIFFERENCE" and r["needs_confirmation"]
    assert c["difference_type"] == "SHORT_PAYMENT" and c["difference"] == 2594.60


def test_small_difference_within_tolerance():
    r = propose_clearing(CASH_APP, 12747.50, "EUR", references=REFS)
    assert r["candidates"][0]["difference_type"] == "WITHIN_TOLERANCE"
    assert r["tolerance_limit"] == 5.00


def test_difference_above_tolerance_is_short_payment():
    r = propose_clearing(CASH_APP, 12740.00, "EUR", references=REFS)
    assert r["candidates"][0]["difference_type"] == "SHORT_PAYMENT"


def test_overpayment_is_flagged():
    r = propose_clearing(CASH_APP, 12800.00, "EUR", references=REFS)
    assert r["candidates"][0]["difference_type"] == "OVER_PAYMENT"


def test_cash_discount_earned_within_discount_period():
    items = [item("1800500001", 1000, {"DocumentReferenceID": "INV-1"}, pct=2, cd_due="2026-10-10")]
    r = propose_clearing(items, 980, "EUR", value_date="2026-10-08", references=["INV-1"])
    assert r["status"] == "MATCHED"
    assert r["candidates"][0]["difference_type"] == "CASH_DISCOUNT_EARNED"


def test_unearned_cash_discount_after_discount_period():
    items = [item("1800500001", 1000, {"DocumentReferenceID": "INV-1"}, pct=2, cd_due="2026-10-10")]
    r = propose_clearing(items, 980, "EUR", value_date="2026-10-20", references=["INV-1"])
    assert r["status"] == "MATCHED_WITH_DIFFERENCE"
    assert r["candidates"][0]["difference_type"] == "UNEARNED_CASH_DISCOUNT"
    assert r["candidates"][0]["difference"] == 20.00


def test_amount_only_unique_subset_needs_confirmation():
    items = [item("A", 100), item("B", 250), item("C", 400), item("D", 75)]
    r = propose_clearing(items, 325, "EUR")
    assert r["status"] == "MATCHED" and r["method"] == "subset_sum" and r["needs_confirmation"]
    assert docs(r) == ["B", "D"]


def test_amount_only_ambiguous_subsets_are_never_auto_matched():
    items = [item("A", 100), item("B", 200), item("C", 300), item("D", 150), item("E", 150)]
    r = propose_clearing(items, 300, "EUR")
    assert r["status"] == "AMBIGUOUS" and len(r["candidates"]) >= 2


def test_reference_hitting_several_items_is_ambiguous():
    items = [item(f"18000{n}", 1000, {"DocumentReferenceID": "REFKEY"}) for n in range(3)]
    r = propose_clearing(items, 1000, "EUR", references=["REFKEY"])
    assert r["status"] == "AMBIGUOUS"
    assert r["ambiguous_references"] == [{"reference": "REFKEY", "matching_items": 3}]


def test_unknown_reference_is_listed_not_guessed():
    items = [item("A", 100, {"DocumentReferenceID": "X1"}), item("B", 100)]
    r = propose_clearing(items, 100, "EUR", references=["X1", "DOES-NOT-EXIST"])
    assert docs(r) == ["A"] and r["unresolved_references"] == ["DOES-NOT-EXIST"] and r["needs_confirmation"]


def test_no_match_when_nothing_fits():
    r = propose_clearing([item("A", 100), item("B", 250)], 999, "EUR")
    assert r["status"] == "NO_MATCH" and r["candidates"] == []


def test_negative_and_foreign_currency_items_are_excluded():
    r = propose_clearing([item("A", 100), item("B", -550), item("C", 100, cur="USD")], 100, "EUR")
    assert r["items_excluded"] == 2 and r["items_considered"] == 1


def test_odata_date_format_is_parsed():
    r = propose_clearing([item("A", 100, due="/Date(1792022400000)/")], 100, "EUR")
    assert r["candidates"][0]["items"][0]["net_due_date"] == "2026-10-15"
