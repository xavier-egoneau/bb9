import {REQUIRED_FEATURES} from './bb9-client.js';
import {
  renderApproval,
  renderArtifacts,
  renderMessageContent,
  renderTrace,
} from './renderers.js';

export function createBb9Chat({root = document, client, capabilities = {}}) {
  const resolvedCapabilities = {...client.capabilities, ...capabilities};
  const elements = getElements(root);
  const attachments = [];
  const themeStoreKey = 'bb9.chat.theme';
  let commands = [];
  let commandIndex = 0;
  let themes = fallbackThemes();
  let running = false;
  let stopRequested = false;
  let activeController = null;
  let draftQueue = [];
  let draftId = 0;

  function addMessage(role, content, meta = {}) {
    const node = document.createElement('section');
    node.className = `message ${role}`;
    const label = document.createElement('div');
    label.className = 'role';
    label.textContent = role === 'user' ? 'Vous' : 'BB9';
    node.append(label, renderMessageContent(content, client));
    const trace = renderTrace(meta.events || [], meta.artifacts || []);
    if (trace) node.append(trace);
    if (meta.artifacts && meta.artifacts.length) node.append(renderArtifacts(meta.artifacts));
    if (meta.approval && resolvedCapabilities.canApprove) {
      node.append(renderApproval(meta.approval, resolveApproval));
    }
    elements.thread.appendChild(node);
    node.scrollIntoView({block: 'end'});
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

  async function resolveApproval(id, decision, node) {
    elements.status.textContent = decision === 'allow' ? 'Action autorisée' : 'Action refusée';
    const buttons = node.querySelectorAll('button');
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const payload = await client.resolveApproval(id, decision);
      if (!payload.ok) throw new Error(payload.message || payload.error || 'approval failed');
      addMessage('assistant', payload.answer, {events: payload.events, artifacts: payload.artifacts});
      loadStatus();
    } catch (err) {
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
    }
  }

  async function loadStatus() {
    const payload = await client.status();
    if (!payload.ok) return;
    const model = payload.model ? ` · ${payload.model}` : '';
    const reasoning = payload.reasoning_effort ? ` · ${payload.reasoning_effort}` : '';
    const active = payload.active_project && payload.active_project !== payload.workspace
      ? ` · vue: ${payload.active_project}`
      : '';
    elements.status.title = `${payload.workspace}${active} · ${payload.provider}${model}${reasoning} · ${payload.profile} · ${payload.agent}`;
  }

  async function loadSettings() {
    const payload = await client.settings();
    if (!payload.ok) return;
    renderOptions(elements.profile, payload.profiles || [], payload.profile || '');
    renderOptions(elements.reasoning, payload.reasoning_efforts || [], payload.reasoning_effort || '', (value) => value || 'auto');
    elements.model.value = payload.model || '';
  }

  async function saveSettings() {
    elements.status.textContent = 'Réglages';
    try {
      const payload = await client.updateSettings({
        profile: elements.profile.value,
        model: elements.model.value.trim(),
        reasoning_effort: elements.reasoning.value,
      });
      if (!payload.ok) throw new Error(payload.error || 'settings failed');
      await loadStatus();
    } catch (err) {
      addMessage('assistant', String(err), {});
      elements.thread.lastElementChild.classList.add('error');
    } finally {
      elements.status.textContent = 'Prêt';
      elements.input.focus();
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
      elements.thread.textContent = '';
      for (const message of payload.messages) addMessage(message.role, message.content, {artifacts: message.artifacts || []});
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

  function renderMessages(messages) {
    elements.thread.textContent = '';
    for (const message of messages) addMessage(message.role, message.content, {artifacts: message.artifacts || []});
  }

  async function switchSession() {
    const id = elements.session.value;
    if (!id) return;
    const payload = await client.switchSession(id);
    if (!payload.ok) {
      elements.banner.textContent = `Session indisponible: ${payload.error || 'switch failed'}`;
      return;
    }
    renderMessages(payload.messages || []);
    await loadStatus();
    elements.input.focus();
  }

  async function switchProject() {
    const path = elements.project.value;
    if (!path || !client.switchProject) return;
    const payload = await client.switchProject(path);
    if (!payload.ok) {
      elements.banner.textContent = `Projet indisponible: ${payload.error || 'switch failed'}`;
      return;
    }
    elements.banner.textContent = '';
    renderMessages(payload.messages || []);
    await loadProjects();
    await loadSessions();
    await loadCommands();
    await loadStatus();
    elements.input.focus();
  }

  async function newSession() {
    const payload = await client.newSession();
    if (!payload.ok) {
      elements.banner.textContent = `Nouvelle session impossible: ${payload.error || 'new session failed'}`;
      return;
    }
    renderMessages([]);
    await loadStatus();
    await loadSessions();
    elements.input.focus();
  }

  async function sendMessage(event) {
    event.preventDefault();
    const message = composerMessage();
    if (!message) return;
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
    addMessage('user', message);
    clearComposer();
    setRunning(true);
    elements.status.textContent = 'BB9 travaille';
    activeController = new AbortController();
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
        addMessage('assistant', payload.answer, {events: payload.events, artifacts: payload.artifacts, approval: payload.approval});
        loadStatus();
        loadSessions();
        loadCommands();
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        addMessage('assistant', String(err), {});
        elements.thread.lastElementChild.classList.add('error');
      }
    } finally {
      activeController = null;
      setRunning(false);
      elements.status.textContent = 'Prêt';
      const shouldContinue = !stopRequested;
      stopRequested = false;
      elements.input.focus();
      if (shouldContinue) runNextDraft();
    }
  }

  async function stopRun() {
    if (!running || stopRequested) return;
    stopRequested = true;
    elements.status.textContent = 'Arrêt demandé';
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
    running = value;
    elements.send.type = value ? 'button' : 'submit';
    elements.send.textContent = value ? '■' : '↑';
    elements.send.title = value ? 'Arrêter' : 'Envoyer';
    elements.send.setAttribute('aria-label', value ? 'Arrêter' : 'Envoyer');
    elements.send.classList.toggle('stop-icon', value);
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
    elements.input.focus();
  }

  function bindEvents() {
    elements.form.addEventListener('submit', sendMessage);
    elements.send.addEventListener('click', (event) => {
      if (!running) return;
      event.preventDefault();
      stopRun();
    });
    elements.input.addEventListener('input', renderCommandMenu);
    elements.input.addEventListener('keydown', (event) => {
      if (handleCommandKey(event)) return;
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        elements.form.requestSubmit();
      }
    });
    elements.applySettings.addEventListener('click', saveSettings);
    elements.project.addEventListener('change', switchProject);
    elements.session.addEventListener('change', switchSession);
    elements.newSession.addEventListener('click', newSession);
    elements.commandMenu.addEventListener('mousedown', (event) => {
      const button = event.target.closest('button[data-command]');
      if (!button) return;
      event.preventDefault();
      chooseCommand(button.dataset.command || '');
    });
    elements.theme.addEventListener('change', () => setTheme(elements.theme.value, themeStoreKey, themes));
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
    await checkCompatibility();
    await loadThemes();
    initTheme(elements.theme, themeStoreKey, themes);
    await loadSettings();
    await loadProjects();
    await loadCommands();
    await loadStatus();
    await loadHistory();
    await loadSessions();
    elements.input.focus();
  }

  return {
    addMessage,
    capabilities: resolvedCapabilities,
    checkCompatibility,
    loadHistory,
    loadCommands,
    loadProjects,
    loadSessions,
    loadStatus,
    start,
  };
}

