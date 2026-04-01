# Claude Code Instructions

## Session start
Before reading any files, sync with the latest code:
```bash
git stash           # park dirty files (.env, __pycache__, etc.)
git pull --rebase origin main
git stash pop
```
Then read README.md and HANDOFF.md to get up to speed.

## Git / push rules
- Never push to GitHub unless explicitly asked ("push this" or "commit and push")
- Batch changes — make multiple edits in a session before pushing
- Never commit: `.env`, `__pycache__/`, `*.pyc`
- GitHub Actions pushes agent data files (`swing/`, `long_term/`) to main daily — the remote will often be ahead of local; always pull before pushing

## Project context
- See README.md for full project overview (agents, file structure, how it works)
- See HANDOFF.md for current session state and recent changes
