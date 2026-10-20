import { S3Client, ListObjectsV2Command, GetObjectCommand } from '@aws-sdk/client-s3';
import { fetchAuthSession } from 'aws-amplify/auth';
import { getConfig } from '../lib/config';
import { displaySegment } from '../lib/displayLabels';

// Re-export so existing importers (and tests) can reach it via s3Service.
export { displaySegment };

const isMock = import.meta.env.VITE_MOCK === 'true';
const MOCK_API = 'http://localhost:8081'; // nosemgrep: react-insecure-request — mock server, local dev only

// --- Types ---

export interface FileTreeNode {
  id: string;
  name: string;
  type: 'file' | 'folder';
  children?: FileTreeNode[];
  key?: string;
  size?: number;
}

interface S3Object {
  Key: string;
  Size?: number;
  LastModified?: string;
}

// --- S3 Client (lazy, credential-aware) ---

let s3Client: S3Client | null = null;

async function getS3Client(): Promise<S3Client> {
  const session = await fetchAuthSession();
  const credentials = session.credentials;
  if (!credentials) throw new Error('No AWS credentials available. Ensure Identity Pool is configured.');

  // Recreate client each time to handle credential rotation
  s3Client = new S3Client({
    region: getConfig().awsRegion,
    credentials,
  });
  return s3Client;
}

export function resetS3Client(): void {
  s3Client = null;
}

// --- Public API ---

export async function listFiles(prefix?: string): Promise<S3Object[]> {
  if (isMock) {
    const res = await fetch(`${MOCK_API}/files`); // nosemgrep: react-insecure-request
    return await res.json();
  }

  const config = getConfig();
  const client = await getS3Client();
  const objects: S3Object[] = [];
  let continuationToken: string | undefined;
  const effectivePrefix = prefix ?? 'projects/';

  do {
    const command = new ListObjectsV2Command({
      Bucket: config.filesBucket,
      Prefix: effectivePrefix,
      ContinuationToken: continuationToken,
    });
    const response = await client.send(command);
    if (response.Contents) {
      objects.push(
        ...response.Contents.map((c) => ({
          Key: c.Key!,
          Size: c.Size,
          LastModified: c.LastModified?.toISOString(),
        })),
      );
    }
    continuationToken = response.NextContinuationToken;
  } while (continuationToken);

  return objects;
}

export async function getFileContent(key: string): Promise<string> {
  if (isMock) {
    const res = await fetch(`${MOCK_API}/file?key=${encodeURIComponent(key)}`); // nosemgrep: react-insecure-request
    return await res.text();
  }

  const config = getConfig();
  const client = await getS3Client();

  const command = new GetObjectCommand({
    Bucket: config.filesBucket,
    Key: key,
  });
  const response = await client.send(command);
  return (await response.Body?.transformToString()) ?? '';
}

// --- Tree builder ---

// Folder labels are relabeled for display via `displaySegment` (imported at
// top from `lib/displayLabels`); file fetching uses the untouched S3 key.

let idCounter = 0;

export function buildFileTree(objects: S3Object[], rootName = 'projects'): FileTreeNode {
  idCounter = 0;
  const root: FileTreeNode = {
    id: String(idCounter++),
    name: rootName,
    children: [],
    type: 'folder',
  };

  for (const obj of objects) {
    const parts = obj.Key.split('/').filter(Boolean);
    // Hide internal/dot-prefixed entries (e.g. the `.collab/` idempotency
    // marker) from the file tree — they are runtime bookkeeping, not artifacts.
    if (parts.some((p, i) => i >= 1 && p.startsWith('.'))) continue;
    let current = root;

    for (let i = 1; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1 && !obj.Key.endsWith('/');
      // Folder labels may be relabeled for display; file names are kept verbatim.
      const label = isFile ? part : displaySegment(part);

      let child = current.children?.find((c) => c.name === label);
      if (!child) {
        child = {
          id: String(idCounter++),
          name: label,
          type: isFile ? 'file' : 'folder',
          children: isFile ? undefined : [],
          key: isFile ? obj.Key : undefined,
          size: isFile ? obj.Size : undefined,
        };
        current.children!.push(child);
      }
      if (!isFile) {
        current = child;
      }
    }
  }

  sortTree(root);
  return root;
}

function sortTree(node: FileTreeNode): void {
  if (node.children) {
    node.children.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    node.children.forEach(sortTree);
  }
}

// --- Image utilities ---

const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'];

export function isImageFile(filePath: string): boolean {
  const lower = filePath.toLowerCase();
  return IMAGE_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

export async function getFileObjectUrl(key: string): Promise<string> {
  if (isMock) {
    const res = await fetch(`${MOCK_API}/file?key=${encodeURIComponent(key)}`); // nosemgrep: react-insecure-request
    if (!res.ok) throw new Error(`Failed to fetch image: ${res.status}`);
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  const config = getConfig();
  const client = await getS3Client();

  const command = new GetObjectCommand({
    Bucket: config.filesBucket,
    Key: key,
  });
  const response = await client.send(command);
  const bytes = await response.Body?.transformToByteArray();
  if (!bytes) throw new Error('Empty response body');

  const contentType = response.ContentType || 'application/octet-stream';
  const blob = new Blob([bytes], { type: contentType });
  return URL.createObjectURL(blob);
}
