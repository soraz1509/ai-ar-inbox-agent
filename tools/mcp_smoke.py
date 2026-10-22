"""Step 4 smoke test: connect to s4-ar-mcp like an agent would, list tools, call each once."""
import asyncio, sys
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sap_sandbox import ENV

URL = "http://localhost:8002/mcp"
CALLS = [
    ("find_customer", {"query": "Hypercom AG", "references": ["PAYADV7", "PAYADV8", "PAYADV9"]}),
    ("get_open_items", {"customer": "CASH_APP", "company_code": "1010"}),
    ("lookup_reference", {"customer": "10100001", "company_code": "1010", "reference": "PO 4500017342"}),
    ("get_payment_behaviour", {"customer": "10100013", "company_code": "1010"}),
    ("propose_clearing", {"customer": "10100013", "company_code": "1010", "payment_amount": 127135.40,
                          "currency": "EUR", "value_date": "2026-10-22",
                          "references": ["1800000999", "1800001000", "1800001001"],
                          "deductions": [{"amount": 2594.60, "reason_text": "Aktionsrabatt Oktober 2%"}]}),
    ("draft_dispute_case", {"customer": "10100013", "company_code": "1010", "currency": "EUR",
                            "disputed_amount": 100.0, "reason_code": "TEST", "title": "should be rejected",
                            "items": [{"company_code": "1010", "fiscal_year": "2022",
                                       "accounting_document": "1899999999"}]}),
    ("get_invoice_copy", {"billing_document": "90027528"}),
]


async def main():
    headers = {"Authorization": f"Bearer {ENV['MCP_BEARER_TOKEN']}"}
    async with streamablehttp_client(URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", ", ".join(t.name for t in tools.tools))
            for name, args in CALLS:
                res = await session.call_tool(name, args)
                text = res.content[0].text if res.content else ""
                print(f"\n=== {name} {'(ERROR)' if res.isError else ''} ===\n{text[:1200]}")

asyncio.run(main())
