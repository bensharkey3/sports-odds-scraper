# AFL Odds Scraper

Scrapes AFL head-to-head odds from the Sportsbet API and writes JSONL files to S3. Runs as an AWS Lambda function on a scheduled trigger every 2 hours between 9am–9pm Melbourne time.

## What it does

- Fetches all upcoming AFL matches and their head-to-head markets from Sportsbet
- Writes one JSONL record per match to S3 after each run
- Sends a Slack notification summarising each scrape
- Detects when the betting favourite for a match has changed and sends a separate Slack alert

## Architecture

```
EventBridge Scheduler (every 2hrs, 9am–9pm AEST/AEDT)
    └── Lambda: afl-odds-scraper
            ├── Sportsbet API  (fetch events + H2H markets)
            ├── S3             (write JSONL results)
            ├── S3             (read historical files for favourite change detection)
            ├── SSM            (fetch Slack webhook URLs)
            └── Slack          (scrape summary + favourite change alerts)
```

All infrastructure is managed with Terraform and deployed to AWS `ap-southeast-2`.

## Output format

Each run produces two S3 files:

| Path | Description |
|------|-------------|
| `odds/YYYY/MM/DD/HH-MM-SSZ.jsonl` | Timestamped snapshot (retained 30 days in dev, 365 in prod) |
| `odds/latest.jsonl` | Overwritten each run — always the current snapshot |

Each line in the JSONL is one match:

```json
{
  "event_id": 123456,
  "match": "Richmond v Carlton",
  "start_time": "2026-05-24T09:30:00Z",
  "betting_status": "OPEN",
  "team1": "Richmond",
  "team1_odds": 1.85,
  "team2": "Carlton",
  "team2_odds": 2.05,
  "market_status": "Active"
}
```

## Slack notifications

| Channel | When | Example |
|---------|------|---------|
| `afl-odds-scraper` | After every successful scrape | `✅ AFL odds scraped: 8 games at 2026-05-24T09:00:00Z` |
| `afl-odds-scraper` (favourite alerts) | When the favourite flips for a match | `Richmond v Carlton - the favourite has changed to Carlton` |

Webhook URLs are stored in AWS SSM Parameter Store as `SecureString`:

| Parameter | Used for |
|-----------|----------|
| `/afl-odds/slack-webhook` | Scrape summary |
| `/afl-odds/slack-webhook-favourite` | Favourite change alerts |

## Project structure

```
├── src/
│   ├── handler.py          # Lambda handler — scraping, S3 writes, Slack notifications
│   └── requirements.txt    # Python dependencies (requests)
├── infrastructure/
│   ├── main.tf             # All AWS resources
│   ├── variables.tf
│   ├── outputs.tf
│   ├── terraform.tf        # Provider config
│   └── tfvars/
│       ├── dev.tfvars
│       └── prod.tfvars
└── Makefile                # Build, deploy, invoke, logs helpers
```

## Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform >= 1.0
- Python 3.12
- `make`

## Deployment

**First time (once per AWS account):**

```bash
make bootstrap   # creates the artifact S3 bucket
```

**Store Slack webhook URLs in SSM:**

```bash
aws ssm put-parameter --name "/afl-odds/slack-webhook" \
  --value "https://hooks.slack.com/..." --type SecureString --region ap-southeast-2

aws ssm put-parameter --name "/afl-odds/slack-webhook-favourite" \
  --value "https://hooks.slack.com/..." --type SecureString --region ap-southeast-2
```

**Deploy:**

```bash
make deploy           # deploys dev
make deploy ENV=prod  # deploys prod
```

## Running ad-hoc

```bash
make invoke           # invoke dev Lambda and tail logs
make invoke ENV=prod
```

## Viewing logs

```bash
make logs             # tail CloudWatch logs for dev
make logs ENV=prod
```
