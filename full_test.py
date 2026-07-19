#!/usr/bin/env python
"""Test the complete app integration including chat."""

import requests
import json
import time
from io import BytesIO

BASE_URL = "http://127.0.0.1:5000"

# Create a better PDF with actual readable content
pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< >>
stream
BT
/F1 12 Tf
50 700 Td
(SERVICE INVOICE) Tj
0 -25 Td
(Invoice Number: INV-2026-7890) Tj
0 -20 Td
(Company: ABC Technology Services) Tj
0 -20 Td
(Date: July 11, 2026) Tj
0 -20 Td
(Due Date: August 11, 2026) Tj
0 -30 Td
(SERVICES PROVIDED:) Tj
0 -20 Td
(Consulting Services: $5,000.00) Tj
0 -20 Td
(Software Development: $8,500.00) Tj
0 -20 Td
(Testing and QA: $2,000.00) Tj
0 -30 Td
(Total Amount: $15,500.00) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000207 00000 n 
trailer
<< /Size 5 /Root 1 0 R >>
startxref
304
%%EOF
"""

print("=" * 70)
print("COMPREHENSIVE APP INTEGRATION TEST")
print("=" * 70)

# Step 1: Upload
print("\n[1/4] UPLOADING DOCUMENT...")
files = {'file': ('service_invoice.pdf', BytesIO(pdf_content), 'application/pdf')}
response = requests.post(f"{BASE_URL}/api/upload", files=files, timeout=30)
doc_id = response.json().get('document', {}).get('id')
print(f"✓ Uploaded (ID: {doc_id}, Status: {response.status_code})")

# Step 2: Wait and check processing
print("\n[2/4] WAITING FOR PROCESSING...")
for i in range(6):
    time.sleep(2)
    response = requests.get(f"{BASE_URL}/api/document/{doc_id}/status", timeout=30)
    data = response.json()
    status = data.get('status')
    print(f"  Check {i+1}: {status}")
    if status == 'Completed':
        print(f"✓ Completed! Type: {data.get('doc_type')}, Score: {data.get('confidence_score')}")
        extraction = data.get('extracted_data', {})
        print(f"  Extracted Company: {extraction.get('company', 'N/A')}")
        print(f"  Extracted Total: {extraction.get('total', 'N/A')}")
        break

# Step 3: Test chat
print("\n[3/4] TESTING CHAT...")
test_queries = [
    "What is the total amount on this invoice?",
    "Which company issued this invoice?",
    "What is the invoice number?",
]

for query in test_queries:
    response = requests.post(
        f"{BASE_URL}/api/document/{doc_id}/chat",
        json={"message": query},
        timeout=30
    )
    answer = response.json().get('answer', 'ERROR')
    print(f"  Q: {query}")
    print(f"  A: {answer[:120]}..." if len(answer) > 120 else f"  A: {answer}")
    print()

print("=" * 70)
print("TEST COMPLETE - Check app logs for [API], [CLASSIFY], [EXTRACT], [CHAT] output")
print("=" * 70)
