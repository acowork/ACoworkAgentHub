#!/usr/bin/env python3
"""Validate community agent directories against the repo's contracts.

Checks, in order of cost:

  1. schema   - manifest.toml validates against schema/manifest.schema.json
                (a mirror of the main repo's AgentManifest) and hub.json
                against schema/hub.schema.json
  2. catalog  - every label the agent declares is either mapped to a
                category or explicitly unmapped, and the map is coherent
  3. graph    - capability -> skill references resolve, and every skill
                directory is reachable (the main repo's validate_graph)
  4. layout   - directory shape, required files, skill layout, naming rules
  5. hygiene  - no binaries, no index.json, no stray secrets

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

OWNER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")
LABEL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Mirrors the main repo's `is_valid_label`: lowercase kebab, 2-32 chars.
MIN_LABEL_LEN = 2
MAX_LABEL_LEN = 32
#: Mirrors the main repo's `MAX_MANIFEST_LABELS`.
MAX_LABELS = 12


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


def load_toml(path: Path, where: str, report: Report) -> dict | None:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, do not crash the run
        report.error(where, f"cannot parse {path.name}: {exc}")
        return None


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


# ---------------------------------------------------------------------------
# 1. schema
# ---------------------------------------------------------------------------

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


def check_hub_schema(hub: dict, schema: dict, where: str, report: Report) -> None:
    try:
        import jsonschema
    except ModuleNotFoundError:
        return

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(hub), key=lambda e: list(e.absolute_path))
    for err in errors:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        report.error(where, f"hub schema: {loc}: {err.message}")


def check_catalog_coherence(catalog: dict, report: Report) -> None:
    """The category map must point at categories that exist."""
    categories = set(catalog.get("categories", {}).keys())
    mapping = catalog.get("label_categories", {})
    if not mapping:
        report.error("schema/catalog.json", "label_categories is empty")
        return
    for label, category in sorted(mapping.items()):
        if category not in categories:
            report.error(
                "schema/catalog.json",
                f"label `{label}` maps to unknown category `{category}`",
            )
        if not LABEL_RE.match(label):
            report.error(
                "schema/catalog.json",
                f"label key `{label}` is not a valid manifest label",
            )


# ---------------------------------------------------------------------------
# 2. labels / category derivation
# ---------------------------------------------------------------------------

def check_labels(manifest: dict, catalog: dict, where: str, report: Report) -> None:
    """Mirror the main repo's label rules, then check the category mapping."""
    labels = manifest.get("labels", [])
    if not isinstance(labels, list):
        report.error(where, "labels must be an array")
        return

    if len(labels) > MAX_LABELS:
        report.error(where, f"labels: at most {MAX_LABELS} allowed, got {len(labels)}")

    seen: set[str] = set()
    mapping = catalog.get("label_categories", {})
    for label in labels:
        if not isinstance(label, str):
            report.error(where, f"labels: `{label!r}` is not a string")
            continue
        if not (MIN_LABEL_LEN <= len(label) <= MAX_LABEL_LEN) or not LABEL_RE.match(label):
            report.error(
                where,
                f"labels: `{label}` is invalid - lowercase letters, digits and hyphens "
                f"only, {MIN_LABEL_LEN}-{MAX_LABEL_LEN} chars",
            )
            continue
        if label in seen:
            report.error(where, f"labels: duplicate `{label}`")
        seen.add(label)

    if not labels:
        report.warn(
            where,
            "no labels declared - the agent cannot be filed under any category "
            "and will only appear under 'other'",
        )
        return

    if not any(label in mapping for label in labels):
        report.warn(
            where,
            f"none of the labels {labels} map to a category in schema/catalog.json - "
            "the agent will be filed under 'other'. Add the label to "
            "label_categories, or use a mapped label.",
        )


# ---------------------------------------------------------------------------
# 3. declaration graph (the main repo's validate_graph)
# ---------------------------------------------------------------------------

def skill_names(agent_dir: Path) -> list[str]:
    skills_dir = agent_dir / "skills"
    if not skills_dir.is_dir():
        return []
    names = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            names.append(child.name)
    return names


def check_capability_graph(manifest: dict, agent_dir: Path, where: str, report: Report) -> None:
    """A capability may only link a skill that is really shipped."""
    available = set(skill_names(agent_dir))
    capabilities = manifest.get("capabilities", {})
    if not isinstance(capabilities, dict):
        return
    for action, def_ in capabilities.items():
        if not isinstance(def_, dict):
            continue
        linked = def_.get("skills", [])
        for skill in linked:
            if skill not in available:
                report.error(
                    where,
                    f"capabilities.`{action}` links skill `{skill}`, "
                    f"which is not shipped (have: {sorted(available)})",
                )


