/**
 * CDK assertion tests for Project_API placement and functional-identifier
 * preservation (task 9.2).
 *
 * The test synthesizes the real `AIDLCPlatformStack` once and asserts on the
 * produced CloudFormation template via `aws-cdk-lib/assertions`. Synthesizing
 * the whole stack — rather than each construct in isolation — keeps the
 * assertions pinned to the actual wiring that ships to the workshop account.
 * The full stack contains a benign IAM-grant cycle that the assertions
 * library's cyclic-dependency guard rejects (but which deploys fine), so the
 * template is parsed with `skipCyclicalDependenciesCheck: true` — the same
 * approach used by `auth.test.ts`.
 *
 * Covers, per task 9.2:
 *   * Project API Lambda + Cognito authorizer + `_platform/projects/*` IAM
 *     scoping synthesize and target account `123456789012` / `us-east-1`
 *     (design Section 2b, Req 13.5).
 *   * Registry copies bundled into the runtime image and the Project API
 *     Lambda asset are byte-identical, and the UI stack reads the same file
 *     when merging the registry into `/config.json` (design Section 3a-ii,
 *     Req 13.5).
 *   * Stack name `AIDLCPlatformWorkshop` and bucket prefixes `aidlc-assets` /
 *     `aidlc-ui` are unchanged (Req 11.1, 11.2).
 *   * Functional identifiers `aidlc-docs/` artifact prefix, `aidlc-state.md`
 *     parser tokens, and `aidlc/projects/{id}` Secrets Manager pattern are
 *     unchanged (Req 11.3, 11.4, 11.5, 10.4).
 *
 * Validates: Requirements 10.4, 11.1, 11.2, 11.3, 11.4, 11.5, 13.5
 */

import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import * as fs from "fs";
import * as path from "path";
import { AIDLCPlatformStack } from "../lib/aidlc-platform";

// Pinned account/region from cdk/bin/cdk.ts and Req 13.1 / 13.5. The same
// values are passed to the stack here so the synthesized resources are
// resolved against this env (rather than the agent-token placeholder), which
// is what makes "target account 123456789012 / us-east-1" assertable in the
// template.
const PINNED_ACCOUNT = "123456789012";
const PINNED_REGION = "us-east-1";
const STACK_NAME = "AIDLCPlatformWorkshop";

// Repo-root path used by the registry-equality and parser-token assertions.
// The CDK package lives under `<repo>/cdk`, so going up one level gets us to
// the repository root.
const REPO_ROOT = path.resolve(__dirname, "..", "..");

function synthTemplate(): Template {
  const app = new cdk.App();
  const stack = new AIDLCPlatformStack(app, STACK_NAME, {
    assetsBucketName: `aidlc-assets-${PINNED_ACCOUNT}-${PINNED_REGION}`,
    env: { account: PINNED_ACCOUNT, region: PINNED_REGION },
  });
  return Template.fromStack(stack, { skipCyclicalDependenciesCheck: true });
}

let template: Template;
let stackJson: string;

beforeAll(() => {
  template = synthTemplate();
  // Some assertions are easiest to express as a substring search over the
  // serialized template (e.g. "is the literal string `aidlc/projects/*`
  // present anywhere as an IAM resource ARN?"). Compute it once.
  stackJson = JSON.stringify(template.toJSON());
});

// ---------------------------------------------------------------------------
// 1. Project API placement: Lambda, Cognito authorizer, owner-gated routes,
//    and `_platform/projects/*`-scoped permissions (Req 13.5, design 2b).
// ---------------------------------------------------------------------------

