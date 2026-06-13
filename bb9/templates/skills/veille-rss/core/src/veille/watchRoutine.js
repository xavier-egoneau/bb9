const { selectFeedsForTopic } = require("./feedSelector.js");
const { fetchFeeds } = require("./feedFetcher.js");
const { scoreArticles } = require("./watchScorer.js");
const { summarizeWatch } = require("./watchSummarizer.js");
const { classifyArticlesForTopic } = require("./watchClassifier.js");
const { addSource, listSources, removeSource } = require("./sourceManager.js");

function deduplicateArticles(articles) {
  const seen = new Set();
  const deduped = [];

  for (const article of articles) {
    const key = (article.link || article.id || "").trim().toLowerCase();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    deduped.push(article);
  }

  return deduped;
}

async function runWatch(topic, options = {}) {
  const normalizedTopic = String(topic || "").trim();
  if (!normalizedTopic) throw new Error("Sujet de veille vide. Utilise: /veille <sujet>");

  const runtimeOptions = {
    maxFeeds: options.maxFeeds || 12,
    maxArticlesPerFeed: options.maxArticlesPerFeed || 20,
    maxResults: options.maxResults || 15,
    language: options.language,
    timeoutMs: options.timeoutMs || 10000,
    useLlmRelevance: options.useLlmRelevance !== false,
    removeBrokenSources: options.removeBrokenSources !== false,
    classificationTimeoutMs: options.classificationTimeoutMs || 20000,
    minLlmConfidence: typeof options.minLlmConfidence === "number" ? options.minLlmConfidence : 0.35,
    translateToFrench: options.translateToFrench !== false,
    translationTimeoutMs: options.translationTimeoutMs || 12000,
    ollamaModel: options.ollamaModel,
    ollamaHost: options.ollamaHost,
  };

  const sources = selectFeedsForTopic(normalizedTopic, runtimeOptions);
  if (!sources.length) {
    return summarizeWatch([], normalizedTopic, { sources: [], errors: ["Aucune source pertinente sélectionnée."], maxResults: runtimeOptions.maxResults });
  }

  const fetched = await fetchFeeds(sources, runtimeOptions);
  const errors = [...fetched.errors];
  const removedBrokenSources = [];

  if (runtimeOptions.removeBrokenSources) {
    for (const failed of fetched.failedSources.filter((item) => item.permanentFailure)) {
      try {
        removeSource(failed.source.id);
        removedBrokenSources.push(failed.source.id);
      } catch (error) {
        errors.push(`Impossible de supprimer la source cassée ${failed.source.id}: ${error.message}`);
      }
    }
  }

  const deduped = deduplicateArticles(fetched.articles);
  const classification = runtimeOptions.useLlmRelevance
    ? await classifyArticlesForTopic(deduped, normalizedTopic, runtimeOptions)
    : { articles: deduped, classified: false, error: null, removedCount: 0 };

  if (classification.error) errors.push(classification.error);
  let curatedArticles = classification.articles;
  let llmClassification = {
    enabled: runtimeOptions.useLlmRelevance,
    applied: classification.classified,
    removedCount: classification.removedCount,
  };
  if (runtimeOptions.useLlmRelevance && classification.classified && !curatedArticles.length && deduped.length) {
    curatedArticles = deduped;
    llmClassification = {
      enabled: true,
      applied: false,
      removedCount: 0,
    };
    errors.push("Filtrage LLM trop strict : aucun article retenu, fallback sur scoring RSS déterministe.");
  }

  const scored = scoreArticles(curatedArticles, normalizedTopic)
    .sort((a, b) => b.score - a.score)
    .slice(0, runtimeOptions.maxResults);

  return summarizeWatch(scored, normalizedTopic, {
    sources,
    errors,
    maxResults: runtimeOptions.maxResults,
    llmClassification,
    removedBrokenSources,
    translateToFrench: runtimeOptions.translateToFrench,
    translationTimeoutMs: runtimeOptions.translationTimeoutMs,
    ollamaModel: runtimeOptions.ollamaModel,
    ollamaHost: runtimeOptions.ollamaHost,
  });
}

function formatSourceList(sources) {
  if (!sources.length) return "Aucune source.";
  return `# Sources RSS/Atom de veille\n\n${sources.map((source) => (
    `- **${source.id}** — ${source.name}\n  - URL : ${source.url}\n  - Langue : ${source.language}\n  - Domaines : ${source.domains.join(", ")}\n  - Tags : ${source.tags.join(", ")}\n  - Fiabilité/bruit : ${source.reliability}/${source.noise}\n  - Activée : ${source.enabled ? "oui" : "non"}`
  )).join("\n\n")}`;
}

async function handleWatchCommand(input) {
  const text = String(input || "").trim();

  if (text.startsWith("/veille--add")) {
    const jsonText = text.slice("/veille--add".length).trim();
    if (!jsonText) return "Erreur: fournis une source JSON après /veille--add.";
    try {
      const source = JSON.parse(jsonText);
      const added = addSource(source);
      return `Source ajoutée: **${added.id}** — ${added.name}`;
    } catch (error) {
      return `Erreur /veille--add: ${error.message}`;
    }
  }

  if (text.startsWith("/veille--list")) {
    return formatSourceList(listSources());
  }

  if (text.startsWith("/veille--remove")) {
    const sourceId = text.slice("/veille--remove".length).trim();
    if (!sourceId) return "Erreur: fournis un sourceId après /veille--remove.";
    try {
      removeSource(sourceId);
      return `Source supprimée: **${sourceId}**`;
    } catch (error) {
      return `Erreur /veille--remove: ${error.message}`;
    }
  }

  if (text.startsWith("/veille")) {
    const topic = text.slice("/veille".length).trim();
    try {
      return await runWatch(topic);
    } catch (error) {
      return `Erreur /veille: ${error.message}`;
    }
  }

  return "Commande inconnue. Utilise /veille, /veille--add, /veille--list ou /veille--remove.";
}

module.exports = {
  deduplicateArticles,
  runWatch,
  handleWatchCommand,
};
