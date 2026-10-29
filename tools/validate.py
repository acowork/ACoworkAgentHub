#!/usr/bin/env python3
"""Validate community agent directories against the repo's contracts.

Checks, in order of cost:

  1. schema   - every manifest.toml validates against schema/manifest.schema.json
  2. catalog  - schema enums and schema/catalog.json agree (no taxonomy drift)
  3. layout   - directory shape, required files, skill layout, naming rules
  4. hygiene  - no binaries, no index.json, no stray secrets

Usage:
    python tools/validate.py                 # validate every agent
    python tools/validate.py agents/foo/bar  # validate specific directories
    python tools/validate.py --strict        # also fail on warnings

Exit codes:
    0  ok
    1  validation errors
    2  environment error (missing python 3.11+, missing schema)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("[validate] FATAL: needs Python 3.11+ (tomllib). Exit 2")

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schema"
AGENTS_DIR = REPO_ROOT / "agents"

#: Directories under agents/ that are not agents.
TEMPLATE_DIRS = {"_template"}

#: Binary/artifact extensions that must never be committed.
FORBIDDEN_SUFFIXES = {
    ".agent", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".wasm",
    ".png-index", ".pyc",
}

OWNER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"[validate] FATAL: {path} not found. Exit 2")
    except json.JSONDecodeError as exc:
        sys.exit(f"[validate] FATAL: {path} is not valid JSON: {exc}. Exit 2")


def load_manifest(agent_dir: Path, report: Report) -> dict | None:
    manifest_path = agent_dir / "manifest.toml"
    try:
        return tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, do not crash the run
        report.error(str(agent_dir), f"cannot parse manifest.toml: {exc}")
        return None


def check_catalog_drift(schema: dict, catalog: dict, report: Report) -> None:
    """schema/manifest.schema.json and schema/catalog.json must not drift."""
    props = schema.get("properties", {})

    schema_categories = set(props.get("category", {}).get("enum", []))
    catalog_categories = set(catalog.get("categories", {}).keys())
    if schema_categories != catalog_categories:
        only_schema = sorted(schema_categories - catalog_categories)
        only_catalog = sorted(catalog_categories - schema_categories)
        report.error(
            "schema",
            "category enum differs from schema/catalog.json "
            f"(only in manifest schema: {only_schema}; only in catalog: {only_catalog})",
        )

    schema_tiers = set(props.get("tier", {}).get("enum", []))
    catalog_tiers = set(catalog.get("tiers", {}).keys())
    if schema_tiers != catalog_tiers:
        report.error(
            "schema",
            "tier enum differs from schema/catalog.json "
            f"(only in manifest schema: {sorted(schema_tiers - catalog_tiers)}; "
            f"only in catalog: {sorted(catalog_tiers - schema_tiers)})",
        )

    schema_tags = set(props.get("tags", {}).get("items", {}).get("enum", []))
    catalog_tags = set(catalog.get("tags", []))
    if schema_tags != catalog_tags:
        report.error(
            "schema",
            "tag vocabulary differs from schema/catalog.json "
            f"(only in manifest schema: {sorted(schema_tags - catalog_tags)}; "
            f"only in catalog: {sorted(catalog_tags - schema_tags)})",
        )


def check_manifest_schema(manifest: dict, schema: dict, where: str, report: Report) -> None:
    try:
        import jsonschema
    except ModuleNotFoundError:
        report.warn(
            where,
            "jsonschema is not installed - schema validation skipped "
            "(pip install jsonschema to enable it)",
        )
        return

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    for err in errors:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        report.error(where, f"schema: {loc}: {err.message}")


def check_layout(agent_dir: Path, owner: str, name: str, manifest: dict | None,
                 report: Report, is_template: bool = False) -> None:
    where = f"agents/{owner}/{name}"

    if not is_template:
        if not OWNER_RE.match(owner):
            report.error(where, f"owner segment {owner!r} must be lowercase kebab-case")
        if not NAME_RE.match(name):
            report.error(where, f"name segment {name!r} must be lowercase kebab-case")

    if not (agent_dir / "README.md").is_file():
        report.error(where, "README.md is required")
    if not (agent_dir / "LICENSE").is_file():
        report.warn(where, "no LICENSE file - the agent inherits the repo MIT license by default")

    if manifest is not None:
        declared = manifest.get("name")
        if declared and declared != name:
            report.error(where, f"manifest name {declared!r} does not match directory name {name!r}")
        agent_id = manifest.get("agent_id", "")
        if "." not in agent_id:
            report.error(where, f"agent_id {agent_id!r} must be reverse-DNS style (e.g. io.github.you.agent)")
        elif agent_id.split(".")[0] in {"com", "io", "org", "net", "dev"} and len(agent_id.split(".")) >= 3:
            pass  # conventional reverse-DNS shape
        else:
            report.warn(where, f"agent_id {agent_id!r} does not look like reverse-DNS")

        avatar = manifest.get("avatar")
        if avatar:
            avatar_path = agent_dir / avatar
            if not avatar_path.is_file():
                report.error(where, f"avatar {avatar!r} declared but missing on disk")
            elif avatar_path.suffix.lower() not in {".png", ".svg"}:
                report.error(where, f"avatar {avatar!r} must be .png or .svg")

    skills_dir = agent_dir / "skills"
    if skills_dir.is_dir():
        for skill in sorted(skills_dir.iterdir()):
            if not skill.is_dir():
                report.error(where, f"skills/{skill.name} must be a directory")
                continue
            if not (skill / "SKILL.md").is_file():
                report.error(where, f"skills/{skill.name}/SKILL.md is missing")
            if not NAME_RE.match(skill.name):
                report.warn(where, f"skills/{skill.name} name should be lowercase kebab-case")
    else:
        report.warn(where, "no skills/ directory - agents normally ship at least one skill")


def check_tier_consistency(agent_dir: Path, manifest: dict, catalog: dict,
                          report: Report) -> None:
    """The declared tier must match the declared permissions and skill count.

    Without this, a publisher could advertise `core` (no shell-out) while
    shipping `Shell` permissions - the label would be meaningless. The rules
    live in schema/catalog.json so the validator and the UI cannot disagree.
    """
    where = f"agents/{agent_dir.parent.name}/{agent_dir.name}"
    tier = manifest.get("tier")
    spec = catalog.get("tiers", {}).get(tier)
    if spec is None:
        return
    constraints = spec.get("constraints", {})

    permissions = {p.get("type") for p in manifest.get("permissions", []) if p.get("type")}
    forbidden = set(constraints.get("forbidden_permissions", []))
    offending = sorted(permissions & forbidden)
    if offending:
        report.error(
            where,
            f"tier {tier!r} forbids {forbidden} but the manifest declares {offending}",
        )

    max_skills = constraints.get("max_skills")
    if max_skills is not None:
        skills_dir = agent_dir / "skills"
        count = 0
        if skills_dir.is_dir():
            count = sum(1 for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())
        if count > max_skills:
            report.error(
                where,
                f"tier {tier!r} allows at most {max_skills} skills, found {count}",
            )


def check_hygiene(agent_dir: Path, where: str, report: Report) -> None:
    for path in agent_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(agent_dir)
        if path.name in {"index.json", "agents.json"}:
            report.error(where, f"{rel} is a generated artifact and must not be committed")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            report.error(where, f"{rel} looks like a binary artifact - commit sources only")
        if path.name.endswith((".key", ".pem", ".p12", ".pfx")):
            report.error(where, f"{rel} looks like key material and must not be committed")


def check_no_binaries(agent_dir: Path, catalog: dict, report: Report) -> None:
    """An agent package contains no binaries.

    The capability surface of an agent is exactly prompt + skills + tools +
    mcp. Any binary capability is reached through an stdio MCP server that
    lives outside the package. A package that carries an executable would
    therefore ship an unexpected code-execution surface, and there is no
    legitimate reason for one.
    """
    where = f"agents/{agent_dir.parent.name}/{agent_dir.name}"
    policy = catalog.get("package_policy", {})
    forbidden = set(policy.get("forbidden_suffixes", []))

    for path in sorted(agent_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(agent_dir)
        suffix = path.suffix.lower()
        if suffix in forbidden:
            report.error(
                where,
                f"{rel} is a binary ({suffix}). Agent packages contain no binaries - "
                "expose binary capability through a stdio MCP server instead "
                "(see docs/publishing.md)",
            )
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}:
            # Binary assets are allowed - they are inert images, not code.
            continue


def check_mcp_declarations(manifest: dict, catalog: dict, report: Report) -> None:
    """MCP servers are referenced by name only.

    A package declares the MCP tools it wants. The server binary and its
    stdio transport configuration are supplied by the runtime; shipping
    them inside the package would defeat the "no binaries" rule.
    """
    where = "manifest"
    policy = catalog.get("package_policy", {}).get("mcp_declaration", {})
    prefix = policy.get("tool_prefix", "mcp_")

    for tool in manifest.get("tools", []):
        name = tool.get("name", "")
        if name.startswith(prefix):
            # MCP tools are opted in by name. Nothing else may be declared
            # for them - in particular no command/args/env, which would let
            # a package smuggle in an executable.
            extra = set(tool) - {"name", "type", "config", "rag"}
            if extra:
                report.error(
                    where,
                    f"MCP tool {name!r} declares extra keys {sorted(extra)} - "
                    "MCP servers are referenced by name only",
                )
            if tool.get("config"):
                report.error(
                    where,
                    f"MCP tool {name!r} must not carry a config block - "
                    "the runtime supplies the stdio server configuration",
                )


def validate_agent_dir(agent_dir: Path, schema: dict, catalog: dict, report: Report) -> None:
    owner = agent_dir.parent.name
    name = agent_dir.name
    manifest = load_manifest(agent_dir, report)
    if manifest is not None:
        check_manifest_schema(manifest, schema, f"agents/{owner}/{name}", report)
        for prefix in catalog.get("reserved_agent_id_prefixes", []):
            if manifest.get("agent_id", "").startswith(prefix):
                report.error(
                    f"agents/{owner}/{name}",
                    f"agent_id {manifest.get('agent_id')!r} uses the reserved prefix {prefix!r} - "
                    "reserved for the platform, not for community submissions",
                )
    if manifest is not None:
        check_tier_consistency(agent_dir, manifest, catalog, report)
        check_mcp_declarations(manifest, catalog, report)
    check_layout(agent_dir, owner, name, manifest, report)
    check_no_binaries(agent_dir, catalog, report)
    check_hygiene(agent_dir, f"agents/{owner}/{name}", report)


def discover_agent_dirs() -> list[Path]:
    """All agent directories: agents/<owner>/<name>/ (one level of owner)."""
    if not AGENTS_DIR.is_dir():
        return []
    found = []
    for owner_dir in sorted(AGENTS_DIR.iterdir()):
        if not owner_dir.is_dir() or owner_dir.name.startswith("_") or owner_dir.name.startswith("."):
            continue
        for name_dir in sorted(owner_dir.iterdir()):
            if name_dir.is_dir() and (name_dir / "manifest.toml").is_file():
                found.append(name_dir)
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="agent directories to validate (default: all)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--check-template", action="store_true",
                    help="also validate agents/_template (relaxed: template placeholders allowed)")
    args = ap.parse_args()

    schema = load_json(SCHEMA_DIR / "manifest.schema.json")
    catalog = load_json(SCHEMA_DIR / "catalog.json")

    report = Report()
    check_catalog_drift(schema, catalog, report)

    if args.paths:
        targets = [Path(p).resolve() for p in args.paths]
    else:
        targets = discover_agent_dirs()

    for target in targets:
        validate_agent_dir(target, schema, catalog, report)

    if args.check_template:
        template = AGENTS_DIR / "_template"
        if template.is_dir():
            check_layout(template, "_template", "_template", None, report, is_template=True)

    for warning in report.warnings:
        print(f"[validate] WARN  {warning}")
    for error in report.errors:
        print(f"[validate] ERROR {error}")

    if report.errors:
        print(f"[validate] FAILED: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
        return 1
    if args.strict and report.warnings:
        print(f"[validate] FAILED (--strict): {len(report.warnings)} warning(s)")
        return 1

    print(f"[validate] OK: {len(targets)} agent(s) validated, {len(report.warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
