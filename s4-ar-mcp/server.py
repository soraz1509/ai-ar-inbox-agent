"""s4-ar-mcp: MCP server exposing SAP S/4HANA accounts-receivable tools to AI agents.

Read tools query the SAP Business Accelerator Hub sandbox (S4_MODE=sandbox; seeded demo items are
overlaid and the snapshot mock is used automatically if the sandbox fails) or the mock only
(S4_MODE=mock). Write tools only create DRAFTS in the S/4 mock. There is deliberately no tool that
posts, clears or approves anything: a separate executor does that after human approval.
"""
import logging
import os
import re
import secrets
import time
from datetime import date
from statistics import mean, median
from typing import Optional
from urllib.parse import quote

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from ar_matching import REF_FIELDS, norm_ref, to_date
from ar_matching import propose_clearing as match_payment

MODE = os.environ.get("S4_MODE", "sandbox").lower()
MOCK_URL = os.environ.get("MOCK_URL", "http://s4-ar-mock:8000").rstrip("/")
SANDBOX_BASE = os.environ.get("SAP_SANDBOX_BASE", "https://sandbox.api.sap.com/s4hanacloud").rstrip("/")
SANDBOX_HEADERS = {"APIKey": os.environ.get("SAP_SANDBOX_APIKEY", ""), "Accept": "application/json"}
TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")
ITEMS_PATH = "/sap/opu/odata/sap/API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube"
CUSTOMER_PATH = "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_Customer"
FIELDS = ["CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem",
          "Customer", "CustomerName", "FinancialAccountType", "AccountingDocumentType", "PostingDate",
          "NetDueDate", "AmountInTransactionCurrency", "TransactionCurrency", "DebitCreditCode",
          "DocumentReferenceID", "AssignmentReference", "BillingDocument", "SalesDocument",
          "PaymentTerms", "CashDiscount1Percent", "CashDiscount1DueDate", "DunningLevel", "IsCleared"]
CLEARING_FIELDS = ["ClearingDate", "ClearingAccountingDocument"]
CACHE_SECONDS = 300

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("s4-ar-mcp")

# host="0.0.0.0": the server is reached as "s4-ar-mcp" inside Docker, not only as localhost.
mcp = FastMCP("s4-ar", host="0.0.0.0", instructions=(
    "SAP S/4HANA accounts receivable tools. Work in one company code at a time (1010 = BestRun DE). "
    "propose_clearing does all amount matching deterministically - never add up amounts yourself. "
    "draft_* tools create drafts that a human must approve; nothing is ever posted by these tools."))
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
DRAFT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


# ---------- SAP access ----------
def esc(v) -> str:
    return str(v).replace("'", "''")


def build_url(base, path, params) -> str:
    q = "&".join(f"{k}={quote(str(v), safe=',' + chr(39))}" for k, v in params.items())
    return f"{base}{path}?{q}" if q else f"{base}{path}"


def iso(v):
    d = to_date(v)
    return d.isoformat() if d else None


def _paged(base, headers, flt, fields, cap=5000, page=500):
    rows, skip = [], 0
    with httpx.Client(timeout=60) as client:
        while True:
            params = {"$filter": flt, "$select": ",".join(fields), "$top": str(page),
                      "$skip": str(skip), "$inlinecount": "allpages"}
            r = client.get(build_url(base, ITEMS_PATH, params), headers=headers)
            r.raise_for_status()
            d = r.json()["d"]
            batch, total = d["results"], int(d.get("__count", 0))
            rows += [{k: v for k, v in x.items() if k != "__metadata"} for x in batch]
            skip += page
            if not batch or len(rows) >= min(total or cap, cap):
                return rows


_cache: dict = {}


def read_items(flt, fields, cap=5000):
    key = (flt, tuple(fields), cap)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1], hit[2]
    if MODE == "sandbox":
        try:
            rows = [{**r, "source": "sandbox"} for r in _paged(SANDBOX_BASE, SANDBOX_HEADERS, flt, fields, cap)]
            src = "sandbox"
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("sandbox read failed (%s) - falling back to snapshot mock", e)
            rows, src = _paged(MOCK_URL, {}, flt, fields, cap), "mock_fallback"
    else:
        rows, src = _paged(MOCK_URL, {}, flt, fields, cap), "mock"
    _cache[key] = (time.time(), rows, src)
    return rows, src


