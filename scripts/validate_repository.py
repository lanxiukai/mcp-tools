"""Validate local Markdown links and GitHub YAML syntax."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = frozenset(
    {".git", ".runtime", ".venv", "mcp-tool-test", "mcp_test", "node_modules"}
)


def repository_files(pattern: str) -> list[Path]:
    return sorted(
        path
        for path in REPOSITORY_ROOT.rglob(pattern)
        if not IGNORED_PARTS.intersection(path.relative_to(REPOSITORY_ROOT).parts)
    )


def validate_markdown_links() -> int:
    parser = MarkdownIt()
    failures: list[str] = []
    markdown_files = repository_files("*.md")
    for source in markdown_files:
        for token in parser.parse(source.read_text(encoding="utf-8")):
            if token.type != "inline":
                continue
            for child in token.children or ():
                if child.type not in {"link_open", "image"}:
                    continue
                attribute = "href" if child.type == "link_open" else "src"
                target_value = child.attrGet(attribute)
                if not target_value or target_value.startswith("#"):
                    continue
                parsed = urlsplit(target_value)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (source.parent / unquote(parsed.path)).resolve()
                if not target.exists():
                    relative_source = source.relative_to(REPOSITORY_ROOT)
                    failures.append(f"{relative_source}: {target_value}")

    if failures:
        joined = "\n".join(failures)
        raise RuntimeError(f"Missing local Markdown targets:\n{joined}")
    return len(markdown_files)


def validate_github_yaml() -> int:
    yaml_files = sorted(
        [
            *REPOSITORY_ROOT.glob(".github/**/*.yml"),
            *REPOSITORY_ROOT.glob(".github/**/*.yaml"),
        ]
    )
    for source in yaml_files:
        document = yaml.load(source.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        if not isinstance(document, dict):
            raise RuntimeError(f"GitHub YAML must contain a mapping: {source}")
        relative = source.relative_to(REPOSITORY_ROOT)
        if ".github/workflows" in str(relative.parent):
            missing = {"name", "on", "jobs"} - set(document)
            if missing:
                raise RuntimeError(
                    f"Workflow {relative} is missing: {', '.join(sorted(missing))}"
                )
        elif relative.parent == Path(".github/ISSUE_TEMPLATE") and source.suffix in {
            ".yml",
            ".yaml",
        }:
            missing = {"name", "description", "body"} - set(document)
            if missing:
                raise RuntimeError(
                    f"Issue form {relative} is missing: {', '.join(sorted(missing))}"
                )
    return len(yaml_files)


def main() -> int:
    markdown_count = validate_markdown_links()
    yaml_count = validate_github_yaml()
    print(
        f"Repository metadata: {markdown_count} Markdown files and {yaml_count} YAML files OK"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
