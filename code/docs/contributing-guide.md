# Contributing Guide and Coding Standards

This document covers repository conventions, coding standards, dependency management, and how to extend the project with new agents, tools, or frontend features.

## Repository Structure

```
.
├── cdk/                          # CDK infrastructure (TypeScript)
│   ├── app.ts                    # CDK app entrypoint
│   ├── config.ts                 # Typed deployment configuration
│   ├── stack.ts                  # Main CDK stack (1200+ lines)
│   ├── stack.test.ts             # CDK stack tests
│   └── package.json              # Pinned CDK dependencies
│
├── code/
│   ├── agentcore-gateway/
│   │   └── lambda-tools/
│   │       └── handler.py        # Lambda handler (file operations)
│   │
│   ├── agentcore-runtime/
│   │   └── icode-agent/          # Agent container source
│   │       ├── agents/           # 13 stage agents (inception/ + construction/)
│   │       ├── config/           # Model configuration
│   │       ├── tools/            # Shared tools (gateway, file_ops, state, etc.)
│   │       ├── main.py           # CLI entrypoint
│   │       ├── main_ws.py        # WebSocket entrypoint (AgentCore Runtime)
│   │       ├── orchestrator.py   # Graph builder with conditional edges
│   │       ├── shared_state.py   # SharedState schema and validation
│   │       ├── Dockerfile        # Container image
│   │       └── pyproject.toml    # Python dependencies
│   │
│   ├── scripts/
│   │   └── run.py                # Helper script
│   │
│   └── ui-frontend/              # React SPA
│       ├── app/                  # Application code
│       │   ├── components/       # React components
│       │   ├── context/          # AuthContext, WebSocketContext
│       │   ├── lib/              # Config, Amplify setup
│       │   └── types/            # TypeScript type definitions
│       ├── styles/               # Tailwind CSS
│       └── __tests__/            # Frontend tests
│
├── scripts/                      # Deployment and helper scripts
├── tests/                        # Python and TypeScript test suites
└── docs/                         # Documentation
```

## Code Style

### Python (Agent Code)

- Python 3.13+ with type hints on all function signatures
- Use `from __future__ import annotations` for forward references
- Docstrings: Google style with Args/Returns/Raises sections
- Tool functions: `@tool(context=True)` decorator, first param is `tool_context: dict`
- State access: always use `get_state(tool_context)` from `tools/_compat.py`
- File I/O: always use `tools/file_ops.py` functions (never direct S3 calls from agent code)
- Gateway calls: always use `tools/gateway.call_tool()` (handles name translation)
- Imports: standard library first, then third-party, then local modules
- No wildcard imports
- Module-level constants in `UPPER_SNAKE_CASE`
- Private functions prefixed with `_`

### TypeScript (CDK)

- Strict TypeScript with explicit types on all exports
- CDK constructs follow the pattern: create resource, configure, grant permissions, add outputs
- Configuration via typed interfaces in `config.ts`
- Inline Lambda code for simple custom resources (< 100 lines)
- Asset-based Lambda code for complex handlers
- Comments with `// -------` section dividers for major blocks in `stack.ts`

### TypeScript/React (Frontend)

- React 18 with functional components and hooks
- Explicit Props interfaces for all components
- Context providers for shared state (no Redux/Zustand)
- Tailwind CSS v4 utility classes (see `.kiro/steering/tailwindcss.md` for project conventions)
- Semantic color tokens (`bg-primary`, `text-foreground`) over raw colors
- Radix UI primitives for accessible dialogs, selects, alerts
- `lucide-react` for icons
- File naming: PascalCase for components (`ChatPanel.tsx`), camelCase for utilities (`config.ts`)

### Shell Scripts

- `#!/usr/bin/env bash` shebang
- `set -euo pipefail` at the top
- `UPPER_SNAKE_CASE` for variables
- Resolve paths relative to script directory using `SCRIPT_DIR`
- Support optional environment argument (`$1`)
- Echo progress with `==>` prefix

## Dependency Management

All dependency versions are pinned to exact versions for reproducible builds.

### CDK (TypeScript)

`cdk/package.json` uses exact versions (no `^` or `~`):

