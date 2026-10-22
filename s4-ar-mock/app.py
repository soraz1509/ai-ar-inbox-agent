"""S/4 AR mock.

Serves a snapshot of SAP Business Accelerator Hub sandbox AR data (plus clearly labelled
seeded items) in the same OData V2 shape as API_OPLACCTGDOCITEMCUBE_SRV, and holds drafts
(payment advice, dispute case, promise to pay) that only the executor may post.
"""
import json, os, re, secrets, sqlite3, uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
EXECUTOR_KEY = os.environ.get("EXECUTOR_KEY", "")
DB_PATH = os.environ.get("DB_PATH", str(HERE / "mock.db"))
ODATA = "/sap/opu/odata/sap"
DATE_FIELDS = ("PostingDate", "NetDueDate", "CashDiscount1DueDate", "ClearingDate")
KEY = ("CompanyCode", "FiscalYear", "AccountingDocument", "AccountingDocumentItem")

app = FastAPI(title="S/4 AR mock", version="1.0")


# ---------- data ----------
def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def odata_date(value):
    if not value or str(value).startswith("/Date("):
        return value
    d = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return f"/Date({int(d.timestamp() * 1000)})/"


def iso_date(value):
    m = re.search(r"-?\d+", value or "")
    return datetime.fromtimestamp(int(m.group()) / 1000, tz=timezone.utc).date().isoformat() if m else None


def tag(rows, source):
    return [{"FinancialAccountType": "D", **r, "source": source} for r in rows]


SEED = load_json(HERE / "seed" / "seed_items.json") or {}
SEEDED = tag([{k: (odata_date(v) if k in DATE_FIELDS else v) for k, v in r.items()}
              for r in SEED.get("items", [])], "seeded")
ITEMS = (tag(load_json(HERE / "snapshot" / "open_items.json"), "sandbox_snapshot")
         + tag(load_json(HERE / "snapshot" / "cleared_items.json"), "sandbox_snapshot")
         + SEEDED)
CUSTOMERS = {c["Customer"]: c for c in load_json(HERE / "snapshot" / "customers.json")}
INDEX = {tuple(i[k] for k in KEY): i for i in ITEMS}


# ---------- minimal OData V2 query support ----------
COND = re.compile(r"^\s*(\w+)\s+eq\s+(?:'((?:[^']|'')*)'|(true|false)|(-?\d+(?:\.\d+)?))\s*$", re.I)


def parse_filter(expr):
    conds = []
    for part in re.split(r"\s+and\s+", (expr or "").strip(), flags=re.I):
        if not part:
            continue
        m = COND.match(part)
        if not m:
            raise HTTPException(400, f"Mock supports only 'Field eq value' joined by 'and'; got: {part}")
        field, text, boolean, number = m.groups()
        if text is not None:
            value = text.replace("''", "'")
        elif boolean:
            value = boolean.lower() == "true"
        else:
            value = number
        conds.append((field, value))
    return conds


def matches(row, conds):
    for field, value in conds:
        if field not in row:
            raise HTTPException(400, f"Unknown field: {field}")
        if isinstance(value, bool):
            if bool(row[field]) is not value:
                return False
        elif str(row[field]) != str(value):
            return False
    return True


def odata_list(rows, request):
    q = request.query_params
    conds = parse_filter(q.get("$filter"))
    rows = [r for r in rows if matches(r, conds)]
    total = len(rows)
    skip, top = int(q.get("$skip", 0)), int(q.get("$top", 1000))
    rows = rows[skip:skip + top]
    if q.get("$select"):
        keys = [k.strip() for k in q["$select"].split(",")] + ["source"]
        rows = [{k: r[k] for k in keys if k in r} for r in rows]
    d = {"results": rows}
    if q.get("$inlinecount") == "allpages":
        d["__count"] = str(total)
    return {"d": d}


