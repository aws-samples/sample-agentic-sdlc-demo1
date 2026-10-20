import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

import * as path from "path";
import * as fs from "fs";
import * as codebuild from "aws-cdk-lib/aws-codebuild";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda_ from "aws-cdk-lib/aws-lambda";
import { getDockerImageTag, getBuildTimestamp } from "./utils/deployment-utils";
import { AssetsBucket } from "./aidlc-assetbucket";

export interface Params {
  assetsBucket: AssetsBucket;
}

export class AIDLCPlatformBuild extends Construct {
  public readonly agentEcrRepo: ecr.Repository;
  public readonly diagramEcrRepo: ecr.Repository;
  public readonly agentBuildCustomResource: cdk.CustomResource;
  public readonly diagramBuildCustomResource: cdk.CustomResource;
  public readonly imageTag: string;

  constructor(scope: Construct, id: string, props: Params) {
    super(scope, id);

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // Custom resource to trigger the build
    // Use timestamp to force rebuild on every deployment
    const buildTimestamp = getBuildTimestamp();

    // Generate timestamp-based image tag for versioning
    const imageTag = getDockerImageTag();
    this.imageTag = imageTag;

    // Create CodeBuild role
    const codeBuildRole = new iam.Role(this, "CodeBuildRole", {
      assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "AmazonEC2ContainerRegistryPowerUser",
        ),
        iam.ManagedPolicy.fromAwsManagedPolicyName("CloudWatchLogsFullAccess"),
        iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonS3FullAccess"),
      ],
    });

    /**
     * BUILD OF AWS AGENT
     */

    // Create ECR repository for Agent image
    this.agentEcrRepo = new ecr.Repository(this, "AgentRepo", {
      repositoryName: "icode-agent",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
    });

    // CodeBuild project for Agent
    const agentBuild = new codebuild.Project(this, "AgentBuild", {
      projectName: "icode-agent-builder",
      description: "Builds iCode agent container (ARM64)",
      environment: {
        buildImage: codebuild.LinuxBuildImage.STANDARD_7_0,
        privileged: true,
        computeType: codebuild.ComputeType.LARGE,
      },
      environmentVariables: {
        AWS_DEFAULT_REGION: { value: region },
        AWS_ACCOUNT_ID: { value: account },
        IMAGE_REPO_NAME: { value: this.agentEcrRepo.repositoryName },
        IMAGE_TAG: { value: imageTag },
        SOURCE_BUCKET: { value: props.assetsBucket.bucket.bucketName },
        SOURCE_PREFIX: {
          value: props.assetsBucket.getS3Key("icode-agent.zip"),
        },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: "0.2",
        phases: {
          pre_build: {
            commands: [
              "echo Source already downloaded by CodeBuild",
              "ls -la",
              "echo Logging in to Amazon ECR...",
              "aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com",
              "REPOSITORY_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$IMAGE_REPO_NAME",
            ],
          },
          build: {
            commands: [
              "echo Build started on `date`",
              "echo Building the Docker image...",
              "docker buildx create --use --name multiarch-builder || true",
              "docker buildx build --platform linux/arm64 -t $REPOSITORY_URI:$IMAGE_TAG --push .",
            ],
          },
          post_build: {
            commands: [
              "echo Build completed on `date`",
              "echo Image URI: $REPOSITORY_URI:$IMAGE_TAG",
            ],
          },
        },
      }),
      role: codeBuildRole,
      source: codebuild.Source.s3({
        bucket: props.assetsBucket.bucket,
        path: props.assetsBucket.getS3Key("icode-agent.zip"),
      }),
    });

    const buildAgentFunctionCode = fs.readFileSync(
      path.join(__dirname, "..", "custom-resources", "build.py"),
      "utf8",
    );

    // Custom Lambda to trigger Agent build and wait for completion
    const buildAgentFunctionLambda = new lambda_.Function(
      this,
      "BuildAgentFunctionLambda",
      {
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: "build.handler",
        timeout: cdk.Duration.minutes(15),
        memorySize: 256,
        code: lambda_.Code.fromAsset(
          path.join(__dirname, "..", "custom-resources"),
          { bundling: undefined },
        ),
      },
    );

    buildAgentFunctionLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "codebuild:StartBuild",
          "codebuild:BatchGetBuilds",
          "codebuild:StopBuild",
        ],
        resources: [agentBuild.projectArn],
      }),
    );

    const buildAgentFunctionLambdaCustom = new cdk.CustomResource(
      this,
      "BuildAgentFunctionLambdaCustom",
      {
        serviceToken: buildAgentFunctionLambda.functionArn,
        properties: {
          ProjectName: agentBuild.projectName,
          BuildTimestamp: buildTimestamp, // Forces rebuild on every deployment
        },
      },
    );
    this.agentBuildCustomResource = buildAgentFunctionLambdaCustom;

    /**
     * BUILD OF AWS DIAGRAM TOOLS
     */

    // Create ECR repository for Docker image
    this.diagramEcrRepo = new ecr.Repository(this, "DiagramEcrRepo", {
      repositoryName: "aidlc-platform-diagram-awslabs",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
    });

    // Grant Lambda SERVICE permission to pull images (required for function creation)
    this.diagramEcrRepo.addToResourcePolicy(
      new iam.PolicyStatement({
        principals: [new iam.ServicePrincipal("lambda.amazonaws.com")],
        actions: ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
        conditions: {
          StringLike: {
            "aws:sourceArn": `arn:aws:lambda:${region}:${account}:function:aidlc-platform-diagram-awslabs`,
          },
        },
      }),
    );

    // Create CodeBuild project to build Docker image
    const lambdaBuild = new codebuild.Project(
      this,
      "IcodeDiagramDockerBuildProject",
      {
        projectName: "aidlc-platform-diagram-awslabs",
        source: codebuild.Source.s3({
          bucket: props.assetsBucket.bucket,
          path: props.assetsBucket.getS3Key("lambda-diagram-tools.zip"),
        }),
        environment: {
          buildImage: codebuild.LinuxBuildImage.STANDARD_5_0,
          privileged: true,
          environmentVariables: {
            AWS_DEFAULT_REGION: { value: region },
            AWS_ACCOUNT_ID: { value: account },
            IMAGE_REPO_NAME: { value: this.diagramEcrRepo.repositoryName },
          },
        },
        role: codeBuildRole,
      },
    );

    const buildLambdaFunctionCode = fs.readFileSync(
      path.join(__dirname, "..", "custom-resources", "build.py"),
      "utf8",
    );

    // Custom Lambda to trigger Agent build and wait for completion
    const buildDiagramFunctionLambda = new lambda_.Function(
      this,
      "BuildDiagramFunctionLambda",
      {
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: "build.handler",
        timeout: cdk.Duration.minutes(15),
        memorySize: 256,
        code: lambda_.Code.fromAsset(
          path.join(__dirname, "..", "custom-resources"),
          { bundling: undefined },
        ),
      },
    );

    buildDiagramFunctionLambda.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "codebuild:StartBuild",
          "codebuild:BatchGetBuilds",
          "codebuild:StopBuild",
        ],
        resources: [lambdaBuild.projectArn],
      }),
    );

    const buildDiagramFunctionLambdaCustom = new cdk.CustomResource(
      this,
      "BuildDiagramFunctionLambdaCustom",
      {
        serviceToken: buildDiagramFunctionLambda.functionArn,
        properties: {
          ProjectName: lambdaBuild.projectName,
          BuildTimestamp: buildTimestamp, // Forces rebuild on every deployment
        },
      },
    );
    this.diagramBuildCustomResource = buildDiagramFunctionLambdaCustom;
  }
}
