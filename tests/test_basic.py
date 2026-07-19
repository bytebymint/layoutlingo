import os
import unittest
from io import BytesIO
from unittest.mock import patch
from app import create_app, db
from app.models.document import Document
from app.services.document_classifier import classify_document
from app.services.information_extractor import extract_document_info

class BasicTestCase(unittest.TestCase):
    def setUp(self):
        test_config = type('BasicTestConfig', (), {
            'TESTING': True,
            'SECRET_KEY': 'basic-test-secret',
            'WTF_CSRF_ENABLED': False,
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'SQLALCHEMY_TRACK_MODIFICATIONS': False,
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'UPLOAD_FOLDER': os.path.join(os.path.dirname(__file__), 'tmp-uploads'),
            'MAX_CONTENT_LENGTH': 10 * 1024 * 1024,
            'ALLOWED_EXTENSIONS': {'pdf'},
            'GEMINI_API_KEY': '',
            'FREEMODEL_API_KEY': 'test-provider-key',
            'TRANSLATION_WORKER_MODE': 'external',
        })
        os.makedirs(test_config.UPLOAD_FOLDER, exist_ok=True)
        self.app = create_app(test_config)
        
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Recreate tables in-memory
        db.drop_all()
        db.create_all()
        
        self.client = self.app.test_client()
        
    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()
        
    def test_landing_page(self):
        """The root route should render the friendly quality dashboard."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Quality you can ', response.data)
        self.assertIn(b'Your translation team', response.data)
        self.assertIn(b'Enable local AI', response.data)
        self.assertIn(b'What the translation team is doing', response.data)
        
    def test_dashboard_alias_is_available_without_auth(self):
        """The legacy dashboard URL should continue to open the quality dashboard."""
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Quality you can ', response.data)

    def test_analyze_workspace_is_available_without_auth(self):
        """The document upload and analysis workspace lives at /analyze."""
        response = self.client.get('/analyze')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Analyze <span class="gradient-text">documents</span>', response.data)
        self.assertIn(b'Drop your document here', response.data)

    def test_translate_workspace_contains_failed_qa_review_desk(self):
        response = self.client.get('/translate')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Your decision is needed', response.data)
        self.assertIn(b'Approve corrections and continue', response.data)
        
    def test_upload_creates_document_without_auth(self):
        """Uploading a document should create a document record without requiring login."""
        test_file = (BytesIO(b'%PDF-1.4\n%test pdf content'), 'test.pdf')
        with patch('app.routes.api.run_async') as mock_run_async:
            response = self.client.post('/api/upload', data={'file': test_file}, content_type='multipart/form-data')

        self.assertEqual(response.status_code, 201)
        mock_run_async.assert_called_once()

        document = Document.query.order_by(Document.id.desc()).first()
        self.assertIsNotNone(document)
        self.assertEqual(document.user_id, 1)
        self.assertEqual(document.original_filename, 'test.pdf')

    def test_upload_rejects_spoofed_pdf_extension(self):
        test_file = (BytesIO(b'not a real PDF'), 'spoofed.pdf')
        response = self.client.post(
            '/api/upload',
            data={'file': test_file},
            content_type='multipart/form-data',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b'does not match its extension', response.data)

    def test_classification_and_extraction_heuristics(self):
        """Test that local heuristic services correctly classify and extract document data."""
        sample_invoice = """
        INVOICE
        Global Tech Solutions LLC
        100 Innovation Way, Suite 400
        Invoice Number: INV-2026-0892
        Invoice Date: July 11, 2026
        Total Due: $10,500.25
        """
        
        # Classification test
        doc_type = classify_document(sample_invoice)
        self.assertEqual(doc_type, "Invoice")
        
        # Extraction test
        data = extract_document_info(sample_invoice, doc_type)
        self.assertEqual(data["type"], "Invoice")
        self.assertEqual(data["company"], "Global Tech Solutions LLC")
        self.assertEqual(data["total"], "10,500.25")
        self.assertEqual(data["details"]["invoice_number"], "INV-2026-0892")

        
if __name__ == '__main__':
    unittest.main()