# ---------- read endpoints (same paths as S/4HANA) ----------
@app.get("/health")
def health():
    return {"status": "ok", "items": len(ITEMS), "seeded": len(SEEDED), "customers": sorted(CUSTOMERS)}


@app.get(f"{ODATA}/API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube")
def accounting_items(request: Request):
    return odata_list(ITEMS, request)


@app.get(f"{ODATA}/API_BUSINESS_PARTNER/A_Customer")
def customer_list(request: Request):
    return odata_list(list(CUSTOMERS.values()), request)


@app.get(ODATA + "/API_BUSINESS_PARTNER/A_Customer('{customer}')")
def customer(customer: str):
    if customer not in CUSTOMERS:
        raise HTTPException(404, f"Customer {customer} not in snapshot")
    return {"d": CUSTOMERS[customer]}


@app.get("/mock/seeded-items")
def seeded_items(customer: Optional[str] = None):
    """Overlay source for sandbox mode: items that exist only in the mock."""
    return [i for i in SEEDED if customer in (None, i["Customer"])]


@app.get("/mock/invoice-copy/{billing_document}")
def invoice_copy(billing_document: str):
    ref = billing_document.lstrip("0")
    hits = [i for i in ITEMS if (i.get("BillingDocument") or "").lstrip("0") == ref and i["DebitCreditCode"] == "S"]
    if not hits:
        raise HTTPException(404, f"No invoice with billing document {billing_document}")
    i = hits[0]
    return {
        "billing_document": i["BillingDocument"], "accounting_document": i["AccountingDocument"],
        "fiscal_year": i["FiscalYear"], "company_code": i["CompanyCode"],
        "customer": i["Customer"], "customer_name": i["CustomerName"],
        "posting_date": iso_date(i["PostingDate"]), "net_due_date": iso_date(i["NetDueDate"]),
        "amount": i["AmountInTransactionCurrency"], "currency": i["TransactionCurrency"],
        "payment_terms": i["PaymentTerms"], "source": i["source"],
        "text": (f"Invoice {i['BillingDocument']} to {i['CustomerName']} ({i['Customer']}), "
                 f"dated {iso_date(i['PostingDate'])}, amount {i['AmountInTransactionCurrency']} "
                 f"{i['TransactionCurrency']}, due {iso_date(i['NetDueDate'])}."),
    }


# ---------- drafts (written by the agent via MCP, posted only by the executor) ----------
class ItemRef(BaseModel):
    company_code: str = "1010"
    fiscal_year: str
    accounting_document: str
    accounting_document_item: str = "1"
    amount: Optional[float] = Field(None, description="Amount applied / disputed / promised for this item")


class DraftBase(BaseModel):
    company_code: str = "1010"
    customer: str
    currency: str
    items: list[ItemRef] = Field(min_length=1)
    case_ref: Optional[str] = None
    note: str = ""


class PaymentAdviceDraft(DraftBase):
    payment_amount: float = Field(gt=0)
    value_date: Optional[date] = None


class DisputeCaseDraft(DraftBase):
    disputed_amount: float = Field(gt=0)
    reason_code: str
    title: str


class PromiseToPayDraft(DraftBase):
    promised_amount: float = Field(gt=0)
    promised_date: date


