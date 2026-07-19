from flask import Blueprint, render_template, send_from_directory, current_app, abort
from app.models.document import Document, ChatMessage, DocumentComparison, DocumentTranslation
from app.services.translation_service import LANGUAGE_OPTIONS, RTL_LANGUAGE_CODES, get_language_label
import os

main_bp = Blueprint('main', __name__)


def _document_stats(all_docs):
    """Return shared, plain-language document counts for workspace pages."""
    all_docs = Document.query.order_by(Document.created_at.desc()).all()
    total_docs = len(all_docs)
    completed_docs = sum(1 for d in all_docs if d.status == 'Completed')
    processing_docs = sum(1 for d in all_docs if d.status == 'Processing')
    failed_docs = sum(1 for d in all_docs if d.status == 'Failed')
    total_bytes = sum(d.storage_size for d in all_docs if d.storage_size)
    storage_mb = round(total_bytes / (1024 * 1024), 2)
    return {
        'documents': all_docs,
        'total_docs': total_docs,
        'completed_docs': completed_docs,
        'processing_docs': processing_docs,
        'failed_docs': failed_docs,
        'storage_mb': storage_mb,
    }


@main_bp.route('/')
def index():
    """Render the quality dashboard as the product home page."""
    stats = _document_stats(Document.query.order_by(Document.created_at.desc()).all())
    translations = DocumentTranslation.query.order_by(
        DocumentTranslation.created_at.desc()
    ).all()
    document_names = {
        document.id: document.original_filename
        for document in stats['documents']
    }
    completed = [job for job in translations if job.status == 'Completed']
    scored = [job.quality_score for job in completed if job.quality_score is not None]
    ready = sum(
        1 for job in completed
        if job.parsed_quality_report.get('publication_ready', True)
    )
    recovered = sum(
        int(job.parsed_quality_report.get('recovered_review_items') or 0)
        for job in completed
    )
    recent_jobs = []
    for job in translations[:8]:
        report = job.parsed_quality_report
        recent_jobs.append({
            'id': job.id,
            'document_name': document_names.get(job.document_id, 'Deleted document'),
            'status': job.status or 'Pending',
            'message': job.status_message or 'Waiting to begin.',
            'quality_score': job.quality_score,
            'publication_ready': report.get('publication_ready'),
            'provider_mode': job.provider_mode or 'online',
            'created_at': job.created_at,
            'current_page': job.current_page or 0,
            'total_pages': job.total_pages or 0,
        })

    return render_template(
        'quality_dashboard.html',
        **stats,
        translation_total=len(translations),
        translation_active=sum(
            1 for job in translations if job.status in {'Pending', 'Processing', 'NeedsReview'}
        ),
        translation_ready=ready,
        average_quality=round(sum(scored) / len(scored), 1) if scored else None,
        recovered_items=recovered,
        recent_jobs=recent_jobs,
    )


@main_bp.route('/dashboard')
def dashboard():
    """Keep the legacy dashboard URL pointing to the quality home page."""
    return index()


@main_bp.route('/analyze')
def analyze_page():
    """Render the document upload and analysis workspace."""
    return render_template('dashboard.html', **_document_stats(
        Document.query.order_by(Document.created_at.desc()).all()
    ))


@main_bp.route('/translate')
def translate_page():
    """Render the document translation workspace."""
    stats = _document_stats(Document.query.order_by(Document.created_at.desc()).all())
    all_docs = stats['documents']

    translations = DocumentTranslation.query.order_by(DocumentTranslation.created_at.desc()).all()
    translation_rows = []
    for job in translations:
        doc = Document.query.get(job.document_id)
        translation_rows.append({
            'id': job.id,
            'document_name': doc.original_filename if doc else 'Deleted Document',
            'source_language': get_language_label(job.source_language),
            'target_language': get_language_label(job.target_language),
            'status': job.status,
            'created_at': job.created_at,
            'completed_at': job.completed_at,
            'download_available': bool(job.translated_pdf_path and job.status == 'Completed'),
        })

    return render_template(
        'translate.html',
        **stats,
        translations=translation_rows,
        language_options=LANGUAGE_OPTIONS,
        rtl_codes=','.join(sorted(RTL_LANGUAGE_CODES)),
    )


@main_bp.route('/document/<int:doc_id>')
def document_view(doc_id):
    """View a single processed document with chat interface."""
    doc = Document.query.get_or_404(doc_id)
    chat_history = ChatMessage.query.filter_by(
        document_id=doc.id
    ).order_by(ChatMessage.created_at.asc()).all()
    return render_template('document.html', document=doc, chat_history=chat_history)


@main_bp.route('/uploads/<filename>')
def serve_uploaded_file(filename):
    """Serve an uploaded file directly (local setup — no auth)."""
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@main_bp.route('/compare')
def compare_page():
    """Render the AI document comparison dashboard."""
    completed_docs = Document.query.filter_by(status='Completed').order_by(Document.created_at.desc()).all()
    # Fetch all comparisons
    comparisons = DocumentComparison.query.order_by(DocumentComparison.created_at.desc()).all()
    
    comparisons_data = []
    for c in comparisons:
        doc1 = Document.query.get(c.document_one_id)
        doc2 = Document.query.get(c.document_two_id)
        comparisons_data.append({
            'id': c.id,
            'document_one_name': doc1.original_filename if doc1 else 'Deleted Document',
            'document_two_name': doc2.original_filename if doc2 else 'Deleted Document',
            'created_at': c.created_at
        })

    return render_template(
        'compare.html',
        documents=completed_docs,
        comparisons=comparisons_data
    )

