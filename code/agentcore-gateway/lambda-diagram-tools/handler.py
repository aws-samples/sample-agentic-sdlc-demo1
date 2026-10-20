"""
Lambda handler for AWS Labs-style diagram generation
Provides specialized tools using the diagrams library

When folder_path is provided, diagrams are automatically stored to the
specified folder path in the artifacts bucket.
"""
import json
import os
import re
import boto3
from datetime import datetime, timezone
from typing import Dict, Any, Optional

s3_client = boto3.client('s3')
bedrock_client = boto3.client('bedrock-runtime')

BUCKET_NAME = os.environ.get('DIAGRAM_BUCKET')
MODEL_ID = os.environ.get('MODEL_ID', "us.anthropic.claude-3-5-sonnet-20241022-v2:0")


def success(text):
    """Return a success response in MCP content format."""
    return {"content": [{"type": "text", "text": text}]}


def error(text):
    """Return an error response in MCP content format."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def validate_folder_path(folder_path: Optional[str]) -> tuple[bool, str]:
    """
    Validate folder_path format.

    Args:
        folder_path: The S3 folder path to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not folder_path:
        return False, "folder_path is required"

    if not isinstance(folder_path, str):
        return False, "folder_path must be a string"

    folder_path = folder_path.strip()
    if not folder_path:
        return False, "folder_path cannot be empty"

    if len(folder_path) > 512:
        return False, "folder_path must be 512 characters or less"

    # Allow alphanumeric, hyphens, underscores, and forward slashes
    if not re.match(r'^[a-zA-Z0-9_/.-]+$', folder_path):
        return False, "folder_path must contain only alphanumeric characters, hyphens, underscores, dots, and forward slashes"

    return True, ""


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - routes to appropriate diagram generator

    When folder_path is provided, diagrams are automatically stored to
    the specified path in the artifacts bucket.
    """
    try:
        print(f"DEBUG - Event: {json.dumps(event, indent=2)}")

        # Validate folder_path
        folder_path = event.get('folder_path')
        is_valid, error_msg = validate_folder_path(folder_path)
        if not is_valid:
            return error(error_msg)

        # Determine diagram type from parameters
        return generate_aws_architecture(event)

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return error(str(e))

def generate_aws_architecture(event: Dict[str, Any]) -> Dict[str, Any]:
    """Generate AWS architecture diagram"""
    description = event.get('architecture_description', '')
    folder_path = event.get('folder_path')

    # Use Bedrock to generate diagram code
    diagram_code = generate_code_with_bedrock(
        description,
        diagram_type='AWS Architecture'
    )

    # Execute and upload
    s3_key = execute_and_upload(diagram_code, 'architecture', folder_path)

    return success(f'AWS architecture diagram generated and stored at {s3_key}')

def generate_code_with_bedrock(description: str, diagram_type: str) -> str:
    """Use Bedrock to generate Python diagrams code"""

    prompt = f"""Generate Python code using the 'diagrams' library to create a {diagram_type}.

CRITICAL REQUIREMENTS:
1. Use ONLY these EXACT import patterns (case-sensitive):
   - from diagrams import Diagram, Cluster, Edge
   - from diagrams.aws.compute import EC2, Lambda, ECS, EKS
   - from diagrams.aws.database import RDS, Dynamodb, ElastiCache, Aurora
   - from diagrams.aws.network import ELB, ALB, NLB, VPC, CloudFront, Route53
   - from diagrams.aws.storage import S3
   - from diagrams.aws.integration import SNS, SQS, Eventbridge
   - from diagrams.aws.security import IAM, Cognito, SecretsManager

2. Common AWS service class names (EXACT spelling):
   - RDS (not RDS Aurora)
   - Dynamodb (not DynamoDB - lowercase 'db'!)
   - ElastiCache (not Elasticache)
   - ELB, ALB, or NLB (not LoadBalancer)
   - SNS, SQS (not SimpleNotificationService)
   - Eventbridge (not EventBridge)

3. For flow/sequence diagrams without AWS services:
   - Use generic 'diagrams.generic' components like Custom()
   - Don't try to use AWS services for generic flowcharts

4. Code requirements:
   - Set show=False and filename="/tmp/diagram"
   - Use proper >> or << operators for connections
   - Keep it simple and clear

Description: {description}

