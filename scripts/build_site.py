#!/usr/bin/env python3
"""Copy the guide into site_src/ and adapt GitHub-flavoured markdown for MkDocs Material.

- <details><summary>Q</summary> ... </details>  ->  ??? question "Q"  (collapsible admonition)
- inserts blank lines before lists (Python-Markdown needs them, GitHub doesn't)
- widens 2-space nested list indents to 4 spaces
- drops the GitHub-only prev/next footer (MkDocs renders its own)
"""
import glob, html, os, re, shutil

SRC_DIRS = ["01-foundations", "02-operating-aws", "03-architecture-deep-dives",
            "04-genai-bedrock", "05-interview-prep", "06-labs", "07-certification"]
OUT = "site_src"
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+")


def quotes_to_blocks(text: str) -> str:
    """Turn '> **Interviewer lens...' blockquotes into admonitions and fix lists inside other quotes."""
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if lines[i].startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(re.sub(r"^> ?", "", lines[i]))
                i += 1
            first = block[0]
            m = re.match(r"\*\*(Interviewer lens[^*]*?)[.:]?\*\*\s*(.*)", first)
            if m:
                kind, title, block[0] = "question", m.group(1), m.group(2)
            elif first.startswith("**Sources") or first.startswith("**Freshness") or first.startswith("Timelines"):
                kind, title = "info", None
            else:
                kind, title = "note", None
            body = []
            for b in block:  # blank line before list starts
                if LIST_RE.match(b) and body and body[-1].strip() and not LIST_RE.match(body[-1]):
                    body.append("")
                body.append(b)
            head = f'!!! {kind} "{title}"' if title else f"!!! {kind} \"\""
            out += ["", head, ""] + [("    " + b) if b.strip() else "" for b in body] + [""]
        else:
            out.append(lines[i]); i += 1
    return "\n".join(out)


def convert(text: str) -> str:
    text = text.split("<!-- nav -->")[0].rstrip() + "\n"
    text = quotes_to_blocks(text)
    out, in_fence, in_details = [], False, False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            m = re.match(r"<details><summary>(.*?)</summary>\s*$", s)
            if m:
                title = re.sub(r"</?b>", "", m.group(1))
                title = html.unescape(title).replace('"', "'")
                out += ["", f'??? question "{title}"', ""]
                in_details = True
                continue
            if s == "</details>":
                in_details = False
                out.append("")
                continue
            lm = LIST_RE.match(line)
            if lm:
                indent = len(lm.group(1))
                if indent and indent % 4:  # 2-space nesting -> 4-space
                    line = " " * (indent * 2) + line.lstrip()
                prev = out[-1] if out else ""
                prev_body = prev[4:] if in_details and prev.startswith("    ") else prev
                if prev_body.strip() and not LIST_RE.match(prev_body) and not prev_body.startswith("  "):
                    out.append("")
        if in_details:
            line = ("    " + line) if line.strip() else ""
        out.append(line)
    return "\n".join(out)


shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
files = ["README.md"] + [f for d in SRC_DIRS for f in sorted(glob.glob(f"{d}/*.md"))]
for f in files:
    dest = os.path.join(OUT, "index.md" if f == "README.md" else f)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    body = convert(open(f, encoding="utf-8").read())
    # links to the root README become links to the site home page
    body = re.sub(r"\((\.\./)*README\.md\)", lambda m: "(" + (m.group(1) or "") + "index.md)", body) \
        if f != "README.md" else body
    open(dest, "w", encoding="utf-8").write(body)
print(f"wrote {len(files)} pages to {OUT}/")
