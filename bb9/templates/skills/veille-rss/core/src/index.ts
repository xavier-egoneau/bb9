import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getMarkdownTheme } from "@earendil-works/pi-coding-agent";
import { Container, Markdown } from "@earendil-works/pi-tui";
import { Type } from "typebox";

const { runWatch, handleWatchCommand } = require("./veille/watchRoutine.js");
const { addSource, listSources, removeSource } = require("./veille/sourceManager.js");

function emitMarkdown(pi: ExtensionAPI, markdown: string) {
  pi.sendMessage({
    customType: "veille-rss",
    content: markdown,
    display: true,
    details: {},
  });
}

export default function (pi: ExtensionAPI) {
  pi.registerMessageRenderer("veille-rss", (message) => {
    const markdown = String(message.content || "");
    const container = new Container();
    container.addChild(new Markdown(markdown, 0, 0, getMarkdownTheme()));
    return container;
  });

  pi.registerCommand("veille", {
    description: "Lancer une veille RSS locale: /veille <sujet>",
    handler: async (args) => {
      const markdown = await handleWatchCommand(`/veille ${args || ""}`);
      emitMarkdown(pi, markdown);
    },
  });

  pi.registerCommand("veille--add", {
    description: "Ajouter une source RSS/Atom JSON: /veille--add { ... }",
    handler: async (args) => {
      const markdown = await handleWatchCommand(`/veille--add ${args || ""}`);
      emitMarkdown(pi, markdown);
    },
  });

  pi.registerCommand("veille--list", {
    description: "Lister les sources RSS/Atom locales",
    handler: async () => {
      const markdown = await handleWatchCommand("/veille--list");
      emitMarkdown(pi, markdown);
    },
  });

  pi.registerCommand("veille--remove", {
    description: "Supprimer une source RSS/Atom: /veille--remove <sourceId>",
    handler: async (args) => {
      const markdown = await handleWatchCommand(`/veille--remove ${args || ""}`);
      emitMarkdown(pi, markdown);
    },
  });

  pi.registerTool({
    name: "rss_watch",
    label: "RSS Watch",
    description: "Run a local RSS/Atom watch without external API keys or article persistence.",
    promptSnippet: "Run a local RSS/Atom watch for a topic.",
    promptGuidelines: ["Use rss_watch when the user asks for a local RSS veille or explicitly invokes /veille."],
    parameters: Type.Object({
      topic: Type.String({ description: "Topic to watch." }),
      maxFeeds: Type.Optional(Type.Number({ description: "Maximum feeds to select." })),
      maxResults: Type.Optional(Type.Number({ description: "Maximum articles to keep." })),
      language: Type.Optional(Type.String({ description: "Optional preferred language, e.g. fr or en." })),
      useLlmRelevance: Type.Optional(Type.Boolean({ description: "Use local Ollama to tag/filter truly relevant articles. Defaults to true." })),
      removeBrokenSources: Type.Optional(Type.Boolean({ description: "Remove sources that return permanent 404/410 errors. Defaults to true." })),
      minLlmConfidence: Type.Optional(Type.Number({ description: "Minimum LLM relevance confidence, default 0.35." })),
      translateToFrench: Type.Optional(Type.Boolean({ description: "Translate titles and snippets to French with local Ollama before rendering. Defaults to true." })),
    }),
    async execute(_toolCallId, params, signal) {
      const markdown = await runWatch(params.topic, {
        maxFeeds: params.maxFeeds,
        maxResults: params.maxResults,
        language: params.language,
        useLlmRelevance: params.useLlmRelevance,
        removeBrokenSources: params.removeBrokenSources,
        minLlmConfidence: params.minLlmConfidence,
        translateToFrench: params.translateToFrench,
        timeoutMs: signal ? 10000 : 10000,
      });
      return { content: [{ type: "text", text: markdown }], details: { topic: params.topic } };
    },
  });

  pi.registerTool({
    name: "rss_source_manager",
    label: "RSS Source Manager",
    description: "List, add, or remove local RSS/Atom watch sources stored in sources.json.",
    parameters: Type.Object({
      action: Type.Union([Type.Literal("list"), Type.Literal("add"), Type.Literal("remove")]),
      source: Type.Optional(Type.Any({ description: "Source object for action=add." })),
      sourceId: Type.Optional(Type.String({ description: "Source id for action=remove." })),
    }),
    async execute(_toolCallId, params) {
      if (params.action === "list") {
        return { content: [{ type: "text", text: JSON.stringify(listSources(), null, 2) }], details: {} };
      }
      if (params.action === "add") {
        const added = addSource(params.source);
        return { content: [{ type: "text", text: `Source ajoutée: ${added.id}` }], details: { source: added } };
      }
      if (params.action === "remove") {
        const removed = removeSource(params.sourceId);
        return { content: [{ type: "text", text: `Source supprimée: ${params.sourceId}` }], details: removed };
      }
      return { content: [{ type: "text", text: "Action inconnue." }], details: {}, isError: true };
    },
  });
}
