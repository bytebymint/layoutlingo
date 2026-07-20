import os
import unittest

from app import create_app, db
from app.models.document import Document
from app.models.user import User


class AccessControlTests(unittest.TestCase):
    def setUp(self):
        config = type('AccessControlConfig', (), {
            'TESTING': False,
            'SECRET_KEY': 'access-control-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'SQLALCHEMY_TRACK_MODIFICATIONS': False,
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'UPLOAD_FOLDER': os.path.join(os.path.dirname(__file__), 'tmp-uploads'),
            'MAX_CONTENT_LENGTH': 1024 * 1024,
            'ALLOWED_EXTENSIONS': {'pdf'},
            'TRANSLATION_WORKER_MODE': 'external',
        })
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.owner = User(username='owner', email='owner@example.test')
        self.owner.set_password('not-used')
        self.other = User(username='other', email='other@example.test')
        self.other.set_password('not-used')
        db.session.add_all([self.owner, self.other])
        db.session.flush()
        self.document = Document(
            user_id=self.owner.id,
            filename='private.pdf',
            original_filename='private.pdf',
            file_path='private.pdf',
            file_type='pdf',
            status='Completed',
        )
        db.session.add(self.document)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_workspace_and_api_require_a_sign_in(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(
            self.client.get(f'/api/document/{self.document.id}/status').status_code,
            401,
        )

    def test_other_user_cannot_read_private_document(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.other.id)
            session['_fresh'] = True
        self.assertEqual(
            self.client.get(f'/api/document/{self.document.id}/status').status_code,
            404,
        )


if __name__ == '__main__':
    unittest.main()
