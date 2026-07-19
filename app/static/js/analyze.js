document.addEventListener('DOMContentLoaded', () => {
  const analyzeBtn = document.getElementById('analyzeBtn');
  const overlay = document.getElementById('analyzeOverlay');
  const analysisPanel = document.getElementById('analysisPanel');
  const analysisContent = document.getElementById('analysisContent');
  const copyReportBtn = document.getElementById('copyReportBtn');
  const closeAnalysisBtn = document.getElementById('closeAnalysisBtn');

  // Progress elements
  const progressFill = document.getElementById('analyzeProgressFill');
  const percentEl = document.getElementById('analyzePercent');
  const timeLeftEl = document.getElementById('analyzeTimeLeft');
  const statusEl = document.getElementById('analyzeStatus');
  const detailEl = document.getElementById('analyzeDetail');

  if (!analyzeBtn) return;

  const docId = analyzeBtn.getAttribute('data-doc-id');
  let rawMarkdown = '';
  let progressInterval = null;

  // Progress stages — simulated but feels real
  const stages = [
    { pct: 5,   time: '~30s', status: 'Initializing analysis engine…',           detail: 'Preparing document context and chunks' },
    { pct: 12,  time: '~28s', status: 'Loading document text…',                   detail: 'Retrieving OCR content and metadata' },
    { pct: 22,  time: '~25s', status: 'Extracting document chunks…',              detail: 'Splitting text into semantic segments' },
    { pct: 30,  time: '~22s', status: 'Building analysis context…',               detail: 'Selecting most relevant chunks for AI' },
    { pct: 40,  time: '~20s', status: 'Sending to AI model…',                     detail: 'Transmitting context to AI engine' },
    { pct: 50,  time: '~17s', status: 'AI is reading the document…',              detail: 'Model is processing document content' },
    { pct: 58,  time: '~14s', status: 'Generating document overview…',            detail: 'Identifying key themes and structure' },
    { pct: 65,  time: '~12s', status: 'Extracting key information…',              detail: 'Pulling structured data and entities' },
    { pct: 72,  time: '~9s',  status: 'Analyzing important sections…',            detail: 'Evaluating critical document passages' },
    { pct: 78,  time: '~7s',  status: 'Generating insights…',                     detail: 'Drawing conclusions from document data' },
    { pct: 85,  time: '~5s',  status: 'Scanning for risks & anomalies…',          detail: 'Checking for red flags and inconsistencies' },
    { pct: 90,  time: '~3s',  status: 'Writing final summary…',                   detail: 'Composing the structured report' },
    { pct: 95,  time: '~1s',  status: 'Finalizing report…',                       detail: 'Formatting and validating output' },
  ];

  function startProgress() {
    let stageIdx = 0;
    // Reset to first stage
    updateProgress(stages[0]);

    progressInterval = setInterval(() => {
      stageIdx++;
      if (stageIdx < stages.length) {
        updateProgress(stages[stageIdx]);
      } else {
        // Hold at 95% until real response arrives
        clearInterval(progressInterval);
        progressInterval = null;
      }
    }, 2200);
  }

  function updateProgress(stage) {
    if (progressFill) {
      progressFill.style.width = stage.pct + '%';
      progressFill.style.animation = 'none'; // stop the slide animation, use real width
      progressFill.style.marginLeft = '0';
    }
    if (percentEl)  percentEl.textContent = stage.pct + '%';
    if (timeLeftEl) timeLeftEl.textContent = stage.time + ' remaining';
    if (statusEl)   statusEl.textContent = stage.status;
    if (detailEl)   detailEl.textContent = stage.detail;
  }

  function finishProgress() {
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
    updateProgress({ pct: 100, time: '0s', status: 'Analysis complete!', detail: 'Rendering report…' });
  }

  function resetProgress() {
    if (progressFill) {
      progressFill.style.width = '0%';
      progressFill.style.animation = '';
      progressFill.style.marginLeft = '';
    }
    if (percentEl)  percentEl.textContent = '0%';
    if (timeLeftEl) timeLeftEl.textContent = '';
    if (statusEl)   statusEl.textContent = '';
    if (detailEl)   detailEl.textContent = '';
  }

  analyzeBtn.addEventListener('click', async () => {
    // Disable button & show overlay
    analyzeBtn.disabled = true;
    analyzeBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Analyzing…';
    overlay.style.display = 'flex';
    startProgress();

    try {
      const response = await fetch('/api/document/' + docId + '/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await response.json();

      finishProgress();

      if (data.status === 'success') {
        rawMarkdown = data.analysis;

        // Parse markdown — use marked if available, else basic fallback
        let html = '';
        if (typeof marked !== 'undefined') {
          html = marked.parse(data.analysis);
        } else {
          html = data.analysis
            .replace(/^## (.*$)/gim, '<h2>$1</h2>')
            .replace(/^### (.*$)/gim, '<h3>$1</h3>')
            .replace(/^\- (.*$)/gim, '<li>$1</li>')
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/\n/g, '<br>');
        }

        analysisContent.innerHTML = html;

        // Highlight Risks / Anomalies sections
        analysisContent.querySelectorAll('h2, h3').forEach(h => {
          if (/risk|anomal/i.test(h.textContent)) {
            h.classList.add('risk-heading');
            let sibling = h.nextElementSibling;
            while (sibling && !['H2', 'H3'].includes(sibling.tagName)) {
              sibling.classList.add('risk-content');
              sibling = sibling.nextElementSibling;
            }
          }
        });

        // Brief delay so user sees 100%, then show panel
        setTimeout(() => {
          overlay.style.display = 'none';
          resetProgress();
          analysisPanel.style.display = 'flex';
          analysisPanel.classList.add('fade-in');
        }, 600);

      } else {
        overlay.style.display = 'none';
        resetProgress();
        alert('Analysis failed: ' + (data.message || 'Unknown error'));
      }
    } catch (err) {
      console.error('Analysis error:', err);
      overlay.style.display = 'none';
      resetProgress();
      alert('Error while analyzing document. Please try again.');
    } finally {
      analyzeBtn.disabled = false;
      analyzeBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Analyze Document';
    }
  });

  // Copy Report button
  if (copyReportBtn) {
    copyReportBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(rawMarkdown).then(() => {
        copyReportBtn.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
        setTimeout(() => {
          copyReportBtn.innerHTML = '<i class="bi bi-clipboard"></i> Copy';
        }, 2000);
      });
    });
  }

  // Close analysis panel
  if (closeAnalysisBtn) {
    closeAnalysisBtn.addEventListener('click', () => {
      analysisPanel.style.display = 'none';
      analysisPanel.classList.remove('fade-in');
    });
  }
});
