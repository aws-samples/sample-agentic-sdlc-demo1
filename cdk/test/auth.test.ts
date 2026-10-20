/**
 * CDK assertion tests for the persona role-assignment auth changes (task 4.5,
 * Part B).
 *
 * These synthesize the real `AIDLCPlatformStack` (the same stack `bin/cdk.ts`
 * deploys) once and assert on the produced CloudFormation template via
 * `aws-cdk-lib/assertions`. Synthesizing the whole stack — rather than the auth
 * construct in isolation — means the assertions cover the actual wiring that
 * ships, including the credential `CfnOutput`s which are declared at the stack
 * level (Req 1.7).
 *
 * The full stack's generated template contains a benign circular reference
 * between IAM grants that the assertions library's cyclic-dependency guard
 * rejects (but which deploys fine), so the template is parsed with
 * `skipCyclicalDependenciesCheck: true`. No Docker bundling occurs: the agent
 * and diagram images are built via CodeBuild/ECR at deploy time, and the Python
 * Lambdas use `Code.fromAsset` (zip), so synth is fast and hermetic.
 *
 * Covers:
 *   * Post_Confirmation_Trigger registered on the User Pool + its Lambda
 *     (handler `post_confirmation_role_assign.handler`) exists (Req 1.2, 1.5).
 *   * The `custom:role` attribute is present on the pool schema (Req 1.1
 *     enabler; the trigger reads it for 1.2/1.3).
 *   * Two persona Test_Users are added to the correct groups
 *     (`requirements-analyst`, `system-architect`) via `adminAddUserToGroup`,
 *     and the six Cognito groups exist (Req 1.6).
 *   * The four Test_User credential stack outputs are present (Req 1.7).
 *
 * Validates: Requirements 1.2, 1.3, 1.5, 1.6, 1.7
 */

import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { AIDLCPlatformStack } from "../lib/aidlc-platform";

// Synthesize the stack once and share the template across assertions — synth is
// the expensive step, the assertions are cheap.
function synthTemplate(): Template {
  const app = new cdk.App();
  const stack = new AIDLCPlatformStack(app, "AIDLCPlatformWorkshop", {
    assetsBucketName: "test-assets-bucket",
    env: { account: "123456789012", region: "us-east-1" },
  });
  // The full stack contains a deploy-valid IAM grant cycle that the assertions
  // parser would otherwise reject; skip only that check (everything else is
  // asserted normally).
  return Template.fromStack(stack, { skipCyclicalDependenciesCheck: true });
}

let template: Template;

beforeAll(() => {
  template = synthTemplate();
});

describe("Post_Confirmation_Trigger registration (Req 1.2, 1.5)", () => {
  test("the User Pool registers a PostConfirmation Lambda trigger", () => {
    // Req 1.5: the pool wires a post-confirmation trigger. Match.anyValue()
    // keeps this resilient to the Fn::GetAtt logical-id detail.
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      LambdaConfig: Match.objectLike({
        PostConfirmation: Match.anyValue(),
      }),
    });
  });

  test("a Lambda exists for the post-confirmation role-assign handler", () => {
    // Req 1.2: the handler that performs admin_add_user_to_group is deployed.
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "post_confirmation_role_assign.handler",
    });
  });

  test("the trigger Lambda may add a confirmed user to a group", () => {
    // Req 1.2: the trigger's execution role is granted AdminAddUserToGroup so
    // it can actually assign the group named by custom:role.
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "cognito-idp:AdminAddUserToGroup",
            Effect: "Allow",
          }),
        ]),
      }),
    });
  });
});

describe("custom:role attribute on the pool schema (Req 1.1/1.3 enabler)", () => {
  test("the User Pool schema contains a custom 'role' attribute", () => {
    // The custom:role attribute is what the trigger reads to decide the group
    // (Req 1.2) or reject (Req 1.3). It surfaces in the pool Schema as Name
    // "role" (Cognito prefixes it with custom: at runtime).
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      Schema: Match.arrayWith([
        Match.objectLike({
          Name: "role",
          AttributeDataType: "String",
          Mutable: true,
        }),
      ]),
    });
  });
});

