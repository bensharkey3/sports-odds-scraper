# ── Configuration ────────────────────────────────────────────────────────────
ENV    ?= dev
REGION ?= ap-southeast-2

ACCOUNT_ID    := $(shell aws sts get-caller-identity --query Account --output text)
ARTIFACT_BUCKET := afl-odds-artifacts-$(ACCOUNT_ID)
ARTIFACT_KEY    := afl-odds/lambda.zip
CHART_ARTIFACT_KEY := afl-odds/chart-lambda.zip

BUILD_DIR := build
ZIP_PATH  := $(BUILD_DIR)/lambda.zip
CHART_ZIP_PATH := $(BUILD_DIR)/chart/chart-lambda.zip

.PHONY: help bootstrap build build-chart upload upload-chart deploy destroy invoke logs

help:
	@echo ""
	@echo "  make bootstrap          Create artifact S3 bucket (run once per account)"
	@echo "  make build              Package Lambda with dependencies"
	@echo "  make build-chart        Package self-contained chart builder Lambda"
	@echo "  make upload             Upload zip to S3"
	@echo "  make upload-chart       Upload chart builder zip to S3"
	@echo "  make deploy  [ENV=dev]  Deploy/update Terraform infrastructure"
	@echo "  make destroy [ENV=dev]  Destroy Terraform infrastructure"
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
	cp src/handler.py src/parquet_builder.py $(BUILD_DIR)/package/
	cd $(BUILD_DIR)/package && zip -r ../lambda.zip . -x "*.pyc" -x "*/__pycache__/*" -x "*.dist-info/*"
	@echo "Built $(ZIP_PATH)"

# ── Build chart builder (self-contained: pandas + matplotlib + fastparquet) ────
# manylinux flags so a non-Linux host still produces Lambda-compatible wheels.
build-chart:
	@echo "Building chart builder package..."
	rm -rf $(BUILD_DIR)/chart
	mkdir -p $(BUILD_DIR)/chart/package
	pip install -r src/requirements-chart.txt -t $(BUILD_DIR)/chart/package/ \
		--platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
		--only-binary=:all: --quiet
	cp src/chart_builder.py $(BUILD_DIR)/chart/package/
	cd $(BUILD_DIR)/chart/package && zip -rq ../chart-lambda.zip . -x "*.pyc" -x "*/__pycache__/*" -x "*.dist-info/*"
	@echo "Built $(CHART_ZIP_PATH)"

# ── Upload ────────────────────────────────────────────────────────────────────
upload: build
	@echo "Uploading to s3://$(ARTIFACT_BUCKET)/$(ARTIFACT_KEY)"
	aws s3 cp $(ZIP_PATH) s3://$(ARTIFACT_BUCKET)/$(ARTIFACT_KEY) --region $(REGION)

upload-chart: build-chart
	@echo "Uploading to s3://$(ARTIFACT_BUCKET)/$(CHART_ARTIFACT_KEY)"
	aws s3 cp $(CHART_ZIP_PATH) s3://$(ARTIFACT_BUCKET)/$(CHART_ARTIFACT_KEY) --region $(REGION)

# ── Deploy ────────────────────────────────────────────────────────────────────
deploy: upload upload-chart
	@echo "Deploying $(ENV) environment..."
	cd infrastructure && terraform init -input=false && \
		(terraform workspace select $(ENV) 2>/dev/null || terraform workspace new $(ENV)) && \
		terraform apply -auto-approve \
			-var-file=tfvars/$(ENV).tfvars \
			-var="artifact_bucket=$(ARTIFACT_BUCKET)" \
			-var="artifact_key=$(ARTIFACT_KEY)"
	@echo ""
	@echo "Outputs:"
	@cd infrastructure && terraform workspace select $(ENV) 2>/dev/null && terraform output

# ── Destroy ───────────────────────────────────────────────────────────────────
destroy:
	@echo "Destroying $(ENV) environment..."
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	cd infrastructure && terraform workspace select $(ENV) && \
		terraform destroy -auto-approve \
			-var-file=tfvars/$(ENV).tfvars \
			-var="artifact_bucket=$(ARTIFACT_BUCKET)" \
			-var="artifact_key=$(ARTIFACT_KEY)"
	@echo "Destroyed"

# ── Invoke manually ───────────────────────────────────────────────────────────
invoke:
	aws lambda invoke \
		--function-name sports-odds-scraper-$(ENV) \
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
	aws logs tail /aws/lambda/sports-odds-scraper-$(ENV) --follow --region $(REGION)