def customer_filter(customer, company_code, cleared) -> str:
    return (f"Customer eq '{esc(customer)}' and CompanyCode eq '{esc(company_code)}' "
            f"and FinancialAccountType eq 'D' and IsCleared eq {'true' if cleared else 'false'}")


def open_rows(customer, company_code):
    rows, src = read_items(customer_filter(customer, company_code, False), FIELDS)
    if src == "sandbox":  # overlay demo items that exist only in the mock
        try:
            seeded = httpx.get(f"{MOCK_URL}/mock/seeded-items", params={"customer": customer}, timeout=10).json()
            rows = rows + [s for s in seeded if s["CompanyCode"] == company_code and not s["IsCleared"]]
        except httpx.HTTPError as e:
            log.warning("seeded overlay unavailable: %s", e)
    return rows, src


def brief(r) -> dict:
    return {"company_code": r["CompanyCode"], "fiscal_year": r["FiscalYear"],
            "accounting_document": r["AccountingDocument"], "accounting_document_item": r["AccountingDocumentItem"],
            "amount": float(r["AmountInTransactionCurrency"]), "currency": r["TransactionCurrency"],
            "posting_date": iso(r.get("PostingDate")), "net_due_date": iso(r.get("NetDueDate")),
            "references": {k: r[k] for k in ("DocumentReferenceID", "AssignmentReference", "BillingDocument",
                                              "SalesDocument") if r.get(k)},
            "payment_terms": r.get("PaymentTerms") or None, "source": r.get("source")}


def is_overdue(r, today) -> bool:
    d = to_date(r.get("NetDueDate"))
    return bool(d and d < today)


def _name(v) -> str:
    return re.sub(r"[^0-9a-z]", "", str(v or "").lower())


# ---------- read tools ----------
@mcp.tool(annotations=READ)
def find_customer(query: str, references: Optional[list[str]] = None, company_code: str = "1010") -> dict:
    """Find SAP customer accounts by name or customer number as written in an email or signature.
    Several accounts can share a name. Pass the references quoted in the email (invoice, PO or
    payment-advice numbers) to rank candidates by how many of their open items carry them.
    If two candidates remain equally plausible, do not choose - report the ambiguity."""
    q = _name(query)
    r = httpx.get(build_url(MOCK_URL, CUSTOMER_PATH, {"$top": "1000"}), timeout=15)
    r.raise_for_status()
    found = []
    for c in r.json()["d"]["results"]:
        num, name = c.get("Customer", ""), c.get("CustomerName", "")
        n_num, n_name = _name(num), _name(name)
        if q and q in (n_num, n_name):
            score = 1.0
        elif q and n_name and len(q) >= 4 and (q in n_name or n_name in q):
            score = 0.7
        else:
            continue
        found.append({"customer": num, "name": name, "name_score": score})
    if references:
        wanted = {norm_ref(x) for x in references} - {""}
        for f in found:
            rows, _ = open_rows(f["customer"], company_code)
            f["open_items_with_quoted_references"] = sum(
                1 for row in rows if wanted & {norm_ref(row.get(k)) for k in REF_FIELDS})
        found.sort(key=lambda f: (-f["open_items_with_quoted_references"], -f["name_score"]))
    else:
        found.sort(key=lambda f: -f["name_score"])
    return {"query": query, "company_code": company_code, "candidates": found,
            "note": "Searches the demo customer directory (snapshot customers)."}


@mcp.tool(annotations=READ)
def get_open_items(customer: str, company_code: str, currency: Optional[str] = None, limit: int = 25) -> dict:
    """Open receivables of ONE customer in ONE company code (like FBL5N open items): count, totals,
    overdue count and the oldest `limit` items. To find specific documents use lookup_reference."""
    rows, src = open_rows(customer, company_code)
    if currency:
        rows = [r for r in rows if r["TransactionCurrency"] == currency]
    today, totals = date.today(), {}
    for r in rows:
        cur = r["TransactionCurrency"]
        totals[cur] = round(totals.get(cur, 0) + float(r["AmountInTransactionCurrency"]), 2)
    limit = max(1, min(limit, 100))
    ordered = sorted(rows, key=lambda r: iso(r.get("NetDueDate")) or "9999-12-31")
    return {"customer": customer, "company_code": company_code, "data_source": src,
            "open_item_count": len(rows), "totals_by_currency": totals,
            "overdue_count": sum(is_overdue(r, today) for r in rows),
            "items": [brief(r) for r in ordered[:limit]], "truncated": len(rows) > limit}


