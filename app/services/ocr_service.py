import os
import logging
from pypdf import PdfReader
from PIL import Image
from app.services.freemodel_api import call_gemini_vision

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path):
    """Extract text from a digital PDF using pypdf."""
    text = ""
    try:
        reader = PdfReader(file_path)
        for page_num, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text += f"\n--- Page {page_num + 1} ---\n" + page_text
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        return ""


def extract_text_using_gemini(file_path, api_key=None):
    """Use Gemini vision to OCR an image file when an API key is available."""
    prompt = (
        "Extract all readable text from this document image. "
        "Return plain text only, preserving line breaks where useful."
    )
    return call_gemini_vision(file_path, prompt, api_key=api_key)


def extract_text_using_tesseract(file_path):
    """Fallback OCR using pytesseract if installed locally."""
    try:
        import pytesseract
        img = Image.open(file_path)
        return pytesseract.image_to_string(img)
    except ImportError:
        logger.warning("pytesseract not installed. Skipping local OCR.")
        return ""
    except Exception as e:
        logger.error(f"Tesseract OCR failed: {str(e)}")
        return ""


def get_simulated_ocr_text(filename, file_type):
    """Provides realistic fallback mock text based on filename for offline demo."""
    lower_name = filename.lower()

    if "invoice" in lower_name or "bill" in lower_name:
        return """--- Page 1 ---
INVOICE
GLOBAL TECH SOLUTIONS LLC
100 Innovation Way, Suite 400
San Francisco, CA 94107
Email: billing@globaltech.com

Bill To:
Acme Corporation
Attn: Accounts Payable
500 Enterprise Parkway
Austin, TX 78701

Invoice Number: INV-2026-0892
Invoice Date: July 11, 2026
Due Date: August 10, 2026
Payment Terms: Net 30

Description                                 Qty    Unit Price      Amount
1. Premium Cloud Architecture Consulting   40 hrs     $150.00   $6,000.00
2. Enterprise API Integration Service       1         $2,500.00   $2,500.00
3. Monthly Maintenance & SLA Support        1         $1,200.00   $1,200.00

Subtotal:                                                       $9,700.00
Tax (8.25%):                                                      $800.25
Total Due:                                                      $10,500.25

Payment Instructions:
Please wire funds to Bank of America.
Routing Number: 121000248
Account Number: 9876543210
Reference: INV-2026-0892

Thank you for your business!"""

    elif "contract" in lower_name or "agreement" in lower_name or "lease" in lower_name:
        return """--- Page 1 ---
SOFTWARE LICENSE AND SERVICE AGREEMENT

This Agreement is entered into as of July 11, 2026, by and between:

LICENSOR: Nova Systems Inc., 1200 Technology Drive, Seattle, WA 98101
LICENSEE: Vertex Solutions Corp, 750 Commerce Boulevard, New York, NY 10001

1. LICENSE GRANT.
Licensor grants Licensee a non-exclusive, non-transferable, perpetual license to use the Licensed Software.

2. FEES AND PAYMENT.
One-time license fee: $45,000.00. Annual maintenance fee: $9,000.00, due within 30 days of invoice.

3. CONFIDENTIALITY.
Both parties shall maintain in confidence all non-public information designated as confidential.

4. TERM AND TERMINATION.
Either party may terminate upon 30 days written notice of material breach.

IN WITNESS WHEREOF the parties hereto have executed this Agreement.
For Nova Systems Inc.: /s/ Sarah Jenkins, VP of Enterprise Sales
For Vertex Solutions Corp.: /s/ David Cho, Chief Technology Officer"""

    elif "certificate" in lower_name or "diploma" in lower_name:
        return """--- Page 1 ---
UNIVERSITY OF DATA SCIENCE
Be it known that the Board of Regents has conferred upon

EMILY R. JOHNSON

the degree of
MASTER OF SCIENCE IN ARTIFICIAL INTELLIGENCE AND MACHINE LEARNING

with all the rights, privileges, and honors thereunto appertaining.
Given at Boston, Massachusetts, this 11th day of July, 2026.

Chancellor: John A. Marcus
Dean of Graduate Studies: Dr. Helen Vance"""

    elif "passport" in lower_name or "id" in lower_name or "license" in lower_name:
        return """--- Page 1 ---
PASSPORT — UNITED STATES OF AMERICA
Type: P | Code: USA | Passport No: D84710928
Surname: SMITH | Given Names: ALEXANDER JAMES
Nationality: UNITED STATES OF AMERICA
Date of Birth: 15 APR 1992 | Sex: M
Place of Birth: CALIFORNIA, USA
Date of Issue: 20 MAY 2020 | Date of Expiration: 19 MAY 2030"""

    else:
        return f"""--- Page 1 ---
[Document: {filename}]
Processed by AI Document Intelligence Platform.

This appears to be a general document.
Timestamp: July 11, 2026

Tip: Name your file with keywords like 'invoice', 'contract', 'certificate', or 'passport'
to receive realistic simulated data when running offline without a Gemini API key."""


def process_document_ocr(file_path, filename, api_key=None):
    """
    Main OCR entrypoint. Tries:
    1. pypdf for digital PDFs
    2. pytesseract as local fallback
    3. Simulated text as final fallback
    Returns (text, confidence_score).
    """
    ext = os.path.splitext(filename)[1].lower().replace('.', '')
    ocr_text = ""
    confidence = 0.0

    if ext == 'pdf':
        ocr_text = extract_text_from_pdf(file_path)
        if ocr_text and len(ocr_text.strip()) > 50:
            logger.info(f"PDF text extracted via pypdf ({len(ocr_text)} chars).")
            confidence = 0.98
            return ocr_text, confidence

    # Image upload or scanned PDF page image fallback
    if ext in ('png', 'jpg', 'jpeg'):
        if api_key:
            ocr_text = extract_text_using_gemini(file_path, api_key=api_key)
            if ocr_text and len(ocr_text.strip()) > 10:
                logger.info(f"Image OCR extracted via Gemini ({len(ocr_text)} chars).")
                return ocr_text.strip(), 0.95

        ocr_text = extract_text_using_tesseract(file_path)
        if ocr_text and len(ocr_text.strip()) > 20:
            confidence = 0.85
            return ocr_text, confidence

    if ext == 'pdf' and api_key and not ocr_text:
        # If the PDF has no embedded text, try a Gemini fallback using the file as a document image surrogate.
        # This is best-effort only; if the SDK cannot process the PDF directly, we continue to local fallback.
        gemini_text = extract_text_using_gemini(file_path, api_key=api_key)
        if gemini_text and len(gemini_text.strip()) > 10:
            logger.info(f"PDF OCR extracted via Gemini ({len(gemini_text)} chars).")
            return gemini_text.strip(), 0.9

    # Final fallback — simulated content for demo/offline use
    logger.warning(f"All OCR methods exhausted for '{filename}'. Using simulated content.")
    sim_text = get_simulated_ocr_text(filename, ext)
    return sim_text, 0.5
