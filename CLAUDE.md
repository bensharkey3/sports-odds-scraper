# CLAUDE.md

## What this project does

Scrapes AFL head-to-head odds from the Sportsbet API and writes JSONL files to S3. Runs as an AWS Lambda function triggered by EventBridge Scheduler every 4 hours between 9am–9pm Melbourne time. The purpose is to create a data that can be analysed later.

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

- Always create a new branch for each change.
- Do not commit directly to main branch.
- Raise a pull request for approval. I must approve before merge.

