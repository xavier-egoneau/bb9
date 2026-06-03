"""Markdown/ANSI rendering for the CLI surface."""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..core.models import Artifact


class CliTheme:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\033[{code}m{text}\033[0m"

    def accent(self, text: str) -> str:
        return self._wrap("38;5;208;1", text)

    def logo(self, text: str) -> str:
        return self._wrap("38;5;202;1", text)

    def logo_line(self, line: str, row: int) -> str:
        if not self.enabled or not line:
            return line
        palette = [
            (172, 130),
            (166, 130),
            (166, 94),
            (130, 94),
            (130, 94),
            (94, 58),
        ]
        bright, dim = palette[min(row, len(palette) - 1)]
        result: list[str] = []
        for ch in line:
            cp = ord(ch)
            if ch == " " or cp == 0x2800:
                result.append(ch)
            elif 0x2800 < cp <= 0x28FF:
                dots = bin(cp - 0x2800).count("1")
                if dots >= 5:
                    result.append(f"\033[38;5;{bright};1m{ch}\033[0m")
                else:
                    result.append(f"\033[38;5;{dim}m{ch}\033[0m")
            elif ch == "\u2588":
                result.append(f"\033[38;5;{bright};1m{ch}\033[0m")
            else:
                result.append(f"\033[38;5;{dim}m{ch}\033[0m")
        return "".join(result)

    def title(self, text: str) -> str:
        return self._wrap("38;5;214;1", text)

    def command(self, text: str) -> str:
        return self._wrap("38;5;208;1", text)

    def keyword(self, text: str) -> str:
        return self._wrap("38;5;81;1", text)

    def string(self, text: str) -> str:
        return self._wrap("38;5;114", text)

    def number(self, text: str) -> str:
        return self._wrap("38;5;141", text)

    def comment(self, text: str) -> str:
        return self._wrap("38;5;244", text)

    def dim(self, text: str) -> str:
        return self._wrap("38;5;94", text)

    def border(self, text: str) -> str:
        return self._wrap("38;5;94", text)


def render_cli_markdown(text: str, theme: CliTheme) -> str:
    if not theme.enabled:
        return text
    lines: list[str] = []
    in_fence = False
    fence_label = ""
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                fence_label = stripped.removeprefix("```").strip()
                label = f" {fence_label}" if fence_label else ""
                lines.append(theme.border(f"\u256d\u2500 code{label}"))
                in_fence = True
            else:
                lines.append(theme.border("\u2570\u2500"))
                in_fence = False
                fence_label = ""
            continue
        if in_fence:
            lines.append(theme.border("\u2502 ") + _highlight_code(raw_line, fence_label, theme))
            continue
        lines.append(_render_markdown_line(raw_line, theme))
    if in_fence:
        lines.append(theme.border("\u2570\u2500"))
    return "\n".join(lines)


def render_cli_diff_artifact(artifact: Artifact, theme: CliTheme, *, limit: int = 8) -> str:
    if artifact.kind != "diff":
        return ""
    title = artifact.title or _diff_artifact_title(artifact.metadata)
    lines = [theme.dim(f"diff... {title}")]
    files = artifact.metadata.get("files")
    if isinstance(files, list):
        for item in files[: max(0, limit)]:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            status = str(item.get("status") or "").strip()
            status_part = f" ({status})" if status else ""
            insertions = _metadata_int(item.get("insertions"))
            deletions = _metadata_int(item.get("deletions"))
            lines.append(theme.dim(f"  {path}{status_part} +{insertions}/-{deletions}"))
        if len(files) > limit:
            lines.append(theme.dim(f"  ... {len(files) - limit} fichier(s) de plus dans /history"))
    if artifact.path:
        lines.append(theme.dim(f"  patch... {artifact.path}"))
    return "\n".join(lines)


def _diff_artifact_title(metadata: dict[str, object]) -> str:
    files = _metadata_int(metadata.get("files_changed"))
    insertions = _metadata_int(metadata.get("insertions"))
    deletions = _metadata_int(metadata.get("deletions"))
    suffix = "fichier modifi\u00e9" if files == 1 else "fichiers modifi\u00e9s"
    return f"{files} {suffix} (+{insertions}/-{deletions})"


def _metadata_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else 0


def _render_markdown_line(line: str, theme: CliTheme) -> str:
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped:
        return ""
    heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if heading:
        level = len(heading.group(1))
        marker = "\u2501" if level <= 2 else "\u2500"
        return theme.title(f"{marker} {_render_inline_markdown(heading.group(2), theme)}")
    quote = re.match(r"^>\s?(.*)$", stripped)
    if quote:
        return indent + theme.dim("\u2502 " + _render_inline_markdown(quote.group(1), theme))
    task = re.match(r"^[-*]\s+\[( |x|X)\]\s+(.+)$", stripped)
    if task:
        checked = task.group(1).lower() == "x"
        box = theme.accent("[x]") if checked else theme.dim("[ ]")
        return indent + box + " " + _render_inline_markdown(task.group(2), theme)
    bullet = re.match(r"^([-*])\s+(.+)$", stripped)
    if bullet:
        return indent + theme.accent("\u2022") + " " + _render_inline_markdown(bullet.group(2), theme)
    numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
    if numbered:
        return indent + theme.accent(numbered.group(1) + ".") + " " + _render_inline_markdown(numbered.group(2), theme)
    return indent + _render_inline_markdown(stripped, theme)


def _render_inline_markdown(text: str, theme: CliTheme) -> str:
    rendered = re.sub(r"`([^`]+)`", lambda match: theme.command(match.group(1)), text)
    rendered = re.sub(r"\*\*([^*]+)\*\*", lambda match: theme.title(match.group(1)), rendered)
    rendered = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda match: theme.accent(match.group(1)), rendered)
    return rendered


def _highlight_code(line: str, language: str, theme: CliTheme) -> str:
    lang = _normalize_language(language)
    if lang in {"javascript", "typescript", "python", "bash", "json"}:
        return _highlight_code_tokens(line, theme, lang)
    return line


def _highlight_code_tokens(line: str, theme: CliTheme, language: str) -> str:
    tokens = _CODE_TOKEN_RE.split(line)
    rendered: list[str] = []
    for token in tokens:
        if not token:
            continue
        rendered.append(_highlight_token(token, theme, language))
    return "".join(rendered)


def _highlight_token(token: str, theme: CliTheme, language: str) -> str:
    if token.startswith(("//", "#")):
        return theme.comment(token)
    if token.startswith(("'", '"', "`")):
        return theme.string(token)
    if re.fullmatch(r"\b\d+(?:\.\d+)?\b", token):
        return theme.number(token)
    if token in _KEYWORDS.get(language, set()):
        return theme.keyword(token)
    if language == "json" and token in {"true", "false", "null"}:
        return theme.keyword(token)
    return token


def _normalize_language(language: str) -> str:
    lang = str(language or "").strip().lower()
    aliases = {
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "py": "python",
        "sh": "bash",
        "shell": "bash",
        "zsh": "bash",
    }
    return aliases.get(lang, lang)


def supports_color() -> bool:
    return (
        sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM", "") != "dumb"
    )


_CODE_TOKEN_RE = re.compile(
    r"(//.*$|#.*$|`(?:\\.|[^`])*`|'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\"|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][A-Za-z0-9_]*\b)"
)

_KEYWORDS: dict[str, set[str]] = {
    "javascript": {
        "async", "await", "break", "case", "catch", "class", "const", "continue",
        "default", "else", "export", "extends", "false", "finally", "for",
        "function", "if", "import", "let", "new", "null", "return", "switch",
        "this", "throw", "true", "try", "typeof", "var", "while",
    },
    "typescript": {
        "async", "await", "break", "case", "catch", "class", "const", "continue",
        "default", "else", "export", "extends", "false", "finally", "for",
        "function", "if", "import", "interface", "let", "new", "null", "private",
        "public", "readonly", "return", "switch", "this", "throw", "true", "try",
        "type", "typeof", "var", "while",
    },
    "python": {
        "and", "as", "async", "await", "break", "class", "continue", "def",
        "elif", "else", "except", "False", "finally", "for", "from", "if",
        "import", "in", "is", "lambda", "None", "not", "or", "pass", "raise",
        "return", "True", "try", "while", "with", "yield",
    },
    "bash": {
        "case", "do", "done", "elif", "else", "esac", "fi", "for", "function",
        "if", "in", "then", "while",
    },
    "json": set(),
}


class CliActivityIndicator:
    def __init__(self, theme: CliTheme, text: str, *, interval: float = 0.12) -> None:
        self.theme = theme
        self.text = text
        self.interval = interval
        self.frames = ("\u00b7", "\u2022", "\u25cf", "\u2022")
        self.enabled = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        with self._lock:
            self._clear_line()

    def set_text(self, text: str) -> None:
        with self._lock:
            self.text = text

    def interrupt(self, writer: Callable[[], None]) -> None:
        if not self.enabled:
            writer()
            return
        with self._lock:
            self._clear_line()
            writer()

    @contextmanager
    def paused(self) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        with self._lock:
            self._clear_line()
            yield

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            with self._lock:
                frame = self.frames[index % len(self.frames)]
                self._write_frame(frame)
            index += 1
            self._stop.wait(self.interval)

    def _write_frame(self, frame: str) -> None:
        label = self.theme.dim(f"{frame} {self.text}")
        sys.stdout.write("\r\033[K" + label)
        sys.stdout.flush()

    def _clear_line(self) -> None:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()


def banner_width() -> int:
    columns = shutil.get_terminal_size((88, 24)).columns
    return max(54, min(columns - 2, 98))


import shutil  # noqa: E402


def bb9_logo() -> tuple[str, ...]:
    return (
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557   \u2588\u2588\u2588\u2588\u2588\u2557 ",
        "\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d \u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2551",
        "\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557  \u255a\u2550\u2550\u2550\u2588\u2588\u2551",
        "\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d  \u2588\u2588\u2588\u2588\u2588\u2554\u255d",
        "\u255a\u2550\u2550\u2550\u2550\u2550\u255d  \u255a\u2550\u2550\u2550\u2550\u2550\u255d   \u255a\u2550\u2550\u2550\u2550\u255d ",
    )


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def strip_ansi(text: str) -> str:
    result = []
    index = 0
    while index < len(text):
        if text[index:index + 2] == "\033[":
            index += 2
            while index < len(text) and text[index] != "m":
                index += 1
            index += 1
            continue
        result.append(text[index])
        index += 1
    return "".join(result)


def truncate_visible(text: str, width: int) -> str:
    plain = strip_ansi(text)
    if len(plain) <= width:
        return text
    if width <= 1:
        return plain[:width]
    return plain[: width - 1] + "\u2026"


def pad_visible(text: str, width: int) -> str:
    visible = visible_len(text)
    if visible >= width:
        return truncate_visible(text, width)
    return text + " " * (width - visible)


def fit_words(text: str, width: int) -> str:
    plain = " ".join(strip_ansi(str(text or "")).split())
    if width <= 0:
        return ""
    if len(plain) <= width:
        return plain
    if width <= 1:
        return "\u2026"
    words = plain.split()
    fitted = ""
    for word in words:
        candidate = word if not fitted else f"{fitted} {word}"
        if len(candidate) > width - 1:
            break
        fitted = candidate
    if fitted:
        return fitted.rstrip(" ,.;:") + "\u2026"
    return plain[: max(1, width - 1)] + "\u2026"


def short_message(text: str, limit: int = 64) -> str:
    plain = " ".join(text.split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."


def live_tool_summary(tool: str, summary: str, limit: int = 64) -> str:
    plain = " ".join(str(summary or "").split())
    lowered = plain[:200].lower()
    if tool == "shell" and (lowered.startswith("<!doctype") or lowered.startswith("<html") or "<html" in lowered[:80]):
        return "sortie HTML recue"
    return short_message(plain, limit=limit)


def short_index_names(index: str, limit: int = 6) -> str:
    names: list[str] = []
    for line in index.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- `"):
            continue
        rest = stripped[3:]
        name, _, _ = rest.partition("`")
        if name:
            names.append(name)
        if len(names) >= limit:
            break
    return ", ".join(names)


def archive_command_parts(line: str) -> tuple[str, str]:
    text = line.strip()
    if text.startswith("`"):
        raw, _, rest = text[1:].partition("`")
        command = _display_command(raw.strip())
        description = rest.strip(" :-")
        return command, description
    command, _, rest = text.partition(" ")
    return command.strip(), rest.strip(" :-")


def _display_command(command: str) -> str:
    if command.endswith(" ..."):
        command = command[:-4].strip()
    return command.split(maxsplit=1)[0]
