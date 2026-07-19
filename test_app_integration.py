#!/usr/bin/env python
"""Test the app's document processing with FreeModel API."""

import requests
import json
import time
from io import BytesIO

BASE_URL = "http://127.0.0.1:5000"

def upload_test_document():
    """Upload a test document."""
    print("=" * 60)
    print("1. Uploading test document...")
    print("=" * 60)
    
    # Create a simple PDF-like file
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
(INVOICE) Tj
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
    
    files = {'file': ('test_invoice.pdf', BytesIO(pdf_content), 'application/pdf')}
    
    try:
        response = requests.post(f"{BASE_URL}/api/upload", files=files, timeout=30)
        print(f"Upload Status: {response.status_code}")
        print(f"Upload Response: {response.text[:500]}")
        
        if response.status_code == 201:
            data = response.json()
            doc_id = data.get('document', {}).get('id')
            print(f"✓ Document uploaded successfully (ID: {doc_id})")
            return doc_id
        else:
            print("✗ Upload failed")
            return None
    except Exception as e:
        print(f"✗ Error uploading: {str(e)}")
        return None

def check_processing_status(doc_id):
    """Check document processing status."""
    print("\n" + "=" * 60)
    print("2. Checking document processing status...")
    print("=" * 60)
    
    try:
        response = requests.get(f"{BASE_URL}/api/document/{doc_id}/status", timeout=30)
        print(f"Status Response: {response.status_code}")
        data = response.json()
        print(f"Processing Status: {data.get('status')}")
        print(f"Document Type: {data.get('doc_type')}")
        print(f"Confidence Score: {data.get('confidence_score')}")
        print(f"Extracted Data: {json.dumps(data.get('extracted_data', {}), indent=2)}")
        return data
    except Exception as e:
        print(f"✗ Error checking status: {str(e)}")
        return None

def test_chat(doc_id):
    """Test chat with document."""
    print("\n" + "=" * 60)
    print("3. Testing chat with document...")
    print("=" * 60)
    
    try:
        payload = {"message": "What is the total amount?"}
        response = requests.post(
            f"{BASE_URL}/api/document/{doc_id}/chat", 
            json=payload, 
            timeout=30
        )
        print(f"Chat Status: {response.status_code}")
        data = response.json()
        print(f"Answer: {data.get('answer', 'No answer')}")
        return data
    except Exception as e:
        print(f"✗ Error in chat: {str(e)}")
        return None

if __name__ == '__main__':
    # Upload document
    doc_id = upload_test_document()
    
    if doc_id:
        # Wait for processing
        print("\nWaiting 3 seconds for document processing...")
        time.sleep(3)
        
        # Check status
        status = check_processing_status(doc_id)
        
        # Test chat if document is completed
        if status and status.get('status') == 'Completed':
            test_chat(doc_id)
        else:
            print(f"\nDocument still processing (Status: {status.get('status') if status else 'unknown'})")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
