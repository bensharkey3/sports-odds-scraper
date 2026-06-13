# CLAUDE.md

## What this project does

Scrapes AFL and FIFA World Cup 2026 odds from the Sportsbet API and writes JSONL files to S3. Runs as an AWS Lambda function triggered by EventBridge Scheduler every hour, 24/7, Melbourne time. The purpose is to create data that can be analysed later.

Two downstream Lambdas run as a chain after the scraper (each async-invokes the next on completion): the **parquet builder** (`parquet_builder.py`) consolidates each market's JSONL history into `parquet/<market>.parquet` (columns: `date`, `selection`, `odds`), and the **chart builder** (`chart_builder.py`) renders an odds-over-time line chart to `charts/<market>.png`. See `README.md` for the architecture diagram and output formats.

## Priorities

- Minimise AWS costs.
- Prefer simple, serverless, pay per use AWS services.
- Prefer AWS free tier eligible services where suitable.


## Allowed technologies

- Preference for SQL and Python.
- Use Terraform for infrastructure as code.
- Use AWS as cloud provider.

## Engineering rules

- Jobs should be idempotent.
- Never hardcode secrets, tokens or passwords.
- Keep code and architecture simple.

## Git workflow

- Never commit directly to main branch.
- Before making any code changes, check which branch you're on. If the current branch has been deleted from remote then always pull from main before creating a new branch from main before making any code changes.
- After completing any code changes, commit the changes, and raise a PR without waiting to be asked.
