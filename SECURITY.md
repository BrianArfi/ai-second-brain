# Security policy

## Supported versions

The latest tagged release of this template is the only supported version.
There are no backports.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private reporting: the **Security** tab of this repository, then
**Report a vulnerability**. If that is not available to you, open an empty issue
titled `security contact request` and the maintainer will reply with a private
channel.

Include what you can:

- what an attacker gets,
- the steps to reproduce it,
- the version or commit you tested,
- your operating system.

You get a first reply within 7 days. If a fix is needed, the maintainer agrees a
disclosure date with you before anything is published.

## What is in scope

- Anything in `.agent/scripts/`, `.claude/hooks/`, `setup/`, `dashboard/`, and
  `install.sh` in this repository.
- Any path where a workspace file can make a script run a command it should not.

## What is out of scope

- Vulnerabilities in Claude Code, in MCP servers, or in any third-party service
  this template connects to. Report those to their own maintainers.
- Anything that needs an attacker to already have write access to your
  workspace or your shell.
- Secrets that a user committed to their own fork.

## Secrets

This template never stores credentials in the repository. They live in `.env`,
in `token*.json`, or in the OS keychain, all of which are gitignored. If you
find a credential committed anywhere in this repository's history, report it
through the private channel above rather than opening an issue.