function getElements(root) {
  return {
    thread: root.querySelector('#thread'),
    form: root.querySelector('#form'),
    input: root.querySelector('#message'),
    send: root.querySelector('#send'),
    attach: root.querySelector('#attach'),
    fileInput: root.querySelector('#file'),
    draftQueue: root.querySelector('#draft-queue'),
    queued: root.querySelector('#queued'),
    status: root.querySelector('#status'),
    banner: root.querySelector('#banner'),
    profile: root.querySelector('#profile'),
    model: root.querySelector('#model'),
    reasoning: root.querySelector('#reasoning'),
    applySettings: root.querySelector('#apply-settings'),
    project: root.querySelector('#project'),
    session: root.querySelector('#session'),
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

function projectLabel(project, workspace) {
  const path = project.path || '';
  const name = path.split('/').filter(Boolean).pop() || path || 'Projet';
  const count = Number(project.session_count || 0);
  const suffix = project.runtime_workspace || path === workspace ? ' · runtime' : '';
  return `${project.label || name}${count ? ` (${count})` : ''}${suffix}`;
}

function commandMatches(value, commands) {
  const text = value.trimStart();
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  const needle = text.toLowerCase();
  return commands
    .filter((command) => command.name && command.name.toLowerCase().startsWith(needle))
    .slice(0, 8);
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
