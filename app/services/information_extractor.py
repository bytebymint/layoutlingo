import logging
import re
import json
from app.services.freemodel_api import call_freemodel_chat

logger = logging.getLogger(__name__)

def clean_json_response(text):
    """Cleans markdown JSON blocks from model outputs."""
    cleaned = text.strip()
    # Remove markdown code fence if present
    if cleaned.startswith("```"):
        match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
    return cleaned

def extract_with_freemodel(text, doc_type, api_key):
    """Sends document text to FreeModel to extract structured JSON metadata."""
    try:
        print(f"[EXTRACT] Starting FreeModel extraction for {doc_type} (text length: {len(text)})")
        logger.info(f"FreeModel extraction for document type: {doc_type}")
        
        system_prompt = """You are an expert document data extractor. Extract structured information from the following document.
You must output a single valid JSON object. Do not wrap it in markdown codeblocks (like ```json), do not write any greetings or explanations.

The JSON object must contain the following keys:
1. "type": The document type (e.g. "Invoice", "Contract", "Certificate", "Identification document", "Other").
2. "company": The primary company, organization, or institution issuing or named in the document.
3. "date": The primary relevant date formatted as YYYY-MM-DD, or null if none found.
4. "total": The primary key numeric/text value (e.g. invoice total, contract value, passport number, degree name).
5. "details": A flat JSON dictionary containing other key-value pairs of important extracted fields."""

        user_prompt = f"""Extract structured JSON from this {doc_type} document:

Document Text:
\"\"\"
{text[:4000]}
\"\"\"

Output valid JSON only:"""

        response = call_freemodel_chat(
            system_prompt,
            user_prompt,
            temperature=0.1,
            max_tokens=1000,
            api_key=api_key,
        )
        print(f"[EXTRACT] FreeModel response: {response}")
        if response:
            cleaned_response = clean_json_response(response)
            result = json.loads(cleaned_response)
            print(f"[EXTRACT] Successfully parsed JSON: {result}")
            return result
        print(f"[EXTRACT] No response from FreeModel")
    except Exception as e:
        print(f"[EXTRACT] FreeModel extraction failed: {str(e)}")
        logger.error(f"FreeModel data extraction failed: {str(e)}")
        return None

