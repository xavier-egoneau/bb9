const Parser = require("rss-parser");

function normalizeItem(item, source) {
  const publishedAt = item.isoDate || item.pubDate || item.published || item.updated || null;
  const link = item.link || item.guid || "";

  return {
    id: item.guid || link || `${source.id}:${item.title || "untitled"}`,
    sourceId: source.id,
    sourceName: source.name,
    sourceReliability: source.reliability,
    sourceNoise: source.noise,
    title: item.title || "Sans titre",
    link,
    publishedAt,
    author: item.creator || item.author || "",
    summary: item.summary || item.contentSnippet || item.content || "",
    contentSnippet: item.contentSnippet || item.summary || "",
    categories: item.categories || [],
    raw: item,
  };
}

function parseStatusCode(error) {
  const match = String(error && error.message || "").match(/Status code (\d+)/i);
  return match ? Number(match[1]) : null;
}

async function fetchFeed(source, options = {}) {
  const { timeoutMs = 10000, maxArticlesPerFeed = 20 } = options;
  const parser = new Parser({ timeout: timeoutMs });

  try {
    const feed = await parser.parseURL(source.url);
    const items = Array.isArray(feed.items) ? feed.items : [];
    return {
      source,
      articles: items.slice(0, maxArticlesPerFeed).map((item) => normalizeItem(item, source)),
      error: null,
      statusCode: 200,
    };
  } catch (error) {
    const statusCode = parseStatusCode(error);
    return {
      source,
      articles: [],
      error: `Échec du flux ${source.name}: ${error.message}`,
      errorMessage: error.message,
      statusCode,
      permanentFailure: statusCode === 404 || statusCode === 410,
    };
  }
}

async function fetchFeeds(sources, options = {}) {
  const results = await Promise.all(sources.map((source) => fetchFeed(source, options)));
  return {
    articles: results.flatMap((result) => result.articles),
    errors: results.filter((result) => result.error).map((result) => result.error),
    failedSources: results.filter((result) => result.error).map((result) => ({
      source: result.source,
      error: result.error,
      statusCode: result.statusCode,
      permanentFailure: result.permanentFailure,
    })),
    results,
  };
}

module.exports = {
  fetchFeed,
  fetchFeeds,
};