@mcp.tool(annotations=READ)
def lookup_reference(customer: str, company_code: str, reference: str) -> dict:
    """Find a customer's open items by ANY reference the customer quoted: invoice or billing document
    number, accounting document, PO number (held in the assignment field), payment advice number.
    Leading zeros, spaces, dashes and prefixes like 'PO' or 'Rechnung' are tolerated."""
    n = norm_ref(reference)
    digits = re.sub(r"\D", "", reference).lstrip("0")
    rows, src = open_rows(customer, company_code)
    exact, partial = [], []
    for r in rows:
        refs = {norm_ref(r.get(f)) for f in REF_FIELDS} - {""}
        if n in refs or (len(digits) >= 5 and digits in refs):
            exact.append(r)
        elif len(n) >= 5 and any(len(x) >= 5 and (n in x or x in n) for x in refs):
            partial.append(r)
    return {"reference": reference, "customer": customer, "company_code": company_code, "data_source": src,
            "exact_matches": [brief(r) for r in exact[:20]], "exact_count": len(exact),
            "partial_matches": [brief(r) for r in partial[:10]], "partial_count": len(partial)}


@mcp.tool(annotations=READ)
def get_payment_behaviour(customer: str, company_code: str) -> dict:
    """Payment history from cleared items (days paid after net due date, share paid late) plus the
    current open and overdue position. Context for disputes, promises to pay and collections."""
    cleared, src = read_items(customer_filter(customer, company_code, True), FIELDS + CLEARING_FIELDS, cap=1000)
    late = [(to_date(r["ClearingDate"]) - to_date(r["NetDueDate"])).days for r in cleared
            if r.get("DebitCreditCode") == "S" and r.get("ClearingDate") and r.get("NetDueDate")]
    open_items, _ = open_rows(customer, company_code)
    out = {"customer": customer, "company_code": company_code, "data_source": src,
           "cleared_items_analysed": len(cleared), "invoices_with_dates": len(late),
           "open_item_count": len(open_items),
           "overdue_open_items": sum(is_overdue(r, date.today()) for r in open_items),
           "note": "Based on up to 1000 cleared items - a sample, not the full history."}
    if late:
        out.update(avg_days_after_due=round(mean(late), 1), median_days_after_due=median(late),
                   share_paid_late=round(sum(d > 0 for d in late) / len(late), 2), max_days_late=max(late))
    return out


class Deduction(BaseModel):
    amount: float = Field(gt=0, description="Amount the customer deducted")
    reason_text: str = Field(description="The customer's own words for the deduction")


@mcp.tool(annotations=READ)
def propose_clearing(customer: str, company_code: str, payment_amount: float, currency: str,
                     value_date: Optional[str] = None, references: Optional[list[str]] = None,
                     deductions: Optional[list[Deduction]] = None) -> dict:
    """Deterministic matching of a payment against the customer's open items - no AI involved.
    ALWAYS use this for any amount question; never add up amounts yourself. Returns status
    (MATCHED, MATCHED_WITH_DIFFERENCE, AMBIGUOUS, NO_MATCH), the matched items, the difference and
    its type (e.g. SHORT_PAYMENT, UNEARNED_CASH_DISCOUNT, WITHIN_TOLERANCE) and an explanation."""
    rows, src = open_rows(customer, company_code)
    result = match_payment(rows, payment_amount, currency, value_date=value_date,
                           references=references or [],
                           deductions=[d.model_dump() for d in (deductions or [])])
    return {"customer": customer, "company_code": company_code, "data_source": src, **result}


@mcp.tool(annotations=READ)
def get_invoice_copy(billing_document: str) -> dict:
    """Invoice data for a billing document number, to answer a customer's request for an invoice copy."""
    r = httpx.get(f"{MOCK_URL}/mock/invoice-copy/{quote(billing_document)}", timeout=10)
    if r.status_code == 404:
        return {"found": False, "billing_document": billing_document}
    r.raise_for_status()
    return {"found": True, **r.json()}


