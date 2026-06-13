#!/usr/bin/env python3
"""
Migre les fichiers utilisateur ~/.bb9/agents/ et ~/.bb9/skills/ :
- IDENTITY.md : ajoute "Description :" après "Nom :" si absent
- SKILL.md    : ajoute frontmatter name+description si absent

Usage :
    python3 scripts/migrate_user_descriptions.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

BB9_HOME = Path.home() / ".bb9"
AGENTS_DIR = BB9_HOME / "agents"
SKILLS_DIR = BB9_HOME / "skills"


def has_field(text: str, label: str) -> bool:
    nl = _normalize(label)
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, _ = line.partition(":")
        if _normalize(key) == nl:
            return True
    return False


def _normalize(text: str) -> str:
    tr = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return " ".join(text.lower().translate(tr).split())


def field_value(text: str, label: str) -> str:
    nl = _normalize(label)
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if _normalize(key) == nl:
            return value.strip()
    return ""


def migrate_identity(path: Path, dry_run: bool) -> bool:
    """Ajoute 'Description :' après 'Nom :' si absent."""
    text = path.read_text(encoding="utf-8")
    if has_field(text, "Description"):
        return False  # déjà présent
    lines = text.splitlines(keepends=True)
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if not inserted and re.match(r"\s*Nom\s*:", line):
            new_lines.append("Description :\n")
            inserted = True
    if not inserted:
        # Pas de "Nom :" trouvé, on insère après le premier header
        new_lines = []
        header_seen = False
        for line in lines:
            new_lines.append(line)
            if not header_seen and line.startswith("#"):
                header_seen = True
                new_lines.append("\nDescription :\n")
                inserted = True
    if not inserted:
        new_lines.insert(0, "Description :\n\n")
    new_text = "".join(new_lines)
    print(f"  {'[dry-run] ' if dry_run else ''}IDENTITY {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def migrate_skill(path: Path, dry_run: bool) -> bool:
    """Ajoute frontmatter name+description si absent."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        # Frontmatter existe, vérifier name/description
        end = text.find("\n---", 3)
        if end == -1:
            return False
        frontmatter = text[3:end]
        has_name = "name:" in frontmatter
        has_desc = "description:" in frontmatter
        if has_name and has_desc:
            return False
        # Ajouter les champs manquants dans le frontmatter existant
        slug = path.parent.name
        additions = ""
        if not has_name:
            additions += f"name: {slug}\n"
        if not has_desc:
            additions += "description: \n"
        new_fm = "---\n" + additions + frontmatter.lstrip("\n") + "\n---"
        new_text = new_fm + text[end + 4:]
    else:
        slug = path.parent.name
        header = f"---\nname: {slug}\ndescription: \n---\n\n"
        new_text = header + text

    print(f"  {'[dry-run] ' if dry_run else ''}SKILL    {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans modifier")
    args = parser.parse_args()

    changed = 0

    # Agents
    if AGENTS_DIR.exists():
        print(f"\n=== Agents ({AGENTS_DIR}) ===")
        for identity in sorted(AGENTS_DIR.glob("*/IDENTITY.md")):
            if migrate_identity(identity, args.dry_run):
                changed += 1
    else:
        print(f"Pas de dossier agents : {AGENTS_DIR}")

    # Skills
    if SKILLS_DIR.exists():
        print(f"\n=== Skills ({SKILLS_DIR}) ===")
        for skill in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            if migrate_skill(skill, args.dry_run):
                changed += 1
    else:
        print(f"Pas de dossier skills : {SKILLS_DIR}")

    print(f"\n{'[dry-run] ' if args.dry_run else ''}{changed} fichier(s) modifié(s).")
    if args.dry_run and changed:
        print("Relancer sans --dry-run pour appliquer.")


if __name__ == "__main__":
    main()
