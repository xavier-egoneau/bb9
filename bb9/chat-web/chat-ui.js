import {REQUIRED_FEATURES} from './bb9-client.js?v=workspace-switch-1';
import {
  renderApproval,
  renderArtifacts,
  renderMarkdownFragment,
  renderMessageContent,
  renderTrace,
  renderTraceStep,
  traceDisplayGroups,
  workflowGroups,
} from './renderers.js?v=workspace-switch-1';

export function liveTraceDisplayGroups(groups) {
  return traceDisplayGroups(groups);
}

export function liveTraceVisibleGroups(groups, limit = 6) {
  const displayGroups = liveTraceDisplayGroups(groups);
  if (displayGroups.length <= limit) return displayGroups;
  const visibleIndexes = new Set();
  displayGroups.forEach((group, index) => {
    if (group.kind === 'subagent' && String(group.subagentStatus || '').toLowerCase() === 'running') {
      visibleIndexes.add(index);
    }
  });
  for (let index = Math.max(0, displayGroups.length - limit); index < displayGroups.length; index += 1) {
    visibleIndexes.add(index);
  }
  return displayGroups.filter((_, index) => visibleIndexes.has(index));
}

export function latestValidationMessageIndex(messages, approval = null) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === 'assistant' && isValidationMessage(message, approval)) return index;
    if (message.role === 'assistant') return -1;
  }
  return -1;
}

function isValidationMessage(message, approval = null) {
  const content = String(message && message.content ? message.content : '').trim();
  if (content === 'Validation requise.') return true;
  if (content.startsWith('Validation requise pour ')) return true;
  const taskTitle = String(approval && approval.task_title ? approval.task_title : '').trim();
  return Boolean(taskTitle && content.includes(`Validation requise pour \`${taskTitle}\``));
}

export function planTaskStatus(task) {
  const status = String(task && task.status ? task.status : '').trim().toLowerCase();
  if ((task && task.done) || status === 'done') return 'done';
  if (status === 'blocked' || dependencyOnlyBlockers(task && task.blockers ? task.blockers : '') || dependencySkipSummary(task && task.summary ? task.summary : '')) return 'blocked';
  if (status === 'error') return 'error';
  return 'pending';
}

export function planHasRetryableErrors(tasks) {
  return (Array.isArray(tasks) ? tasks : []).some((task) => planTaskStatus(task) === 'error');
}

export function idleTraceLabel(events, idleSeconds) {
  return `${idleTraceSubject(events)} · ${Number(idleSeconds || 0)}s sans nouvelle trace`;
}

function idleTraceSubject(events) {
  const groups = workflowGroups(events || []);
  const runningSubagent = groups.findLast
    ? groups.findLast((group) => group.kind === 'subagent' && String(group.subagentStatus || '').toLowerCase() === 'running')
    : groups.slice().reverse().find((group) => group.kind === 'subagent' && String(group.subagentStatus || '').toLowerCase() === 'running');
  if (runningSubagent) return 'Subagent en cours';
  const latest = groups[groups.length - 1];
  if (latest && String(latest.status || '').toLowerCase() === 'en cours') return 'Toujours en cours';
  return 'Aucune nouvelle trace';
}

function dependencyOnlyBlockers(value) {
  const blockers = String(value || '').split(/[;,]/).map((item) => item.trim()).filter(Boolean);
  return Boolean(blockers.length) && blockers.every((blocker) => blocker.startsWith('dependency:'));
}

function dependencySkipSummary(value) {
  const summary = String(value || '').toLowerCase().replace(/\s+/g, ' ');
  return summary.includes('dependencies are not done') || summary.includes('dependencies could not be resolved');
}

