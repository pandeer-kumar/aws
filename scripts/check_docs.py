#!/usr/bin/env python3
"""Layout + link checks for the study guide. Run: python3 scripts/check_docs.py"""
import glob, os, re, sys
errors = []
for f in sorted(glob.glob("**/*.md", recursive=True)):
    lines = open(f, encoding="utf-8").read().split("\n")
    in_fence = False
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "</summary>" in l and i + 1 < len(lines) and lines[i + 1].strip():
            errors.append(f"{f}:{i+1}: add a blank line after </summary>")
        if s == "</details>" and lines[i - 1].strip():
            errors.append(f"{f}:{i+1}: add a blank line before </details>")
        if l.startswith("|") and i and lines[i - 1].strip() and not lines[i - 1].startswith("|"):
            errors.append(f"{f}:{i+1}: add a blank line before the table")
        for m in re.finditer(r"\]\(([^)#\s]+\.md)(#[^)]*)?\)", l):
            target = os.path.normpath(os.path.join(os.path.dirname(f), m.group(1)))
            if not os.path.exists(target):
                errors.append(f"{f}:{i+1}: broken link -> {m.group(1)}")
    if in_fence:
        errors.append(f"{f}: unclosed code fence")
print("\n".join(errors) or "docs OK")
sys.exit(1 if errors else 0)
