terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial backend config — supply the rest via -backend-config=backend.hcl (local)
  # or -backend-config flags in CI. See infrastructure/backend.hcl.example.
  backend "s3" {}
}

provider "aws" {
  region = "ap-southeast-2"
}
