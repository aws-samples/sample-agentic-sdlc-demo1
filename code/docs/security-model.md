# Security Model and Access Control

This document describes the authentication flows, authorization model, IAM roles, secrets management, network security, and known security considerations for Agentic SDLC Transformation.

## Authentication Flows

### 1. Machine-to-Machine: Runtime → Gateway (Client Credentials)

The Runtime container authenticates with the Gateway using OAuth 2.0 client credentials flow.

```
Runtime Container
  → Reads client secret from Secrets Manager (via GATEWAY_CLIENT_SECRET_ARN)
  → POST to Cognito token endpoint with client_id + client_secret + scope=agentcore/gateway
  → Receives access token (JWT, 1-hour expiry)
  → Passes token as Bearer header to Gateway MCP endpoint
```

Implementation: `code/agentcore-runtime/icode-agent/tools/oauth_provider.py`

The `OAuthProvider` class retrieves the secret once, then requests tokens with retry (3 attempts, exponential backoff). Tokens are not cached or refreshed — a new token is obtained per WebSocket session.

**Known limitation:** Tokens expire after 1 hour. Long-running sessions may fail with auth errors if a single token is used beyond its lifetime. The current architecture obtains one token per WebSocket connection.

### 2. Browser → Runtime: PKCE Authorization Code Flow

The React SPA authenticates users via Cognito using the PKCE (Proof Key for Code Exchange) flow through AWS Amplify.

```
Browser
  → User enters email + password on SignInPage
  → Amplify calls Cognito SRP auth (no client secret needed)
  → Receives ID token + access token
  → Access token passed to WebSocket via Sec-WebSocket-Protocol header (base64url encoded)
  → Runtime validates token against Cognito User Pool
```

Implementation: `code/ui-frontend/app/context/AuthContext.tsx`, `code/ui-frontend/app/lib/amplify.ts`

The PKCE client (`agentcore-pkce-client`) has no secret and uses SRP auth flow. OAuth callback URLs are dynamically set to the CloudFront domain.

### 3. Browser → WebSocket: Bearer Token via Subprotocol

Browsers cannot set custom headers on WebSocket connections. The access token is passed via the `Sec-WebSocket-Protocol` header:

```
Sec-WebSocket-Protocol: base64UrlBearerAuthorization.{base64url(accessToken)}, base64UrlBearerAuthorization
```

Implementation: `code/ui-frontend/app/context/WebSocketContext.tsx`, line 175

## Authorization Model

### Cognito User Groups (RBAC)

Six Cognito groups are created by the CDK stack (`cdk/stack.ts`, lines 549–561):

| Group Name | Display Name |
|-----------|-------------|
| `requirements-analyst` | Requirements Analyst |
| `system-architect` | System Architect |
| `software-developer` | Software Developer |
| `qa-engineer` | QA Engineer |
| `devops-engineer` | DevOps Engineer |
| `maintenance-specialist` | Maintenance Specialist |

Users select a role at sign-up. The group name is included in the Cognito ID token under `cognito:groups`. The frontend uses it for presentational gating — most notably the **operations dashboard** (`/dashboard`), which is shown and rendered only for the `maintenance-specialist` group; other personas are redirected/blocked. Workflow *actions* (handoff transitions, uploads) are owner-gated server-side by the Project API regardless of what the UI shows.

**Group assignment is automated.** A Cognito **post-confirmation Lambda trigger** (`cdk/custom-resources/post_confirmation_role_assign.py`, wired in `aidlc-platform-auth.ts`) reads the user's `custom:role` attribute and adds them to the matching group via `AdminAddUserToGroup`. Pre-provisioned test users are placed in their groups by the consolidated test-user provisioner. Manual group assignment is not required.

### OAuth Scopes

| Scope | Purpose | Used By |
|-------|---------|---------|
| `agentcore/gateway` | Access to Gateway MCP tools | Runtime → Gateway (client credentials) |
| `openid` | Standard OIDC | Browser → Runtime (PKCE) |
| `email` | Email claim in ID token | Browser → Runtime (PKCE) |
| `profile` | Profile claims in ID token | Browser → Runtime (PKCE) |

The `agentcore/gateway` scope is defined on a Cognito resource server (`cdk/stack.ts`, line 71) and required for all Gateway tool invocations.

## IAM Roles and Permissions

### Runtime Role

The AgentCore Runtime execution role has these permissions (granted in `cdk/stack.ts`, lines 450–480):

