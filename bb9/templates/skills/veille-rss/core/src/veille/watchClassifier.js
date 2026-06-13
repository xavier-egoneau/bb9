function cleanText(value) {
  return String(value || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function truncate(value, maxLength = 700) {
  const text = cleanText(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function buildClassificationPayload(articles) {
  return articles.map((article, index) => ({
    index,
    title: truncate(article.title, 220),
    source: article.sourceName,
    description: truncate(article.summary || article.contentSnippet || article.raw?.contentSnippet || article.raw?.summary || article.raw?.content || "", 700),
    categories: Array.isArray(article.categories) ? article.categories.slice(0, 8) : [],
    link: article.link,
  }));
}

function normalizeClassificationResponse(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.classifications)) return value.classifications;
  if (value && Array.isArray(value.articles)) return value.articles;
  if (value && Array.isArray(value.items)) return value.items;
  throw new Error("Réponse Ollama sans tableau de classifications.");
}

function extractJsonArray(text) {
  const raw = String(text || "").trim();
  try {
    return normalizeClassificationResponse(JSON.parse(raw));
  } catch (_) {
    const start = raw.indexOf("[");
    const end = raw.lastIndexOf("]");
    if (start === -1 || end === -1 || end <= start) throw new Error("Réponse Ollama sans tableau JSON.");
    return normalizeClassificationResponse(JSON.parse(raw.slice(start, end + 1)));
  }
}

async function resolveOllamaModel(host, options, signal) {
  if (options.ollamaModel || process.env.PI_VEILLE_OLLAMA_MODEL) {
    return options.ollamaModel || process.env.PI_VEILLE_OLLAMA_MODEL;
  }

  const response = await fetch(`${host.replace(/\/$/, "")}/api/tags`, { signal });
  if (!response.ok) throw new Error(`Ollama tags HTTP ${response.status}`);
  const data = await response.json();
  const models = Array.isArray(data.models) ? data.models.map((model) => model.name).filter(Boolean) : [];
  if (!models.length) throw new Error("aucun modèle Ollama installé");

  return models.find((name) => /llama|qwen|mistral|gemma/i.test(name)) || models[0];
}

function applyClassifications(articles, classifications) {
  const byIndex = new Map();
  for (const item of Array.isArray(classifications) ? classifications : []) {
    if (Number.isInteger(item.index)) byIndex.set(item.index, item);
  }

  return articles.map((article, index) => {
    const classification = byIndex.get(index);
    if (!classification) {
      return {
        ...article,
        llmRelevant: true,
        llmRelevanceConfidence: 0,
        llmRelevanceReason: "Non classé par le LLM local ; conservé par sécurité.",
        llmTags: [],
      };
    }

    return {
      ...article,
      llmRelevant: classification.relevant !== false,
      llmRelevanceConfidence: Number(classification.confidence || 0),
      llmRelevanceReason: cleanText(classification.reason || ""),
      llmTags: Array.isArray(classification.tags) ? classification.tags.map(cleanText).filter(Boolean).slice(0, 8) : [],
    };
  });
}

async function classifyArticlesForTopic(articles, topic, options = {}) {
  if (!articles.length) {
    return { articles, classified: false, error: null, removedCount: 0 };
  }

  const host = options.ollamaHost || process.env.OLLAMA_HOST || "http://localhost:11434";
  const timeoutMs = options.classificationTimeoutMs || 20000;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const payload = buildClassificationPayload(articles);

  const prompt = `Tu es un filtre de veille RSS. Tu dois décider si chaque article est réellement lié au sujet demandé.\n` +
    `Sujet de veille : ${topic}\n\n` +
    `Contraintes strictes :\n` +
    `- Réponds uniquement par un tableau JSON valide.\n` +
    `- Conserve exactement le champ index.\n` +
    `- relevant=false si l'article est hors sujet, trop général, ou seulement lié par un mot ambigu.\n` +
    `- relevant=true uniquement si l'article apporte une information utile pour ce sujet de veille.\n` +
    `- confidence entre 0 et 1.\n` +
    `- tags en français, courts, maximum 5.\n` +
    `Format attendu : [{"index":0,"relevant":true,"confidence":0.86,"tags":["llm","outils développeur"],"reason":"raison courte en français"}]\n\n` +
    `Articles :\n${JSON.stringify(payload, null, 2)}`;

  try {
    const model = await resolveOllamaModel(host, options, controller.signal);
    const response = await fetch(`${host.replace(/\/$/, "")}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, prompt, stream: false, format: "json" }),
      signal: controller.signal,
    });

    if (!response.ok) throw new Error(`Ollama HTTP ${response.status}`);
    const data = await response.json();
    const parsed = extractJsonArray(data.response);
    const classifiedArticles = applyClassifications(articles, parsed);
    const minConfidence = typeof options.minLlmConfidence === "number" ? options.minLlmConfidence : 0.35;
    const keptArticles = classifiedArticles.filter((article) => article.llmRelevant && article.llmRelevanceConfidence >= minConfidence);

    return {
      articles: keptArticles,
      classified: true,
      error: null,
      removedCount: articles.length - keptArticles.length,
    };
  } catch (error) {
    return {
      articles,
      classified: false,
      error: `Filtrage LLM local indisponible : ${error.message}`,
      removedCount: 0,
    };
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = {
  classifyArticlesForTopic,
};
