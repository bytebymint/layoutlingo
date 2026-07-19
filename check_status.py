import time
import requests

for i in range(8):
    time.sleep(2)
    response = requests.get('http://127.0.0.1:5000/api/document/1/status')
    data = response.json()
    status = data.get("status")
    doc_type = data.get("doc_type")
    score = data.get("confidence_score")
    print(f'Check {i+1}: Status={status}, Type={doc_type}, Score={score}')
    if status == 'Completed':
        print(f'Extracted Data: {data.get("extracted_data")}')
        break
