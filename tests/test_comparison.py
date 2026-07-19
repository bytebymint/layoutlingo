import unittest
import json
from io import BytesIO
from unittest.mock import patch
from app import create_app, db
from app.models.document import Document, DocumentComparison
from app.services.document_comparison_service import compare_documents
from config import Config


class ComparisonTestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    SQLALCHEMY_ENGINE_OPTIONS = {}
    TRANSLATION_WORKER_MODE = 'external'

class DocumentComparisonTestCase(unittest.TestCase):
    def setUp(self):
        # SQLAlchemy binds its engine while create_app runs. Passing the test
        # config up front prevents drop_all() from ever touching the real DB.
        self.app = create_app(ComparisonTestConfig)
        
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Recreate tables in-memory
        db.drop_all()
        db.create_all()
        
        self.client = self.app.test_client()

        # Seed test documents
        self.doc1 = Document(
            user_id=1,
            filename="doc1.pdf",
            original_filename="doc1.pdf",
            file_path="uploads/doc1.pdf",
            file_type="pdf",
            status="Completed",
            ocr_text="Payment due within 30 days. Total contract value is $10,000.",
            doc_type="Contract"
        )
        self.doc1.parsed_extracted_data = {
            "type": "Contract",
            "company": "Company A",
            "date": "2026-07-01",
            "total": "$10,000",
            "details": {"payment_terms": "30 days"}
        }

        self.doc2 = Document(
            user_id=1,
            filename="doc2.pdf",
            original_filename="doc2.pdf",
            file_path="uploads/doc2.pdf",
            file_type="pdf",
            status="Completed",
            ocr_text="Payment due within 90 days. Total contract value is $15,000.",
            doc_type="Contract"
        )
        self.doc2.parsed_extracted_data = {
            "type": "Contract",
            "company": "Company A",
            "date": "2026-07-01",
            "total": "$15,000",
            "details": {"payment_terms": "90 days"}
        }

        db.session.add(self.doc1)
        db.session.add(self.doc2)
        db.session.commit()
        
    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_compare_page_loads(self):
        """Test that the /compare page loads successfully."""
        response = self.client.get('/compare')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Compare Docs', response.data)
        self.assertIn(b'AI Document', response.data)

    @patch('app.services.document_comparison_service.call_freemodel_chat')
    def test_comparison_api(self, mock_call_chat):
        """Test the semantic comparison route and database saving."""
        mock_response = {
            "overview": {
                "document_1_type": "Contract",
                "document_2_type": "Contract",
                "summary": "Contract value increased and payment term extended."
            },
            "changed_information": [
                {
                    "category": "Contract Value",
                    "before": "$10,000",
                    "after": "$15,000",
                    "importance": "High"
                },
                {
                    "category": "Payment Terms",
                    "before": "30 days",
                    "after": "90 days",
                    "importance": "Medium"
                }
            ],
            "added_content": [],
            "removed_content": [],
            "risk_analysis": ["Increased cost"],
            "final_recommendation": "Review increased obligations."
        }
        mock_call_chat.return_value = json.dumps(mock_response)

        payload = {
            "document_id_1": self.doc1.id,
            "document_id_2": self.doc2.id
        }

        response = self.client.post('/api/documents/compare', json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["comparison"]["overview"]["summary"], "Contract value increased and payment term extended.")
        
        # Verify saved in database
        comp = DocumentComparison.query.filter_by(document_one_id=self.doc1.id, document_two_id=self.doc2.id).first()
        self.assertIsNotNone(comp)
        self.assertEqual(comp.parsed_result["overview"]["similarity_score"], 100) # Computed TF-cosine similarity is 100% since mock texts differ only in numbers/symbols ignored by the tokenizer

    def test_comparisons_history_list(self):
        """Test listing comparisons returns the seeded history."""
        comp = DocumentComparison(
            document_one_id=self.doc1.id,
            document_two_id=self.doc2.id,
            result_json=json.dumps({"overview": {"summary": "Direct Test"}})
        )
        db.session.add(comp)
        db.session.commit()

        response = self.client.get('/api/comparisons')
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertEqual(len(data["comparisons"]), 1)
        self.assertEqual(data["comparisons"][0]["document_one_name"], "doc1.pdf")

    def test_delete_comparison(self):
        """Test deleting a comparison removes it from database."""
        comp = DocumentComparison(
            document_one_id=self.doc1.id,
            document_two_id=self.doc2.id,
            result_json=json.dumps({"overview": {"summary": "Direct Test"}})
        )
        db.session.add(comp)
        db.session.commit()

        response = self.client.delete(f'/api/comparison/{comp.id}')
        self.assertEqual(response.status_code, 200)
        
        comp_check = DocumentComparison.query.get(comp.id)
        self.assertIsNone(comp_check)

if __name__ == '__main__':
    unittest.main()
