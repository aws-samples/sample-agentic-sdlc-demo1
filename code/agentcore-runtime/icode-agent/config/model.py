"""Model configuration for the AI-SDLC Multi-Agent System."""

import os

from botocore.config import Config as BotocoreConfig
from strands.models import BedrockModel

# Default model and region — overridden by env vars set in CDK stack
# Defaults — overridden by BEDROCK_MODEL_ID and AWS_REGION env vars set in CDK.
# Using global inference profile so the agent works in any AWS region.
_DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"
_DEFAULT_REGION = "us-east-1"


def create_model(
    model_id: str | None = None,
    region: str | None = None,
    streaming: bool = True,
) -> BedrockModel:
    """Create a BedrockModel instance for LLM inference.

    Reads BEDROCK_MODEL_ID and AWS_REGION from environment if not
    explicitly provided, falling back to sensible defaults.

    Args:
        model_id: The Bedrock model identifier.
        region: AWS region for the Bedrock endpoint.
        streaming: Whether to enable streaming inference.

    Returns:
        A configured BedrockModel instance.
    """
    if model_id is None:
        model_id = os.environ.get("BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID)
    if region is None:
        region = os.environ.get("AWS_REGION", _DEFAULT_REGION)

    return BedrockModel(
        model_id=model_id,
        region_name=region,
        streaming=streaming,
        boto_client_config=BotocoreConfig(
            read_timeout=600,  # 10 min — large artifact generation can be slow
            retries={"max_attempts": 3, "mode": "adaptive"},
        ),
    )
