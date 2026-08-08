from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


PATTERNS = {
    "OpenRouter API key": re.compile(r"sk-or-v1-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}"),
    "GitHub fine-grained token": re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    "OpenAI-style API key": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Assigned secret": re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*[\"'][^\"'<>${}\s]{16,}[\"']"
    ),
}

SKIP_PATHS = {"scripts/check_secrets.py"}
TEXT_SUFFIXES = {
    "",
    ".bat",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _git_paths(staged: bool) -> list[str]:
    command = (
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
        if staged
        else ["git", "ls-files", "-z"]
    )
    result = subprocess.run(command, check=True, capture_output=True)
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _content(path: str, staged: bool) -> str:
    if staged:
        result = subprocess.run(["git", "show", f":{path}"], check=True, capture_output=True)
        raw = result.stdout
    else:
        raw = Path(path).read_bytes()
    return raw.decode("utf-8", errors="replace")


def scan(staged: bool = False) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in _git_paths(staged):
        if path in SKIP_PATHS or Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = _content(path, staged)
        except (OSError, subprocess.CalledProcessError):
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path, line_number, label))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect likely credentials before they reach GitHub.")
    parser.add_argument("--staged", action="store_true", help="Scan the exact content staged for commit.")
    args = parser.parse_args()
    findings = scan(staged=args.staged)
    if findings:
        print("SECURITY CHECK FAILED: possible secret(s) detected:", file=sys.stderr)
        for path, line_number, label in findings:
            print(f"- {path}:{line_number}: {label}", file=sys.stderr)
        print("Remove the secret and rotate it if it was real. Never bypass this check.", file=sys.stderr)
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
