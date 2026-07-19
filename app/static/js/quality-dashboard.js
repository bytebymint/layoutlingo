document.addEventListener('DOMContentLoaded', () => {
  const engineCards = document.getElementById('engine-cards');
  const overall = document.getElementById('engine-overall');
  const control = document.getElementById('engine-control');
  const controlTitle = document.getElementById('engine-control-title');
  const controlMessage = document.getElementById('engine-control-message');
  const toggle = document.getElementById('local-ai-toggle');
  const operationsPanel = document.getElementById('operations-panel');
  const operationsList = document.getElementById('operations-list');
  const operationsOverall = document.getElementById('operations-overall');
  const operationsSync = document.getElementById('operations-sync');
  if (!engineCards || !overall || !control || !toggle) return;

  let engineState = 'checking';
  let refreshTimer = null;
  let refreshBusy = false;

  const createElement = (tag, className, textValue) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (textValue !== undefined) element.textContent = textValue;
    return element;
  };

  const setText = (root, selector, value) => {
    const element = root?.querySelector(selector);
    if (element && element.textContent !== value) element.textContent = value;
  };

  const formatElapsed = (value) => {
    const seconds = Math.max(0, Number(value || 0));
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60);
    return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
  };

  const engineLiveLabel = (name, engine) => {
    const activity = engine?.activity || {};
    if (activity.state !== 'working') return 'Standing by';
    const elapsed = formatElapsed(activity.elapsed_seconds);
    if (name === 'fast') {
      const count = Number(activity.current_segments || 0);
      const passages = `${count || 'A'} passage${count === 1 ? '' : 's'}`;
      return `Drafting ${passages} locally · ${elapsed}`;
    }
    return `Generating a review response · ${elapsed}`;
  };

  const updateCard = (name, engine) => {
    const card = engineCards.querySelector(`[data-engine="${name}"]`);
    if (!card) return;
    const ready = Boolean(engine?.available);
    const activity = engine?.activity || {};
    const working = activity.state === 'working';
    card.classList.toggle('is-ready', ready);
    card.classList.toggle('is-unavailable', !ready);
    card.classList.toggle('is-working', working);
    setText(card, '.engine-state', working ? 'Working now' : (ready ? 'Ready to help' : 'Currently off'));
    setText(card, '[data-engine-live]', engineLiveLabel(name, engine));
    setText(card, '[data-engine-model]', ready ? (engine.model || activity.model || 'Local AI model') : 'Not running');
    setText(card, '[data-engine-action]', working
      ? (activity.action || 'Working on a translation')
      : (ready ? (activity.action || 'Standing by') : 'Waiting to be enabled'));
    const total = name === 'fast'
      ? Number(activity.translated_segments || 0)
      : Number(activity.completed_requests || 0);
    const totalLabel = name === 'fast'
      ? `${total} passage${total === 1 ? '' : 's'} translated`
      : `${total} review${total === 1 ? '' : 's'} completed`;
    setText(card, '[data-engine-total]', totalLabel);
  };

  const updateEngineControl = (engine) => {
    const aya = engine?.aya || {};
    const fast = engine?.fast || {};
    engineState = engine?.state || (aya.available ? 'ready' : 'off');
    const isOn = engineState === 'ready';
    const isStarting = engineState === 'starting';
    control.classList.toggle('is-on', isOn);
    control.classList.toggle('is-starting', isStarting);
    toggle.disabled = isStarting;
    toggle.setAttribute('aria-pressed', String(isOn));
    setText(toggle, '.power-copy strong', isStarting
      ? 'Waking up local AI'
      : (isOn ? 'Turn off local AI' : 'Enable local AI'));
    setText(toggle, '.power-copy small', isStarting
      ? 'Loading privately from Drive D'
      : (isOn ? 'Reviewer is available locally' : 'Run privately from Drive D'));

    if (isStarting) {
      setText(control, '#engine-control-title', 'Your quality reviewer is waking up');
      setText(control, '#engine-control-message', 'The model is loading from Drive D. This can take a short moment.');
    } else if (isOn) {
      setText(control, '#engine-control-title', 'Your private AI team is available');
      setText(control, '#engine-control-message', 'Fast translation loads when needed; the quality reviewer is ready now.');
    } else {
      setText(control, '#engine-control-title', 'Local AI is currently off');
      setText(control, '#engine-control-message', engine?.last_error || 'Enable it when you want to run a private offline translation.');
    }

    updateCard('fast', fast);
    updateCard('aya', aya);
    const allReady = Boolean(fast.available && aya.available);
    overall.classList.toggle('is-ready', allReady);
    overall.classList.toggle('is-attention', !allReady);
    overall.textContent = allReady
      ? 'Both AI tools are ready'
      : (isStarting ? 'Local AI is starting' : 'Local reviewer is off');
  };

  const stageFamily = (key) => {
    if (['planning', 'routing', 'preparing', 'queued'].includes(key)) return 'planning';
    if (['quality', 'review'].includes(key)) return 'quality';
    if (['layout', 'rendering', 'saving'].includes(key)) return 'rendering';
    return 'translation';
  };

  const updatePipeline = (operations) => {
    document.querySelectorAll('[data-pipeline-stage]').forEach((item) => item.classList.remove('is-active'));
    const working = operations.find((item) => item.status === 'Processing') || operations[0];
    if (!working) return;
    document.querySelector(`[data-pipeline-stage="${stageFamily(working.stage?.key)}"]`)?.classList.add('is-active');
  };

  const operationLiveLabel = (operation) => {
    const active = Array.isArray(operation.live_engines) ? operation.live_engines[0] : null;
    if (!active) return operation.status_message || operation.action || 'Preparing the next safe step';
    const elapsed = formatElapsed(active.elapsed_seconds);
    const detail = active.engine === 'fast' && active.segment_count
      ? ` · ${active.segment_count} passage${active.segment_count === 1 ? '' : 's'}`
      : '';
    return `${active.label}: ${active.action}${detail} · ${elapsed}`;
  };

  const buildOperation = (operation) => {
    const card = createElement('article', 'operation-card');
    card.dataset.translationId = String(operation.translation_id);
    const summary = createElement('button', 'operation-summary');
    summary.type = 'button';
    summary.setAttribute('aria-expanded', 'false');
    const mark = createElement('span', 'operation-owner-mark');
    mark.appendChild(createElement('i', 'bi bi-stars'));
    const file = createElement('span', 'operation-file');
    file.appendChild(createElement('strong', 'operation-document'));
    file.appendChild(createElement('small', 'operation-page'));
    const owner = createElement('span', 'operation-owner');
    owner.appendChild(createElement('strong', 'operation-owner-name'));
    owner.appendChild(createElement('small', 'operation-owner-action'));
    const percent = createElement('span', 'operation-percent');
    summary.append(mark, file, owner, percent);

    const details = createElement('div', 'operation-details');
    details.hidden = true;
    details.appendChild(createElement('p', 'operation-live-copy'));
    const link = createElement('a', 'operation-link');
    details.appendChild(link);
    const progress = createElement('div', 'operation-progress');
    progress.appendChild(createElement('span'));
    details.appendChild(progress);

    summary.addEventListener('click', () => {
      const open = summary.getAttribute('aria-expanded') === 'true';
      summary.setAttribute('aria-expanded', String(!open));
      details.hidden = open;
      card.classList.toggle('is-expanded', !open);
    });
    card.append(summary, details);
    return card;
  };

  const updateOperation = (card, operation) => {
    const review = operation.status === 'NeedsReview';
    const live = Array.isArray(operation.live_engines) && operation.live_engines.length > 0;
    card.classList.toggle('is-review', review);
    card.classList.toggle('is-live', live);
    card.classList.toggle('is-processing', operation.status === 'Processing');
    const icon = card.querySelector('.operation-owner-mark i');
    if (icon) icon.className = `bi ${review ? 'bi-person-check' : (live ? 'bi-cpu' : 'bi-stars')}`;
    setText(card, '.operation-document', operation.document_name || 'Translation');
    const pageText = operation.total_pages
      ? `Page ${operation.current_page || 0} of ${operation.total_pages}`
      : (review ? 'A quality decision is waiting' : 'Preparing pages');
    setText(card, '.operation-page', pageText);
    setText(card, '.operation-owner-name', operation.owner || 'Translation team');
    setText(card, '.operation-owner-action', operation.action || 'Working safely');
    setText(card, '.operation-percent', `${operation.progress_percent || 0}%`);
    setText(card, '.operation-live-copy', operationLiveLabel(operation));
    const link = card.querySelector('.operation-link');
    if (link) {
      link.textContent = review ? 'Open quality decision' : 'Open translation';
      link.href = `/translate?translation_id=${Number(operation.translation_id)}`;
    }
    const progress = card.querySelector('.operation-progress');
    progress?.style.setProperty('--operation-progress', Math.max(0, Math.min(1, Number(operation.progress_percent || 0) / 100)));
  };

  const renderOperations = (data) => {
    if (!operationsList || !operationsOverall) return;
    const operations = Array.isArray(data?.operations) ? data.operations : [];
    if (!operations.length) {
      if (!operationsList.querySelector('.operation-empty')) {
        const empty = createElement('div', 'operation-empty');
        empty.appendChild(createElement('span', 'operation-empty-orb'));
        const copy = createElement('div');
        copy.appendChild(createElement('strong', '', 'The translation desk is clear'));
        copy.appendChild(createElement('p', '', 'Start a translation and its live stages will appear here.'));
        empty.appendChild(copy);
        operationsList.replaceChildren(empty);
      }
      operationsOverall.className = 'plain-status is-ready';
      operationsOverall.textContent = 'No active work';
    } else {
      const existing = new Map(
        [...operationsList.querySelectorAll('.operation-card')]
          .map((card) => [card.dataset.translationId, card]),
      );
      const activeIds = new Set();
      operations.forEach((operation) => {
        const id = String(operation.translation_id);
        const card = existing.get(id) || buildOperation(operation);
        activeIds.add(id);
        updateOperation(card, operation);
        operationsList.appendChild(card);
      });
      existing.forEach((card, id) => {
        if (!activeIds.has(id)) card.remove();
      });
      const waiting = Number(data.waiting_for_review_count || 0);
      const active = Number(data.active_count || 0);
      operationsOverall.className = `plain-status ${waiting ? 'is-attention' : 'is-ready'}`;
      operationsOverall.textContent = waiting
        ? `${waiting} decision${waiting === 1 ? '' : 's'} needed`
        : `${active} job${active === 1 ? '' : 's'} working`;
    }
    updatePipeline(operations);
    if (operationsSync) {
      operationsSync.textContent = data?.live_engines?.length
        ? 'Live model activity, refreshed just now'
        : 'Live board, waiting for the next step';
    }
  };

  const refresh = async () => {
    if (refreshBusy || document.hidden) return;
    refreshBusy = true;
    try {
      const operationsUrl = operationsPanel?.dataset.operationsUrl;
      const response = await fetch(operationsUrl || engineCards.dataset.engineUrl, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      if (!response.ok) throw new Error('The local AI status could not be checked.');
      const data = await response.json();
      if (data.engine) {
        updateEngineControl(data.engine);
        renderOperations(data);
      } else {
        updateEngineControl(data);
      }
    } catch (_error) {
      updateEngineControl({
        state: 'off', aya: { available: false }, fast: { available: false },
        last_error: 'The dashboard could not check local AI right now.',
      });
      operationsOverall?.classList.add('is-attention');
      if (operationsOverall) operationsOverall.textContent = 'Update unavailable';
      if (operationsSync) operationsSync.textContent = 'Live updates will retry automatically';
    } finally {
      refreshBusy = false;
      window.clearTimeout(refreshTimer);
      const activeWork = engineCards.querySelector('.engine-card.is-working')
        || operationsList?.querySelector('.operation-card.is-processing');
      refreshTimer = window.setTimeout(refresh, activeWork || engineState === 'starting' ? 1000 : 8000);
    }
  };

  toggle.addEventListener('click', async () => {
    const turningOff = engineState === 'ready';
    toggle.disabled = true;
    if (!turningOff) updateEngineControl({ state: 'starting', aya: { available: false }, fast: { available: true } });
    try {
      const response = await fetch(
        turningOff ? engineCards.dataset.stopUrl : engineCards.dataset.startUrl,
        { method: 'POST', headers: { Accept: 'application/json' } },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'The local AI setting could not be changed.');
      updateEngineControl(data);
    } catch (error) {
      controlMessage.textContent = error.message || 'The local AI setting could not be changed.';
      control.classList.remove('is-starting');
    } finally {
      toggle.disabled = false;
      refresh();
    }
  });

  engineCards.querySelectorAll('.engine-card-summary').forEach((summary) => {
    summary.addEventListener('click', () => {
      const card = summary.closest('.engine-card');
      const details = card?.querySelector('.engine-card-details');
      const open = summary.getAttribute('aria-expanded') === 'true';
      summary.setAttribute('aria-expanded', String(!open));
      if (details) details.hidden = open;
      card?.classList.toggle('is-expanded', !open);
    });
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });
  refresh();
});
