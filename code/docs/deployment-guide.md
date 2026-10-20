# Deployment Guide — How to Deploy from Scratch

This guide walks through deploying Agentic SDLC Transformation from a clean environment. The platform consists of AWS CDK infrastructure, a containerized Python agent runtime, a Lambda-backed tool gateway, and a React SPA frontend. Inspired by the AWS AI-DLC methodology.

## 1. Prerequisites

### Accounts and Access

- An AWS account with billing enabled
- AWS CLI v2 configured with credentials (`aws configure`)
- Bedrock model access enabled for the target model (default: `us.anthropic.claude-sonnet-4-6`) — see Step 1.5 below

### CLI Tools

| Tool | Minimum Version | Install |
|------|----------------|---------|
| AWS CLI | v2 | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| Node.js | v18+ | [nodejs.org](https://nodejs.org/) |
| AWS CDK CLI | v2 | `npm install -g aws-cdk` |
| Docker | Latest | [docker.com](https://www.docker.com/get-started/) |
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |

### AWS Permissions

The deploying IAM principal needs permissions for: CloudFormation, Cognito, S3, Lambda, IAM, SSM Parameter Store, Secrets Manager, Bedrock (including AgentCore), CloudWatch/X-Ray, and ECR. For development, `AdministratorAccess` works. For production, scope permissions to the services listed in `README.md` under "AWS Permissions".

## 2. Clone the Repository

```bash
git clone <repository-url>
cd agentcore-strands-deployment
```

## 3. Bootstrap CDK (One-Time per Account/Region)

```bash
cdk bootstrap aws://ACCOUNT_ID/REGION
```

Replace `ACCOUNT_ID` and `REGION` with your values (e.g., `cdk bootstrap aws://123456789012/us-west-2`).

## 4. Enable Bedrock Model Access

1. Open the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/)
2. Navigate to **Model access** in the left sidebar
3. Click **Manage model access**
4. Enable the model specified in `cdk/config.ts` (default: `us.anthropic.claude-sonnet-4-6`)
5. Click **Save changes** and wait for "Access granted"

## 5. Configure the Deployment

Edit `cdk/config.ts` to customize your deployment. The defaults work out of the box. At minimum, verify:

```typescript
// cdk/config.ts — key settings to review
awsRegion: "us-west-2",
runtime: {
  bedrockModelId: "us.anthropic.claude-sonnet-4-6",
},
```

See the `DeploymentConfig` interface in `cdk/config.ts` (line 1–80) for all configurable options.

## 6. Install CDK Dependencies

```bash
cd cdk
npm install
cd ..
```

## 7. Install Frontend Dependencies

```bash
cd code/ui-frontend
npm install
cd ../..
```

## 8. Build the Frontend

The CDK stack deploys the pre-built SPA from `code/ui-frontend/dist/`. The `deploy.sh` script handles this automatically, but if deploying manually:

```bash
cd code/ui-frontend
npm run build
cd ../..
```

## 9. Deploy the Full Stack

```bash
bash scripts/deploy.sh
```

This script performs three steps:
1. Enables CloudWatch Transaction Search (X-Ray trace destination + resource policy)
2. Runs `cdk deploy --require-approval never` (builds the Docker image, provisions all infrastructure, deploys the SPA to S3/CloudFront)
3. Uploads a default system prompt to S3 if none exists

The first deployment takes 10–20 minutes due to Docker image building and AgentCore resource provisioning.

### Deploy to a Specific Environment

```bash
bash scripts/deploy.sh dev      # dev environment
bash scripts/deploy.sh staging  # staging environment
bash scripts/deploy.sh prod     # production environment
```

## 10. Post-Deployment Verification

### Retrieve Stack Outputs

```bash
bash scripts/get-params.sh
```

This prints all SSM Parameter Store values including the Runtime ARN, Gateway endpoint, Cognito IDs, and CloudFront domain.

### Smoke Test the Agent via WebSocket

```bash
bash scripts/test-invoke.sh
```

Send a custom message:

```bash
bash scripts/test-invoke.sh --message "Create a folder called test-project"
```

### Access the Frontend

Get the CloudFront domain from the stack outputs:

```bash
aws cloudformation describe-stacks --stack-name AgentCoreStrandsStack \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" --output text
```

Open `https://<cloudfront-domain>` in a browser. Sign up for an account, create a project, and start the SDLC workflow.

## 11. Environment Configuration

### Environment Variables (Runtime Container)

These are set automatically by CDK in the Runtime construct (`cdk/stack.ts`, lines 430–445):

| Variable | Source | Purpose |
|----------|--------|---------|
| `GATEWAY_MCP_URL` | CDK output | Gateway MCP endpoint URL |
| `GATEWAY_CLIENT_ID` | CDK output | Cognito app client ID |
| `GATEWAY_CLIENT_SECRET_ARN` | CDK output | Secrets Manager ARN for client secret |
| `GATEWAY_TOKEN_ENDPOINT` | CDK output | Cognito OAuth2 token endpoint |
| `GATEWAY_SCOPE` | Hardcoded | `agentcore/gateway` |
| `AGENTCORE_MEMORY_ID` | CDK output | AgentCore Memory resource ID |
| `BEDROCK_MODEL_ID` | Config | Bedrock model identifier |
| `SYSTEM_PROMPT_BUCKET` | CDK output | S3 bucket for system prompts |
| `SYSTEM_PROMPT_PREFIX` | Config | S3 prefix for system prompt files |
| `AWS_REGION` | Config | AWS region |

### Frontend Environment Variables

For local frontend development, copy `.env.example` and fill in values from `get-params.sh`:

```bash
cd code/ui-frontend
cp .env.example .env
# Edit .env with values from: bash scripts/get-params.sh
```

In production, the CDK stack generates a `config.json` file deployed alongside the SPA to S3/CloudFront (`cdk/stack.ts`, lines 590–600).

## 12. Multi-Environment Deployment

Three environments are pre-configured in `cdk/config.ts`:

| Environment | Stack Name | Key Differences |
|-------------|-----------|-----------------|
| `dev` | `AgentCoreStrandsDev` | S3 removal policy = `DESTROY` (auto-delete on stack teardown) |
| `staging` | `AgentCoreStrandsStaging` | Default settings with staging tag |
| `prod` | `AgentCoreStrandsProd` | 100% observability sampling |

Add custom environments by adding entries to the `ENV_OVERRIDES` object in `cdk/config.ts` (line 155).

## 13. Common Pitfalls

1. **Docker not running**: CDK builds a Docker image for the agent container. Ensure Docker Desktop is running before deploying.
2. **S3 bucket name collision**: S3 bucket names are globally unique. If deployment fails with `BucketAlreadyExists`, change `s3.bucketNamePrefix` in `cdk/config.ts`.
3. **Bedrock model not enabled**: The deploy will succeed but the agent will fail at runtime with `AccessDeniedException`. Always enable model access first (Step 4).
4. **CDK version mismatch**: All CDK packages must be the same version. The project pins versions in `cdk/package.json` — run `npm install` (not `npm update`) to respect pins.
5. **AgentCore region availability**: Bedrock AgentCore is not available in all regions. Verify your target region supports it.
6. **First deploy is slow**: The initial Docker build and AgentCore resource creation takes 10–20 minutes. Subsequent deploys are faster.
7. **Frontend must be built before CDK deploy**: The CDK stack deploys from `code/ui-frontend/dist/`. The `deploy.sh` script handles this, but manual `cdk deploy` requires a prior `npm run build` in the frontend directory.

## 14. Teardown

```bash
bash scripts/destroy.sh
```

To also empty the S3 bucket (required if removal policy is `RETAIN`):

```bash
bash scripts/destroy.sh --empty-bucket
```
