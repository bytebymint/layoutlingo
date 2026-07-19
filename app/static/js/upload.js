/* upload.js — drag & drop upload with live progress bar */
'use strict';

document.addEventListener('DOMContentLoaded', () => {
    initUpload();
});

function initUpload() {
    const uploadZone = document.getElementById('upload-zone');
    const fileInput  = document.getElementById('file-input');
    const browseBtn  = document.getElementById('browse-btn');
    if (!uploadZone || !fileInput) return;

    // Browse button inside zone
    if (browseBtn) {
        browseBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
    }

    // Click on zone triggers file picker
    uploadZone.addEventListener('click', () => fileInput.click());

    // Drag events
    ['dragenter', 'dragover'].forEach(evt => {
        uploadZone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation();
            uploadZone.classList.add('drag-over');
        });
    });
    ['dragleave', 'drop'].forEach(evt => {
        uploadZone.addEventListener(evt, (e) => {
            e.preventDefault(); e.stopPropagation();
            uploadZone.classList.remove('drag-over');
        });
    });

    uploadZone.addEventListener('drop', (e) => {
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFile(files[0]);
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            uploadFile(fileInput.files[0]);
            fileInput.value = '';
        }
    });
}

function uploadFile(file) {
    const maxSize = 500 * 1024 * 1024;
    if (file.size > maxSize) {
        showToast('File exceeds the 500 MB limit.', 'error');
        return;
    }

    const allowed = ['pdf', 'png', 'jpg', 'jpeg'];
    const ext     = file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
        showToast('Unsupported file type. Use PDF, PNG, or JPG.', 'error');
        return;
    }

    // Show progress bar
    const progressEl   = document.getElementById('upload-progress');
    const progressFill = document.getElementById('progress-fill');
    const progressName = document.getElementById('progress-filename');
    const progressPct  = document.getElementById('progress-pct');

    if (progressEl) {
        progressEl.style.display = 'block';
        if (progressName) progressName.textContent = file.name;
        if (progressFill) progressFill.style.width = '0%';
        if (progressPct)  progressPct.textContent  = '0%';
    }

    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload', true);

    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            if (progressFill) progressFill.style.width = pct + '%';
            if (progressPct)  progressPct.textContent  = pct + '%';
        }
    });

    xhr.onload = function () {
        if (xhr.status === 201) {
            const data = JSON.parse(xhr.responseText);
            showToast('Uploaded! AI analysis started…', 'ok');
            if (progressEl) setTimeout(() => { progressEl.style.display = 'none'; }, 1500);
            addDocRow(data.document);
        } else {
            let msg = 'Upload failed.';
            try { msg = JSON.parse(xhr.responseText).error || msg; } catch (_) {}
            showToast(msg, 'error');
            if (progressEl) progressEl.style.display = 'none';
        }
    };

    xhr.onerror = function () {
        showToast('Network error during upload.', 'error');
        if (progressEl) progressEl.style.display = 'none';
    };

    xhr.send(formData);
}

function fmtSize(bytes) {
    if (!bytes) return '—';
    if (bytes > 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1024).toFixed(1) + ' KB';
}

function addDocRow(doc) {
    const tbody    = document.getElementById('doc-tbody');
    const emptyRow = document.getElementById('empty-row');
    if (!tbody) return;
    if (emptyRow) emptyRow.remove();

    const dateStr = new Date(doc.created_at).toLocaleDateString(undefined, {
        day: 'numeric', month: 'short', year: 'numeric'
    });
    const fileIconCls = doc.file_type === 'pdf'
        ? 'file-icon pdf"><i class="bi bi-file-earmark-pdf-fill'
        : 'file-icon img"><i class="bi bi-file-earmark-image-fill';

    const tr = document.createElement('tr');
    tr.id        = `row-${doc.id}`;
    tr.className = 'doc-row is-processing';
    tr.style.animation = 'slideInRow .35s ease';
    tr.innerHTML = `
        <td class="td-name">
            <div class="file-cell">
                <span class="${fileIconCls}"></i></span>
                <span class="file-name" title="${doc.original_filename}">${doc.original_filename}</span>
            </div>
        </td>
        <td><span class="mono upper small-muted">${doc.file_type}</span></td>
        <td><span class="mono small-muted">${fmtSize(doc.storage_size)}</span></td>
        <td><span class="small-muted">${dateStr}</span></td>
        <td><span class="cat-badge" id="cat-${doc.id}">—</span></td>
        <td>
            <span class="status-chip status-pending" id="status-${doc.id}">
                <span class="chip-spinner"></span> Pending
            </span>
        </td>
        <td class="td-actions">
            <div class="action-group">
                <button class="act-btn act-view" disabled title="Processing…">
                    <i class="bi bi-hourglass-split"></i>
                </button>
                <button class="act-btn act-delete" onclick="confirmDelete(${doc.id}, '${doc.original_filename.replace(/'/g, "\\'")}')" title="Delete">
                    <i class="bi bi-trash3-fill"></i>
                </button>
            </div>
        </td>
    `;

    tbody.insertBefore(tr, tbody.firstChild);

    // Update sidebar stats
    const statTotal = document.getElementById('stat-total');
    if (statTotal) statTotal.textContent = parseInt(statTotal.textContent || '0') + 1;
    const docCount = document.getElementById('doc-count');
    if (docCount) docCount.textContent = parseInt(docCount.textContent || '0') + 1;
}

// Keyframe for row entrance (injected once)
(function injectAnimation() {
    if (document.getElementById('upload-anim')) return;
    const style = document.createElement('style');
    style.id = 'upload-anim';
    style.textContent = `
        @keyframes slideInRow {
            from { opacity:0; transform: translateY(-10px); }
            to   { opacity:1; transform: translateY(0); }
        }
    `;
    document.head.appendChild(style);
})();
