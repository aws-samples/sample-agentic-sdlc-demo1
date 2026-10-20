# Architecture Overview

## High-Level Architecture

```mermaid
graph TB
    User[User Browser] --> CF[CloudFront CDN]
    CF --> S3[S3 Static Hosting]
    CF --> APIGW[API Gateway]
    APIGW --> Lambda[Lambda Functions]
    Lambda --> DDB[DynamoDB]
    Lambda --> SES[SES Email]
    Lambda --> Cognito[Cognito Auth]
```

## Components

### Frontend
- React 18 SPA hosted on S3
- CloudFront CDN for global delivery
- Responsive design with Tailwind CSS

### Backend
- API Gateway (REST)
- Lambda functions (Node.js 20)
- DynamoDB tables for books, orders, users

### Authentication
- Amazon Cognito User Pool
- JWT token-based auth
- Social sign-in support (Google, Apple)

### Infrastructure
- AWS CDK for IaC
- CloudWatch for monitoring
- X-Ray for tracing
