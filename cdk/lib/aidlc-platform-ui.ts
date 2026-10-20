import * as cdk from "aws-cdk-lib";
import * as path from "path";
import * as fs from "fs";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import { Construct } from "constructs";
import * as cr from "aws-cdk-lib/custom-resources";
import * as lambda_ from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3deploy from "aws-cdk-lib/aws-s3-deployment";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { AIDLCPlatformAuth } from "./aidlc-platform-auth";
import { DeploymentConfig } from "./config";
import { AssetsBucket } from "./aidlc-assetbucket";
import { AIDLCPlatformAgents } from "./aidlc-platform-agents";
import { getForceRedeployTimestamp } from "./utils/deployment-utils";

export interface Params {
  assetsBucket: AssetsBucket;
  projectBucket: s3.Bucket;
  auth: AIDLCPlatformAuth;
  agent: AIDLCPlatformAgents;
  readonly config: DeploymentConfig;
  ideUrl?: string;
}

export class AIDLCPlatformUI extends Construct {
  public readonly cloudFrontUrl: string;

  constructor(scope: Construct, id: string, props: Params) {
    super(scope, id);

    const { config } = props;

    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    const removalPolicy =
      config.s3.removalPolicy === "DESTROY"
        ? cdk.RemovalPolicy.DESTROY
        : cdk.RemovalPolicy.RETAIN;

    const webBucket = new s3.Bucket(this, "WebBucket", {
      bucketName: `${config.s3.bucketNameWebPrefix}-${account}-${region}`,
      removalPolicy,
      autoDeleteObjects: config.s3.removalPolicy === "DESTROY",
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
    });

    // -------------------------------------------------------
    // CloudFront Distribution for SPA hosting
    // -------------------------------------------------------

    // Origin Access Control for S3
    const oac = new cloudfront.S3OriginAccessControl(this, "SpaOac", {
      signing: cloudfront.Signing.SIGV4_NO_OVERRIDE,
    });

    // CloudFront distribution serving SPA from /web prefix in filesBucket
    const spaDistribution = new cloudfront.Distribution(
      this,
      "SpaDistribution",
      {
        defaultBehavior: {
          origin: origins.S3BucketOrigin.withOriginAccessControl(webBucket, {
            originAccessControl: oac,
            originPath: "/web",
          }),
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        },
        defaultRootObject: "index.html",
        errorResponses: [
          {
            httpStatus: 404,
            responseHttpStatus: 200,
            responsePagePath: "/index.html",
            ttl: cdk.Duration.seconds(0),
          },
          {
            httpStatus: 403,
            responseHttpStatus: 200,
            responsePagePath: "/index.html",
            ttl: cdk.Duration.seconds(0),
          },
        ],
      },
    );

    // -------------------------------------------------------
    // Update PKCE client with CloudFront callback URLs
    // -------------------------------------------------------
    this.cloudFrontUrl = cdk.Fn.join("", [
      "https://",
      spaDistribution.distributionDomainName,
    ]);
    const cloudFrontUrlSlash = cdk.Fn.join("", [
      "https://",
      spaDistribution.distributionDomainName,
      "/",
    ]);

    // Use L1 escape hatch to add OAuth settings now that CloudFront domain is known
    new cr.AwsCustomResource(this, "UpdatePkceClientCallbacks", {
      onCreate: {
        service: "CognitoIdentityServiceProvider",
        action: "updateUserPoolClient",
        parameters: {
          UserPoolId: props.auth.userPool.userPoolId,
          ClientId: props.auth.pkceClient.userPoolClientId,
          AllowedOAuthFlows: ["code"],
          AllowedOAuthFlowsUserPoolClient: true,
          AllowedOAuthScopes: ["openid", "email", "profile"],
          CallbackURLs: [this.cloudFrontUrl, cloudFrontUrlSlash],
          LogoutURLs: [this.cloudFrontUrl, cloudFrontUrlSlash],
          SupportedIdentityProviders: ["COGNITO"],
        },
        physicalResourceId: cr.PhysicalResourceId.of("pkce-client-callbacks"),
      },
      onUpdate: {
        service: "CognitoIdentityServiceProvider",
        action: "updateUserPoolClient",
        parameters: {
          UserPoolId: props.auth.userPool.userPoolId,
          ClientId: props.auth.pkceClient.userPoolClientId,
          AllowedOAuthFlows: ["code"],
          AllowedOAuthFlowsUserPoolClient: true,
          AllowedOAuthScopes: ["openid", "email", "profile"],
          CallbackURLs: [this.cloudFrontUrl, cloudFrontUrlSlash],
          LogoutURLs: [this.cloudFrontUrl, cloudFrontUrlSlash],
          SupportedIdentityProviders: ["COGNITO"],
        },
        physicalResourceId: cr.PhysicalResourceId.of("pkce-client-callbacks"),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new cdk.aws_iam.PolicyStatement({
          actions: ["cognito-idp:UpdateUserPoolClient"],
          resources: [props.auth.userPool.userPoolArn],
        }),
      ]),
    });

    // -------------------------------------------------------
    // CORS on Files Bucket
    // -------------------------------------------------------
    // Presigned S3 URLs are fetched directly by the browser from a
    // different origin (CloudFront → S3), so CORS headers are required.
    webBucket.addCorsRule({
      allowedOrigins: ["*"],
      allowedMethods: [s3.HttpMethods.GET],
      allowedHeaders: ["*"],
      maxAge: 3600,
    });

    const prefix = config.parameterStorePrefix;

    new ssm.StringParameter(this, "SsmCloudFrontDomain", {
      parameterName: `${prefix}cloudfront/domain`,
      stringValue: spaDistribution.distributionDomainName,
      description: "CloudFront distribution domain for SPA",
    });

