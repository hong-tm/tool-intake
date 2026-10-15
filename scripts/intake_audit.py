#!/usr/bin/env python3
"""Read-only evidence gathering for agent tool/skill intake decisions.

Python 3.10+, standard library only. This file ships as a standalone CLI, so the
module docstring is its public interface note; usage and examples live in --help.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

USER_ROOTS = [
    "~/.agents/skills",
    "~/.claude/skills",
    "~/.codex/skills",
    "~/.config/opencode/skills",
    "~/.gemini/skills",
]

PROJECT_ROOTS = [
    ".agents/skills",
    ".claude/skills",
    ".codex/skills",
    ".opencode/skill",
    ".opencode/skills",
]

RED_FLAGS = {
    "root/sudo required": r"\b(sudo|run as root|requires? root|must be root)\b",
    "heavy deps (browser/docker)": r"\b(docker|playwright|puppeteer|chromium|selenium|electron)\b",
    "daemon/service install": r"\b(daemon|systemd|launchd|install as a service|background service)\b",
    "credential needed": r"\b(api[ _-]?key|access token|bearer token|credential|\.env file)\b",
    "node runtime": r"\b(npm i(nstall)?|bun install|pnpm (i|add)|yarn add)\b",
    "python runtime": r"\b(pip install|pipx install|uv tool install|poetry add|conda install)\b",
    "telemetry/network callout": r"\b(telemetry|phone home|sends? data to|uploads? (to|your))\b",
}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "use",
    "using", "used", "when", "then", "into", "any", "all", "not", "but", "are",
    "was", "were", "has", "have", "had", "can", "will", "would", "should",
    "skill", "skills", "tool", "tools", "agent", "agents", "cli", "repo",
    "run", "runs", "file", "files", "docs", "readme", "each", "other", "more",
    "than", "such", "also", "only", "over", "own", "new", "via", "per", "one",
    "its", "it's", "them", "they", "out", "get", "gets", "make", "made", "how",
    "what", "which", "who", "why", "does", "do", "done", "like", "just",
    "name", "description", "version", "license", "author", "https", "http",
    "com", "org", "www", "raw", "png", "svg", "github", "githubusercontent",
    "code", "coding", "local", "locally", "state", "find", "data", "config",
    "output", "based", "plus", "user", "users", "system", "support", "test",
    "tests", "text", "string", "list", "item", "items",
}


def strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", " ", text or "")


def looks_like_opaque_token(token: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{12,}", token))


def tokenize(text: str) -> set[str]:
    if not text:
        return set()
    raw = re.findall(r"[a-z0-9][a-z0-9_+.-]{2,}", text.lower())
    out = set()
    for token in raw:
        token = token.strip("._-")
        if not 3 <= len(token) <= 24 or token.isdigit() or token in STOPWORDS:
            continue
        if looks_like_opaque_token(token):
            continue
        out.add(token)
    return out


def parse_frontmatter(path: Path, text: str | None = None) -> dict[str, str] | None:
    if text is None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    text = text[:20000]
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end].splitlines()

    fields: dict[str, str] = {}
    for key in ("name", "description"):
        for i, line in enumerate(block):
            m = re.match(rf"^\s*{key}\s*:\s*(.*)$", line)
            if not m:
                continue
            value = m.group(1).strip()
            if value in (">", "|", ">-", "|-", ">+", "|+"):
                chunks = []
                for follow in block[i + 1:]:
                    if not follow.strip():
                        continue
                    if re.match(r"^\s*\w[\w-]*\s*:", follow) and not follow.startswith(("  ", "\t")):
                        break
                    chunks.append(follow.strip())
                value = " ".join(chunks)
            fields[key] = value.strip().strip("'\"")
            break
    if not fields.get("name"):
        return None
    return fields


def find_project_roots(start: Path) -> list[Path]:
    found: list[Path] = []
    current = start.resolve()
    while True:
        for rel in PROJECT_ROOTS:
            candidate = current / rel
            if candidate.is_dir() or candidate.is_symlink():
                found.append(candidate)
        if current.parent == current or current == Path("/"):
            break
        current = current.parent
    return found


def collect_roots(extra: list[str], only_roots: bool = False) -> tuple[list[Path], list[str]]:
    notes: list[str] = []
    roots: list[Path] = []
    if not only_roots:
        for entry in USER_ROOTS:
            path = Path(os.path.expanduser(entry))
            if path.is_dir() or path.is_symlink():
                roots.append(path)
        roots.extend(find_project_roots(Path.cwd()))
    for entry in extra:
        roots.append(Path(os.path.abspath(os.path.expanduser(entry))))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in roots:
        lexical = str(path)
        if lexical in seen:
            continue
        seen.add(lexical)
        resolved = resolve_inventory_path(path, notes)
        if resolved is not None:
            if resolved.is_dir():
                unique.append(path)
            else:
                notes.append(f"root is not a directory, skipped: {path}")
    return unique, notes


def resolve_inventory_path(path: Path, notes: list[str]) -> Path | None:
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        notes.append(f"unresolvable path, skipped: {path}: {exc}")
        return None


def inventory(roots: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    skills: list[dict[str, Any]] = []
    notes: list[str] = []
    canonical_skills: dict[str, dict[str, Any]] = {}
    for root in roots:
        try:
            entries = sorted(root.iterdir())
        except OSError as exc:
            notes.append(f"unreadable root, skipped: {root}: {exc}")
            continue
        for entry in entries:
            resolved_entry = resolve_inventory_path(entry, notes)
            if resolved_entry is None or not resolved_entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists() and not skill_md.is_symlink():
                continue
            resolved_skill = resolve_inventory_path(skill_md, notes)
            if resolved_skill is None or not resolved_skill.is_file():
                continue
            canonical = str(resolved_skill)
            entrypoint = {"path": str(skill_md), "root": str(root)}
            if canonical in canonical_skills:
                aliases = canonical_skills[canonical]["entrypoints"]
                if entrypoint not in aliases:
                    aliases.append(entrypoint)
                continue
            try:
                content = resolved_skill.read_bytes()
            except OSError as exc:
                notes.append(f"unreadable skill, skipped: {skill_md}: {exc}")
                continue
            meta = parse_frontmatter(skill_md, content.decode("utf-8", "replace"))
            if meta is None:
                notes.append(f"unparseable frontmatter, skipped: {skill_md}")
                continue
            skill = {
                "name": meta["name"],
                "description": meta.get("description", ""),
                "path": str(skill_md),
                "root": str(root),
                "canonical_path": canonical,
                "sha256": hashlib.sha256(content).hexdigest(),
                "entrypoints": [entrypoint],
            }
            canonical_skills[canonical] = skill
            skills.append(skill)
    by_name: dict[str, list[str]] = {}
    for skill in skills:
        by_name.setdefault(skill["name"], []).append(skill["canonical_path"])
    for name, paths in by_name.items():
        if len(paths) > 1:
            notes.append(f"skill name collision: {name}: {'; '.join(paths)}")
    return skills, notes


def same_name_groups(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, list[dict[str, str]]] = {}
    for skill in skills:
        by_name.setdefault(skill["name"], []).append({
            "canonical_path": skill["canonical_path"], "sha256": skill["sha256"],
        })
    return [
        {"name": name, "copies": sorted(copies, key=lambda item: item["canonical_path"]),
         "content_identical": len({copy["sha256"] for copy in copies}) == 1,
         "equality_scope": "SKILL.md"}
        for name, copies in sorted(by_name.items()) if len(copies) > 1
    ]


def parse_github(url: str) -> tuple[str, str] | None:
    m = re.match(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", url.strip())
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git")


def http_json(url: str) -> tuple[dict[str, Any] | None, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "intake-audit/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code} for {url}"
    except Exception as exc:  # noqa: BLE001 - network failures are expected here
        return None, f"{type(exc).__name__}: {exc}"


def upstream_github(owner: str, repo: str, as_of: dt.date | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"repo": f"{owner}/{repo}", "warnings": [],
                              "evidence_sources": [], "evidence_status": "partial"}
    data, error = http_json(f"https://api.github.com/repos/{owner}/{repo}")
    if error or not data:
        result["warnings"].append(error or "no data")
    else:
        result["stars"] = data.get("stargazers_count")
        result["forks"] = data.get("forks_count")
        result["license"] = ((data.get("license") or {}).get("spdx_id") or "NONE")
        result["archived"] = bool(data.get("archived"))
        result["open_issues"] = data.get("open_issues_count")
        result["created"] = (data.get("created_at") or "")[:10]
        pushed = (data.get("pushed_at") or "")[:10]
        result["last_push"] = pushed
        if pushed:
            try:
                today = as_of or dt.datetime.now(dt.timezone.utc).date()
                result["days_since_push"] = (today - dt.date.fromisoformat(pushed)).days
            except ValueError:
                result["warnings"].append(f"invalid last push date: {pushed}")
        result["description"] = data.get("description") or ""

    readme, readme_error = http_json(f"https://api.github.com/repos/{owner}/{repo}/readme")
    if readme_error or not readme:
        result["warnings"].append(readme_error or "no readme")
        return result
    try:
        content = base64.b64decode("".join(readme.get("content", "").split()), validate=True)
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append(f"readme decode failed: {exc}")
        return result
    record_documents(result, [(readme.get("download_url") or
                               f"https://api.github.com/repos/{owner}/{repo}/readme", content)])
    return result


def record_documents(result: dict[str, Any], documents: list[tuple[str, bytes]]) -> None:
    result["evidence_sources"] = [
        {"source": source, "utf8_bytes": len(content),
         "sha256": hashlib.sha256(content).hexdigest()}
        for source, content in documents
    ]
    text = "\n".join(content.decode("utf-8", "replace") for _, content in documents)
    result["readme_bytes"] = sum(len(content) for _, content in documents)
    result["red_flags"] = scan_red_flags(text)
    result["readme_head"] = " ".join(text.split())[:1500]
    result["document_keywords"] = sorted(tokenize(strip_urls(text)))
    result["document_content_available"] = bool(text.strip())
    if not text.strip():
        result["warnings"].append("no document content available at target")
    result["evidence_status"] = "partial" if result["warnings"] else "complete"


def local_target(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"repo": str(path), "local": True, "warnings": [],
                              "evidence_sources": [], "evidence_status": "partial"}
    documents = []
    candidates = ([path / name for name in ("README.md", "readme.md", "README", "SKILL.md")]
                  if path.is_dir() else [path])
    for candidate in candidates:
        if candidate.is_file():
            try:
                documents.append((str(candidate), candidate.read_bytes()))
            except OSError as exc:
                result["warnings"].append(f"{candidate}: {exc}")
    if not documents:
        result["warnings"].append("no README.md / SKILL.md found at target")
        return result
    record_documents(result, documents)
    return result


def scan_red_flags(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for label, pattern in RED_FLAGS.items():
        found = re.findall(pattern, text, flags=re.IGNORECASE)
        if found:
            hits[label] = sorted({f if isinstance(f, str) else f[0] for f in found})[:5]
    return hits


def idf_weights(skills: list[dict[str, Any]]) -> dict[str, float]:
    """Rarity weight per token: a token shared by many installed skills carries little."""
    counts: dict[str, int] = {}
    for skill in skills:
        for token in tokenize(skill["name"] + " " + skill["description"]):
            counts[token] = counts.get(token, 0) + 1
    total = len(skills)
    return {token: math.log((total + 1) / (count + 0.5)) for token, count in counts.items()}


def unseen_weight(skills: list[dict[str, Any]]) -> float:
    """Weight of a token no installed skill mentions."""
    return math.log((len(skills) + 1) / 0.5)


def dedup_scan(keywords: set[str], skills: list[dict[str, Any]], top: int) -> list[dict[str, Any]]:
    weights = idf_weights(skills)
    default = unseen_weight(skills)
    keyword_mass = sum(weights.get(token, default) for token in keywords)
    scored = []
    for skill in skills:
        skill_tokens = tokenize(skill["name"] + " " + skill["description"])
        matched = keywords & skill_tokens
        if not matched:
            continue
        matched_mass = sum(weights.get(token, default) for token in matched)
        skill_mass = sum(weights.get(token, default) for token in skill_tokens)
        score = matched_mass / ((keyword_mass * skill_mass) ** 0.5)
        ranked = sorted(matched, key=lambda token: (-weights.get(token, default), token))
        scored.append(
            {
                "name": skill["name"],
                "root": skill["root"],
                "canonical_path": skill["canonical_path"],
                "matched": ranked[:8],
                "matched_count": len(matched),
                "score": round(score, 3),
            }
        )
    scored.sort(key=lambda item: (-item["score"], -item["matched_count"], item["name"], item["root"]))
    return scored[:top]


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Tool intake audit")
    add("")
    add(f"- Target: {report['target'] or '(none, inventory only)'}")
    add(f"- Generated: {report['generated']}")
    add(f"- Collection status: {report['collection_status']}")
    add("- Read-only: no file was modified")
    add("")

    up = report.get("upstream")
    if up:
        add("## 1. Upstream vitality")
        if up.get("local"):
            add(f"- Local target: {up['repo']}")
        else:
            add(f"- Repo: {up['repo']}")
            if "stars" in up:
                add(f"- Stars: {up['stars']} | Forks: {up.get('forks')} | Open issues: {up.get('open_issues')}")
                add(f"- License: {up.get('license')} | Archived: {'yes' if up.get('archived') else 'no'}")
                add(f"- Created: {up.get('created')} | Last push: {up.get('last_push')} ({up.get('days_since_push')} days ago)")
        if up.get("description"):
            add(f"- Description: {up['description']}")
        flags = up.get("red_flags") or {}
        add("")
        if flags:
            add("### Preflight red flags in project text")
            for label, samples in flags.items():
                add(f"- {label}: {len(samples)} kind(s) seen -> {', '.join(samples)}")
        elif up.get("document_content_available"):
            add("### Preflight red flags in project text")
            add("- none detected")
        else:
            add("### Preflight red flags in project text")
            add("- incomplete evidence: no document content available")
        for source in up.get("evidence_sources", []):
            add(f"- Evidence: {source['source']} | UTF-8 bytes: {source['utf8_bytes']} | SHA-256: {source['sha256']}")
        if up.get("warnings"):
            add("")
            add(f"- Warnings: {'; '.join(up['warnings'])}")
        add("")

    add("## 2. Installed skill inventory")
    add("")
    for entry in report["inventory_roots"]:
        add(f"- {entry['root']} ({entry['count']} unique skill(s), {entry['entrypoint_count']} entrypoint(s))")
    add(f"- Total distinct skills: {len(report['skills'])}")
    add(f"- Total entrypoints: {sum(len(skill['entrypoints']) for skill in report['skills'])}")
    for skill in report["skills"]:
        add(f"- {skill['name']}: {skill['canonical_path']} | SHA-256: {skill['sha256']}")
        for entrypoint in skill["entrypoints"]:
            add(f"  - entrypoint: {entrypoint['path']} | root: {entrypoint['root']}")
    for note in report["notes"]:
        add(f"- note: {note}")
    for group in report["same_name_groups"]:
        add(f"- Same name: {group['name']} | SKILL.md content identical: {group['content_identical']}")
    add("")

    add("## 3. Duplicate-function scan")
    add("")
    matched_tokens = sorted({token for item in report["overlaps"] for token in item["matched"]})
    add(f"- Keywords considered: {len(report['keywords'])} | top matched tokens: {', '.join(matched_tokens) or '(none)'}")
    add("- Rarity-weighted scores order reading only; the agent determines functional overlap from source evidence")
    if report["overlaps"]:
        add("")
        add("| skill | source | matched tokens | matched | score |")
        add("|---|---|---|---|---|")
        for item in report["overlaps"]:
            add(f"| {item['name']} | {item['canonical_path']} | {', '.join(item['matched'])} | {item['matched_count']} | {item['score']} |")
    elif report["target"] and not (up and up.get("document_content_available")):
        add("- incomplete candidate evidence; keyword overlap cannot be ruled out")
    else:
        add("- no installed skill shares a keyword with the candidate")
    add("")

    add("## 4. Agent review handoff")
    add("")
    add(f"- Review status: {report['agent_review']['status']}")
    add("- Decision: unset; collection status and scores never approve or reject installation")
    add("- Return this report to the calling agent with the user's intended use and target environment")
    add("- The agent reads sources, resolves uncertainty, and returns install / decline / defer with evidence and open questions")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="intake_audit.py",
        description="Read-only evidence for agent tool/skill intake decisions.",
        epilog=(
            "examples:\n"
            "  intake_audit.py https://github.com/owner/repo\n"
            "  intake_audit.py ./local/path --keywords secret,scan,redact\n"
            "  intake_audit.py --keywords webhook,retry --json\n"
            "  intake_audit.py https://github.com/owner/repo --no-network --roots /opt/skills"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="?", help="GitHub URL or local path of the candidate")
    parser.add_argument("--keywords", default="", help="comma-separated extra dedup keywords")
    parser.add_argument("--roots", action="append", default=[], help="extra skill root (repeatable)")
    parser.add_argument("--only-roots", action="store_true", help="disable automatic root discovery; requires --roots")
    parser.add_argument("--as-of", help="report date, YYYY-MM-DD (default: current UTC date)")
    parser.add_argument("--strict", action="store_true", help="exit nonzero on incomplete evidence or inventory warnings")
    parser.add_argument("--top", type=int, default=8, help="max overlap rows (default 8)")
    parser.add_argument("--no-network", action="store_true", help="skip live GitHub lookups")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)
    if args.only_roots and not args.roots:
        parser.error("--only-roots requires --roots")
    if args.top <= 0:
        parser.error("--top must be positive")
    if args.as_of:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.as_of):
                raise ValueError
            as_of = dt.date.fromisoformat(args.as_of)
        except ValueError:
            parser.error("--as-of must be an ISO date (YYYY-MM-DD)")
    else:
        as_of = dt.datetime.now(dt.timezone.utc).date()

    roots, notes = collect_roots(args.roots, args.only_roots)
    skills, inv_notes = inventory(roots)
    notes.extend(inv_notes)

    inventory_roots = []
    for root in roots:
        inventory_roots.append(
            {
                "root": str(root),
                "count": sum(
                    1 for skill in skills
                    if any(entry["root"] == str(root) for entry in skill["entrypoints"])
                ),
                "entrypoint_count": sum(
                    1 for skill in skills for entry in skill["entrypoints"]
                    if entry["root"] == str(root)
                ),
            }
        )

    upstream: dict[str, Any] | None = None
    keywords = {token for token in tokenize(args.keywords.replace(",", " "))}

    if args.target:
        github = parse_github(args.target)
        target_path = Path(os.path.expanduser(args.target))
        if github and not args.no_network:
            upstream = upstream_github(*github, as_of=as_of)
            keywords |= tokenize(upstream.get("repo", ""))
            keywords |= tokenize(upstream.get("description", ""))
            keywords.update(upstream.get("document_keywords", []))
        elif github and args.no_network:
            keywords |= tokenize(github[0] + " " + github[1])
            notes.append("network lookups skipped (--no-network)")
            upstream = {"repo": "/".join(github), "warnings": [notes[-1]],
                        "evidence_sources": [], "evidence_status": "partial"}
        elif target_path.exists():
            upstream = local_target(target_path)
            keywords |= tokenize(target_path.name)
            keywords.update(upstream.get("document_keywords", []))
        else:
            keywords |= tokenize(args.target)
            notes.append(f"target is neither a GitHub URL nor an existing path: {args.target}")
            upstream = {"repo": args.target, "local": True, "warnings": [notes[-1]],
                        "evidence_sources": [], "evidence_status": "partial"}

    keywords = {token for token in keywords if len(token) >= 3 and token not in STOPWORDS}

    report = {
        "schema_version": 2,
        "agent_review": {
            "status": "required" if args.target else "not_requested",
            "decision": None,
        },
        "target": args.target or "",
        "generated": as_of.isoformat(),
        "collection_status": ("partial" if notes or (upstream and upstream["evidence_status"] != "complete")
                              else "complete" if args.target else "inventory_only"),
        "upstream": upstream,
        "inventory_roots": inventory_roots,
        "skills": skills,
        "same_name_groups": same_name_groups(skills),
        "overlaps": dedup_scan(keywords, skills, args.top),
        "keywords": sorted(keywords),
        "notes": notes,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(report))
    return 1 if args.strict and report["collection_status"] == "partial" else 0


if __name__ == "__main__":
    sys.exit(main())
