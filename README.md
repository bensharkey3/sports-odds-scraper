# Sports Odds Scraper

Scrapes AFL odds from the Sportsbet API and writes JSONL files to S3. Runs as an AWS Lambda function on a scheduled trigger every 2 hours between 9am–9pm Melbourne time.

## What it does

Each run scrapes three markets:

| Market | Description |
|--------|-------------|
| **H2H match odds** | Head-to-head odds for all upcoming AFL matches |
| **Brownlow Medal** | Winner odds for every Brownlow Medal candidate |
| **Premiership Winner** | Winner odds for all 18 AFL teams to win the premiership |

After each run it:
- Writes JSONL results to S3 (timestamped + latest)
- Sends a Slack summary notification
- Detects when the betting favourite has changed and sends a separate Slack alert

## Architecture

```
EventBridge Scheduler (every 2hrs, 9am–9pm AEST/AEDT)
    └── Lambda: sports-odds-scraper
            ├── Sportsbet API  (fetch H2H, Brownlow, and Premiership Winner markets)
            ├── S3             (write JSONL results)
            ├── S3             (read historical files for favourite change detection)
            ├── SSM            (fetch Slack webhook URLs)
            └── Slack          (scrape summary + favourite change alerts)
```

All infrastructure is managed with Terraform and deployed to AWS `ap-southeast-2`.

## Output format

### H2H match odds

S3 paths: `odds/YYYY/MM/DD/HH-MM-SSZ.jsonl` (timestamped) and `odds/latest.jsonl`

One record per match:

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

### Brownlow Medal

S3 paths: `brownlow/YYYY/MM/DD/HH-MM-SSZ.jsonl` (timestamped) and `brownlow/latest.jsonl`

One record per candidate player:

```json
{
  "event_id": 9641792,
  "event_name": "2026 AFL Brownlow Medal",
  "scraped_at": "2026-05-24T09:00:00Z",
  "start_time": "2027-07-21T09:30:00Z",
  "betting_status": "PRICED",
  "player": "Bailey Smith",
  "odds": 4.0,
  "market_status": "A"
}
```

### Premiership Winner

S3 paths: `premiership/YYYY/MM/DD/HH-MM-SSZ.jsonl` (timestamped) and `premiership/latest.jsonl`

One record per AFL team:

```json
{
  "event_id": 9641840,
  "event_name": "AFL Premiership Winner 2026",
  "scraped_at": "2026-05-24T09:00:00Z",
  "start_time": "2026-09-26T09:30:00Z",
  "betting_status": "PRICED",
  "team": "Fremantle",
  "odds": 5.5,
  "market_status": "A"
}
```

## Slack notifications

| Channel | When | Example |
|---------|------|---------|
| `sports-odds-scraper` | After every successful scrape | `✅ AFL odds scraped: 8 games at 2026-05-24T09:00:00Z` |
| `sports-odds-scraper` (favourite alerts) | When the favourite flips for any market | `Richmond v Carlton - the favourite has changed to Carlton` |

Webhook URLs are stored in AWS SSM Parameter Store as `SecureString`:

| Parameter | Used for |
|-----------|----------|
| `/afl-odds/slack-webhook` | Scrape summary |
| `/afl-odds/slack-webhook-favourite` | Favourite change alerts (H2H, Brownlow, Premiership) |

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
