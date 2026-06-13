const { translateArticlesToFrench } = require("./watchTranslator.js");

function formatDate(value) {
  if (!value) return "date inconnue";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "date inconnue";
  return date.toISOString().slice(0, 10);
}

function hasDirectLink(article) {
  return Boolean(article && typeof article.link === "string" && /^https?:\/\//i.test(article.link.trim()));
}

function cleanText(value) {
  return String(value || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function getArticleDescription(article) {
  const description = cleanText(article.summary || article.contentSnippet || article.raw?.contentSnippet || article.raw?.summary || article.raw?.content);
  if (description) return description.length > 500 ? `${description.slice(0, 497)}...` : description;
  return "Aucun descriptif court fourni par le flux RSS/Atom.";
}

function renderArticle(article) {
  const title = cleanText(article.title) || "Article sans titre";
  const source = cleanText(article.sourceName) || "source inconnue";
  const date = formatDate(article.publishedAt);
  const score = typeof article.score === "number" ? article.score : "non calculé";
  const description = getArticleDescription(article);
  const link = article.link.trim();
  const originalTitleLine = article.translatedToFrench && article.originalTitle
    ? `\nTitre original : ${cleanText(article.originalTitle)}\n`
    : "";
  const llmTagsLine = Array.isArray(article.llmTags) && article.llmTags.length
    ? `Tags LLM : ${article.llmTags.join(", ")}  \n`
    : "";
  const llmReasonLine = article.llmRelevanceReason
    ? `Pourquoi retenu : ${cleanText(article.llmRelevanceReason)}  \n`
    : "";

  return `### ${title}\n` +
    originalTitleLine +
    `\nSource : ${source}  \n` +
    `Date : ${date}  \n` +
    `Score : ${score}  \n` +
    llmTagsLine +
    llmReasonLine +
    `\nDescriptif : ${description}\n\n` +
    `Lien : ${link}`;
}

async function summarizeWatch(articles, topic, context = {}) {
  const now = new Date().toISOString();
  const selectedSources = context.sources || [];
  const errors = [...(context.errors || [])];
  const sourceNames = [...new Set(selectedSources.map((source) => source.name))];
  const displayableArticles = articles.filter(hasDirectLink);
  const limitedArticles = displayableArticles.slice(0, context.maxResults || displayableArticles.length);
  const shouldTranslate = context.translateToFrench !== false;
  const translation = shouldTranslate
    ? await translateArticlesToFrench(limitedArticles, context)
    : { articles: limitedArticles, translated: false, error: null };
  const topArticles = translation.articles;
  if (translation.error) errors.push(translation.error);

  const llmClassification = context.llmClassification || {};
  const removedBrokenSources = context.removedBrokenSources || [];
  const discoveryBlocks = topArticles.slice(0, 5).map(renderArticle);
  const articleBlocks = topArticles.map(renderArticle);
  const hiddenWithoutLink = articles.length - displayableArticles.length;

  return `# Veille RSS — ${topic}\n\n` +
    `- **Date d'exécution :** ${now}\n` +
    `- **Sujet demandé :** ${topic}\n` +
    `- **Sources utilisées :** ${sourceNames.length ? sourceNames.join(", ") : "aucune"}\n` +
    `- **Articles retenus :** ${topArticles.length}\n\n` +
    `## Résumé global\n\n` +
    (topArticles.length
      ? `Cette veille locale a identifié ${topArticles.length} article(s) pertinent(s) avec lien direct. Les résultats sont classés par correspondance avec le sujet, fraîcheur, fiabilité de la source et bruit estimé.\n\n`
      : `Aucun article suffisamment pertinent avec lien direct n'a été trouvé avec les sources sélectionnées.\n\n`) +
    `## Top découvertes\n\n` +
    (discoveryBlocks.length ? discoveryBlocks.join("\n\n") : "- Aucune découverte majeure avec lien direct.") +
    `\n\n## Articles importants\n\n` +
    (articleBlocks.length ? articleBlocks.join("\n\n") : "Aucun article avec lien direct à afficher.") +
    `\n\n## Limites de la veille\n\n` +
    `- Synthèse extractive sans appel à une API LLM externe ; traduction optionnelle via Ollama local si disponible.\n` +
    `- Les articles ne sont pas stockés ; ils restent uniquement en mémoire pendant l'exécution.\n` +
    `- La pertinence dépend des métadonnées RSS/Atom disponibles.\n` +
    `- Les articles sans lien direct valide ne sont jamais affichés.\n` +
    (llmClassification.applied ? `- Un filtrage LLM local a taggué les articles et masqué ${llmClassification.removedCount || 0} résultat(s) jugé(s) hors sujet.\n` : "") +
    (removedBrokenSources.length ? `- Sources supprimées automatiquement après erreur permanente 404/410 : ${removedBrokenSources.join(", ")}.\n` : "") +
    (shouldTranslate && translation.translated ? `- Les titres et descriptifs ont été traduits en français avant le rendu via Ollama local.\n` : "") +
    (hiddenWithoutLink > 0 ? `- ${hiddenWithoutLink} article(s) sans lien direct ont été masqués.\n` : "") +
    (errors.length ? `- Certains flux ont échoué : ${errors.join(" ; ")}\n` : "") +
    `\n## Suggestion d'amélioration\n\n` +
    `Brancher ultérieurement un résumé local via Ollama pour produire une synthèse plus sémantique, sans clé API ni service payant.\n`;
}

module.exports = {
  summarizeWatch,
};
