const { getEnabledSources } = require("./sourceLoader.js");

const STOP_WORDS = new Set([
  "a", "an", "and", "are", "as", "avec", "au", "aux", "de", "des", "du", "en", "et", "for", "in", "la", "le", "les", "mon", "nouveau", "nouveaux", "of", "on", "ou", "pour", "sur", "the", "to", "un", "une", "with"
]);

const SYNONYMS = {
  ai: ["ai", "ia", "llm", "machine-learning", "models", "local-ai", "local-llm", "llm-locaux"],
  ia: ["ia", "ai", "llm", "machine-learning", "models", "local-ai", "local-llm", "llm-locaux"],
  frontend: ["frontend", "front-end", "react", "vite", "javascript", "css", "ui", "webdev"],
  cybersécurité: ["cybersécurité", "cybersecurite", "security", "vulnerabilities", "malware", "ransomware", "breach"],
  cybersecurite: ["cybersécurité", "cybersecurite", "security", "vulnerabilities", "malware", "ransomware", "breach"],
  dev: ["dev", "developer-tools", "programming", "open-source", "github"],
  développeurs: ["dev", "developer-tools", "programming", "open-source", "github"],
  developpeurs: ["dev", "developer-tools", "programming", "open-source", "github"],
  design: ["design", "ui", "ux", "css", "accessibility"],
  espace: ["space", "nasa", "astronomy", "science"],
  science: ["science", "research", "space", "machine-learning"]
};

function normalizeText(text) {
  return String(text || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9+#.-]+/g, " ")
    .trim();
}

function extractTerms(topic) {
  const normalized = normalizeText(topic);
  const baseTerms = normalized.split(/\s+/).filter((term) => term.length > 1 && !STOP_WORDS.has(term));
  const expanded = new Set(baseTerms);

  for (const term of baseTerms) {
    const aliases = SYNONYMS[term] || SYNONYMS[normalizeText(term)] || [];
    for (const alias of aliases) expanded.add(normalizeText(alias));
  }

  return Array.from(expanded);
}

function selectFeedsForTopic(topic, options = {}) {
  const { maxFeeds = 12, minScore = 1, language } = options;
  const terms = extractTerms(topic);
  const sources = getEnabledSources();

  return sources
    .map((source) => {
      const searchable = [...source.domains, ...source.tags, source.name].map(normalizeText);
      const matchedTerms = terms.filter((term) => searchable.some((value) => value === term || value.includes(term) || term.includes(value)));
      const directMatchScore = matchedTerms.length * 3;
      const reliabilityScore = source.reliability * 0.8;
      const noisePenalty = source.noise * 0.5;
      const languageBoost = language && source.language === language ? 1.5 : 0;
      const broadTechFallback = matchedTerms.length === 0 && searchable.some((value) => ["tech", "dev", "actualite"].includes(value)) ? 0.75 : 0;
      const selectionScore = Number((directMatchScore + reliabilityScore + languageBoost + broadTechFallback - noisePenalty).toFixed(2));

      return { ...source, selectionScore, matchedTerms };
    })
    .filter((source) => source.selectionScore >= minScore)
    .sort((a, b) => b.selectionScore - a.selectionScore || b.reliability - a.reliability || a.noise - b.noise)
    .slice(0, maxFeeds);
}

module.exports = {
  extractTerms,
  normalizeText,
  selectFeedsForTopic,
};
