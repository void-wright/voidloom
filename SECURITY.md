# Security policy

Voidloom runs on your own machine against a local SQLite database. It has no
server, no account system and no telemetry. The parts that matter most for
security are the ones that read your files, run on a schedule, or call an AI
model with a token you supply.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private reporting instead: go to the
[Security tab](https://github.com/void-wright/voidloom/security) of this
repository and choose **Report a vulnerability**. Only the maintainer sees it.

Please include:

- what an attacker could do, and what they need first (local access, a crafted
  memory, a malicious file in a watched folder, ...);
- the steps to reproduce it, and the Voidloom version (`voidloom --version`, plus `git rev-parse --short HEAD` if you run from a clone);
- your operating system and Python version.

You can expect an acknowledgement within 7 days. This is a one-person project, so
fixes are best-effort; a confirmed issue gets a fix or a documented mitigation
before details are made public, and you are credited unless you ask not to be.

## Supported versions

Only the latest release on `main` receives security fixes.

## Things that are by design

- **Locality is the access control.** The brain is protected by living on your
  local filesystem with your user's permissions; anyone who can already read your
  home directory can read your memories. Voidloom refuses to open a brain on a
  network or cloud-synced path, but it does not encrypt the database at rest.
- **Scheduled jobs are off by default.** Nothing runs on a timer unless you install
  with `--with-crons`. [`docs/FIRST_NIGHT.md`](docs/FIRST_NIGHT.md) lists every job
  and exactly what each one sends to a model.
- **No token ships with the package.** Jobs that call a model read a token you put
  in `~/.voidloom/wizard-library-env` (mode `0600`).
