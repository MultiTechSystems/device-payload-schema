#!/usr/bin/env python3
"""
generate_docs_index.py - Generate docs/INDEX.md, the repository index.

Produces a single machine- and agent-readable index of the repository: the
directory map, every document with its purpose and section headings, the tool
inventory, and the device schema inventory with quality tier and score.

The point is that a reader (human or agent) can answer "where does X live?" and
"which schemas need work?" from one file instead of opening 22 documents and
158 schemas.

Usage:
    python tools/generate_docs_index.py                 # write docs/INDEX.md
    python tools/generate_docs_index.py --no-scores     # skip quality scoring
    python tools/generate_docs_index.py --check         # fail if out of date
    python tools/generate_docs_index.py -o -            # write to stdout
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
TOOLS_DIR = REPO_ROOT / "tools"
SCHEMAS_DIR = REPO_ROOT / "schemas"
DEVICES_DIR = SCHEMAS_DIR / "devices"
DEFAULT_OUTPUT = DOCS_DIR / "INDEX.md"

#: One-line purpose for each top-level directory. Hand-maintained: a directory's
#: role cannot be derived from its contents, and this is the only place it is
#: written down.
DIRECTORY_PURPOSE = {
    "bindings": "Reference interpreters per language (c, go, java, node, python)",
    "build-system": "Shared make fragments for the C/C++ builds",
    "docs": "Documentation (this index is generated into it)",
    "dotnet": "C# interpreter and its test project",
    "examples": "Small illustrative schemas used by `make validate` and the C tests",
    "fuzz": "Fuzzing harnesses (Python, Go, C libFuzzer)",
    "go": "Go interpreter package",
    "include": "C/C++ headers, including the generated codec headers",
    "output": "Output-format converters (SenML, IPSO, TTN, WoT)",
    "proto": "Protocol buffer definitions of the schema language",
    "schemas": "Schema language JSON Schema, device schemas, shared library",
    "src": "C/C++ interpreter, self-tests and benchmarks",
    "tests": "Python test suite (pytest)",
    "tools": "Python/JS tooling: interpreter, validators, generators, converters",
}

#: Sections of the schema language that a schema may use, keyed by the marker
#: that identifies them when scanning a device schema.
LAYOUT_MARKERS = (
    ("ports", "ports"),
    ("tlv", "tlv"),
    ("match", "match"),
    ("flagged", "flagged"),
    ("byte_group", "byte_group"),
    ("repeat", "repeat"),
)


#: Front-matter style lines ("**Generated:** ...", "Status: ...") describe the
#: document's metadata rather than its subject, so they make poor summaries.
_METADATA_LINE = re.compile(
    r"^\**(generated|date|updated|status|version|author|spec)\**\s*:", re.IGNORECASE
)


def first_paragraph(text: str) -> str:
    """Return the first prose paragraph of a markdown document, unwrapped.

    Headings, metadata lines and list/table markup are skipped so the summary
    describes what the document is about.
    """
    collected: List[str] = []
    in_fence = False
    # Only the lede is a summary. Prose found deep in a reference document is
    # some incidental sentence, not what the document is about.
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped or stripped.startswith("#"):
            if collected:
                break
            continue
        if _METADATA_LINE.match(stripped) or stripped[0] in "|-*>":
            if collected:
                break
            continue
        # Drop emphasis and code markers but keep underscores: they are part of
        # identifiers such as verify_spec_completeness.py.
        collected.append(re.sub(r"[*`]", "", stripped))
    return " ".join(collected).rstrip(":")


def truncate(text: str, limit: int = 100) -> str:
    """Shorten text to limit characters, breaking on a word boundary."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def escape_cell(text: str) -> str:
    """Escape a value for use inside a markdown table cell."""
    return text.replace("|", r"\|")