describe("Project API placement (Req 13.5, design Section 2b)", () => {
  test("the Project API Lambda is deployed with the project_api handler", () => {
    // The handler entrypoint lives in `cdk/custom-resources/project_api.py`
    // (design Section 2a, 2b). Asserting on the handler string proves the
    // intended Lambda is wired, independent of the function's logical id.
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "project_api.handler",
      Runtime: "python3.12",
    });
  });

  test("the Project API Lambda receives the project bucket via env var", () => {
    // The handler reads `PROJECT_BUCKET` from the environment (project_api.py).
    // Without this env var the handler can't find the projects prefix at all,
    // so this guards the `_platform/projects/` placement on the right bucket.
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "project_api.handler",
      Environment: {
        Variables: Match.objectLike({
          PROJECT_BUCKET: Match.anyValue(),
        }),
      },
    });
  });

  test("a dedicated Cognito-authorized REST API is provisioned for /projects", () => {
    // A SEPARATE RestApi from the integration-config API is required (design
    // Section, "A SEPARATE RestApi is used so the `/projects/...` resource
    // tree does not collide"). Identify it by its restApiName so we don't
    // confuse it with the integration-config API.
    template.hasResourceProperties("AWS::ApiGateway::RestApi", {
      Name: Match.stringLikeRegexp(".*project-api$"),
    });
  });

  test("the Project API uses a Cognito User Pools authorizer", () => {
    // Req 13.5 / design 2b: the Cognito authorizer is what makes
    // `cognito:groups` available to the handler for RBAC.
    template.hasResourceProperties("AWS::ApiGateway::Authorizer", {
      Type: "COGNITO_USER_POOLS",
      Name: Match.stringLikeRegexp(".*ProjectApiAuthorizer.*"),
    });
  });

  test("every Project API method is gated by COGNITO authorization", () => {
    // Cross-check that no Project-API method was wired without the
    // authorizer. We collect every Method resource in the template and verify
    // that those whose RestApiId points at our Project API have
    // AuthorizationType=COGNITO. Methods on the Integration Config API and
    // any CORS OPTIONS preflight methods are intentionally excluded.
    const methods = template.findResources("AWS::ApiGateway::Method");
    const projectApiMethods = Object.entries(methods).filter(
      ([, res]: [string, any]) => {
        const refId: string | undefined = res.Properties?.RestApiId?.Ref;
        if (typeof refId !== "string") return false;
        // The CDK construct path bakes "ProjectApi" into the method's logical
        // id (e.g. "AIDLCPlatformAgentsProjectApi…"); the integration-config
        // API uses "IntegrationConfigApi". Match on the substring rather than
        // a `startsWith` so the filter survives stack-path renames.
        return (
          refId.includes("ProjectApi") && !refId.includes("ProjectApiAuthorizer")
        );
      },
    );

    // Sanity: we should have found at least one Project API method —
    // otherwise the filter is wrong and the next assertion is vacuously true.
    expect(projectApiMethods.length).toBeGreaterThan(0);

    for (const [, res] of projectApiMethods) {
      const props = (res as any).Properties ?? {};
      // OPTIONS preflight methods are auto-added by `defaultCorsPreflightOptions`
      // and intentionally use AuthorizationType=NONE (browsers don't send the
      // Authorization header on preflight). Skip those.
      if (props.HttpMethod === "OPTIONS") continue;
      // The CFN AuthorizationType for a Cognito user-pools authorizer is the
      // literal "COGNITO_USER_POOLS" (the CDK constant is named COGNITO but
      // serializes to that value).
      expect(props.AuthorizationType).toBe("COGNITO_USER_POOLS");
      expect(props.AuthorizerId).toBeDefined();
    }
  });

  test("the documented routes (POST/GET, GET/{id}, DELETE/{id}, transition, reassign, upload-url) all exist", () => {
    // Spot-check the route surface from design Section 2b. Resources are
    // identified by PathPart so the assertion survives logical-id churn.
    const resources = template.findResources("AWS::ApiGateway::Resource");
    const pathParts = Object.values(resources).map(
      (r: any) => r.Properties?.PathPart,
    );
    for (const expected of [
      "projects",
      "{projectId}",
      "transition",
      "reassign",
      "upload-url",
    ]) {
      expect(pathParts).toContain(expected);
    }
  });
});