def check_mcp_declarations(manifest: dict, where: str, report: Report) -> None:
    """Mirror the main repo's `[[mcp_servers]]` rules.

    The main repo rejects an inline stdio MCP declaration because a package
    must not name an executable to run on the user's machine. It also
    requires exactly one of preset / hosted / inline.
    """
    servers = manifest.get("mcp_servers", [])
    if not isinstance(servers, list):
        return
    permissions = {
        p.get("type") for p in manifest.get("permissions", []) if isinstance(p, dict)
    }
    seen_keys: set[str] = set()
    for decl in servers:
        if not isinstance(decl, dict):
            continue
        forms = [
            bool(decl.get("preset")),
            bool(decl.get("hosted")),
            bool(decl.get("transport")) or bool(decl.get("url")),
        ]
        if sum(forms) != 1:
            report.error(
                where,
                f"mcp_servers `{decl.get('name') or decl.get('preset') or decl.get('hosted')}`: "
                "must declare exactly one of `preset`, `hosted`, or the inline form",
            )
            continue

        transport = decl.get("transport")
        if transport == "stdio":
            report.error(
                where,
                f"mcp_servers `{decl.get('name')}`: inline stdio is not allowed - "
                "a package must not name an executable to run on the user's machine",
            )

        url = decl.get("url")
        if url is not None:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                report.error(where, f"mcp_servers inline url `{url}` must be an http(s) URL")
            elif "Network" not in permissions:
                report.error(
                    where,
                    f"mcp_servers inline url `{url}` is not covered by a `Network` permission",
                )

        key = decl.get("name") or decl.get("preset") or decl.get("hosted")
        if key in seen_keys:
            report.error(where, f"mcp_servers: duplicate server `{key}`")
        seen_keys.add(key)


# ---------------------------------------------------------------------------
# 4. layout
# ---------------------------------------------------------------------------

#: Agent-id prefixes the platform owns. A community submission may not use
#: them - it could then impersonate a platform agent by id alone, which is
#: exactly the hole the old `com.acowork.` prefix check left open.
RESERVED_AGENT_ID_PREFIXES = ("com.acowork.", "acowork.", "official.")

#: The one publisher allowed to use those prefixes: the platform itself.
#: The official channel re-publishes the main repo's example agents under
#: their original `com.acowork.*` ids so an installed package stays
#: upgradeable instead of forking into a second, differently-named agent.
OFFICIAL_PUBLISHER_ID = "acowork:official"


def check_layout(agent_dir: Path, owner: str, name: str, manifest: dict | None,
                 hub: dict | None, report: Report, is_template: bool = False) -> None:
    where = f"agents/{owner}/{name}"

    if not is_template:
        if not OWNER_RE.match(owner):
            report.error(where, f"owner directory `{owner}` must be lowercase kebab-case")
        if not NAME_RE.match(name):
            report.error(where, f"agent directory `{name}` must be lowercase kebab-case")

    if manifest is not None:
        if not str(manifest.get("name") or "").strip():
            report.error(where, "manifest.name is required")
        # manifest.name is the MAIN REPO's field: a human-readable short
        # name ("Docs", "PM", "QA"). The hub's directory name is a separate
        # slug used for routing and for the publisher's own tree. Binding
        # them would force the platform's examples to be renamed, so the
        # two are deliberately independent - the hub never rewrites
        # manifest.name.
        if not manifest.get("role"):
            report.error(where, "manifest.role is required - it is core identity information")
        if manifest.get("dev"):
            report.error(
                where,
                "manifest.dev must be false - a dev package skips the main repo's "
                "marketplace and signature checks",
            )
        publisher_id = ""
        if isinstance(hub, dict) and isinstance(hub.get("publisher"), dict):
            publisher_id = str(hub["publisher"].get("id") or "")
        is_official = publisher_id == OFFICIAL_PUBLISHER_ID
        for prefix in RESERVED_AGENT_ID_PREFIXES:
            if str(manifest.get("agent_id", "")).startswith(prefix):
                if is_official:
                    continue
                report.error(
                    where,
                    f"agent_id `{manifest.get('agent_id')}` uses the reserved prefix "
                    f"`{prefix}` - reserved for the platform. Only "
                    f"`{OFFICIAL_PUBLISHER_ID}` may publish under it.",
                )

    if not (agent_dir / "README.md").is_file():
        report.error(where, "missing README.md")
    if not (agent_dir / "hub.json").is_file() and not is_template:
        report.error(
            where,
            "missing hub.json - the hub-specific metadata (publisher, license, "
            "repository) lives there, not in manifest.toml",
        )

    # skills/<skill>/SKILL.md
    skills_dir = agent_dir / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("_"):
                continue
            if not (child / "SKILL.md").is_file():
                report.error(where, f"skills/{child.name}/ is missing SKILL.md")
            if not NAME_RE.match(child.name):
                report.error(where, f"skills/{child.name}/ must be lowercase kebab-case")

    # The main repo's package format: prompts/ carries the system prompt.
    if not is_template and not (agent_dir / "prompts").is_dir():
        report.warn(
            where,
            "no prompts/ directory - an agent with no system prompt cannot run",
        )


# ---------------------------------------------------------------------------
# 5. hygiene
# ---------------------------------------------------------------------------

