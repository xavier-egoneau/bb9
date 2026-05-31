export function renderMessageContent(content, client) {
  const fragment = document.createDocumentFragment();
  const imagePaths = imageRefs(content);
  const text = stripImageRefs(content);
  if (text) {
    const body = document.createElement('div');
    body.className = 'message-text';
    body.textContent = text;
    fragment.appendChild(body);
  }
  if (imagePaths.length) {
    const grid = document.createElement('div');
    grid.className = 'message-images';
    for (const image of imagePaths) {
      const img = document.createElement('img');
      img.className = 'message-image';
      img.loading = 'lazy';
      img.alt = image.alt || 'Image jointe';
      img.src = client.imageUrl(image.path);
      grid.appendChild(img);
    }
    fragment.appendChild(grid);
  }
  if (!text && !imagePaths.length) {
    const body = document.createElement('div');
    body.className = 'message-text';
    body.textContent = content;
    fragment.appendChild(body);
  }
  return fragment;
}

export function imageRefs(content) {
  const refs = [];
  const bb9RefPattern = /\[image:\s*([^\]]+)\]/gi;
  let match = bb9RefPattern.exec(content);
  while (match) {
    refs.push({path: cleanImagePath(match[1]), alt: 'Image jointe'});
    match = bb9RefPattern.exec(content);
  }
  const markdownPattern = /!\[([^\]]*)\]\(([^)]+)\)/g;
  match = markdownPattern.exec(content);
  while (match) {
    refs.push({path: cleanImagePath(match[2]), alt: match[1].trim() || 'Image jointe'});
    match = markdownPattern.exec(content);
  }
  return refs;
}

export function stripImageRefs(content) {
  return content
    .replace(/\[image:\s*[^\]]+\]/gi, '')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .trim();
}

export function cleanImagePath(path) {
  return path.trim().replace(/^`|`$/g, '').replace(/^<|>$/g, '').replace(/^file:\/\//, '');
}

export function renderTrace(events, artifacts = []) {
  let groups = traceGroups(events);
  if (!groups.length) groups = traceGroupsFromArtifacts(artifacts);
  if (!groups.length) return null;
  const details = document.createElement('details');
  details.className = 'trace';
  const summary = document.createElement('summary');
  const title = document.createElement('span');
  title.textContent = 'Trace outils';
  const count = document.createElement('span');
  count.className = 'trace-count';
  count.textContent = `${groups.length} étape${groups.length > 1 ? 's' : ''}`;
  summary.append(title, count);
  const timeline = document.createElement('div');
  timeline.className = 'timeline';
  for (const group of groups) timeline.appendChild(renderTraceStep(group));
  details.append(summary, timeline);
  return details;
}

export function traceGroups(events) {
  const groups = [];
  let pendingGuardians = [];
  let current = null;
  for (const event of events) {
    if (!['action', 'observation', 'guardian'].includes(event.type)) continue;
    const data = event.data || {};
    if (event.type === 'guardian') {
      pendingGuardians.push(event);
      continue;
    }
    if (event.type === 'action') {
      current = {
        tool: String(data.tool || event.summary || 'tool'),
        command: String(data.cmd || ''),
        action: event,
        observation: null,
        guardians: pendingGuardians,
      };
      pendingGuardians = [];
      groups.push(current);
      continue;
    }
    const tool = String(data.tool || '');
    if (event.type === 'observation' && tool) {
      if (current && (!current.observation || current.tool === tool)) {
        current.observation = event;
      } else {
        groups.push({tool, command: '', action: null, observation: event, guardians: pendingGuardians});
        pendingGuardians = [];
      }
    }
  }
  for (const guardian of pendingGuardians) {
    const data = guardian.data || {};
    const action = data.action ? String(data.action) : '';
    if (action) groups.push({tool: action, command: '', action: null, observation: null, guardians: [guardian]});
  }
  return groups;
}

export function traceGroupsFromArtifacts(artifacts) {
  const toolTrace = artifacts.find((artifact) => artifact.kind === 'tool_trace');
  const entries = toolTrace && toolTrace.metadata ? toolTrace.metadata.entries || [] : [];
  return entries.map((entry) => ({
    tool: String(entry.tool || 'tool'),
    command: String(entry.cmd || ''),
    action: null,
    observation: {
      summary: String(entry.summary || ''),
      data: {ok: entry.ok !== false},
    },
    guardians: [],
  }));
}

export function renderTraceStep(group) {
  const ok = group.observation && group.observation.data ? group.observation.data.ok !== false : true;
  const step = document.createElement('div');
  step.className = `trace-step ${ok ? 'ok' : 'error'}`;
  const dot = document.createElement('div');
  dot.className = 'trace-dot';
  const card = document.createElement('div');
  card.className = 'trace-card';
  const title = document.createElement('div');
  title.className = 'trace-title';
  const name = document.createElement('span');
  name.textContent = group.tool;
  const statusText = group.observation ? (ok ? 'ok' : 'erreur') : 'validation';
  const status = document.createElement('span');
  status.className = 'trace-status';
  status.textContent = statusText;
  title.append(name, status);
  card.appendChild(title);
  if (group.command) {
    const command = document.createElement('div');
    command.className = 'trace-command';
    command.textContent = group.command;
    card.appendChild(command);
  }
  for (const guardian of group.guardians || []) {
    const note = document.createElement('div');
    note.className = 'trace-guardian';
    note.textContent = guardian.summary;
    card.appendChild(note);
  }
  if (group.observation) {
    const output = document.createElement('div');
    output.className = 'trace-summary';
    output.textContent = group.observation.summary;
    card.appendChild(output);
  }
  step.append(dot, card);
  return step;
}

export function renderApproval(approval, onResolve) {
  const node = document.createElement('div');
  node.className = 'approval';
  const reason = document.createElement('div');
  const details = approval.tool ? `${approval.tool} · ${approval.reason}` : approval.reason;
  reason.textContent = `Validation requise · ${details}`;
  const actions = document.createElement('div');
  actions.className = 'approval-actions';
  const allow = document.createElement('button');
  allow.type = 'button';
  allow.textContent = 'Autoriser';
  const deny = document.createElement('button');
  deny.type = 'button';
  deny.className = 'deny';
  deny.textContent = 'Refuser';
  allow.onclick = () => onResolve(approval.id, 'allow', node);
  deny.onclick = () => onResolve(approval.id, 'deny', node);
  actions.append(allow, deny);
  node.append(reason, actions);
  return node;
}

export function renderArtifacts(artifacts) {
  const visibleArtifacts = artifacts.filter((artifact) => artifact.kind !== 'tool_trace' && !(artifact.metadata || {}).default_hidden);
  if (!visibleArtifacts.length) return document.createDocumentFragment();
  const list = document.createElement('div');
  list.className = 'artifacts';
  for (const artifact of visibleArtifacts) {
    const item = document.createElement('div');
    item.className = 'artifact';
    const title = document.createElement('div');
    title.className = 'artifact-title';
    title.textContent = `${artifact.kind} · ${artifact.title || artifact.id}`;
    item.appendChild(title);
    if (artifact.path) {
      const path = document.createElement('div');
      path.className = 'artifact-path';
      path.textContent = artifact.path;
      item.appendChild(path);
    }
    list.appendChild(item);
  }
  return list;
}