def document_entries() -> List[Dict[str, Any]]:
    """Collect title, summary, size and top-level sections for each document."""
    entries = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        if path.name == DEFAULT_OUTPUT.name:
            continue
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        sections = re.findall(r"^##\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        # Reference-style documents (tables only) have no prose to summarise.
        summary = first_paragraph(text) or title
        entries.append(
            {
                "name": path.name,
                "title": title,
                "summary": truncate(summary),
                "lines": len(text.splitlines()),
                "sections": [s.strip() for s in sections],
            }
        )
    return entries


def tool_entries() -> List[Dict[str, str]]:
    """Collect the first docstring line of every tool script."""
    entries = []
    for path in sorted(TOOLS_DIR.iterdir()):
        if path.suffix not in (".py", ".js") or path.name.startswith("_"):
            continue
        if path.name == Path(__file__).name:
            purpose = "Generate docs/INDEX.md (this file)"
        else:
            purpose = script_purpose(path)
        entries.append({"name": path.name, "purpose": truncate(purpose, 90)})
    return entries


def script_purpose(path: Path) -> str:
    """Extract a one-line purpose from a script's leading comment or docstring."""
    text = path.read_text(encoding="utf-8", errors="replace")
    docstring = re.search(r'"""(.*?)"""', text, re.DOTALL)
    if docstring:
        for line in docstring.group(1).splitlines():
            stripped = line.strip()
            # Skip a repeated "name.py - " prefix line only if it carries no text.
            if not stripped:
                continue
            return re.sub(r"^%s\s*[-:]\s*" % re.escape(path.name), "", stripped)
    for line in text.splitlines():
        if line.startswith("//") or line.startswith("#"):
            stripped = line.lstrip("/# ").strip()
            if stripped and not stripped.startswith("!"):
                return stripped
    return ""


def count_fields(node: Any) -> int:
    """Count leaf field definitions anywhere in a schema, including branches."""
    total = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "name" and isinstance(value, str):
                total += 1
            else:
                total += count_fields(value)
    elif isinstance(node, list):
        for item in node:
            total += count_fields(item)
    return total


def detect_constructs(schema: Dict[str, Any]) -> List[str]:
    """Return the layout/branching constructs a schema uses."""
    text = yaml.safe_dump(schema)
    found = []
    for marker, label in LAYOUT_MARKERS:
        if re.search(r"^\s*%s:" % re.escape(marker), text, re.MULTILINE):
            found.append(label)
    return found or ["fields"]


def load_scorer():
    """Import the scoring tool, or return None when it cannot be used."""
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        import score_schema

        return score_schema
    except Exception as exc:  # pragma: no cover - environment dependent
        print("warning: scoring unavailable (%s)" % exc, file=sys.stderr)
        return None


def schema_entries(with_scores: bool) -> List[Dict[str, Any]]:
    """Collect vendor, constructs, vector count and quality tier per schema."""
    scorer = load_scorer() if with_scores else None
    entries = []
    for path in sorted(DEVICES_DIR.rglob("*.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rel = path.relative_to(REPO_ROOT)
        entry = {
            "path": rel.as_posix(),
            "vendor": path.parent.name,
            "model": path.stem,
            "name": schema.get("name", ""),
            "fields": count_fields(schema.get("fields", schema.get("ports", {}))),
            "vectors": len(schema.get("test_vectors", []) or []),
            "constructs": detect_constructs(schema),
            "score": None,
            "tier": "UNSCORED",
        }
        if scorer is not None:
            result = scorer.score_schema(str(path))
            entry["score"] = result.score
            entry["tier"] = result.tier
        entries.append(entry)
    return entries


def render_directory_map() -> List[str]:
    """Render the top-level directory table."""
    lines = ["## Repository map", "", "| Path | Contents |", "|---|---|"]
    for name in sorted(DIRECTORY_PURPOSE):
        if (REPO_ROOT / name).exists():
            lines.append("| `%s/` | %s |" % (name, DIRECTORY_PURPOSE[name]))
    lines.append("")
    return lines


def render_documents(entries: List[Dict[str, Any]]) -> List[str]:
    """Render the document table followed by per-document section lists."""
    lines = [
        "## Documents",
        "",
        "%d documents in `docs/`. Read the one that matches the task; the"
        " sections list below tells you what is inside without opening it."
        % len(entries),
        "",
        "| Document | Purpose | Lines |",
        "|---|---|---|",
    ]
    for entry in entries:
        lines.append(
            "| [`%s`](%s) | %s | %d |"
            % (entry["name"], entry["name"], escape_cell(entry["summary"]), entry["lines"])
        )
    lines.extend(["", "### Document sections", ""])
    for entry in entries:
        if not entry["sections"]:
            continue
        shown = entry["sections"][:12]
        suffix = " ..." if len(entry["sections"]) > len(shown) else ""
        lines.append(
            "- **%s** — %s%s" % (entry["name"], "; ".join(shown), suffix)
        )
    lines.append("")
    return lines


def render_tools(entries: List[Dict[str, str]]) -> List[str]:
    """Render the tool inventory table."""
    lines = ["## Tools", "", "| Script | Purpose |", "|---|---|"]
    for entry in entries:
        lines.append(
            "| `tools/%s` | %s |" % (entry["name"], escape_cell(entry["purpose"]))
        )
    lines.append("")
    return lines


def render_schema_summary(entries: List[Dict[str, Any]]) -> List[str]:
    """Render totals, the per-vendor tier breakdown and the per-device table."""
    tiers = (
        "PLATINUM", "GOLD", "SILVER", "BRONZE", "REJECTED", "FAILED", "UNSCORED",
    )
    scored = [e for e in entries if e["score"] is not None]
    lines = ["## Device schemas", ""]
    lines.append("%d schemas under `schemas/devices/`." % len(entries))
    if scored:
        mean = sum(e["score"] for e in scored) / len(scored)
        counts = {t: sum(1 for e in entries if e["tier"] == t) for t in tiers}
        lines.append(
            "Mean quality score %.1f%% (%s). Tiers follow the specification's"
            " Section 10: Platinum 95-100%%, Gold 85-94%%, Silver 70-84%%,"
            " Bronze 60-69%%, Rejected below 60%%. Gold and Platinum also have"
            " gates (PS-239) -- see `../AGENTS.md`. A high score shows"
            " self-consistency with a schema's own test vectors, not that the"
            " vectors are right."
            % (mean, ", ".join("%s %d" % (t, counts[t]) for t in tiers if counts[t]))
        )
    lines.extend(["", "### By vendor", "", "| Vendor | Schemas | Vectors | Tiers |", "|---|---|---|---|"])
    vendors: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        vendors.setdefault(entry["vendor"], []).append(entry)
    for vendor in sorted(vendors, key=lambda v: (-len(vendors[v]), v)):
        group = vendors[vendor]
        breakdown = {t: sum(1 for e in group if e["tier"] == t) for t in tiers}
        lines.append(
            "| %s | %d | %d | %s |"
            % (
                vendor,
                len(group),
                sum(e["vectors"] for e in group),
                ", ".join("%s %d" % (t, n) for t, n in breakdown.items() if n) or "-",
            )
        )
    lines.extend(
        [
            "",
            "### By device",
            "",
            "`vectors` is the number of `test_vectors` entries: 0 means the schema"
            " has never been verified against a known payload.",
            "",
            "| Schema | Fields | Vectors | Constructs | Score | Tier |",
            "|---|---|---|---|---|---|",
        ]
    )
    for entry in sorted(entries, key=lambda e: e["path"]):
        score = "-" if entry["score"] is None else "%.0f%%" % entry["score"]
        lines.append(
            "| `%s/%s` | %d | %d | %s | %s | %s |"
            % (
                entry["vendor"],
                entry["model"],
                entry["fields"],
                entry["vectors"],
                ", ".join(entry["constructs"]),
                score,
                entry["tier"],
            )
        )
    lines.append("")
    return lines


def render_other_schemas() -> List[str]:
    """Render the non-device schema files (language schema, outputs, library)."""
    lines = ["## Other schema files", "", "| Path | Role |", "|---|---|"]
    roles = {
        "payload-schema.json": "JSON Schema for the payload schema language itself",
        "ipso-output.schema.json": "JSON Schema for IPSO-shaped decoder output",
        "senml-output.schema.json": "JSON Schema for SenML-shaped decoder output",
        "ttn-output.schema.json": "JSON Schema for TTN-shaped decoder output",
    }
    for path in sorted(SCHEMAS_DIR.glob("*.json")):
        lines.append("| `schemas/%s` | %s |" % (path.name, roles.get(path.name, "")))
    library = SCHEMAS_DIR / "library"
    if library.exists():
        count = len(list(library.rglob("*.yaml")))
        lines.append(
            "| `schemas/library/` | %d reusable schema fragments (`use:` targets) |"
            % count
        )
    lines.append("")
    return lines


def build_index(with_scores: bool) -> str:
    """Assemble the whole index document."""
    docs = document_entries()
    tools = tool_entries()
    schemas = schema_entries(with_scores)
    lines = [
        "# Repository index",
        "",
        "<!-- GENERATED FILE - do not edit by hand."
        " Regenerate: python tools/generate_docs_index.py -->",
        "",
        "Generated inventory of this repository: what lives where, what each"
        " document covers, what each tool does, and the state of every device"
        " schema. See [`../AGENTS.md`](../AGENTS.md) for how to work in this"
        " repository.",
        "",
    ]
    lines.extend(render_directory_map())
    lines.extend(render_documents(docs))
    lines.extend(render_tools(tools))
    lines.extend(render_schema_summary(schemas))
    lines.extend(render_other_schemas())
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate docs/INDEX.md")
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="output path, or - for stdout (default: docs/INDEX.md)",
    )
    parser.add_argument(
        "--no-scores",
        action="store_true",
        help="skip quality scoring (faster, omits score/tier columns)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed index is out of date",
    )
    args = parser.parse_args()

    content = build_index(with_scores=not args.no_scores)

    if args.check:
        existing = ""
        if DEFAULT_OUTPUT.exists():
            existing = DEFAULT_OUTPUT.read_text(encoding="utf-8")
        if existing != content:
            print(
                "docs/INDEX.md is out of date - run:"
                " python tools/generate_docs_index.py",
                file=sys.stderr,
            )
            return 1
        print("docs/INDEX.md is up to date")
        return 0

    if args.output == "-":
        sys.stdout.write(content)
    else:
        out_path = Path(args.output)
        out_path.write_text(content, encoding="utf-8")
        print("Wrote %s (%d lines)" % (out_path, len(content.splitlines())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
