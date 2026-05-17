# ── Configuration ────────────────────────────────────────────────────────────
ENV    ?= dev
REGION ?= ap-southeast-2

ACCOUNT_ID    := $(shell aws sts get-caller-identity --query Account --output text)
ARTIFACT_BUCKET := afl-odds-artifacts-$(ACCOUNT_ID)
ARTIFACT_KEY    := afl-odds/lambda.zip
STACK_NAME      := afl-odds-$(ENV)
PARAM_FILE      := infrastructure/parameters/$(ENV).json

BUILD_DIR := build
ZIP_PATH  := $(BUILD_DIR)/lambda.zip

.PHONY: help bootstrap build upload deploy destroy invoke logs

help:
	@echo ""
	@echo "  make bootstrap          Create artifact S3 bucket (run once per account)"
	@echo "  make build              Package Lambda with dependencies"
	@echo "  make upload             Upload zip to S3"
	@echo "  make deploy  [ENV=dev]  Deploy/update CloudFormation stack"
	@echo "  make destroy [ENV=dev]  Delete CloudFormation stack"
	@echo "  make invoke  [ENV=dev]  Manually invoke the Lambda"
	@echo "  make logs    [ENV=dev]  Tail CloudWatch logs"
	@echo ""

# ── Bootstrap (once per account) ─────────────────────────────────────────────
bootstrap:
	@echo "Creating artifact bucket: $(ARTIFACT_BUCKET)"
	aws s3 mb s3://$(ARTIFACT_BUCKET) --region $(REGION) 2>/dev/null || true
	aws s3api put-bucket-versioning \
		--bucket $(ARTIFACT_BUCKET) \
		--versioning-configuration Status=Enabled

# ── Build ─────────────────────────────────────────────────────────────────────
build:
	@echo "Building Lambda package..."
	rm -rf $(BUILD_DIR)
	mkdir -p $(BUILD_DIR)/package
	pip install -r src/requirements.txt -t $(BUILD_DIR)/package/ --quiet
	cp src/handler.py $(BUILD_DIR)/package/
	cd $(BUILD_DIR)/package && zip -r ../lambda.zip . -x "*.pyc" -x "*/__pycache__/*" -x "*.dist-info/*"
	@echo "Built $(ZIP_PATH)"

# ── Upload ────────────────────────────────────────────────────────────────────
upload: build
	@echo "Uploading to s3://$(ARTIFACT_BUCKET)/$(ARTIFACT_KEY)"
	aws s3 cp $(ZIP_PATH) s3://$(ARTIFACT_BUCKET)/$(ARTIFACT_KEY) --region $(REGION)

# ── Deploy ────────────────────────────────────────────────────────────────────
deploy: upload
	@echo "Deploying stack: $(STACK_NAME) (ENV=$(ENV))"
	aws cloudformation deploy \
		--template-file infrastructure/template.yaml \
		--stack-name $(STACK_NAME) \
		--parameter-overrides \
			$$(jq -r '.[] | "\(.ParameterKey)=\(.ParameterValue)"' $(PARAM_FILE) | tr '\n' ' ') \
			ArtifactBucket=$(ARTIFACT_BUCKET) \
			ArtifactKey=$(ARTIFACT_KEY) \
		--capabilities CAPABILITY_NAMED_IAM \
		--region $(REGION) \
		--no-fail-on-empty-changeset
	@echo ""
	@echo "Stack outputs:"
	aws cloudformation describe-stacks \
		--stack-name $(STACK_NAME) \
		--region $(REGION) \
		--query "Stacks[0].Outputs" \
		--output table

# ── Destroy ───────────────────────────────────────────────────────────────────
destroy:
	@echo "Deleting stack: $(STACK_NAME)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	aws cloudformation delete-stack --stack-name $(STACK_NAME) --region $(REGION)
	aws cloudformation wait stack-delete-complete --stack-name $(STACK_NAME) --region $(REGION)
	@echo "Stack deleted"

# ── Invoke manually ───────────────────────────────────────────────────────────
invoke:
	aws lambda invoke \
		--function-name afl-odds-scraper-$(ENV) \
		--region $(REGION) \
		--log-type Tail \
		--query "LogResult" \
		--output text \
		/tmp/afl-odds-response.json | base64 --decode
	@echo ""
	@echo "Response:"
	@cat /tmp/afl-odds-response.json

# ── Logs ─────────────────────────────────────────────────────────────────────
logs:
	aws logs tail /aws/lambda/afl-odds-scraper-$(ENV) --follow --region $(REGION)
