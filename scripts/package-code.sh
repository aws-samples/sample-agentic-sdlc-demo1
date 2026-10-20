#!/bin/bash

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

set -e

cd "$SCRIPT_DIR"/../code

assets_dir="$SCRIPT_DIR"/../assets

# Make sure the assets directory exists
mkdir -p "$assets_dir"

# ── UI Frontend ──────────────────────────────────────────────
echo "Building UI Frontend..."
(cd ui-frontend && npm install && npm run build)

rm -f "$assets_dir"/ui-frontend.zip
(cd ui-frontend/dist && zip -qr "$assets_dir"/ui-frontend.zip .)

echo "UI Frontend packaged successfully"

# ── Workflow Registry (shared source of truth) ───────────────
# `workflow_registry.json` is the single canonical Persona/Workflow Registry
# (design Section 3a) and `workflow_registry.py` is its shared loader/validator
# module (design Section 3a-i). The canonical copies live in the agent runtime
# image (agentcore-runtime/icode-agent/) and are therefore bundled into
# icode-agent.zip automatically. Copy both identical files into the Project API
# Lambda asset (cdk/custom-resources/) so both runtimes load a byte-identical
# registry and share the same helper code — do not maintain a forked copy.
registry_src="agentcore-runtime/icode-agent/workflow_registry.json"
registry_dst="$SCRIPT_DIR/../cdk/custom-resources/workflow_registry.json"
cp "$registry_src" "$registry_dst"

registry_module_src="agentcore-runtime/icode-agent/workflow_registry.py"
registry_module_dst="$SCRIPT_DIR/../cdk/custom-resources/workflow_registry.py"
cp "$registry_module_src" "$registry_module_dst"

echo "workflow_registry.json + workflow_registry.py synced to cdk/custom-resources"

# ── icode-agent ──────────────────────────────────────────────
rm -f "$assets_dir"/icode-agent.zip
(cd agentcore-runtime/icode-agent && zip -qr "$assets_dir"/icode-agent.zip . \
  -x "*/node_modules/*" -x "*/__pycache__/*" -x "*/.venv/*")

echo "icode-agent packaged successfully"

# ── Lambda Tools ─────────────────────────────────────────────
rm -f "$assets_dir"/lambda-tools.zip

# extract_document_text (design Section 7b, requirements 5.2-5.3) brings in
# pypdf + python-docx, neither of which is provided by the Lambda Python
# runtime. Stage the source + vendored dependencies in a build directory so the
# real source tree stays untouched, then zip the staged tree into a single
# self-contained deployment artifact.
lambda_tools_src="agentcore-gateway/lambda-tools"
lambda_tools_build="$assets_dir/.lambda-tools-build"

rm -rf "$lambda_tools_build"
mkdir -p "$lambda_tools_build"

# Copy source (handler, shared, tools/) — exclude tests, build artifacts, and
# the requirements file (only used by this script).
rsync -a \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'tests' \
  --exclude '.venv*' \
  --exclude '.pytest_cache' \
  --exclude 'requirements.txt' \
  "$lambda_tools_src/" "$lambda_tools_build/"

# Vendor third-party dependencies into the build dir.
if [[ -f "$lambda_tools_src/requirements.txt" ]]; then
  echo "Installing lambda-tools Python dependencies..."
  python3 -m pip install \
    --quiet \
    --no-compile \
    --target "$lambda_tools_build" \
    --requirement "$lambda_tools_src/requirements.txt"
fi

(cd "$lambda_tools_build" && zip -qr "$assets_dir"/lambda-tools.zip . \
  -x "*/__pycache__/*" -x "*.dist-info/*" -x "*.egg-info/*")

rm -rf "$lambda_tools_build"

echo "lambda-tools packaged successfully"

# ── Lambda Tools Diagram ──────────────────────────────────────────────
rm -f "$assets_dir"/lambda-diagram-tools.zip
(cd agentcore-gateway/lambda-diagram-tools && zip -qr "$assets_dir"/lambda-diagram-tools.zip .)

echo "lambda-diagram-tools packaged successfully"