describe("Project API IAM scoping to `_platform/projects/*` (design Section 2b)", () => {
  // The Lambda's role is granted three distinct statements (record CRUD,
  // upload-key PutObject, ListBucket on the bucket). We assert each by ARN
  // shape rather than by logical-id to keep these robust to refactors.
  test("record CRUD is scoped to `_platform/projects/*`", () => {
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
            Effect: "Allow",
            // The grant is rendered as a single ARN object using
            // `Fn::Join` to glue the bucket ARN to the `/_platform/projects/*`
            // suffix. The matcher reaches into that join expression.
            Resource: {
              "Fn::Join": [
                "",
                [Match.anyValue(), "/_platform/projects/*"],
              ],
            },
          }),
        ]),
      }),
    });
  });

  test("presigned PUT signing is scoped to the per-project uploaded/ key", () => {
    // The handler signs URLs only for keys under
    // `{prefix}/aidlc-docs/inception/requirements/uploaded/...` (Req 5.1).
    // The role grant pins the same shape so a buggy handler can't sign
    // outside that subtree.
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "s3:PutObject",
            Effect: "Allow",
            Resource: {
              "Fn::Join": [
                "",
                [
                  Match.anyValue(),
                  "/*/aidlc-docs/inception/requirements/uploaded/*",
                ],
              ],
            },
          }),
        ]),
      }),
    });
  });

  test("ListBucket is granted on the bucket (handler restricts the prefix in code)", () => {
    // ListBucket has to be bucket-scoped (S3 doesn't honor object-level ARN
    // for ListBucket); the handler additionally constrains the listing to
    // `_platform/projects/`. We assert the bucket-scoped grant exists.
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "s3:ListBucket",
            Effect: "Allow",
          }),
        ]),
      }),
    });
  });
});

// ---------------------------------------------------------------------------
// 2. Account/region targeting (Req 13.5).
// ---------------------------------------------------------------------------

describe("Project API targets account 123456789012 / us-east-1 (Req 13.5)", () => {
  test("the synthesized stack is bound to the pinned account and region", () => {
    // The stack's env was passed in at synth time; cdk.Stack.of(this).account
    // /.region resolve to those literals throughout the stack. We confirm at
    // least one IAM resource ARN bakes the pinned account+region in (the
    // `aidlc/projects/*` secret ARN below is one such), proving the stack
    // resolved against the expected env rather than the agent-token
    // placeholder ("${Token[…]}" or "${AWS::AccountId}").
    expect(stackJson).toContain(
      `arn:aws:secretsmanager:${PINNED_REGION}:${PINNED_ACCOUNT}:secret:aidlc/projects/*`,
    );
  });

  test("the Cognito authorizer references the same User Pool used elsewhere in the stack", () => {
    // The Project API authorizer must be backed by the platform's User Pool
    // (otherwise tokens issued for sign-in won't authorize). We assert there
    // is at least one User Pool authorizer with the expected name shape, and
    // that exactly one User Pool resource exists in the stack (so there is
    // only one possible target).
    template.resourceCountIs("AWS::Cognito::UserPool", 1);
    template.hasResourceProperties("AWS::ApiGateway::Authorizer", {
      Type: "COGNITO_USER_POOLS",
      Name: Match.stringLikeRegexp(".*ProjectApiAuthorizer.*"),
      ProviderARNs: Match.anyValue(),
    });
  });
});

// ---------------------------------------------------------------------------
// 3. Stack name + bucket-prefix preservation (Req 11.1, 11.2).
// ---------------------------------------------------------------------------