    // -------------------------------------------------------
    // SPA Deploy: unzip UI from assets bucket into filesBucket/web
    // -------------------------------------------------------
    const unzipFn = new lambda_.Function(this, "UnzipUiFn", {
      functionName: `${config.projectName}-unzip-ui`,
      runtime: lambda_.Runtime.PYTHON_3_12,
      handler: "index.handler",
      timeout: cdk.Duration.seconds(300),
      memorySize: 512,
      ephemeralStorageSize: cdk.Size.mebibytes(2048),
      code: lambda_.Code.fromInline(
        fs.readFileSync(
          path.join(__dirname, "..", "custom-resources", "unzip_ui.py"),
          "utf-8",
        ),
      ),
      environment: {
        FORCE_REDEPLOY: getForceRedeployTimestamp(),
      },
    });

    // Grant read on assets bucket; read+write+delete on web bucket
    // (the Update handler clears the /web prefix before re-extracting)
    props.assetsBucket.bucket.grantRead(unzipFn);
    webBucket.grantReadWrite(unzipFn);
    webBucket.grantDelete(unzipFn);

    const unzipProvider = new cr.Provider(this, "UnzipUiProvider", {
      onEventHandler: unzipFn,
    });

    const unzipCr = new cdk.CustomResource(this, "UnzipUiDeployment", {
      serviceToken: unzipProvider.serviceToken,
      properties: {
        SourceBucket: props.assetsBucket.bucket.bucketName,
        SourceKey: props.assetsBucket.getS3Key("ui-frontend.zip"),
        DestBucket: webBucket.bucketName,
        DestPrefix: "web",
        // Force CloudFormation to trigger an Update on every deploy.
        // The Lambda's FORCE_REDEPLOY env var changes, but CF only
        // re-invokes the Custom Resource when *properties* change.
        DeployTimestamp: getForceRedeployTimestamp(),
      },
    });

    // Generate runtime config.json with actual resource values.
    // Include deployTimestamp so the source hash changes on every deploy,
    // forcing BucketDeployment to re-upload config.json even when config
    // values haven't changed (the unzip Lambda clears /web on each update).
    //
    // The Persona/Workflow Registry is merged in here as a byte-identical copy
    // of `workflow_registry.json` (design Section 3a-iii) so the Frontend's
    // `getRegistry()` (reads `config.workflowRegistry`) has the same source of
    // truth as the Project API Lambda and the runtime image.
    const workflowRegistry = JSON.parse(
      fs.readFileSync(
        path.join(
          __dirname,
          "..",
          "custom-resources",
          "workflow_registry.json",
        ),
        "utf-8",
      ),
    );

    const runtimeConfig = s3deploy.Source.jsonData("config.json", {
      cognitoUserPoolId: props.auth.userPool.userPoolId,
      cognitoClientId: props.auth.pkceClient.userPoolClientId,
      cognitoIdentityPoolId: props.auth.identityPool.identityPoolId,
      cognitoDomain: `${props.auth.domainPrefix}.auth.${region}.amazoncognito.com`,
      awsRegion: region,
      runtimeArn: props.agent.runtime.agentRuntimeArn,
      filesBucket: props.projectBucket.bucketName,
      _deployTimestamp: getForceRedeployTimestamp(),
      integrationConfigApiUrl: props.agent.integrationConfigApiUrl,
      projectApiUrl: props.agent.projectApiUrl,
      workflowRegistry: workflowRegistry,
      ideUrl: props.ideUrl,
    });

    // Deploy config.json to S3 under /web prefix
    const configDeployment = new s3deploy.BucketDeployment(
      this,
      "SpaConfigDeployment",
      {
        sources: [runtimeConfig],
        destinationBucket: webBucket,
        destinationKeyPrefix: "web",
        prune: false,
        // config.json must always be revalidated so config changes (and the
        // embedded workflow registry) take effect immediately after a deploy.
        cacheControl: [s3deploy.CacheControl.fromString("no-cache")],
      },
    );

    // Ensure config.json is deployed after the zip is extracted
    configDeployment.node.addDependency(unzipCr);

    // isv-agents.json is bundled inside ui-frontend.zip (added by deploy.sh Step 2)
    // and extracted by the UnzipUiDeployment Lambda — no separate deployment needed.

    // -------------------------------------------------------
    // CloudFront cache invalidation after UI deployment
    // -------------------------------------------------------
    // Use a stable deploy timestamp so both onCreate and onUpdate
    // reference the same CallerReference within a single deploy.
    const deployTs = getForceRedeployTimestamp();
    const invalidationParams = {
      service: "CloudFront",
      action: "createInvalidation",
      parameters: {
        DistributionId: spaDistribution.distributionId,
        InvalidationBatch: {
          CallerReference: `deploy-${deployTs}`,
          Paths: {
            Quantity: 1,
            Items: ["/*"],
          },
        },
      },
      physicalResourceId: cr.PhysicalResourceId.of(
        `cf-invalidation-${deployTs}`,
      ),
    };

    const invalidation = new cr.AwsCustomResource(
      this,
      "CloudFrontInvalidation",
      {
        // Fire on both create (first deploy) and update (subsequent deploys)
        onCreate: invalidationParams,
        onUpdate: invalidationParams,
        policy: cr.AwsCustomResourcePolicy.fromStatements([
          new cdk.aws_iam.PolicyStatement({
            actions: ["cloudfront:CreateInvalidation"],
            resources: [
              `arn:aws:cloudfront::${account}:distribution/${spaDistribution.distributionId}`,
            ],
          }),
        ]),
      },
    );

    // Invalidate after both the UI zip extraction and config.json are deployed
    invalidation.node.addDependency(unzipCr);
    invalidation.node.addDependency(configDeployment);
  }
}