export function createBb9Chat({root = document, client, capabilities = {}}) {
  const resolvedCapabilities = {...client.capabilities, ...capabilities};
  const elements = getElements(root);
  const attachments = [];
  const themeStoreKey = 'bb9.chat.theme';
  const planCollapsedStoreKey = 'bb9.chat.plan.collapsed';
  const channelSeenStoreKey = 'bb9.chat.channel.seen.v2';
  let commands = [];
  let commandIndex = 0;
  let themes = fallbackThemes();
  let running = false;
  let stopRequested = false;
  let activeController = null;
  let pendingApproval = null;
  let draftQueue = [];
  let draftId = 0;
  let composerObserver = null;
  let activityNode = null;
  let activityTraceNode = null;
  let liveTraceEvents = [];
  let liveTraceCursor = 0;
  let liveTraceTimer = null;
  let liveTraceInFlight = false;
  let liveTraceGeneration = 0;
  let liveTraceRunId = '';
  let statusTimer = null;
  let statusInFlight = false;
  let projectInFlight = false;
  let channelPollTick = 0;
  let projectReloadInFlight = false;
  let runningSince = 0;
  let planCollapsed = localStorage.getItem(planCollapsedStoreKey) === '1';
  let planFingerprint = '';
  let currentProjectPath = '';
  let workspaceWarningText = '';
  let channelSeen = readJsonStore(channelSeenStoreKey);

  function addMessage(role, content, meta = {}, options = {}) {
    const stickToBottom = Object.prototype.hasOwnProperty.call(options, 'stickToBottom') ? Boolean(options.stickToBottom) : shouldStickToBottom();
    if (role === 'assistant') removeActivityIndicator();
    const node = document.createElement('section');
    node.className = `message ${role}`;
    appendMessageContent(node, role, content, meta);
    elements.thread.appendChild(node);
    if (stickToBottom) scrollToThreadBottom();
  }

  function finalizeActivityMessage(content, meta = {}, options = {}) {
    if (!activityNode) {
      addMessage('assistant', content, meta, options);
      return;
    }
    const stickToBottom = Object.prototype.hasOwnProperty.call(options, 'stickToBottom') ? Boolean(options.stickToBottom) : shouldStickToBottom();
    const node = activityNode;
    stopLiveTracePolling();
    node.className = 'message assistant';
    node.removeAttribute('aria-live');
    node.textContent = '';
    activityNode = null;
    activityTraceNode = null;
    appendMessageContent(node, 'assistant', content, meta);
    if (stickToBottom) scrollToThreadBottom();
  }

  function appendMessageContent(node, role, content, meta = {}) {
    const label = document.createElement('div');
    label.className = 'role';
    if (role === 'notification') {
      label.innerHTML = '<svg width="11" height="11" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true" style="vertical-align:-1px;margin-right:5px;opacity:.75"><rect x="2" y="0" width="8" height="5" rx="1"/><rect x="0" y="6" width="12" height="2.5" rx="1"/><rect x="1" y="9.5" width="2" height="2.5" rx="0.5"/><rect x="4" y="9.5" width="2" height="2.5" rx="0.5"/><rect x="7" y="9.5" width="2" height="2.5" rx="0.5"/><rect x="10" y="9.5" width="1.5" height="2.5" rx="0.5"/></svg>Info';
    } else {
      label.textContent = role === 'user' ? 'Vous' : 'BB9';
    }
    node.append(label, renderMessageContent(content, client, {markdown: role === 'assistant' || role === 'notification'}));
    if (role === 'assistant') node.appendChild(copyButton(content));
    const trace = renderTrace(meta.events || [], meta.artifacts || []);
    if (trace) node.append(trace);
    if (meta.artifacts && meta.artifacts.length) node.append(renderArtifacts(meta.artifacts, client));
    if (meta.approval && resolvedCapabilities.canApprove) {
      node.append(renderApproval(meta.approval, resolveApproval));
    }
    if (meta.staleApproval) node.append(renderInactiveApprovalNotice());
  }

  function shouldStickToBottom(threshold = 96) {
    const distance = elements.main.scrollHeight - elements.main.scrollTop - elements.main.clientHeight;
    return distance <= threshold;
  }

  function scrollToThreadBottom() {
    elements.main.scrollTop = elements.main.scrollHeight;
    window.requestAnimationFrame(() => {
      elements.main.scrollTop = elements.main.scrollHeight;
    });
  }

  function renderInactiveApprovalNotice() {
    const node = document.createElement('div');
    node.className = 'approval inactive';
    const title = document.createElement('div');
    title.className = 'approval-title';
    title.textContent = 'Validation inactive';
    const reason = document.createElement('div');
    reason.className = 'approval-reason';
    reason.textContent = 'Cette validation n’est plus disponible, probablement après un redémarrage du serveur. Relance la demande pour recréer une action à valider.';
    node.append(title, reason);
    return node;
  }

  function showActivityIndicator(options = {}) {
    if (activityNode) return;
    const stickToBottom = shouldStickToBottom();
    const node = document.createElement('section');
    node.className = 'message assistant working';
    node.setAttribute('aria-live', 'polite');
    const label = document.createElement('div');
    label.className = 'role';
    label.textContent = 'BB9';
    const body = document.createElement('div');
    body.className = 'message-text working-content';
    const text = document.createElement('span');
    text.className = 'working-label';
    text.textContent = 'Traitement en cours';
    body.append(text);
    const trace = document.createElement('details');
    trace.className = 'trace working-live-trace';
    trace.open = true;
    const summary = document.createElement('summary');
    const title = document.createElement('span');
    title.textContent = 'Processus';
    const count = document.createElement('span');
    count.className = 'trace-count';
    count.textContent = '0 étape';
    summary.append(title, count);
    const timeline = document.createElement('div');
    timeline.className = 'working-trace timeline';
    activityTraceNode = timeline;
    trace.append(summary, timeline);
    if (!options.preserveTrace) resetLiveTrace();
    renderLiveTrace(liveTraceEvents);
    node.append(label, body);
    node.append(trace);
    activityNode = node;
    elements.thread.appendChild(node);
    if (stickToBottom) scrollToThreadBottom();
  }

  function removeActivityIndicator() {
    if (!activityNode) return;
    activityNode.remove();
    activityNode = null;
    activityTraceNode = null;
    stopLiveTracePolling();
  }

  function renderLiveTrace(events) {
    if (!activityTraceNode) return;
    const stickToBottom = shouldStickToBottom();
    activityTraceNode.textContent = '';
    let groups = workflowGroups(events || []);
    if (!groups.length) {
      groups = [{
        kind: 'process',
        title: 'Préparer le travail',
        status: 'en cours',
        summary: 'Je prépare le contexte et j’attends les premiers événements du run.',
        guardians: [],
      }];
    }
    const visibleGroups = liveTraceVisibleGroups(groups);
    const count = activityTraceNode.closest('.trace')?.querySelector('.trace-count');
    if (count) count.textContent = `${visibleGroups.length} étape${visibleGroups.length > 1 ? 's' : ''}`;
    visibleGroups.forEach((group) => activityTraceNode.appendChild(renderTraceStep(group)));
    const latest = groups[groups.length - 1];
    const label = activityNode ? activityNode.querySelector('.working-label') : null;
    if (label) label.textContent = latest && (latest.title || latest.tool) ? `${latest.title || latest.tool}` : 'Traitement en cours';
    if (stickToBottom) scrollToThreadBottom();
  }

  function startLiveTracePolling() {
    if (liveTraceTimer || !client.runEvents) return;
    const poll = async () => {
      if (!running || !activityNode) return;
      if (liveTraceInFlight) return;
      const generation = liveTraceGeneration;
      const cursor = liveTraceCursor;
      liveTraceInFlight = true;
      try {
        const payload = await client.runEvents(cursor);
        if (generation !== liveTraceGeneration) return;
        if (payload.ok) {
          const runId = String(payload.run_id || '');
          if (!payload.running || !runId) return;
          if (liveTraceRunId && runId && runId !== liveTraceRunId) return;
          if (!liveTraceRunId && runId) liveTraceRunId = runId;
          liveTraceCursor = Number(payload.next || liveTraceCursor);
          liveTraceEvents = liveTraceEvents.concat(payload.events || []).slice(-50);
          renderLiveTrace(liveTraceEvents);
          updateRunWaitLabel(payload);
        }
      } catch (_) {
        // La trace finale arrivera avec la réponse du tour.
      } finally {
        if (generation === liveTraceGeneration) liveTraceInFlight = false;
      }
    };
    poll();
    liveTraceTimer = window.setInterval(poll, 900);
  }

  function renderPlan(plan, options = {}) {
    if (!elements.planPanel) return;
    const planProject = String(plan && plan.project_path ? plan.project_path : '');
    if (planProject && currentProjectPath && planProject !== currentProjectPath) return;
    const exists = Boolean(plan && plan.exists);
    if (!exists) {
      planFingerprint = '';
      elements.planPanel.hidden = true;
      elements.planPanel.textContent = '';
      syncComposerSpace();
      return;
    }
    const markdown = String(plan.markdown || '');
    const nextFingerprint = `${plan.updated_at || ''}:${markdown.length}:${markdown.slice(0, 80)}`;
    if (options.openOnChange && nextFingerprint && nextFingerprint !== planFingerprint) {
      planCollapsed = false;
      localStorage.setItem(planCollapsedStoreKey, '0');
    }
    planFingerprint = nextFingerprint;
    const tasks = Array.isArray(plan.tasks) ? plan.tasks : [];
    const total = Number(plan.total || tasks.length || 0);
    const completed = Number(plan.completed || tasks.filter((task) => task.done).length || 0);
    const errors = tasks.filter((task) => planTaskStatus(task) === 'error').length;
    const blocked = tasks.filter((task) => planTaskStatus(task) === 'blocked').length;
    const retryableErrors = planHasRetryableErrors(tasks);
    elements.planPanel.hidden = false;
    elements.planPanel.textContent = '';
    elements.planPanel.classList.toggle('collapsed', planCollapsed);

    const header = document.createElement('div');
    header.className = 'plan-header';
    const title = document.createElement('span');
    title.className = 'plan-heading';
    const titleText = document.createElement('span');
    titleText.className = 'plan-heading-title';
    titleText.textContent = 'Plan courant';
    const titleMeta = document.createElement('span');
    titleMeta.className = 'plan-title';
    const metaParts = total ? [`${completed} tâches sur ${total} terminées`] : ['Aucune tâche structurée'];
    if (errors) metaParts.push(`${errors} erreur${errors > 1 ? 's' : ''}`);
    if (blocked) metaParts.push(`${blocked} bloquée${blocked > 1 ? 's' : ''}`);
    titleMeta.textContent = metaParts.join(' · ');
    title.append(titleText, titleMeta);
    const togglePlan = () => {
      planCollapsed = !planCollapsed;
      localStorage.setItem(planCollapsedStoreKey, planCollapsed ? '1' : '0');
      renderPlan(plan);
      elements.input.focus();
    };
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'plan-clear';
    clear.title = 'Vider le plan';
    clear.setAttribute('aria-label', 'Vider le plan courant');
    clear.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M4 7h16"></path><path d="M10 11v6"></path><path d="M14 11v6"></path><path d="M6 7l1 14h10l1-14"></path><path d="M9 7V4h6v3"></path></svg>';
    clear.addEventListener('click', clearPlan);
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'plan-retry';
    retry.title = 'Relancer les erreurs';
    retry.setAttribute('aria-label', 'Relancer les tâches en erreur du plan');
    retry.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.64-6.36"></path><path d="M21 3v6h-6"></path></svg>';
    retry.addEventListener('click', retryPlanErrors);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'plan-toggle';
    toggle.title = planCollapsed ? 'Ouvrir le plan' : 'Fermer le plan';
    toggle.setAttribute('aria-label', planCollapsed ? 'Ouvrir le plan courant' : 'Fermer le plan courant');
    toggle.setAttribute('aria-expanded', planCollapsed ? 'false' : 'true');
    const chevron = document.createElement('span');
    chevron.className = 'plan-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    toggle.appendChild(chevron);
    toggle.addEventListener('click', togglePlan);
    const actions = document.createElement('div');
    actions.className = 'plan-actions';
    if (retryableErrors) actions.appendChild(retry);
    actions.append(clear, toggle);
    header.append(title, actions);
    elements.planPanel.appendChild(header);

    const body = document.createElement('div');
    body.className = 'plan-body';
    if (tasks.length) {
      const list = document.createElement('div');
      list.className = 'plan-tasks';
      for (const task of tasks) list.appendChild(renderPlanTask(task));
      body.appendChild(list);
    } else {
      const markdownBody = document.createElement('div');
      markdownBody.className = 'plan-markdown markdown compact-markdown';
      markdownBody.appendChild(renderMarkdownFragment(markdown));
      body.appendChild(markdownBody);
    }
    elements.planPanel.appendChild(body);
    syncComposerSpace();
  }

  async function clearPlan(event) {
    if (event) event.stopPropagation();
    if (!client.clearPlan) return;
    elements.status.textContent = 'Plan vidé';
    try {
      const payload = await client.clearPlan(currentProjectPath);
      if (!payload.ok) throw new Error(payload.message || payload.error || 'plan clear failed');
      renderPlan(payload.plan);
    } catch (err) {
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
      elements.input.focus();
    }
  }

  async function retryPlanErrors(event) {
    if (event) event.stopPropagation();
    const command = '/build --retry-errors';
    if (pendingApproval || running) {
      enqueueDraft(command);
      elements.status.textContent = pendingApproval ? 'Validation en attente' : 'Relance ajoutée à la queue';
      elements.input.focus();
      return;
    }
    await sendNow(command);
  }

  function renderPlanTask(task) {
    const status = planTaskStatus(task);
    const row = document.createElement('div');
    row.className = `plan-task ${status}`;
    const box = document.createElement('span');
    box.className = 'plan-task-box';
    box.setAttribute('aria-hidden', 'true');
    const content = document.createElement('span');
    content.className = 'plan-task-content';
    const label = document.createElement('span');
    label.className = 'plan-task-label';
    const prefix = task.id ? `${task.id} · ` : '';
    label.textContent = `${prefix}${task.title || 'Tâche'}`;
    content.appendChild(label);
    if (status === 'error' || status === 'blocked') {
      const details = [task.blockers, task.summary, task.evidence].map((v) => String(v || '').trim()).filter(Boolean);
      const deduped = details.filter((value, i) => !details.some((other, j) => j < i && other.toLowerCase().includes(value.toLowerCase())));
      const reason = compactPlanText(deduped.join(' — ') || 'Erreur pendant /build.');
      if (reason) {
        const meta = document.createElement('span');
        meta.className = 'plan-task-meta';
        meta.textContent = reason;
        content.appendChild(meta);
      }
    }
    row.append(box, content);
    return row;
  }

  function compactPlanText(value, limit = 180) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    if (text.length <= limit) return text;
    return `${text.slice(0, limit - 3).trim()}...`;
  }

  function stopLiveTracePolling() {
    liveTraceGeneration += 1;
    if (!liveTraceTimer) return;
    window.clearInterval(liveTraceTimer);
    liveTraceTimer = null;
    liveTraceInFlight = false;
  }

  function resetLiveTrace() {
    liveTraceGeneration += 1;
    liveTraceEvents = [];
    liveTraceCursor = 0;
    liveTraceInFlight = false;
    liveTraceRunId = '';
  }

  function copyButton(content) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'copy-message';
    button.title = 'Copier la réponse';
    button.setAttribute('aria-label', 'Copier la réponse');
    button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"></path></svg>';
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(String(content || ''));
        button.classList.add('copied');
        setTimeout(() => button.classList.remove('copied'), 900);
      } catch (_) {
        button.classList.add('copy-failed');
        setTimeout(() => button.classList.remove('copy-failed'), 900);
      }
    });
    return button;
  }

  function renderQueued() {
    elements.queued.textContent = '';
    attachments.forEach((attachment, index) => {
      const item = document.createElement('div');
      item.className = 'thumb';
      const img = document.createElement('img');
      img.src = attachment.preview;
      const label = document.createElement('span');
      label.textContent = attachment.name || attachment.mime;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'thumb-remove';
      remove.title = 'Retirer cette image';
      remove.setAttribute('aria-label', 'Retirer cette image');
      remove.textContent = '×';
      remove.onclick = () => removeAttachment(index);
      item.append(img, label, remove);
      elements.queued.appendChild(item);
    });
  }

  function removeAttachment(index) {
    attachments.splice(index, 1);
    renderQueued();
    elements.input.focus();
  }

  async function uploadFile(file) {
    if (!resolvedCapabilities.canUpload || !file || !file.type.startsWith('image/')) return;
    elements.status.textContent = 'Upload image';
    const preview = await filePreview(file);
    const payload = await client.upload(file);
    if (!payload.ok) throw new Error(payload.error || 'upload failed');
    attachments.push({
      reference: payload.reference,
      mime: payload.mime,
      name: file.name,
      preview,
    });
    renderQueued();
    elements.status.textContent = 'Prêt';
  }

  async function uploadFiles(files) {
    try {
      for (const file of files) await uploadFile(file);
    } catch (err) {
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
      elements.input.focus();
    }
  }

  async function resolveApproval(id, decision, node, options = {}) {
    elements.status.textContent = options.trust_root
      ? 'Trusted root ajouté'
      : (decision === 'allow' ? 'Action autorisée' : 'Action refusée');
    const buttons = node.querySelectorAll('button');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const payload = await client.resolveApproval(id, decision, options);
      if (!payload.ok) {
        if (payload.error === 'approval_not_found') {
          pendingApproval = null;
          node.replaceWith(renderInactiveApprovalNotice());
          await loadStatus();
          return;
        }
        throw new Error(payload.message || payload.error || 'approval failed');
      }
      pendingApproval = payload.approval || null;
      if ('plan' in payload) renderPlan(payload.plan, {openOnChange: true});
      addMessage('assistant', payload.answer, {
        events: payload.events,
        artifacts: payload.artifacts,
        approval: payload.approval,
      });
      if (payload.notice) addMessage('notification', payload.notice);
      loadStatus();
    } catch (err) {
      buttons.forEach((button) => { button.disabled = false; });
      await loadStatus();
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = pendingApproval ? 'Validation en attente' : 'Prêt';
      if (!pendingApproval) runNextDraft();
    }
  }

  function updateContextBar(payload) {
    const bar = elements.contextBar;
    const fill = elements.contextBarFill;
    if (!bar || !fill) return;
    const windowTokens = Number(payload.context_window_tokens) || 0;
    const usedTokens = Number(payload.estimated_tokens) || 0;
    if (!windowTokens) {
      bar.hidden = true;
      return;
    }
    const ratio = Math.max(0, Math.min(1, usedTokens / windowTokens));
    bar.hidden = false;
    fill.style.width = `${(ratio * 100).toFixed(1)}%`;
    bar.classList.toggle('context-bar-warn', ratio >= 0.7 && ratio < 0.9);
    bar.classList.toggle('context-bar-danger', ratio >= 0.9);
    const percent = Math.round(ratio * 100);
    const usedLabel = usedTokens.toLocaleString('fr-FR');
    const windowLabel = windowTokens.toLocaleString('fr-FR');
    bar.title = `Contexte : ${usedLabel} / ${windowLabel} tokens (${percent}%)`;
  }

  async function loadStatus() {
    if (statusInFlight) return;
    statusInFlight = true;
    try {
      const payload = await client.status();
      if (!payload.ok) return;
      const projectChanged = syncCurrentProject(payload);
      if ('plan' in payload) renderPlan(payload.plan);
      const model = payload.model ? ` · ${payload.model}` : '';
      const reasoning = payload.reasoning_effort ? ` · ${payload.reasoning_effort}` : '';
      const active = payload.active_project && payload.active_project !== payload.workspace
        ? ` · vue: ${payload.active_project}`
        : '';
      elements.status.title = `${payload.workspace}${active} · ${payload.provider}${model}${reasoning} · ${payload.profile} · ${payload.agent}`;
      applyWorkspaceWarning(payload);
      updateContextBar(payload);
      reconcileRuntimeStatus(payload);
      if (projectChanged && !running && !activeController) {
        reloadProjectViewAfterExternalSwitch(payload).catch(() => {});
      }
    } finally {
      statusInFlight = false;
    }
  }

  function applyWorkspaceWarning(payload) {
    const warning = String(payload && payload.workspace_warning ? payload.workspace_warning : '').trim();
    if (warning) {
      if (!elements.banner.textContent || elements.banner.textContent === workspaceWarningText) {
        elements.banner.textContent = warning;
        workspaceWarningText = warning;
      }
      return;
    }
    if (workspaceWarningText && elements.banner.textContent === workspaceWarningText) {
      elements.banner.textContent = '';
    }
    workspaceWarningText = '';
  }

  function reconcileRuntimeStatus(payload) {
    const previousApprovalId = pendingApproval && pendingApproval.id ? String(pendingApproval.id) : '';
    if (payload.pending_approval) pendingApproval = payload.pending_approval;
    if (pendingApproval && !payload.pending_approval) pendingApproval = null;
    if (payload.pending_approval && !payload.running) {
      const approvalChanged = previousApprovalId !== String(payload.pending_approval.id || '');
      if (running && Date.now() - runningSince > 500 && !activeController) {
        stopRequested = false;
        setRunning(false);
        elements.status.textContent = 'Validation en attente';
        recoverCompletedRunFromHistory();
        return;
      }
      if (approvalChanged && !activeController) recoverCompletedRunFromHistory();
    }
    if (!payload.running && running && Date.now() - runningSince > 1800) {
      if (activeController) return;
      stopRequested = false;
      setRunning(false);
      elements.status.textContent = 'Prêt';
      recoverCompletedRunFromHistory();
      if (!pendingApproval) runNextDraft();
    }
    if (payload.running && !running) {
      setRunning(true);
      startLiveTracePolling();
      elements.status.textContent = 'BB9 travaille';
    }
    if (payload.running) updateRunWaitLabel(payload);
  }

  function updateRunWaitLabel(payload) {
    const idleSeconds = Number(payload && payload.run_idle_seconds ? payload.run_idle_seconds : 0);
    const label = activityNode ? activityNode.querySelector('.working-label') : null;
    if (idleSeconds >= 15) {
      const text = idleTraceLabel(liveTraceEvents, idleSeconds);
      if (label) label.textContent = text;
      elements.status.textContent = text;
      return;
    }
    if (isIdleTraceStatus(elements.status.textContent)) {
      elements.status.textContent = 'BB9 travaille';
    }
  }

  function isIdleTraceStatus(value) {
    const text = String(value || '');
    return text.startsWith('En attente provider') || text.startsWith('Subagent en cours') || text.startsWith('Toujours en cours') || text.startsWith('Aucune nouvelle trace');
  }

  function recoverCompletedRunFromHistory() {
    if (!resolvedCapabilities.canLoadHistory || !client.history) return;
    loadHistory().catch(() => {});
    loadSessions().catch(() => {});
    loadGit().catch(() => {});
  }

  async function loadGit() {
    if (!client.git) return;
    try {
      const payload = await client.git();
      if (!payload.ok) throw new Error(payload.error || 'git failed');
      renderGit(payload);
    } catch (err) {
      elements.gitBranch.disabled = true;
      elements.gitDiff.hidden = true;
      elements.gitCount.hidden = true;
      renderGitPanel({git: false, files: [], files_changed: 0, error: String(err)});
    }
  }

  function renderGit(payload) {
    const isGit = Boolean(payload.git);
    elements.gitDiff.disabled = !isGit;
    elements.gitDiff.hidden = !isGit;
    elements.gitBranch.textContent = '';
    if (isGit) {
      const branches = payload.branches && payload.branches.length
        ? payload.branches
        : [{name: payload.branch || 'detached', current: true}];
      for (const branch of branches) {
        const option = document.createElement('option');
        option.value = branch.name;
        option.textContent = branch.name;
        option.selected = branch.current || branch.name === payload.branch;
        elements.gitBranch.appendChild(option);
      }
      elements.gitBranch.value = payload.branch && branches.some((branch) => branch.name === payload.branch)
        ? payload.branch
        : elements.gitBranch.value;
    }
    const count = Number(payload.files_changed || 0);
    elements.gitBranch.disabled = !isGit || count > 0;
    elements.gitBranch.title = count > 0 ? 'Commit ou stash requis avant de changer de branche.' : 'Branche Git';
    elements.gitBranchNote.textContent = isGit && count > 0
      ? 'Commit ou stash requis avant de changer de branche.'
      : '';
    elements.gitCount.textContent = String(count);
    elements.gitCount.hidden = !isGit || count === 0;
    const canCommit = Boolean(client.gitCommitMessage && client.commitGit);
    elements.gitCommit.disabled = !isGit || count === 0 || !canCommit;
    elements.gitCommit.title = count > 0 ? 'Préparer un message de commit' : 'Aucun changement à committer';
    if (!isGit || count === 0) {
      resetGitCommitPreview();
      elements.gitCommitNote.textContent = '';
    }
    renderGitPanel(payload);
  }

  function renderGitPanel(payload) {
    elements.gitPanelTitle.textContent = payload.git
      ? `${Number(payload.files_changed || 0)} fichier(s) modifié(s)`
      : 'Git indisponible';
    elements.gitFiles.textContent = '';
    const files = payload.files || [];
    if (!payload.git) {
      const empty = document.createElement('div');
      empty.className = 'git-file';
      empty.textContent = payload.error || 'Projet hors dépôt Git.';
      elements.gitFiles.appendChild(empty);
      return;
    }
    if (!files.length) {
      const empty = document.createElement('div');
      empty.className = 'git-file';
      empty.textContent = 'Aucun fichier modifié.';
      elements.gitFiles.appendChild(empty);
      return;
    }
    for (const file of files) {
      const row = document.createElement('details');
      row.className = 'git-file';
      const status = document.createElement('span');
      status.className = `git-file-status ${gitStatusClass(file.status || '')}`;
      status.title = `Statut Git: ${file.status || '??'}`;
      status.textContent = gitStatusLabel(file.status || '');
      const path = document.createElement('span');
      path.className = 'git-file-path';
      path.title = file.path || '';
      path.textContent = file.path || '';
      const stats = document.createElement('span');
      stats.className = 'git-file-stats';
      const plus = document.createElement('span');
      plus.className = 'git-plus';
      plus.textContent = `+${Number(file.insertions || 0)}`;
      const minus = document.createElement('span');
      minus.className = 'git-minus';
      minus.textContent = `-${Number(file.deletions || 0)}`;
      stats.append(plus, minus);
      const summary = document.createElement('summary');
      const fileMain = document.createElement('span');
      fileMain.className = 'git-file-main';
      fileMain.append(status, path);
      summary.append(fileMain, stats);
      const detail = document.createElement('pre');
      detail.className = 'git-diff-detail';
      detail.textContent = 'Chargement...';
      row.append(summary, detail);
      row.addEventListener('toggle', () => {
        if (row.open && !row.dataset.loaded) loadGitFileDiff(file.path || '', detail, row);
      });
      elements.gitFiles.appendChild(row);
    }
  }

  function gitStatusLabel(status) {
    const value = String(status).trim();
    if (value === '??') return 'Nouveau';
    if (value === 'M') return 'Modifié';
    if (value === 'A') return 'Ajouté';
    if (value === 'D') return 'Supprimé';
    if (value === 'R') return 'Renommé';
    return value || 'Changé';
  }

  function gitStatusClass(status) {
    const value = String(status).trim();
    if (value === '??' || value === 'A') return 'new';
    if (value === 'D') return 'deleted';
    return 'modified';
  }

  // ── Providers modal ────────────────────────────────
  const providersModal = root.querySelector('#providers-modal');
  const providersList = root.querySelector('#providers-list');
  const providersForm = root.querySelector('#providers-form');
  const providersAddBtn = root.querySelector('#providers-add-btn');
  const providersModalClose = root.querySelector('#providers-modal-close');
  const pfId = root.querySelector('#pf-id');
  const pfName = root.querySelector('#pf-name');
  const pfCancel = root.querySelector('#pf-cancel');
  const pfSubmit = root.querySelector('#pf-submit');
  const pfProvider = root.querySelector('#pf-provider');
  const pfUrl = root.querySelector('#pf-url');
  const pfKey = root.querySelector('#pf-key');
  const pfWebProvider = root.querySelector('#pf-web-provider');
  const providersApiSection = root.querySelector('#providers-api-section');
  const providersWebSection = root.querySelector('#providers-web-section');

  const PROVIDER_URLS = {
    openai: 'https://api.openai.com/v1',
    deepseek: 'https://api.deepseek.com/v1',
    openrouter: 'https://openrouter.ai/api/v1',
    'openai-compatible': '',
    ollama: 'http://localhost:11434/v1',
    'ollama-cloud': 'https://ollama.com',
  };

  let providersAuthType = 'api';

  function openProvidersModal() {
    providersModal.hidden = false;
    providersForm.hidden = true;
    loadProvidersModal();
    providersModal.addEventListener('click', onProvidersModalBackdropClick);
  }

  function closeProvidersModal() {
    providersModal.hidden = true;
    providersForm.hidden = true;
    providersModal.removeEventListener('click', onProvidersModalBackdropClick);
  }

  function onProvidersModalBackdropClick(event) {
    if (event.target === providersModal) closeProvidersModal();
  }

  async function loadProvidersModal() {
    providersList.innerHTML = '';
    if (!client.providers) return;
    const payload = await client.providers().catch(() => null);
    if (!payload || !payload.ok) return;
    renderProvidersModal(payload.providers || [], payload.active_id || '');
  }

  function renderProvidersModal(providers, activeId) {
    if (!providers.length) {
      providersList.innerHTML = '<p class="providers-empty">Aucun provider configuré.</p>';
      return;
    }
    providersList.innerHTML = '';
    for (const p of providers) {
      const row = document.createElement('div');
      row.className = 'provider-row';
      row.innerHTML = `
        <div class="provider-row-info">
          <div class="provider-row-name">${escapeHtml(p.name)}</div>
          <div class="provider-row-meta">${escapeHtml(p.provider)}${p.auth_type === 'web' ? ' · Web auth' : ' · API key'}${p.model ? ' · ' + escapeHtml(p.model) : ''}</div>
        </div>
        ${p.id === activeId ? '<span class="provider-row-active">actif</span>' : ''}
        <button class="provider-edit" type="button" data-id="${escapeHtml(p.id)}" aria-label="Modifier">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="provider-delete" type="button" data-id="${escapeHtml(p.id)}" aria-label="Supprimer">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      `;
      const providerData = p;
      row.querySelector('.provider-edit').addEventListener('click', () => showProvidersEditForm(providerData));
      row.querySelector('.provider-delete').addEventListener('click', async (event) => {
        const id = event.currentTarget.dataset.id;
        await client.deleteProvider(id);
        await loadProvidersModal();
        await loadModels('', '');
      });
      providersList.appendChild(row);
    }
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function showProvidersAddForm() {
    pfId.value = '';
    pfName.value = '';
    providersAuthType = 'api';
    providersApiSection.hidden = false;
    providersWebSection.hidden = true;
    for (const tab of providersForm.querySelectorAll('.providers-auth-tab')) {
      tab.classList.toggle('active', tab.dataset.auth === 'api');
      tab.disabled = false;
    }
    pfProvider.value = 'openai';
    pfUrl.value = PROVIDER_URLS.openai;
    pfKey.value = '';
    pfKey.placeholder = 'sk-...';
    pfSubmit.textContent = 'Ajouter';
    providersForm.hidden = false;
    providersAddBtn.hidden = true;
  }

  function showProvidersEditForm(provider) {
    pfId.value = provider.id;
    pfName.value = provider.name;
    providersAuthType = provider.auth_type;
    if (provider.auth_type === 'web') {
      providersApiSection.hidden = true;
      providersWebSection.hidden = false;
      pfWebProvider.value = provider.provider;
    } else {
      providersApiSection.hidden = false;
      providersWebSection.hidden = true;
      pfProvider.value = provider.provider;
      pfUrl.value = provider.base_url;
      pfKey.value = '';
      pfKey.placeholder = '••••••••  (laisser vide pour conserver)';
    }
    for (const tab of providersForm.querySelectorAll('.providers-auth-tab')) {
      tab.classList.toggle('active', tab.dataset.auth === provider.auth_type);
      tab.disabled = true;
    }
    pfSubmit.textContent = 'Mettre à jour';
    providersForm.hidden = false;
    providersAddBtn.hidden = true;
  }

  function hideProvidersAddForm() {
    providersForm.hidden = true;
    providersAddBtn.hidden = false;
    pfId.value = '';
    pfKey.placeholder = 'sk-...';
    pfSubmit.textContent = 'Ajouter';
    for (const tab of providersForm.querySelectorAll('.providers-auth-tab')) {
      tab.disabled = false;
    }
  }

  providersAddBtn.addEventListener('click', showProvidersAddForm);
  pfCancel.addEventListener('click', hideProvidersAddForm);
  providersModalClose.addEventListener('click', closeProvidersModal);

  pfProvider.addEventListener('change', () => {
    pfUrl.value = PROVIDER_URLS[pfProvider.value] || '';
  });

  for (const tab of providersForm.querySelectorAll('.providers-auth-tab')) {
    tab.addEventListener('click', () => {
      providersAuthType = tab.dataset.auth;
      for (const t of providersForm.querySelectorAll('.providers-auth-tab')) {
        t.classList.toggle('active', t === tab);
      }
      providersApiSection.hidden = providersAuthType !== 'api';
      providersWebSection.hidden = providersAuthType !== 'web';
    });
  }

  providersForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    pfSubmit.disabled = true;
    const isEdit = !!pfId.value.trim();
    pfSubmit.textContent = '…';
    const name = pfName.value.trim();
    const data = providersAuthType === 'api'
      ? {auth_type: 'api', provider: pfProvider.value, base_url: pfUrl.value.trim(), api_key: pfKey.value.trim(), name}
      : {auth_type: 'web', provider: pfWebProvider.value, name};
    let payload;
    if (isEdit) {
      data.id = pfId.value.trim();
      payload = await client.updateProvider(data).catch(() => null);
    } else {
      payload = await client.addProvider(data).catch(() => null);
    }
    pfSubmit.disabled = false;
    pfSubmit.textContent = isEdit ? 'Mettre à jour' : 'Ajouter';
    if (payload && payload.ok) {
      hideProvidersAddForm();
      await loadProvidersModal();
      await loadModels('', '');
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !providersModal.hidden) closeProvidersModal();
  });
  // ─────────────────────────────────────────────────────

  // ── Skills modal ─────────────────────────────────────
  const skillsModal = root.querySelector('#skills-modal');
  const skillsModalClose = root.querySelector('#skills-modal-close');
  const skillsList = root.querySelector('#skills-list');
  const skillsAddBtn = root.querySelector('#skills-add');
  const skillsEmpty = root.querySelector('#skills-empty');
  const skillsForm = root.querySelector('#skills-form');
  const skillsEditorTitle = root.querySelector('#skills-editor-title');
  const skillsEditorMeta = root.querySelector('#skills-editor-meta');
  const skillsNameField = root.querySelector('#skills-name-field');
  const skillNameInput = root.querySelector('#skill-name');
  const skillSourceSelect = root.querySelector('#skill-source');
  const skillBody = root.querySelector('#skill-body');
  const skillsSave = root.querySelector('#skills-save');
  let skillsPayload = null;
  let selectedSkillKey = '';
  let skillsCreateMode = false;
  let skillDraftTemplateName = '';

  function openSkillsModal() {
    skillsModal.hidden = false;
    loadSkillsModal();
    skillsModal.addEventListener('click', onSkillsModalBackdropClick);
  }

  function closeSkillsModal() {
    skillsModal.hidden = true;
    skillsModal.removeEventListener('click', onSkillsModalBackdropClick);
  }

  function onSkillsModalBackdropClick(event) {
    if (event.target === skillsModal) closeSkillsModal();
  }

  async function loadSkillsModal() {
    skillsList.innerHTML = '<p class="providers-empty">Chargement...</p>';
    skillsForm.hidden = true;
    skillsEmpty.hidden = false;
    if (!client.skills) return;
    const payload = await client.skills().catch(() => null);
    if (!payload || !payload.ok) {
      skillsList.innerHTML = '<p class="providers-empty">Skills indisponibles.</p>';
      return;
    }
    skillsPayload = payload;
    renderSkillsModal();
  }

  function renderSkillsModal() {
    const skills = skillsPayload && Array.isArray(skillsPayload.skills) ? skillsPayload.skills : [];
    if (!skills.length) {
      skillsList.innerHTML = '<p class="providers-empty">Aucun skill trouvé.</p>';
      if (!skillsCreateMode) selectSkill(null);
      return;
    }
    const selectedStillExists = skills.some((skill) => skillKey(skill) === selectedSkillKey);
    if (!selectedStillExists) {
      const firstActive = skills.find((skill) => skill.active) || skills.find((skill) => skill.effective) || skills[0];
      selectedSkillKey = skillKey(firstActive);
    }
    skillsList.textContent = '';
    for (const skill of skills) {
      const row = document.createElement('div');
      row.className = `skill-row${skillKey(skill) === selectedSkillKey ? ' selected' : ''}${skill.shadowed ? ' shadowed' : ''}`;
      row.dataset.key = skillKey(skill);
      const commands = Array.isArray(skill.commands) && skill.commands.length ? ` · ${skill.commands.join(' ')}` : '';
      const source = skill.source === 'local' ? 'local' : 'global';
      const status = skill.shadowed ? 'masqué' : (skill.enabled ? 'actif' : 'inactif');
      row.innerHTML = `
        <div class="skill-row-name">${escapeHtml(skill.name)}</div>
        <span class="skill-row-status ${skill.shadowed ? 'shadowed' : (skill.enabled ? 'active' : 'inactive')}">${status}</span>
        <button class="skill-toggle ${skill.enabled ? 'active' : ''}" type="button" role="switch" aria-checked="${skill.enabled ? 'true' : 'false'}" ${skill.shadowed ? 'disabled' : ''} aria-label="${skill.enabled ? 'Désactiver' : 'Activer'} ${escapeHtml(skill.name)}"></button>
        <div class="skill-row-meta">${escapeHtml(source)} · ${escapeHtml(skill.activation || 'on-demand')}${escapeHtml(commands)}</div>
      `;
      row.addEventListener('click', () => selectSkill(skill));
      row.querySelector('.skill-toggle').addEventListener('click', async (event) => {
        event.stopPropagation();
        await toggleSkill(skill);
      });
      skillsList.appendChild(row);
    }
    selectSkill(skills.find((skill) => skillKey(skill) === selectedSkillKey) || skills[0]);
  }

  function selectSkill(skill) {
    skillsCreateMode = false;
    skillsNameField.hidden = true;
    if (!skill) {
      selectedSkillKey = '';
      skillsForm.hidden = true;
      skillsEmpty.hidden = false;
      return;
    }
    selectedSkillKey = skillKey(skill);
    skillsForm.hidden = false;
    skillsEmpty.hidden = true;
    skillsEditorTitle.textContent = skill.name;
    const source = skill.source === 'local' ? 'local' : 'global';
    const state = skill.shadowed ? 'masqué par un skill local' : (skill.enabled ? 'actif' : 'inactif');
    skillsEditorMeta.textContent = `${source} · ${state} · ${skill.path || ''}`;
    skillBody.value = skill.body || '';
    for (const row of skillsList.querySelectorAll('.skill-row')) {
      row.classList.toggle('selected', row.dataset.key === selectedSkillKey);
    }
  }

  function showSkillCreateForm() {
    skillsCreateMode = true;
    selectedSkillKey = '';
    skillsForm.hidden = false;
    skillsEmpty.hidden = true;
    skillsNameField.hidden = false;
    skillsEditorTitle.textContent = 'Nouveau skill';
    skillsEditorMeta.textContent = 'Nom en kebab-case, puis SKILL.md.';
    skillNameInput.value = '';
    skillSourceSelect.value = 'global';
    skillBody.value = skillTemplate('');
    skillDraftTemplateName = '';
    for (const row of skillsList.querySelectorAll('.skill-row')) row.classList.remove('selected');
    skillNameInput.focus();
  }

  function skillTemplate(name) {
    const safeName = name || 'mon-skill';
    const title = safeName.replace(/[-_]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
    return `---\nname: ${safeName}\ndescription: ""\nactivation: on-demand\n---\n\n# ${title}\n\n## Résumé\n\nDécrire en une phrase ce que ce skill ajoute à BB9.\n\n## Activation\n\non-demand\n\n## Commandes\n\n- \`/${safeName}\` : lancer ce skill.\n\n## Méthode\n\nDécrire ici la méthode, les règles ou le comportement attendu.\n`;
  }

  async function toggleSkill(skill) {
    if (!client.toggleSkill || !skill || skill.shadowed) return;
    elements.status.textContent = skill.enabled ? 'Désactivation du skill' : 'Activation du skill';
    const payload = await client.toggleSkill(skill.name, !skill.enabled).catch(() => null);
    if (payload && payload.ok) {
      skillsPayload = payload;
      renderSkillsModal();
      await loadCommands();
    }
    elements.status.textContent = 'Prêt';
  }

  skillsForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    skillsSave.disabled = true;
    skillsSave.textContent = '…';
    let payload;
    let nextSelectedKey = selectedSkillKey;
    if (skillsCreateMode) {
      if (!client.addSkill) {
        skillsSave.disabled = false;
        skillsSave.textContent = 'Enregistrer';
        return;
      }
      const name = skillNameInput.value.trim();
      const source = skillSourceSelect.value;
      if (!name) {
        skillsSave.disabled = false;
        skillsSave.textContent = 'Enregistrer';
        elements.status.textContent = 'Nom du skill requis';
        skillNameInput.focus();
        return;
      }
      elements.status.textContent = 'Création du skill';
      const body = skillBody.value === skillTemplate('') ? skillTemplate(name) : skillBody.value;
      payload = await client.addSkill({name, source, body}).catch(() => null);
      if (payload && payload.ok) nextSelectedKey = `${source}:${name}`;
    } else {
      if (!client.updateSkill || !skillsPayload) {
        skillsSave.disabled = false;
        skillsSave.textContent = 'Enregistrer';
        return;
      }
      const skill = (skillsPayload.skills || []).find((item) => skillKey(item) === selectedSkillKey);
      if (!skill) {
        skillsSave.disabled = false;
        skillsSave.textContent = 'Enregistrer';
        return;
      }
      elements.status.textContent = 'Enregistrement du skill';
      payload = await client.updateSkill({
        name: skill.name,
        source: skill.source,
        body: skillBody.value,
      }).catch(() => null);
      if (payload && payload.ok) nextSelectedKey = `${skill.source}:${skill.name}`;
    }
    skillsSave.disabled = false;
    skillsSave.textContent = 'Enregistrer';
    if (payload && payload.ok) {
      skillsPayload = payload;
      skillsCreateMode = false;
      selectedSkillKey = nextSelectedKey;
      renderSkillsModal();
      await loadCommands();
      elements.status.textContent = 'Skill enregistré';
      window.setTimeout(() => {
        if (elements.status.textContent === 'Skill enregistré') elements.status.textContent = 'Prêt';
      }, 900);
      return;
    }
    elements.status.textContent = payload && payload.error === 'skill_exists' ? 'Ce skill existe déjà' : 'Erreur skill';
  });

  function skillKey(skill) {
    return `${skill.source || 'global'}:${skill.name || ''}`;
  }

  skillNameInput.addEventListener('input', () => {
    if (!skillsCreateMode) return;
    const nextName = skillNameInput.value.trim();
    if (skillBody.value !== skillTemplate(skillDraftTemplateName)) return;
    skillBody.value = skillTemplate(nextName);
    skillDraftTemplateName = nextName;
  });
  skillsAddBtn.addEventListener('click', showSkillCreateForm);
  skillsModalClose.addEventListener('click', closeSkillsModal);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !skillsModal.hidden) closeSkillsModal();
  });
  // ─────────────────────────────────────────────────────

  // ── Notes & todos modal ──────────────────────────────
  const notesModal = root.querySelector('#notes-modal');
  const notesModalClose = root.querySelector('#notes-modal-close');
  const notesErrorBox = root.querySelector('#notes-error');
  const todoAddForm = root.querySelector('#todo-add-form');
  const todoAddInput = root.querySelector('#todo-add-input');
  const todoListBox = root.querySelector('#todo-list');
  const notesListBox = root.querySelector('#notes-list');
  const noteAddBtn = root.querySelector('#note-add');
  let notesPayload = null;
  let noteEditingSlug = null;

  function openNotesModal() {
    notesModal.hidden = false;
    noteEditingSlug = null;
    setNotesError('');
    loadNotesModal();
    notesModal.addEventListener('click', onNotesModalBackdropClick);
  }

  function closeNotesModal() {
    notesModal.hidden = true;
    notesModal.removeEventListener('click', onNotesModalBackdropClick);
  }

  function onNotesModalBackdropClick(event) {
    if (event.target === notesModal) closeNotesModal();
  }

  function setNotesError(message) {
    notesErrorBox.textContent = message || '';
    notesErrorBox.hidden = !message;
  }

  async function loadNotesModal() {
    todoListBox.innerHTML = '<p class="providers-empty">Chargement...</p>';
    notesListBox.textContent = '';
    const payload = await client.notes().catch(() => null);
    if (!payload || !payload.ok) {
      todoListBox.innerHTML = '<p class="providers-empty">Notes indisponibles.</p>';
      return;
    }
    notesPayload = payload;
    renderNotesModal();
  }

  function renderNotesModal() {
    renderTodoList();
    renderNotesList();
  }

  function applyNotesUpdate(payload, fallbackError) {
    if (payload && payload.ok) {
      notesPayload = payload;
      setNotesError('');
      renderNotesModal();
      return true;
    }
    setNotesError((payload && (payload.message || payload.error)) || fallbackError);
    return false;
  }

  function renderTodoList() {
    const todos = (notesPayload && notesPayload.todos) || [];
    todoListBox.textContent = '';
    if (!todos.length) {
      todoListBox.innerHTML = '<p class="providers-empty">Aucune tâche.</p>';
      return;
    }
    for (const item of todos) {
      const row = document.createElement('div');
      row.className = `todo-row${item.done ? ' done' : ''}`;
      row.innerHTML = `
        <button class="todo-check ${item.done ? 'active' : ''}" type="button" role="checkbox"
          aria-checked="${item.done ? 'true' : 'false'}" aria-label="Cocher ${escapeHtml(item.text)}"></button>
        <span class="todo-text">${escapeHtml(item.text)}</span>
        <div class="todo-actions">
          <button class="agent-action todo-edit" type="button" title="Modifier" aria-label="Modifier la tâche">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="agent-action todo-remove" type="button" title="Supprimer" aria-label="Supprimer la tâche">×</button>
        </div>
      `;
      row.querySelector('.todo-check').addEventListener('click', async () => {
        const payload = await client.updateTodo({op: 'toggle', index: item.index, done: !item.done}).catch(() => null);
        applyNotesUpdate(payload, 'Mise à jour impossible.');
      });
      row.querySelector('.todo-remove').addEventListener('click', async () => {
        const payload = await client.updateTodo({op: 'remove', index: item.index}).catch(() => null);
        applyNotesUpdate(payload, 'Suppression impossible.');
      });
      row.querySelector('.todo-edit').addEventListener('click', () => {
        const next = window.prompt('Modifier la tâche', item.text);
        if (next === null) return;
        const text = next.trim();
        if (!text || text === item.text) return;
        client.updateTodo({op: 'edit', index: item.index, text}).then((payload) => {
          applyNotesUpdate(payload, 'Modification impossible.');
        });
      });
      todoListBox.appendChild(row);
    }
  }

  function renderNotesList() {
    const notes = (notesPayload && notesPayload.notes) || [];
    notesListBox.textContent = '';
    if (noteEditingSlug === '__new__') {
      notesListBox.appendChild(buildNoteEditor({slug: '', title: '', content: ''}, {isNew: true}));
    }
    if (!notes.length && noteEditingSlug !== '__new__') {
      notesListBox.innerHTML = '<p class="providers-empty">Aucune note.</p>';
      return;
    }
    for (const note of notes) {
      if (noteEditingSlug === note.slug) {
        notesListBox.appendChild(buildNoteEditor(note, {isNew: false}));
      } else {
        notesListBox.appendChild(buildNoteRow(note));
      }
    }
  }

  function buildNoteRow(note) {
    const row = document.createElement('div');
    row.className = 'note-row';
    row.innerHTML = `
      <div class="note-row-info">
        <div class="note-row-title">${escapeHtml(note.title || note.slug)}</div>
        <div class="note-row-slug">${escapeHtml(note.slug)}.md</div>
      </div>
      <div class="note-row-actions">
        <button class="agent-action note-edit" type="button" title="Éditer" aria-label="Éditer ${escapeHtml(note.slug)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="agent-action note-delete" type="button" title="Supprimer" aria-label="Supprimer ${escapeHtml(note.slug)}">×</button>
      </div>
    `;
    row.querySelector('.note-edit').addEventListener('click', () => {
      noteEditingSlug = note.slug;
      renderNotesList();
    });
    row.querySelector('.note-delete').addEventListener('click', async () => {
      if (!window.confirm(`Supprimer la note ${note.slug} ?`)) return;
      const payload = await client.updateNote({op: 'delete', slug: note.slug}).catch(() => null);
      applyNotesUpdate(payload, 'Suppression impossible.');
    });
    return row;
  }

  function buildNoteEditor(note, {isNew}) {
    const form = document.createElement('form');
    form.className = 'note-editor';
    form.innerHTML = `
      ${isNew ? `<input type="text" class="providers-input note-editor-slug" placeholder="nom-de-la-note" autocomplete="off" spellcheck="false" aria-label="Nom de la note">` : ''}
      <textarea class="skill-body note-editor-content" spellcheck="false" aria-label="Contenu de la note"></textarea>
      <div class="note-editor-actions">
        <button type="submit" class="providers-submit">Enregistrer</button>
        <button type="button" class="note-editor-cancel">Annuler</button>
      </div>
    `;
    form.querySelector('.note-editor-content').value = note.content || '';
    const cancel = () => {
      noteEditingSlug = null;
      renderNotesList();
    };
    form.querySelector('.note-editor-cancel').addEventListener('click', cancel);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const content = form.querySelector('.note-editor-content').value;
      const slug = isNew ? (form.querySelector('.note-editor-slug').value || '').trim() : note.slug;
      if (!slug) {
        setNotesError('Donne un nom à la note.');
        return;
      }
      const payload = await client.updateNote({op: 'write', slug, content}).catch(() => null);
      if (applyNotesUpdate(payload, 'Enregistrement impossible.')) {
        noteEditingSlug = null;
        renderNotesModal();
      }
    });
    return form;
  }

  todoAddForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = todoAddInput.value.trim();
    if (!text) return;
    const payload = await client.updateTodo({op: 'add', text}).catch(() => null);
    if (applyNotesUpdate(payload, 'Ajout impossible.')) todoAddInput.value = '';
  });

  noteAddBtn.addEventListener('click', () => {
    noteEditingSlug = '__new__';
    renderNotesList();
  });

  notesModalClose.addEventListener('click', closeNotesModal);
  // ─────────────────────────────────────────────────────

  // ── Routines modal ───────────────────────────────────
  const projectsModal = root.querySelector('#projects-modal');
  const projectsModalClose = root.querySelector('#projects-modal-close');
  const projectsAddForm = root.querySelector('#projects-add-form');
  const projectsAddPath = root.querySelector('#projects-add-path');
  const projectsErrorBox = root.querySelector('#projects-error');
  const projectsListBox = root.querySelector('#projects-list');
  let projectsModalPayload = null;
  let projectsEditingPath = '';

  function openProjectsModal() {
    projectsModal.hidden = false;
    projectsEditingPath = '';
    setProjectsError('');
    loadProjectsModal();
    projectsModal.addEventListener('click', onProjectsModalBackdropClick);
  }

  function closeProjectsModal() {
    projectsModal.hidden = true;
    projectsModal.removeEventListener('click', onProjectsModalBackdropClick);
  }

  function onProjectsModalBackdropClick(event) {
    if (event.target === projectsModal) closeProjectsModal();
  }

  function setProjectsError(message) {
    projectsErrorBox.textContent = message || '';
    projectsErrorBox.hidden = !message;
  }

  async function loadProjectsModal() {
    projectsListBox.innerHTML = '<p class="providers-empty">Chargement...</p>';
    const payload = await client.projects().catch(() => null);
    if (!payload || !payload.ok) {
      projectsListBox.innerHTML = '<p class="providers-empty">Projets indisponibles.</p>';
      return;
    }
    projectsModalPayload = payload;
    renderProjectsModal();
  }

  function renderProjectsModal() {
    const projects = ((projectsModalPayload && projectsModalPayload.projects) || []).filter((project) => project.path);
    projectsListBox.textContent = '';
    if (!projects.length) {
      projectsListBox.innerHTML = '<p class="providers-empty">Aucun projet. Ajoute un chemin ci-dessus.</p>';
      return;
    }
    for (const project of projects) projectsListBox.appendChild(buildProjectRow(project));
  }

  function buildProjectRow(project) {
    const row = document.createElement('div');
    row.className = 'project-row';
    const name = project.label || (project.path || '').split('/').filter(Boolean).pop() || project.path;
    const editing = projectsEditingPath === project.path;
    row.innerHTML = `
      <div class="project-row-info">
        <div class="project-row-name">${escapeHtml(name)}${project.active ? ' <span class="project-row-active">actif</span>' : ''}</div>
        <div class="project-row-path">${escapeHtml(project.path || '')}</div>
      </div>
      <div class="project-row-actions">
        <button class="skill-toggle project-switch ${project.active ? 'active' : ''}" type="button" role="switch"
          aria-checked="${project.active ? 'true' : 'false'}" title="Activer ce projet"
          aria-label="Activer ${escapeHtml(name)}"></button>
        <button class="agent-action project-edit" type="button" title="Modifier le chemin" aria-label="Modifier le chemin de ${escapeHtml(name)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="agent-action project-delete" type="button" title="Retirer de la liste"
          aria-label="Retirer ${escapeHtml(name)}" ${project.active ? 'disabled' : ''}>×</button>
      </div>
    `;
    row.querySelector('.project-switch').addEventListener('click', async () => {
      if (project.active) return;
      const switched = await switchToChannel(project.path, {kind: 'project', path: project.path});
      if (switched) await loadProjectsModal();
    });
    row.querySelector('.project-edit').addEventListener('click', () => {
      projectsEditingPath = editing ? '' : project.path;
      renderProjectsModal();
    });
    row.querySelector('.project-delete').addEventListener('click', async () => {
      if (project.active) return;
      if (!window.confirm(`Retirer ${name} de la liste des projets ?`)) return;
      const payload = await client.updateProjects({op: 'delete', path: project.path}).catch(() => null);
      applyProjectsUpdate(payload, `Suppression impossible.`);
    });
    if (editing) {
      const editorForm = document.createElement('form');
      editorForm.className = 'project-row-editor';
      editorForm.innerHTML = `
        <input type="text" class="providers-input" value="${escapeHtml(project.path || '')}" spellcheck="false" aria-label="Nouveau chemin">
        <button type="submit" class="providers-submit">Valider</button>
      `;
      editorForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const newPath = editorForm.querySelector('input').value.trim();
        if (!newPath || newPath === project.path) {
          projectsEditingPath = '';
          renderProjectsModal();
          return;
        }
        const payload = await client.updateProjects({op: 'edit', path: project.path, new_path: newPath}).catch(() => null);
        projectsEditingPath = '';
        applyProjectsUpdate(payload, `Chemin invalide: ${newPath}`);
      });
      row.appendChild(editorForm);
    }
    return row;
  }

  function applyProjectsUpdate(payload, fallbackError) {
    if (payload && payload.ok) {
      projectsModalPayload = payload;
      setProjectsError('');
      renderProjectsModal();
      loadProjects();
      return;
    }
    setProjectsError((payload && (payload.message || payload.error)) || fallbackError);
  }

  projectsAddForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const path = projectsAddPath.value.trim();
    if (!path) return;
    const payload = await client.updateProjects({op: 'add', path}).catch(() => null);
    if (payload && payload.ok) projectsAddPath.value = '';
    applyProjectsUpdate(payload, `Dossier introuvable: ${path}`);
  });

  projectsModalClose.addEventListener('click', closeProjectsModal);

  const routinesModal = root.querySelector('#routines-modal');
  const routinesModalClose = root.querySelector('#routines-modal-close');
  const routinesList = root.querySelector('#routines-list');
  const routinesAddBtn = root.querySelector('#routines-add');
  const routinesEmpty = root.querySelector('#routines-empty');
  const routinesForm = root.querySelector('#routines-form');
  const routinesEditorTitle = root.querySelector('#routines-editor-title');
  const routinesEditorMeta = root.querySelector('#routines-editor-meta');
  const routinesNameField = root.querySelector('#routines-name-field');
  const routineNameInput = root.querySelector('#routine-name');
  const routinePrompt = root.querySelector('#routine-prompt');
  const routineActivation = root.querySelector('#routine-activation');
  const routineActivationLabel = root.querySelector('#routine-activation-label');
  const routineAgent = root.querySelector('#routine-agent');
  const routineRecurrence = root.querySelector('#routine-recurrence');
  const routineInterval = root.querySelector('#routine-interval');
  const routineTime = root.querySelector('#routine-time');
  const routineMonth = root.querySelector('#routine-month');
  const routineDay = root.querySelector('#routine-day');
  const routineTimezone = root.querySelector('#routine-timezone');
  const routineWeekdays = root.querySelector('#routine-weekdays');
  const routineIntervalField = root.querySelector('.routine-interval-field');
  const routineTimeField = root.querySelector('.routine-time-field');
  const routineMonthField = root.querySelector('.routine-month-field');
  const routineDayField = root.querySelector('.routine-day-field');
  const routineBody = root.querySelector('#routine-body');
  const routinesSave = root.querySelector('#routines-save');
  let routinesPayload = null;
  let routineAgentsPayload = null;
  let selectedRoutineName = '';
  let routinesCreateMode = false;

  const routineTimezoneFallbacks = [
    'Europe/Paris',
    'UTC',
    'Europe/London',
    'Europe/Berlin',
    'Europe/Madrid',
    'Europe/Rome',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
    'America/Sao_Paulo',
    'Africa/Casablanca',
    'Africa/Abidjan',
    'Asia/Dubai',
    'Asia/Tokyo',
    'Asia/Shanghai',
    'Australia/Sydney',
  ];

  function openRoutinesModal() {
    routinesModal.hidden = false;
    loadRoutinesModal();
    routinesModal.addEventListener('click', onRoutinesModalBackdropClick);
  }

  function closeRoutinesModal() {
    routinesModal.hidden = true;
    routinesModal.removeEventListener('click', onRoutinesModalBackdropClick);
  }

  function onRoutinesModalBackdropClick(event) {
    if (event.target === routinesModal) closeRoutinesModal();
  }

  async function loadRoutinesModal() {
    routinesList.innerHTML = '<p class="providers-empty">Chargement...</p>';
    routinesForm.hidden = true;
    routinesEmpty.hidden = false;
    if (!client.routines) return;
    const [payload, agentsPayload] = await Promise.all([
      client.routines().catch(() => null),
      client.agents ? client.agents().catch(() => null) : Promise.resolve(null),
    ]);
    if (!payload || !payload.ok) {
      routinesList.innerHTML = '<p class="providers-empty">Routines indisponibles.</p>';
      return;
    }
    routinesPayload = payload;
    routineAgentsPayload = agentsPayload && agentsPayload.ok ? agentsPayload : null;
    renderRoutinesModal();
  }

  function renderRoutinesModal() {
    const routines = routinesPayload && Array.isArray(routinesPayload.routines) ? routinesPayload.routines : [];
    if (!routines.length) {
      routinesList.innerHTML = '<p class="providers-empty">Aucune routine trouvée.</p>';
      if (!routinesCreateMode) selectRoutine(null);
      return;
    }
    if (!routinesCreateMode && !routines.some((routine) => routine.name === selectedRoutineName)) {
      selectedRoutineName = routines[0].name;
    }
    routinesList.textContent = '';
    for (const routine of routines) {
      const row = document.createElement('div');
      row.className = `skill-row routine-row${!routinesCreateMode && routine.name === selectedRoutineName ? ' selected' : ''}`;
      row.dataset.name = routine.name;
      const statusClass = routine.error ? 'inactive' : (routine.due ? 'active' : (routine.activation === 'active' ? 'active' : 'inactive'));
      const status = routine.error ? 'erreur' : (routine.due ? 'due' : (routine.activation || 'paused'));
      const meta = [
        routine.mode || 'mode ?',
        routine.schedule || '-',
        routine.next_run ? `next ${routine.next_run}` : '',
      ].filter(Boolean).join(' · ');
      row.innerHTML = `
        <div class="skill-row-name">${escapeHtml(routine.name)}</div>
        <span class="skill-row-status ${statusClass}">${escapeHtml(status)}</span>
        <div class="skill-row-meta">${escapeHtml(meta)}</div>
      `;
      row.addEventListener('click', () => selectRoutine(routine));
      routinesList.appendChild(row);
    }
    if (!routinesCreateMode) {
      selectRoutine(routines.find((routine) => routine.name === selectedRoutineName) || routines[0]);
    }
  }

  function selectRoutine(routine) {
    routinesCreateMode = false;
    routinesNameField.hidden = true;
    if (!routine) {
      selectedRoutineName = '';
      routinesForm.hidden = true;
      routinesEmpty.hidden = false;
      return;
    }
    selectedRoutineName = routine.name;
    routinesForm.hidden = false;
    routinesEmpty.hidden = true;
    routinesEditorTitle.textContent = routine.name;
    routinesEditorMeta.textContent = routineMetaLine(routine);
    setRoutineForm(parseRoutineMarkdown(routine.body || ''));
    for (const row of routinesList.querySelectorAll('.routine-row')) {
      row.classList.toggle('selected', row.dataset.name === selectedRoutineName);
    }
  }

  function routineMetaLine(routine) {
    const state = routine.state || {};
    const parts = [
      routine.path || '',
      routine.due ? 'due maintenant' : '',
      routine.next_run ? `next ${routine.next_run}` : '',
      state.last_run ? `last ${state.last_run}` : '',
      state.last_error ? `erreur ${state.last_error}` : '',
    ].filter(Boolean);
    return parts.join(' · ');
  }

  function showRoutineCreateForm() {
    routinesCreateMode = true;
    selectedRoutineName = '';
    routinesForm.hidden = false;
    routinesEmpty.hidden = true;
    routinesNameField.hidden = false;
    routinesEditorTitle.textContent = 'Nouvelle routine';
    routinesEditorMeta.textContent = 'Archive CRON.md globale utilisateur.';
    routineNameInput.value = '';
    setRoutineForm(defaultRoutineModel());
    for (const row of routinesList.querySelectorAll('.routine-row')) row.classList.remove('selected');
    routineNameInput.focus();
  }

  function routineTemplate(model = defaultRoutineModel()) {
    const prompt = model.prompt.trim() || 'Décrire le prompt à déclencher.';
    const summary = firstPromptLine(prompt);
    const schedule = routineScheduleMarkdown(model);
    return `# CRON.md

## Résumé

${summary}

## Activation

${model.activation || 'paused'}

## Agent

${model.agent || 'default'}

## Mode

recurring

## Schedule

${schedule}

## Intention

${prompt}

## Limites

- Ne pas modifier de fichiers sans demande explicite.

## Retry

Attempts: 0

## Notification

Mode: errors
Channel: local

## History

Mode: summary
Limit: 20
`;
  }

  function defaultRoutineModel() {
    return {
      prompt: '',
      activation: 'paused',
      agent: 'default',
      recurrence: 'daily',
      interval: '1',
      time: '08:30',
      weekdays: ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'],
      day: '1',
      month: '1',
      timezone: 'Europe/Paris',
    };
  }

  function parseRoutineMarkdown(markdown) {
    const schedule = markdownSection(markdown, 'Schedule');
    const frequency = markdownField(schedule, 'Frequency', 'Frequence', 'Fréquence').toLowerCase();
    const days = parseRoutineDays(markdownField(schedule, 'Days', 'Jours'));
    let recurrence = 'daily';
    if (['minute', 'minutes', 'minutely', 'min', 'mins'].includes(normalizeText(frequency))) recurrence = 'minutely';
    else if (['hour', 'hours', 'hourly', 'heure', 'heures'].includes(normalizeText(frequency))) recurrence = 'hourly';
    else if (['year', 'yearly', 'annual', 'annuel', 'annee', 'an'].includes(normalizeText(frequency))) recurrence = 'yearly';
    else if (['month', 'monthly', 'mensuel', 'mois'].includes(normalizeText(frequency))) recurrence = 'monthly';
    else if (['week', 'weekly', 'hebdo', 'hebdomadaire', 'semaine', 'semaines'].includes(normalizeText(frequency)) || (days.length && days.length < 7)) recurrence = 'weekly';
    const prompt = markdownSection(markdown, 'Intention') || markdownSection(markdown, 'Résumé', 'Resume');
    return {
      prompt: prompt.trim(),
      activation: firstSectionLine(markdown, 'Activation') || 'paused',
      agent: firstSectionLine(markdown, 'Agent') || 'default',
      recurrence,
      interval: markdownField(schedule, 'Every', 'Interval', 'EveryMinutes', 'IntervalMinutes', 'ToutesLes') || '1',
      time: markdownField(schedule, 'Time', 'Heure') || '08:30',
      weekdays: days.length ? days : ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'],
      day: markdownField(schedule, 'Day', 'DayOfMonth', 'Jour', 'JourDuMois') || '1',
      month: markdownField(schedule, 'Month', 'Mois') || '1',
      timezone: markdownField(schedule, 'Timezone', 'Fuseau', 'Fuseau horaire') || 'Europe/Paris',
    };
  }

  function setRoutineForm(model) {
    routinePrompt.value = model.prompt || '';
    setRoutineActivation(model.activation === 'active');
    setSelectOptions(routineAgent, routineAgentOptions(), model.agent || 'default');
    routineRecurrence.value = model.recurrence || 'daily';
    routineInterval.value = model.interval || '1';
    routineTime.value = model.time || '08:30';
    routineDay.value = model.day || '1';
    routineMonth.value = String(model.month || '1');
    setSelectOptions(routineTimezone, routineTimezoneOptions(), model.timezone || 'Europe/Paris');
    const selectedDays = new Set(model.weekdays || []);
    for (const input of routineWeekdays.querySelectorAll('input[type="checkbox"]')) {
      input.checked = selectedDays.has(input.value);
    }
    updateRoutineRecurrenceFields();
    updateRoutineMarkdown();
  }

  function currentRoutineModel() {
    const weekdays = Array.from(routineWeekdays.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value);
    return {
      prompt: routinePrompt.value,
      activation: routineActivationValue(),
      agent: routineAgent.value || 'default',
      recurrence: routineRecurrence.value,
      interval: String(Math.max(1, Number.parseInt(routineInterval.value || '1', 10) || 1)),
      time: routineTime.value || '08:30',
      weekdays: weekdays.length ? weekdays : ['monday'],
      day: String(Math.min(31, Math.max(1, Number.parseInt(routineDay.value || '1', 10) || 1))),
      month: String(Math.min(12, Math.max(1, Number.parseInt(routineMonth.value || '1', 10) || 1))),
      timezone: routineTimezone.value || 'Europe/Paris',
    };
  }

  function setRoutineActivation(active) {
    routineActivation.classList.toggle('active', active);
    routineActivation.setAttribute('aria-checked', active ? 'true' : 'false');
    routineActivation.setAttribute('aria-label', active ? 'Désactiver la routine' : 'Activer la routine');
    routineActivationLabel.textContent = active ? 'active' : 'pause';
  }

  function routineActivationValue() {
    return routineActivation.getAttribute('aria-checked') === 'true' ? 'active' : 'paused';
  }

  function routineAgentOptions() {
    const names = ['default'];
    if (routineAgentsPayload && routineAgentsPayload.active_agent) names.push(routineAgentsPayload.active_agent);
    for (const agent of routineAgentsPayload && Array.isArray(routineAgentsPayload.agents) ? routineAgentsPayload.agents : []) {
      if (agent && agent.name) names.push(agent.name);
    }
    return uniqueOptions(names);
  }

  function routineTimezoneOptions() {
    if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
      try {
        return uniqueOptions(['Europe/Paris', 'UTC', ...Intl.supportedValuesOf('timeZone')]);
      } catch (_) {
        // Browser support varies; fall back to the compact list below.
      }
    }
    return uniqueOptions(routineTimezoneFallbacks);
  }

  function setSelectOptions(select, values, selectedValue) {
    const selected = selectedValue || '';
    const options = uniqueOptions(selected ? [...values, selected] : values);
    select.textContent = '';
    for (const value of options) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    }
    select.value = selected;
  }

  function uniqueOptions(values) {
    const seen = new Set();
    const options = [];
    for (const value of values) {
      const text = String(value || '').trim();
      if (!text || seen.has(text)) continue;
      seen.add(text);
      options.push(text);
    }
    return options;
  }

  function updateRoutineMarkdown() {
    routineBody.value = routineTemplate(currentRoutineModel());
  }

  function updateRoutineRecurrenceFields() {
    const recurrence = routineRecurrence.value;
    const intervalMode = ['hourly', 'minutely'].includes(recurrence);
    routineIntervalField.hidden = !intervalMode;
    routineTimeField.hidden = intervalMode;
    routineWeekdays.hidden = recurrence !== 'weekly';
    routineDayField.hidden = !['monthly', 'yearly'].includes(recurrence);
    routineMonthField.hidden = recurrence !== 'yearly';
  }

  function routineScheduleMarkdown(model) {
    const lines = [
      `Frequency: ${model.recurrence || 'daily'}`,
    ];
    if (['hourly', 'minutely'].includes(model.recurrence)) {
      lines.push(`Every: ${model.interval || '1'}`);
    } else {
      lines.push(`Time: ${model.time || '08:30'}`);
    }
    if (model.recurrence === 'weekly') {
      lines.push(`Days: ${(model.weekdays && model.weekdays.length ? model.weekdays : ['monday']).join(', ')}`);
    } else if (model.recurrence === 'monthly') {
      lines.push(`Day: ${model.day || '1'}`);
    } else if (model.recurrence === 'yearly') {
      lines.push(`Month: ${model.month || '1'}`);
      lines.push(`Day: ${model.day || '1'}`);
    }
    lines.push(`Timezone: ${model.timezone || 'Europe/Paris'}`);
    return lines.join('\n');
  }

  function firstPromptLine(prompt) {
    return prompt.split('\n').map((line) => line.trim()).find(Boolean) || 'Routine planifiée.';
  }

  function markdownSection(markdown, ...headings) {
    const lines = String(markdown || '').split(/\r?\n/);
    const wanted = new Set(headings.map(normalizeText));
    let collecting = false;
    const collected = [];
    for (const line of lines) {
      const match = /^#{2,6}\s+(.+?)\s*$/.exec(line);
      if (match) {
        if (collecting) break;
        collecting = wanted.has(normalizeText(match[1]));
        continue;
      }
      if (collecting) collected.push(line);
    }
    return collected.join('\n').trim();
  }

  function firstSectionLine(markdown, ...headings) {
    return markdownSection(markdown, ...headings).split(/\r?\n/).map((line) => line.trim()).find(Boolean) || '';
  }

  function markdownField(markdown, ...labels) {
    const wanted = new Set(labels.map(normalizeText));
    for (const line of String(markdown || '').split(/\r?\n/)) {
      const [key, ...rest] = line.split(':');
      if (!rest.length) continue;
      if (wanted.has(normalizeText(key))) return rest.join(':').trim();
    }
    return '';
  }

  function parseRoutineDays(value) {
    const normalized = normalizeText(value.replace(/,/g, ' '));
    if (!normalized) return [];
    if (normalized.includes('daily')) return ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
    if (normalized.includes('weekdays')) return ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'];
    if (normalized.includes('weekend')) return ['saturday', 'sunday'];
    const allowed = new Set(['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']);
    return normalized.split(/\s+/).filter((day) => allowed.has(day));
  }

  function normalizeText(text) {
    return String(text || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[-_]/g, ' ').trim();
  }

  routinesForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    routinesSave.disabled = true;
    routinesSave.textContent = '…';
    let payload;
    if (routinesCreateMode) {
      const name = routineNameInput.value.trim();
      if (!name) {
        routinesSave.disabled = false;
        routinesSave.textContent = 'Enregistrer';
        elements.status.textContent = 'Nom de routine requis';
        routineNameInput.focus();
        return;
      }
      elements.status.textContent = 'Création de la routine';
      payload = await client.addRoutine({name, body: routineBody.value}).catch(() => null);
      if (payload && payload.ok) selectedRoutineName = name;
    } else {
      const routine = (routinesPayload.routines || []).find((item) => item.name === selectedRoutineName);
      if (!routine) {
        routinesSave.disabled = false;
        routinesSave.textContent = 'Enregistrer';
        return;
      }
      elements.status.textContent = 'Enregistrement de la routine';
      payload = await client.updateRoutine({name: routine.name, body: routineBody.value}).catch(() => null);
    }
    routinesSave.disabled = false;
    routinesSave.textContent = 'Enregistrer';
    if (payload && payload.ok) {
      routinesPayload = payload;
      routinesCreateMode = false;
      renderRoutinesModal();
      elements.status.textContent = 'Routine enregistrée';
      window.setTimeout(() => {
        if (elements.status.textContent === 'Routine enregistrée') elements.status.textContent = 'Prêt';
      }, 900);
      return;
    }
    elements.status.textContent = payload && payload.error === 'routine_exists' ? 'Cette routine existe déjà' : 'Erreur routine';
  });

  for (const field of [routinePrompt, routineAgent, routineInterval, routineTime, routineMonth, routineDay, routineTimezone]) {
    field.addEventListener('input', updateRoutineMarkdown);
    field.addEventListener('change', updateRoutineMarkdown);
  }
  routineActivation.addEventListener('click', () => {
    setRoutineActivation(routineActivationValue() !== 'active');
    updateRoutineMarkdown();
  });
  routineRecurrence.addEventListener('change', () => {
    updateRoutineRecurrenceFields();
    updateRoutineMarkdown();
  });
  routineWeekdays.addEventListener('change', updateRoutineMarkdown);
  routinesAddBtn.addEventListener('click', showRoutineCreateForm);
  routinesModalClose.addEventListener('click', closeRoutinesModal);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !routinesModal.hidden) closeRoutinesModal();
  });
  // ─────────────────────────────────────────────────────

  // ── Agents modal ─────────────────────────────────────
  const agentsModal = root.querySelector('#agents-modal');
  const agentsModalClose = root.querySelector('#agents-modal-close');
  const agentsList = root.querySelector('#agents-list');
  const agentsAddBtn = root.querySelector('#agents-add');
  const agentsEmpty = root.querySelector('#agents-empty');
  const agentsForm = root.querySelector('#agents-form');
  const agentsEditorTitle = root.querySelector('#agents-editor-title');
  const agentsTitleEdit = root.querySelector('#agents-title-edit');
  const agentsTitleField = root.querySelector('#agents-title-field');
  const agentsTitleInput = root.querySelector('#agents-title-input');
  const agentsEditorMeta = root.querySelector('#agents-editor-meta');
  const agentsNameField = root.querySelector('#agents-name-field');
  const agentNameInput = root.querySelector('#agent-name');
  const agentIdentity = root.querySelector('#agent-identity');
  const agentSoul = root.querySelector('#agent-soul');
  const agentModelSelect = root.querySelector('#agent-model');
  const agentReasoningSelect = root.querySelector('#agent-reasoning');
  const agentSubagentToggle = root.querySelector('#agent-subagent');
  const agentTelegramToggle = root.querySelector('#agent-telegram-enabled');
  const agentTelegramFields = root.querySelector('#agent-telegram-fields');
  const agentTelegramToken = root.querySelector('#agent-telegram-token');
  const agentTelegramChatIds = root.querySelector('#agent-telegram-chat-ids');
  const agentTelegramChatIdInput = root.querySelector('#agent-telegram-chat-id-input');
  const agentTelegramChatIdChips = root.querySelector('#agent-telegram-chat-id-chips');
  const agentModelRow = agentsForm.querySelector('.agent-model-row');
  const agentSkills = root.querySelector('#agent-skills');
  const agentTools = root.querySelector('#agent-tools');
  const agentSubagents = root.querySelector('#agent-subagents');
  const agentSubagentsGroup = root.querySelector('#agent-subagents-group');
  const agentEditors = agentsForm.querySelector('.agent-editors');
  const agentIdentityField = agentIdentity.closest('.agent-editor-field');
  const agentTabs = root.querySelector('#agent-tabs');
  const agentTabPanels = {
    settings: root.querySelector('#agent-tab-settings'),
    skills: root.querySelector('#agent-tab-skills'),
    tools: root.querySelector('#agent-tab-tools'),
  };
  const agentsSave = root.querySelector('#agents-save');
  let agentsPayload = null;
  let agentProviders = [];
  let selectedAgentName = '';
  let selectedAgentRecord = null;
  let agentsCreateMode = false;
  let agentTelegramChatIdValues = [];

  function setAgentTab(name) {
    const target = agentTabPanels[name] ? name : 'settings';
    for (const [key, panel] of Object.entries(agentTabPanels)) {
      if (panel) panel.hidden = key !== target;
    }
    for (const button of agentTabs.querySelectorAll('.agent-tab')) {
      const active = button.dataset.tab === target;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  }

  agentTabs.addEventListener('click', (event) => {
    const button = event.target.closest('.agent-tab');
    if (button) setAgentTab(button.dataset.tab);
  });

  function openAgentsModal() {
    agentsModal.hidden = false;
    loadAgentsModal();
    agentsModal.addEventListener('click', onAgentsModalBackdropClick);
  }

  function closeAgentsModal() {
    agentsModal.hidden = true;
    agentsModal.removeEventListener('click', onAgentsModalBackdropClick);
  }

  function onAgentsModalBackdropClick(event) {
    if (event.target === agentsModal) closeAgentsModal();
  }

  async function loadAgentsModal() {
    agentsList.innerHTML = '<p class="providers-empty">Chargement...</p>';
    agentsForm.hidden = true;
    agentsEmpty.hidden = false;
    if (!client.agents) return;
    const [payload, modelsPayload] = await Promise.all([
      client.agents().catch(() => null),
      client.models ? client.models().catch(() => null) : Promise.resolve(null),
    ]);
    if (!payload || !payload.ok) {
      agentsList.innerHTML = '<p class="providers-empty">Agents indisponibles.</p>';
      return;
    }
    agentsPayload = payload;
    agentProviders = [];
    if (modelsPayload && modelsPayload.ok) {
      agentProviders = modelsPayload.providers || [];
    }
    renderAgentsModal();
  }

  function renderAgentsModal() {
    const agents = agentsPayload && Array.isArray(agentsPayload.agents) ? agentsPayload.agents : [];
    if (!agents.length) {
      agentsList.innerHTML = '<p class="providers-empty">Aucun agent trouvé.</p>';
      selectAgent(null);
      return;
    }
    if (!agentsCreateMode && !agents.some((agent) => agent.name === selectedAgentName)) {
      const current = agents.find((agent) => agent.current) || agents[0];
      selectedAgentName = current.name;
    }
    agentsList.textContent = '';
    for (const agent of agents) {
      const row = document.createElement('div');
      row.className = `skill-row agent-row${!agentsCreateMode && agent.name === selectedAgentName ? ' selected' : ''}`;
      row.innerHTML = `
        <div class="skill-row-name">${escapeHtml(agent.name)}</div>
        <span class="skill-row-status${agent.current ? ' active' : ' placeholder'}">${agent.current ? 'actif' : ''}</span>
        <button class="agent-action agent-edit" type="button" aria-label="Modifier ${escapeHtml(agent.name)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>
        <button class="agent-action agent-delete" type="button" ${agent.current || agent.name === 'default' ? 'disabled' : ''} aria-label="Supprimer ${escapeHtml(agent.name)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
        <div class="skill-row-meta">${escapeHtml(agent.summary || 'Pas de description.')}</div>
      `;
      row.addEventListener('click', () => selectAgent(agent));
      row.querySelector('.agent-edit').addEventListener('click', (event) => {
        event.stopPropagation();
        selectAgent(agent);
        startAgentTitleEdit();
      });
      row.querySelector('.agent-delete').addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!window.confirm(`Supprimer l'agent ${agent.name} ?`)) return;
        const payload = await client.deleteAgent(agent.name).catch(() => null);
        if (payload && payload.ok) {
          agentsPayload = payload;
          if (selectedAgentName === agent.name) selectedAgentName = '';
          renderAgentsModal();
        }
      });
      agentsList.appendChild(row);
    }
    if (!agentsCreateMode) {
      selectAgent(agents.find((agent) => agent.name === selectedAgentName) || agents[0]);
    }
  }

  function selectAgent(agent) {
    agentsCreateMode = false;
    selectedAgentRecord = agent || null;
    agentsNameField.hidden = true;
    agentTabs.hidden = false;
    agentModelRow.hidden = false;
    agentIdentityField.hidden = false;
    setAgentTitleEditing(false);
    if (!agent) {
      selectedAgentName = '';
      agentsForm.hidden = true;
      agentsEmpty.hidden = false;
      return;
    }
    selectedAgentName = agent.name;
    agentsForm.hidden = false;
    agentsEmpty.hidden = true;
    agentsEditorTitle.textContent = agent.name;
    agentsTitleInput.value = agent.name;
    agentsTitleEdit.hidden = agent.name === 'default';
    agentsEditorMeta.textContent = `${agent.current ? 'agent actif · ' : ''}${agent.subagent ? 'subagent · ' : ''}archives actives par défaut, gérées en listes disabled`;
    agentIdentity.value = agent.identity || '';
    agentSoul.value = agent.soul || '';
    setAgentSubagentToggle(agent.subagent === true);
    setAgentTelegramConfig(agent.telegram || {});
    renderAgentModelOptions(agent.model || '', agent.effective_model || '');
    renderAgentReasoningOptions(agent.reasoning_effort || '', agent.effective_reasoning_effort || '');
    renderAgentArchiveList(agentSkills, 'skill', agentsPayload.skills || [], agent);
    renderAgentArchiveList(agentTools, 'tool', agentsPayload.tools || [], agent);
    agentSubagentsGroup.hidden = agent.subagent === true;
    if (agent.subagent) {
      agentSubagents.textContent = '';
    } else {
      const pool = (agentsPayload.subagents || []).filter((item) => item.name !== agent.name);
      renderAgentArchiveList(agentSubagents, 'subagent', pool, agent);
    }
    for (const row of agentsList.querySelectorAll('.agent-row')) {
      row.classList.toggle('selected', row.querySelector('.skill-row-name').textContent === agent.name);
    }
  }

  function setAgentSubagentToggle(active) {
    agentSubagentToggle.classList.toggle('active', active);
    agentSubagentToggle.setAttribute('aria-checked', active ? 'true' : 'false');
  }

  function setAgentTelegramConfig(config) {
    setAgentTelegramEnabled(Boolean(config && config.enabled));
    agentTelegramToken.value = config && config.token_ref ? config.token_ref : '';
    setAgentTelegramChatIds(telegramChatIdsValues(config));
  }

  function setAgentTelegramEnabled(active) {
    agentTelegramToggle.classList.toggle('active', active);
    agentTelegramToggle.setAttribute('aria-checked', active ? 'true' : 'false');
    agentTelegramFields.hidden = !active;
  }

  function agentTelegramPayload() {
    return {
      enabled: agentTelegramToggle.classList.contains('active'),
      token: agentTelegramToken.value.trim(),
      allowed_chat_ids: agentTelegramChatIds.value.trim(),
    };
  }

  function telegramChatIdsValues(config) {
    if (config && Array.isArray(config.allowed_chat_ids)) return config.allowed_chat_ids;
    if (config && config.allowed_chat_ids_text) return parseArrayChipText(config.allowed_chat_ids_text);
    return [];
  }

  function setAgentTelegramChatIds(values) {
    agentTelegramChatIdValues = uniqueArrayChipValues(values);
    renderAgentTelegramChatIdChips();
  }

  function addAgentTelegramChatId(value) {
    const nextValues = uniqueArrayChipValues(parseArrayChipText(value));
    if (!nextValues.length) return;
    const existing = new Set(agentTelegramChatIdValues);
    for (const next of nextValues) {
      if (existing.has(next)) continue;
      existing.add(next);
      agentTelegramChatIdValues.push(next);
    }
    agentTelegramChatIdInput.value = '';
    renderAgentTelegramChatIdChips();
  }

  function removeAgentTelegramChatId(value) {
    agentTelegramChatIdValues = agentTelegramChatIdValues.filter((item) => item !== value);
    renderAgentTelegramChatIdChips();
  }

  function renderAgentTelegramChatIdChips() {
    agentTelegramChatIds.value = JSON.stringify(agentTelegramChatIdValues.map(arrayChipWireValue));
    agentTelegramChatIdChips.textContent = '';
    if (!agentTelegramChatIdValues.length) {
      const empty = document.createElement('span');
      empty.className = 'array-chip-empty';
      empty.textContent = 'Aucun chat ID autorisé';
      agentTelegramChatIdChips.appendChild(empty);
      return;
    }
    for (const value of agentTelegramChatIdValues) {
      const chip = document.createElement('span');
      chip.className = 'array-chip';
      const label = document.createElement('span');
      label.textContent = value;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'array-chip-remove';
      remove.setAttribute('aria-label', `Supprimer ${value}`);
      remove.textContent = '×';
      remove.addEventListener('click', () => removeAgentTelegramChatId(value));
      chip.append(label, remove);
      agentTelegramChatIdChips.appendChild(chip);
    }
  }

  function parseArrayChipText(value) {
    const text = String(value || '').trim();
    if (!text) return [];
    if (text.startsWith('[')) {
      try {
        const parsed = JSON.parse(text);
        if (Array.isArray(parsed)) return parsed;
      } catch (error) {
        return [text];
      }
    }
    return text.split(/[\s,;]+/).filter(Boolean);
  }

  function uniqueArrayChipValues(values) {
    const seen = new Set();
    const result = [];
    for (const value of Array.isArray(values) ? values : []) {
      const normalized = normalizeArrayChipValue(value);
      if (!normalized || seen.has(normalized)) continue;
      seen.add(normalized);
      result.push(normalized);
    }
    return result;
  }

  function normalizeArrayChipValue(value) {
    return String(value || '').trim();
  }

  function arrayChipWireValue(value) {
    const number = Number(value);
    return /^-?\d+$/.test(value) && Number.isSafeInteger(number) ? number : value;
  }

  function setAgentTitleEditing(editing) {
    agentsTitleField.hidden = !editing;
    agentsEditorTitle.hidden = editing;
    agentsTitleEdit.classList.toggle('active', editing);
    if (!editing && selectedAgentRecord) {
      agentsTitleInput.value = selectedAgentRecord.name;
    }
  }

  function startAgentTitleEdit() {
    if (agentsCreateMode || !selectedAgentRecord || selectedAgentRecord.name === 'default') return;
    setAgentTitleEditing(true);
    agentsTitleInput.focus();
    agentsTitleInput.select();
  }

  function renderAgentModelOptions(currentModel, effectiveModel = '') {
    agentModelSelect.textContent = '';
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = effectiveModel ? `hérité : ${effectiveModel}` : 'hérité du provider actif';
    agentModelSelect.appendChild(empty);
    const allModels = agentProviders.flatMap((p) => p.models && p.models.length ? p.models : [p.model].filter(Boolean));
    if (currentModel && !allModels.includes(currentModel)) {
      const option = document.createElement('option');
      option.value = currentModel;
      option.textContent = currentModel;
      agentModelSelect.appendChild(option);
    }
    for (const provider of agentProviders) {
      const models = provider.models && provider.models.length ? provider.models : [provider.model].filter(Boolean);
      if (!models.length) continue;
      const group = document.createElement('optgroup');
      group.label = provider.name || provider.provider || 'Provider';
      for (const model of models) {
        const option = document.createElement('option');
        option.value = model;
        option.textContent = model;
        group.appendChild(option);
      }
      agentModelSelect.appendChild(group);
    }
    agentModelSelect.value = currentModel || '';
  }

  function renderAgentReasoningOptions(currentReasoning, effectiveReasoning = '') {
    agentReasoningSelect.textContent = '';
    const inherited = document.createElement('option');
    inherited.value = '';
    inherited.textContent = effectiveReasoning ? `hérité : ${effectiveReasoning}` : 'hérité';
    agentReasoningSelect.appendChild(inherited);
    const values = ['none', 'low', 'medium', 'high', 'xhigh'];
    if (currentReasoning && !values.includes(currentReasoning)) values.unshift(currentReasoning);
    for (const value of values) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      agentReasoningSelect.appendChild(option);
    }
    agentReasoningSelect.value = currentReasoning || '';
  }

  function selectedAgentIsSubagent(agent) {
    if (selectedAgentRecord && agent && agent.name === selectedAgentRecord.name) {
      return agentSubagentToggle.classList.contains('active');
    }
    return agent && agent.subagent === true;
  }

  function renderAgentArchiveList(container, kind, archives, agent) {
    const disabledByKind = {
      skill: agent.disabled_skills || [],
      tool: agent.disabled_tools || [],
      subagent: agent.disabled_subagents || [],
    };
    const disabled = new Set(disabledByKind[kind] || []);
    const subagentMode = selectedAgentIsSubagent(agent);
    container.textContent = '';
    if (!archives.length) {
      container.innerHTML = '<p class="providers-empty">Aucune archive.</p>';
      return;
    }
    for (const archive of archives) {
      const lockedDelegate = kind === 'tool' && archive.name === 'delegate' && subagentMode;
      const enabled = lockedDelegate ? false : !disabled.has(archive.name);
      const row = document.createElement('div');
      row.className = 'agent-archive-row';
      row.innerHTML = `
        <div class="agent-archive-info">
          <div class="skill-row-name">${escapeHtml(archive.name)}</div>
          <div class="skill-row-meta">${escapeHtml(archive.summary || '')}</div>
        </div>
        <button class="skill-toggle ${enabled ? 'active' : ''}" type="button" role="switch" aria-checked="${enabled ? 'true' : 'false'}" ${lockedDelegate ? 'disabled' : ''} aria-label="${enabled ? 'Désactiver' : 'Activer'} ${escapeHtml(archive.name)}"></button>
      `;
      row.querySelector('.skill-toggle').addEventListener('click', async () => {
        if (lockedDelegate) return;
        const payload = await client.toggleAgentArchive({
          agent: agent.name,
          kind,
          name: archive.name,
          enabled: !enabled,
        }).catch(() => null);
        if (payload && payload.ok) {
          agentsPayload = payload;
          renderAgentsModal();
        }
      });
      container.appendChild(row);
      if (kind === 'tool' && enabled && Array.isArray(archive.params) && archive.params.length) {
        container.appendChild(buildToolParamsSection(archive));
      }
    }
  }

  function buildToolParamsSection(archive) {
    const section = document.createElement('div');
    section.className = 'agent-tool-params';
    const inputs = [];
    for (const param of archive.params) {
      const field = document.createElement('div');
      field.className = 'providers-field';
      const inputId = `tool-param-${archive.name}-${param.name}`;
      field.innerHTML = `
        <label class="providers-label" for="${escapeHtml(inputId)}">${escapeHtml(param.name)}</label>
        <input type="password" id="${escapeHtml(inputId)}" class="providers-input" autocomplete="off" spellcheck="false"
          placeholder="${param.set ? 'défini — laisser vide pour conserver' : 'non défini'}">
      `;
      inputs.push({name: param.name, element: field.querySelector('input')});
      section.appendChild(field);
    }
    const actions = document.createElement('div');
    actions.className = 'agent-tool-params-actions';
    actions.innerHTML = `
      <button type="button" class="providers-submit">Enregistrer les paramètres</button>
      <span class="agent-tool-params-status" aria-live="polite"></span>
    `;
    const status = actions.querySelector('.agent-tool-params-status');
    actions.querySelector('button').addEventListener('click', async () => {
      const pending = inputs.filter((item) => item.element.value.trim());
      if (!pending.length) {
        status.textContent = 'Aucune valeur à enregistrer.';
        return;
      }
      status.textContent = 'Enregistrement…';
      let payload = null;
      for (const item of pending) {
        payload = await client.setToolSecret({
          tool: archive.name,
          name: item.name,
          value: item.element.value.trim(),
        }).catch(() => null);
        if (!payload || !payload.ok) {
          status.textContent = `Échec pour ${item.name}.`;
          return;
        }
      }
      if (payload && payload.ok) {
        agentsPayload = payload;
        renderAgentsModal();
      }
    });
    section.appendChild(actions);
    return section;
  }

  function showAgentCreateForm() {
    agentsCreateMode = true;
    selectedAgentName = '';
    selectedAgentRecord = null;
    agentsForm.hidden = false;
    agentsEmpty.hidden = true;
    agentsNameField.hidden = false;
    agentTabs.hidden = true;
    setAgentTab('settings');
    agentModelRow.hidden = true;
    agentIdentityField.hidden = true;
    setAgentTitleEditing(false);
    agentsTitleEdit.hidden = true;
    agentsEditorTitle.textContent = 'Nouvel agent';
    agentsEditorMeta.textContent = "Nom en kebab-case, puis SOUL.md. Le reste s'édite après création.";
    agentNameInput.value = '';
    agentSoul.value = '# Soul\n\nDécris ici le comportement attendu de cet agent : ton, priorités, limites.\n';
    setAgentSubagentToggle(false);
    setAgentTelegramConfig({enabled: false, token_ref: '', allowed_chat_ids_text: '[]'});
    for (const row of agentsList.querySelectorAll('.agent-row')) row.classList.remove('selected');
    agentNameInput.focus();
  }

  agentSubagentToggle.addEventListener('click', () => {
    if (selectedAgentName === 'default') return;
    const active = !agentSubagentToggle.classList.contains('active');
    setAgentSubagentToggle(active);
    if (!selectedAgentRecord || agentsCreateMode) return;
    const liveAgent = {
      ...selectedAgentRecord,
      subagent: active,
      disabled_tools: active
        ? Array.from(new Set([...(selectedAgentRecord.disabled_tools || []), 'delegate']))
        : selectedAgentRecord.disabled_tools || [],
    };
    renderAgentArchiveList(agentTools, 'tool', agentsPayload.tools || [], liveAgent);
    agentSubagentsGroup.hidden = active;
    if (active) {
      agentSubagents.textContent = '';
    } else {
      const pool = (agentsPayload.subagents || []).filter((item) => item.name !== selectedAgentRecord.name);
      renderAgentArchiveList(agentSubagents, 'subagent', pool, liveAgent);
    }
  });

  agentTelegramToggle.addEventListener('click', () => {
    setAgentTelegramEnabled(!agentTelegramToggle.classList.contains('active'));
    if (!agentTelegramFields.hidden) agentTelegramToken.focus();
  });

  agentTelegramChatIdInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addAgentTelegramChatId(agentTelegramChatIdInput.value);
  });

  agentsForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    agentsSave.disabled = true;
    agentsSave.textContent = '…';
    const subagent = agentSubagentToggle.classList.contains('active');
    let payload;
    if (agentsCreateMode) {
      const name = agentNameInput.value.trim();
      payload = name ? await client.addAgent({name, soul: agentSoul.value, subagent}).catch(() => null) : null;
      if (payload && payload.ok) selectedAgentName = name;
    } else {
      const newName = agentsTitleField.hidden ? selectedAgentName : agentsTitleInput.value.trim();
      payload = await client.updateAgent({
        name: selectedAgentName,
        new_name: newName && newName !== selectedAgentName ? newName : '',
        identity: agentIdentity.value,
        soul: agentSoul.value,
        model: agentModelSelect.value,
        reasoning_effort: agentReasoningSelect.value,
        subagent,
        telegram: agentTelegramPayload(),
      }).catch(() => null);
      if (payload && payload.ok && newName) selectedAgentName = newName;
    }
    agentsSave.disabled = false;
    agentsSave.textContent = 'Enregistrer';
    if (payload && payload.ok) {
      agentsPayload = payload;
      agentsCreateMode = false;
      renderAgentsModal();
      elements.status.textContent = 'Agent enregistré';
      window.setTimeout(() => {
        if (elements.status.textContent === 'Agent enregistré') elements.status.textContent = 'Prêt';
      }, 900);
      return;
    }
    elements.status.textContent = payload && payload.error === 'agent_exists' ? 'Cet agent existe déjà' : 'Erreur agent';
  });

  agentsAddBtn.addEventListener('click', showAgentCreateForm);
  agentsTitleEdit.addEventListener('click', startAgentTitleEdit);
  agentsTitleInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    setAgentTitleEditing(false);
  });
  agentsModalClose.addEventListener('click', closeAgentsModal);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || agentsModal.hidden) return;
    if (!agentsTitleField.hidden) {
      setAgentTitleEditing(false);
      return;
    }
    closeAgentsModal();
  });
  // ─────────────────────────────────────────────────────

  function toggleSidebar() {
    const isOpen = elements.app.classList.toggle('sidebar-open');
    elements.sidebarToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  }

  function closeSidebar() {
    elements.app.classList.remove('sidebar-open');
    elements.sidebarToggle.setAttribute('aria-expanded', 'false');
  }

  function toggleGitPanel() {
    elements.gitPanel.hidden = !elements.gitPanel.hidden;
  }

  function toggleGitPanelFullscreen() {
    const fullscreen = !elements.gitPanel.classList.contains('fullscreen');
    elements.gitPanel.classList.toggle('fullscreen', fullscreen);
    elements.gitPanelFullscreen.setAttribute('aria-pressed', fullscreen ? 'true' : 'false');
    elements.gitPanelFullscreen.title = fullscreen ? 'Réduire' : 'Plein écran';
    elements.gitPanelFullscreen.setAttribute('aria-label', fullscreen ? 'Réduire' : 'Plein écran');
  }

  async function prepareGitCommit() {
    if (!client.gitCommitMessage) return;
    elements.gitCommit.disabled = true;
    elements.gitCommitNote.textContent = 'Génération du message...';
    try {
      const payload = await client.gitCommitMessage();
      if (!payload.ok) throw new Error(payload.message || payload.error || 'commit message failed');
      elements.gitCommitMessage.value = payload.message || '';
      elements.gitCommitPreview.hidden = false;
      elements.gitCommitNote.textContent = 'Relis puis confirme le commit.';
      elements.gitCommitMessage.focus();
    } catch (err) {
      elements.gitCommitNote.textContent = `Commit indisponible: ${err}`;
    } finally {
      elements.gitCommit.disabled = false;
    }
  }

  async function commitGitChanges() {
    if (!client.commitGit) return;
    const message = elements.gitCommitMessage.value.trim();
    if (!message) {
      elements.gitCommitNote.textContent = 'Message de commit requis.';
      elements.gitCommitMessage.focus();
      return;
    }
    elements.gitCommitConfirm.disabled = true;
    elements.gitCommitNote.textContent = 'Commit en cours...';
    try {
      const payload = await client.commitGit(message);
      if (!payload.ok) throw new Error(payload.message || payload.error || 'git commit failed');
      resetGitCommitPreview();
      renderGit(payload);
      elements.banner.textContent = '';
      addCommitTrace(payload);
      await loadGit();
    } catch (err) {
      elements.gitCommitNote.textContent = `Commit échoué: ${err}`;
    } finally {
      elements.gitCommitConfirm.disabled = false;
      elements.input.focus();
    }
  }

  function resetGitCommitPreview() {
    elements.gitCommitPreview.hidden = true;
    elements.gitCommitMessage.value = '';
  }

  function addCommitTrace(payload) {
    const commit = payload.commit || '';
    const commitLabel = commit ? `Commit créé : \`${commit}\`` : 'Commit créé.';
    const subject = String(payload.message || '').split('\n').find((line) => line.trim()) || '';
    const content = subject ? `${commitLabel}\n\n${subject}` : commitLabel;
    addMessage('assistant', content, {
      events: [
        {
          type: 'action',
          summary: 'git commit',
          data: {tool: 'git', cmd: 'git commit'},
        },
        {
          type: 'observation',
          summary: subject ? `Commit créé: ${commit}\n${subject}` : `Commit créé: ${commit}`,
          data: {tool: 'git', ok: true},
        },
      ],
    });
  }

  async function loadGitFileDiff(path, node, row) {
    if (!path || !client.gitDiff) {
      node.textContent = 'Diff indisponible.';
      row.dataset.loaded = 'true';
      return;
    }
    try {
      const payload = await client.gitDiff(path);
      if (!payload.ok) throw new Error(payload.message || payload.error || 'git diff failed');
      renderColoredDiff(node, payload.diff || 'Aucun diff textuel disponible.');
    } catch (err) {
      node.textContent = `Diff indisponible: ${err}`;
    } finally {
      row.dataset.loaded = 'true';
    }
  }

  function renderColoredDiff(node, diff) {
    node.textContent = '';
    const lines = String(diff).split('\n');
    const additionsOnly = diffHasAdditionsOnly(lines);
    node.classList.toggle('additions-only', additionsOnly);
    let oldLine = null;
    let newLine = null;
    lines.forEach((line, index) => {
      const hunk = parseHunkHeader(line);
      if (hunk) {
        oldLine = hunk.oldLine;
        newLine = hunk.newLine;
      }
      const row = document.createElement('span');
      row.className = `git-diff-row ${diffLineClass(line)}`;
      const oldNumber = document.createElement('span');
      oldNumber.className = 'git-diff-line-number git-diff-old';
      const newNumber = document.createElement('span');
      newNumber.className = 'git-diff-line-number git-diff-new';
      const code = document.createElement('span');
      code.className = 'git-diff-code';
      code.textContent = line;
      const numbers = diffLineNumbers(line, oldLine, newLine);
      oldNumber.textContent = additionsOnly ? '' : numbers.oldNumber;
      newNumber.textContent = numbers.newNumber;
      row.append(oldNumber, newNumber, code);
      node.appendChild(row);
      if (numbers.advanceOld) oldLine += 1;
      if (numbers.advanceNew) newLine += 1;
    });
  }

  function diffHasAdditionsOnly(lines) {
    return lines.some((line) => line.startsWith('+') && !line.startsWith('+++'))
      && !lines.some((line) => line.startsWith('-') && !line.startsWith('---'));
  }

  function diffLineClass(line) {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('@@')) {
      return 'git-diff-meta';
    }
    if (line.startsWith('+')) return 'git-diff-add';
    if (line.startsWith('-')) return 'git-diff-remove';
    return 'git-diff-context';
  }

  function diffLineNumbers(line, oldLine, newLine) {
    if (oldLine === null || newLine === null || line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++') || line.startsWith('@@')) {
      return {oldNumber: '', newNumber: '', advanceOld: false, advanceNew: false};
    }
    if (line.startsWith('+')) {
      return {oldNumber: '', newNumber: String(newLine), advanceOld: false, advanceNew: true};
    }
    if (line.startsWith('-')) {
      return {oldNumber: String(oldLine), newNumber: '', advanceOld: true, advanceNew: false};
    }
    if (line.startsWith('\\')) {
      return {oldNumber: '', newNumber: '', advanceOld: false, advanceNew: false};
    }
    return {oldNumber: String(oldLine), newNumber: String(newLine), advanceOld: true, advanceNew: true};
  }

  function parseHunkHeader(line) {
    const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (!match) return null;
    return {oldLine: Number(match[1]), newLine: Number(match[2])};
  }

  async function switchGitBranch() {
    const branch = elements.gitBranch.value;
    if (!branch) return;
    const switcher = client.switchGitBranch || client.switchBranch;
    if (!switcher) return;
    elements.status.textContent = 'Changement de branche';
    try {
      const payload = await switcher(branch);
      if (!payload.ok) throw new Error(payload.message || payload.error || 'git switch failed');
      renderGit(payload);
      elements.banner.textContent = '';
      await loadStatus();
    } catch (err) {
      elements.banner.textContent = `Branche Git indisponible: ${err}`;
      await loadGit();
    } finally {
      elements.status.textContent = 'Prêt';
      elements.input.focus();
    }
  }

  async function loadSettings() {
    const payload = await client.settings();
    if (!payload.ok) return;
    renderOptions(elements.profile, payload.profiles || [], payload.profile || '');
    renderOptions(elements.reasoning, payload.reasoning_efforts || [], payload.reasoning_effort || '', (value) => value || 'auto');
    const cachedTheme = localStorage.getItem(themeStoreKey);
    if (payload.theme && (!cachedTheme || cachedTheme === 'system' || payload.theme !== 'system')) setTheme(payload.theme, themeStoreKey, themes);
    await loadModels(payload.provider_id || '', payload.model || '');
  }

  async function loadModels(activeProviderId = '', activeModel = '') {
    if (!client.models) {
      renderOptions(elements.model, activeModel ? [activeModel] : [], activeModel);
      return;
    }
    const payload = await client.models();
    if (!payload.ok) {
      renderOptions(elements.model, activeModel ? [activeModel] : [], activeModel);
      return;
    }
    renderModelOptions(payload.providers || [], activeProviderId || payload.active_provider_id || '', activeModel || payload.model || '');
  }

  async function saveSettings() {
    elements.status.textContent = 'Réglages';
    try {
      const payload = await client.updateSettings({
        profile: elements.profile.value,
        provider_id: selectedModelProviderId(elements.model),
        model: selectedModelName(elements.model),
        reasoning_effort: elements.reasoning.value,
      });
      if (!payload.ok) throw new Error(payload.error || 'settings failed');
      await loadStatus();
      await loadModels(payload.provider_id || '', payload.model || '');
    } catch (err) {
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
      elements.input.focus();
    }
  }

  async function saveTheme(value) {
    setTheme(value, themeStoreKey, themes);
    if (!client.updateSettings) return;
    try {
      await client.updateSettings({theme: document.documentElement.dataset.theme || 'system'});
    } catch (_) {
      // localStorage keeps the surface preference if the API is temporarily unavailable.
    }
  }

  async function checkCompatibility() {
    try {
      const payload = await client.health();
      const features = payload.features || [];
      const missing = REQUIRED_FEATURES.filter((feature) => !features.includes(feature));
      if (missing.length) {
        elements.banner.textContent = `Serveur BB9 web ancien ou incomplet. Relance bb9 web puis ouvre le nouveau port affiché. Manque: ${missing.join(', ')}`;
      } else {
        elements.banner.textContent = '';
      }
    } catch (err) {
      elements.banner.textContent = `Impossible de vérifier le serveur BB9 web: ${err}`;
    }
  }

  async function loadHistory() {
    if (!resolvedCapabilities.canLoadHistory) return;
    try {
      const payload = await client.history();
      if (!payload.ok) throw new Error(payload.error || 'history failed');
      syncCurrentProject(payload);
      pendingApproval = payload.pending_approval || pendingApproval;
      if ('plan' in payload) renderPlan(payload.plan);
      renderMessages(payload.messages || [], pendingApproval);
    } catch (err) {
      elements.banner.textContent = `Historique indisponible: ${err}`;
    }
  }

  async function loadCommands() {
    if (!client.commands) return;
    try {
      const payload = await client.commands();
      if (!payload.ok) return;
      commands = payload.commands || [];
      // Les commandes en collision sont exclues de payload.commands par le
      // backend : on les affiche quand même, marquées en conflit, plutôt que
      // de les faire disparaître silencieusement du menu.
      for (const collision of payload.collisions || []) {
        commands.push({
          name: collision.name,
          description: `conflit: ${(collision.owners || []).join(' · ')}`,
          owner: '',
          source: 'conflit',
          local: false,
          conflict: true,
        });
      }
      renderCommandMenu();
    } catch (_) {
      commands = [];
      renderCommandMenu();
    }
  }

  async function loadProjects() {
    if (!resolvedCapabilities.canLoadHistory || !client.projects) return;
    if (projectInFlight) return;
    projectInFlight = true;
    try {
      const payload = await client.projects();
      if (!payload.ok) return;
      syncCurrentProject(payload);
      elements.project.textContent = '';
      const projects = payload.channels || payload.projects || [];
      const activeChannel = payload.active_channel || payload.active_project || '';
      const unreadChannels = reconcileChannelSeen(projects, activeChannel);
      updateChannelBadge(projects, activeChannel, unreadChannels);
      if (!projects.length) {
        const option = document.createElement('option');
        option.value = payload.active_project || payload.workspace || '';
        option.textContent = 'Projet courant';
        elements.project.appendChild(option);
        return;
      }
      for (const project of projects) {
        const option = document.createElement('option');
        option.value = project.channel_id || project.path;
        option.dataset.kind = project.kind || 'project';
        option.dataset.path = project.path || '';
        option.textContent = projectLabel(project, payload.workspace, unreadChannels.get(option.value) || 0);
        option.selected = option.value === activeChannel;
        elements.project.appendChild(option);
      }
    } finally {
      projectInFlight = false;
    }
  }

  async function loadSessions() {
    if (!resolvedCapabilities.canLoadHistory) return;
    const payload = await client.sessions();
    if (!payload.ok) return;
    syncCurrentProject(payload);
    elements.session.textContent = '';
    const sessions = payload.sessions || [];
    if (!sessions.length) {
      const option = document.createElement('option');
      option.value = payload.active_session_id || '';
      option.textContent = 'Session courante';
      elements.session.appendChild(option);
      return;
    }
    for (const session of sessions) {
      const option = document.createElement('option');
      option.value = session.id;
      option.textContent = `${session.active ? '• ' : ''}${session.title}`;
      option.selected = session.id === payload.active_session_id;
      elements.session.appendChild(option);
    }
  }

  function renderMessages(messages, approval = pendingApproval) {
    const stickToBottom = shouldStickToBottom();
    const scrollTop = elements.main.scrollTop;
    const restoreActivity = running && !approval;
    const traceSnapshot = liveTraceEvents.slice();
    const traceCursor = liveTraceCursor;
    const traceRunId = liveTraceRunId;
    if (restoreActivity) {
      activityNode = null;
      activityTraceNode = null;
    }
    elements.thread.textContent = '';
    const approvalIndex = latestValidationMessageIndex(messages || [], approval);
    for (const [index, message] of (messages || []).entries()) {
      const meta = {artifacts: message.artifacts || []};
      if (index === approvalIndex) {
        if (approval) meta.approval = approval;
        else meta.staleApproval = true;
      }
      addMessage(message.role, message.content, meta, {stickToBottom});
    }
    if (restoreActivity) restoreActivityAfterRender(traceSnapshot, traceCursor, traceRunId);
    if (stickToBottom) scrollToThreadBottom();
    else elements.main.scrollTop = scrollTop;
  }

  function restoreActivityAfterRender(events, cursor, runId) {
    if (!running) return;
    liveTraceEvents = events || [];
    liveTraceCursor = Number(cursor || 0);
    liveTraceRunId = String(runId || '');
    showActivityIndicator({preserveTrace: true});
    startLiveTracePolling();
    elements.status.textContent = 'BB9 travaille';
  }

  async function switchSession() {
    const id = elements.session.value;
    if (!id) return;
    const payload = await client.switchSession(id);
      if (!payload.ok) {
        elements.banner.textContent = `Session indisponible: ${payload.error || 'switch failed'}`;
        return;
      }
      syncCurrentProject(payload);
      if ('plan' in payload) renderPlan(payload.plan);
      renderMessages(payload.messages || []);
    await loadStatus();
    elements.input.focus();
  }

  async function switchProject() {
    const channel = elements.project.value;
    if (!channel) return;
    const selectedOption = elements.project.selectedOptions[0];
    const kind = selectedOption ? selectedOption.dataset.kind || 'project' : 'project';
    const path = selectedOption ? selectedOption.dataset.path || channel : channel;
    await switchToChannel(channel, {kind, path});
  }

  async function switchToChannel(channel, {kind = 'project', path = ''} = {}) {
    if (!channel || !client.switchProject) return false;
    const targetPath = path || channel;
    const previousProjectPath = currentProjectPath;
    if (kind !== 'agent_home') {
      currentProjectPath = targetPath;
      renderPlan({exists: false, project_path: targetPath});
    }
    const payload = await client.switchProject(channel);
    if (!payload.ok) {
      currentProjectPath = previousProjectPath;
      elements.banner.textContent = `Channel indisponible: ${payload.error || 'switch failed'}`;
      await loadStatus();
      return false;
    }
    syncCurrentProject(payload);
    elements.banner.textContent = '';
    if ('plan' in payload) renderPlan(payload.plan);
    renderMessages(payload.messages || []);
    await loadProjects();
    await loadSessions();
    await loadCommands();
    await loadStatus();
    await loadGit();
    elements.input.focus();
    return true;
  }

  async function newSession() {
    const payload = await client.newSession();
    if (!payload.ok) {
      elements.banner.textContent = `Nouvelle session impossible: ${payload.error || 'new session failed'}`;
        return;
      }
      syncCurrentProject(payload);
      if ('plan' in payload) renderPlan(payload.plan);
      renderMessages([]);
    await loadStatus();
    await loadSessions();
    elements.input.focus();
  }

  function syncCurrentProject(payload) {
    if (!payload) return;
    const nextProjectPath = String(payload.active_project || payload.workspace || currentProjectPath || '');
    const changed = Boolean(currentProjectPath && nextProjectPath && nextProjectPath !== currentProjectPath);
    currentProjectPath = nextProjectPath;
    updateHeaderProject();
    return changed;
  }

  function updateHeaderProject() {
    if (!elements.headerProject) return;
    const path = currentProjectPath || '';
    const name = path.split('/').filter(Boolean).pop() || '';
    if (!name) {
      elements.headerProject.hidden = true;
      return;
    }
    elements.headerProjectName.textContent = name;
    elements.headerProject.title = `Projet actif : ${path} — gérer les projets`;
    elements.headerProject.hidden = false;
  }

  function reconcileChannelSeen(channels, activeChannel) {
    const unread = new Map();
    const initialized = channelSeen.__initialized === true;
    let changed = false;
    for (const channel of channels || []) {
      if (!channel || channel.kind !== 'agent_home') continue;
      const id = channel.channel_id || '';
      if (!id) continue;
      const current = channelNotificationCursor(channel);
      if (!initialized || id === activeChannel) {
        if (!sameChannelCursor(channelSeen[id], current)) {
          channelSeen[id] = current;
          changed = true;
        }
        continue;
      }
      const seen = channelSeen[id];
      if (!seen) {
        if (current.message_count > 0 || current.updated_at) unread.set(id, Math.max(1, current.message_count));
        continue;
      }
      if (channelCursorNewer(current, seen)) {
        unread.set(id, Math.max(1, current.message_count - Number(seen.message_count || 0)));
      }
    }
    if (!initialized) {
      channelSeen.__initialized = true;
      changed = true;
    }
    if (changed) writeJsonStore(channelSeenStoreKey, channelSeen);
    return unread;
  }

  function updateChannelBadge(channels, activeChannel, unreadChannels) {
    if (!elements.channelBadge) return;
    const unread = unreadChannels ? unreadChannels.size : 0;
    const unreadNames = [];
    for (const channel of channels || []) {
      if (!channel || channel.kind !== 'agent_home') continue;
      const id = channel.channel_id || '';
      if (!id || id === activeChannel) continue;
      if (unreadChannels && unreadChannels.has(id)) unreadNames.push(projectLabel(channel, '', 0));
    }
    if (unread <= 0) {
      elements.channelBadge.hidden = true;
      elements.channelBadge.textContent = '0';
      elements.channelBadge.title = '';
      return;
    }
    elements.channelBadge.hidden = false;
    elements.channelBadge.textContent = unread > 99 ? '99+' : String(unread);
    const names = unreadNames.slice(0, 3).join(' · ');
    const suffix = unreadNames.length > 3 ? ` · +${unreadNames.length - 3}` : '';
    elements.channelBadge.title = `${unread} channel${unread > 1 ? 's' : ''} avec nouvelle activité${names ? `: ${names}${suffix}` : ''}`;
  }

  function channelNotificationCursor(channel) {
    return {
      message_count: Number(channel.message_count || 0),
      updated_at: String(channel.updated_at || ''),
    };
  }

  function sameChannelCursor(left, right) {
    return Boolean(left)
      && Number(left.message_count || 0) === Number(right.message_count || 0)
      && String(left.updated_at || '') === String(right.updated_at || '');
  }

  function channelCursorNewer(current, seen) {
    return current.message_count > Number(seen.message_count || 0)
      || String(current.updated_at || '') > String(seen.updated_at || '');
  }

  async function reloadProjectViewAfterExternalSwitch(payload = {}) {
    if (projectReloadInFlight) return;
    projectReloadInFlight = true;
    try {
      elements.banner.textContent = '';
      if ('plan' in payload) renderPlan(payload.plan);
      await Promise.allSettled([loadProjects(), loadCommands(), loadGit(), loadSessions(), loadHistory()]);
    } finally {
      projectReloadInFlight = false;
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const message = composerMessage();
    if (!message) return;
    if (pendingApproval) {
      enqueueDraft(message);
      clearComposer();
      elements.status.textContent = 'Validation en attente';
      elements.input.focus();
      return;
    }
    if (running) {
      enqueueDraft(message);
      clearComposer();
      elements.status.textContent = 'Ajouté à la queue';
      elements.input.focus();
      return;
    }
    await sendNow(message);
  }

  async function sendNow(message) {
    const baselineMessageCount = renderedMessageCount();
    resetLiveTrace();
    addMessage('user', message);
    clearComposer();
    setRunning(true);
    elements.status.textContent = 'BB9 travaille';
    activeController = new AbortController();
    startLiveTracePolling();
    try {
      const payload = await client.chat(message, {signal: activeController.signal});
      if (!payload.ok) {
        if (payload.error !== 'run_cancelled') {
          finalizeActivityMessage(payload.message || payload.error || 'Erreur', {});
        } else {
          finalizeActivityMessage(payload.message || 'Run interrompu.', {});
        }
        elements.thread.lastElementChild.classList.add('error');
      } else {
        pendingApproval = payload.approval || null;
        if ('plan' in payload) renderPlan(payload.plan, {openOnChange: true});
        finalizeActivityMessage(payload.answer, {events: payload.events, artifacts: payload.artifacts, approval: payload.approval});
        if (payload.notice) addMessage('notification', payload.notice);
        refreshAfterRun();
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        const recovered = await recoverAfterChatNetworkError(err, message, baselineMessageCount);
        if (!recovered) {
          finalizeActivityMessage(String(err), {});
          elements.thread.lastElementChild.classList.add('error');
        }
      }
    } finally {
      activeController = null;
      setRunning(false);
      elements.status.textContent = pendingApproval ? 'Validation en attente' : 'Prêt';
      const shouldContinue = !stopRequested;
      stopRequested = false;
      elements.input.focus();
      if (shouldContinue && !pendingApproval) runNextDraft();
    }
  }

  function refreshAfterRun() {
    loadStatus();
    loadSessions();
    loadCommands();
    loadGit();
  }

  async function recoverAfterChatNetworkError(_err, message, baselineMessageCount) {
    if (!resolvedCapabilities.canLoadHistory || !client.history) return false;
    elements.status.textContent = 'Connexion interrompue, récupération';
    const deadline = Date.now() + 15 * 60 * 1000;
    while (Date.now() < deadline) {
      try {
        if (client.status) {
          const statusPayload = await client.status();
          if (statusPayload.ok && statusPayload.running) {
            await sleep(1200);
            continue;
          }
        }
        const historyPayload = await client.history();
        const messages = historyPayload.messages || [];
        if (historyPayload.ok && hasRecoveredTurn(messages, message, baselineMessageCount)) {
          pendingApproval = historyPayload.pending_approval || null;
          if ('plan' in historyPayload) renderPlan(historyPayload.plan);
          renderMessages(messages, pendingApproval);
          elements.banner.textContent = 'Connexion interrompue; résultat récupéré depuis l’historique.';
          refreshAfterRun();
          return true;
        }
      } catch (_) {
        // Le serveur peut être en redémarrage; on réessaie brièvement.
      }
      await sleep(1200);
    }
    return false;
  }

  function hasRecoveredTurn(messages, message, baselineMessageCount) {
    if (!Array.isArray(messages) || messages.length < 2) return false;
    for (let index = messages.length - 2; index >= 0; index -= 1) {
      const candidate = messages[index];
      if (!candidate || candidate.role !== 'user' || candidate.content !== message) continue;
      const assistantIndex = messages.findIndex((item, offset) => offset > index && item.role === 'assistant');
      if (assistantIndex < 0) continue;
      const countAdvanced = messages.length >= baselineMessageCount + 2;
      const recent = isRecentHistoryMessage(candidate) || isRecentHistoryMessage(messages[assistantIndex]);
      return countAdvanced || recent;
    }
    return false;
  }

  function isRecentHistoryMessage(message) {
    const time = Date.parse(message.created_at || '');
    return Number.isFinite(time) && Date.now() - time < 5 * 60 * 1000;
  }

  function renderedMessageCount() {
    return elements.thread.querySelectorAll('.message.user, .message.assistant:not(.working)').length;
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function stopRun() {
    if (!running || stopRequested) return;
    stopRequested = true;
    elements.status.textContent = 'Arrêt demandé';
    elements.stop.disabled = true;
    try {
      await client.stop();
    } catch (_) {
      // Le run courant vérifiera aussi son signal côté serveur.
    }
  }

  function composerMessage() {
    const refs = attachments.map((item) => item.reference);
    return [elements.input.value.trim(), ...refs].filter(Boolean).join('\n');
  }

  function clearComposer() {
    elements.input.value = '';
    attachments.length = 0;
    renderQueued();
    renderCommandMenu();
    resizeComposer();
  }

  function resizeComposer() {
    elements.input.style.height = 'auto';
    elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
  }

  function syncComposerSpace() {
    const height = Math.ceil(elements.form.getBoundingClientRect().height);
    const measuredScrollbarWidth = Math.max(0, elements.main.offsetWidth - elements.main.clientWidth);
    const scrollbarWidth = Math.max(18, measuredScrollbarWidth);
    elements.app.style.setProperty('--composer-space', `${height}px`);
    elements.app.style.setProperty('--scrollbar-width', `${scrollbarWidth}px`);
  }

  function observeComposerSpace() {
    syncComposerSpace();
    if ('ResizeObserver' in window) {
      composerObserver = new ResizeObserver(syncComposerSpace);
      composerObserver.observe(elements.form);
      composerObserver.observe(elements.main);
      return;
    }
    window.addEventListener('resize', syncComposerSpace);
  }

  function enqueueDraft(message) {
    draftQueue.push({id: ++draftId, text: message});
    renderDraftQueue();
  }

  function runNextDraft() {
    if (running || !draftQueue.length) return;
    const next = draftQueue.shift();
    renderDraftQueue();
    if (next && next.text.trim()) sendNow(next.text.trim());
  }

  function renderDraftQueue() {
    elements.draftQueue.textContent = '';
    if (!draftQueue.length) return;
    const title = document.createElement('div');
    title.className = 'draft-queue-title';
    title.textContent = `${draftQueue.length} demande(s) en attente`;
    elements.draftQueue.appendChild(title);
    draftQueue.forEach((draft) => {
      const item = document.createElement('div');
      item.className = 'draft-item';
      const input = document.createElement('textarea');
      input.value = draft.text;
      input.rows = Math.min(4, Math.max(1, draft.text.split('\n').length));
      input.setAttribute('aria-label', 'Message en attente');
      input.addEventListener('input', () => {
        draft.text = input.value;
      });
      const actions = document.createElement('div');
      actions.className = 'draft-actions';
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'icon-button draft-remove';
      remove.title = 'Supprimer ce message';
      remove.setAttribute('aria-label', 'Supprimer ce message');
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        draftQueue = draftQueue.filter((itemDraft) => itemDraft.id !== draft.id);
        renderDraftQueue();
      });
      actions.append(remove);
      item.append(input, actions);
      elements.draftQueue.appendChild(item);
    });
  }

  function setRunning(value) {
    const wasRunning = running;
    running = value;
    if (value && !wasRunning) runningSince = Date.now();
    if (!value) runningSince = 0;
    if (value) showActivityIndicator();
    else removeActivityIndicator();
    elements.stop.hidden = !value;
    elements.stop.disabled = !value || stopRequested;
    elements.send.type = 'submit';
    elements.send.textContent = '↑';
    elements.send.title = value ? 'Ajouter à la queue' : 'Envoyer';
    elements.send.setAttribute('aria-label', value ? 'Ajouter à la queue' : 'Envoyer');
  }

  function startStatusPolling() {
    if (statusTimer) return;
    statusTimer = window.setInterval(() => {
      if (running || pendingApproval) loadStatus().catch(() => {});
      channelPollTick += 1;
      if (channelPollTick >= 6) {
        channelPollTick = 0;
        loadProjects().catch(() => {});
      }
    }, 2500);
    window.addEventListener('focus', () => {
      loadStatus().catch(() => {});
      loadProjects().catch(() => {});
    });
  }

  async function loadThemes() {
    if (!client.themes) {
      renderThemeOptions(elements.theme, themes, localStorage.getItem(themeStoreKey) || 'system');
      return;
    }
    try {
      const payload = await client.themes();
      themes = payload.ok && payload.themes && payload.themes.length ? payload.themes : fallbackThemes();
    } catch (_) {
      themes = fallbackThemes();
    }
    renderThemeOptions(elements.theme, themes, localStorage.getItem(themeStoreKey) || 'system');
  }

  function renderCommandMenu() {
    const matches = commandMatches(elements.input.value, commands);
    elements.commandMenu.textContent = '';
    if (!matches.length) {
      elements.commandMenu.hidden = true;
      return;
    }
    commandIndex = Math.min(commandIndex, matches.length - 1);
    matches.forEach((command, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `command-item${index === commandIndex ? ' active' : ''}${command.conflict ? ' conflict' : ''}`;
      item.dataset.command = command.name;
      const name = document.createElement('span');
      name.className = 'command-name';
      name.textContent = command.name;
      const description = document.createElement('span');
      description.className = 'command-description';
      description.textContent = command.description || command.owner || '';
      const source = document.createElement('span');
      source.className = 'command-source';
      source.textContent = command.local ? 'local' : command.source || '';
      item.append(name, description, source);
      elements.commandMenu.appendChild(item);
    });
    elements.commandMenu.hidden = false;
  }

  function handleCommandKey(event) {
    const matches = commandMatches(elements.input.value, commands);
    if (!matches.length) return false;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      commandIndex = (commandIndex + 1) % matches.length;
      renderCommandMenu();
      return true;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      commandIndex = (commandIndex - 1 + matches.length) % matches.length;
      renderCommandMenu();
      return true;
    }
    if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
      event.preventDefault();
      chooseCommand(matches[commandIndex].name);
      return true;
    }
    if (event.key === 'Escape') {
      elements.commandMenu.hidden = true;
      return true;
    }
    return false;
  }

  function chooseCommand(command) {
    if (!command) return;
    elements.input.value = `${command} `;
    elements.commandMenu.hidden = true;
    resizeComposer();
    elements.input.focus();
  }

  function bindEvents() {
    elements.form.addEventListener('submit', sendMessage);
    elements.stop.addEventListener('click', stopRun);
    elements.input.addEventListener('input', () => {
      renderCommandMenu();
      resizeComposer();
    });
    elements.input.addEventListener('keydown', (event) => {
      if (handleCommandKey(event)) return;
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        elements.form.requestSubmit();
      }
    });
    elements.profile.addEventListener('change', saveSettings);
    elements.reasoning.addEventListener('change', saveSettings);
    elements.model.addEventListener('change', saveSettings);
    elements.project.addEventListener('change', switchProject);
    if (elements.headerProject) elements.headerProject.addEventListener('click', openProjectsModal);
    elements.session.addEventListener('change', switchSession);
    elements.sidebarToggle.addEventListener('click', toggleSidebar);
    elements.sidebar.addEventListener('click', (event) => {
      const link = event.target.closest('.sidebar-link');
      if (!link) return;
      const panel = link.dataset.panel;
      if (panel === 'providers') openProvidersModal();
      if (panel === 'skills') openSkillsModal();
      if (panel === 'agents') openAgentsModal();
      if (panel === 'projects') openProjectsModal();
      if (panel === 'notes') openNotesModal();
      if (panel === 'routines') openRoutinesModal();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && elements.app.classList.contains('sidebar-open')) closeSidebar();
    });
    elements.gitDiff.addEventListener('click', toggleGitPanel);
    elements.gitPanelClose.addEventListener('click', () => {
      elements.gitPanel.hidden = true;
      elements.input.focus();
    });
    elements.gitPanelFullscreen.addEventListener('click', toggleGitPanelFullscreen);
    elements.gitBranch.addEventListener('change', switchGitBranch);
    elements.gitCommit.addEventListener('click', prepareGitCommit);
    elements.gitCommitConfirm.addEventListener('click', commitGitChanges);
    elements.gitCommitCancel.addEventListener('click', () => {
      resetGitCommitPreview();
      elements.gitCommitNote.textContent = '';
      elements.input.focus();
    });
    elements.newSession.addEventListener('click', newSession);
    elements.commandMenu.addEventListener('mousedown', (event) => {
      const button = event.target.closest('button[data-command]');
      if (!button) return;
      event.preventDefault();
      chooseCommand(button.dataset.command || '');
    });
    elements.theme.addEventListener('change', () => saveTheme(elements.theme.value));
    if (resolvedCapabilities.canUpload) {
      elements.attach.addEventListener('click', () => elements.fileInput.click());
      elements.fileInput.addEventListener('change', () => {
        uploadFiles(elements.fileInput.files || []);
        elements.fileInput.value = '';
      });
      document.addEventListener('paste', (event) => {
        const files = Array.from(event.clipboardData ? event.clipboardData.items : [])
          .filter((item) => item.kind === 'file')
          .map((item) => item.getAsFile())
          .filter(Boolean);
        if (files.length) uploadFiles(files);
      });
      document.addEventListener('dragover', (event) => {
        event.preventDefault();
        document.body.classList.add('drop-active');
      });
      document.addEventListener('dragleave', () => document.body.classList.remove('drop-active'));
      document.addEventListener('drop', (event) => {
        event.preventDefault();
        document.body.classList.remove('drop-active');
        uploadFiles(event.dataTransfer ? event.dataTransfer.files : []);
      });
    } else {
      elements.attach.hidden = true;
      elements.fileInput.disabled = true;
    }
  }

  async function start() {
    bindEvents();
    observeComposerSpace();
    startStatusPolling();
    await Promise.allSettled([checkCompatibility(), loadThemes()]);
    initTheme(elements.theme, themeStoreKey, themes);
    await loadSettings();
    await Promise.allSettled([loadProjects(), loadCommands(), loadStatus(), loadGit(), loadSessions(), loadHistory()]);
    resizeComposer();
    elements.input.focus();
  }

  return {
    addMessage,
    capabilities: resolvedCapabilities,
    checkCompatibility,
    loadHistory,
    loadCommands,
    loadGit,
    loadProjects,
    loadSessions,
    loadStatus,
    start,
  };
}

