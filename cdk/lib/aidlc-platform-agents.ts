import * as cdk from "aws-cdk-lib";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import { Construct } from "constructs";

import * as path from "path";
import * as fs from "fs";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as cr from "aws-cdk-lib/custom-resources";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda_ from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { DeploymentConfig } from "./config";
import { AIDLCPlatformAuth } from "./aidlc-platform-auth";
import { getForceRedeployTimestamp } from "./utils/deployment-utils";
import { AssetsBucket } from "./aidlc-assetbucket";
import { AIDLCPlatformBuild } from "./aidlc-platform-builds";

export interface Params {
  vpc: ec2.Vpc;
  auth: AIDLCPlatformAuth;
  assetsBucket: AssetsBucket;
  build: AIDLCPlatformBuild;
  readonly config: DeploymentConfig;
}

export class AIDLCPlatformAgents extends Construct {
  public readonly projectBucket: s3.Bucket;
  public readonly gateway: agentcore.Gateway;
  public readonly memory: agentcore.Memory;
  public readonly runtime: agentcore.Runtime;
  public readonly toolsLambda: lambda_.Function;
  public readonly toolsDiagramLambda: lambda_.Function;
  public readonly systemPromptPrefix: string;
  public readonly ssmParameters: ssm.StringParameter[];
  public readonly integrationConfigApiUrl: string;
  public readonly projectApiUrl: string;

  constructor(scope: Construct, id: string, props: Params) {
    super(scope, id);

    const { config } = props;

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    const removalPolicy =
      config.s3.removalPolicy === "DESTROY"
        ? cdk.RemovalPolicy.DESTROY
        : cdk.RemovalPolicy.RETAIN;

    this.projectBucket = new s3.Bucket(this, "ProjectBucket", {
      bucketName: `${config.s3.bucketNameAgentPrefix}-${account}-${region}`,
      removalPolicy,
      autoDeleteObjects: config.s3.removalPolicy === "DESTROY",
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });
    // Preserve the original CloudFormation logical IDs from when this bucket
    // was defined at the stack root level. Moving it into this construct
    // changed the generated logical IDs, which causes CloudFormation to
    // try delete + recreate the existing bucket (fails if not empty) and
    // creates a new bucket policy resource that conflicts with the old one
    // (the old policy deletion wipes the new policy, leaving a Deny-all).
    (this.projectBucket.node.defaultChild as cdk.CfnResource).overrideLogicalId(
      "ProjectBucket01EBB8FB",
    );
    const bucketPolicy = this.projectBucket.policy;
    if (bucketPolicy) {
      (bucketPolicy.node.defaultChild as cdk.CfnResource).overrideLogicalId(
        "ProjectBucketPolicy4340EDEA",
      );
    }

    this.projectBucket.addCorsRule({
      allowedOrigins: ["*"],
      // GET: Document Viewer fetches artifacts via presigned GET URLs.
      // PUT: Business Analyst uploads requirements docs via presigned PUT URLs
      //      (RequirementsUploadControl). Without PUT the browser preflight
      //      blocks the upload and surfaces as "Failed to fetch".
      allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.PUT],
      allowedHeaders: ["*"],
      maxAge: 3600,
    });

    // -------------------------------------------------------
    // Lambda Function (gateway tools)
    // -------------------------------------------------------
    this.toolsLambda = new lambda_.Function(this, "ToolsLambda", {
      functionName: config.toolsLambda.functionName,
      runtime: lambda_.Runtime.PYTHON_3_12,
      memorySize: config.toolsLambda.memorySize,
      timeout: cdk.Duration.seconds(config.toolsLambda.timeoutSeconds),
      code: lambda_.Code.fromBucketV2(
        props.assetsBucket.bucket,
        props.assetsBucket.getS3Key("lambda-tools.zip"),
      ),
      handler: "handler.handler",
      environment: {
        BUCKET_NAME: this.projectBucket.bucketName,
        FORCE_REDEPLOY: getForceRedeployTimestamp(),
      },
    });

    // 4. Diagram Lambda
    this.toolsDiagramLambda = new lambda_.Function(this, "ToolsDiagramLambda", {
      functionName: config.diagramToolslambda.functionName,
      runtime: lambda_.Runtime.FROM_IMAGE,
      handler: lambda_.Handler.FROM_IMAGE,
      code: lambda_.EcrImageCode.fromEcrImage(props.build.diagramEcrRepo),
      timeout: cdk.Duration.minutes(15),
      memorySize: 2048,
      ephemeralStorageSize: cdk.Size.mebibytes(2048),
      environment: {
        FORCE_REDEPLOY: getForceRedeployTimestamp(),
        DIAGRAM_BUCKET: this.projectBucket.bucketName,
        MODEL_ID: config.defaultBedrockModelId,
      },
    });

    // Ensure the Docker image is built before the Lambda is created
    this.toolsDiagramLambda.node.addDependency(
      props.build.diagramBuildCustomResource,
    );

    // Grant the Lambda read/write/delete on the S3 files bucket
    this.projectBucket.grantReadWrite(this.toolsLambda);
    this.projectBucket.grantDelete(this.toolsLambda);

    // Grant the Lambda read/write/delete on the S3 files bucket
    this.projectBucket.grantReadWrite(this.toolsDiagramLambda);
    this.projectBucket.grantDelete(this.toolsDiagramLambda);

