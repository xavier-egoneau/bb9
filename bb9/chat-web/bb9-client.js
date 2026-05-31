export const REQUIRED_FEATURES = ['chat-api', 'image-api'];

export function httpBb9Client(options = {}) {
  const apiBase = trimRight(options.apiBase || '/api');
  const healthPath = options.healthPath || '/health';

  return {
    capabilities: {
      canApprove: true,
      canLoadHistory: true,
      canRenderImages: true,
      canUpload: true,
    },
    async chat(message, options = {}) {
      return postJson(`${apiBase}/chat`, {message}, options);
    },
    async stop() {
      return postJson(`${apiBase}/stop`, {});
    },
    async resolveApproval(id, decision) {
      return postJson(`${apiBase}/approval`, {id, decision});
    },
    async upload(file) {
      const data = await fileToBase64(file);
      return postJson(`${apiBase}/upload`, {mime: file.type, data});
    },
    async status() {
      return getJson(`${apiBase}/status`);
    },
    async history() {
      return getJson(`${apiBase}/history`);
    },
    async commands() {
      return getJson(`${apiBase}/commands`);
    },
    async sessions() {
      return getJson(`${apiBase}/sessions`);
    },
    async projects() {
      return getJson(`${apiBase}/projects`);
    },
    async switchProject(path) {
      return postJson(`${apiBase}/project`, {path});
    },
    async switchSession(id) {
      return postJson(`${apiBase}/session`, {id});
    },
    async newSession() {
      return postJson(`${apiBase}/session/new`, {});
    },
    async settings() {
      return getJson(`${apiBase}/settings`);
    },
    async themes() {
      return getJson(`${apiBase}/themes`);
    },
    async updateSettings(settings) {
      return postJson(`${apiBase}/settings`, settings);
    },
    async health() {
      return getJson(healthPath);
    },
    imageUrl(path) {
      return `${apiBase}/image?path=${encodeURIComponent(path)}`;
    },
  };
}

async function getJson(url) {
  const response = await fetch(url);
  return response.json();
}

async function postJson(url, payload, options = {}) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  return response.json();
}

async function fileToBase64(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
  return String(dataUrl).split(',', 2)[1] || '';
}

function trimRight(value) {
  return String(value).replace(/\/+$/, '');
}
