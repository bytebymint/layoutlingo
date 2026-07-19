import re
import json
import logging
import math

logger = logging.getLogger(__name__)

# Very basic list of English stopwords to ignore in offline TF similarity
STOPWORDS = {
    'a', 'about', 'above', 'after', 'again', 'against', 'all', 'am', 'an', 'and', 'any', 'are', 'arent', 'as', 'at',
    'be', 'because', 'been', 'before', 'being', 'below', 'between', 'both', 'but', 'by', 'cant', 'cannot', 'could',
    'did', 'didnt', 'do', 'does', 'doesnt', 'doing', 'dont', 'down', 'during', 'each', 'few', 'for', 'from', 'further',
    'had', 'hadnt', 'has', 'hasnt', 'have', 'havent', 'having', 'he', 'hed', 'hell', 'hes', 'her', 'here', 'heres',
    'hers', 'herself', 'him', 'himself', 'his', 'how', 'hows', 'i', 'id', 'ill', 'im', 'ive', 'if', 'in', 'into', 'is',
    'isnt', 'it', 'its', 'itself', 'lets', 'me', 'more', 'most', 'mustnt', 'my', 'myself', 'no', 'nor', 'not', 'of', 'off',
    'on', 'once', 'only', 'or', 'other', 'ought', 'our', 'ours', 'ourselves', 'out', 'over', 'own', 'same', 'shant', 'she',
    'shed', 'shell', 'shes', 'should', 'shouldnt', 'so', 'some', 'such', 'than', 'that', 'thats', 'the', 'their', 'theirs',
    'them', 'themselves', 'then', 'there', 'theres', 'these', 'they', 'theyd', 'theyll', 'theyre', 'theyve', 'this',
    'those', 'through', 'to', 'too', 'under', 'until', 'up', 'very', 'was', 'wasnt', 'we', 'wed', 'well', 'were', 'weve',
    'werent', 'what', 'whats', 'when', 'whens', 'where', 'wheres', 'which', 'while', 'who', 'whos', 'whom', 'why', 'whys',
    'with', 'wont', 'would', 'wouldnt', 'you', 'youd', 'youll', 'youre', 'youve', 'your', 'yours', 'yourself', 'yourselves'
}


def chunk_text(text, chunk_size=800, overlap=150):
    """
    Splits text into chunks of roughly chunk_size characters with overlap.
    Attempts to break at paragraph or sentence boundaries.
    """
    chunks = []
    if not text:
        return chunks
        
    start = 0
    text_len = len(text)
    
    while start < text_len:
        end = min(start + chunk_size, text_len)
        
        if end < text_len:
            last_boundary = -1
            for char in ['.', '?', '!', '\n']:
                pos = text.rfind(char, start, end)
                if pos > last_boundary:
                    last_boundary = pos
                    
            if last_boundary > start + (chunk_size // 2):
                end = last_boundary + 1
                
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
            
        start = end - overlap
        if start >= text_len or end == text_len:
            break
            
    return chunks


def tokenize_and_count(text):
    """Tokenizes text and returns term-frequency dictionary (excluding stopwords)."""
    words = re.findall(r'\b[a-z]{2,15}\b', text.lower())
    tf = {}
    for word in words:
        if word not in STOPWORDS:
            tf[word] = tf.get(word, 0) + 1
    return tf


def generate_embedding(text, api_key=None):
    """
    Generates offline TF-based embedding for a chunk of text.
    Ensures 100% reliability locally since Freemodel dev API doesn't support embeddings.
    """
    tf_dict = tokenize_and_count(text)
    return json.dumps(tf_dict)


def generate_query_embedding(query, api_key=None):
    """Generates query embedding using local TF-based tokenizer."""
    return tokenize_and_count(query)


def tf_cosine_similarity(dict1, dict2):
    """Calculates cosine similarity between two term-frequency dictionaries."""
    intersection = set(dict1.keys()) & set(dict2.keys())
    if not intersection:
        return 0.0
    dot_product = sum(dict1[x] * dict2[x] for x in intersection)
    norm1 = math.sqrt(sum(val * val for val in dict1.values()))
    norm2 = math.sqrt(sum(val * val for val in dict2.values()))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return dot_product / (norm1 * norm2)


def calculate_similarity(query_emb, stored_emb_json):
    """
    Calculates similarity between query embedding and stored embedding JSON.
    Handles mixed formats safely.
    """
    try:
        stored_val = json.loads(stored_emb_json)
    except Exception as e:
        logger.warning(f"Failed to parse stored embedding JSON: {str(e)}, returning 0.0")
        return 0.0
    
    # If the database contains an old float list embedding from Gemini, ignore it
    if isinstance(query_emb, dict) and isinstance(stored_val, dict):
        return tf_cosine_similarity(query_emb, stored_val)
        
    return 0.0
