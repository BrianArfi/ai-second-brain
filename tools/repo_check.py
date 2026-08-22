#!/usr/bin/env python3
"""Check that this template is safe to hand to a stranger.

This is the gate CONTRIBUTING.md points at. It answers three questions about the
committed tree, in order of how much damage a miss would do:

1. Does anything personal ship here? Client names, home paths, real Drive ids,
   emails, ticket keys, credentials. This is the one that must never pass.
2. Does every script at least parse? A skill that dies on import is worse than a
   skill that does not exist, because the user believes they have the capability.
3. Does every skill and command carry the frontmatter an agent reads to decide
   whether to use it?

(1) and (2) fail the build. (3) reports and does not fail, because a number of
skills predate the frontmatter convention and are fixed as they are touched.
Making it fatal today would leave main red, and a permanently red check is a
check everybody learns to ignore.

Usage:
    python3 tools/repo_check.py              # check the whole repo
    python3 tools/repo_check.py --strict     # also fail on missing frontmatter
    python3 tools/repo_check.py --selftest   # test the rules themselves, no I/O
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TEXT_SUFFIXES = {".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".py", ".sh", ".js", ""}

# Paths that legitimately contain the shapes below and are not leaks.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "docs/carousel", "docs/workshop"}
SKIP_FILES = {"tools/repo_check.py", "LICENSE", "NOTICE", "CHANGELOG.md",
              "tools/repo_check_baseline.txt"}

# Findings that already exist and are tracked as debt rather than fixed in a
# rush. CI fails on anything NOT in here, so the repo can only get cleaner.
# The real fix for most of these is upstream, in the scrub pass of the private
# export, not a hand edit here: a hand edit to a generated file is undone by the
# next export. Regenerate with:  python3 tools/repo_check.py --write-baseline
BASELINE_PATH = REPO_ROOT / "tools" / "repo_check_baseline.txt"


def load_baseline() -> set:
    if not BASELINE_PATH.exists():
        return set()
    return {
        line.strip()
        for line in BASELINE_PATH.read_text(encoding="utf-8").split("\n")
        if line.strip() and not line.startswith("#")
    }

# Real client names the private upstream harness works with. Case-sensitive on a
# whole word: several are ordinary English words in lowercase, and matching
# case-insensitively would flag harmless prose far more often than a real leak.
CLIENT_NAMES = ["Merit", "Flip", "Entertainer", "Saudia", "Salasa", "Giftomatic", "HSI", "Tintash", "Gogogo", "Safaraya"]

# Acronyms shaped like a Jira key. Extend this rather than loosening the pattern.
JIRA_EXCEPTIONS = {
    "UTF", "SHA", "TLS", "SSL", "HTTP", "HTTPS", "GPT", "OAUTH", "RFC", "ISO",
    "MD5", "JSON", "YAML", "CSS", "HTML", "URL", "URI", "API", "PDF", "PNG",
    "JPG", "SQL", "CLI", "SDK", "OS", "ID", "UI", "OK", "IP", "CI", "CD", "AI",
    "ML", "LLM", "MCP", "UUID", "CSV", "XML", "SVG", "TCP", "UDP", "WSL", "RGB",
    "P0", "P1", "P2", "P3", "GB", "MB", "KB", "AM", "PM", "V1", "V2", "SPF",
    # Not ticket keys: crypto suites, model names, and user-story numbering in
    # the example scripts. Every one of these was a false positive on the real
    # tree, and a checker that cries wolf is a checker nobody reads.
    "AES", "RSA", "SHA1", "SHA2", "GLM", "US", "ES", "HS", "RS", "PS", "GPT4",
    "IPV4", "IPV6", "BASE64", "UTF8", "ISO8601", "EC", "DH", "X25519",
    # This harness's own ledger id prefixes. They appear all over the skill
    # docs as examples and are not anybody's ticket.
    "COM", "DEC", "WAIT", "CHASE", "XX", "YY", "ABC",
}

# Local parts that are obviously a stand-in. Anything else in front of a
# placeholder domain is a real person's name with the domain filed off, which is
# exactly the leak that is easiest to miss by eye.
PLACEHOLDER_LOCALS = {
    "you", "your-email", "youremail", "user", "username", "me", "name",
    "owner", "addr", "address", "someone", "example", "test", "admin",
    "first.last", "firstname.lastname", "email", "teammate", "owner.local",
    "colleague", "manager", "recipient", "sender",
}
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "yourcompany.com",
                       "yourdomain.com", "company.com", "examplevendor.com", "exampleclient.com"}

# Home-directory segments that are placeholders in prose, not a real account.
# Compared upper-cased with dashes removed, the same way Rule.hits normalizes.
PLACEHOLDER_USERS = {"U", "YOU", "USER", "USERNAME", "ME", "NAME", "YOURUSER", "THE", "YOURNAME"}

# Example values that are meant to be here: docs need something to point at.
ALLOWED_LITERALS = {
    "you@example.com", "user@example.com", "name@example.com",
    "/Users/you", "/home/you", "/Users/username", "/home/username",
}


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern
    reason: str
    exceptions: frozenset = frozenset()

    def hits(self, line: str) -> list:
        out = []
        for m in self.pattern.finditer(line):
            text = m.group(0)
            if text in ALLOWED_LITERALS:
                continue
            if self.name == "email":
                local, domain = m.group(1).lower(), m.group(2).lower()
                if domain in PLACEHOLDER_DOMAINS and local in PLACEHOLDER_LOCALS:
                    continue
            elif self.exceptions and m.lastindex and m.group(1).upper().replace("-", "") in self.exceptions:
                continue
            out.append(text)
        return out


def rules() -> list:
    names = "|".join(re.escape(n) for n in CLIENT_NAMES)
    return [
        Rule("client-name", re.compile(rf"\b(?:{names})\b"),
             "a real client name. Use ExampleClient."),
        Rule("home-path", re.compile(r"(?:/home/|/Users/)([A-Za-z0-9._-]+)"),
             "a real home directory. Use ~ or an environment variable.",
             frozenset(PLACEHOLDER_USERS)),
        Rule("drive-id", re.compile(r"(?:docs|drive)\.google\.com/\S*?/d/([A-Za-z0-9_-]{25,})"),
             "a real Google Drive or Docs id."),
        Rule("email", re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
             "a real email address, or a real person's name on a placeholder domain."),
        Rule("jira-key", re.compile(r"\b([A-Z]{2,10})-\d+\b"), "a real ticket key.",
             frozenset(JIRA_EXCEPTIONS)),
        Rule("secret", re.compile(r"\b(?:xox[bpsa]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})"),
             "a credential. Rotate it now, then remove it."),
    ]


def tracked_files() -> list:
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout.split("\n")
    keep = []
    for rel in out:
        if not rel:
            continue
        if rel in SKIP_FILES or any(rel.startswith(d + "/") or rel == d for d in SKIP_DIRS):
            continue
        p = REPO_ROOT / rel
        if p.suffix.lower() in TEXT_SUFFIXES and p.is_file():
            keep.append(rel)
    return keep


def scan_personal_data(files: list) -> list:
    found = []
    checks = rules()
    for rel in files:
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for rule in checks:
                for hit in rule.hits(line):
                    found.append(f"{rel}:{lineno}  [{rule.name}] {hit}  -- {rule.reason}")
    return found


def scan_syntax(files: list) -> list:
    broken = []
    for rel in files:
        p = REPO_ROOT / rel
        if p.suffix == ".py":
            try:
                ast.parse(p.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError as e:
                broken.append(f"{rel}:{e.lineno}  python syntax error: {e.msg}")
        elif p.suffix == ".sh":
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            if r.returncode != 0:
                broken.append(f"{rel}  bash syntax error: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'unknown'}")
    return broken


def scan_frontmatter() -> list:
    missing = []
    # A command's name comes from its filename, so only `description:` is
    # required there. A skill and an agent are addressed by name, so both keys
    # have to be present.
    targets = [(p, ("name:", "description:")) for p in (REPO_ROOT / ".agent" / "skills").glob("*/SKILL.md")]
    targets += [(p, ("name:", "description:")) for p in (REPO_ROOT / ".claude" / "agents").glob("*.md")]
    targets += [(p, ("description:",)) for p in (REPO_ROOT / ".claude" / "commands").glob("*.md")]
    for p, required in targets:
        rel = p.relative_to(REPO_ROOT).as_posix()
        lines = p.read_text(encoding="utf-8").split("\n")
        if not lines or lines[0].strip() != "---":
            missing.append(f"{rel}  no YAML frontmatter")
            continue
        block = []
        for line in lines[1:]:
            if line.strip() == "---":
                break
            block.append(line)
        body = "\n".join(block)
        for key in required:
            if key not in body:
                missing.append(f"{rel}  frontmatter is missing `{key}`")
    return missing


def selftest() -> int:
    cases_bad = [
        "the Merit weekly report",
        "cd /Users/brianarfi/repos",
        "see https://docs.google.com/document/d/1NjK3UX7DqEzgt2Ox7niVWue2ZptEiCntySls_T4DwHU/edit",
        "ping brian@example.org",
        "ticket MP-1234 is open",
        "token xoxb-1234567890-abcdefghijkl",
    ]
    cases_ok = [
        "the example client weekly report",
        "cd ~/repos",
        "see the linked document",
        "https://docs.google.com/document/d/<YOUR_DRIVE_ID>/edit",
        "https://docs.google.com/document/d/{args.id}/edit",
        "write to you@example.com",
        "the UTF-8 encoding, HTTP-2, and P0 items",
        "token from token.env",
    ]
    checks = rules()
    failures = 0
    for line in cases_bad:
        if not any(r.hits(line) for r in checks):
            print(f"SELFTEST FAIL: should have flagged: {line}")
            failures += 1
    for line in cases_ok:
        hit = [(r.name, r.hits(line)) for r in checks if r.hits(line)]
        if hit:
            print(f"SELFTEST FAIL: false positive on: {line} -> {hit}")
            failures += 1
    print("selftest: ok" if not failures else f"selftest: {failures} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="also fail on missing frontmatter")
    ap.add_argument("--selftest", action="store_true", help="test the rules, touch no files")
    ap.add_argument("--write-baseline", action="store_true",
                    help="record today's findings as accepted debt")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    files = tracked_files()
    print(f"checking {len(files)} tracked text files")

    fatal = 0

    leaks = scan_personal_data(files)

    if args.write_baseline:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Accepted personal-data findings, one per line.\n"
            "# CI fails on anything NOT listed here, so this file can only shrink.\n"
            "# Most of these are fixed upstream in the export scrub, not by hand.\n"
        )
        BASELINE_PATH.write_text(header + "\n".join(sorted(leaks)) + "\n", encoding="utf-8")
        print(f"wrote {len(leaks)} finding(s) to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    baseline = load_baseline()
    fresh = [l for l in leaks if l not in baseline]
    stale = len(baseline) - (len(leaks) - len(fresh))
    if fresh:
        print(f"\nNEW PERSONAL DATA: {len(fresh)} finding(s)")
        for line in fresh:
            print(f"  {line}")
        fatal += 1
    else:
        print(f"personal data: no new findings ({len(baseline)} accepted in baseline)")
    if stale > 0:
        print(f"personal data: {stale} baseline entr(ies) now fixed. Run --write-baseline to shrink the file.")

    broken = scan_syntax(files)
    if broken:
        print(f"\nSYNTAX: {len(broken)} broken script(s)")
        for line in broken:
            print(f"  {line}")
        fatal += 1
    else:
        print("syntax: clean")

    missing = scan_frontmatter()
    if missing:
        label = "FRONTMATTER" if args.strict else "frontmatter (warning)"
        print(f"\n{label}: {len(missing)} file(s)")
        for line in missing:
            print(f"  {line}")
        if args.strict:
            fatal += 1
    else:
        print("frontmatter: clean")

    print("\nFAILED" if fatal else "\nOK")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
