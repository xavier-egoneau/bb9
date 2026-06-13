const fs = require("node:fs");
const path = require("node:path");

const SOURCES_PATH = path.join(__dirname, "sources.json");
const REQUIRED_FIELDS = ["id", "name", "url", "type", "language", "domains", "tags", "reliability", "noise", "enabled"];

function validateSource(source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    throw new Error("La source doit être un objet JSON.");
  }

  for (const field of REQUIRED_FIELDS) {
    if (!(field in source)) throw new Error(`Champ obligatoire manquant: ${field}`);
  }

  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(source.id)) throw new Error("Le champ id doit être en kebab-case.");
  if (typeof source.name !== "string" || !source.name.trim()) throw new Error("Le champ name doit être une chaîne non vide.");
  if (typeof source.url !== "string" || !/^https?:\/\//i.test(source.url)) throw new Error("Le champ url doit être une URL http(s).");
  if (!["rss", "atom"].includes(source.type)) throw new Error('Le champ type doit être "rss" ou "atom".');
  if (typeof source.language !== "string" || !source.language.trim()) throw new Error("Le champ language doit être une chaîne non vide.");
  if (!Array.isArray(source.domains) || source.domains.some((item) => typeof item !== "string")) throw new Error("Le champ domains doit être un tableau de chaînes.");
  if (!Array.isArray(source.tags) || source.tags.some((item) => typeof item !== "string")) throw new Error("Le champ tags doit être un tableau de chaînes.");
  if (!Number.isInteger(source.reliability) || source.reliability < 1 || source.reliability > 5) throw new Error("Le champ reliability doit être un entier de 1 à 5.");
  if (!Number.isInteger(source.noise) || source.noise < 1 || source.noise > 5) throw new Error("Le champ noise doit être un entier de 1 à 5.");
  if (typeof source.enabled !== "boolean") throw new Error("Le champ enabled doit être un booléen.");

  return true;
}

function loadSources() {
  let raw;
  try {
    raw = fs.readFileSync(SOURCES_PATH, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") throw new Error(`Fichier sources introuvable: ${SOURCES_PATH}`);
    throw new Error(`Impossible de lire sources.json: ${error.message}`);
  }

  let sources;
  try {
    sources = JSON.parse(raw);
  } catch (error) {
    throw new Error(`sources.json est invalide: ${error.message}`);
  }

  if (!Array.isArray(sources)) throw new Error("sources.json doit contenir un tableau de sources.");

  const ids = new Set();
  const urls = new Set();
  for (const source of sources) {
    validateSource(source);
    if (ids.has(source.id)) throw new Error(`Identifiant de source dupliqué: ${source.id}`);
    if (urls.has(source.url)) throw new Error(`URL de source dupliquée: ${source.url}`);
    ids.add(source.id);
    urls.add(source.url);
  }

  return sources;
}

function getEnabledSources() {
  return loadSources().filter((source) => source.enabled);
}

module.exports = {
  SOURCES_PATH,
  loadSources,
  getEnabledSources,
  validateSource,
};
