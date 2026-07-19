import logging
from app.models.document import Document, DocumentChunk
from app.services.freemodel_api import call_freemodel_chat

logger = logging.getLogger(__name__)


def analyze_document(document_id, api_key=None, top_k=None):
    """Generate a structured AI analysis report for a single document.

    Returns a tuple ``(analysis_text, error_msg)`` where ``error_msg`` is ``None`` on success.
    """
    # Retrieve document
    doc = Document.query.get(document_id)
    if not doc:
        return None, f"Document with id {document_id} not found."
    if not doc.ocr_text:
        return None, "Document OCR text is empty."

    # Retrieve chunks as context (all if top_k is None)
    query = DocumentChunk.query.filter_by(document_id=document_id).order_by(DocumentChunk.chunk_index)
    if top_k is not None:
        query = query.limit(top_k)
    chunks = query.all()
    context = "\n\n---\n\n".join([c.text_content for c in chunks]) if chunks else ""

    system_prompt = "You are a professional AI Document Analyst. Provide a structured markdown report."
    user_prompt = f"""
    Provide a comprehensive analysis of the following document. The report must contain the exact sections:
    ## Document Overview
    ## Key Structured Information
    ## Important Sections
    ## Insights
    ## Risks / Anomalies
    ## Final Summary

    Use markdown headings (##) and bullet points where appropriate. Do NOT add any information that is not present in the document text.

    Document Text:\n{doc.ocr_text}\n
    Relevant excerpts (if any):\n{context}\n    """
    try:
        answer = call_freemodel_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=2000,
            api_key=api_key,
        )
        if answer:
            return answer.strip(), None
        else:
            return None, "Empty answer from AI service."
    except Exception as e:
        logger.exception("Error during document analysis: %s", e)
        return None, str(e)