| Permission | Resource | Purpose |
|-----------|----------|---------|
| `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | Configured model ARN (+ underlying foundation model for inference profiles) | LLM inference |
| `secretsmanager:GetSecretValue` | Cognito client secret ARN | OAuth token retrieval |
| `s3:GetObject` | Files bucket, system prompt prefix | Read system prompts |
| `ssm:GetParameter`, `ssm:GetParameters`, `ssm:GetParametersByPath` | `/agentcore/*` parameters | Configuration reading |
| AgentCore Memory full access | Memory resource | STM/LTM operations |

### Lambda Execution Role

The file operations Lambda has these permissions:

| Permission | Resource | Purpose |
|-----------|----------|---------|
| `s3:GetObject`, `s3:PutObject` | Files bucket (`*`) | Read/write files |
| `s3:DeleteObject` | Files bucket (`*`) | Delete files/folders |

### Observability Roles

| Role | Permissions | Purpose |
|------|-----------|---------|
| Transaction Search Lambda | `xray:UpdateTraceSegmentDestination`, `xray:GetIndexingRules`, `xray:UpdateIndexingRule`, `logs:PutResourcePolicy` | X-Ray + CloudWatch setup |
| Log Delivery Lambdas | `logs:PutDeliverySource`, `logs:CreateDelivery`, etc. | Vended log delivery |
| Bedrock Logging Role | `logs:CreateLogStream`, `logs:PutLogEvents` | Bedrock → CloudWatch |
| Bedrock Logging Lambda | `bedrock:PutModelInvocationLoggingConfiguration`, `logs:CreateLogGroup`, `iam:PassRole` | Logging setup |

## Secrets Management

### Stored Secrets

| Secret | Location | Rotation | Accessed By |
|--------|----------|----------|-------------|
| Cognito app client secret | Secrets Manager: `/agentcore/secrets/cognito-client-secret` | None (no rotation configured) | Runtime container |

### Secret Retrieval

The Runtime reads the client secret from Secrets Manager at the start of each WebSocket session via `OAuthProvider._retrieve_client_secret()`. The secret ARN is passed as the `GATEWAY_CLIENT_SECRET_ARN` environment variable.

**Recommendation:** Consider enabling automatic rotation for the Cognito client secret. Currently, if the secret is compromised, manual rotation and redeployment are required.

## Network Security

### Public Network Mode (Default)

The Runtime runs on the public internet with Cognito-based authentication. All traffic is encrypted via TLS (WebSocket over WSS, HTTPS for API calls).

### VPC Mode (Optional)

When `vpc.enabled: true` in `cdk/config.ts`, the Runtime runs inside a VPC with specified subnets and security groups. This requires:
- NAT gateways or VPC endpoints for: Bedrock, S3, Secrets Manager, SSM, CloudWatch
- Security group rules allowing outbound HTTPS (443) to AWS service endpoints

### S3 Security

- Block all public access enabled on the files bucket (`cdk/stack.ts`, line 127)
- CloudFront accesses S3 via Origin Access Control (OAC), not public bucket policy
- CORS configured with `allowedOrigins: ["*"]` for presigned URL access from browsers (`cdk/stack.ts`, line 565)

### CloudFront

- HTTPS redirect enforced (`ViewerProtocolPolicy.REDIRECT_TO_HTTPS`)
- SPA error responses (403, 404) return `index.html` with 200 status for client-side routing

## Data Protection

### Data at Rest

| Data | Storage | Encryption |
|------|---------|------------|
| SDLC artifacts (markdown, code) | S3 files bucket | S3 default encryption (SSE-S3). No explicit KMS key configured. |
| Cognito user data | Cognito User Pool | AWS-managed encryption |
| Client secret | Secrets Manager | AWS-managed encryption |
| SSM parameters | SSM Parameter Store | Standard parameters (not SecureString) |
| CloudWatch logs | CloudWatch Logs | AWS-managed encryption |
| Bedrock log overflow | S3 bedrock log bucket | S3 default encryption |

**Note:** SSM parameters store non-sensitive configuration (endpoint URLs, resource IDs). The Cognito client secret is stored in Secrets Manager, not SSM.

### Data in Transit

All service-to-service communication uses TLS:
- WebSocket: WSS (TLS)
- Gateway MCP: HTTPS
- S3, Secrets Manager, SSM, Bedrock: HTTPS via AWS SDK
- CloudFront → S3: HTTPS via OAC

## Known Security Considerations

### Auto-Confirm Lambda

The Cognito pre-signup trigger (`cdk/stack.ts`, line 48) auto-confirms all users and auto-verifies email addresses. This means:
- Any email address can be used to create an account without verification
- There is no protection against fake or disposable email addresses
- Suitable for development/demo environments only

**Recommendation for production:** Remove the auto-confirm trigger and enable standard Cognito email verification.

### CORS Wildcard

The S3 files bucket has CORS configured with `allowedOrigins: ["*"]` (`cdk/stack.ts`, line 565). This allows any origin to access presigned URLs.

**Recommendation for production:** Restrict `allowedOrigins` to the CloudFront domain.

### No Secret Rotation

The Cognito client secret in Secrets Manager has no rotation policy configured.

**Recommendation:** Implement automatic rotation using a Lambda rotation function.

### No WAF

CloudFront has no AWS WAF (Web Application Firewall) configured. The distribution is open to all traffic.

**Recommendation for production:** Attach a WAF WebACL with rate limiting and common attack protection rules.

### No S3 Versioning

The files bucket does not have versioning enabled. Accidental overwrites or deletions are permanent.

**Recommendation:** Enable S3 versioning, especially for production environments.

### Client-Side Project Storage

Project metadata (name, description, type) is stored in `localStorage` in the browser. This data is not backed up, not synced across devices, and lost if the user clears browser data.

### No Rate Limiting on Agent Invocations

There is no application-level rate limiting on WebSocket connections or agent invocations. Bedrock API throttling provides some protection, but a malicious user could exhaust model quotas.

### Audit Trail in SharedState

The `audit_buffer` in SharedState is an in-memory list that is serialized to the state markdown file. It is not written to a tamper-proof log store. For compliance-sensitive deployments, consider writing audit entries to CloudWatch Logs or a dedicated DynamoDB table.