def extract_heuristically(text, doc_type):
    """Fallback heuristic extractor that parses text via regex to return structured JSON."""
    result = {
        "type": doc_type,
        "company": "Unknown Entity",
        "date": None,
        "total": None,
        "details": {}
    }
    
    # 1. Date Extraction Helper
    # Match YYYY-MM-DD, DD/MM/YYYY, Month DD, YYYY
    date_patterns = [
        r'\b\d{4}-\d{2}-\d{2}\b', # 2026-07-11
        r'\b\d{2}/\d{2}/\d{4}\b', # 11/07/2026
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b' # July 11, 2026
    ]
    
    all_dates = []
    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            all_dates.extend(matches)
            
    if all_dates:
        result["date"] = all_dates[0] # Take first date as primary

    # 2. Company Extraction Helper (look for patterns like Inc, LLC, Corp)
    company_match = re.search(r'\b([A-Z][a-zA-Z0-9 \t&]+(?:\bLLC\b|\bInc\b|\bCorp\b|\bCorporation\b|\bLtd\b|\bSolutions\b|\bSystems\b|\bUniversity\b))\b', text)
    if company_match:
        result["company"] = company_match.group(1).strip()
    elif "passport" in text.lower():
        result["company"] = "Government Authority"

    # 3. Specialized extraction by doc type
    text_lower = text.lower()
    
    if doc_type == "Invoice":
        # Extract invoice total
        total_patterns = [
            r'(?:total|total due|amount due|balance due)[:\s]*\$?\s*([0-9,]+\.[0-9]{2})',
            r'(?:total|total due|amount due|balance due)[:\s]*([0-9,]+\s*AED)',
            r'total[:\s]*\$?\s*([0-9,]+)'
        ]
        for pattern in total_patterns:
            match = re.search(pattern, text_lower)
            if match:
                result["total"] = match.group(1).strip()
                break
        
        # Details
        inv_num_match = re.search(r'(?:invoice number|invoice #|inv-?#)[:\s]*([a-zA-Z0-9\-]+)', text_lower)
        if inv_num_match:
            result["details"]["invoice_number"] = inv_num_match.group(1).strip().upper()
            
        due_date_match = re.search(r'(?:due date|payment due)[:\s]*([^\n]+)', text_lower)
        if due_date_match:
            result["details"]["due_date"] = due_date_match.group(1).strip()

        # Add subtotal and tax if present
        subtotal_match = re.search(r'subtotal[:\s]*\$?\s*([0-9,]+\.[0-9]{2})', text_lower)
        if subtotal_match:
            result["details"]["subtotal"] = f"${subtotal_match.group(1)}"
        tax_match = re.search(r'tax[:\s]*\$?\s*([0-9,]+\.[0-9]{2})', text_lower)
        if tax_match:
            result["details"]["tax"] = f"${tax_match.group(1)}"

    elif doc_type == "Contract":
        # Extract contract size/value or description
        value_match = re.search(r'(?:fee of|sum of|value of)[:\s]*\$?\s*([0-9,]+(?:\.[0-9]{2})?)', text_lower)
        if value_match:
            result["total"] = f"${value_match.group(1)}"
        else:
            result["total"] = "Service Agreement"
            
        # Details
        licensor_match = re.search(r'(?:licensor|first party)[:\s]*([^\n]+)', text_lower)
        licensee_match = re.search(r'(?:licensee|second party)[:\s]*([^\n]+)', text_lower)
        if licensor_match:
            result["details"]["licensor"] = licensor_match.group(1).strip()
        if licensee_match:
            result["details"]["licensee"] = licensee_match.group(1).strip()
            
        term_match = re.search(r'term[:\s]*([^\n]+)', text_lower)
        if term_match:
            result["details"]["contract_term"] = term_match.group(1).strip()

    elif doc_type == "Certificate":
        # Degree / Title is total
        degree_match = re.search(r'(?:degree of|certified that|conferred upon.*the degree of)[:\s]*([^\n,]+)', text_lower)
        if degree_match:
            result["total"] = degree_match.group(1).strip().title()
        else:
            result["total"] = "Certificate of Achievement"
            
        # Details
        recipient_match = re.search(r'(?:conferred upon|awarded to|know that)[:\s]*([A-Z\s\.]{4,25})', text)
        if recipient_match:
            result["details"]["recipient"] = recipient_match.group(1).strip()
            
        auth_match = re.search(r'(?:dean|chancellor|director)[:\s]*([^\n]+)', text_lower)
        if auth_match:
            result["details"]["authority"] = auth_match.group(1).strip().title()

    elif doc_type == "Identification document":
        # Passport / ID number is total
        id_match = re.search(r'(?:passport no|id no|document no|passport number)[:\s]*([a-zA-Z0-9]+)', text_lower)
        if id_match:
            result["total"] = id_match.group(1).strip().upper()
        else:
            # Look for general alphanumeric strings that look like passport numbers
            fallback_id = re.search(r'\b[A-Z][0-9]{8}\b', text)
            if fallback_id:
                result["total"] = fallback_id.group(0)
            else:
                result["total"] = "Identity Card"
                
        # Details
        surname_match = re.search(r'surname[:\s]*([a-zA-Z]+)', text_lower)
        given_match = re.search(r'given names?[:\s]*([a-zA-Z\s]+)', text_lower)
        dob_match = re.search(r'date of birth[:\s]*([^\n]+)', text_lower)
        expiry_match = re.search(r'(?:expiry|date of expiration)[:\s]*([^\n]+)', text_lower)
        
        if surname_match:
            result["details"]["surname"] = surname_match.group(1).strip().upper()
        if given_match:
            result["details"]["given_names"] = given_match.group(1).strip().title()
        if dob_match:
            result["details"]["date_of_birth"] = dob_match.group(1).strip()
        if expiry_match:
            result["details"]["expiration_date"] = expiry_match.group(1).strip()

    else:
        # General document
        result["total"] = "General Analysis"
        result["details"]["character_count"] = len(text)
        result["details"]["word_count"] = len(text.split())

    return result

def extract_document_info(text, doc_type, api_key=None):
    """Main extraction handler, calling Gemini or falling back to heuristics."""
    if not text:
        return {
            "type": doc_type,
            "company": "Empty Document",
            "date": None,
            "total": None,
            "details": {}
        }
        
    # Attempt online FreeModel metadata extraction when api_key is provided (even if empty string)
    if api_key is not None:
        result = extract_with_freemodel(text, doc_type, api_key)
        if result:
            logger.info("Metadata extracted successfully using FreeModel.")
            return result
        
    logger.info("Using local heuristic metadata extractor.")
    return extract_heuristically(text, doc_type)