Generate ONLY the Python code, no markdown, no explanations:"""

    try:
        response = bedrock_client.converse(
            modelId=MODEL_ID,
            messages=[{
                "role": "user",
                "content": [{"text": prompt}]
            }],
            inferenceConfig={
                "temperature": 0.3,
                "maxTokens": 2000
            }
        )

        code = response['output']['message']['content'][0]['text']

        # Extract code if wrapped in markdown
        if '```python' in code:
            code = code.split('```python')[1].split('```')[0].strip()
        elif '```' in code:
            code = code.split('```')[1].split('```')[0].strip()

        return code

    except Exception as e:
        print(f"Bedrock error: {e}")
        # Fallback to simple template
        return get_fallback_code(description, diagram_type)

def get_fallback_code(description: str, diagram_type: str) -> str:
    """Fallback diagram code if Bedrock fails"""
    return f'''
from diagrams import Diagram
from diagrams.aws.compute import Lambda, EC2
from diagrams.aws.database import RDS
from diagrams.aws.network import ELB

with Diagram("{diagram_type}", show=False, filename="/tmp/diagram"):
    lb = ELB("Load Balancer")
    app = EC2("Application")
    db = RDS("Database")

    lb >> app >> db
'''

def ensure_tmp_directory(code: str) -> str:
    """
    Ensure generated code changes to /tmp directory before creating diagrams.
    This is critical because Lambda's /var/task directory is read-only.
    """
    import re

    # Check if code already has os.chdir('/tmp')
    if 'os.chdir' in code:
        return code

    # Add os.chdir at the beginning, after imports
    lines = code.split('\n')
    import_section_end = 0

    # Find where imports end
    for i, line in enumerate(lines):
        if line.strip() and not line.strip().startswith(('import ', 'from ', '#')):
            import_section_end = i
            break

    # Insert os.chdir after imports
    if 'import os' not in code:
        lines.insert(import_section_end, 'import os')
        import_section_end += 1

    lines.insert(import_section_end, 'os.chdir("/tmp")  # Required for Lambda write access')

    fixed_code = '\n'.join(lines)

    # Also ensure all filename parameters use /tmp/ prefix
    # Fix patterns like filename="diagram_name" to filename="/tmp/diagram_name"
    fixed_code = re.sub(
        r'filename\s*=\s*["\'](?!/tmp/)([^"\']+)["\']',
        r'filename="/tmp/\1"',
        fixed_code
    )

    return fixed_code

def execute_and_upload(
    code: str,
    diagram_type: str,
    folder_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute diagram code and upload to S3.

    Args:
        code: Python code to generate the diagram
        diagram_type: Type of diagram (architecture, sequence, flow, class)
        folder_path: S3 folder path for storing the diagram

    Returns:
        Dict with diagram_url and s3_key
    """
    import subprocess

    # Fix common import errors before execution
    code = fix_common_import_errors(code)

    # CRITICAL FIX: Ensure all code changes to /tmp directory first
    # This is required because Lambda's /var/task is read-only
    code = ensure_tmp_directory(code)

    # Write the generated code to a fixed path in Lambda's writable /tmp.
    # Using a constant path (rather than a random tempfile name) keeps the
    # subprocess argv a fully static literal list below — no variable argument.
    code_file = "/tmp/_diagram_exec.py"  # nosec B108 — Lambda only has /tmp for writable storage
    with open(code_file, "w", encoding="utf-8") as f:
        f.write(code)
        f.flush()

    try:
        # Execute the generated diagram code in an isolated subprocess.
        # Static literal argv + shell=False => no command/argument injection;
        # only the file *contents* vary (model-generated diagram code), run in
        # the ephemeral Lambda sandbox (/tmp-only writes, 30s timeout, scoped
        # execution role). Non-production sample.
        result = subprocess.run(
            ["python", "/tmp/_diagram_exec.py"],
            check=True,
            timeout=30,
            capture_output=True,
            text=True,
            shell=False,
        )

        diagram_file = '/tmp/diagram.png'  # nosec B108 — Lambda only has /tmp for writable storage

        folder_path = folder_path.strip().rstrip('/')
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{diagram_type}_diagram_{timestamp}.png"
        s3_key = f"{folder_path}/{filename}"

        # Upload diagram to S3
        s3_client.upload_file(
            diagram_file,
            BUCKET_NAME,
            s3_key,
            ExtraArgs={'ContentType': 'image/png'}
        )

        return s3_key

    except subprocess.TimeoutExpired:
        raise Exception("Diagram generation timed out")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if isinstance(e.stderr, str) else e.stderr.decode()
        print(f"Diagram execution error:\n{error_msg}")
        raise Exception(f"Failed to generate diagram: {error_msg}")
    finally:
        # Cleanup
        if os.path.exists(code_file):
            os.remove(code_file)

def fix_common_import_errors(code: str) -> str:
    """Fix common import naming issues in generated code"""
    import re

    # Remove any invalid diagrams.generic imports
    lines = code.split('\n')
    filtered_lines = []
    for line in lines:
        # Skip lines with diagrams.generic imports (doesn't exist!)
        if 'from diagrams.generic' in line:
            print(f"Removing invalid import: {line}")
            continue
        filtered_lines.append(line)
    code = '\n'.join(filtered_lines)

    # Common mistakes and their fixes
    fixes = {
        # Database services
        r'from diagrams\.aws\.database import DynamoDB': 'from diagrams.aws.database import Dynamodb',
        r'DynamoDB\(': 'Dynamodb(',

        # Integration services
        r'from diagrams\.aws\.integration import SimpleEmailServiceSes': 'from diagrams.aws.integration import SES',
        r'SimpleEmailServiceSes\(': 'SES(',
        r'from diagrams\.aws\.integration import SimpleNotificationService': 'from diagrams.aws.integration import SNS',
        r'SimpleNotificationService\(': 'SNS(',
        r'from diagrams\.aws\.integration import SimpleQueueService': 'from diagrams.aws.integration import SQS',
        r'SimpleQueueService\(': 'SQS(',
        r'from diagrams\.aws\.integration import EventBridge': 'from diagrams.aws.integration import Eventbridge',
        r'EventBridge\(': 'Eventbridge(',

        # Network services
        r'from diagrams\.aws\.network import LoadBalancer': 'from diagrams.aws.network import ELB',
        r'LoadBalancer\(': 'ELB(',
        r'from diagrams\.aws\.network import ApplicationLoadBalancer': 'from diagrams.aws.network import ALB',
        r'ApplicationLoadBalancer\(': 'ALB(',

        # Compute services
        r'from diagrams\.aws\.compute import Fargate': 'from diagrams.aws.compute import ECS',
        r'Fargate\(': 'ECS(',

        # Security services
        r'from diagrams\.aws\.security import CognitoUserPool': 'from diagrams.aws.security import Cognito',
        r'CognitoUserPool\(': 'Cognito(',
    }

    fixed_code = code
    for pattern, replacement in fixes.items():
        fixed_code = re.sub(pattern, replacement, fixed_code)

    return fixed_code