    // Grant the diagram Lambda permission to invoke Bedrock models
    this.toolsDiagramLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${account}:inference-profile/*`,
        ],
      }),
    );

    props.assetsBucket.bucket.grantRead(this.toolsLambda);

    // -------------------------------------------------------
    // System Prompt S3 Prefix
    // -------------------------------------------------------
    this.systemPromptPrefix = `runtime-${config.systemPrompt.agentName}-systemprompts`;

    // -------------------------------------------------------
    // AgentCore Memory (STM + LTM)
    // Create Memory before Gateway so it has time to leave
    // the CREATING transitional state if a rollback occurs.
    // -------------------------------------------------------
    const strategyMap: Record<string, () => agentcore.IMemoryStrategy> = {
      summarization: () => agentcore.MemoryStrategy.usingBuiltInSummarization(),
      semantic: () => agentcore.MemoryStrategy.usingBuiltInSemantic(),
      userPreference: () =>
        agentcore.MemoryStrategy.usingBuiltInUserPreference(),
      episodic: () => agentcore.MemoryStrategy.usingBuiltInEpisodic(),
    };

    const memoryStrategies = config.memory.strategies
      .map((s: any) => strategyMap[s]?.())
      .filter((s: any): s is agentcore.IMemoryStrategy => s !== undefined);

    this.memory = new agentcore.Memory(this, "Memory", {
      memoryName: config.memory.name,
      description: config.memory.description,
      expirationDuration: cdk.Duration.days(config.memory.expirationDays),
      memoryStrategies,
    });

    // -------------------------------------------------------
    // AgentCore Gateway (MCP protocol + Cognito auth)
    // -------------------------------------------------------
    this.gateway = new agentcore.Gateway(this, "Gateway", {
      gatewayName: config.gateway.name,
      description: config.gateway.description,
      protocolConfiguration: agentcore.GatewayProtocol.mcp({
        instructions: config.gateway.instructions,
        searchType:
          config.gateway.searchType === "SEMANTIC"
            ? agentcore.McpGatewaySearchType.SEMANTIC
            : undefined,
        supportedVersions: config.gateway.supportedVersions.map((v: any) => {
          if (v === "MCP_2025_03_26")
            return agentcore.MCPProtocolVersion.MCP_2025_03_26;
          if (v === "MCP_2025_06_18")
            return agentcore.MCPProtocolVersion.MCP_2025_06_18;
          return v as unknown as agentcore.MCPProtocolVersion;
        }),
      }),
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingCognito({
        userPool: props.auth.userPool,
        allowedClients: [props.auth.userPoolClient],
      }),
      exceptionLevel:
        config.gateway.exceptionLevel === "DEBUG"
          ? agentcore.GatewayExceptionLevel.DEBUG
          : undefined,
    });

    // Ensure Memory finishes creating before Gateway, so a Gateway
    // rollback doesn't hit Memory while it's still in CREATING state.
    this.gateway.node.addDependency(this.memory);

    // -------------------------------------------------------
    // Gateway Lambda Target (file operations)
    // -------------------------------------------------------

    const toolSchema = agentcore.ToolSchema.fromInline([
      {
        name: "create_folder",
        description: "Creates a folder in S3",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            folder_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "The folder path to create in S3",
            },
          },
          required: ["folder_path"],
        },
      },
      {
        name: "read_file",
        description: "Reads a file from S3",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            file_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "The file path to read from S3",
            },
          },
          required: ["file_path"],
        },
      },
      {
        name: "write_file",
        description: "Writes content to a file in S3",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            file_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "The file path to write to in S3",
            },
            content: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "The content to write to the file",
            },
          },
          required: ["file_path", "content"],
        },
      },
      {
        name: "list_files",
        description: "Lists S3 objects matching a prefix",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            prefix: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "S3 key prefix to list",
            },
            max_keys: {
              type: agentcore.SchemaDefinitionType.INTEGER,
              description: "Maximum number of keys to return (default 1000)",
            },
          },
          required: ["prefix"],
        },
      },
      {
        name: "get_presigned_url",
        description:
          "Generates a short-lived presigned GET URL for an S3 object",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            file_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "The S3 key to generate a presigned URL for",
            },
            expires_in: {
              type: agentcore.SchemaDefinitionType.INTEGER,
              description: "URL expiration time in seconds (default 900)",
            },
          },
          required: ["file_path"],
        },
      },
      {
        name: "delete_folder",
        description: "Deletes all objects under an S3 prefix",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            prefix: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: "S3 key prefix to delete",
            },
          },
          required: ["prefix"],
        },
      },
      {
        name: "extract_document_text",
        description:
          "Extracts plain text from an uploaded requirements document at the given S3 key. .md/.txt are returned directly; .pdf is parsed with pypdf; .docx is parsed with python-docx. The normalized text is written to .../uploaded/extracted/<filename>.txt and also returned.",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            file_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description:
                "S3 key of the uploaded document. Must live directly under an '.../uploaded/' prefix.",
            },
          },
          required: ["file_path"],
        },
      },
    ]);

    this.gateway.addLambdaTarget("FileOperationsTarget", {
      gatewayTargetName: config.gatewayTarget.name,
      description: config.gatewayTarget.description,
      lambdaFunction: this.toolsLambda,
      toolSchema,
      credentialProviderConfigurations: [
        agentcore.GatewayCredentialProvider.fromIamRole(),
      ],
    });

    const toolDiagramSchema = agentcore.ToolSchema.fromInline([
      {
        name: "generate_aws_architecture_diagram",
        description:
          "Generate professional AWS architecture diagrams with official AWS service icons. Stores the diagram to the specified folder_path and returns the full S3 key.",
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            architecture_description: {
              type: agentcore.SchemaDefinitionType.STRING,
              description:
                "Natural language description of AWS architecture to visualize",
            },
            folder_path: {
              type: agentcore.SchemaDefinitionType.STRING,
              description:
                "The S3 folder path where the diagram will be stored",
            },
          },
          required: ["architecture_description", "folder_path"],
        },
      },
    ]);

    this.gateway.addLambdaTarget("AWSDiagramOperationsTarget", {
      gatewayTargetName: "diagram-operations",
      description: "Lambda target for AWS architecture diagram generation",
      lambdaFunction: this.toolsDiagramLambda,
      toolSchema: toolDiagramSchema,
      credentialProviderConfigurations: [
        agentcore.GatewayCredentialProvider.fromIamRole(),
      ],
    });

    // -------------------------------------------------------
    // Atlassian MCP Proxy Lambda + Gateway Target
    // -------------------------------------------------------
    if (config.integrations?.atlassian?.enabled) {
      const atlassianSecretArn =
        config.integrations.atlassian.atlassianOAuthSecretArn;

      const atlassianProxyFn = new lambda_.Function(
        this,
        "AtlassianMcpProxyLambda",
        {
          functionName: `${config.projectName}-atlassian-mcp-proxy`,
          runtime: lambda_.Runtime.PYTHON_3_12,
          handler: "atlassian_mcp_proxy.handler",
          timeout: cdk.Duration.seconds(30),
          memorySize: 256,
          code: lambda_.Code.fromAsset(
            path.join(__dirname, "..", "custom-resources"),
            { bundling: undefined },
          ),
          environment: {
            ATLASSIAN_SECRET_ARN: atlassianSecretArn,
          },
        },
      );

      atlassianProxyFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["secretsmanager:GetSecretValue"],
          // Scope to the per-project Atlassian secrets the proxy resolves
          // (`aidlc/projects/{uuid}/atlassian`) plus the optional global
          // fallback secret when configured. Never fall back to "*", which
          // would grant read access to every secret in the account.
          resources: [
            `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*/atlassian*`,
            ...(atlassianSecretArn ? [atlassianSecretArn] : []),
          ],
        }),
      );

      atlassianProxyFn.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ["secretsmanager:GetSecretValue"],
          resources: [
            `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*`,
          ],
        }),
      );

      const atlassianToolSchema = agentcore.ToolSchema.fromInline([
        {
          name: "ensure_jira_project",
          description: "Ensure a Jira project exists, creating it if needed",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              project_name: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Project name",
              },
              project_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Optional project key",
              },
            },
            required: ["project_name"],
          },
        },
        {
          name: "ensure_confluence_space",
          description:
            "Ensure a Confluence space exists, creating it if needed",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              space_name: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Space name",
              },
              space_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Optional space key",
              },
            },
            required: ["space_name"],
          },
        },
        {
          name: "confluence_create_page",
          description: "Create a Confluence page",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              space_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Space key",
              },
              title: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page title",
              },
              content: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page content",
              },
            },
            required: ["space_key", "title", "content"],
          },
        },
        {
          name: "confluence_update_page",
          description: "Update an existing Confluence page",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              page_id: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page ID",
              },
              title: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "New title",
              },
              content: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "New content",
              },
            },
            required: ["page_id", "title", "content"],
          },
        },
        {
          name: "confluence_get_page",
          description: "Get a Confluence page by ID",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              page_id: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page ID",
              },
            },
            required: ["page_id"],
          },
        },
        {
          name: "confluence_search",
          description: "Search Confluence content",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              query: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "CQL search query",
              },
            },
            required: ["query"],
          },
        },
        {
          name: "confluence_add_label",
          description: "Add a label to a Confluence page",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              page_id: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page ID",
              },
              label: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Label name",
              },
            },
            required: ["page_id", "label"],
          },
        },
        {
          name: "confluence_get_labels",
          description: "Get all labels on a Confluence page",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              page_id: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Page ID",
              },
            },
            required: ["page_id"],
          },
        },
        {
          name: "jira_create_issue",
          description: "Create a Jira issue",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              project_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Project key",
              },
              summary: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Issue summary",
              },
              issue_type: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Issue type",
              },
            },
            required: ["project_key", "summary", "issue_type"],
          },
        },
        {
          name: "jira_update_issue",
          description: "Update a Jira issue",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              issue_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Issue key",
              },
              summary: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "New summary",
              },
            },
            required: ["issue_key"],
          },
        },
        {
          name: "jira_transition_issue",
          description: "Transition a Jira issue to a new status",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              issue_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Issue key",
              },
              transition_name: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Target status",
              },
            },
            required: ["issue_key", "transition_name"],
          },
        },
        {
          name: "jira_get_issue",
          description: "Get a Jira issue by key",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              issue_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Issue key",
              },
            },
            required: ["issue_key"],
          },
        },
        {
          name: "jira_create_sprint",
          description: "Create a Jira sprint",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              board_id: {
                type: agentcore.SchemaDefinitionType.INTEGER,
                description: "Board ID",
              },
              name: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Sprint name",
              },
            },
            required: ["board_id", "name"],
          },
        },
        {
          name: "jira_get_board",
          description: "Get a Jira board for a project",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              project_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Project key",
              },
            },
            required: ["project_key"],
          },
        },
        {
          name: "jira_list_backlog_issues",
          description: "List issues in the backlog for a Jira board",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              board_id: {
                type: agentcore.SchemaDefinitionType.INTEGER,
                description: "Board ID",
              },
              project_key: {
                type: agentcore.SchemaDefinitionType.STRING,
                description: "Optional project key filter",
              },
            },
            required: ["board_id"],
          },
        },
        {
          name: "jira_move_to_sprint",
          description: "Move issues to a sprint",
          inputSchema: {
            type: agentcore.SchemaDefinitionType.OBJECT,
            properties: {
              sprint_id: {
                type: agentcore.SchemaDefinitionType.INTEGER,
                description: "Sprint ID",
              },
              issue_keys: {
                type: agentcore.SchemaDefinitionType.ARRAY,
                description: "List of issue keys",
              },
            },
            required: ["sprint_id", "issue_keys"],
          },
        },
      ]);

      this.gateway.addLambdaTarget("AtlassianProxyTarget", {
        gatewayTargetName: "atlassian",
        description: "Atlassian MCP proxy Lambda (Confluence + Jira)",
        lambdaFunction: atlassianProxyFn,
        toolSchema: atlassianToolSchema,
        credentialProviderConfigurations: [
          agentcore.GatewayCredentialProvider.fromIamRole(),
        ],
      });

      new ssm.StringParameter(this, "SsmAtlassianTargetName", {
        parameterName: `${config.parameterStorePrefix}integrations/atlassian/target-name`,
        stringValue: "atlassian",
        description: "Atlassian Gateway target name",
      });
    }

    // -------------------------------------------------------
    // Integration Config API Lambda + API Gateway
    // -------------------------------------------------------
    const integrationConfigFn = new lambda_.Function(
      this,
      "IntegrationConfigApiLambda",
      {
        functionName: `${config.projectName}-integration-config-api`,
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: "integration_config_api.handler",
        timeout: cdk.Duration.seconds(30),
        memorySize: 256,
        code: lambda_.Code.fromAsset(
          path.join(__dirname, "..", "custom-resources"),
          { bundling: undefined },
        ),
        environment: {
          PROJECT_BUCKET: this.projectBucket.bucketName,
        },
      },
    );

    integrationConfigFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
        resources: [
          `${this.projectBucket.bucketArn}/*`,
          this.projectBucket.bucketArn,
        ],
      }),
    );

    integrationConfigFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "secretsmanager:CreateSecret",
          "secretsmanager:UpdateSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
        ],
        resources: [
          `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*`,
        ],
      }),
    );

    const integrationConfigApi = new apigw.RestApi(
      this,
      "IntegrationConfigApi",
      {
        restApiName: `${config.projectName}-integration-config-api`,
        defaultCorsPreflightOptions: {
          allowOrigins: apigw.Cors.ALL_ORIGINS,
          allowMethods: apigw.Cors.ALL_METHODS,
        },
      },
    );

    const cognitoAuthorizer = new apigw.CognitoUserPoolsAuthorizer(
      this,
      "IntegrationConfigAuthorizer",
      { cognitoUserPools: [props.auth.userPool] },
    );

    const lambdaIntegration = new apigw.LambdaIntegration(integrationConfigFn);
    const authOptions: apigw.MethodOptions = {
      authorizer: cognitoAuthorizer,
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    const projectsResource = integrationConfigApi.root.addResource("projects");
    const projectIdResource = projectsResource.addResource("{projectId}");
    const integrationsResource = projectIdResource.addResource("integrations");
    integrationsResource.addMethod("GET", lambdaIntegration, authOptions);

    const integrationIdResource =
      integrationsResource.addResource("{integrationId}");
    integrationIdResource.addMethod("PUT", lambdaIntegration, authOptions);

    const statusResource = integrationIdResource.addResource("status");
    statusResource.addMethod("GET", lambdaIntegration, authOptions);

    this.integrationConfigApiUrl = integrationConfigApi.url;

    // -------------------------------------------------------
    // Project API Lambda + API Gateway
    // -------------------------------------------------------
    // Shared, server-side Project Store + Project API (design Sections 2b, 7a).
    // Modeled directly on the Integration Config API above: a Python Lambda
    // behind an apigw.RestApi protected by a Cognito authorizer. A SEPARATE
    // RestApi is used so the `/projects/{projectId}/...` resource tree does not
    // collide with the Integration Config API's own `/projects/...` tree.
    const projectApiFn = new lambda_.Function(this, "ProjectApiLambda", {
      functionName: `${config.projectName}-project-api`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "project_api.handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      code: lambda_.Code.fromAsset(
        path.join(__dirname, "..", "custom-resources"),
        { bundling: undefined },
      ),
      environment: {
        PROJECT_BUCKET: this.projectBucket.bucketName,
      },
    });

    // IAM: record CRUD scoped to the platform projects prefix, ListBucket on the
    // bucket, and a separate PutObject for presigned-URL signing of uploaded
    // requirements documents (design Section 2b). The handler additionally
    // restricts keys in code; these statements keep the role least-privilege.
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Record CRUD for `_platform/projects/{uuid}.json`.
        actions: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        resources: [`${this.projectBucket.bucketArn}/_platform/projects/*`],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Presigned PUT signing for requirements uploads lands under each
        // project's `aidlc-docs/inception/requirements/uploaded/` prefix.
        actions: ["s3:PutObject"],
        resources: [
          `${this.projectBucket.bucketArn}/*/aidlc-docs/inception/requirements/uploaded/*`,
        ],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // ListBucket is bucket-scoped (the listing is constrained to the
        // platform prefix in handler code).
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // DeleteObject for artifact-tree cleanup when a project is deleted.
        // The handler restricts deletion to the project's own
        // `{artifact_prefix}/` (with a trailing-slash boundary), so a future
        // same-named project starts with a clean prefix. Object-scoped on the
        // project bucket; the handler is the authority on which keys are
        // removed (mirrors the upload PutObject statement above).
        actions: ["s3:DeleteObject"],
        resources: [`${this.projectBucket.bucketArn}/*`],
      }),
    );

    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        // Store an optional private-repo Git token for the C7 round-trip. The
        // link-repo handler writes it (put-or-create) to
        // `aidlc/projects/{uuid}/git`, which the ingest CodeBuild reads. Scoped
        // to that per-project secret path only (the `-*` suffix matches the
        // random suffix Secrets Manager appends to ARNs).
        actions: ["secretsmanager:CreateSecret", "secretsmanager:PutSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*/git-*`,
        ],
      }),
    );

    const projectApi = new apigw.RestApi(this, "ProjectApi", {
      restApiName: `${config.projectName}-project-api`,
      defaultCorsPreflightOptions: {
        allowOrigins: apigw.Cors.ALL_ORIGINS,
        allowMethods: apigw.Cors.ALL_METHODS,
      },
    });

    const projectApiAuthorizer = new apigw.CognitoUserPoolsAuthorizer(
      this,
      "ProjectApiAuthorizer",
      { cognitoUserPools: [props.auth.userPool] },
    );

    const projectApiIntegration = new apigw.LambdaIntegration(projectApiFn);
    const projectApiAuthOptions: apigw.MethodOptions = {
      authorizer: projectApiAuthorizer,
      authorizationType: apigw.AuthorizationType.COGNITO,
    };

    // Collection: /projects (POST create, GET list)
    const projectApiProjects = projectApi.root.addResource("projects");
    projectApiProjects.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );
    projectApiProjects.addMethod(
      "GET",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item: /projects/{projectId} (GET read, DELETE)
    const projectApiProjectId = projectApiProjects.addResource("{projectId}");
    projectApiProjectId.addMethod(
      "GET",
      projectApiIntegration,
      projectApiAuthOptions,
    );
    projectApiProjectId.addMethod(
      "DELETE",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resources: transition, reassign, upload-url (all POST)
    const projectApiTransition = projectApiProjectId.addResource("transition");
    projectApiTransition.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    const projectApiReassign = projectApiProjectId.addResource("reassign");
    projectApiReassign.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    const projectApiUploadUrl = projectApiProjectId.addResource("upload-url");
    projectApiUploadUrl.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    // Item sub-resource: link-repo (POST) — Git round-trip (C7). The developer
    // links the pushed GitHub/GitLab repo; the handler is owner-gated and
    // validates the URL against an allowlist.
    const projectApiLinkRepo = projectApiProjectId.addResource("link-repo");
    projectApiLinkRepo.addMethod(
      "POST",
      projectApiIntegration,
      projectApiAuthOptions,
    );

    this.projectApiUrl = projectApi.url;

    // -------------------------------------------------------
    // Git repository ingestion (C7 round-trip)
    // -------------------------------------------------------
    // A CodeBuild project clones the developer's linked repo and syncs it into
    // the project's `{slug}/source/` prefix, then invokes the Project API
    // Lambda's ingest-callback (direct Lambda invoke — no new network route) to
    // advance the project from implementation_submitted to assigned_to_qa.
    //
    // The Project API Lambda references the ingest project only by its literal
    // name + ARN (not a CDK object reference) to avoid a Lambda↔CodeBuild
    // circular dependency: the Lambda is the RestApi integration target, and the
    // build depends on the Lambda (invoke + function name). This mirrors the
    // string-ARN pattern used elsewhere in the stack to break cycles.
    const ingestProjectName = `${config.projectName}-repo-ingest`;

    const repoIngestBuild = new codebuild.Project(this, "RepoIngestBuild", {
      projectName: ingestProjectName,
      description:
        "Clones a linked Git repo and syncs it into the project source prefix (C7 ingestion)",
      // Fail fast rather than hang on a pathological repo.
      timeout: cdk.Duration.minutes(30),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        // MEDIUM gives more vCPU/RAM/disk than SMALL for larger working trees.
        computeType: codebuild.ComputeType.MEDIUM,
      },
      environmentVariables: {
        PROJECT_BUCKET: { value: this.projectBucket.bucketName },
        PROJECT_API_FUNCTION: { value: projectApiFn.functionName },
        // Ingest guardrail: a working tree over ~2 GB or 50k files fails fast
        // (reported as status=failed with a reason) instead of timing out on a
        // huge/monorepo checkout. Tune per environment.
        MAX_INGEST_BYTES: { value: "2147483648" },
        MAX_INGEST_FILES: { value: "50000" },
        // Per-build values (PROJECT_UUID / REPO_URL / REPO_BRANCH /
        // PROJECT_SLUG) are supplied as environmentVariablesOverride by
        // StartBuild in the Project API's link-repo handler.
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          build: {
            commands: [
              "set -e",
              "echo Ingesting repo for project $PROJECT_UUID",
              // Optional per-project git token for private repos (may be absent).
              "TOKEN=$(aws secretsmanager get-secret-value --secret-id aidlc/projects/$PROJECT_UUID/git --query SecretString --output text 2>/dev/null || true)",
              "CLONE_URL=$REPO_URL",
              'if [ -n "$TOKEN" ]; then CLONE_URL=$(echo "$REPO_URL" | sed -E "s#https://#https://$TOKEN@#"); fi',
              'git clone --depth 1 --branch "$REPO_BRANCH" "$CLONE_URL" repo',
              // Size guardrail: fail fast with a reason instead of timing out.
              "BYTES=$(du -sb repo 2>/dev/null | cut -f1 || echo 0)",
              "FILES=$(find repo -type f -not -path '*/.git/*' | wc -l | tr -d ' ')",
              'echo "Ingest working tree: $BYTES bytes, $FILES files (limits: $MAX_INGEST_BYTES bytes / $MAX_INGEST_FILES files)"',
              'if [ "$BYTES" -gt "$MAX_INGEST_BYTES" ] || [ "$FILES" -gt "$MAX_INGEST_FILES" ]; then echo "Repository exceeds the ingest size limit ($FILES files, $BYTES bytes)" > /tmp/ingest_reason.txt; exit 1; fi',
              "git -C repo rev-parse HEAD > /tmp/commit.txt",
              // Exclude VCS, dependency, build and vendored trees so only source
              // is synced (far fewer PUTs, smaller footprint).
              "aws s3 sync repo \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/source/\" --delete --exclude '*.git/*' --exclude '*node_modules/*' --exclude '*/.venv/*' --exclude '*/dist/*' --exclude '*/build/*' --exclude '*/target/*' --exclude '*/vendor/*' --exclude '*__pycache__/*'",
            ],
          },
          post_build: {
            commands: [
              // Runs whether or not the build phase succeeded; report the
              // outcome (and any guardrail reason) back to the Project API so
              // it can advance to QA (ready) or record a failure.
              'if [ "$CODEBUILD_BUILD_SUCCEEDING" = "1" ]; then export STATUS=ready; else export STATUS=failed; fi',
              "export COMMIT=$(cat /tmp/commit.txt 2>/dev/null || echo '')",
              "export REASON=$(cat /tmp/ingest_reason.txt 2>/dev/null || echo '')",
              "export PAYLOAD=$(python3 -c \"import json,os;print(json.dumps({'httpMethod':'POST','resource':'/projects/{projectId}/ingest-callback','pathParameters':{'projectId':os.environ['PROJECT_UUID']},'body':json.dumps({'status':os.environ['STATUS'],'commit':os.environ.get('COMMIT',''),'reason':os.environ.get('REASON','')})}))\")",
              'aws lambda invoke --function-name "$PROJECT_API_FUNCTION" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" /tmp/callback-out.json',
              "cat /tmp/callback-out.json || true",
            ],
          },
        },
      }),
    });

    // CodeBuild role: sync into the project source prefix, read the optional
    // per-project git token, and invoke the Project API for the callback.
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:PutObject", "s3:DeleteObject"],
        resources: [`${this.projectBucket.bucketArn}/*/source/*`],
      }),
    );
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    repoIngestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${region}:${account}:secret:aidlc/projects/*/git-*`,
        ],
      }),
    );
    projectApiFn.grantInvoke(repoIngestBuild);

    // Let the Project API Lambda start the ingest build. Reference by literal
    // name + ARN (no CDK object reference) to avoid the circular dependency
    // described above.
    projectApiFn.addEnvironment("REPO_INGEST_CODEBUILD_PROJECT", ingestProjectName);
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${ingestProjectName}`,
        ],
      }),
    );

    // -------------------------------------------------------
    // Git push webhook (C7 auto-ingest) — PUBLIC endpoint, authenticated by a
    // shared-secret signature (GitHub HMAC / GitLab token), NOT Cognito. On a
    // verified push it maps repo->project and re-triggers the ingest build.
    // -------------------------------------------------------
    const gitWebhookSecret = new secretsmanager.Secret(this, "GitWebhookSecret", {
      secretName: "aidlc/git-webhook/secret",
      description:
        "Shared secret for verifying GitHub/GitLab push webhooks (configure it on the repo's webhook).",
      generateSecretString: {
        excludePunctuation: true,
        passwordLength: 40,
      },
    });

    const gitWebhookFn = new lambda_.Function(this, "GitWebhookLambda", {
      functionName: `${config.projectName}-git-webhook`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "git_webhook.handler",
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      code: lambda_.Code.fromAsset(path.join(__dirname, "..", "custom-resources"), {
        bundling: undefined,
      }),
      environment: {
        PROJECT_BUCKET: this.projectBucket.bucketName,
        GIT_WEBHOOK_SECRET_ARN: gitWebhookSecret.secretArn,
        // Literal name (no ref) — the webhook reuses project_api._maybe_trigger_ingest.
        REPO_INGEST_CODEBUILD_PROJECT: ingestProjectName,
      },
    });
    gitWebhookSecret.grantRead(gitWebhookFn);
    // List/read project records to match the pushed repo to a project.
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`${this.projectBucket.bucketArn}/_platform/projects/*`],
      }),
    );
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    gitWebhookFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${ingestProjectName}`,
        ],
      }),
    );

    // Public POST /git-webhook on a DEDICATED RestApi (keeps the Project API
    // fully Cognito-gated — this endpoint is authenticated by the HMAC/token
    // signature the handler verifies before any access, not by Cognito).
    const gitWebhookApi = new apigw.RestApi(this, "GitWebhookApi", {
      restApiName: `${config.projectName}-git-webhook`,
      description:
        "Public Git push webhook (signature-verified) for C7 auto-ingest.",
    });
    gitWebhookApi.root
      .addResource("git-webhook")
      .addMethod("POST", new apigw.LambdaIntegration(gitWebhookFn), {
        authorizationType: apigw.AuthorizationType.NONE,
      });

    // -------------------------------------------------------
    // QA test-runner (C7 real test execution)
    // -------------------------------------------------------
    // Pulls the ingested source from S3, runs the auto-detected test suite,
    // writes aidlc-docs/qa/test-results.md, and calls back with pass/fail via
    // direct Lambda invoke. Same literal-name pattern to avoid a cycle.
    const testProjectName = `${config.projectName}-repo-test`;
    const repoTestBuild = new codebuild.Project(this, "RepoTestBuild", {
      projectName: testProjectName,
      description:
        "Runs the ingested repo's test suite and reports pass/fail to the Project API (C7 QA)",
      timeout: cdk.Duration.minutes(30),
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        computeType: codebuild.ComputeType.MEDIUM,
      },
      environmentVariables: {
        PROJECT_BUCKET: { value: this.projectBucket.bucketName },
        PROJECT_API_FUNCTION: { value: projectApiFn.functionName },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          install: {
            // Match the runtime most modern Node repos declare (engines.node
            // >= 20). CodeBuild STANDARD_7_0 defaults to Node 18, where the
            // global Web Crypto (`crypto.randomUUID()`) is not exposed, so
            // handlers relying on it throw at runtime and tests spuriously
            // fail. Node 20 provides global `crypto` and satisfies `>=20`.
            "runtime-versions": { nodejs: 20 },
          },
          build: {
            commands: [
              "echo Running QA tests for $PROJECT_UUID",
              "aws s3 sync \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/source/\" src || true",
              "cd src",
              "TEST_RC=0",
              // Best-effort auto-detect: npm test, else pytest, else no-op.
              "if [ -f package.json ]; then npm ci > /tmp/test.log 2>&1 && npm test >> /tmp/test.log 2>&1; TEST_RC=$?; elif [ -f requirements.txt ] || [ -f pyproject.toml ]; then python3 -m pip install -q pytest > /tmp/test.log 2>&1 || true; [ -f requirements.txt ] && python3 -m pip install -q -r requirements.txt >> /tmp/test.log 2>&1 || true; python3 -m pytest >> /tmp/test.log 2>&1; TEST_RC=$?; else echo 'No recognized test suite found.' > /tmp/test.log; TEST_RC=0; fi",
              "echo $TEST_RC > /tmp/test_rc",
              "cd ..",
            ],
          },
          post_build: {
            commands: [
              "TEST_RC=$(cat /tmp/test_rc 2>/dev/null || echo 1)",
              'if [ "$TEST_RC" = "0" ]; then export STATUS=passed; else export STATUS=failed; fi',
              "python3 -c \"import os;rc=(open('/tmp/test_rc').read().strip() if os.path.exists('/tmp/test_rc') else '1');log=(open('/tmp/test.log').read() if os.path.exists('/tmp/test.log') else '');open('/tmp/test-results.md','w').write('# Test Results\\n\\nExit code: '+rc+'\\n\\n'+chr(96)*3+'\\n'+log[-4000:]+'\\n'+chr(96)*3+'\\n')\"",
              "aws s3 cp /tmp/test-results.md \"s3://$PROJECT_BUCKET/$PROJECT_SLUG/aidlc-docs/qa/test-results.md\" || true",
              "export SUMMARY=$(head -c 400 /tmp/test.log 2>/dev/null | tr '\\n' ' ' || echo '')",
              "export PAYLOAD=$(python3 -c \"import json,os;print(json.dumps({'httpMethod':'POST','resource':'/projects/{projectId}/test-callback','pathParameters':{'projectId':os.environ['PROJECT_UUID']},'body':json.dumps({'status':os.environ['STATUS'],'summary':os.environ.get('SUMMARY','')})}))\")",
              'aws lambda invoke --function-name "$PROJECT_API_FUNCTION" --cli-binary-format raw-in-base64-out --payload "$PAYLOAD" /tmp/test-callback-out.json',
              "cat /tmp/test-callback-out.json || true",
            ],
          },
        },
      }),
    });
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject"],
        resources: [`${this.projectBucket.bucketArn}/*/source/*`],
      }),
    );
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:PutObject"],
        resources: [`${this.projectBucket.bucketArn}/*/aidlc-docs/qa/*`],
      }),
    );
    repoTestBuild.role?.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:ListBucket"],
        resources: [this.projectBucket.bucketArn],
      }),
    );
    projectApiFn.grantInvoke(repoTestBuild);

    // Project API can start the test build (literal name/ARN to avoid a cycle).
    projectApiFn.addEnvironment("REPO_TEST_CODEBUILD_PROJECT", testProjectName);
    projectApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["codebuild:StartBuild"],
        resources: [
          `arn:aws:codebuild:${region}:${account}:project/${testProjectName}`,
        ],
      }),
    );

    // QA-owner-gated route to start the test-runner.
    const projectApiRunTests = projectApiProjectId.addResource("run-tests");
    projectApiRunTests.addMethod("POST", projectApiIntegration, projectApiAuthOptions);
    // -------------------------------------------------------
    const prefix = config.parameterStorePrefix;

    const ssmBedrockModelId = new ssm.StringParameter(
      this,
      "SsmBedrockModelId",
      {
        parameterName: `${prefix}bedrock/model-id`,
        stringValue: config.runtime.bedrockModelId,
        description: "Bedrock foundation model ID",
      },
    );

    const ssmMemoryId = new ssm.StringParameter(this, "SsmMemoryId", {
      parameterName: `${prefix}memory/id`,
      stringValue: this.memory.memoryId,
      description: "AgentCore Memory resource ID",
    });

    const ssmGatewayEndpoint = new ssm.StringParameter(
      this,
      "SsmGatewayEndpoint",
      {
        parameterName: `${prefix}gateway/endpoint`,
        stringValue: this.gateway.gatewayUrl!,
        description: "AgentCore Gateway MCP endpoint URL",
      },
    );

    const ssmGatewayScope = new ssm.StringParameter(this, "SsmGatewayScope", {
      parameterName: `${prefix}gateway/scope`,
      stringValue: "agentcore/gateway",
      description: "OAuth scope for Gateway access",
    });

    this.ssmParameters = [
      ssmBedrockModelId,
      ssmMemoryId,
      ssmGatewayEndpoint,
      ssmGatewayScope,
    ];

    // -------------------------------------------------------
    // AgentCore Runtime (containerized Strands agent)
    // -------------------------------------------------------
    const networkConfiguration =
      agentcore.RuntimeNetworkConfiguration.usingPublicNetwork();

    this.runtime = new agentcore.Runtime(this, "Runtime", {
      runtimeName: config.runtime.name,
      description: config.runtime.description,
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromImageUri(
        `${props.build.agentEcrRepo.repositoryUri}:${props.build.imageTag}`,
      ),
      authorizerConfiguration:
        agentcore.RuntimeAuthorizerConfiguration.usingCognito(
          props.auth.userPool,
          [props.auth.userPoolClient, props.auth.pkceClient],
        ),
      networkConfiguration,
      environmentVariables: {
        GATEWAY_MCP_URL: this.gateway.gatewayUrl!,
        GATEWAY_CLIENT_ID: props.auth.userPoolClient.userPoolClientId,
        GATEWAY_CLIENT_SECRET_ARN: props.auth.clientSecret.secretArn,
        GATEWAY_TOKEN_ENDPOINT: props.auth.cognitoTokenEndpoint,
        GATEWAY_SCOPE: "agentcore/gateway",
        AGENTCORE_MEMORY_ID: this.memory.memoryId,
        BEDROCK_MODEL_ID: config.runtime.bedrockModelId,
        SYSTEM_PROMPT_BUCKET: props.assetsBucket.bucket.bucketName,
        SYSTEM_PROMPT_PREFIX: this.systemPromptPrefix,
        AWS_REGION: region,
        PROJECT_BUCKET: this.projectBucket.bucketName,
      },
    });

    // Grant ECR pull permissions to the runtime's execution role
    props.build.agentEcrRepo.grantPull(this.runtime);

    // Ensure the agent Docker image is built before the Runtime is created
    this.runtime.node.addDependency(props.build.agentBuildCustomResource);

    // Also grant ECR authorization token permission
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ecr:GetAuthorizationToken"],
        resources: ["*"],
      }),
    );

    // Grant permissions to the Runtime
    // Bedrock model invoke
    const modelId = config.runtime.bedrockModelId;
    const isInferenceProfile =
      modelId.startsWith("us.") ||
      modelId.startsWith("eu.") ||
      modelId.startsWith("ap.") ||
      modelId.startsWith("global.");
    const bedrockModelArn = isInferenceProfile
      ? `arn:aws:bedrock:${region}:${account}:inference-profile/${modelId}`
      : `arn:aws:bedrock:${region}::foundation-model/${modelId}`;
    const bedrockResources = isInferenceProfile
      ? [
          bedrockModelArn,
          // Cross-region inference profiles also need access to the underlying foundation model
          `arn:aws:bedrock:*::foundation-model/${modelId.replace(/^(us|eu|ap|global)\./, "")}`,
        ]
      : [bedrockModelArn];

    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: bedrockResources,
      }),
    );

    // Secrets Manager read (Cognito client secret)
    props.auth.clientSecret.grantRead(this.runtime);

    // S3 read for system prompts
    this.projectBucket.grantRead(this.runtime, `${this.systemPromptPrefix}/*`);

    // S3 read/write for session persistence (graph-level S3SessionManager)
    this.projectBucket.grantReadWrite(this.runtime, `*/sessions/*`);

    // Memory access
    this.memory.grantFullAccess(this.runtime);

    // SSM read for all parameters
    this.runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ],
        resources: [`arn:aws:ssm:${region}:${account}:parameter${prefix}*`],
      }),
    );

    // -------------------------------------------------------
    // Runtime ARN SSM Parameter
    // -------------------------------------------------------
    const ssmRuntimeArn = new ssm.StringParameter(this, "SsmRuntimeArn", {
      parameterName: `${prefix}runtime/arn`,
      stringValue: this.runtime.agentRuntimeArn,
      description: "AgentCore Runtime ARN",
    });

    this.ssmParameters.push(ssmRuntimeArn);

    // -------------------------------------------------------
    // Observability: Transaction Search + Log Delivery
    // -------------------------------------------------------

    /**
     * Configure AgentCore observability:
     * 1. Enable CloudWatch Transaction Search (account-level, one-time)
     * 2. Create log groups and log delivery for Runtime (APPLICATION_LOGS + USAGE_LOGS)
     * 3. Create log groups and log delivery for Gateway (APPLICATION_LOGS)
     * 4. Create log groups and log delivery for Memory (APPLICATION_LOGS)
     *
     * Log delivery uses the CloudWatch Logs vended-logs API:
     *   PutDeliverySource → PutDeliveryDestination → CreateDelivery
     */

    // -------------------------------------------------------
    // 1. Enable Transaction Search (idempotent Lambda)
    //
    // The X-Ray updateTraceSegmentDestination API errors when
    // the destination is already set. This Lambda checks the
    // current state first and only mutates when needed, making
    // it safe for both first deploy and subsequent updates.
    // -------------------------------------------------------
    const txnSearchFn = new lambda_.Function(this, "TxnSearchFn", {
      functionName: `${config.projectName}-txn-search-setup`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "txn_search_setup.handler",
      timeout: cdk.Duration.seconds(60),
      code: lambda_.Code.fromAsset(
        path.join(__dirname, "..", "custom-resources"),
        { bundling: undefined },
      ),
    });

    txnSearchFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "xray:UpdateTraceSegmentDestination",
          "xray:GetTraceSegmentDestination",
          "xray:UpdateIndexingRule",
          "xray:GetIndexingRules",
          "logs:PutResourcePolicy",
          "logs:DeleteResourcePolicy",
          "application-signals:StartDiscovery",
        ],
        resources: ["*"],
      }),
    );

    const txnSearchProvider = new cr.Provider(this, "TxnSearchProvider", {
      onEventHandler: txnSearchFn,
    });

    new cdk.CustomResource(this, "TxnSearchSetup", {
      serviceToken: txnSearchProvider.serviceToken,
      properties: {
        Region: region,
        AccountId: account,
        SamplingPercentage: config.observability.samplingPercentage,
      },
    });

    // -------------------------------------------------------
    // 2. Runtime Log Delivery (APPLICATION_LOGS + USAGE_LOGS)
    // -------------------------------------------------------
    const runtimeLogGroupApp = new logs.LogGroup(this, "RuntimeAppLogGroup", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/${config.runtime.name}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const runtimeLogGroupUsage = new logs.LogGroup(
      this,
      "RuntimeUsageLogGroup",
      {
        logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/USAGE_LOGS/${config.runtime.name}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      },
    );

    this.createLogDelivery("RuntimeApp", {
      resourceArn: this.runtime.agentRuntimeArn,
      logType: "APPLICATION_LOGS",
      logGroup: runtimeLogGroupApp,
    });

    this.createLogDelivery("RuntimeUsage", {
      resourceArn: this.runtime.agentRuntimeArn,
      logType: "USAGE_LOGS",
      logGroup: runtimeLogGroupUsage,
    });

    // -------------------------------------------------------
    // 3. Gateway Log Delivery (APPLICATION_LOGS)
    // -------------------------------------------------------
    const gatewayLogGroup = new logs.LogGroup(this, "GatewayAppLogGroup", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/${config.gateway.name}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.createLogDelivery("GatewayApp", {
      resourceArn: this.gateway.gatewayArn,
      logType: "APPLICATION_LOGS",
      logGroup: gatewayLogGroup,
    });

    // -------------------------------------------------------
    // 4. Memory Log Delivery (APPLICATION_LOGS)
    // -------------------------------------------------------
    const memoryLogGroup = new logs.LogGroup(this, "MemoryAppLogGroup", {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/memory/APPLICATION_LOGS/${config.memory.name}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.createLogDelivery("MemoryApp", {
      resourceArn: this.memory.memoryArn,
      logType: "APPLICATION_LOGS",
      logGroup: memoryLogGroup,
    });

    // -------------------------------------------------------
    // 5. Bedrock Model Invocation Logging (CloudWatch + S3)
    //
    // PutModelInvocationLoggingConfiguration is idempotent (PUT).
    // The log group and S3 bucket may already exist from a prior
    // deploy, so the Lambda creates them only if missing.
    // -------------------------------------------------------
    const bedrockLogGroupName = "bedrock_model_invocation_loggrp";
    const bedrockS3BucketName = `bedrock-invoc-log-overflow-${account}-${region}`;
    const bedrockLogGroupArn = `arn:aws:logs:${region}:${account}:log-group:${bedrockLogGroupName}`;

    // S3 bucket for large data overflow (RETAIN so it survives stack deletion)
    const bedrockLogBucket = new s3.Bucket(this, "BedrockInvocationLogBucket", {
      bucketName: bedrockS3BucketName,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // S3 bucket policy allowing Bedrock to write logs
    bedrockLogBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AmazonBedrockLogsWrite",
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal("bedrock.amazonaws.com")],
        actions: ["s3:PutObject"],
        resources: [
          `${bedrockLogBucket.bucketArn}/AWSLogs/${account}/BedrockModelInvocationLogs/*`,
        ],
        conditions: {
          StringEquals: { "aws:SourceAccount": account },
          ArnLike: {
            "aws:SourceArn": `arn:aws:bedrock:${region}:${account}:*`,
          },
        },
      }),
    );

    // IAM role for Bedrock to write to CloudWatch Logs
    const bedrockLoggingRole = new iam.Role(
      this,
      "BedrockInvocationLoggingRole",
      {
        assumedBy: new iam.ServicePrincipal("bedrock.amazonaws.com", {
          conditions: {
            StringEquals: { "aws:SourceAccount": account },
            ArnLike: {
              "aws:SourceArn": `arn:aws:bedrock:${region}:${account}:*`,
            },
          },
        }),
        inlinePolicies: {
          BedrockLogging: new iam.PolicyDocument({
            statements: [
              new iam.PolicyStatement({
                actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
                resources: [
                  `${bedrockLogGroupArn}:log-stream:aws/bedrock/modelinvocations`,
                ],
              }),
            ],
          }),
        },
      },
    );

    const bedrockLoggingFn = new lambda_.Function(this, "BedrockLoggingFn", {
      functionName: `${config.projectName}-bedrock-logging-setup`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "bedrock_logging_setup.handler",
      timeout: cdk.Duration.seconds(60),
      code: lambda_.Code.fromAsset(
        path.join(__dirname, "..", "custom-resources"),
        { bundling: undefined },
      ),
    });

    bedrockLoggingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:PutModelInvocationLoggingConfiguration",
          "bedrock:GetModelInvocationLoggingConfiguration",
        ],
        resources: ["*"],
      }),
    );
    bedrockLoggingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["logs:CreateLogGroup", "logs:PutRetentionPolicy"],
        resources: [bedrockLogGroupArn, `${bedrockLogGroupArn}:*`],
      }),
    );
    bedrockLoggingFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["iam:PassRole"],
        resources: [bedrockLoggingRole.roleArn],
        conditions: {
          StringEquals: { "iam:PassedToService": "bedrock.amazonaws.com" },
        },
      }),
    );

    const bedrockLoggingProvider = new cr.Provider(
      this,
      "BedrockLoggingProvider",
      {
        onEventHandler: bedrockLoggingFn,
      },
    );

    const bedrockLoggingCr = new cdk.CustomResource(
      this,
      "BedrockLoggingSetup",
      {
        serviceToken: bedrockLoggingProvider.serviceToken,
        properties: {
          Region: region,
          LogGroupName: bedrockLogGroupName,
          S3BucketName: bedrockS3BucketName,
          RoleArn: bedrockLoggingRole.roleArn,
          RetentionDays: 30,
        },
      },
    );

    bedrockLoggingCr.node.addDependency(bedrockLogBucket);
    bedrockLoggingCr.node.addDependency(bedrockLoggingRole);
  }

  /**
   * Create a vended-log delivery pipeline using a Lambda-backed
   * custom resource that handles the full lifecycle idempotently:
   *   PutDeliverySource → PutDeliveryDestination → CreateDelivery
   *
   * On delete: DeleteDelivery → DeleteDeliveryDestination → DeleteDeliverySource
   *
   * The Lambda checks existing state before mutating, so repeated
   * deploys are safe.
   */
  private createLogDelivery(
    id: string,
    opts: {
      resourceArn: string;
      logType: string;
      logGroup: logs.ILogGroup;
    },
  ): void {
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    const sourceName = `${id}-source`;
    const destName = `${id}-dest`;

    const fn = new lambda_.Function(this, `${id}Fn`, {
      functionName: `${cdk.Names.uniqueResourceName(this, { maxLength: 40, separator: "-" }).toLowerCase()}-${id.toLowerCase()}`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "log_delivery.handler",
      timeout: cdk.Duration.seconds(120),
      code: lambda_.Code.fromAsset(
        path.join(__dirname, "..", "custom-resources"),
        { bundling: undefined },
      ),
    });

    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "logs:PutDeliverySource",
          "logs:DeleteDeliverySource",
          "logs:PutDeliveryDestination",
          "logs:DeleteDeliveryDestination",
          "logs:CreateDelivery",
          "logs:DeleteDelivery",
          "logs:GetDelivery",
          "logs:DescribeDeliveries",
          "logs:DescribeDeliverySources",
          "logs:DescribeDeliveryDestinations",
        ],
        resources: ["*"],
      }),
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:AllowVendedLogDeliveryForResource"],
        resources: ["*"],
      }),
    );

    // V2 vended log delivery requires the caller to have resource policy
    // permissions on the destination log group so AWS can auto-create the
    // policy allowing delivery.logs.amazonaws.com to write log streams.
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ],
        resources: [`arn:aws:logs:${region}:${account}:*`],
      }),
    );

    const provider = new cr.Provider(this, `${id}Provider`, {
      onEventHandler: fn,
    });

    new cdk.CustomResource(this, `${id}CR`, {
      serviceToken: provider.serviceToken,
      properties: {
        SourceName: sourceName,
        DestName: destName,
        LogType: opts.logType,
        ResourceArn: opts.resourceArn,
        LogGroupArn: opts.logGroup.logGroupArn,
        Region: cdk.Stack.of(this).region,
      },
    });
  }
}
