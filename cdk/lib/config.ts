export interface RuntimeConfig {
  name: string;
  description: string;
  bedrockModelId: string;
  networkMode: "PUBLIC" | "VPC";
  idleSessionTimeoutSeconds: number;
  maxLifetimeSeconds: number;
  allowlistedHeaders: string[];
}

export interface CognitoConfig {
  userPoolName: string;
  appClientName: string;
}

export interface GatewayConfig {
  name: string;
  description: string;
  instructions: string;
  searchType: "SEMANTIC" | "NONE";
  supportedVersions: string[];
  exceptionLevel: "DEBUG" | "DEFAULT";
}

export interface GatewayTargetConfig {
  name: string;
  description: string;
}

export interface LambdaConfig {
  functionName: string;
  runtime: string;
  memorySize: number;
  timeoutSeconds: number;
}

export interface MemoryConfig {
  name: string;
  description: string;
  expirationDays: number;
  strategies: string[];
}

export interface S3Config {
  bucketNameAgentPrefix: string;
  bucketNameWebPrefix: string;
  removalPolicy: "DESTROY" | "RETAIN";
}

export interface VpcConfig {
  enabled: boolean;
  vpcId?: string;
  subnetIds?: string[];
  securityGroupIds?: string[];
}

export interface ObservabilityConfig {
  samplingPercentage: number;
}

export interface SystemPromptConfig {
  bucketNamePrefix: string;
  agentName: string;
}

export interface AtlassianIntegrationConfig {
  enabled: boolean;
  mcpServerUrl: string;
  atlassianOAuthProviderArn: string;
  atlassianOAuthSecretArn: string;
}

export interface IntegrationsConfig {
  atlassian: AtlassianIntegrationConfig;
}

export interface DeploymentConfig {
  awsAccount?: string;
  stackName: string;
  projectName: string;
  defaultBedrockModelId: string;
  parameterStorePrefix: string;
  tags: Record<string, string>;
  runtime: RuntimeConfig;
  cognito: CognitoConfig;
  gateway: GatewayConfig;
  gatewayTarget: GatewayTargetConfig;
  toolsLambda: LambdaConfig;
  diagramToolslambda: LambdaConfig;
  memory: MemoryConfig;
  s3: S3Config;
  vpc: VpcConfig;
  observability: ObservabilityConfig;
  systemPrompt: SystemPromptConfig;
  integrations?: IntegrationsConfig;
}

function getDefaultConfig(): DeploymentConfig {
  const runtimeName = "strands_agent";
  return {
    stackName: "AgentCoreStrandsStack",
    projectName: "agentcore-strands",
    parameterStorePrefix: "/agentcore/",
    defaultBedrockModelId: "global.anthropic.claude-sonnet-4-6",
    tags: {},
    runtime: {
      name: runtimeName,
      description: "Strands Agent SDK runtime",
      bedrockModelId: "global.anthropic.claude-sonnet-4-6",
      networkMode: "PUBLIC",
      idleSessionTimeoutSeconds: 900,
      maxLifetimeSeconds: 28800,
      allowlistedHeaders: [],
    },
    cognito: {
      userPoolName: "agentcore-user-pool",
      appClientName: "agentcore-client",
    },
    gateway: {
      name: "agentcore-gateway",
      description: "MCP gateway for agent tools",
      instructions: "Use this gateway to access file operation tools",
      searchType: "SEMANTIC",
      supportedVersions: ["MCP_2025_03_26"],
      exceptionLevel: "DEFAULT",
    },
    gatewayTarget: {
      name: "file-operations",
      description: "Lambda target for file and folder operations",
    },
    toolsLambda: {
      functionName: "agentcore-file-tools",
      runtime: "PYTHON_3_12",
      memorySize: 256,
      timeoutSeconds: 30,
    },
    diagramToolslambda: {
      functionName: "agentcore-awsdiagram-tools",
      runtime: "PYTHON_3_12",
      memorySize: 256,
      timeoutSeconds: 30,
    },
    memory: {
      name: "agent_memory",
      description: "Short-term and long-term memory for agent",
      expirationDays: 90,
      strategies: ["summarization", "semantic"],
    },
    s3: {
      bucketNameAgentPrefix: "agentcore-files",
      bucketNameWebPrefix: "aidlc-ui",
      removalPolicy: "DESTROY",
    },
    vpc: {
      enabled: false,
    },
    observability: {
      samplingPercentage: 100,
    },
    systemPrompt: {
      bucketNamePrefix: "runtime",
      agentName: runtimeName,
    },
    integrations: {
      atlassian: {
        enabled: true,
        mcpServerUrl: "https://mcp.atlassian.com/v1/mcp",
        // These ARNs are account/region-specific and must be set per environment.
        // Use ATLASSIAN_OAUTH_PROVIDER_ARN and ATLASSIAN_OAUTH_SECRET_ARN env vars,
        // or override via ENV_OVERRIDES for each target environment.
        // When empty, the Atlassian integration is skipped during deployment.
        atlassianOAuthProviderArn:
          process.env.ATLASSIAN_OAUTH_PROVIDER_ARN ?? "",
        atlassianOAuthSecretArn: process.env.ATLASSIAN_OAUTH_SECRET_ARN ?? "",
      },
    },
  };
}

/**
 * Environment-specific overrides. Each key is an environment name
 * and the value is a partial DeploymentConfig that gets deep-merged
 * on top of the defaults.
 */
const ENV_OVERRIDES: Record<string, DeepPartial<DeploymentConfig>> = {
  dev: {
    stackName: "AgentCoreStrandsDev",
    tags: { Environment: "dev" },
    s3: { removalPolicy: "DESTROY" },
  },
  staging: {
    stackName: "AgentCoreStrandsStaging",
    tags: { Environment: "staging" },
  },
  prod: {
    stackName: "AgentCoreStrandsProd",
    tags: { Environment: "prod" },
    observability: { samplingPercentage: 100 },
  },
};

/** Recursive partial type for deep merge support. */
type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P];
};

/** Deep merge source into target, returning a new object. Arrays are replaced, not merged. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function deepMerge(target: any, source: any): any {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    const sourceVal = source[key];
    const targetVal = target[key];
    if (
      sourceVal !== undefined &&
      typeof sourceVal === "object" &&
      sourceVal !== null &&
      !Array.isArray(sourceVal) &&
      typeof targetVal === "object" &&
      targetVal !== null &&
      !Array.isArray(targetVal)
    ) {
      result[key] = deepMerge(targetVal, sourceVal);
    } else if (sourceVal !== undefined) {
      result[key] = sourceVal;
    }
  }
  return result;
}

/**
 * Returns a fully-populated DeploymentConfig.
 * When called with no arguments, returns defaults.
 * When called with an environment name, merges that environment's
 * overrides on top of the defaults.
 */
export function getConfig(env?: string): DeploymentConfig {
  const defaults = getDefaultConfig();
  if (!env) {
    return defaults;
  }
  const overrides = ENV_OVERRIDES[env];
  if (!overrides) {
    return defaults;
  }
  const merged: DeploymentConfig = deepMerge(defaults, overrides);

  // Ensure systemPrompt.agentName stays in sync with runtime.name
  // if the override changed runtime.name but not systemPrompt.agentName
  if (overrides.runtime?.name && !overrides.systemPrompt?.agentName) {
    merged.systemPrompt.agentName = merged.runtime.name;
  }

  return merged;
}
