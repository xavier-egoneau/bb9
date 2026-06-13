const { extractTerms, normalizeText } = require("./feedSelector.js");

function countMatches(text, terms) {
  const normalized = normalizeText(text);
  return terms.filter((term) => normalized.includes(term)).length;
}

function freshnessScore(publishedAt, now = Date.now()) {
  if (!publishedAt) return { value: 0, reason: null };
  const date = new Date(publishedAt).getTime();
  if (Number.isNaN(date)) return { value: 0, reason: null };
  const ageDays = Math.max(0, (now - date) / 86400000);
  if (ageDays <= 2) return { value: 3, reason: "Article très récent" };
  if (ageDays <= 7) return { value: 2, reason: "Article récent" };
  if (ageDays <= 30) return { value: 1, reason: "Article publié ce mois-ci" };
  return { value: -1, reason: "Article ancien" };
}

function scoreArticle(article, topic, options = {}) {
  const terms = extractTerms(topic);
  const reasons = [];
  let score = 0;

  const titleMatches = countMatches(article.title, terms);
  if (titleMatches > 0) {
    score += titleMatches * 4;
    reasons.push("Mot-clé trouvé dans le titre");
  }

  const summaryMatches = countMatches(`${article.summary || ""} ${article.contentSnippet || ""}`, terms);
  if (summaryMatches > 0) {
    score += summaryMatches * 2;
    reasons.push("Mot-clé trouvé dans le résumé");
  }

  const freshness = freshnessScore(article.publishedAt, options.now || Date.now());
  score += freshness.value;
  if (freshness.reason) reasons.push(freshness.reason);

  if (article.sourceReliability >= 4) reasons.push("Source fiable");
  score += (article.sourceReliability || 3) * 0.8;

  if (article.sourceNoise >= 4) reasons.push("Source bruitée pénalisée");
  score -= (article.sourceNoise || 3) * 0.5;

  if (article.link && /^https?:\/\//i.test(article.link)) {
    score += 1;
    reasons.push("Lien valide");
  } else {
    score -= 2;
    reasons.push("Lien absent ou invalide");
  }

  return {
    score: Number(score.toFixed(2)),
    reasons,
  };
}

function scoreArticles(articles, topic, options = {}) {
  return articles.map((article) => {
    const scoring = scoreArticle(article, topic, options);
    return { ...article, score: scoring.score, scoreReasons: scoring.reasons };
  });
}

module.exports = {
  scoreArticle,
  scoreArticles,
};
