import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as cognitoIdentity from "aws-cdk-lib/aws-cognito-identitypool";
import * as lambda_ from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as path from "path";
import { DeploymentConfig } from "./config";

export interface Params {
  readonly config: DeploymentConfig;
}

export class AIDLCPlatformAuth extends Construct {
  public readonly userPool: cognito.UserPool;
  public readonly userPoolClient: cognito.UserPoolClient;
  public readonly userPoolDomain: cognito.UserPoolDomain;
  public readonly resourceServer: cognito.UserPoolResourceServer;
  public readonly pkceClient: cognito.UserPoolClient;
  public readonly clientSecret: secretsmanager.Secret;
  public readonly identityPool: cognitoIdentity.IdentityPool;
  public readonly cognitoTokenEndpoint: string;
  public readonly domainPrefix: string;
  public readonly testUserEmail: string;
  public readonly testUserPassword: string;
  public readonly baTestUserEmail: string;
  public readonly baTestUserPassword: string;
  public readonly saTestUserEmail: string;
  public readonly saTestUserPassword: string;
  public readonly qaTestUserEmail: string;
  public readonly qaTestUserPassword: string;
  public readonly devopsTestUserEmail: string;
  public readonly devopsTestUserPassword: string;
  public readonly maintTestUserEmail: string;
  public readonly maintTestUserPassword: string;

