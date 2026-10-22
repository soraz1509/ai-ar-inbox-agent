"""Deterministic AR matching engine (no LLM).

Given a payment (amount, currency, value date), the references and deductions the customer
quoted, and the customer's open items (raw OData rows from S/4HANA or the mock), propose which
items the payment clears and classify any difference. Amounts are handled in integer cents.
Ambiguity is always reported, never resolved silently.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from itertools import combinations

REF_FIELDS = ("AccountingDocument", "DocumentReferenceID", "AssignmentReference", "BillingDocument", "SalesDocument")
# Lower of absolute amount and percentage of the payment applies (cf. customer tolerance groups, OBA3).
DEFAULT_TOLERANCE = {"abs": 5.00, "pct": 0.5}
MAX_SUBSET_ITEMS = 25     # oldest open items considered for amount-only matching
MAX_SUBSET_SIZE = 6       # max invoices in one amount-only combination
MAX_CANDIDATES = 3        # candidate sets returned when ambiguous


# ---------- helpers ----------
def cents(value) -> int:
    return int(round(float(value) * 100))


def money(c: int) -> float:
    return round(c / 100, 2)


def norm_ref(value) -> str:
    """Upper-case, drop everything except letters and digits, strip leading zeros."""
    s = re.sub(r"[^0-9A-Za-z]", "", str(value or "")).upper()
    return s.lstrip("0") or s


def to_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    m = re.match(r"/Date\((-?\d+)", str(value))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date()
    return date.fromisoformat(str(value)[:10])


def iso(d):
    return d.isoformat() if d else None


def normalize_item(row: dict) -> dict:
    return {
        "key": {"company_code": row["CompanyCode"], "fiscal_year": row["FiscalYear"],
                "accounting_document": row["AccountingDocument"],
                "accounting_document_item": row.get("AccountingDocumentItem", "1")},
        "customer": row.get("Customer"),
        "amount_cents": cents(row["AmountInTransactionCurrency"]),
        "currency": row["TransactionCurrency"],
        "net_due": to_date(row.get("NetDueDate")),
        "refs": {norm_ref(row.get(f)) for f in REF_FIELDS} - {""},
        "raw_refs": {f: row.get(f) for f in REF_FIELDS if row.get(f)},
        "cd1_percent": float(row.get("CashDiscount1Percent") or 0),
        "cd1_due": to_date(row.get("CashDiscount1DueDate")),
        "source": row.get("source", "sap"),
    }


def item_out(i: dict) -> dict:
    return {**i["key"], "amount": money(i["amount_cents"]), "currency": i["currency"],
            "net_due_date": iso(i["net_due"]), "references": i["raw_refs"], "source": i["source"]}


def key_of(i: dict) -> tuple:
    return tuple(i["key"].values())


# ---------- core steps ----------
def resolve_references(cands, references):
    """Map each quoted reference to exactly one open item, or report it as unresolved/ambiguous."""
    matched, unresolved, ambiguous, seen = [], [], [], set()
    for r in references:
        raw = r.get("ref") if isinstance(r, dict) else r
        amount = cents(r["amount"]) if isinstance(r, dict) and r.get("amount") is not None else None
        n = norm_ref(raw)
        hits = [i for i in cands if n and n in i["refs"]]
        if amount is not None and len(hits) > 1:
            hits = [i for i in hits if i["amount_cents"] == amount] or hits
        if not hits:
            unresolved.append(raw)
        elif len(hits) > 1:
            ambiguous.append({"reference": raw, "matching_items": len(hits)})
        elif key_of(hits[0]) not in seen:
            seen.add(key_of(hits[0]))
            matched.append(hits[0])
    return matched, unresolved, ambiguous


def tolerance_limit(pay_cents: int, tol: dict) -> int:
    return min(cents(tol["abs"]), round(abs(pay_cents) * tol["pct"] / 100))


def evaluate(selected, pay, ded, vdate, tol):
    """Compare open amount with payment + stated deductions and classify the difference."""
    total = sum(i["amount_cents"] for i in selected)
    diff = total - pay - ded          # > 0: customer paid less than the open amount
    status, dtype = "MATCHED", "NONE"
    if diff != 0:
        disc = sum(round(i["amount_cents"] * i["cd1_percent"] / 100) for i in selected if i["cd1_percent"] > 0)
        disc_due = min((i["cd1_due"] for i in selected if i["cd1_due"]), default=None)
        if disc and abs(diff - disc) <= 1:
            if vdate and disc_due and vdate <= disc_due:
                dtype = "CASH_DISCOUNT_EARNED"
            else:
                status, dtype = "MATCHED_WITH_DIFFERENCE", "UNEARNED_CASH_DISCOUNT"
        elif abs(diff) <= tolerance_limit(pay, tol):
            status, dtype = "MATCHED_WITH_DIFFERENCE", "WITHIN_TOLERANCE"
        else:
            status, dtype = "MATCHED_WITH_DIFFERENCE", ("SHORT_PAYMENT" if diff > 0 else "OVER_PAYMENT")
    return status, {"items": [item_out(i) for i in selected], "open_total": money(total),
                    "difference": money(diff), "difference_type": dtype}


def subset_candidates(cands, target: int):
    """Exact amount-only combinations of the oldest open items (bounded search)."""
    pool = sorted((i for i in cands if i["amount_cents"] <= target),
                  key=lambda i: (i["net_due"] or date.max, i["amount_cents"]))[:MAX_SUBSET_ITEMS]
    found = []
    for size in range(1, MAX_SUBSET_SIZE + 1):
        for combo in combinations(pool, size):
            if sum(i["amount_cents"] for i in combo) == target:
                found.append(list(combo))
                if len(found) > MAX_CANDIDATES:
                    return found
    return found


def explain(r: dict) -> str:
    pay = f"{r['currency']} {r['payment_amount']:,.2f}"
    if r["status"] == "NO_MATCH":
        return f"Payment {pay}: no open item or combination of open items matches. Manual research needed."
    if r["status"] == "AMBIGUOUS":
        if r["ambiguous_references"]:
            refs = ", ".join(f"{a['reference']} ({a['matching_items']} items)" for a in r["ambiguous_references"])
            return f"Payment {pay}: reference(s) {refs} match several open items. A person must decide."
        return f"Payment {pay}: {len(r['candidates'])} different combinations of open items match. A person must choose."
    c = r["candidates"][0]
    how = "by quoted reference" if r["method"] == "reference" else "by amount only (no usable reference)"
    text = f"Payment {pay} matched {how} to {len(c['items'])} open item(s) totalling {c['open_total']:,.2f}"
    if r["deductions_total"]:
        text += f", with stated deductions of {r['deductions_total']:,.2f}"
    text += f". Difference {c['difference']:,.2f} ({c['difference_type']})."
    if r["unresolved_references"]:
        text += " Unresolved references: " + ", ".join(map(str, r["unresolved_references"])) + "."
    return text


# ---------- public API ----------
def propose_clearing(open_items, payment_amount, currency, value_date=None,
                     references=None, deductions=None, tolerance=None) -> dict:
    """Return a clearing proposal. Never posts anything; never picks between equal options."""
    tol = {**DEFAULT_TOLERANCE, **(tolerance or {})}
    pay = cents(payment_amount)
    ded = sum(cents(d.get("amount", 0)) for d in (deductions or []))
    vdate = to_date(value_date)
    items = [normalize_item(r) for r in open_items]
    cands = [i for i in items if i["currency"] == currency and i["amount_cents"] > 0]

    r = {"status": "NO_MATCH", "method": None, "needs_confirmation": True,
         "payment_amount": money(pay), "currency": currency, "value_date": iso(vdate),
         "deductions_total": money(ded), "tolerance_limit": money(tolerance_limit(pay, tol)),
         "items_considered": len(cands), "items_excluded": len(items) - len(cands),
         "candidates": [], "unresolved_references": [], "ambiguous_references": []}

    if references:
        matched, unresolved, ambiguous = resolve_references(cands, references)
        r["unresolved_references"], r["ambiguous_references"] = unresolved, ambiguous
        if ambiguous:
            r["status"], r["method"] = "AMBIGUOUS", "reference"
            if matched:
                r["candidates"] = [evaluate(matched, pay, ded, vdate, tol)[1]]
        elif matched:
            status, cand = evaluate(matched, pay, ded, vdate, tol)
            r.update(status=status, method="reference", candidates=[cand],
                     needs_confirmation=status != "MATCHED" or bool(unresolved))
        if r["method"]:
            r["explanation"] = explain(r)
            return r

    found = subset_candidates(cands, pay + ded)
    if found:
        r["method"] = "subset_sum"
        r["candidates"] = [evaluate(s, pay, ded, vdate, tol)[1] for s in found[:MAX_CANDIDATES]]
        r["status"] = "MATCHED" if len(found) == 1 else "AMBIGUOUS"
    r["explanation"] = explain(r)
    return r
