# CLAUDE.md

## What this project does

Scrapes AFL odds from the Sportsbet API and writes JSONL files to S3. Runs as an AWS Lambda function triggered by EventBridge Scheduler every hour, 24/7, Melbourne time. (FIFA World Cup 2026 odds were also scraped until the tournament ended in July 2026; that scraping is now disabled via `WORLD_CUP_ENABLED = False`, with the code left dormant.) Scraping is currently paused for the AFL offseason — the prod schedule is disabled via `schedule_enabled = "DISABLED"` in `infrastructure/tfvars/prod.tfvars` (paused 13 October 2026; last data collected 22 September 2026), and is re-enabled by setting it back to `"ENABLED"`. The purpose is to create data that can be analysed later. See `README.md` for the architecture diagram and output formats.

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
