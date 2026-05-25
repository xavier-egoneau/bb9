from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bb9.core.archives import (
    ArchiveNotFoundError,
    discover_archives_any,
    discover_archives,
    load_archive,
    load_enabled_archives,
    parse_frontmatter,
    parse_markdown_name_list,
    read_archive_text,
    read_optional_text,
    valid_archive_name,
)
from bb9.core.agents import AgentNotFoundError, discover_agents, discover_subagents, load_agent, load_subagent
from bb9.core.skills import discover_skills, load_effective_skills, load_skill, parse_disabled_skills
from bb9.core.tools import discover_tools, load_tool, parse_disabled_tools


class MarkdownArchiveTests(unittest.TestCase):
    def test_discovers_named_archives_by_kind_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "beta" / "TOOL.md").write_text("# Beta\n", encoding="utf-8")
            (root / "alpha").mkdir()
            (root / "alpha" / "TOOL.md").write_text("# Alpha\n", encoding="utf-8")
            (root / "notes").mkdir()
            (root / "bad name").mkdir()
            (root / "bad name" / "TOOL.md").write_text("# Bad\n", encoding="utf-8")

            self.assertEqual(["alpha", "beta"], discover_archives(root, "TOOL.md"))

    def test_discovers_archives_by_any_marker_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "identity-only").mkdir()
            (root / "identity-only" / "IDENTITY.md").write_text("id\n", encoding="utf-8")
            (root / "model-only").mkdir()
            (root / "model-only" / "MODEL.md").write_text("model\n", encoding="utf-8")
            (root / "empty").mkdir()

            names = discover_archives_any(root, ("IDENTITY.md", "MODEL.md"))

            self.assertEqual(["identity-only", "model-only"], names)

    def test_loads_archive_body_and_minimal_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "demo"
            item.mkdir()
            item.joinpath("SKILL.md").write_text(
                "---\n"
                "activation: always\n"
                "description: \"Demo skill\"\n"
                "---\n\n"
                "# Demo\n\n"
                "## Résumé\n\n"
                "Capacité test.\n",
                encoding="utf-8",
            )

            archive = load_archive(root, "demo", "SKILL.md")

            self.assertEqual("demo", archive.name)
            self.assertEqual("always", archive.metadata["activation"])
            self.assertEqual("Demo skill", archive.metadata["description"])
            self.assertTrue(archive.body.startswith("# Demo"))

    def test_missing_or_invalid_archive_raises_common_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            with self.assertRaises(ArchiveNotFoundError):
                load_archive(root, "../nope", "TOOL.md")
            with self.assertRaises(ArchiveNotFoundError):
                load_archive(root, "nope", "TOOL.md")

    def test_load_enabled_archives_applies_disabled_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("alpha", "beta"):
                item = root / name
                item.mkdir()
                item.joinpath("TOOL.md").write_text(f"# {name}\n", encoding="utf-8")

            archives = load_enabled_archives(root, "TOOL.md", disabled=("beta",))

            self.assertEqual(("alpha",), tuple(archive.name for archive in archives))

    def test_parse_frontmatter_leaves_invalid_blocks_as_body(self) -> None:
        metadata, body = parse_frontmatter("---\nname: demo\n# no closing fence\n")

        self.assertEqual({}, metadata)
        self.assertTrue(body.startswith("---"))

    def test_parse_markdown_name_list_keeps_only_archive_names(self) -> None:
        text = "- `shell`\n- skill-two # note\n* bad/name\n- ok_name\n"

        self.assertEqual(("shell", "skill-two", "ok_name"), parse_markdown_name_list(text))

    def test_reads_optional_archive_files_with_name_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = root / "agent"
            item.mkdir()
            item.joinpath("SOUL.md").write_text("âme\n", encoding="utf-8")

            self.assertTrue(valid_archive_name("agent-one"))
            self.assertFalse(valid_archive_name("../agent"))
            self.assertEqual("âme\n", read_archive_text(root, "agent", "SOUL.md"))
            self.assertEqual("", read_archive_text(root, "../agent", "SOUL.md"))
            self.assertEqual("", read_optional_text(root / "missing.md"))

    def test_tools_and_skills_use_the_generic_archive_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool_dir = root / "tools" / "demo-tool"
            tool_dir.mkdir(parents=True)
            tool_dir.joinpath("TOOL.md").write_text(
                "# Demo\n\n## Résumé\n\nLire une chose.\n\n## Quand l'utiliser\n\n- Quand utile.\n",
                encoding="utf-8",
            )
            skill_dir = root / "skills" / "demo_skill"
            skill_dir.mkdir(parents=True)
            skill_dir.joinpath("SKILL.md").write_text(
                "---\nactivation: always\n---\n\n"
                "# Demo\n\n"
                "## Résumé\n\nFaire mieux.\n\n"
                "## Commandes\n\n- `/demo` : lancer demo.\n",
                encoding="utf-8",
            )

            tool = load_tool(root / "tools", "demo-tool")
            skill = load_skill(root / "skills", "demo_skill")

            self.assertEqual(["demo-tool"], discover_tools(root / "tools"))
            self.assertEqual("Lire une chose.", tool.summary)
            self.assertEqual(["demo_skill"], discover_skills(root / "skills"))
            self.assertEqual("always", skill.activation)
            self.assertEqual(("`/demo` : lancer demo.",), skill.commands)
            self.assertEqual(("demo-tool",), parse_disabled_tools("- `demo-tool`\n"))
            self.assertEqual(("demo_skill",), parse_disabled_skills("- `demo_skill`\n"))

    def test_local_skills_override_global_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_skill = root / "global" / "plan"
            local_skill = root / "workspace" / ".bb9" / "skills" / "plan"
            other_global = root / "global" / "dev"
            global_skill.mkdir(parents=True)
            local_skill.mkdir(parents=True)
            other_global.mkdir(parents=True)
            global_skill.joinpath("SKILL.md").write_text("# Plan\n\n## Résumé\n\nGlobal.\n", encoding="utf-8")
            local_skill.joinpath("SKILL.md").write_text("# Plan\n\n## Résumé\n\nLocal.\n", encoding="utf-8")
            other_global.joinpath("SKILL.md").write_text("# Dev\n\n## Résumé\n\nGlobal dev.\n", encoding="utf-8")

            skills = load_effective_skills(root / "global", root / "workspace" / ".bb9" / "skills")

            self.assertEqual(("dev", "plan"), tuple(skill.name for skill in skills))
            plan = next(skill for skill in skills if skill.name == "plan")
            self.assertEqual("Local.", plan.summary)
            self.assertEqual(root / "workspace" / ".bb9" / "skills", plan.root)

    def test_agents_and_subagents_share_archive_discovery_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            parent = agents / "main"
            child = parent / "subagents" / "research"
            child.mkdir(parents=True)
            parent.joinpath("IDENTITY.md").write_text("Nom : main\n", encoding="utf-8")
            parent.joinpath("SKILLS_DISABLED.md").write_text("- `nope`\n- bad/name\n", encoding="utf-8")
            child.joinpath("MODEL.md").write_text("Model : light\n", encoding="utf-8")
            (agents / "bad name").mkdir()
            (agents / "bad name" / "IDENTITY.md").write_text("bad\n", encoding="utf-8")

            self.assertEqual(["main"], discover_agents(agents))
            self.assertEqual(["research"], discover_subagents(agents, "main"))
            self.assertEqual(("nope",), load_agent(agents, "main").disabled_skills)
            self.assertEqual("light", load_subagent(agents, "main", "research").model)
            with self.assertRaises(AgentNotFoundError):
                load_agent(agents, "../main")


if __name__ == "__main__":
    unittest.main()