function getElements(root) {
  return {
    app: root.querySelector('#bb9-chat'),
    main: root.querySelector('main'),
    thread: root.querySelector('#thread'),
    form: root.querySelector('#form'),
    planPanel: root.querySelector('#plan-panel'),
    input: root.querySelector('#message'),
    send: root.querySelector('#send'),
    stop: root.querySelector('#stop'),
    attach: root.querySelector('#attach'),
    fileInput: root.querySelector('#file'),
    draftQueue: root.querySelector('#draft-queue'),
    queued: root.querySelector('#queued'),
    contextBar: root.querySelector('#context-bar'),
    contextBarFill: root.querySelector('#context-bar-fill'),
    status: root.querySelector('#status'),
    banner: root.querySelector('#banner'),
    profile: root.querySelector('#profile'),
    model: root.querySelector('#model'),
    reasoning: root.querySelector('#reasoning'),
    project: root.querySelector('#project'),
    headerProject: root.querySelector('#header-project'),
    headerProjectName: root.querySelector('#header-project-name'),
    channelBadge: root.querySelector('#channel-badge'),
    session: root.querySelector('#session'),
    gitDiff: root.querySelector('#git-diff'),
    gitCount: root.querySelector('#git-count'),
    sidebarToggle: root.querySelector('#sidebar-toggle'),
    sidebar: root.querySelector('#sidebar'),
    gitPanel: root.querySelector('#git-panel'),
    gitPanelFullscreen: root.querySelector('#git-panel-fullscreen'),
    gitPanelTitle: root.querySelector('#git-panel-title'),
    gitPanelClose: root.querySelector('#git-panel-close'),
    gitFiles: root.querySelector('#git-files'),
    gitBranch: root.querySelector('#git-branch'),
    gitBranchNote: root.querySelector('#git-branch-note'),
    gitCommit: root.querySelector('#git-commit'),
    gitCommitNote: root.querySelector('#git-commit-note'),
    gitCommitPreview: root.querySelector('#git-commit-preview'),
    gitCommitMessage: root.querySelector('#git-commit-message'),
    gitCommitConfirm: root.querySelector('#git-commit-confirm'),
    gitCommitCancel: root.querySelector('#git-commit-cancel'),
    newSession: root.querySelector('#new-session'),
    theme: root.querySelector('#theme'),
    commandMenu: root.querySelector('#command-menu'),
  };
}

