#!/usr/bin/env bash
set -euo pipefail

# Start the AgentCore local development server using `agentcore dev`.
# Usage: ./local-dev.sh [environment]
# Example: ./local-dev.sh dev

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_DIR="$PROJECT_ROOT/agentcore-runtime/icode-agent"

ENV="${1:-}"

echo "==> AgentCore Local Development"
if [ -n "$ENV" ]; then
  echo "    Environment: $ENV"
else
  echo "    Environment: default"
fi

echo "    Agent dir: $AGENT_DIR"
echo ""

# ------------------------------------------------------------------
# Start agentcore dev from the agent source directory
# ------------------------------------------------------------------
echo "==> Starting agentcore dev..."
(cd "$AGENT_DIR" && agentcore dev)
