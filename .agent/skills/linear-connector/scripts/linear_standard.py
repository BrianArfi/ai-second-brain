"""The Work Linear filing standard, as checks the connector runs before it writes.

Source of truth is Handbook Part 2 (LINEAR.md): 4.1 initiatives, 5 projects,
6 issues, 9 labels and priority. This file only encodes what a machine can
check. It never talks to the network. Each check returns a list of problems;
an empty list means the draft meets the standard.
"""
import re

ISSUE_TYPES = ["Bug", "Feature", "Task", "Improvement", "Spike"]
PRIORITIES = {"urgent": 1, "high": 2, "medium": 3, "low": 4}
SIZES = ["XS", "S", "M", "L", "XL", "XXL"]

HEAD_FEATURE = ["Context", "Acceptance criteria", "Out of scope", "Links"]
HEAD_BUG = ["Steps to reproduce", "Expected", "Actual", "Environment", "Evidence", "Impact"]
HEAD_PROJECT = ["Problem", "Goal", "Success metric", "Scope", "Out of scope", "Depends on", "Links"]
HEAD_INITIATIVE = ["Outcome", "Success metric", "Owner and squads", "Links"]

def _headings(text):
    return {m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+?)\s*$", text or "", re.M)}

def _missing(text, required):
    have = _headings(text)
    return [h for h in required if h.lower() not in have]

def check_issue_title(title):
    out = []
    if not title or len(title) > 80:
        out.append("Title must be 80 characters or fewer.")
    if re.search(r"\b[A-Z]{2,6}-\d+\b", title or ""):
        out.append("No ticket number in the title.")
    if re.match(r"^(example program|exampleprogram|stc|sab|exampleco)\s*[:\-]", (title or ""), re.I):
        out.append("No client prefix in the title. Use the Client label.")
    return out

def check_issue(title, description, type_):
    out = check_issue_title(title)
    if type_ not in ISSUE_TYPES:
        out.append(f"Type must be one of {ISSUE_TYPES}.")
        return out
    if type_ == "Bug":
        miss = _missing(description, HEAD_BUG)
    elif type_ == "Spike":
        low = (description or "").lower()
        miss = [] if ("timebox" in low and "unlock" in low) else ["question, timebox (max 3 days) and what the answer unlocks"]
    else:
        miss = _missing(description, HEAD_FEATURE)
    if miss:
        out.append(f"Description lacks: {', '.join(miss)} (LINEAR.md 6.2).")
    if type_ in ("Feature", "Task", "Improvement") and not re.search(r"\bgiven\b.*\bwhen\b.*\bthen\b", description or "", re.I | re.S):
        out.append("Acceptance criteria need at least one Given / when / then line.")
    return out

def triage_gaps(type_, squad, project, priority):
    """Fields an issue needs before it leaves Triage (LINEAR.md 6.1). Not errors."""
    gaps = []
    if not type_:
        gaps.append("Type label")
    if not squad:
        gaps.append("Squad label")
    if not project:
        gaps.append("project")
    if not priority:
        gaps.append("priority")
    return gaps

def check_project(name, description, lead, product, size, target_date=None):
    out = []
    # the owner, 17 Nov 2026 (handbook v3.2): a project is a dated deliverable that can take issues
    # from several epics. An epic is a parent issue with sub-issues, never a project.
    if not target_date and not (name or "").rstrip().lower().endswith("maintenance"):
        out.append("A target date is required. If this is an epic, file it as a parent issue "
                   "with the stories as sub-issues, not as a project (handbook v3.2).")
    if not re.match(r"^[^:]{2,40}:\s+\S.{2,}$", name or ""):
        out.append("Name must be '<Product>: <Deliverable>' (LINEAR.md 5.2).")
    miss = _missing(description, HEAD_PROJECT)
    if miss:
        out.append(f"Description lacks: {', '.join(miss)} (LINEAR.md 5.2).")
    if not lead:
        out.append("A lead is required.")
    if not product:
        out.append("Exactly one Product project label is required.")
    if size and size.upper() not in SIZES:
        out.append(f"Size must be one of {SIZES}.")
    elif not size:
        out.append("One Size label is required.")
    return out

def check_initiative(name, description, owner, target_date):
    out = []
    if not name:
        out.append("A name is required.")
    miss = _missing(description, HEAD_INITIATIVE)
    if miss:
        out.append(f"Description lacks: {', '.join(miss)} (LINEAR.md 4.1).")
    if not owner:
        out.append("One owner is required.")
    if not target_date:
        out.append("An end date is required. Without a metric and a date it is not an initiative.")
    sec = re.search(r"^##\s+Success metric\s*\n(.+?)(?=^##|\Z)", description or "", re.M | re.S | re.I)
    if sec is None or not re.search(r"\d", sec.group(1)):
        out.append("Success metric must contain a number and a date.")
    return out
