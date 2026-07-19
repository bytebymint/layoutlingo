import json
import logging
from app import db
from app.models.document import Document, DocumentChunk
from app.services.freemodel_api import call_freemodel_chat
from app.services.embedding_service import generate_query_embedding, tf_cosine_similarity
from app.services.information_extractor import clean_json_response

logger = logging.getLogger(__name__)

def compare_documents(doc1_id, doc2_id, api_key=None):
    """
    Perform semantic comparison between two documents.
    Returns (result_dict, error_msg).
    """
    # 1. Fetch both documents
    doc1 = Document.query.get(doc1_id)
    doc2 = Document.query.get(doc2_id)

    if not doc1:
        return None, f"Document 1 (ID {doc1_id}) not found."
    if not doc2:
        return None, f"Document 2 (ID {doc2_id}) not found."

    if doc1.status != 'Completed' or doc2.status != 'Completed':
        return None, "Both documents must be successfully processed (status = 'Completed') before comparison."

    # 2. Retrieve texts
    text1 = doc1.ocr_text or ""
    text2 = doc2.ocr_text or ""

    if not text1.strip() or not text2.strip():
        return None, "One or both documents have empty OCR text."

    # 3. Calculate semantic similarity using the local TF cosine similarity
    try:
        emb1 = generate_query_embedding(text1)
        emb2 = generate_query_embedding(text2)
        sim_score = tf_cosine_similarity(emb1, emb2)
    except Exception as e:
        logger.warning(f"Error calculating text similarity: {str(e)}")
        sim_score = 0.0

    # Represent similarity percentage (rounded to integer)
    sim_percent = int(round(sim_score * 100))

    # 4. Formulate LLM reasoning comparison prompt
    system_prompt = (
        "You are an expert Document Comparison AI Engine. Your task is to perform a detailed semantic comparison between two documents (Document 1 and Document 2) and return a structured JSON report.\n"
        "Ensure you output ONLY a valid JSON object. Do not include any explanations, code block formatting (like ```json), or introductory/concluding remarks. The output must parse directly as JSON."
    )

    user_prompt = f"""
We want to compare Document 1 (Source / "Before") and Document 2 (Target / "After").

DOCUMENT 1 DETAILS:
- Filename: {doc1.original_filename}
- Classified Type: {doc1.doc_type or 'Unknown'}
- Metadata: {json.dumps(doc1.parsed_extracted_data)}
- Excerpt (First 6000 chars):
\"\"\"
{text1[:6000]}
\"\"\"

DOCUMENT 2 DETAILS:
- Filename: {doc2.original_filename}
- Classified Type: {doc2.doc_type or 'Unknown'}
- Metadata: {json.dumps(doc2.parsed_extracted_data)}
- Excerpt (First 6000 chars):
\"\"\"
{text2[:6000]}
\"\"\"

Perform a detailed semantic comparison between Document 1 and Document 2.
You must return a valid JSON object with the exact keys below:
{{
  "overview": {{
    "document_1_type": "Document 1 type",
    "document_2_type": "Document 2 type",
    "summary": "Concise summary of differences and how the documents relate to each other"
  }},
  "changed_information": [
    {{
      "category": "e.g. Payment terms, Interest rate, Party names, Liability limits",
      "before": "value or clause in Document 1",
      "after": "value or clause in Document 2",
      "importance": "High/Medium/Low"
    }}
  ],
  "added_content": [
    "precise description of important content/clauses appearing only in Document 2"
  ],
  "removed_content": [
    "precise description of important content/clauses present in Document 1 but missing from Document 2"
  ],
  "risk_analysis": [
    "specific warning or risk, e.g., legal liabilities, increased costs, unfavorable terms, or missing protections"
  ],
  "final_recommendation": "A concise professional recommendation or summary of next steps"
}}

Respond ONLY with this valid JSON structure:"""

    try:
        ai_response = call_freemodel_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=2500,
            api_key=api_key
        )

        if not ai_response:
            return None, "Empty response from AI comparison engine."

        cleaned = clean_json_response(ai_response)
        result = json.loads(cleaned)

        # Inject computed similarity score into overview
        if "overview" in result and isinstance(result["overview"], dict):
            result["overview"]["similarity_score"] = sim_percent
        else:
            result["overview"] = {
                "document_1_type": doc1.doc_type or "Unknown",
                "document_2_type": doc2.doc_type or "Unknown",
                "summary": "Comparison complete.",
                "similarity_score": sim_percent
            }

        return result, None

    except json.JSONDecodeError as jde:
        logger.exception("Failed to parse AI response as JSON")
        # Try a basic heuristic parser or structure fallback
        fallback_result = {
            "overview": {
                "document_1_type": doc1.doc_type or "Unknown",
                "document_2_type": doc2.doc_type or "Unknown",
                "summary": "Failed to parse AI report as JSON structure. The raw text response is preserved.",
                "similarity_score": sim_percent
            },
            "changed_information": [],
            "added_content": [],
            "removed_content": [],
            "risk_analysis": ["AI response formatting issue encountered. Please re-run the comparison."],
            "final_recommendation": "Review raw response.",
            "raw_text": ai_response if ai_response else ""
        }
        return fallback_result, None
    except Exception as e:
        logger.exception("Comparison processing pipeline failed")
        return None, str(e)