class Decision(BaseModel):
    decided_by: str
    comment: str = ""


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS drafts (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
        created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT, comment TEXT, sap_object_id TEXT)""")
    return con


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reject(problems):
    raise HTTPException(422, {"message": "Draft rejected by S/4 validation", "problems": problems})


def check_items(d: DraftBase):
    """Deterministic guard: every referenced item must exist, be open, and belong to this customer/currency."""
    problems = []
    for ref in d.items:
        key = (ref.company_code, ref.fiscal_year, ref.accounting_document, ref.accounting_document_item)
        label = "/".join(key)
        item = INDEX.get(key)
        if not item:
            problems.append(f"{label}: document item not found")
            continue
        if item["Customer"] != d.customer:
            problems.append(f"{label}: belongs to customer {item['Customer']}, not {d.customer}")
        if item["IsCleared"]:
            problems.append(f"{label}: already cleared")
        if item["TransactionCurrency"] != d.currency:
            problems.append(f"{label}: item currency {item['TransactionCurrency']}, draft currency {d.currency}")
    if problems:
        reject(problems)


def create(kind, draft):
    check_items(draft)
    draft_id = f"DRF-{uuid.uuid4().hex[:10].upper()}"
    with db() as con:
        con.execute("INSERT INTO drafts (id, kind, status, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                    (draft_id, kind, "PENDING_APPROVAL", draft.model_dump_json(), now()))
    return {"draft_id": draft_id, "kind": kind, "status": "PENDING_APPROVAL"}


@app.post("/drafts/payment-advice", status_code=201)
def draft_payment_advice(d: PaymentAdviceDraft):
    amounts = [i.amount for i in d.items]
    if all(a is not None for a in amounts) and abs(sum(amounts) - d.payment_amount) > 0.01:
        reject([f"item amounts sum to {sum(amounts):.2f}, payment amount is {d.payment_amount:.2f}"])
    return create("payment-advice", d)


@app.post("/drafts/dispute-case", status_code=201)
def draft_dispute_case(d: DisputeCaseDraft):
    return create("dispute-case", d)


@app.post("/drafts/promise-to-pay", status_code=201)
def draft_promise_to_pay(d: PromiseToPayDraft):
    if d.promised_date < date.today():
        reject([f"promised date {d.promised_date} is in the past"])
    return create("promise-to-pay", d)


def row_out(r):
    out = dict(r)
    out["payload"] = json.loads(out["payload"])
    return out


@app.get("/drafts")
def list_drafts(status: Optional[str] = None):
    with db() as con:
        rows = con.execute("SELECT * FROM drafts WHERE (? IS NULL OR status = ?) ORDER BY created_at DESC",
                           (status, status)).fetchall()
    return [row_out(r) for r in rows]


@app.get("/drafts/{draft_id}")
def get_draft(draft_id: str):
    with db() as con:
        r = con.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if not r:
        raise HTTPException(404, f"Draft {draft_id} not found")
    return row_out(r)


SAP_NUMBER_BASE = {"payment-advice": 8100000000, "dispute-case": 1000000000, "promise-to-pay": 4000000000}


def decide(draft_id, new_status, decision, key):
    if not EXECUTOR_KEY or not secrets.compare_digest(key or "", EXECUTOR_KEY):
        raise HTTPException(403, "Executor key required")
    with db() as con:
        r = con.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not r:
            raise HTTPException(404, f"Draft {draft_id} not found")
        if r["status"] != "PENDING_APPROVAL":
            raise HTTPException(409, f"Draft {draft_id} is already {r['status']}")
        sap_id = None
        if new_status == "EXECUTED":
            n = con.execute("SELECT COUNT(*) FROM drafts WHERE kind = ? AND status = 'EXECUTED'",
                            (r["kind"],)).fetchone()[0]
            sap_id = str(SAP_NUMBER_BASE[r["kind"]] + n + 1)
        con.execute("UPDATE drafts SET status = ?, decided_at = ?, decided_by = ?, comment = ?, sap_object_id = ? "
                    "WHERE id = ?", (new_status, now(), decision.decided_by, decision.comment, sap_id, draft_id))
    return get_draft(draft_id)


@app.post("/drafts/{draft_id}/execute")
def execute(draft_id: str, decision: Decision, x_executor_key: Optional[str] = Header(None)):
    """Creates the SAP object. A payment advice does not clear items; clearing happens at bank statement posting."""
    return decide(draft_id, "EXECUTED", decision, x_executor_key)


@app.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: str, decision: Decision, x_executor_key: Optional[str] = Header(None)):
    return decide(draft_id, "REJECTED", decision, x_executor_key)
