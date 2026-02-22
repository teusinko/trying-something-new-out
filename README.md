# Ranking watcher

A small Python watcher that polls this ranking page every few minutes:

- https://www.sodiwseries.com/en-gb/rankings/global/2026/slovakia-c37/junior-cup-3

When rankings change, it posts a snapshot of the latest table either:

- to stdout (default), or
- to a webhook URL (via `--webhook-url` or `WEBHOOK_URL`).

## Setup

No external packages are required (Python 3.10+).

## Run

```bash
python3 ranking_watcher.py --interval 300
```

### Post to a webhook

```bash
WEBHOOK_URL="https://your-webhook.example" python3 ranking_watcher.py --interval 300
```

## Useful flags

- `--always-post` posts every cycle (not just when a change is detected).
- `--state-file .ranking_state.json` controls where the last hash is persisted.
- `--timeout 20` sets request timeout.
- `--run-once` fetches one snapshot and exits.

## Example: run in background (Linux)

```bash
nohup python3 ranking_watcher.py --interval 300 > watcher.log 2>&1 &
```
