document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('compare-form');
  const docSelect1 = document.getElementById('doc-select-1');
  const docSelect2 = document.getElementById('doc-select-2');
  const submitBtn = document.getElementById('compare-submit-btn');

  // Loading elements
  const loader = document.getElementById('compare-loader');
  const progressFill = document.getElementById('compare-progress-fill');
  const percentEl = document.getElementById('compare-percent');
  const timeLeftEl = document.getElementById('compare-time-left');
  const statusEl = document.getElementById('compare-status');
  const detailEl = document.getElementById('compare-detail');

  // Results elements
  const resultsContainer = document.getElementById('comparison-results');
  const diffCountEl = document.getElementById('summary-diff-count');
  const matchPercentEl = document.getElementById('summary-match-percent');
  const overviewTextEl = document.getElementById('summary-overview-text');
  const doc1TypeEl = document.getElementById('overview-doc1-type');
  const doc2TypeEl = document.getElementById('overview-doc2-type');
  
  const changedInfoList = document.getElementById('changed-info-list');
  const riskAnalysisList = document.getElementById('risk-analysis-list');
  const addedContentList = document.getElementById('added-content-list');
  const removedContentList = document.getElementById('removed-content-list');
  const recommendationText = document.getElementById('recommendation-text');

  // Export buttons
  const btnCopyMarkdown = document.getElementById('btn-copy-markdown');
  const btnCopyJson = document.getElementById('btn-copy-json');
  const btnExportPrint = document.getElementById('btn-export-print');

  let currentComparisonData = null;
  let comparisonsHistoryCache = {};
  let progressInterval = null;

  // Stages of comparison
  const stages = [
    { pct: 5,   time: '~28s', status: 'Reading document contents...',      detail: 'Extracting source text and metadata' },
    { pct: 15,  time: '~25s', status: 'Parsing classifications...',         detail: 'Comparing document type contexts' },
    { pct: 28,  time: '~22s', status: 'Computing semantic similarities...', detail: 'Running TF-cosine similarity calculations' },
    { pct: 40,  time: '~19s', status: 'Comparing document metadata...',    detail: 'Correlating extracted JSON key-values' },
    { pct: 52,  time: '~16s', status: 'Mapping document chunks...',         detail: 'Identifying parallel sections' },
    { pct: 64,  time: '~12s', status: 'Sending inputs to AI model...',      detail: 'Requesting deep semantic comparison' },
    { pct: 75,  time: '~9s',  status: 'AI is analyzing difference maps...', detail: 'Detecting additions, removals, and modifications' },
    { pct: 85,  time: '~6s',  status: 'Assessing legal & financial risks...',detail: 'Identifying contract liabilities and omissions' },
    { pct: 92,  time: '~3s',  status: 'Compiling recommendations...',     detail: 'Drafting structured summary and next steps' },
    { pct: 97,  time: '~1s',  status: 'Formatting comparison report...',    detail: 'Validating JSON document output structure' }
  ];

  function startProgress() {
    let stageIdx = 0;
    updateProgress(stages[0]);

    progressInterval = setInterval(() => {
      stageIdx++;
      if (stageIdx < stages.length) {
        updateProgress(stages[stageIdx]);
      } else {
        clearInterval(progressInterval);
        progressInterval = null;
      }
    }, 2200);
  }

  function updateProgress(stage) {
    if (progressFill) progressFill.style.width = stage.pct + '%';
    if (percentEl) percentEl.textContent = stage.pct + '%';
    if (timeLeftEl) timeLeftEl.textContent = stage.time + ' remaining';
    if (statusEl) statusEl.textContent = stage.status;
    if (detailEl) detailEl.textContent = stage.detail;
  }

  function finishProgress() {
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
    if (progressFill) progressFill.style.width = '100%';
    if (percentEl) percentEl.textContent = '100%';
    if (timeLeftEl) timeLeftEl.textContent = '0s';
    if (statusEl) statusEl.textContent = 'Comparison report loaded!';
    if (detailEl) detailEl.textContent = 'Rendering results...';
  }

  // Load history list from server to build local cache
  async function reloadHistory() {
    try {
      const response = await fetch('/api/comparisons');
      const data = await response.json();
      if (data.comparisons) {
        data.comparisons.forEach(c => {
          comparisonsHistoryCache[c.id] = c;
        });
      }
    } catch (e) {
      console.error('Failed to load comparison history:', e);
    }
  }
  
  reloadHistory(); // Load initially

  // Handle form submission
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      
      const doc1 = docSelect1.value;
      const doc2 = docSelect2.value;
      
      if (doc1 === doc2) {
        alert('Please select two different documents to compare.');
        return;
      }

      // Hide results & show loader
      resultsContainer.style.display = 'none';
      loader.style.display = 'block';
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Comparing...';
      
      startProgress();

      try {
        const response = await fetch('/api/documents/compare', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ document_id_1: parseInt(doc1), document_id_2: parseInt(doc2) })
        });

        const data = await response.json();

        finishProgress();

        if (data.status === 'success') {
          setTimeout(() => {
            loader.style.display = 'none';
            renderComparison(data.comparison);
            // Refresh history table
            refreshHistoryTable();
          }, 800);
        } else {
          loader.style.display = 'none';
          alert('Comparison failed: ' + (data.message || 'Unknown error'));
        }
      } catch (err) {
        console.error(err);
        loader.style.display = 'none';
        alert('Network error while running comparison.');
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Compare Documents';
      }
    });
  }

  function renderComparison(comparison) {
    currentComparisonData = comparison;
    resultsContainer.style.display = 'block';

    const overview = comparison.overview || {};
    const changed = comparison.changed_information || [];
    const added = comparison.added_content || [];
    const removed = comparison.removed_content || [];
    const risks = comparison.risk_analysis || [];
    const rec = comparison.final_recommendation || "";

    // Set stats
    diffCountEl.textContent = changed.length + added.length + removed.length;
    matchPercentEl.textContent = (overview.similarity_score || 0) + '%';
    overviewTextEl.textContent = overview.summary || 'No summary overview provided.';
    doc1TypeEl.textContent = overview.document_1_type || 'Document A';
    doc2TypeEl.textContent = overview.document_2_type || 'Document B';

    // 1. Changed information
    changedInfoList.innerHTML = '';
    if (changed.length > 0) {
      changed.forEach(item => {
        const itemCard = document.createElement('div');
        itemCard.className = 'change-card glass fade-in';
        
        let importanceClass = 'badge-low';
        if (item.importance?.toLowerCase() === 'high') importanceClass = 'badge-high';
        else if (item.importance?.toLowerCase() === 'medium') importanceClass = 'badge-medium';

        itemCard.innerHTML = `
          <div class="change-card-header">
            <span class="change-cat">${item.category || 'General Change'}</span>
            <span class="importance-badge ${importanceClass}">${(item.importance || 'Low').toUpperCase()}</span>
          </div>
          <div class="change-diff-split">
            <div class="diff-box before">
              <span class="diff-lbl">Document A (Before)</span>
              <p>${item.before || '—'}</p>
            </div>
            <div class="diff-arrow"><i class="bi bi-arrow-right"></i></div>
            <div class="diff-box after">
              <span class="diff-lbl">Document B (After)</span>
              <p>${item.after || '—'}</p>
            </div>
          </div>
        `;
        changedInfoList.appendChild(itemCard);
      });
    } else {
      changedInfoList.innerHTML = '<p class="empty-state-info glass-panel">No modifications detected.</p>';
    }

    // 2. Added Section
    addedContentList.innerHTML = '';
    if (added.length > 0) {
      added.forEach(text => {
        const card = document.createElement('div');
        card.className = 'added-item-card glass fade-in';
        card.innerHTML = `<span class="add-icon">+</span> <p>${text}</p>`;
        addedContentList.appendChild(card);
      });
    } else {
      addedContentList.innerHTML = '<p class="empty-state-info glass-panel">No added clauses or content.</p>';
    }

    // 3. Removed Section
    removedContentList.innerHTML = '';
    if (removed.length > 0) {
      removed.forEach(text => {
        const card = document.createElement('div');
        card.className = 'removed-item-card glass fade-in';
        card.innerHTML = `<span class="remove-icon">-</span> <p>${text}</p>`;
        removedContentList.appendChild(card);
      });
    } else {
      removedContentList.innerHTML = '<p class="empty-state-info glass-panel">No removed clauses or content.</p>';
    }

    // 4. Risks Section
    riskAnalysisList.innerHTML = '';
    if (risks.length > 0) {
      risks.forEach(text => {
        const card = document.createElement('div');
        card.className = 'risk-item-card glass fade-in';
        card.innerHTML = `
          <div class="risk-icon"><i class="bi bi-exclamation-triangle-fill"></i></div>
          <p>${text}</p>
        `;
        riskAnalysisList.appendChild(card);
      });
    } else {
      riskAnalysisList.innerHTML = '<p class="empty-state-info glass-panel">No warnings or severe risks identified.</p>';
    }

    // 5. Final Recommendation
    recommendationText.textContent = rec || 'No final recommendation generated.';
    
    // Smooth scroll down to results
    resultsContainer.scrollIntoView({ behavior: 'smooth' });
  }

  // Load from history cache
  document.addEventListener('click', (e) => {
    const loadBtn = e.target.closest('.btn-load-comp');
    if (loadBtn) {
      const compId = loadBtn.getAttribute('data-comp-id');
      const cached = comparisonsHistoryCache[compId];
      if (cached && cached.result) {
        renderComparison(cached.result);
      } else {
        alert('Could not retrieve cached comparison report.');
      }
    }
  });

  // Delete comparison handler
  document.addEventListener('click', async (e) => {
    const deleteBtn = e.target.closest('.btn-delete-comp');
    if (deleteBtn) {
      const compId = deleteBtn.getAttribute('data-comp-id');
      if (confirm('Are you sure you want to delete this comparison from history?')) {
        try {
          const response = await fetch(`/api/comparison/${compId}`, { method: 'DELETE' });
          if (response.ok) {
            const row = document.getElementById(`comp-row-${compId}`);
            if (row) row.remove();
            delete comparisonsHistoryCache[compId];
            
            // Check if history is empty now
            const historyBody = document.getElementById('history-tbody');
            if (historyBody && historyBody.children.length === 0) {
              historyBody.innerHTML = `
                <tr id="comp-empty-row">
                  <td colspan="4" class="empty-state">
                    <div class="empty-inner">
                      <i class="bi bi-calculator"></i>
                      <p>No comparisons run yet. Select two documents above to begin.</p>
                    </div>
                  </td>
                </tr>
              `;
            }
          } else {
            alert('Failed to delete comparison.');
          }
        } catch (err) {
          alert('Network error while deleting comparison.');
        }
      }
    }
  });

  // Refresh history UI table after a new comparison runs
  async function refreshHistoryTable() {
    await reloadHistory();
    const historyBody = document.getElementById('history-tbody');
    if (!historyBody) return;

    let rowsHTML = '';
    const sortedComps = Object.values(comparisonsHistoryCache).sort((a,b) => new Date(b.created_at) - new Date(a.created_at));

    if (sortedComps.length > 0) {
      sortedComps.forEach(comp => {
        const formattedDate = new Date(comp.created_at).toLocaleDateString('en-GB', {
          day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
        });
        rowsHTML += `
          <tr id="comp-row-${comp.id}">
            <td><strong>${escapeHTML(comp.document_one_name)}</strong></td>
            <td><strong>${escapeHTML(comp.document_two_name)}</strong></td>
            <td><span class="small-muted">${formattedDate}</span></td>
            <td class="td-actions">
              <div class="action-group">
                <button class="act-btn act-view btn-load-comp" data-comp-id="${comp.id}" title="View Comparison">
                  <i class="bi bi-eye-fill"></i>
                </button>
                <button class="act-btn act-delete btn-delete-comp" data-comp-id="${comp.id}" title="Delete Comparison">
                  <i class="bi bi-trash3-fill"></i>
                </button>
              </div>
            </td>
          </tr>
        `;
      });
    } else {
      rowsHTML = `
        <tr id="comp-empty-row">
          <td colspan="4" class="empty-state">
            <div class="empty-inner">
              <i class="bi bi-calculator"></i>
              <p>No comparisons run yet. Select two documents above to begin.</p>
            </div>
          </td>
        </tr>
      `;
    }
    historyBody.innerHTML = rowsHTML;
  }

  function escapeHTML(str) {
    if (!str) return '';
    return str.replace(/[&<>'"]/g, 
      tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
  }

  // Export Copy Markdown
  if (btnCopyMarkdown) {
    btnCopyMarkdown.addEventListener('click', () => {
      if (!currentComparisonData) return;
      const md = generateMarkdownReport(currentComparisonData);
      navigator.clipboard.writeText(md).then(() => {
        btnCopyMarkdown.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
        setTimeout(() => {
          btnCopyMarkdown.innerHTML = '<i class="bi bi-markdown-fill"></i> Copy Markdown Report';
        }, 2000);
      });
    });
  }

  // Export Copy JSON
  if (btnCopyJson) {
    btnCopyJson.addEventListener('click', () => {
      if (!currentComparisonData) return;
      const jsonStr = JSON.stringify(currentComparisonData, null, 2);
      navigator.clipboard.writeText(jsonStr).then(() => {
        btnCopyJson.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
        setTimeout(() => {
          btnCopyJson.innerHTML = '<i class="bi bi-braces"></i> Copy JSON Data';
        }, 2000);
      });
    });
  }

  // Export Print Report
  if (btnExportPrint) {
    btnExportPrint.addEventListener('click', () => {
      window.print();
    });
  }

  function generateMarkdownReport(data) {
    const overview = data.overview || {};
    const changed = data.changed_information || [];
    const added = data.added_content || [];
    const removed = data.removed_content || [];
    const risks = data.risk_analysis || [];
    const rec = data.final_recommendation || "";

    let md = `# AI Document Comparison Report\n\n`;
    md += `## 1. Overview\n`;
    md += `- **Document 1 Type**: ${overview.document_1_type || 'Unknown'}\n`;
    md += `- **Document 2 Type**: ${overview.document_2_type || 'Unknown'}\n`;
    md += `- **Semantic Similarity**: ${overview.similarity_score || 0}%\n\n`;
    md += `### Summary\n${overview.summary || 'N/A'}\n\n`;

    md += `## 2. Changed Information\n\n`;
    if (changed.length > 0) {
      changed.forEach(item => {
        md += `### [${item.importance || 'Medium'}] ${item.category}\n`;
        md += `- **Document A (Before)**: ${item.before || '—'}\n`;
        md += `- **Document B (After)**: ${item.after || '—'}\n\n`;
      });
    } else {
      md += `No changed information detected.\n\n`;
    }

    md += `## 3. Added Content\n`;
    if (added.length > 0) {
      added.forEach(text => {
        md += `- **[ADDED]** ${text}\n`;
      });
      md += `\n`;
    } else {
      md += `No added content detected.\n\n`;
    }

    md += `## 4. Removed Content\n`;
    if (removed.length > 0) {
      removed.forEach(text => {
        md += `- **[REMOVED]** ${text}\n`;
      });
      md += `\n`;
    } else {
      md += `No removed content detected.\n\n`;
    }

    md += `## 5. Risk Analysis\n`;
    if (risks.length > 0) {
      risks.forEach(text => {
        md += `- ⚠️ ${text}\n`;
      });
      md += `\n`;
    } else {
      md += `No severe risks identified.\n\n`;
    }

    md += `## 6. Final Recommendation\n`;
    md += `${rec || 'N/A'}\n`;

    return md;
  }
});