# ---------- draft tools (nothing is posted) ----------
class ItemRef(BaseModel):
    company_code: str
    fiscal_year: str
    accounting_document: str
    accounting_document_item: str = "1"
    amount: Optional[float] = Field(None, description="Amount applied / disputed / promised for this item")


def _draft(kind, payload) -> dict:
    r = httpx.post(f"{MOCK_URL}/drafts/{kind}", json=payload, timeout=15)
    if r.status_code == 201:
        return {"ok": True, **r.json(),
                "next_step": "Draft awaits human approval. It is NOT posted in SAP."}
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = r.text
    return {"ok": False, "http_status": r.status_code, "rejected": detail,
            "next_step": "Fix the problems (use document keys exactly as returned by the read tools) or escalate."}


@mcp.tool(annotations=DRAFT)
def draft_payment_advice(customer: str, company_code: str, currency: str, payment_amount: float,
                         items: list[ItemRef], value_date: Optional[str] = None,
                         note: str = "", case_ref: Optional[str] = None) -> dict:
    """Create a payment advice DRAFT listing which open items a payment settles, with the amount
    applied per item. Use document keys and amounts exactly as returned by propose_clearing.
    A human must approve it before anything reaches SAP."""
    return _draft("payment-advice", {
        "company_code": company_code, "customer": customer, "currency": currency,
        "payment_amount": payment_amount, "value_date": value_date,
        "items": [i.model_dump() for i in items], "note": note, "case_ref": case_ref})


@mcp.tool(annotations=DRAFT)
def draft_dispute_case(customer: str, company_code: str, currency: str, disputed_amount: float,
                       reason_code: str, title: str, items: list[ItemRef],
                       note: str = "", case_ref: Optional[str] = None) -> dict:
    """Create a dispute case DRAFT for a short payment or deduction (FSCM Dispute Management).
    reason_code must come from the AR policy dispute catalogue. Put the customer's explanation and
    the policy basis in note. A human must approve it."""
    return _draft("dispute-case", {
        "company_code": company_code, "customer": customer, "currency": currency,
        "disputed_amount": disputed_amount, "reason_code": reason_code, "title": title,
        "items": [i.model_dump() for i in items], "note": note, "case_ref": case_ref})


@mcp.tool(annotations=DRAFT)
def draft_promise_to_pay(customer: str, company_code: str, currency: str, promised_amount: float,
                         promised_date: str, items: list[ItemRef],
                         note: str = "", case_ref: Optional[str] = None) -> dict:
    """Create a promise-to-pay DRAFT (FSCM Collections Management) for the invoices the customer
    promised to pay on promised_date (YYYY-MM-DD). A human must approve it."""
    return _draft("promise-to-pay", {
        "company_code": company_code, "customer": customer, "currency": currency,
        "promised_amount": promised_amount, "promised_date": promised_date,
        "items": [i.model_dump() for i in items], "note": note, "case_ref": case_ref})


# ---------- knowledge base (RAG) ----------
from kb import search as kb_search  # noqa: E402


@mcp.tool(annotations=READ)
def search_ar_policy(question: str, customer: Optional[str] = None, top_k: int = 4) -> dict:
    """Search the AR policy knowledge base: tolerances, cash discount, deductions, promises to pay,
    approval rules, dispute reason codes and customer-specific agreements. Pass the SAP customer
    number to include that customer's agreements; without it only general policy is searched.
    Cite the source_id of every passage you rely on. If nothing relevant comes back, say so and
    escalate - never invent policy."""
    try:
        passages = kb_search(question, customer=customer, k=max(1, min(top_k, 8)))
    except Exception as e:  # knowledge base must never break the agent run
        log.warning("search_ar_policy failed: %s", e)
        return {"error": f"knowledge base unavailable: {e}"}
    return {"question": question, "customer": customer, "passages": passages}


# ---------- HTTP app with bearer-token protection ----------
class BearerAuth:
    """Rejects every request without the shared bearer token, except GET /health."""

    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/health":
                await JSONResponse({"status": "ok", "mode": MODE})(scope, receive, send)
                return
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not self.token or not secrets.compare_digest(auth, f"Bearer {self.token}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    log.info("s4-ar-mcp starting in %s mode", MODE)
    uvicorn.run(BearerAuth(mcp.streamable_http_app(), TOKEN), host="0.0.0.0", port=8000)
