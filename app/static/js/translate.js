document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('translation-form');
  if (!form) return;

  const workspace = document.getElementById('translate-workspace');
  const rtlCodes = new Set((form.getAttribute('data-rtl-codes') || '').split(',').map(v => v.trim()).filter(Boolean));
  const docSelect = document.getElementById('document_id');
  const sourceSelect = document.getElementById('source_language');
  const targetSelect = document.getElementById('target_language');
  const providerSelect = document.getElementById('translation_provider');
  const localEngineStatus = document.getElementById('local-engine-status');
  const modeSelect = document.getElementById('translation_mode');
  const domainSelect = document.getElementById('translation_domain');
  const qualityLevelSelect = document.getElementById('quality_level');
  const glossaryInput = document.getElementById('translation_glossary');
  const backTranslationInput = document.getElementById('enable_back_translation');
  const submitBtn = document.getElementById('translate-btn');
  const resetBtn = document.getElementById('translate-reset-btn');
  
  // Results UI
  const resultBox = document.getElementById('translation-result');
  const statusPill = document.getElementById('translation-status-pill');
  const titleEl = document.getElementById('translation-title');
  const metaEl = document.getElementById('translation-meta');
  const messageEl = document.getElementById('translation-message');
  const downloadLink = document.getElementById('translation-download');
  const cancelBtn = document.getElementById('translation-cancel');
  const qualityDossier = document.getElementById('translation-quality-dossier');
  const qualitySummary = document.getElementById('translation-quality-summary');
  const qualityStages = document.getElementById('translation-quality-stages');
  const qualityIssues = document.getElementById('translation-quality-issues');
  const reviewPanel = document.getElementById('translation-review-panel');
  const reviewItems = document.getElementById('translation-review-items');
  const reviewSubmit = document.getElementById('translation-review-submit');
  
  // Progress Bar UI
  const progressContainer = document.getElementById('translation-progress-container');
  const progressLabel = document.getElementById('translation-progress-label');
  const progressPercent = document.getElementById('translation-progress-percent');
  const progressFill = document.getElementById('translation-progress-fill');
  const progressTrack = document.getElementById('translation-progress-track');
  const progressStage = document.getElementById('translation-progress-stage');
  const progressElapsed = document.getElementById('translation-progress-elapsed');
  const progressActivity = document.getElementById('translation-progress-activity');
  
  // History table
  const tbody = document.getElementById('translation-tbody');
  const countEl = document.getElementById('translation-count');

  let pollInterval = null;
  let activeTranslationId = null;
  let progressAnimationFrame = null;
  let progressDisplayed = 0;
  let progressTarget = 0;
  let progressIsActive = false;
  let elapsedTimer = null;
  let elapsedBaseSeconds = 0;
  let elapsedSyncedAt = Date.now();
  let progressLastFrame = 0;
  let activeReviewGroups = [];

  function formatElapsed(totalSeconds) {
    const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remainder = seconds % 60;
    return hours > 0
      ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
      : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
  }

  function refreshElapsed() {
    if (!progressElapsed) return;
    const liveSeconds = progressIsActive
      ? Math.floor((Date.now() - elapsedSyncedAt) / 1000)
      : 0;
    progressElapsed.textContent = formatElapsed(elapsedBaseSeconds + liveSeconds);
  }

  function animateProgress(timestamp) {
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const delta = progressTarget - progressDisplayed;
    if (reducedMotion || Math.abs(delta) < 0.025) {
      progressDisplayed = progressTarget;
    } else {
      const elapsed = progressLastFrame ? Math.min(64, timestamp - progressLastFrame) : 16;
      const easing = 1 - Math.exp(-elapsed / 190);
      progressDisplayed += delta * easing;
    }
    progressLastFrame = timestamp;
    const visible = Math.max(0, Math.min(100, progressDisplayed));
    if (progressFill) progressFill.style.transform = `scaleX(${visible / 100})`;
    if (progressPercent) progressPercent.textContent = `${Math.round(visible)}%`;
    if (progressTrack) progressTrack.setAttribute('aria-valuenow', String(Math.round(visible)));

    if (Math.abs(progressTarget - progressDisplayed) >= 0.025) {
      progressAnimationFrame = window.requestAnimationFrame(animateProgress);
    } else {
      progressAnimationFrame = null;
      progressLastFrame = 0;
    }
  }

  function startProgressAnimation() {
    if (!progressAnimationFrame) {
      progressAnimationFrame = window.requestAnimationFrame(animateProgress);
    }
    if (!elapsedTimer) elapsedTimer = window.setInterval(refreshElapsed, 1000);
  }

  function updateProgress(data, percentage) {
    const nextTarget = Math.max(0, Math.min(100, Number(percentage) || 0));
    progressTarget = Math.max(progressTarget, nextTarget);
    elapsedBaseSeconds = Math.max(0, Number(data.elapsed_seconds) || elapsedBaseSeconds);
    elapsedSyncedAt = Date.now();
    progressIsActive = data.status === 'Pending' || data.status === 'Processing';
    if (progressStage) {
      progressStage.textContent = data.progress_stage?.label
        || (data.status === 'Pending' ? 'Queued for translation' : 'Translation in progress');
    }
    if (progressLabel) {
      progressLabel.textContent = data.status_message || 'The translation worker is active.';
    }
    if (progressActivity) {
      const heartbeatAge = Number(data.heartbeat_age_seconds);
      if (data.status === 'Completed') {
        progressActivity.textContent = 'Finished';
      } else if (data.status === 'NeedsReview') {
        progressActivity.textContent = 'Waiting for your decision';
      } else if (data.status === 'Failed') {
        progressActivity.textContent = 'Checkpoint preserved';
      } else if (Number.isFinite(heartbeatAge) && heartbeatAge > 90) {
        progressActivity.textContent = 'Waiting for engine response';
      } else {
        progressActivity.textContent = data.provider_mode === 'offline'
          ? 'Local engine active'
          : 'Translation worker active';
      }
    }
    if (progressContainer) {
      progressContainer.classList.toggle('is-complete', data.status === 'Completed');
      progressContainer.classList.toggle('is-stopped', ['Failed', 'Cancelled'].includes(data.status));
      progressContainer.classList.toggle('is-review', data.status === 'NeedsReview');
    }
    refreshElapsed();
    startProgressAnimation();
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;',
    })[character]);
  }

  function setDir() {
    const rtl = rtlCodes.has((targetSelect.value || '').toLowerCase());
    workspace.setAttribute('dir', 'ltr');
    workspace.dataset.targetDirection = rtl ? 'rtl' : 'ltr';
    reviewItems?.querySelectorAll('.review-target').forEach((field) => {
      field.dir = rtl ? 'rtl' : 'ltr';
    });
  }

  async function refreshLocalEngineStatus() {
    if (!providerSelect || !localEngineStatus) return true;
    if (!providerSelect.value.startsWith('offline')) {
      localEngineStatus.className = 'local-engine-status small-muted';
      localEngineStatus.textContent = 'Online sends translation prompts to the configured provider.';
      return true;
    }
    localEngineStatus.className = 'local-engine-status checking';
    localEngineStatus.textContent = 'Checking the private engine on this PC...';
    try {
      const response = await fetch('/api/translation/local-engine/status');
      const data = await response.json();
      const needsFast = ['offline_fast', 'offline_quality'].includes(providerSelect.value);
      const needsAya = ['offline', 'offline_quality'].includes(providerSelect.value);
      const ready = (!needsFast || data.fast?.available) && (!needsAya || data.aya?.available);
      if (!ready) {
        localEngineStatus.className = 'local-engine-status unavailable';
        localEngineStatus.textContent = 'Required offline engine is not ready. Open the Quality Dashboard and choose Enable local AI.';
        return false;
      }
      localEngineStatus.className = 'local-engine-status ready';
      const engines = providerSelect.value === 'offline_fast'
        ? data.fast?.model
        : providerSelect.value === 'offline_quality'
          ? `${data.fast?.model} + ${data.aya?.model}`
          : data.aya?.model;
      localEngineStatus.textContent = `${engines || 'Local engines'} are ready. Translation-stage prompts stay on this PC; upload classification and metadata extraction use the configured ingestion services.`;
      return true;
    } catch (error) {
      localEngineStatus.className = 'local-engine-status unavailable';
      localEngineStatus.textContent = 'Could not reach the offline engine.';
      return false;
    }
  }

  function resetResult() {
    if (pollInterval) {
      clearTimeout(pollInterval);
      pollInterval = null;
    }
    if (progressAnimationFrame) {
      cancelAnimationFrame(progressAnimationFrame);
      progressAnimationFrame = null;
    }
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    progressDisplayed = 0;
    progressTarget = 0;
    progressIsActive = false;
    progressLastFrame = 0;
    elapsedBaseSeconds = 0;
    elapsedSyncedAt = Date.now();
    if (resultBox) resultBox.classList.add('hidden');
    if (messageEl) messageEl.textContent = '';
    if (metaEl) metaEl.textContent = '';
    if (downloadLink) {
      downloadLink.href = '#';
      downloadLink.setAttribute('aria-disabled', 'true');
      downloadLink.tabIndex = -1;
    }
    if (statusPill) {
      statusPill.className = 'status-pill pending';
      statusPill.textContent = 'Pending';
    }
    if (progressContainer) progressContainer.style.display = 'none';
    if (progressFill) progressFill.style.transform = 'scaleX(0)';
    if (progressPercent) progressPercent.textContent = '0%';
    if (progressLabel) progressLabel.textContent = 'Waiting for the translation worker...';
    if (progressStage) progressStage.textContent = 'Preparing translation';
    if (progressElapsed) progressElapsed.textContent = '00:00';
    if (progressActivity) progressActivity.textContent = 'Local engine active';
    if (progressTrack) progressTrack.setAttribute('aria-valuenow', '0');
    if (progressContainer) progressContainer.classList.remove('is-complete', 'is-stopped', 'is-review');
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (qualityDossier) qualityDossier.classList.add('hidden');
    if (qualitySummary) qualitySummary.textContent = '';
    if (qualityStages) qualityStages.textContent = '';
    if (qualityIssues) qualityIssues.innerHTML = '';
    if (reviewPanel) reviewPanel.classList.add('hidden');
    if (reviewItems) reviewItems.replaceChildren();
    activeReviewGroups = [];
    activeTranslationId = null;
  }

  function renderQualityDossier(report) {
    if (!qualityDossier || !report || !Object.keys(report).length) {
      if (qualityDossier) qualityDossier.classList.add('hidden');
      return;
    }
    qualityDossier.classList.remove('hidden');
    const domain = report.domain || 'general';
    const memoryHits = Number(report.translation_memory_hits || 0);
    const reviewed = Number(report.semantic_reviewed_blocks || 0);
    const backChecked = Number(report.back_translation_blocks || 0);
    const titleRequested = Number(report.mandatory_title_reviews_requested || 0);
    const titleReviewed = Number(report.mandatory_title_reviews_completed || 0);
    const recovered = Number(report.recovered_review_items || 0);
    const publicationState = report.publication_ready
      ? 'publication gate passed'
      : 'publication gate not passed';
    qualitySummary.textContent = `${domain} domain | ${report.risk_level || 'normal'} risk | ${reviewed} semantic reviews | ${titleReviewed}/${titleRequested} title reviews | ${recovered} recovered items | ${backChecked} back-translation checks | ${memoryHits} memory matches | ${publicationState}`;
    const stageNames = {
      intake_analyst: 'Document intake',
      terminology_manager: 'Terminology plan',
      translation_memory: 'Translation memory',
      draft_translator: 'First draft',
      fast_local_draft: 'Fast local draft',
      deterministic_fact_and_language_qa: 'Facts and language checks',
      consolidated_local_editor_reviewer: 'Local editor review',
      target_language_editor: 'Language editing',
      semantic_reviewer: 'Meaning review',
      back_translator: 'Back translation',
      back_translation_reviewer: 'Meaning comparison',
      fact_checker: 'Fact check',
      consistency_checker: 'Consistency check',
      layout_engineer: 'Page layout',
      publication_gate: 'Publication decision',
    };
    qualityStages.textContent = Array.isArray(report.pipeline_stages)
      ? `Team workflow: ${report.pipeline_stages.map((stage) => (
        stageNames[stage] || String(stage).replaceAll('_', ' ')
      )).join(' -> ')}`
      : '';
    qualityIssues.innerHTML = '';
    const issues = Array.isArray(report.issues) ? report.issues : [];
    if (!issues.length) {
      const item = document.createElement('li');
      item.textContent = 'No publication-blocking issues detected.';
      qualityIssues.appendChild(item);
      return;
    }
    issues.slice(0, 12).forEach((issue) => {
      const item = document.createElement('li');
      const location = issue.page_number ? `Page ${issue.page_number}: ` : '';
      item.textContent = `${String(issue.severity || 'warning').toUpperCase()} - ${location}${issue.message || 'Quality issue'}`;
      qualityIssues.appendChild(item);
    });
  }

  function renderReviewQueue(data) {
    if (!reviewPanel || !reviewItems) return;
    if (data.status !== 'NeedsReview') {
      reviewPanel.classList.add('hidden');
      reviewItems.replaceChildren();
      activeReviewGroups = [];
      return;
    }

    const issues = (Array.isArray(data.review_issues) ? data.review_issues : []).filter((issue) => (
      issue.status === 'open'
      && ['error', 'critical'].includes(String(issue.severity || '').toLowerCase())
      && String(issue.source_excerpt || '').trim()
    ));
    const grouped = new Map();
    issues.forEach((issue) => {
      const source = String(issue.source_excerpt || '').trim();
      const key = `${issue.page_number || 0}:${issue.block_number || 0}:${source}`;
      if (!grouped.has(key)) {
        grouped.set(key, {
          issueIds: [],
          sourceText: source,
          targetText: String(issue.target_excerpt || '').trim(),
          pageNumber: issue.page_number,
          blockNumber: issue.block_number,
          reasons: [],
        });
      }
      const group = grouped.get(key);
      group.issueIds.push(issue.id);
      if (String(issue.target_excerpt || '').length > group.targetText.length) {
        group.targetText = String(issue.target_excerpt || '').trim();
      }
      if (issue.message && !group.reasons.includes(issue.message)) {
        group.reasons.push(issue.message);
      }
    });
    activeReviewGroups = Array.from(grouped.values());
    reviewItems.replaceChildren();
    const rtl = rtlCodes.has(String(data.target_language || '').toLowerCase())
      || workspace.dataset.targetDirection === 'rtl';

    activeReviewGroups.forEach((group, index) => {
      const card = document.createElement('article');
      card.className = 'review-item';
      card.style.animationDelay = `${Math.min(index * 55, 220)}ms`;
      const top = document.createElement('div');
      top.className = 'review-item-top';
      const heading = document.createElement('strong');
      heading.textContent = `Failed passage ${index + 1}`;
      const location = document.createElement('span');
      location.textContent = group.pageNumber
        ? `Page ${group.pageNumber}${group.blockNumber ? `, section ${group.blockNumber}` : ''}`
        : 'Quality check';
      top.append(heading, location);

      const reason = document.createElement('p');
      reason.className = 'review-reason';
      reason.textContent = group.reasons.join(' ') || 'The automatic quality check could not approve this passage.';
      const sourceLabel = document.createElement('span');
      sourceLabel.className = 'review-label';
      sourceLabel.textContent = 'Original passage';
      const source = document.createElement('p');
      source.className = 'review-source';
      source.dir = 'auto';
      source.textContent = group.sourceText;
      const targetLabel = document.createElement('label');
      targetLabel.className = 'review-label';
      targetLabel.htmlFor = `review-target-${index}`;
      targetLabel.textContent = 'Approved translation';
      const target = document.createElement('textarea');
      target.className = 'review-target';
      target.id = `review-target-${index}`;
      target.dir = rtl ? 'rtl' : 'ltr';
      target.dataset.reviewIndex = String(index);
      target.value = group.targetText;
      target.setAttribute('aria-label', `Approved translation for failed passage ${index + 1}`);
      card.append(top, reason, sourceLabel, source, targetLabel, target);
      reviewItems.appendChild(card);
    });
    reviewPanel.classList.toggle('hidden', !activeReviewGroups.length);
  }

  function setResult(data, downloadUrl) {
    resultBox.classList.remove('hidden');
    titleEl.textContent = data.document_name || 'Translation job';
    const profile = data.detected_mode || data.translation_mode;
    const quality = Number.isFinite(Number(data.quality_score))
      ? ` | AI QA ${Number(data.quality_score).toFixed(1)}/100`
      : '';
    const domain = data.quality_report?.domain || data.domain;
    const provider = data.provider_mode === 'offline_fast'
      ? 'Offline Fast NLLB'
      : data.provider_mode === 'offline_quality'
        ? 'Offline Quality NLLB + Aya'
        : data.provider_mode === 'offline'
          ? 'Offline Aya'
          : 'Online AI';
    metaEl.textContent = `${data.source_language_label || data.source_language} to ${data.target_language_label || data.target_language} | ${provider}${profile ? ` | ${profile}` : ''}${domain ? ` | ${domain}` : ''}${quality}`;
    renderQualityDossier(data.quality_report);
    renderReviewQueue(data);
    
    // Update status badge class
    statusPill.className = `status-pill ${(data.status || 'Pending').toLowerCase()}`;
    statusPill.textContent = data.status || 'Pending';

    // Show/hide progress bar
    if (data.status === 'Processing' || data.status === 'Pending') {
      progressContainer.style.display = 'block';
      let pct = 0;
      if (Number.isFinite(Number(data.progress_percent))) {
        pct = Math.max(0, Math.min(100, Math.round(Number(data.progress_percent))));
      } else if (data.total_pages > 0) {
        pct = Math.round((data.current_page / data.total_pages) * 100);
      } else {
        pct = 2;
      }
      updateProgress(data, pct);
      messageEl.textContent = '';
      if (cancelBtn) cancelBtn.style.display = 'inline-flex';
      
      downloadLink.setAttribute('aria-disabled', 'true');
      downloadLink.removeAttribute('href');
      downloadLink.tabIndex = -1;
    } else if (data.status === 'NeedsReview') {
      progressContainer.style.display = 'block';
      updateProgress(data, Number(data.progress_percent) || progressTarget);
      if (cancelBtn) cancelBtn.style.display = 'inline-flex';
      messageEl.textContent = 'The automatic checks isolated the passages below. The rest of the translation remains safely checkpointed.';
      downloadLink.setAttribute('aria-disabled', 'true');
      downloadLink.removeAttribute('href');
      downloadLink.tabIndex = -1;
    } else if (data.status === 'Completed') {
      progressContainer.style.display = 'block';
      updateProgress({...data, progress_percent: 100}, 100);
      if (cancelBtn) cancelBtn.style.display = 'none';
      messageEl.textContent = data.status_message || 'Translation completed successfully.';
      if (downloadUrl) {
        downloadLink.href = downloadUrl;
        downloadLink.setAttribute('aria-disabled', 'false');
        downloadLink.tabIndex = 0;
      }
    } else if (data.status === 'Failed') {
      progressContainer.style.display = 'block';
      updateProgress(data, Number(data.progress_percent) || progressTarget);
      if (cancelBtn) cancelBtn.style.display = 'none';
      messageEl.textContent = `Error: ${data.error_message || data.status_message || 'Unknown translation error.'}`;
      downloadLink.setAttribute('aria-disabled', 'true');
      downloadLink.removeAttribute('href');
      downloadLink.tabIndex = -1;
    } else if (data.status === 'Cancelled') {
      progressContainer.style.display = 'block';
      updateProgress(data, Number(data.progress_percent) || progressTarget);
      if (cancelBtn) cancelBtn.style.display = 'none';
      messageEl.textContent = data.status_message || 'Translation cancelled.';
      downloadLink.setAttribute('aria-disabled', 'true');
      downloadLink.removeAttribute('href');
      downloadLink.tabIndex = -1;
    }
  }

  function upsertRow(data, downloadUrl) {
    if (!tbody) return;
    const createdAt = new Date(data.created_at || Date.now()).toLocaleDateString(undefined, {
      day: 'numeric', month: 'short', year: 'numeric'
    });
    const id = `translation-row-${data.translation_id}`;
    let row = document.getElementById(id);
    const documentName = escapeHtml(data.document_name);
    const sourceLanguage = escapeHtml(data.source_language_label || data.source_language);
    const targetLanguage = escapeHtml(data.target_language_label || data.target_language);
    const status = escapeHtml(data.status || 'Pending');
    const statusClass = escapeHtml((data.status || 'pending').toLowerCase());
    const safeDownloadUrl = downloadUrl ? escapeHtml(downloadUrl) : null;
    const actionHtml = safeDownloadUrl
      ? `<a href="${safeDownloadUrl}" class="act-btn act-view" title="Download translated PDF"><i class="bi bi-download"></i></a>`
      : data.status === 'NeedsReview'
        ? `<a href="/translate?translation_id=${Number(data.translation_id)}" class="act-btn act-view" title="Open quality decision"><i class="bi bi-person-check"></i></a>`
        : `<button class="act-btn act-view" disabled title="No download yet"><i class="bi bi-hourglass-split"></i></button>`;
    const html = `
      <td class="td-name"><div class="file-cell"><span class="file-icon pdf"><i class="bi bi-file-earmark-pdf-fill"></i></span><span class="file-name" title="${documentName}">${documentName}</span></div></td>
      <td><span class="mono small-muted">${sourceLanguage}</span></td>
      <td><span class="mono small-muted">${targetLanguage}</span></td>
      <td><span class="status-chip status-${statusClass}">${status}</span></td>
      <td><span class="small-muted">${createdAt}</span></td>
      <td class="td-actions">${actionHtml}</td>
    `;
    
    // Check if table was empty and remove the empty state row
    const emptyRow = tbody.querySelector('.empty-state');
    if (emptyRow) {
      emptyRow.parentElement.remove();
    }

    if (row) {
      row.innerHTML = html;
      return;
    }
    row = document.createElement('tr');
    row.id = id;
    row.innerHTML = html;
    tbody.insertBefore(row, tbody.firstChild);
    if (countEl) countEl.textContent = String((parseInt(countEl.textContent || '0', 10) || 0) + 1);
  }

  function pollTranslationStatus(translationId) {
    if (pollInterval) clearTimeout(pollInterval);
    activeTranslationId = translationId;

    const poll = async () => {
      try {
        const response = await fetch(`/api/translation/${translationId}/status`);
        if (!response.ok) {
          throw new Error('Failed to fetch status');
        }
        const data = await response.json();
        
        const downloadUrl = data.download_url || null;
        setResult(data, downloadUrl);
        upsertRow(data, downloadUrl);

        if (data.status === 'Completed') {
          pollInterval = null;
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
          showToast('Translation ready.', 'ok');
        } else if (data.status === 'NeedsReview') {
          pollInterval = null;
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
          showToast('A failed QA passage needs your decision.', 'ok');
        } else if (data.status === 'Failed' || data.status === 'Cancelled') {
          pollInterval = null;
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
          showToast(
            data.status === 'Cancelled' ? 'Translation cancelled.' : (data.error_message || 'Translation failed.'),
            data.status === 'Cancelled' ? 'ok' : 'error'
          );
        } else if (activeTranslationId === translationId) {
          pollInterval = window.setTimeout(poll, 1500);
        }
      } catch (error) {
        console.error('Polling error:', error);
        if (activeTranslationId === translationId) {
          pollInterval = window.setTimeout(poll, 2500);
        }
      }
    };
    pollInterval = window.setTimeout(poll, 500);
  }

  targetSelect.addEventListener('change', setDir);
  if (providerSelect) providerSelect.addEventListener('change', refreshLocalEngineStatus);
  setDir();

  resetBtn.addEventListener('click', () => {
    form.reset();
    setDir();
    refreshLocalEngineStatus();
    resetResult();
  });

  if (cancelBtn) {
    cancelBtn.addEventListener('click', async () => {
      if (!activeTranslationId) return;
      cancelBtn.disabled = true;
      try {
        const response = await fetch(`/api/translation/${activeTranslationId}/cancel`, {
          method: 'POST',
        });
        const data = await response.json();
        if (!response.ok && data.status !== 'Cancelled') {
          throw new Error(data.message || 'Cancellation failed');
        }
        setResult(data, null);
        showToast('Translation cancelled.', 'ok');
      } catch (error) {
        showToast(error.message || 'Could not cancel translation.', 'error');
      } finally {
        cancelBtn.disabled = false;
      }
    });
  }

  if (reviewSubmit) {
    reviewSubmit.addEventListener('click', async () => {
      if (!activeTranslationId || !activeReviewGroups.length) return;
      const corrections = activeReviewGroups.map((group, index) => ({
        issue_ids: group.issueIds,
        target_text: reviewItems.querySelector(`[data-review-index="${index}"]`)?.value.trim() || '',
      }));
      if (corrections.some((item) => !item.target_text)) {
        showToast('Add an approved translation for every failed passage.', 'error');
        return;
      }

      reviewSubmit.disabled = true;
      reviewSubmit.innerHTML = '<i class="bi bi-hourglass-split"></i> Checking corrections...';
      try {
        const response = await fetch(`/api/translation/${activeTranslationId}/review`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ corrections }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || 'The corrections could not be approved.');
        reviewPanel.classList.add('hidden');
        messageEl.textContent = data.message || 'Corrections approved. Translation is resuming.';
        showToast('Corrections approved. Translation resumed from its checkpoint.', 'ok');
        pollTranslationStatus(activeTranslationId);
      } catch (error) {
        showToast(error.message || 'The corrections could not be approved.', 'error');
      } finally {
        reviewSubmit.disabled = false;
        reviewSubmit.innerHTML = '<i class="bi bi-check2-circle"></i> Approve corrections and continue';
      }
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const documentId = docSelect.value;
    const sourceLanguage = sourceSelect.value;
    const targetLanguage = targetSelect.value;
    const translationMode = modeSelect.value;
    const providerMode = providerSelect ? providerSelect.value : 'online';
    const domain = domainSelect.value;
    const qualityLevel = qualityLevelSelect.value;
    const glossaryEntries = (glossaryInput.value || '')
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean)
      .map((line) => {
        const parts = line.split(/\s*(?:=>|=)\s*/, 2);
        if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
        return {
          source_term: parts[0].trim(),
          target_term: parts[1].trim(),
          authority: 'locked',
        };
      })
      .filter(Boolean);

    if (!documentId || !sourceLanguage || !targetLanguage) {
      showToast('Choose a document, input language, and target language.', 'error');
      return;
    }
    if (sourceLanguage === targetLanguage) {
      showToast('Input and target languages must be different.', 'error');
      return;
    }
    if (providerMode.startsWith('offline') && !(await refreshLocalEngineStatus())) {
      showToast('Set up and start the local AI engine before using Offline mode.', 'error');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Translating…';
    resetResult();

    try {
      const response = await fetch('/api/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          document_id: documentId,
          source_language: sourceLanguage,
          target_language: targetLanguage,
          translation_mode: translationMode,
          provider_mode: providerMode,
          domain,
          quality_level: qualityLevel,
          enable_back_translation: Boolean(backTranslationInput.checked),
          glossary_entries: glossaryEntries,
        }),
      });
      const data = await response.json();
      if (!response.ok || data.status === 'error') {
        showToast(data.message || 'Translation failed.', 'error');
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
        return;
      }

      const downloadUrl = data.download_url || null;
      setResult(data, downloadUrl);
      upsertRow(data, downloadUrl);

      if (data.status === 'Completed') {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
        showToast('Translation ready.', 'ok');
      } else {
        // Start background progress tracking
        pollTranslationStatus(data.translation_id);
        showToast('Translation started in background.', 'ok');
      }
    } catch (error) {
      console.error(error);
      showToast('Network error during translation.', 'error');
      submitBtn.disabled = false;
      submitBtn.innerHTML = '<i class="bi bi-magic"></i> Translate PDF';
    }
  });

  const requestedTranslationId = Number(
    new URLSearchParams(window.location.search).get('translation_id')
  );
  if (Number.isInteger(requestedTranslationId) && requestedTranslationId > 0) {
    pollTranslationStatus(requestedTranslationId);
  }
});
