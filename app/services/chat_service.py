import logging
import re
from app.services.freemodel_api import call_freemodel_chat
from app.models.document import DocumentChunk
from app.services.embedding_service import generate_query_embedding, calculate_similarity

logger = logging.getLogger(__name__)

def retrieve_relevant_chunks(document_id, query_text, api_key=None, top_k=3):
    """
    Retrieves the top_k most relevant chunks for a document based on cosine similarity
    to the query. Falls back to keyword matching if embedding retrieval fails.
    """
    try:
        query_emb = generate_query_embedding(query_text, api_key)
        chunks = DocumentChunk.query.filter_by(document_id=document_id).all()
        if not chunks:
            return []
        
        scored_chunks = []
        for chunk in chunks:
            score = calculate_similarity(query_emb, chunk.embedding)
            scored_chunks.append((chunk, score))
        
        scored_chunks.sort(key=lambda x: x[1], reverse=True)
        
        if scored_chunks and scored_chunks[0][1] > 0.05:
            logger.info(f"Top RAG match score for query '{query_text[:30]}': {scored_chunks[0][1]:.4f}")
            return [chunk for chunk, score in scored_chunks[:top_k]]
    except Exception as e:
        logger.warning(f"Similarity chunk retrieval failed: {str(e)}")
    
    # Fallback keyword matching
    logger.info(f"Keyword fallback retrieval for query: {query_text[:30]}")
    chunks = DocumentChunk.query.filter_by(document_id=document_id).all()
    if not chunks:
        return []
    
    query_words = set(re.findall(r'\b[a-z0-9]{2,20}\b', query_text.lower()))
    scored_chunks = []
    
    for chunk in chunks:
        chunk_words = set(re.findall(r'\b[a-z0-9]{2,20}\b', (chunk.text_content or "").lower()))
        overlap = len(query_words & chunk_words)
        scored_chunks.append((chunk, overlap))
    
    scored_chunks.sort(key=lambda x: x[1], reverse=True)
    return [chunk for chunk, score in scored_chunks[:top_k]]

def generate_rag_answer_online(query, chunks, api_key):
    """Uses Freemodel API to answer questions based strictly on the retrieved chunks."""
    try:
        print(f"[CHAT] Using Freemodel RAG with {len(chunks)} chunks for query: {query}")
        context_str = "\n\n---\n\n".join([chunk.text_content for chunk in chunks])

        system_prompt = (
            "You are an expert AI Document Assistant. Your task is to answer the user's question using ONLY the provided document context.\n"
            "Do not use outside knowledge. If the answer cannot be found in the context, reply: "
            "\"I cannot find the answer to this question in the uploaded document.\""
        )

        user_prompt = f"""Document Context:
\"\"\"
{context_str}
\"\"\"

User Question: {query}

Answer based ONLY on the document context:"""

        response = call_freemodel_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=1000,
            api_key=api_key,
        )
        print(f"[CHAT] Freemodel RAG response: {response}")
        if response:
            return response.strip()
    except Exception as e:
        print(f"[CHAT] Freemodel RAG answer generation failed: {str(e)}")
        logger.error(f"Freemodel RAG answer generation failed: {str(e)}")

    return None

def generate_rag_answer_heuristically(query, chunks):
    """Generate a direct answer from document text without requiring Freemodel."""
    print(f"[CHAT] Using heuristic fallback with {len(chunks)} chunks for query: {query}")
    if not chunks:
        return "I could not find any text in the document related to your question."

    query_words = set(re.findall(r'\b[a-z0-9]{2,20}\b', query.lower()))
    if not query_words:
        return "I could not understand that question. Please ask again about the document content."

    best_match = None
    best_score = -1
    all_sentences = []

    for chunk in chunks:
        text = chunk.text_content or ""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        for sentence in sentences:
            sentence_clean = sentence.strip()
            if not sentence_clean or len(sentence_clean) < 5:
                continue
            all_sentences.append(sentence_clean)
            sentence_words = set(re.findall(r'\b[a-z0-9]{2,20}\b', sentence_clean.lower()))
            overlap = query_words & sentence_words
            score = len(overlap)
            if score > best_score:
                best_score = score
                best_match = sentence_clean

    if best_match and best_score > 0:
        shortened = best_match if len(best_match) <= 600 else best_match[:600] + "..."
        return shortened

    if chunks:
        best_chunk = chunks[0]
        best_chunk_score = 0
        for chunk in chunks:
            text = chunk.text_content or ""
            chunk_words = set(re.findall(r'\b[a-z0-9]{2,20}\b', text.lower()))
            overlap = len(query_words & chunk_words)
            if overlap > best_chunk_score:
                best_chunk_score = overlap
                best_chunk = chunk
        
        chunk_text = best_chunk.text_content or ""
        if len(chunk_text) > 700:
            chunk_text = chunk_text[:700] + "..."
        if chunk_text:
            return chunk_text
    
    return "I could not find relevant information in the document for your question."

def answer_document_query(document_id, query, api_key=None):
    """
    Answers a query about a document. Retrieves relevant chunks,
    then uses Freemodel API or offline fallback extractives to respond.
    """
    print(f"[CHAT] Answering query for document {document_id}: {query}")
    if not query or not query.strip():
        return "Please enter a valid question."
    
    try:
        relevant_chunks = retrieve_relevant_chunks(document_id, query, api_key, top_k=3)
        if not relevant_chunks:
            return "The document does not appear to contain any indexable text content to search."
            
        # Try online Freemodel RAG if api_key is provided (even if empty string)
        if api_key is not None:
            answer = generate_rag_answer_online(query, relevant_chunks, api_key)
            if answer:
                return answer
        
        # Fallback to heuristic answer
        return generate_rag_answer_heuristically(query, relevant_chunks)

    
    except Exception as e:
        logger.exception(f"Error answering query for document {document_id}: {str(e)}")
        return "An error occurred while processing your question. Please try again."