```json
"dependencies": {
  "@aws-cdk/aws-bedrock-agentcore-alpha": "2.243.0-alpha.0",
  "aws-cdk-lib": "2.243.0",
  "constructs": "10.5.1"
}
```

All CDK packages must be the same version. To upgrade:

```bash
cd cdk
npm install @aws-cdk/aws-bedrock-agentcore-alpha@X.Y.Z aws-cdk-lib@X.Y.Z
```

### Python (Agent)

`code/agentcore-runtime/icode-agent/pyproject.toml` lists dependencies without pins (resolved at build time). For reproducible builds, consider adding a `requirements.txt` with `==` pins.

### Frontend

`code/ui-frontend/package.json` uses a mix of exact and caret versions. React is a peer dependency at `18.3.1`.

## Adding a New SDLC Stage Agent

1. Create the agent module in the appropriate phase directory:
   - Inception: `code/agentcore-runtime/icode-agent/agents/inception/{stage_name}.py`
   - Construction: `code/agentcore-runtime/icode-agent/agents/construction/{stage_name}.py`

2. Follow the existing agent pattern:

```python
"""Stage description."""

from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

from agents.factory import create_stage_agent
from tools.approval import present_approval_gate, process_approval_response
from tools.file_io import write_artifact

@tool(context=True)
def my_tool(tool_context: dict, param: str) -> str:
    """Tool description."""
    from tools._compat import get_state
    state = get_state(tool_context)
    # ... tool logic ...
    return "result"

SYSTEM_PROMPT = """\
You are the {Stage Name} Agent...
"""

TOOLS = [my_tool, present_approval_gate, process_approval_response, write_artifact]

def create_{stage_name}_agent(model: BedrockModel, session_manager=None) -> Agent:
    return create_stage_agent(
        stage_name="{stage_name}",
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        model=model,
        session_manager=session_manager,
    )
```

3. Add the stage to `shared_state.py`:
   - Add to `STAGE_NAMES` list
   - Add to `initialize_shared_state` → `stage_progress`
   - Add artifact section if needed

4. Register in `orchestrator.py`:
   - Import the factory function
   - Add to the `agents` dict in `build_sdlc_graph`
   - Add to `NODE_NAMES`
   - Wire edges (conditional or unconditional)
   - Update `_STAGE_ORDER` in `main.py` for resume logic

5. Add tests in `tests/python/`

6. Deploy with `bash scripts/deploy.sh`

## Adding a New Frontend Page

1. Create the component in `code/ui-frontend/app/components/`:

```tsx
import { useAuth } from '../context/AuthContext';

export function MyPage() {
  const { user, isAuthenticated } = useAuth();
  
  return (
    <div className="min-h-screen bg-background">
      {/* Page content */}
    </div>
  );
}
```

2. Add the route in `code/ui-frontend/app/routes.tsx`:

```tsx
{
  path: '/my-page',
  Component: MyPage,
},
```

3. Add tests in `code/ui-frontend/__tests__/`

## Adding a New Frontend Component

1. Create in the appropriate subdirectory:
   - `app/components/chat/` — Chat-related components
   - `app/components/document/` — Document viewer components
   - `app/components/ui/` — Reusable UI primitives (Radix-based)
   - `app/components/` — Page-level components

2. Use explicit Props interface:

```tsx
interface MyComponentProps {
  title: string;
  onAction: (id: string) => void;
}

export function MyComponent({ title, onAction }: MyComponentProps) {
  return (/* ... */);
}
```

3. Use semantic Tailwind tokens for colors (see `.kiro/steering/tailwindcss.md`)

## PR Checklist

Before submitting changes:

- [ ] All test suites pass (`npm test` in `cdk/`, `pytest tests/python/`, `npx vitest --run` in `code/ui-frontend/`)
- [ ] No new TypeScript/Python lint warnings
- [ ] Dependencies pinned to exact versions
- [ ] New environment variables documented
- [ ] New tools follow the existing dispatch/decorator pattern
- [ ] New agents registered in orchestrator with proper edges
- [ ] SharedState changes reflected in `shared_state.py` validation
- [ ] Frontend components use semantic color tokens
- [ ] Documentation updated for user-facing changes
