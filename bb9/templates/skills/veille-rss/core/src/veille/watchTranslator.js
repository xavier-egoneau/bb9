function cleanText(value) {
  return String(value || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function truncate(value, maxLength = 900) {
  const text = cleanText(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function getDescription(article) {
  return truncate(article.summary || article.contentSnippet || article.raw?.contentSnippet || article.raw?.summary || article.raw?.content || "");
}

function buildTranslationPayload(articles) {
  return articles.map((article, index) => ({
    index,
    title: truncate(article.title, 220),
    description: getDescription(article),
  }));
}

function applyTranslations(articles, translations) {
  const byIndex = new Map();
  for (const item of Array.isArray(translations) ? translations : []) {
    if (Number.isInteger(item.index)) byIndex.set(item.index, item);
  }

  return articles.map((article, index) => {
    const translated = byIndex.get(index);
    if (!translated) return article;

    return {
      ...article,
      originalTitle: article.title,
      originalSummary: article.summary,
      originalContentSnippet: article.contentSnippet,
      title: cleanText(translated.titleFr || translated.title || article.title),
      summary: cleanText(translated.descriptionFr || translated.description || article.summary || article.contentSnippet),
      contentSnippet: cleanText(translated.descriptionFr || translated.description || article.contentSnippet || article.summary),
      translatedToFrench: true,
    };
  });
}

function normalizeTranslationResponse(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.translations)) return value.translations;
  if (value && Array.isArray(value.articles)) return value.articles;
  if (value && Array.isArray(value.items)) return value.items;
  throw new Error("Réponse Ollama sans tableau de traductions.");
}

function extractJsonArray(text) {
  const raw = String(text || "").trim();
  try {
    return normalizeTranslationResponse(JSON.parse(raw));
  } catch (_) {
    const start = raw.indexOf("[");
    const end = raw.lastIndexOf("]");
    if (start === -1 || end === -1 || end <= start) throw new Error("Réponse Ollama sans tableau JSON.");
    return normalizeTranslationResponse(JSON.parse(raw.slice(start, end + 1)));
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

async function translateArticlesToFrench(articles, options = {}) {
  if (!articles.length) return { articles, translated: false, error: null };

  const host = options.ollamaHost || process.env.OLLAMA_HOST || "http://localhost:11434";
  const timeoutMs = options.translationTimeoutMs || 12000;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  const payload = buildTranslationPayload(articles);
  const prompt = `Tu traduis en français des titres et descriptifs d'articles RSS.\n` +
    `Contraintes strictes :\n` +
    `- Réponds uniquement par un tableau JSON valide.\n` +
    `- Conserve exactement le champ index.\n` +
    `- Ne change pas le sens, ne résume pas excessivement.\n` +
    `- Si un descriptif est vide, mets une phrase française courte indiquant que le flux ne fournit pas de descriptif.\n` +
    `Format attendu : [{"index":0,"titleFr":"...","descriptionFr":"..."}]\n\n` +
    `Articles à traduire :\n${JSON.stringify(payload, null, 2)}`;

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
    const translatedArticles = applyTranslations(articles, parsed);
    const translatedCount = translatedArticles.filter((article) => article.translatedToFrench).length;
    if (translatedCount === 0) throw new Error("aucune traduction exploitable reçue");

    return {
      articles: translatedArticles,
      translated: true,
      error: null,
    };
  } catch (error) {
    return {
      articles,
      translated: false,
      error: `Traduction française indisponible via Ollama local : ${error.message}`,
    };
  } finally {
    clearTimeout(timeout);
  }
}

module.exports = {
  translateArticlesToFrench,
};
