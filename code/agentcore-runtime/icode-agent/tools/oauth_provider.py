"""OAuth 2.0 provider for Gateway MCP communication.

Retrieves Cognito client credentials from Secrets Manager and obtains
OAuth 2.0 access tokens using the client_credentials grant flow.
"""

import os
import time

import boto3
import requests
from botocore.exceptions import ClientError


class OAuthProvider:
    """Manages OAuth 2.0 tokens for Gateway MCP communication."""

    MAX_RETRIES = 3
    BACKOFF_BASE = 1  # seconds

    def __init__(
        self,
        client_id: str,
        client_secret_arn: str,
        token_endpoint: str,
        scope: str,
        region: str,
    ):
        """Initialize with Cognito configuration.

        Args:
            client_id: Cognito app client ID.
            client_secret_arn: Secrets Manager ARN for the client secret.
            token_endpoint: Cognito OAuth token endpoint URL.
            scope: OAuth scope for the Gateway.
            region: AWS region for Secrets Manager calls.
        """
        self.client_id = client_id
        self.client_secret_arn = client_secret_arn
        self.token_endpoint = token_endpoint
        self.scope = scope
        self.region = region
        self._client_secret: str | None = None

    def _retrieve_client_secret(self) -> str:
        """Retrieve the client secret from Secrets Manager.

        Returns:
            The client secret string.

        Raises:
            RuntimeError: If Secrets Manager retrieval fails.
        """
        try:
            client = boto3.client("secretsmanager", region_name=self.region)
            response = client.get_secret_value(SecretId=self.client_secret_arn)
            return response["SecretString"]
        except ClientError as exc:
            raise RuntimeError(
                f"Failed to retrieve client secret from Secrets Manager: {exc}"
            ) from exc

    def get_access_token(self) -> str:
        """Obtain an OAuth 2.0 access token using client credentials grant.

        Retrieves the client secret from Secrets Manager on first call,
        then requests an access token from the Cognito token endpoint.
        Retries up to 3 times with exponential backoff (1s, 2s, 4s)
        on token request failure.

        Returns:
            Bearer access token string.

        Raises:
            RuntimeError: If Secrets Manager retrieval fails.
            RuntimeError: If token request fails after 3 retries.
        """
        if self._client_secret is None:
            self._client_secret = self._retrieve_client_secret()

        last_exception: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.post(
                    self.token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "scope": self.scope,
                    },
                    auth=(self.client_id, self._client_secret),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                response.raise_for_status()
                token_data = response.json()
                return token_data["access_token"]
            except (requests.RequestException, KeyError) as exc:
                last_exception = exc
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.BACKOFF_BASE * (2 ** attempt)
                    time.sleep(delay)

        raise RuntimeError(
            f"Failed to obtain OAuth token after {self.MAX_RETRIES} attempts: "
            f"{last_exception}"
        )

    @classmethod
    def from_env(cls) -> "OAuthProvider":
        """Create an OAuthProvider from environment variables.

        Reads:
            GATEWAY_CLIENT_ID: Cognito app client ID.
            GATEWAY_CLIENT_SECRET_ARN: Secrets Manager ARN for client secret.
            GATEWAY_TOKEN_ENDPOINT: Cognito OAuth token endpoint URL.
            GATEWAY_SCOPE: OAuth scope for the Gateway.
            AWS_REGION: AWS region for Secrets Manager calls.

        Returns:
            A configured OAuthProvider instance.
        """
        return cls(
            client_id=os.environ["GATEWAY_CLIENT_ID"],
            client_secret_arn=os.environ["GATEWAY_CLIENT_SECRET_ARN"],
            token_endpoint=os.environ["GATEWAY_TOKEN_ENDPOINT"],
            scope=os.environ["GATEWAY_SCOPE"],
            region=os.environ["AWS_REGION"],
        )
