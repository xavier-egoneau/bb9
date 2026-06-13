const fs = require("node:fs");
const { SOURCES_PATH, loadSources, validateSource } = require("./sourceLoader.js");

function writeSources(sources) {
  fs.writeFileSync(SOURCES_PATH, `${JSON.stringify(sources, null, 2)}\n`, "utf8");
}

function addSource(source) {
  validateSource(source);
  const sources = loadSources();
  if (sources.some((existing) => existing.id === source.id)) throw new Error(`Une source existe déjà avec l'id: ${source.id}`);
  if (sources.some((existing) => existing.url === source.url)) throw new Error(`Une source existe déjà avec l'URL: ${source.url}`);
  sources.push(source);
  writeSources(sources);
  return source;
}

function removeSource(sourceId) {
  const sources = loadSources();
  const nextSources = sources.filter((source) => source.id !== sourceId);
  if (nextSources.length === sources.length) throw new Error(`Source introuvable: ${sourceId}`);
  writeSources(nextSources);
  return { removed: sourceId };
}

function updateSource(sourceId, patch) {
  const sources = loadSources();
  const index = sources.findIndex((source) => source.id === sourceId);
  if (index === -1) throw new Error(`Source introuvable: ${sourceId}`);

  const updated = { ...sources[index], ...patch, id: sourceId };
  validateSource(updated);
  if (sources.some((source, currentIndex) => currentIndex !== index && source.url === updated.url)) throw new Error(`Une source existe déjà avec l'URL: ${updated.url}`);

  sources[index] = updated;
  writeSources(sources);
  return updated;
}

function listSources(options = {}) {
  const sources = loadSources();
  if (options.enabled === true) return sources.filter((source) => source.enabled);
  if (options.enabled === false) return sources.filter((source) => !source.enabled);
  return sources;
}

module.exports = {
  addSource,
  removeSource,
  updateSource,
  listSources,
};
