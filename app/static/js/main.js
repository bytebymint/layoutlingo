/* main.js — theme toggle, toast system, delete modal */

(function () {
    'use strict';

    // All browser writes carry the per-session token issued by Flask.
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = function (input, options = {}) {
        const method = String(options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        if (!csrfToken || ['GET', 'HEAD', 'OPTIONS', 'TRACE'].includes(method)) {
            return nativeFetch(input, options);
        }
        const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
        headers.set('X-CSRF-Token', csrfToken);
        return nativeFetch(input, { ...options, headers });
    };

    // ── Theme toggle ──────────────────────────────────────────
    const html       = document.documentElement;
    const themeBtn   = document.getElementById('theme-toggle');
    const themeIcon  = document.getElementById('theme-icon');
    const saved      = localStorage.getItem('theme') || 'dark';
    let currentTheme = saved;

    function applyTheme(t) {
        html.setAttribute('data-theme', t);
        if (themeIcon) {
            themeIcon.className = t === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
        }
        currentTheme = t;
        localStorage.setItem('theme', t);
    }

    applyTheme(currentTheme);

    if (themeBtn) {
        themeBtn.addEventListener('click', function () {
            applyTheme(currentTheme === 'dark' ? 'light' : 'dark');
        });
    }

    // ── Toast system ──────────────────────────────────────────
    window.showToast = function (message, type = 'info', duration = 4000) {
        const container = document.getElementById('toast-container');
        if (!container) return;

        const icons = { ok: 'bi-check-circle-fill', error: 'bi-x-circle-fill', info: 'bi-info-circle-fill' };
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.innerHTML = `
            <i class="bi ${icons[type] || icons.info} toast-icon"></i>
            <span class="toast-msg">${message}</span>
        `;
        container.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(20px)';
            toast.style.transition = 'opacity .3s, transform .3s';
            setTimeout(() => toast.remove(), 350);
        }, duration);
    };

    // ── Delete modal ──────────────────────────────────────────
    let pendingDeleteId = null;

    window.confirmDelete = function (docId, filename) {
        pendingDeleteId = docId;
        const modal     = document.getElementById('delete-modal');
        const nameEl    = document.getElementById('delete-modal-name');
        if (!modal) return;
        if (nameEl) nameEl.textContent = filename;
        modal.style.display = 'flex';
    };

    window.closeDeleteModal = function () {
        pendingDeleteId = null;
        const modal = document.getElementById('delete-modal');
        if (modal) modal.style.display = 'none';
    };

    const confirmBtn = document.getElementById('confirm-delete-btn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', async function () {
            if (!pendingDeleteId) return;
            const docId = pendingDeleteId;
            closeDeleteModal();

            try {
                const res = await fetch(`/api/document/${docId}`, { method: 'DELETE' });
                if (res.ok) {
                    const row = document.getElementById(`row-${docId}`);
                    if (row) {
                        row.style.transition = 'opacity .3s, transform .3s';
                        row.style.opacity = '0';
                        row.style.transform = 'translateX(-20px)';
                        setTimeout(() => {
                            row.remove();
                            updateDocCount();
                        }, 350);
                    }
                    showToast('Document deleted successfully.', 'ok');
                } else {
                    const data = await res.json();
                    showToast(data.error || 'Failed to delete.', 'error');
                }
            } catch (e) {
                showToast('Network error during deletion.', 'error');
            }
        });
    }

    // Close modal on backdrop click
    const deleteModal = document.getElementById('delete-modal');
    if (deleteModal) {
        deleteModal.addEventListener('click', function (e) {
            if (e.target === deleteModal) closeDeleteModal();
        });
    }

    // ── Live polling for in-progress documents ────────────────
    function updateDocCount() {
        const rows = document.querySelectorAll('#doc-tbody tr.doc-row');
        const countEl = document.getElementById('doc-count');
        if (countEl) countEl.textContent = rows.length;
    }

    function updateSidebarStats(docs) {
        const total   = docs.length;
        const done    = docs.filter(d => d.status === 'Completed').length;
        const active  = docs.filter(d => d.status === 'Processing' || d.status === 'Pending').length;
        const failed  = docs.filter(d => d.status === 'Failed').length;

        const se = id => document.getElementById(id);
        if (se('stat-total')) se('stat-total').textContent = total;
        if (se('stat-done'))  se('stat-done').textContent  = done;
        if (se('stat-proc'))  se('stat-proc').textContent  = active;
        if (se('stat-fail'))  se('stat-fail').textContent  = failed;
    }

    function pollStatus() {
        const processingRows = document.querySelectorAll('.doc-row.is-processing');
        if (processingRows.length === 0) return;

        processingRows.forEach(async (row) => {
            const id = row.id.replace('row-', '');
            try {
                const res  = await fetch(`/api/document/${id}/status`);
                if (!res.ok) return;
                const data = await res.json();

                const statusEl = document.getElementById(`status-${id}`);
                const catEl    = document.getElementById(`cat-${id}`);

                if (statusEl) {
                    const s     = data.status;
                    const icons = {
                        Completed: '<i class="bi bi-check-circle-fill"></i>',
                        Failed:    '<i class="bi bi-x-circle-fill"></i>',
                    };
                    const spinner = (s === 'Processing' || s === 'Pending')
                        ? '<span class="chip-spinner"></span>'
                        : (icons[s] || '');

                    statusEl.className = `status-chip status-${s.toLowerCase()}`;
                    statusEl.innerHTML = `${spinner} ${s}`;
                }

                if (catEl && data.doc_type) catEl.textContent = data.doc_type;

                if (data.status === 'Completed' || data.status === 'Failed') {
                    row.classList.remove('is-processing');
                    if (data.status === 'Completed') {
                        // Enable the view button
                        const actGroup = row.querySelector('.action-group');
                        if (actGroup) {
                            const viewBtn = actGroup.querySelector('.act-view');
                            if (viewBtn && viewBtn.disabled) {
                                viewBtn.outerHTML = `
                                    <a href="/document/${id}" class="act-btn act-view" title="View & Chat">
                                        <i class="bi bi-arrow-right-circle-fill"></i>
                                    </a>`;
                            }
                        }
                        showToast(`Processing complete: ${data.original_filename}`, 'ok');
                    } else {
                        showToast(`Processing failed: ${data.original_filename}`, 'error');
                    }
                }
            } catch (e) {
                // silently ignore network errors in polling
            }
        });
    }

    // Start polling every 3 seconds if there are processing docs
    setInterval(pollStatus, 3000);

})();
