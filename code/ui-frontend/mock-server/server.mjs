import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { WebSocketServer } from 'ws';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const HTTP_PORT = 8081;
const WS_PORT = 8080;
const FIXTURES_DIR = path.join(__dirname, 'fixtures');

// --- HTTP Server (config + file browser) ---

const httpServer = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = new URL(req.url, `http://localhost:${HTTP_PORT}`);

  if (url.pathname === '/config.json') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        cognitoUserPoolId: 'mock-pool-id',
        cognitoClientId: 'mock-client-id',
        cognitoIdentityPoolId: 'mock-identity-pool-id',
        cognitoDomain: 'mock.auth.localhost',
        awsRegion: 'us-east-1',
        runtimeArn: 'mock-runtime-arn',
        filesBucket: 'mock-bucket',
      }),
    );
    return;
  }

  if (url.pathname === '/files') {
    const objects = [];
    const walkDir = (dir, prefix) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const fullPath = path.join(dir, entry.name);
        const key = prefix + entry.name;
        if (entry.isDirectory()) {
          walkDir(fullPath, key + '/');
        } else {
          const stat = fs.statSync(fullPath);
          objects.push({
            Key: key,
            Size: stat.size,
            LastModified: stat.mtime.toISOString(),
          });
        }
      }
    };
    walkDir(path.join(FIXTURES_DIR, 'projects'), 'projects/');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(objects));
    return;
  }

  if (url.pathname === '/file') {
    const key = url.searchParams.get('key');
    if (!key) {
      res.writeHead(400);
      res.end('Missing key parameter');
      return;
    }
    const filePath = path.join(FIXTURES_DIR, key);
    if (!filePath.startsWith(FIXTURES_DIR) || !fs.existsSync(filePath)) {
      res.writeHead(404);
      res.end('File not found');
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const BINARY_CONTENT_TYPES = {
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.gif': 'image/gif',
      '.webp': 'image/webp',
      '.bmp': 'image/bmp',
      '.ico': 'image/x-icon',
    };

    const binaryType = BINARY_CONTENT_TYPES[ext];
    if (binaryType) {
      const data = fs.readFileSync(filePath);
      res.writeHead(200, { 'Content-Type': binaryType });
      res.end(data);
    } else {
      const content = fs.readFileSync(filePath, 'utf-8');
      res.writeHead(200, { 'Content-Type': 'text/plain' });
      res.end(content);
    }
    return;
  }

  res.writeHead(404);
  res.end('Not found');
});

httpServer.listen(HTTP_PORT, () => {
  console.log(`Mock HTTP server running on http://localhost:${HTTP_PORT}`);
  console.log(`  GET /config.json  - Runtime config`);
  console.log(`  GET /files        - List project files`);
  console.log(`  GET /file?key=... - Get file content`);
});

// --- WebSocket Server (AgentCore format) ---

const wss = new WebSocketServer({ port: WS_PORT });

wss.on('connection', (ws) => {
  console.log('WebSocket client connected');

  ws.on('message', (raw) => {
    try {
      const data = JSON.parse(raw.toString());
      console.log('Received:', data);

      // Handle init message (has project_id field)
      if (data.project_id) {
        // Send stage_start
        ws.send(JSON.stringify({ type: 'stage_start', node_id: 'requirements_analysis' }));

        // Simulate agent thinking, then respond
        setTimeout(() => {
          ws.send(
            JSON.stringify({
              type: 'text_chunk',
              content: `Welcome to the **AI-DLC Platform**! I'm your development assistant.\n\nI'll help you build your project: **${data.project_name || data.project_id}**.\n\nLet's start by analyzing your requirements. What would you like to build?`,
            }),
          );
        }, 500);

        // Send an artifact update after a delay
        setTimeout(() => {
          ws.send(
            JSON.stringify({
              type: 'artifact_updated',
              file_path: 'projects/sample-app/aidlc-state.md',
              presigned_url: `http://localhost:${HTTP_PORT}/file?key=projects/sample-app/aidlc-state.md`,
            }),
          );
        }, 1500);

        return;
      }

      // Handle follow-up messages (plain text)
      const userMessage = typeof data === 'string' ? data : data.message || JSON.stringify(data);

      // Send processing indicator
      ws.send(JSON.stringify({ type: 'text_chunk', content: '' }));

      setTimeout(() => {
        ws.send(JSON.stringify({ type: 'text_chunk', content: generateResponse(userMessage) }));
      }, 800 + Math.random() * 1200);
    } catch (e) {
      console.error('Failed to parse message:', e);
    }
  });

  ws.on('close', () => {
    console.log('WebSocket client disconnected');
  });
});

console.log(`Mock WebSocket server running on ws://localhost:${WS_PORT}`);

// --- Response generator ---

function generateResponse(userMessage) {
  const msg = userMessage.toLowerCase();

  if (msg.includes('hello') || msg.includes('hi') || msg.includes('introduce')) {
    return `Hello! I'm the **mock iCode agent** running locally.\n\nI can simulate responses for testing the UI. Try asking me to:\n- Build something\n- Create a diagram\n- Explain an architecture\n\n> This is a mock response. In production, this connects to the real AgentCore runtime.`;
  }

  if (msg.includes('diagram') || msg.includes('architecture')) {
    return `## Architecture Diagram\n\nHere's a high-level view:\n\n\`\`\`\nCloudFront -> S3 (React SPA)\n                |\n           API Gateway\n                |\n            Lambda\n                |\n           DynamoDB\n\`\`\`\n\nThis is a **serverless** architecture using AWS managed services.\n\n*Mock response for UI testing.*`;
  }

  if (msg.includes('build') || msg.includes('create') || msg.includes('make')) {
    return `I'd be happy to help you build that! Let me outline the steps:\n\n1. **Requirements Analysis** - Understand what we're building\n2. **Design** - Plan the architecture\n3. **Implementation** - Generate the code\n4. **Testing** - Verify everything works\n\nShall I proceed with step 1?\n\n*Mock response for UI testing.*`;
  }

  return `You said: *"${userMessage}"*\n\nThis is a **mock response** from the local development server. The real agent would process your request through AgentCore.\n\nThe mock server echoes your message and returns formatted markdown to test the UI rendering.`;
}
