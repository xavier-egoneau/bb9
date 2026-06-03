export function renderMessageContent(content, client, options = {}) {
  const fragment = document.createDocumentFragment();
  const imagePaths = imageRefs(content);
  const text = stripImageRefs(content);
  if (text) {
    const body = document.createElement('div');
    body.className = options.markdown ? 'message-text markdown' : 'message-text';
    if (options.markdown) {
      body.appendChild(renderMarkdownFragment(text));
    } else {
      body.textContent = text;
    }
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
      img.addEventListener('click', () => openImageModal(img.src, img.alt));
      img.style.cursor = 'zoom-in';
      grid.appendChild(img);
    }
    fragment.appendChild(grid);
  }
  if (!text && !imagePaths.length) {
    const body = document.createElement('div');
    body.className = options.markdown ? 'message-text markdown' : 'message-text';
    if (options.markdown) {
      body.appendChild(renderMarkdownFragment(content));
    } else {
      body.textContent = content;
    }
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
  const statusText = group.observation ? (ok ? 'ok' : 'erreur') : ((group.guardians || []).length ? 'validation' : 'en cours');
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
    output.className = 'trace-summary markdown compact-markdown';
    output.appendChild(renderMarkdownFragment(group.observation.summary || ''));
    card.appendChild(output);
  }
  step.append(dot, card);
  return step;
}

export function renderMarkdownFragment(markdown) {
  const fragment = document.createDocumentFragment();
  const text = String(markdown || '').replace(/\r\n/g, '\n');
  const parts = text.split(/(```[\s\S]*?```)/g);
  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith('```')) {
      fragment.appendChild(renderCodeBlock(part));
    } else {
      renderMarkdownLines(part, fragment);
    }
  }
  if (!fragment.childNodes.length) fragment.appendChild(document.createTextNode(text));
  return fragment;
}

function renderCodeBlock(block) {
  const match = block.match(/^```([^\n]*)\n?([\s\S]*?)```$/);
  const pre = document.createElement('pre');
  const code = document.createElement('code');
  const language = match ? match[1].trim() : '';
  if (language) code.dataset.language = language;
  code.textContent = match ? match[2].replace(/\n$/, '') : block.replace(/^```|```$/g, '');
  pre.appendChild(code);
  return pre;
}

function renderMarkdownLines(text, fragment) {
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = document.createElement('p');
    appendInlineMarkdown(node, paragraph.join(' '));
    fragment.appendChild(node);
    paragraph = [];
  };
  const flushList = () => {
    if (!list) return;
    fragment.appendChild(list);
    list = null;
  };
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(heading[1].length, 4);
      const node = document.createElement(`h${level}`);
      appendInlineMarkdown(node, heading[2]);
      fragment.appendChild(node);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      if (!list) list = document.createElement('ul');
      const item = document.createElement('li');
      appendInlineMarkdown(item, bullet[1]);
      list.appendChild(item);
      continue;
    }
    const numbered = line.match(/^\d+\.\s+(.+)$/);
    if (numbered) {
      flushParagraph();
      if (!list || list.tagName !== 'OL') {
        flushList();
        list = document.createElement('ol');
      }
      const item = document.createElement('li');
      appendInlineMarkdown(item, numbered[1]);
      list.appendChild(item);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();
}

function appendInlineMarkdown(parent, text) {
  const pattern = /(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith('[')) {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      node = document.createElement('a');
      node.textContent = link ? link[1] : token;
      node.href = safeHref(link ? link[2] : '');
      node.target = '_blank';
      node.rel = 'noreferrer';
    } else {
      node = token.startsWith('`') ? document.createElement('code') : document.createElement('strong');
      node.textContent = token.startsWith('`') ? token.slice(1, -1) : token.slice(2, -2);
    }
    parent.appendChild(node);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
}

function safeHref(href) {
  const value = String(href || '').trim();
  if (/^(https?:|\/|\.\/|\.\.\/)/i.test(value)) return value;
  return '#';
}

export function renderApproval(approval, onResolve) {
  const node = document.createElement('div');
  node.className = 'approval';
  const title = document.createElement('div');
  title.className = 'approval-title';
  title.textContent = `Validation requise · ${approval.tool || 'action'}`;

  const summary = approvalSummary(approval);
  if (summary.label || summary.value) {
    const detail = document.createElement('div');
    detail.className = 'approval-detail';
    const label = document.createElement('span');
    label.textContent = summary.label || 'Action';
    const value = document.createElement('code');
    value.textContent = summary.value || '-';
    detail.append(label, value);
    node.append(title, detail);
  } else {
    node.append(title);
  }

  const reason = document.createElement('div');
  reason.className = 'approval-reason';
  reason.textContent = approval.reason || 'Confirmation requise par le guardian.';
  node.appendChild(reason);

  if (approval.risk) {
    const risk = document.createElement('div');
    risk.className = 'approval-risk';
    risk.textContent = `Risque: ${approval.risk}`;
    node.appendChild(risk);
  }

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
  node.appendChild(actions);
  return node;
}

function approvalSummary(approval) {
  const params = approval.params || {};
  const tool = String(approval.tool || '');
  if (tool === 'shell') return {label: 'Commande', value: String(params.cmd || '')};
  if (tool === 'files') {
    const op = String(params.op || '');
    const path = String(params.path || '');
    const count = Array.isArray(params.items) ? `${params.items.length} fichier(s)` : '';
    return {label: 'Fichier', value: [op, path || count].filter(Boolean).join(' · ')};
  }
  if (tool === 'browser') return {label: 'Navigateur', value: [params.op, params.url].filter(Boolean).join(' · ')};
  if (tool === 'delegate') return {label: 'Délégation', value: [params.worker, params.goal].filter(Boolean).join(' · ')};
  const keys = Object.keys(params);
  if (!keys.length) return {label: '', value: ''};
  return {label: 'Paramètres', value: keys.slice(0, 4).map((key) => `${key}=${params[key]}`).join(' · ')};
}

export function renderArtifacts(artifacts, client = {}) {
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

function openImageModal(src, alt) {
  const existing = document.querySelector('.image-modal-backdrop');
  if (existing) existing.remove();
  const backdrop = document.createElement('div');
  backdrop.className = 'image-modal-backdrop';
  const img = document.createElement('img');
  img.className = 'image-modal-content';
  img.src = src;
  img.alt = alt;
  const close = (event) => {
    if (event.target === backdrop || event.key === 'Escape') {
      backdrop.remove();
      document.removeEventListener('keydown', close);
    }
  };
  backdrop.addEventListener('click', close);
  document.addEventListener('keydown', close);
  backdrop.appendChild(img);
  document.body.appendChild(backdrop);
}
