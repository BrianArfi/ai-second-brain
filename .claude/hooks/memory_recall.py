#!/usr/bin/env python3
"""UserPromptSubmit hook: scoped memory recall.

MEMORY.md (the always-loaded index) lists the user and feedback files. The
rest of journal/memory/ (projects, references, anything not in that index)
never reaches the model unless somebody opens it. This hook keyword-matches
the prompt against each memory file's name and `description:` frontmatter and
prints up to MAX_HITS lines:

    Relevant memory: <name> - <description> (<path>)

Only files NOT already linked from MEMORY.md are considered. Stdlib only,
fast (reads the first lines of each file), and it never blocks a prompt:
any error prints nothing and exits 0.

Kill switch: MEMORY_RECALL_DISABLE=1.
"""
import json
import os
import pathlib
import re
import sys

MAX_HITS = 5
MAX_DESC = 170
MIN_PROMPT_CHARS = 12
# A token present in more than this many memory files is too generic to rank on.
MAX_DOC_FREQ = 14
HEAD_LINES = 14

STOPWORDS = set("""
a an and are as at be but by can could do does for from had has have how i if in into is it its just like me my
not of on or our so that the their them then there these they this to up us was we were what when where which who
will with would you your please need want make get let also about after before more some any all new now
yang dan di ke dari untuk ini itu dengan pada atau juga ada tidak bisa mau buat bikin tolong lagi sudah udah belum
apa gimana kenapa siapa kapan dimana mana aku saya kamu dia kita mereka nya lah kah deh dong sih aja saja
""".split())

def project_dir():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    return str(pathlib.Path(__file__).resolve().parent.parent.parent)

def stem(tok):
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok

def tokens(text):
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) < 3 or w in STOPWORDS or w.isdigit():
            continue
        out.add(stem(w))
    return out

def parse_head(path):
    """Return (name, description) from the YAML-ish frontmatter, tolerant."""
    name = ""
    desc = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
            if first.strip() != "---":
                return pathlib.Path(path).stem, ""
            for _ in range(HEAD_LINES):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                m = re.match(r"(name|description):\s*(.*)$", line.strip())
                if m:
                    val = m.group(2).strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    if m.group(1) == "name":
                        name = val
                    else:
                        desc = val
    except OSError:
        pass
    return name or pathlib.Path(path).stem, desc

def indexed_files(memdir):
    """Filenames linked from MEMORY.md (the always-loaded part)."""
    out = set()
    try:
        with open(os.path.join(memdir, "MEMORY.md"), encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r"\(([^)\s]+\.md)\)", f.read()):
                out.add(os.path.basename(m.group(1)))
    except OSError:
        pass
    return out

def catalogue(memdir):
    skip = indexed_files(memdir) | {"MEMORY.md"}
    items = []
    try:
        names = sorted(os.listdir(memdir))
    except OSError:
        return items
    for fn in names:
        if not fn.endswith(".md") or fn in skip or fn.startswith("INDEX_"):
            continue
        path = os.path.join(memdir, fn)
        name, desc = parse_head(path)
        fname_tokens = tokens(re.sub(r"[-_.]", " ", pathlib.Path(fn).stem) + " " + name.replace("_", " ").replace("-", " "))
        items.append({
            "file": fn,
            "path": path,
            "name": name,
            "desc": desc,
            "name_tokens": fname_tokens,
            "desc_tokens": tokens(desc),
        })
    return items

def recall(prompt, memdir, max_hits=MAX_HITS):
    if not prompt or len(prompt.strip()) < MIN_PROMPT_CHARS:
        return []
    ptoks = tokens(prompt)
    if not ptoks:
        return []
    items = catalogue(memdir)
    if not items:
        return []
    df = {}
    for it in items:
        for t in it["name_tokens"] | it["desc_tokens"]:
            df[t] = df.get(t, 0) + 1
    useful = {t for t in ptoks if df.get(t, 0) <= MAX_DOC_FREQ}
    scored = []
    for it in items:
        name_hits = len(useful & it["name_tokens"])
        desc_hits = len((useful & it["desc_tokens"]) - it["name_tokens"])
        # Needs a name hit, or two description hits, so one stray word never fires.
        if name_hits == 0 and desc_hits < 2:
            continue
        score = 3 * name_hits + desc_hits
        scored.append((score, it))
    scored.sort(key=lambda s: (-s[0], s[1]["file"]))
    lines = []
    for _, it in scored[:max_hits]:
        desc = it["desc"] or "(no description)"
        if len(desc) > MAX_DESC:
            desc = desc[: MAX_DESC - 3].rstrip() + "..."
        path = it["path"].replace("\\", "/")
        lines.append("Relevant memory: %s - %s (%s)" % (it["name"], desc, path))
    return lines

def main():
    if os.environ.get("MEMORY_RECALL_DISABLE") == "1":
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    prompt = data.get("prompt") or ""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    memdir = os.path.join(project_dir(), "journal", "memory")
    for line in recall(prompt, memdir):
        print(line)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
