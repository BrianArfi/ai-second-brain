---
name: Skill or command proposal
about: Propose a new capability before you build it
labels: proposal
---

**The work this automates**

<!-- Describe the real task, not the code. Who does it today and how often. -->

**Which layer it belongs in**

- [ ] Skill (`.agent/skills/`), a script that touches a real service
- [ ] Command (`.claude/commands/`), a procedure an agent follows
- [ ] Agent (`.claude/agents/`), a narrow specialist
- [ ] Hook (`.claude/hooks/`), a guard that runs automatically

**What it needs**

- Services or APIs:
- Credentials (names only, never values):
- Does it send, publish, or delete anything? If yes, how is approval gated?

**Why it belongs in the template rather than in your own fork**

<!-- The bar is: more than one person would use it. -->
