#!/usr/bin/env python
"""Upload a new test document to check logging."""

import requests
from io import BytesIO

BASE_URL = "http://127.0.0.1:5000"

# Create a PDF with actual text content
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
100 700 Td
(PURCHASE ORDER) Tj
0 -20 Td
(PO Number: PO-2026-5432) Tj
0 -20 Td
(Vendor: Acme Corp) Tj
0 -20 Td
(Date: July 11, 2026) Tj
0 -20 Td
(Amount: $15,500.00) Tj
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

files = {'file': ('test_po.pdf', BytesIO(pdf_content), 'application/pdf')}

print("Uploading test document...")
response = requests.post(f"{BASE_URL}/api/upload", files=files, timeout=30)
print(f"Status: {response.status_code}")
data = response.json()
doc_id = data.get('document', {}).get('id')
print(f"Document ID: {doc_id}")
print("\nCheck the app terminal for logging output")
