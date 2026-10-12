#!/usr/bin/env python3
"""Read-only evidence gathering for agent tool/skill intake decisions.

Python 3.10+, standard library only. This file ships as a standalone CLI, so the
module docstring is its public interface note; usage and examples live in --help.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
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


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:20000]
    except OSError:
        return None
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
            if candidate.is_dir():
                found.append(candidate)
        if current.parent == current or current == Path("/"):
            break
        current = current.parent
    return found


def collect_roots(extra: list[str]) -> tuple[list[Path], list[str]]:
    notes: list[str] = []
    roots: list[Path] = []
    for entry in USER_ROOTS:
        path = Path(os.path.expanduser(entry))
        if path.is_dir():
            roots.append(path)
    roots.extend(find_project_roots(Path.cwd()))
    for entry in extra:
        path = Path(os.path.expanduser(entry)).resolve()
        if path.is_dir():
            roots.append(path)
        else:
            notes.append(f"--roots path does not exist, skipped: {entry}")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in roots:
        real = str(path.resolve())
        if real in seen:
            continue
        seen.add(real)
        unique.append(path)
    return unique, notes


def inventory(roots: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    skills: list[dict[str, Any]] = []
    notes: list[str] = []
    for root in roots:
        count_before = len(skills)
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue
            meta = parse_frontmatter(skill_md)
            if meta is None:
                notes.append(f"unparseable frontmatter, skipped: {skill_md}")
                continue
            skills.append(
                {
                    "name": meta["name"],
                    "description": meta.get("description", ""),
                    "path": str(skill_md),
                    "root": str(root),
                }
            )
        if len(skills) == count_before:
            notes.append(f"no skills found under: {root}")
    return skills, notes


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


def upstream_github(owner: str, repo: str) -> dict[str, Any]:
    result: dict[str, Any] = {"repo": f"{owner}/{repo}", "warnings": []}
    data, error = http_json(f"https://api.github.com/repos/{owner}/{repo}")
    if error or not data:
        result["warnings"].append(error or "no data")
        return result

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
            days = (dt.date.today() - dt.date.fromisoformat(pushed)).days
            result["days_since_push"] = days
        except ValueError:
            pass
    result["description"] = data.get("description") or ""

    readme, readme_error = http_json(f"https://api.github.com/repos/{owner}/{repo}/readme")
    if readme_error or not readme:
        result["warnings"].append(readme_error or "no readme")
        return result
    try:
        content = base64.b64decode(readme.get("content", "")).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append(f"readme decode failed: {exc}")
        return result
    result["readme_bytes"] = len(content)
    result["red_flags"] = scan_red_flags(content)
    result["readme_head"] = " ".join(content.split())[:1500]
    return result


def local_target(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"repo": str(path), "local": True, "warnings": []}
    text = ""
    for name in ("README.md", "readme.md", "README", "SKILL.md"):
        candidate = path / name if path.is_dir() else path
        if candidate.is_file():
            try:
                text += candidate.read_text(encoding="utf-8", errors="replace")
                text += "\n"
            except OSError as exc:
                result["warnings"].append(f"{candidate}: {exc}")
    if not text:
        result["warnings"].append("no README.md / SKILL.md found at target")
        return result
    result["readme_bytes"] = len(text)
    result["red_flags"] = scan_red_flags(text)
    result["readme_head"] = " ".join(text.split())[:1500]
    return result


def scan_red_flags(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for label, pattern in RED_FLAGS.items():
        found = re.findall(pattern, text, flags=re.IGNORECASE)
        if found:
            hits[label] = sorted({f if isinstance(f, str) else f[0] for f in found})[:5]
    return hits


def dedup_scan(keywords: set[str], skills: list[dict[str, Any]], top: int) -> list[dict[str, Any]]:
    scored = []
    for skill in skills:
        skill_tokens = tokenize(skill["name"] + " " + skill["description"])
        matched = sorted(keywords & skill_tokens)
        if not matched:
            continue
        score = len(matched) / ((len(keywords) ** 0.5) * (len(skill_tokens) ** 0.5))
        strength = "strong" if len(matched) >= 3 else ("overlap" if len(matched) == 2 else "weak")
        scored.append(
            {
                "name": skill["name"],
                "root": skill["root"],
                "matched": matched,
                "score": round(score, 3),
                "strength": strength,
            }
        )
    scored.sort(key=lambda item: (-len(item["matched"]), -item["score"], item["name"]))
    return scored[:top]


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    add("# Tool intake audit")
    add("")
    add(f"- Target: {report['target'] or '(none, inventory only)'}")
    add(f"- Generated: {report['generated']}")
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
        else:
            add("### Preflight red flags in project text")
            add("- none detected")
        if up.get("warnings"):
            add("")
            add(f"- Warnings: {'; '.join(up['warnings'])}")
        add("")

    add("## 2. Installed skill inventory")
    add("")
    for entry in report["inventory_roots"]:
        add(f"- {entry['root']} ({entry['count']} skill(s))")
    add(f"- Total distinct skills: {len(report['skills'])}")
    for note in report["notes"]:
        add(f"- note: {note}")
    add("")

    add("## 3. Duplicate-function scan")
    add("")
    matched_tokens = sorted({token for item in report["overlaps"] for token in item["matched"]})
    add(f"- Keywords considered: {len(report['keywords'])} | matched: {', '.join(matched_tokens) or '(none)'}")
    if report["overlaps"]:
        add("")
        add("| skill | root | matched tokens | strength |")
        add("|---|---|---|---|")
        for item in report["overlaps"]:
            add(f"| {item['name']} | {item['root']} | {', '.join(item['matched'])} | {item['strength']} |")
    else:
        add("- no installed skill shares a keyword with the candidate")
    add("")

    add("## 4. Verdict skeleton (fill in)")
    add("")
    add("- decision: install | decline")
    add("- scope: user-global | project | archive")
    add("- reason:")
    add("- date:")
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
    parser.add_argument("--top", type=int, default=8, help="max overlap rows (default 8)")
    parser.add_argument("--no-network", action="store_true", help="skip live GitHub lookups")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    roots, notes = collect_roots(args.roots)
    skills, inv_notes = inventory(roots)
    notes.extend(inv_notes)

    inventory_roots = []
    for root in roots:
        inventory_roots.append(
            {"root": str(root), "count": sum(1 for skill in skills if skill["root"] == str(root))}
        )

    upstream: dict[str, Any] | None = None
    keywords = {token for token in tokenize(args.keywords.replace(",", " "))}

    if args.target:
        github = parse_github(args.target)
        target_path = Path(os.path.expanduser(args.target))
        if github and not args.no_network:
            upstream = upstream_github(*github)
            keywords |= tokenize(upstream.get("repo", ""))
            keywords |= tokenize(upstream.get("description", ""))
            keywords |= tokenize(strip_urls(upstream.get("readme_head", "")))
        elif github and args.no_network:
            keywords |= tokenize(github[0] + " " + github[1])
            notes.append("network lookups skipped (--no-network)")
        elif target_path.exists():
            upstream = local_target(target_path)
            keywords |= tokenize(target_path.name)
            keywords |= tokenize(strip_urls(upstream.get("readme_head", "")))
        else:
            keywords |= tokenize(args.target)
            notes.append(f"target is neither a GitHub URL nor an existing path: {args.target}")

    keywords = {token for token in keywords if len(token) >= 3 and token not in STOPWORDS}

    report = {
        "target": args.target or "",
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "upstream": upstream,
        "inventory_roots": inventory_roots,
        "skills": skills,
        "overlaps": dedup_scan(keywords, skills, args.top),
        "keywords": sorted(keywords),
        "notes": notes,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
