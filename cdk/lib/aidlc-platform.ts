import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Construct } from "constructs";
import { getConfig } from "./config";
import { AssetsBucket } from "./aidlc-assetbucket";

import { AIDLCPlatformAuth } from "./aidlc-platform-auth";
import { AIDLCPlatformUI } from "./aidlc-platform-ui";
import { AIDLCPlatformAgents } from "./aidlc-platform-agents";
import { AIDLCPlatformBuild } from "./aidlc-platform-builds";

export interface CdkStackProps extends cdk.StackProps {
  /**
   * Name of the S3 bucket that holds the packaged deployment assets.
   * Created and populated by `scripts/package-assets.sh` before deploy.
   */
  assetsBucketName: string;
  /**
   * Optional URL to an external IDE (VSCode, Cloud9, etc.). When set,
   * the UI will render an "Open IDE" button that links to this URL.
   * Pass via CDK context: `-c ideUrl=https://your-ide.example.com`.
   */
  ideUrl?: string;
}

export interface LambdaFunctionInfo {
  function: lambda.Function;
  toolNames: string[];
}

export class AIDLCPlatformStack extends cdk.Stack {
  constructor(scope: Construct, id: string, prop: CdkStackProps) {
    super(scope, id, prop);

    const config = getConfig();

    const assetsBucket = new AssetsBucket(
      this,
      "AssetsBucket",
      prop.assetsBucketName,
    );

    const vpc = new ec2.Vpc(this, "ICodeVpc", {
      ipAddresses: ec2.IpAddresses.cidr("10.0.0.0/16"),
      vpcName: "aidlc-vpc",
      subnetConfiguration: [
        {
          subnetType: ec2.SubnetType.PUBLIC,
          name: "Public",
          mapPublicIpOnLaunch: true,
          cidrMask: 24,
        },
        {
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          name: "Private",
          cidrMask: 24,
        },
      ],
      maxAzs: 2,
      restrictDefaultSecurityGroup: false,
    });

    const build = new AIDLCPlatformBuild(this, "AIDLCPlatformBuild", {
      assetsBucket,
    });

    const auth = new AIDLCPlatformAuth(this, "AIDLCPlatformAuth", {
      config,
    });

    const agent = new AIDLCPlatformAgents(this, "AIDLCPlatformAgents", {
      config,
      vpc,
      auth,
      assetsBucket,
      build,
    });

    agent.node.addDependency(build);
    agent.node.addDependency(auth);

    // Grant Identity Pool authenticated users read access to project files bucket.
    // Use string-based ARN to avoid circular dependency between Auth and Agents.
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;
    const projectBucketName = `${config.s3.bucketNameAgentPrefix}-${account}-${region}`;
    auth.identityPool.authenticatedRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ["s3:GetObject", "s3:ListBucket"],
        resources: [
          `arn:aws:s3:::${projectBucketName}`,
          `arn:aws:s3:::${projectBucketName}/*`,
        ],
      }),
    );

    const ui = new AIDLCPlatformUI(this, "AIDLCPlatformUI", {
      projectBucket: agent.projectBucket,
      config,
      auth,
      agent,
      assetsBucket,
      ideUrl: prop.ideUrl,
    });

    ui.node.addDependency(agent);

    new cdk.CfnOutput(this, "AIDLCPlatformURL", {
      value: ui.cloudFrontUrl,
    });
    new cdk.CfnOutput(this, "TestUserEmail", {
      value: auth.testUserEmail,
    });
    new cdk.CfnOutput(this, "TestUserPassword", {
      value: auth.testUserPassword,
    });
    new cdk.CfnOutput(this, "BATestUserEmail", {
      value: auth.baTestUserEmail,
    });
    new cdk.CfnOutput(this, "BATestUserPassword", {
      value: auth.baTestUserPassword,
    });
    new cdk.CfnOutput(this, "SATestUserEmail", {
      value: auth.saTestUserEmail,
    });
    new cdk.CfnOutput(this, "SATestUserPassword", {
      value: auth.saTestUserPassword,
    });
    new cdk.CfnOutput(this, "QATestUserEmail", {
      value: auth.qaTestUserEmail,
    });
    new cdk.CfnOutput(this, "QATestUserPassword", {
      value: auth.qaTestUserPassword,
    });
    new cdk.CfnOutput(this, "DevOpsTestUserEmail", {
      value: auth.devopsTestUserEmail,
    });
    new cdk.CfnOutput(this, "DevOpsTestUserPassword", {
      value: auth.devopsTestUserPassword,
    });
    new cdk.CfnOutput(this, "MaintTestUserEmail", {
      value: auth.maintTestUserEmail,
    });
    new cdk.CfnOutput(this, "MaintTestUserPassword", {
      value: auth.maintTestUserPassword,
    });
  }
}
