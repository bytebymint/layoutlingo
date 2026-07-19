import logging
from app.services.freemodel_api import call_freemodel_chat

logger = logging.getLogger(__name__)


def classify_document_with_freemodel(text, api_key=None):
    """Uses FreeModel API to classify text into standard document categories."""
    try:
        print(f"[CLASSIFY] Starting FreeModel classification (text length: {len(text)})")
        logger.info("Classifying document using FreeModel API")
        
        system_prompt = """You are an expert document classifier. Categorize the following document text into exactly one of these categories:
- Invoice
- Contract
- Certificate
- Identification document
- Other

Return ONLY the category name exactly as listed above. No punctuation, quotes, or explanation."""

        user_prompt = f"""Document Text:
\"\"\"
{text[:4000]}
\"\"\"

Classify this document."""

        category = call_freemodel_chat(
            system_prompt,
            user_prompt,
            temperature=0.1,
            max_tokens=50,
            api_key=api_key,
        )
        print(f"[CLASSIFY] FreeModel returned: {category}")
        
        if category:
            category = category.strip().replace('"', '').replace("'", "")
            valid_categories = {"Invoice", "Contract", "Certificate", "Identification document", "Other"}
            if category in valid_categories:
                print(f"[CLASSIFY] Valid category found: {category}")
                return category
            for cat in valid_categories:
                if cat.lower() in category.lower():
                    print(f"[CLASSIFY] Matched category: {cat}")
                    return cat
        
        print(f"[CLASSIFY] No valid category found, returning 'Other'")
        return "Other"

    except Exception as e:
        print(f"[CLASSIFY] FreeModel classification failed: {str(e)}")
        logger.error(f"FreeModel classification failed: {str(e)}")
        return None


def classify_document_heuristically(text):
    """Heuristic classifier using keyword frequencies — used as offline fallback."""
    text_lower = text.lower()

    weights = {
        'Invoice': {
            'invoice': 4, 'bill to': 3, 'subtotal': 3, 'amount due': 3,
            'total due': 3, 'payment terms': 2, 'unit price': 2, 'qty': 2,
            'vat': 2, 'remittance': 2, 'billing': 2, 'invoice number': 3
        },
        'Contract': {
            'agreement': 4, 'licensor': 3, 'licensee': 3, 'confidentiality': 3,
            'effective date': 3, 'in witness whereof': 4, 'shall pay': 2,
            'indemnification': 3, 'severability': 2, 'hereby agrees': 2,
            'term and termination': 3, 'intellectual property': 2
        },
        'Certificate': {
            'certificate': 4, 'hereby certifies': 3, 'has conferred': 3,
            'degree of': 3, 'diploma': 3, 'academic record': 2,
            'achievement': 2, 'certified that': 3, 'graduation': 2
        },
        'Identification document': {
            'passport': 4, 'national id': 3, 'driving license': 3,
            'drivers license': 3, 'date of birth': 3, 'nationality': 3,
            'place of birth': 3, 'date of expiration': 3, 'date of issue': 3,
            'sex': 1, 'surname': 2, 'given names': 2
        }
    }

    scores = {category: 0 for category in weights}
    for category, keywords in weights.items():
        for keyword, weight in keywords.items():
            if keyword in text_lower:
                occurrences = min(text_lower.count(keyword), 3)
                scores[category] += weight * occurrences

    best_category = "Other"
    best_score = 0
    for category, score in scores.items():
        if score > best_score:
            best_score = score
            best_category = category

    return best_category if best_score >= 3 else "Other"


def classify_document(text, api_key=None):
    """Main classification handler — tries FreeModel first, falls back to heuristics."""
    if not text or len(text.strip()) == 0:
        return "Other"

    heuristic_result = classify_document_heuristically(text)

    # Attempt online FreeModel classification when api_key is provided (even if empty string)
    if api_key is not None:
        result = classify_document_with_freemodel(text, api_key)
        if result and result != "Other":
            logger.info(f"Classified as '{result}' via FreeModel.")
            return result
        if result == "Other" and heuristic_result != "Other":
            logger.info(
                "FreeModel returned 'Other', but heuristic classifier found '%s'.",
                heuristic_result,
            )
            return heuristic_result
        if result:
            return result

    logger.info("Using local heuristic classifier.")
    return heuristic_result