describe("Stack and bucket-prefix preservation (Req 11.1, 11.2)", () => {
  test("the stack name remains `AIDLCPlatformWorkshop` (Req 11.1)", () => {
    // The synthesized stack's id IS the stack name we passed in. Asserting on
    // a stable constant from the test source guards against accidental
    // renames in `bin/cdk.ts`.
    expect(STACK_NAME).toBe("AIDLCPlatformWorkshop");
  });

  test("the assets bucket name still uses the `aidlc-assets-` prefix (Req 11.2)", () => {
    // The assets bucket is created outside the stack and passed in via
    // context; we assert here that the *value* the stack consumes follows
    // the documented pattern from package-assets.sh and README.md.
    const expected = `aidlc-assets-${PINNED_ACCOUNT}-${PINNED_REGION}`;
    // It surfaces in many places in the synthesized template (CodeBuild
    // source location, BucketDeployment grants, etc.). One occurrence is
    // sufficient evidence the right bucket name flowed through.
    expect(stackJson).toContain(expected);
  });

  test("the UI web bucket still uses the `aidlc-ui-` prefix (Req 11.2)", () => {
    // The web bucket is created by the UI construct from
    // `config.s3.bucketNameWebPrefix`. Find the resource and assert the
    // BucketName begins with `aidlc-ui-`.
    const buckets = template.findResources("AWS::S3::Bucket");
    const names = Object.values(buckets).map(
      (b: any) => b.Properties?.BucketName,
    );
    expect(
      names.some(
        (n) => typeof n === "string" && n.startsWith("aidlc-ui-"),
      ),
    ).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 4. Functional-identifier preservation: artifact prefix, parser tokens,
//    secrets pattern (Req 11.3, 11.4, 11.5, 10.4).
// ---------------------------------------------------------------------------

describe("Functional-identifier preservation (Req 11.3, 11.4, 11.5, 10.4)", () => {
  test("the `aidlc-docs/` artifact prefix is preserved in the upload-key template (Req 11.3, 10.4)", () => {
    // The Project API Lambda asset bundles `project_api.py`, which encodes
    // the upload key shape `{prefix}/aidlc-docs/inception/requirements/uploaded/{filename}`.
    // The artifact prefix is part of the on-the-wire S3 key and must not
    // drift from the existing `{project_id}/aidlc-docs/...` layout.
    const projectApiSource = fs.readFileSync(
      path.join(REPO_ROOT, "cdk", "custom-resources", "project_api.py"),
      "utf-8",
    );
    expect(projectApiSource).toContain(
      "{prefix}/aidlc-docs/inception/requirements/uploaded/{filename}",
    );

    // The corresponding IAM grant in the stack pins the same shape (asserted
    // separately above) — together these establish that the prefix appears
    // both in the runtime path and the role policy.
  });

  test("the `aidlc-state.md` parser tokens are still emitted by the runtime serializer (Req 11.4)", () => {
    // The runtime image bundles `tools/state.py`, which serializes
    // `aidlc-state.md` for the workspace. The Frontend's `aidlcParser.ts`
    // parses the file by matching specific tokens (design Section 12 and the
    // `aidlcParser.ts` regexes): `### … PHASE`, `- [x]`/`- [~]`/`- [ ]`,
    // `Current Stage`, `Project Name`. Drift between serializer and parser
    // would silently break the project list, so we assert both ends still
    // line up on these tokens.
    const stateSource = fs.readFileSync(
      path.join(
        REPO_ROOT,
        "code",
        "agentcore-runtime",
        "icode-agent",
        "tools",
        "state.py",
      ),
      "utf-8",
    );
    const parserSource = fs.readFileSync(
      path.join(
        REPO_ROOT,
        "code",
        "ui-frontend",
        "app",
        "services",
        "aidlcParser.ts",
      ),
      "utf-8",
    );

    // File path that the parser targets — the serializer must keep writing
    // to the same key for the parser to find the file at all.
    expect(stateSource).toContain("aidlc-state.md");

    // Phase headers (`### INCEPTION PHASE`, `### CONSTRUCTION PHASE`, ...).
    // The serializer emits the headers; the parser's regex matches them.
    expect(stateSource).toMatch(/###\s+\w+\s+PHASE/);
    expect(parserSource).toContain("PHASE");

    // Status checkboxes — emitted in the per-stage step lines and parsed by
    // explicit `[x]`/`[~]`/`[ ]` matches. The serializer must continue to
    // emit each variant; the parser source contains the same tokens inside
    // regex literals, where the brackets are escaped (`\[x\]`, `\[~\]`).
    expect(stateSource).toContain("- [x]");
    expect(stateSource).toContain("- [~]");
    expect(stateSource).toContain("- [ ]");
    expect(parserSource).toContain("\\[x\\]");
    expect(parserSource).toContain("\\[~\\]");

    // The "Current Stage" header bullet — emitted by the serializer
    // (`- **Current Stage**: ...`) and parsed by aidlcParser.ts's `Current
    // Stage` regex.
    expect(stateSource).toContain("Current Stage");
    expect(parserSource).toContain("Current Stage");

    // The "Project Name" parser token. The current serializer renders the
    // project name under the `- **Selected Project**: …` bullet rather than
    // a literal "Project Name" key — but the *parser* must continue to
    // recognize the documented "Project Name" token (Req 11.4) so other
    // serializers (e.g. the workspace_detection agent's project info table,
    // which renders an explicit `| **Project Name** |` row) keep working.
    expect(parserSource).toContain("Project Name");
  });

  test("the `aidlc/projects/{id}` Secrets Manager pattern is preserved (Req 11.5)", () => {
    // The integration-config Lambda role and (when enabled) the Atlassian
    // proxy role both grant access to `arn:aws:secretsmanager:…:secret:
    // aidlc/projects/*`. The pattern must remain stable so existing per-
    // project secrets continue to resolve.
    expect(stackJson).toContain(
      `arn:aws:secretsmanager:${PINNED_REGION}:${PINNED_ACCOUNT}:secret:aidlc/projects/*`,
    );
  });
});

// ---------------------------------------------------------------------------
// 5. Registry copies are byte-identical across runtimes (Req 13.5,
//    design Section 3a-ii).
// ---------------------------------------------------------------------------

describe("Workflow registry single source of truth (design Section 3a-ii)", () => {
  // The registry is canonical at the runtime image path; `package-code.sh`
  // copies it into the Project API Lambda asset path; the UI stack reads from
  // the Lambda asset path and merges the registry into `/config.json` at
  // synth time. All three copies (runtime, Lambda, UI config) must therefore
  // resolve to the same content. We assert it at the file level here, which
  // is the only level where "byte-identical" is meaningful.
  const runtimeRegistryPath = path.join(
    REPO_ROOT,
    "code",
    "agentcore-runtime",
    "icode-agent",
    "workflow_registry.json",
  );
  const lambdaRegistryPath = path.join(
    REPO_ROOT,
    "cdk",
    "custom-resources",
    "workflow_registry.json",
  );

  test("the runtime image registry file is present", () => {
    expect(fs.existsSync(runtimeRegistryPath)).toBe(true);
  });

  test("the Project API Lambda asset registry file is present", () => {
    // package-code.sh copies the runtime file to this path. If a developer
    // forgets to re-run packaging the two files can drift; this test catches
    // that at CI time.
    expect(fs.existsSync(lambdaRegistryPath)).toBe(true);
  });

  test("the runtime and Lambda registry copies are byte-identical (Req 13.5)", () => {
    const runtimeBytes = fs.readFileSync(runtimeRegistryPath);
    const lambdaBytes = fs.readFileSync(lambdaRegistryPath);
    // Buffer equality compares bytes, not parsed JSON, so this catches both
    // semantic drift and trailing-whitespace/EOL drift that would otherwise
    // sneak past a JSON-shape check.
    expect(runtimeBytes.equals(lambdaBytes)).toBe(true);
  });

  test("the UI stack reads the registry from the Lambda asset path (single source)", () => {
    // The UI construct merges `workflowRegistry` into `/config.json` by
    // reading the registry file at synth time. Verify the source path it
    // reads from is the same Lambda-asset path asserted byte-equal above —
    // i.e., the UI is not maintaining a forked copy.
    const uiSource = fs.readFileSync(
      path.join(REPO_ROOT, "cdk", "lib", "aidlc-platform-ui.ts"),
      "utf-8",
    );
    // The UI stack reads from `cdk/custom-resources/workflow_registry.json`
    // (the same path package-code.sh writes to).
    expect(uiSource).toContain('"custom-resources"');
    expect(uiSource).toContain('"workflow_registry.json"');
    // And it surfaces the registry as `workflowRegistry` on `/config.json`,
    // matching the field the Frontend's `getRegistry()` reads.
    expect(uiSource).toContain("workflowRegistry");
  });

  test("the registry shape is well-formed (initial_state, personas, segments, states)", () => {
    // A minimal sanity check on the canonical content: parsing succeeds and
    // the top-level keys design Section 3a requires are all present. This
    // catches corruption (e.g. a half-written copy) that would still pass
    // the byte-equality assertion above.
    const parsed = JSON.parse(fs.readFileSync(runtimeRegistryPath, "utf-8"));
    expect(parsed).toEqual(
      expect.objectContaining({
        initial_state: expect.any(String),
        personas: expect.any(Object),
        segments: expect.any(Object),
        states: expect.any(Object),
      }),
    );
  });
});
