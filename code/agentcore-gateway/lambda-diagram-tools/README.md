# AWS Labs-Style Diagram Generator (Docker Lambda)

This Lambda function provides 4 specialized diagram generation tools using the Python `diagrams` library, inspired by the [AWS Labs MCP Diagram Server](https://awslabs.github.io/mcp/servers/aws-diagram-mcp-server).

## 🎯 Overview

**One Docker Lambda → Four MCP Tools:**

1. **generate_aws_architecture_diagram_official** - AWS architecture diagrams
2. **generate_sequence_diagram** - Process sequence diagrams
3. **generate_flow_diagram** - Flow charts and decision trees
4. **generate_class_diagram** - Component/class diagrams

## 🏗️ Architecture

```
AgentCore Gateway
    ↓
Lambda (Docker Container)
    ↓ (routes based on input parameters)
├─ generate_aws_architecture() → AWS diagrams
├─ generate_sequence_diagram() → Sequence diagrams
├─ generate_flow_diagram() → Flow diagrams
└─ generate_class_diagram() → Class diagrams
    ↓
Bedrock Claude (generates Python code)
    ↓
Execute diagrams library
    ↓
Upload to S3
```

## 📦 Components

### Dockerfile

- Base: `public.ecr.aws/lambda/python:3.10`
- Dependencies: graphviz, diagrams library
- Size: ~500MB (includes graphviz system libraries)

### handler.py

- Routes to 4 diagram generators
- Uses Bedrock to generate Python code
- Executes diagrams library
- Uploads results to S3

## 🚀 Deployment

### 1. Build Docker Image

```bash
cd gateway/notebook/Lambda/icode_diagram_awslabs

# Build for Lambda (linux/amd64)
docker build --platform linux/amd64 -t icode-diagram-awslabs .
```

### 2. Push to ECR

```bash
# Login to ECR
aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag icode-diagram-awslabs:latest ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/icode-diagram-awslabs:latest
docker push ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/icode-diagram-awslabs:latest
```

### 3. Create Lambda Function

Either via notebook or AWS CLI:

```bash
aws lambda create-function \
    --function-name icode_diagram_awslabs \
    --package-type Image \
    --code ImageUri=ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/icode-diagram-awslabs:latest \
    --role arn:aws:iam::ACCOUNT:role/icodeLambdaRole \
    --timeout 300 \
    --memory-size 3008 \
    --environment Variables={DIAGRAM_BUCKET=your-bucket-name}
```

## 🔧 Environment Variables

| Variable         | Description                   | Required |
| ---------------- | ----------------------------- | -------- |
| `DIAGRAM_BUCKET` | S3 bucket for diagram storage | Yes      |

## 📊 Tool Specifications

### Tool 1: generate_aws_architecture_diagram_official

```json
{
  "name": "generate_aws_architecture_diagram_official",
  "description": "Generate AWS architecture diagrams using official patterns",
  "inputSchema": {
    "type": "object",
    "properties": {
      "architecture_description": {
        "type": "string",
        "description": "Natural language description of AWS architecture"
      }
    }
  }
}
```

**Example:**

```json
{
  "architecture_description": "A three-tier web application with Application Load Balancer, EC2 Auto Scaling group, and RDS database in multiple availability zones"
}
```

### Tool 2: generate_sequence_diagram

```json
{
  "name": "generate_sequence_diagram",
  "description": "Generate sequence diagrams for process flows",
  "inputSchema": {
    "type": "object",
    "properties": {
      "process_description": {
        "type": "string",
        "description": "Description of the process flow"
      }
    }
  }
}
```

### Tool 3: generate_flow_diagram

```json
{
  "name": "generate_flow_diagram",
  "description": "Generate flow charts and decision trees",
  "inputSchema": {
    "type": "object",
    "properties": {
      "flow_description": {
        "type": "string",
        "description": "Description of the flow or decision process"
      }
    }
  }
}
```

### Tool 4: generate_class_diagram

```json
{
  "name": "generate_class_diagram",
  "description": "Generate class and component diagrams",
  "inputSchema": {
    "type": "object",
    "properties": {
      "component_description": {
        "type": "string",
        "description": "Description of components and their relationships"
      }
    }
  }
}
```

## 💰 Cost Estimates

### Per Invocation

- Compute: ~$0.002 (30s @ 3GB)
- Bedrock: ~$0.001 (code generation)
- **Total: ~$0.003 per diagram**

### Monthly (1000 diagrams)

- **~$3/month**

## 🔍 Comparison with Custom Lambda

| Aspect      | Custom (ZIP) | AWS Labs (Docker) |
| ----------- | ------------ | ----------------- |
| Deployment  | Simpler      | More complex      |
| Cold start  | Faster (~2s) | Slower (~5s)      |
| Features    | Your control | Pattern-based     |
| Maintenance | You maintain | AWS-inspired      |
| Image size  | 50MB         | 500MB             |

## 🐛 Troubleshooting

### Issue: Lambda timeout

**Solution:** Increase timeout to 300s and memory to 3008MB

### Issue: "graphviz not found"

**Solution:** Rebuild Docker image, ensure graphviz is installed

### Issue: S3 upload fails

**Solution:** Check DIAGRAM_BUCKET env var and IAM permissions

## 📚 References

- [AWS Labs MCP Diagram Server](https://awslabs.github.io/mcp/servers/aws-diagram-mcp-server)
- [Python Diagrams Library](https://diagrams.mingrammer.com/)
- [Lambda Container Images](https://docs.aws.amazon.com/lambda/latest/dg/images-create.html)
