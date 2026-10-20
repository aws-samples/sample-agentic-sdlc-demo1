# Quickstart — Deploy from Zero to Running

This guide takes you from a fresh `git clone` to a fully deployed Agentic SDLC Transformation platform on AWS. Every command is included. No steps are assumed. Inspired by the AWS AI-DLC methodology.

## What You're Deploying

Agentic SDLC Transformation — a multi-persona collaboration platform with:
- A chat interface (React web app) hosted on CloudFront
- AI agents that guide a Business Analyst, a Solution Architect, and a Software Developer through requirements, user stories, design, and architecture — with explicit handoffs between roles — ending in a construction brief the developer takes into an AI-assisted IDE
- File storage in S3, authentication via Cognito (with persona role assigned at sign-up), AI models via Amazon Bedrock

Total deploy time: ~20 minutes for the first deploy.

---

## Step 1: Install Required Tools

You need five tools on your machine. Check each one and install any that are missing.

### Check what you have

```bash
node --version      # Need v18 or higher
npm --version       # Comes with Node.js
aws --version       # Need v2
docker --version    # Need Docker running
python3 --version   # Need 3.12 or higher
```

### Install missing tools

| Tool | macOS Install | Other Platforms |
|------|--------------|-----------------|
| Node.js 18+ | `brew install node` | [nodejs.org/en/download](https://nodejs.org/en/download) |
| AWS CLI v2 | `brew install awscli` | [AWS CLI install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| Docker | [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) | [docker.com/get-started](https://www.docker.com/get-started/) |
| Python 3.12+ | `brew install python@3.13` | [python.org/downloads](https://www.python.org/downloads/) |
| AWS CDK CLI | `npm install -g aws-cdk` | Same command on all platforms |

After installing Docker, make sure Docker Desktop is running (open the app). You can verify with:

```bash
docker info
```

If you see connection errors, Docker isn't running yet.

---

## Step 2: Set Up an AWS Account

If you don't have an AWS account:

1. Go to [aws.amazon.com](https://aws.amazon.com/) and click "Create an AWS Account"
2. Follow the sign-up process (requires a credit card)
3. Sign in to the [AWS Console](https://console.aws.amazon.com/)

Expected costs for light usage: $20–$100/month. See `docs/cost-estimation.md` for details.

---

## Step 3: Configure AWS Credentials

The AWS CLI needs credentials to deploy resources to your account.

### Option A: If you have IAM Identity Center (SSO)

```bash
aws configure sso
```

Follow the prompts. When done, verify:

```bash
aws sts get-caller-identity
```

You should see your account ID and user ARN.

### Option B: If you have an IAM access key

```bash
aws configure
```

Enter your Access Key ID, Secret Access Key, and default region (`us-west-2`).

Verify:

```bash
aws sts get-caller-identity
```

### What permissions do you need?

Your AWS user/role needs broad permissions. For a first deploy, the simplest option is `AdministratorAccess`. If that's not possible, you need permissions for: CloudFormation, S3, Lambda, IAM, Cognito, SSM, Secrets Manager, Bedrock (including AgentCore), CloudWatch, X-Ray, and ECR.

---

## Step 4: Bootstrap CDK

CDK needs a one-time setup in your AWS account to create a staging bucket for deployment assets. You only do this once per account/region.

First, get your AWS account ID:

```bash
aws sts get-caller-identity --query Account --output text
```

Then bootstrap (replace `123456789012` with your actual account ID):

```bash
npx cdk bootstrap aws://123456789012/us-west-2
```

You should see output ending with `Environment aws://123456789012/us-west-2 bootstrapped`.

---

## Step 5: Clone the Repository

```bash
git clone <repository-url>
cd agentcore-strands-deployment
```

Replace `<repository-url>` with the actual Git URL.

---

## Step 6: Review the Configuration (Optional)

The default configuration works out of the box for `us-west-2`. If you need to change the region or model, edit `cdk/config.ts`:

```bash
# Only if you need to change region or model — skip this if us-west-2 is fine
open cdk/config.ts
```

The key settings are near the top:

```typescript
awsRegion: "us-west-2",                              // Change if using a different region
runtime: {
  bedrockModelId: "us.anthropic.claude-sonnet-4-6",   // Change if using a different model
},
```

If you change the region, make sure you've bootstrapped CDK in that region (Step 4).

---

## Step 7: Install Dependencies

```bash
cd cdk
npm install
cd ..
```

This installs the CDK infrastructure dependencies. The frontend dependencies are installed automatically during deployment.

---

## Step 8: Deploy

This is the big one. It builds the frontend, builds a Docker container for the agent, and provisions all AWS resources.

```bash
bash scripts/deploy.sh
```

**What happens:**
1. Enables CloudWatch observability (Transaction Search)
2. Builds the React frontend (`code/ui-frontend/`)
3. Builds the agent Docker image (`code/agentcore-runtime/icode-agent/`)
4. Creates all AWS resources via CloudFormation
5. Uploads a default system prompt to S3

**This takes 15–20 minutes on the first run.** Subsequent deploys are faster (~5 minutes).

You'll see a lot of output. Look for `Deployment complete!` at the end.

### If something goes wrong

| Error | Fix |
|-------|-----|
| `This stack uses assets, so the toolkit stack must be deployed` | You skipped Step 5. Run `npx cdk bootstrap aws://ACCOUNT_ID/us-west-2` |
| `docker build failed` | Make sure Docker Desktop is running. Try `docker info` to check. |
| `BucketAlreadyExists` | S3 bucket names are globally unique. Edit `s3.bucketNamePrefix` in `cdk/config.ts` to something unique, then redeploy. |
| `is not authorized to perform` | Your AWS credentials don't have enough permissions. See Step 3. |
| `Resource type ... is not supported in this region` | Bedrock AgentCore isn't available in your region. Use `us-west-2`. |

---

## Step 9: Find Your App URL

After deployment succeeds, get the CloudFront URL where your app is hosted:

```bash
aws cloudformation describe-stacks \
  --stack-name AgentCoreStrandsStack \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
  --output text
```

This prints something like `d1234abcdef8.cloudfront.net`.

Open it in your browser:

```
https://d1234abcdef8.cloudfront.net
```

---

## Step 10: Create an Account and Sign In

1. On the landing page, click **Sign In**
2. Click **Sign Up** to create a new account
3. Enter a username, email, password, and select a role (any role works — pick "Software Developer" if unsure)
4. Click **Sign Up**
5. You'll be redirected to the sign-in page. Enter your email and password.
6. You're in. You should see the "My Projects" page.

Note: Email verification is skipped automatically (the stack includes an auto-confirm trigger for convenience).

---

## Step 11: Test It

### Via the Web UI

1. Click **Create Project**
2. Enter a name (e.g., "Todo App"), description ("A simple to-do list"), and type ("Development")
3. Click **Create Project**
4. Click on the project card
5. The chat interface opens and the AI agent greets you
6. Follow the prompts — the agent will guide you through requirements, design, and code generation

### Via the Command Line

You can also test the agent directly via WebSocket:

```bash
bash scripts/test-invoke.sh
```

Send a custom message:

```bash
bash scripts/test-invoke.sh --message "Create a folder called hello-world"
```

---

## Step 12: View All Deployed Resources

To see all the configuration values (endpoints, IDs, ARNs):

```bash
bash scripts/get-params.sh
```

This prints every SSM parameter the stack created.

---

## Tear Down (When You're Done)

To delete all AWS resources and stop incurring costs:

```bash
bash scripts/destroy.sh
```

If the S3 bucket has files in it and the destroy fails, empty it first:

```bash
bash scripts/destroy.sh --empty-bucket
```

---

## What's Next

| Want to... | Read... |
|-----------|---------|
| Understand the architecture | `docs/infrastructure-overview.md` |
| Learn how the AI agents work | `docs/ai-agent-architecture.md` |
| Add custom tools to the agent | `docs/guide-creating-new-tools.md` |
| Iterate locally without redeploying | `docs/fullstack-local-development.md` |
| Swap in your own agent | `docs/agent-replacement-guide.md` |
| Troubleshoot issues | `docs/troubleshooting.md` |
| Understand costs | `docs/cost-estimation.md` |

---

## Quick Reference

| Command | What It Does |
|---------|-------------|
| `bash scripts/deploy.sh` | Deploy everything to AWS |
| `bash scripts/deploy.sh dev` | Deploy to the dev environment |
| `bash scripts/destroy.sh` | Tear down all resources |
| `bash scripts/destroy.sh --empty-bucket` | Empty S3 bucket then tear down |
| `bash scripts/test-invoke.sh` | Test the agent via WebSocket |
| `bash scripts/get-params.sh` | Show all deployed resource values |
| `bash scripts/local-dev.sh` | Start the agent locally |