function renderOptions(select, values, selected, label = (value) => value) {
  select.textContent = '';
  for (const value of values) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label(value);
    option.selected = value === selected;
    select.appendChild(option);
  }
}

function renderModelOptions(providers, activeProviderId, activeModel) {
  const select = document.querySelector('#model');
  select.textContent = '';
  let selectedValue = '';
  for (const provider of providers) {
    const group = document.createElement('optgroup');
    group.label = provider.name || provider.provider || 'Provider';
    const models = provider.models && provider.models.length ? provider.models : [provider.model].filter(Boolean);
    for (const model of models) {
      const option = document.createElement('option');
      option.value = `${provider.id || ''}::${model}`;
      option.textContent = model;
      option.dataset.providerId = provider.id || '';
      option.dataset.model = model;
      if ((provider.id || '') === activeProviderId && model === activeModel) selectedValue = option.value;
      group.appendChild(option);
    }
    if (!group.children.length) {
      const option = document.createElement('option');
      option.textContent = provider.error ? 'modèles indisponibles' : 'aucun modèle';
      option.disabled = true;
      if (provider.error) {
        option.title = provider.error;
        group.label += ' ⚠';
      }
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  if (!select.options.length && activeModel) {
    const option = document.createElement('option');
    option.value = `::${activeModel}`;
    option.textContent = activeModel;
    option.dataset.providerId = '';
    option.dataset.model = activeModel;
    select.appendChild(option);
  }
  select.value = selectedValue || (select.options[0] ? select.options[0].value : '');
}

function selectedModelProviderId(select) {
  return select.selectedOptions[0] ? select.selectedOptions[0].dataset.providerId || '' : '';
}

function selectedModelName(select) {
  return select.selectedOptions[0] ? select.selectedOptions[0].dataset.model || select.value : select.value;
}

function projectLabel(project, workspace) {
  if (project.kind === 'agent_home') return project.label || `Accueil · ${project.agent || 'default'}`;
  const path = project.path || '';
  const name = path.split('/').filter(Boolean).pop() || path || 'Projet';
  const count = Number(project.session_count || 0);
  const suffix = project.runtime_workspace || path === workspace ? ' · actif' : '';
  return `${project.label || name}${count ? ` (${count})` : ''}${suffix}`;
}

export function commandMatches(value, commands) {
  const text = value.trimStart();
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  const needle = text.toLowerCase();
  return normalizedCommands(commands)
    .map((command, index) => ({command, index}))
    .filter(({command}) => command.name && command.name.toLowerCase().startsWith(needle))
    .sort((left, right) => {
      const rank = commandRank(left.command) - commandRank(right.command);
      if (rank !== 0) return rank;
      const alpha = left.command.name.localeCompare(right.command.name, 'fr', {sensitivity: 'base'});
      return alpha || left.index - right.index;
    })
    .map(({command}) => command);
}

function normalizedCommands(commands) {
  const seen = new Set();
  const normalized = [];
  for (const command of commands) {
    const name = normalizeCommandName(command.name || '');
    if (!name || seen.has(name)) continue;
    normalized.push({...command, name});
    seen.add(name);
  }
  return normalized;
}

function normalizeCommandName(name) {
  const text = String(name || '').trim();
  if (!text.startsWith('/')) return '';
  const command = text.endsWith(' ...') ? text.slice(0, -4).trim() : text;
  return command.split(/\s+/, 1)[0] || '';
}

function commandRank(command) {
  const workflow = workflowCommandRank(command.name || '');
  if (workflow !== null) return workflow;
  if (command.source === 'native') return 0;
  if (command.local || command.source === 'local-skill') return 2;
  return 1;
}

function workflowCommandRank(name) {
  if (name === '/plan') return -20;
  if (name === '/build') return -19;
  return null;
}

function renderThemeOptions(select, themes, selected) {
  const ids = new Set(themes.map((theme) => theme.id));
  const active = ids.has(selected) ? selected : 'system';
  select.textContent = '';
  for (const theme of themes) {
    const option = document.createElement('option');
    option.value = theme.id;
    option.textContent = theme.label || theme.id;
    option.selected = theme.id === active;
    select.appendChild(option);
  }
}

function initTheme(select, storeKey, themes) {
  const stored = localStorage.getItem(storeKey) || 'system';
  setTheme(stored, storeKey, themes);
  select.value = document.documentElement.dataset.theme || 'system';
}

function setTheme(value, storeKey, themes) {
  const theme = themes.find((item) => item.id === value) ? value : 'system';
  localStorage.setItem(storeKey, theme);
  document.documentElement.dataset.theme = theme;
  const select = document.querySelector('#theme');
  if (select) select.value = theme;
  applyThemeStylesheet(themes.find((item) => item.id === theme));
}

function readJsonStore(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch (_) {
    return {};
  }
}

function writeJsonStore(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value || {}));
  } catch (_) {
    // Best effort: notification badges should never break the chat surface.
  }
}

function applyThemeStylesheet(theme) {
  const id = 'bb9-custom-theme';
  let link = document.getElementById(id);
  if (!theme || !theme.href) {
    if (link) link.remove();
    return;
  }
  if (!link) {
    link = document.createElement('link');
    link.id = id;
    link.rel = 'stylesheet';
    document.head.appendChild(link);
  }
  link.href = theme.href;
}

function fallbackThemes() {
  return [
    {id: 'system', label: 'Système'},
    {id: 'light', label: 'Clair'},
    {id: 'dark', label: 'Sombre'},
  ];
}

async function filePreview(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