def check_hub_file(hub: dict, agent_dir: Path, owner: str, where: str, report: Report) -> None:
    """The publisher id must match the directory it lives in."""
    publisher = hub.get("publisher", {})
    pid = publisher.get("id", "") if isinstance(publisher, dict) else ""
    if not pid:
        return
    _, _, handle = pid.partition(":")
    if handle and handle != owner:
        report.error(
            where,
            f"publisher.id `{pid}` does not match the owner directory `{owner}` - "
            "a submission may only publish under its own account",
        )


def check_official_claim(manifest: dict | None, hub: dict | None, owner: str,
                         where: str, report: Report) -> None:
    """Only the platform may publish as the official channel.

    Two separate claims have to be true for a package to pass as official:

      1. it claims the official publisher id, and
      2. its agent id is on the platform's list in
         registry/official-publishers.json.

    Without (2), a pull request could name itself `official` and ship an
    arbitrary agent under a `com.acowork.` id. The list is the platform's
    explicit, diff-visible statement of what it publishes; adding to it is a
    reviewed act.
    """
    registry_path = REPO_ROOT / "registry" / "official-publishers.json"
    if not registry_path.is_file():
        return
    registry = load_json(registry_path)
    official_publisher = registry.get("publisher_id", OFFICIAL_PUBLISHER_ID)
    official_owner = registry.get("owner_dir", "official")
    allowed_ids = set(registry.get("agents", []))

    publisher = hub.get("publisher") if isinstance(hub, dict) else None
    pid = publisher.get("id") if isinstance(publisher, dict) else None

    claims_official = pid == official_publisher
    lives_in_official_dir = owner == official_owner

    if claims_official and not lives_in_official_dir:
        report.error(
            where,
            f"publisher.id `{official_publisher}` may only publish under "
            f"`agents/{official_owner}/` - this package lives under `agents/{owner}/`",
        )
    if lives_in_official_dir and not claims_official:
        report.error(
            where,
            f"`agents/{official_owner}/` is reserved for the official channel - "
            f"this package must declare publisher.id `{official_publisher}`",
        )
    if claims_official and manifest is not None:
        agent_id = str(manifest.get("agent_id", ""))
        if agent_id not in allowed_ids:
            report.error(
                where,
                f"agent_id `{agent_id}` is not published by the official channel - "
                "add it to registry/official-publishers.json if the platform "
                "really publishes it",
            )


def check_hygiene(agent_dir: Path, where: str, report: Report) -> None:
    policy = load_json(SCHEMA_DIR / "catalog.json").get("package_policy", {})
    forbidden = set(policy.get("forbidden_binary_extensions", []))
    forbidden |= {".agent", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}

    for path in agent_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(agent_dir).as_posix()
        suffix = path.suffix.lower()

        if suffix in forbidden:
            report.error(
                where,
                f"{rel} has a forbidden extension - an agent package never carries "
                "a binary; capabilities come from prompt, skills, builtin tools and MCP",
            )
        if path.name in ("index.json", "agents.json"):
            report.error(where, f"{rel} is a generated artifact and must not be committed")
        if path.name.endswith((".key", ".pem")):
            report.error(where, f"{rel} looks like signing material and must not be committed")


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def validate_agent_dir(agent_dir: Path, schema: dict, hub_schema: dict,
                       catalog: dict, report: Report) -> None:
    owner = agent_dir.parent.name
    name = agent_dir.name
    where = f"agents/{owner}/{name}"

    manifest = load_toml(agent_dir / "manifest.toml", where, report)
    if manifest is not None:
        check_manifest_schema(manifest, schema, where, report)
        check_labels(manifest, catalog, where, report)
        check_capability_graph(manifest, agent_dir, where, report)
        check_mcp_declarations(manifest, where, report)

    hub = None
    hub_path = agent_dir / "hub.json"
    if hub_path.is_file():
        hub = load_json(hub_path)
        check_hub_schema(hub, hub_schema, where, report)
        check_hub_file(hub, agent_dir, owner, where, report)
        check_official_claim(manifest, hub, owner, where, report)

    check_layout(agent_dir, owner, name, manifest, hub, report)
    check_hygiene(agent_dir, where, report)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="agent directories to validate (default: all)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--check-template", action="store_true",
                    help="also validate agents/_template (relaxed: template placeholders allowed)")
    args = ap.parse_args()

    schema = load_json(SCHEMA_DIR / "manifest.schema.json")
    hub_schema = load_json(SCHEMA_DIR / "hub.schema.json")
    catalog = load_json(SCHEMA_DIR / "catalog.json")

    report = Report()
    check_catalog_coherence(catalog, report)

    if args.paths:
        targets = [Path(p).resolve() for p in args.paths]
    else:
        targets = discover_agent_dirs()

    for target in targets:
        validate_agent_dir(target, schema, hub_schema, catalog, report)

    if args.check_template:
        template = AGENTS_DIR / "_template"
        if template.is_dir():
            check_layout(template, "_template", "_template", None, None, report, is_template=True)

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