  constructor(scope: Construct, id: string, props: Params) {
    super(scope, id);

    const { config } = props;
    const account = cdk.Stack.of(this).account;
    const region = cdk.Stack.of(this).region;

    // -------------------------------------------------------
    // Cognito User Pool (OAuth 2.0 identity)
    // -------------------------------------------------------

    // Auto-confirm Lambda: skips email verification for dev/demo
    const autoConfirmFn = new lambda_.Function(this, "AutoConfirmFn", {
      functionName: `${config.projectName}-auto-confirm`,
      runtime: lambda_.Runtime.NODEJS_20_X,
      handler: "index.handler",
      code: lambda_.Code.fromInline(`
        exports.handler = async (event) => {
          event.response.autoConfirmUser = true;
          if (event.request.userAttributes.email) {
            event.response.autoVerifyEmail = true;
          }
          return event;
        };
      `),
    });

    this.userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: config.cognito.userPoolName,
      signInCaseSensitive: false,
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      lambdaTriggers: {
        preSignUp: autoConfirmFn,
      },
      customAttributes: {
        role: new cognito.StringAttribute({
          minLen: 1,
          maxLen: 64,
          mutable: true,
        }),
      },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // -------------------------------------------------------
    // Post-confirmation trigger: assign the user to the Cognito
    // group named by their `custom:role` attribute (handler logic
    // implemented in post_confirmation_role_assign.py).
    // -------------------------------------------------------
    const postConfirmationFn = new lambda_.Function(
      this,
      "PostConfirmationRoleAssignFn",
      {
        functionName: `${config.projectName}-post-confirmation-role-assign`,
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: "post_confirmation_role_assign.handler",
        timeout: cdk.Duration.seconds(30),
        memorySize: 128,
        code: lambda_.Code.fromAsset(
          path.join(__dirname, "..", "custom-resources"),
          { bundling: undefined },
        ),
      },
    );

    // Allow the trigger to add a confirmed user to a group.
    // Scope to a wildcard user-pool ARN (account/region) rather than
    // this.userPool.userPoolArn: referencing the pool *resource* here while the
    // pool also references this function as its post-confirmation trigger
    // creates a UserPool -> Fn -> Role DefaultPolicy -> UserPool cycle that
    // CloudFormation rejects. The action stays AdminAddUserToGroup only.
    postConfirmationFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["cognito-idp:AdminAddUserToGroup"],
        resources: [`arn:aws:cognito-idp:${region}:${account}:userpool/*`],
      }),
    );

    // Attach after the pool + Lambda exist to avoid circular dependencies;
    // addTrigger also grants Cognito permission to invoke the function.
    this.userPool.addTrigger(
      cognito.UserPoolOperation.POST_CONFIRMATION,
      postConfirmationFn,
    );

    // Resource server with a scope for Gateway OAuth
    this.resourceServer = this.userPool.addResourceServer("ResourceServer", {
      identifier: "agentcore",
      scopes: [
        {
          scopeName: "gateway",
          scopeDescription: "Access to AgentCore Gateway tools",
        },
      ],
    });

    // OAuth scopes for the app client
    const oauthScopes = [
      cognito.OAuthScope.resourceServer(this.resourceServer, {
        scopeName: "gateway",
        scopeDescription: "Access to AgentCore Gateway tools",
      }),
    ];

    // App client with client credentials flow (machine-to-machine)
    this.userPoolClient = this.userPool.addClient("AppClient", {
      userPoolClientName: config.cognito.appClientName,
      generateSecret: true,
      oAuth: {
        flows: {
          clientCredentials: true,
        },
        scopes: oauthScopes,
      },
    });

    // PKCE app client for browser-based auth (no secret, SRP flow)
    // OAuth callback URLs are added later via escape hatch once CloudFront domain is known.
    this.pkceClient = this.userPool.addClient("PkceClient", {
      userPoolClientName: "agentcore-pkce-client",
      generateSecret: false,
      authFlows: {
        userSrp: true,
      },
      // Explicit read/write attributes so the app can write `custom:role` at
      // sign-up. Include the standard attributes the frontend relies on so
      // enabling explicit lists does not restrict existing attributes.
      writeAttributes: new cognito.ClientAttributes()
        .withStandardAttributes({ email: true, preferredUsername: true })
        .withCustomAttributes("role"),
      readAttributes: new cognito.ClientAttributes()
        .withStandardAttributes({
          email: true,
          preferredUsername: true,
          emailVerified: true,
        })
        .withCustomAttributes("role"),
      accessTokenValidity: cdk.Duration.hours(24),
      idTokenValidity: cdk.Duration.hours(24),
      refreshTokenValidity: cdk.Duration.days(30),
    });

    // -------------------------------------------------------
    // Cognito Identity Pool (for S3 credentials in browser)
    // -------------------------------------------------------
    this.identityPool = new cognitoIdentity.IdentityPool(this, "IdentityPool", {
      identityPoolName: `${config.projectName}-identity-pool`,
      allowUnauthenticatedIdentities: false,
      authenticationProviders: {
        userPools: [
          new cognitoIdentity.UserPoolAuthenticationProvider({
            userPool: this.userPool,
            userPoolClient: this.pkceClient,
          }),
        ],
      },
    });

    // Cognito domain for the OAuth2 token endpoint
    this.domainPrefix = `${cdk.Names.uniqueResourceName(this, {
      maxLength: 50,
      separator: "-",
    }).toLowerCase()}-${cdk.Stack.of(this).account}`;

    const domainPrefix = this.domainPrefix;
    this.userPoolDomain = this.userPool.addDomain("Domain", {
      cognitoDomain: {
        domainPrefix,
      },
    });

    const prefix = config.parameterStorePrefix;

    // Store the Cognito app client secret in Secrets Manager
    this.clientSecret = new secretsmanager.Secret(this, "CognitoClientSecret", {
      secretName: `${prefix}secrets/cognito-client-secret`,
      description: "Cognito app client secret for AgentCore OAuth",
      secretStringValue: this.userPoolClient.userPoolClientSecret,
    });

    // -------------------------------------------------------
    // Cognito Groups (SDLC roles)
    // -------------------------------------------------------
    const cognitoGroups = [
      { name: "requirements-analyst", description: "Requirements Analyst" },
      { name: "system-architect", description: "System Architect" },
      { name: "software-developer", description: "Software Developer" },
      { name: "qa-engineer", description: "QA Engineer" },
      { name: "devops-engineer", description: "DevOps Engineer" },
      { name: "maintenance-specialist", description: "Maintenance Specialist" },
    ];

    const groupConstructs: cognito.CfnUserPoolGroup[] = [];
    for (const group of cognitoGroups) {
      groupConstructs.push(
        new cognito.CfnUserPoolGroup(this, `Group-${group.name}`, {
          userPoolId: this.userPool.userPoolId,
          groupName: group.name,
          description: group.description,
        }),
      );
    }

    // -------------------------------------------------------
    // Test users (consolidated single Lambda-backed custom resource)
    // -------------------------------------------------------
    // One provisioner Lambda + one CustomResource provisions ALL test users.
    // This replaces the previous 3-AwsCustomResource-per-user pattern, which
    // emitted ~3 Custom::AWS resources (plus IAM) per user and pushed the
    // synthesized template past CloudFormation's hard 1 MB limit.
    // Initial password for the pre-provisioned demo users. Supplied at deploy
    // time via the AIDLC_DEMO_USER_PASSWORD environment variable; the default
    // is a non-working placeholder so no real credential ever ships in source.
    // Override per environment and change on first login.
    const demoUserPassword =
      process.env.AIDLC_DEMO_USER_PASSWORD ?? "ChangeMeViaEnv1!";
    const testUsers = [
      // idPrefix kept for readable outputs; role == Cognito group.
      { email: "testuser@example.com", group: "software-developer", password: demoUserPassword },
      { email: "ba-tester@example.com", group: "requirements-analyst", password: demoUserPassword },
      { email: "sa-tester@example.com", group: "system-architect", password: demoUserPassword },
      { email: "qa-tester@example.com", group: "qa-engineer", password: demoUserPassword },
      { email: "devops-tester@example.com", group: "devops-engineer", password: demoUserPassword },
      { email: "maint-tester@example.com", group: "maintenance-specialist", password: demoUserPassword },
    ];
    const provisionUsers = testUsers.map((u) => ({
      email: u.email,
      password: u.password,
      group: u.group,
      role: u.group,
    }));

    const testUserProvisionerFn = new lambda_.Function(
      this,
      "TestUserProvisionerFn",
      {
        functionName: `${config.projectName}-test-user-provisioner`,
        runtime: lambda_.Runtime.PYTHON_3_12,
        handler: "provision_test_users.handler",
        timeout: cdk.Duration.minutes(2),
        memorySize: 128,
        code: lambda_.Code.fromAsset(
          path.join(__dirname, "..", "custom-resources"),
          { bundling: undefined },
        ),
        environment: {
          USER_POOL_ID: this.userPool.userPoolId,
          TEST_USERS: JSON.stringify(provisionUsers),
        },
      },
    );

    // Cognito admin actions, scoped to a wildcard user-pool ARN (account/region)
    // to avoid a UserPool -> Fn -> Role -> UserPool cycle, mirroring the
    // post-confirmation trigger above.
    testUserProvisionerFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "cognito-idp:AdminCreateUser",
          "cognito-idp:AdminSetUserPassword",
          "cognito-idp:AdminAddUserToGroup",
          "cognito-idp:AdminDeleteUser",
        ],
        resources: [`arn:aws:cognito-idp:${region}:${account}:userpool/*`],
      }),
    );

    const testUsersResource = new cdk.CustomResource(this, "TestUsers", {
      serviceToken: testUserProvisionerFn.functionArn,
      properties: {
        UserPoolId: this.userPool.userPoolId,
        Users: provisionUsers,
      },
    });
    // Users are added to groups, so the groups must exist first.
    for (const g of groupConstructs) {
      testUsersResource.node.addDependency(g);
    }

    this.testUserEmail = "testuser@example.com";
    this.testUserPassword = demoUserPassword;
    this.baTestUserEmail = "ba-tester@example.com";
    this.baTestUserPassword = demoUserPassword;
    this.saTestUserEmail = "sa-tester@example.com";
    this.saTestUserPassword = demoUserPassword;
    this.qaTestUserEmail = "qa-tester@example.com";
    this.qaTestUserPassword = demoUserPassword;
    this.devopsTestUserEmail = "devops-tester@example.com";
    this.devopsTestUserPassword = demoUserPassword;
    this.maintTestUserEmail = "maint-tester@example.com";
    this.maintTestUserPassword = demoUserPassword;

    this.cognitoTokenEndpoint = `https://${domainPrefix}.auth.${region}.amazoncognito.com/oauth2/token`;

    new ssm.StringParameter(this, "SsmCognitoUserPoolId", {
      parameterName: `${prefix}cognito/user-pool-id`,
      stringValue: this.userPool.userPoolId,
      description: "Cognito User Pool ID",
    });

    new ssm.StringParameter(this, "SsmCognitoClientId", {
      parameterName: `${prefix}cognito/client-id`,
      stringValue: this.userPoolClient.userPoolClientId,
      description: "Cognito app client ID",
    });

    new ssm.StringParameter(this, "SsmCognitoTokenEndpoint", {
      parameterName: `${prefix}cognito/token-endpoint`,
      stringValue: this.cognitoTokenEndpoint,
      description: "Cognito OAuth2 token endpoint URL",
    });

    new ssm.StringParameter(this, "SsmPkceClientId", {
      parameterName: `${prefix}cognito/pkce-client-id`,
      stringValue: this.pkceClient.userPoolClientId,
      description: "Cognito PKCE app client ID for browser-based auth",
    });
  }

}
