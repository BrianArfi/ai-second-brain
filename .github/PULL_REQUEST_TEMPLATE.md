## What this changes

<!-- One or two sentences. What is different after this is merged. -->

## Why

<!-- The problem. Link the issue if there is one. -->

## How it was tested

<!-- Which operating system, and what you actually ran. "Ran the skill against a
     real Slack workspace on macOS 15" is worth more than a green checkmark. -->

## Checklist

- [ ] No credential, real client name, home path, email, or ticket key is in the diff
- [ ] `python3 tools/repo_check.py` passes
- [ ] A new skill has `SKILL.md` frontmatter with `name` and `description`
- [ ] Anything that sends, publishes, or deletes is behind an `--approved` flag
- [ ] Docs updated if a user has to do something differently

## Files that may need to travel upstream

<!-- Parts of this repository are generated from a private upstream, so a patch
     to a generated file can be overwritten by the next export. List the files
     you touched under .agent/ or .claude/ so the maintainer can carry them
     back. See CONTRIBUTING.md. -->