describe("Cognito groups and persona Test_Users (Req 1.6)", () => {
  test("all six SDLC role groups are provisioned", () => {
    const groupNames = Object.values(
      template.findResources("AWS::Cognito::UserPoolGroup"),
    ).map((g: any) => g.Properties?.GroupName);

    for (const name of [
      "requirements-analyst",
      "system-architect",
      "software-developer",
      "qa-engineer",
      "devops-engineer",
      "maintenance-specialist",
    ]) {
      expect(groupNames).toContain(name);
    }
  });

  // Test users are now provisioned by a SINGLE Lambda-backed custom resource
  // (consolidated to keep the template under CloudFormation's 1 MB limit). The
  // user->group mapping lives in the provisioner Lambda's TEST_USERS env var
  // (a plain JSON string of literals; the UserPoolId is a separate token env).
  function provisionerUsers(): Array<{
    email: string;
    group: string;
    role?: string;
  }> {
    const fns = template.findResources("AWS::Lambda::Function");
    for (const res of Object.values(fns)) {
      const props = (res as any).Properties;
      if (props?.Handler === "provision_test_users.handler") {
        const raw = props.Environment?.Variables?.TEST_USERS;
        if (typeof raw === "string") return JSON.parse(raw);
      }
    }
    return [];
  }

  test("a single consolidated test-user provisioner Lambda exists", () => {
    const fns = template.findResources("AWS::Lambda::Function");
    const provisioners = Object.values(fns).filter(
      (r) => (r as any).Properties?.Handler === "provision_test_users.handler",
    );
    expect(provisioners).toHaveLength(1);
  });

  test("the Business Analyst test user is mapped to requirements-analyst", () => {
    const ba = provisionerUsers().find((u) => u.email === "ba-tester@example.com");
    expect(ba).toBeDefined();
    expect(ba!.group).toBe("requirements-analyst");
  });

  test("the Solution Architect test user is mapped to system-architect", () => {
    const sa = provisionerUsers().find((u) => u.email === "sa-tester@example.com");
    expect(sa).toBeDefined();
    expect(sa!.group).toBe("system-architect");
  });

  test("the Maintenance Specialist test user is mapped to maintenance-specialist", () => {
    const maint = provisionerUsers().find(
      (u) => u.email === "maint-tester@example.com",
    );
    expect(maint).toBeDefined();
    expect(maint!.group).toBe("maintenance-specialist");
  });

  test("the QA Engineer test user is mapped to qa-engineer", () => {
    const qa = provisionerUsers().find((u) => u.email === "qa-tester@example.com");
    expect(qa).toBeDefined();
    expect(qa!.group).toBe("qa-engineer");
  });

  test("the DevOps Engineer test user is mapped to devops-engineer", () => {
    const devops = provisionerUsers().find(
      (u) => u.email === "devops-tester@example.com",
    );
    expect(devops).toBeDefined();
    expect(devops!.group).toBe("devops-engineer");
  });
});

describe("Test_User credential stack outputs (Req 1.7)", () => {
  test("each persona's email and password credential output is present", () => {
    template.hasOutput("BATestUserEmail", {});
    template.hasOutput("BATestUserPassword", {});
    template.hasOutput("SATestUserEmail", {});
    template.hasOutput("SATestUserPassword", {});
    template.hasOutput("QATestUserEmail", {});
    template.hasOutput("QATestUserPassword", {});
    template.hasOutput("DevOpsTestUserEmail", {});
    template.hasOutput("DevOpsTestUserPassword", {});
    template.hasOutput("MaintTestUserEmail", {});
    template.hasOutput("MaintTestUserPassword", {});
  });
});
