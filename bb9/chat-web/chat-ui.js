import {REQUIRED_FEATURES} from './bb9-client.js';
import {
  renderApproval,
  renderArtifacts,
  renderMarkdownFragment,
  renderMessageContent,
  renderTrace,
  renderTraceStep,
  workflowGroups,
} from './renderers.js';

export function liveTraceDisplayGroups(groups) {
  const lastIndex = groups.length - 1;
  return groups.map((group, index) => {
    if (index >= lastIndex || group.kind !== 'process') return group;
    if (String(group.status || '').toLowerCase() !== 'en cours') return group;
    return {...group, status: 'terminé'};
  });
}

export function createBb9Chat({root = document, client, capabilities = {}}) {
  const resolvedCapabilities = {...client.capabilities, ...capabilities};
  const elements = getElements(root);
  const attachments = [];
  const themeStoreKey = 'bb9.chat.theme';
  const planCollapsedStoreKey = 'bb9.chat.plan.collapsed';
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
  let runningSince = 0;
  let planCollapsed = localStorage.getItem(planCollapsedStoreKey) === '1';
  let planFingerprint = '';
  let currentProjectPath = '';

  function addMessage(role, content, meta = {}, options = {}) {
    const stickToBottom = Object.prototype.hasOwnProperty.call(options, 'stickToBottom') ? Boolean(options.stickToBottom) : shouldStickToBottom();
    if (role === 'assistant') removeActivityIndicator();
    const node = document.createElement('section');
    node.className = `message ${role}`;
    const label = document.createElement('div');
    label.className = 'role';
    label.textContent = role === 'user' ? 'Vous' : (role === 'notification' ? 'Info' : 'BB9');
    node.append(label, renderMessageContent(content, client, {markdown: role === 'assistant' || role === 'notification'}));
    if (role === 'assistant') node.appendChild(copyButton(content));
    const trace = renderTrace(meta.events || [], meta.artifacts || []);
    if (trace) node.append(trace);
    if (meta.artifacts && meta.artifacts.length) node.append(renderArtifacts(meta.artifacts, client));
    if (meta.approval && resolvedCapabilities.canApprove) {
      node.append(renderApproval(meta.approval, resolveApproval));
    }
    if (meta.staleApproval) node.append(renderInactiveApprovalNotice());
    elements.thread.appendChild(node);
    if (stickToBottom) scrollToThreadBottom();
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
    const trace = document.createElement('div');
    trace.className = 'working-trace timeline';
    activityTraceNode = trace;
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
    groups = liveTraceDisplayGroups(groups);
    groups.slice(-6).forEach((group) => activityTraceNode.appendChild(renderTraceStep(group)));
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
    titleMeta.textContent = total ? `${completed} tâches sur ${total} terminées${errors ? ` · ${errors} erreur${errors > 1 ? 's' : ''}` : ''}` : 'Aucune tâche structurée';
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
    const details = document.createElement('details');
    details.className = 'plan-raw';
    const summary = document.createElement('summary');
    summary.textContent = 'Texte du plan';
    const raw = document.createElement('div');
    raw.className = 'markdown compact-markdown';
    raw.appendChild(renderMarkdownFragment(markdown));
    details.append(summary, raw);
    body.appendChild(details);
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
    if (status === 'error') {
      const reason = compactPlanText(task.blockers || task.summary || task.evidence || 'Erreur pendant /build.');
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

  function planTaskStatus(task) {
    const status = String(task.status || '').trim().toLowerCase();
    if (status === 'error') return 'error';
    if (task.done || status === 'done') return 'done';
    return 'pending';
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
      pendingApproval = null;
      addMessage('assistant', payload.answer, {events: payload.events, artifacts: payload.artifacts});
      loadStatus();
    } catch (err) {
      buttons.forEach((button) => { button.disabled = false; });
      await loadStatus();
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
      if (!pendingApproval) runNextDraft();
    }
  }

  async function loadStatus() {
    if (statusInFlight) return;
    statusInFlight = true;
    try {
      const payload = await client.status();
      if (!payload.ok) return;
      syncCurrentProject(payload);
      if ('plan' in payload) renderPlan(payload.plan);
      const model = payload.model ? ` · ${payload.model}` : '';
    const reasoning = payload.reasoning_effort ? ` · ${payload.reasoning_effort}` : '';
    const active = payload.active_project && payload.active_project !== payload.workspace
      ? ` · vue: ${payload.active_project}`
      : '';
    elements.status.title = `${payload.workspace}${active} · ${payload.provider}${model}${reasoning} · ${payload.profile} · ${payload.agent}`;
    reconcileRuntimeStatus(payload);
    } finally {
      statusInFlight = false;
    }
  }

  function reconcileRuntimeStatus(payload) {
    if (payload.pending_approval) pendingApproval = payload.pending_approval;
    if (pendingApproval && !payload.pending_approval) pendingApproval = null;
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
      renderCommandMenu();
    } catch (_) {
      commands = [];
      renderCommandMenu();
    }
  }

  async function loadProjects() {
    if (!resolvedCapabilities.canLoadHistory || !client.projects) return;
    const payload = await client.projects();
    if (!payload.ok) return;
    syncCurrentProject(payload);
    elements.project.textContent = '';
    const projects = payload.projects || [];
    if (!projects.length) {
      const option = document.createElement('option');
      option.value = payload.active_project || payload.workspace || '';
      option.textContent = 'Projet courant';
      elements.project.appendChild(option);
      return;
    }
    for (const project of projects) {
      const option = document.createElement('option');
      option.value = project.path;
      option.textContent = projectLabel(project, payload.workspace);
      option.selected = project.path === payload.active_project;
      elements.project.appendChild(option);
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
    const restoreActivity = running;
    const traceSnapshot = liveTraceEvents.slice();
    const traceCursor = liveTraceCursor;
    const traceRunId = liveTraceRunId;
    if (restoreActivity) {
      activityNode = null;
      activityTraceNode = null;
    }
    elements.thread.textContent = '';
    const approvalIndex = latestValidationMessageIndex(messages || []);
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

  function latestValidationMessageIndex(messages) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role === 'assistant' && String(message.content || '').trim() === 'Validation requise.') return index;
      if (message.role === 'assistant') return -1;
    }
    return -1;
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
    const path = elements.project.value;
    if (!path || !client.switchProject) return;
    const previousProjectPath = currentProjectPath;
    currentProjectPath = path;
    renderPlan({exists: false, project_path: path});
    const payload = await client.switchProject(path);
    if (!payload.ok) {
      currentProjectPath = previousProjectPath;
      elements.banner.textContent = `Projet indisponible: ${payload.error || 'switch failed'}`;
      await loadStatus();
      return;
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
    currentProjectPath = String(payload.active_project || payload.workspace || currentProjectPath || '');
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
          addMessage('assistant', payload.message || payload.error || 'Erreur', {});
        } else {
          addMessage('assistant', payload.message || 'Run interrompu.', {});
        }
        elements.thread.lastElementChild.classList.add('error');
      } else {
        pendingApproval = payload.approval || null;
        if ('plan' in payload) renderPlan(payload.plan, {openOnChange: true});
        addMessage('assistant', payload.answer, {events: payload.events, artifacts: payload.artifacts, approval: payload.approval});
        if (payload.notice) addMessage('notification', payload.notice);
        refreshAfterRun();
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        const recovered = await recoverAfterChatNetworkError(err, message, baselineMessageCount);
        if (!recovered) {
          addMessage('assistant', String(err), {});
          elements.thread.lastElementChild.classList.add('error');
        }
      }
    } finally {
      activeController = null;
      setRunning(false);
      elements.status.textContent = 'Prêt';
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
          pendingApproval = null;
          if ('plan' in historyPayload) renderPlan(historyPayload.plan);
          renderMessages(messages);
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
    }, 2500);
    window.addEventListener('focus', () => {
      loadStatus().catch(() => {});
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
      item.className = `command-item${index === commandIndex ? ' active' : ''}`;
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
    elements.session.addEventListener('change', switchSession);
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
    status: root.querySelector('#status'),
    banner: root.querySelector('#banner'),
    profile: root.querySelector('#profile'),
    model: root.querySelector('#model'),
    reasoning: root.querySelector('#reasoning'),
    project: root.querySelector('#project'),
    session: root.querySelector('#session'),
    gitDiff: root.querySelector('#git-diff'),
    gitCount: root.querySelector('#git-count'),
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
    if (group.children.length) select.appendChild(group);
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
