/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1: the whole app gives its REST API a new deployment when the authorizer invoke role is renamed
 * (#1210), and when the API definition changes.
 *
 * `environments.aws.IamRoleNamePrefix` (infra/cdk.json) renames, and so replaces, every role of the
 * core stack. The API definition names the invoke role as `Fn::GetAtt <logical id>.Arn`, the same
 * before and after, so a deployment keyed on the definition alone stayed in place, and the stage kept
 * authorizer credentials naming the deleted role. That two synths of one configuration keep one
 * deployment logical id is pinned in test/security/hashedNamesAreDeterministic.test.ts, which already
 * runs two fresh synths in one process; the minimal-stack cases are in deploymentAuthorizerInvokeRole.
 */

import cdkJson from "../../cdk.json";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

// Three full-app synths; the default one is usually shared with other suites in the run.
jest.setTimeout(900_000);

const PREFIX = "vamsx-";

/** cdk.json's `environments` with only the role name prefix replaced. */
function environmentsWithPrefix(prefix: string): Record<string, unknown> {
    const environments = JSON.parse(JSON.stringify((cdkJson as any).context.environments));
    environments.aws.IamRoleNamePrefix = prefix;
    return { environments };
}

/** The REST API's one deployment, and the stage that serves it. */
function deploymentAndStage(s: SynthResult): { deployment: Resource; stage: Resource } {
    const deployments = s.ofType("AWS::ApiGateway::Deployment");
    const stages = s.ofType("AWS::ApiGateway::Stage");
    expect(deployments).toHaveLength(1);
    expect(stages).toHaveLength(1);
    return { deployment: deployments[0], stage: stages[0] };
}

function invokeRole(s: SynthResult): Resource {
    const roles = s.where("AWS::IAM::Role", (r) =>
        r.logicalId.includes("RestAuthorizerInvokeRole")
    );
    expect(roles).toHaveLength(1);
    return roles[0];
}

const restApiBody = (s: SynthResult) => {
    const apis = s.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    return apis[0].properties.Body;
};

describe("commercial template: the REST API deployment", () => {
    let base: SynthResult;
    let prefixed: SynthResult;
    let longerTimeout: SynthResult;

    beforeAll(() => {
        base = synthTemplate("commercial");
        prefixed = synthTemplate("commercial", { context: environmentsWithPrefix(PREFIX) });
        longerTimeout = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.api.apiGatewayRest.apiGatewayTimeoutTime += 1;
            },
            mutateKey: "rest-api-deployment-timeout-plus-one",
        });
    });

    test("the stage serves the deployment", () => {
        const { deployment, stage } = deploymentAndStage(base);
        expect(stage.stack).toBe(deployment.stack);
        expect(stage.properties.DeploymentId).toEqual({ Ref: deployment.logicalId });
    });

    test("a role name prefix gives a new deployment, which the stage serves", () => {
        // The control: the invoke role is renamed, which replaces it, while the API definition is the
        // same, so the role's name is what moves the deployment.
        expect(invokeRole(base).properties.RoleName).not.toMatch(new RegExp(`^${PREFIX}`));
        expect(invokeRole(prefixed).properties.RoleName).toMatch(new RegExp(`^${PREFIX}`));
        expect(invokeRole(prefixed).logicalId).toBe(invokeRole(base).logicalId);
        expect(restApiBody(prefixed)).toEqual(restApiBody(base));

        const before = deploymentAndStage(base);
        const after = deploymentAndStage(prefixed);
        expect(after.deployment.logicalId).not.toBe(before.deployment.logicalId);
        expect(after.stage.logicalId).toBe(before.stage.logicalId);
        expect(after.stage.properties.DeploymentId).toEqual({ Ref: after.deployment.logicalId });
    });

    test("a change to the API definition gives a new deployment", () => {
        expect(invokeRole(longerTimeout).properties.RoleName).toBe(
            invokeRole(base).properties.RoleName
        );
        expect(restApiBody(longerTimeout)).not.toEqual(restApiBody(base));
        expect(deploymentAndStage(longerTimeout).deployment.logicalId).not.toBe(
            deploymentAndStage(base).deployment.logicalId
        );
    });

    test("the deployment depends on the invoke role", () => {
        const { deployment } = deploymentAndStage(base);
        expect(deployment.raw.DependsOn ?? []).toEqual(
            expect.arrayContaining([invokeRole(base).logicalId])
        );
    });
});
