/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Session-bootstrap wiring of the EDH Workstations add-on, asserted on synthesized templates: the
 * nested-stack dependency direction, the cross-call plumbing to the API key service, the per-session
 * secret lifecycle grants bounded to the configured name prefix, the grant that binds a session
 * secret to its desktop instance (held by the launch worker, the reconciler and the admin service,
 * and only for a policy that blocks public access), the dedicated CMK with its instance-role decrypt
 * statement, and the reconciler — built whenever the add-on is on, holding the credential-revocation
 * grants whenever it is built and the minting grants only when session bootstrap is. Every absence
 * is paired with the bootstrap-enabled synth as its positive control.
 */

import {
    ACCOUNT,
    synthTemplate,
    expectAbsent,
    SynthResult,
    Resource,
    TemplateName,
    lambdaByHandler,
    statementsOf,
    actionsOf,
    resourcesOf,
} from "../../support/templateSynth";
import { enableEdh } from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs three of them (two templates, bootstrap on and off).
jest.setTimeout(600_000);

const SECRET_PREFIX = "vams/edh-session/";
const roleArn = (partition: string) =>
    `arn:${partition}:iam::${ACCOUNT}:role/EdhDesktopInstanceRole`;
const prefixArn = (s: SynthResult) =>
    `arn:${s.partition}:secretsmanager:${s.region}:${ACCOUNT}:secret:${SECRET_PREFIX}*`;
const WEB_URL_PARAM_ARN = `arn:aws:ssm:us-east-1:${ACCOUNT}:parameter/vams-t1-commercial/web/deployedUrl`;

const enableSessionBootstrap = (c: any) => {
    enableEdh(c);
    const bootstrap = c.app.addons.useEdhWorkstations.sessionBootstrap;
    bootstrap.enabled = true;
    bootstrap.workstationInstanceRoleArns = [roleArn(c.env.partition)];
};

const synthBootstrap = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableSessionBootstrap, mutateKey: "edhSessionBootstrap" });
const synthEdhOnly = (name: TemplateName): SynthResult =>
    synthTemplate(name, { mutate: enableEdh, mutateKey: "edhWorkstationsEnabled" });

const WORKSTATION_HANDLER = "handlers.addon.edh.edhWorkstationService.lambda_handler";
const LAUNCH_WORKER_HANDLER = "handlers.addon.edh.edhLaunchWorker.lambda_handler";
const RECONCILER_HANDLER = "handlers.addon.edh.edhSessionReconciler.lambda_handler";
const ADMIN_HANDLER = "handlers.addon.edh.edhAdminService.lambda_handler";

const BOOTSTRAP_ENV = [
    "EDH_SESSION_BOOTSTRAP_ENABLED",
    "EDH_SESSION_SECRET_PREFIX",
    "EDH_SESSION_KEY_TTL_MINUTES",
    "EDH_WORKSTATION_INSTANCE_ROLE_ARNS",
    "EDH_SESSION_SECRET_KMS_KEY_ARN",
    "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME",
    "VAMS_BASE_URL",
];

function envOf(fn: Resource): Record<string, any> {
    return fn.properties.Environment?.Variables ?? {};
}

const SECRET_LIFECYCLE_ACTIONS = [
    "secretsmanager:CreateSecret",
    "secretsmanager:DeleteSecret",
    "secretsmanager:DescribeSecret",
    "secretsmanager:PutResourcePolicy",
    "secretsmanager:PutSecretValue",
    "secretsmanager:TagResource",
];

// DescribeSecret is also part of the root-key read every add-on Lambda holds, so a statement is a
// per-session lifecycle grant when it carries one of the five actions that read grant lacks.
const LIFECYCLE_ONLY_ACTIONS = SECRET_LIFECYCLE_ACTIONS.filter(
    (a) => a !== "secretsmanager:DescribeSecret"
);

// A revoke overwrites the secret's value and, for a desktop that is gone, removes the container.
const SECRET_REVOKE_ACTIONS = ["secretsmanager:DeleteSecret", "secretsmanager:PutSecretValue"];

// The lifecycle actions only a mint uses.
const SECRET_MINT_ONLY_ACTIONS = LIFECYCLE_ONLY_ACTIONS.filter(
    (a) => !SECRET_REVOKE_ACTIONS.includes(a)
);

/** Statements on the handler's role that grant per-session secret lifecycle actions. */
const lifecycleStatements = (s: SynthResult, handler: string) =>
    statementsOf(s, lambdaByHandler(s, handler)).filter((st) =>
        actionsOf(st).some((a) => LIFECYCLE_ONLY_ACTIONS.includes(a))
    );

/** The lifecycle actions the handler's role holds, as a sorted set. */
const lifecycleActions = (s: SynthResult, handler: string) =>
    [...new Set(lifecycleStatements(s, handler).flatMap(actionsOf))]
        .filter((a) => SECRET_LIFECYCLE_ACTIONS.includes(a))
        .sort();

/** Statements on the handler's role that grant an action only a mint uses. */
const mintStatements = (s: SynthResult, handler: string) =>
    statementsOf(s, lambdaByHandler(s, handler)).filter((st) =>
        actionsOf(st).some((a) => SECRET_MINT_ONLY_ACTIONS.includes(a))
    );

/** lambda:InvokeFunction resources on the handler's role that name the API key service. */
const apiKeyInvokeResources = (s: SynthResult, handler: string) =>
    statementsOf(s, lambdaByHandler(s, handler))
        .filter((st) => actionsOf(st).includes("lambda:InvokeFunction"))
        .flatMap(resourcesOf)
        .filter((res) => /apiKeyService/.test(res));

/** ssm:GetParameter resources on the handler's role that name the deployed web URL parameter. */
const webUrlParameterReads = (s: SynthResult, handler: string) =>
    statementsOf(s, lambdaByHandler(s, handler))
        .filter((st) => actionsOf(st).includes("ssm:GetParameter"))
        .flatMap(resourcesOf)
        .filter((res) => res === WEB_URL_PARAM_ARN);

describe("session bootstrap: nested-stack dependency direction", () => {
    it("AddonBuilder depends on ApiBuilder and not the reverse", () => {
        const s = synthBootstrap("commercial");
        const nested = s.ofType("AWS::CloudFormation::Stack");
        const addon = nested.find((r) => /^AddonBuilderNestedStack/.test(r.logicalId));
        const api = nested.find((r) => /^ApiBuilderNestedStack/.test(r.logicalId));
        expect(addon).toBeDefined();
        expect(api).toBeDefined();
        expect(addon!.raw.DependsOn).toEqual(expect.arrayContaining([api!.logicalId]));
        expect(api!.raw.DependsOn ?? []).not.toContain(addon!.logicalId);
    });
});

describe("session bootstrap: apiKeyService cross-call plumbing", () => {
    it("the workstation service receives the seven bootstrap variables and no other base-URL carrier", () => {
        const env = envOf(lambdaByHandler(synthBootstrap("commercial"), WORKSTATION_HANDLER));
        for (const name of BOOTSTRAP_ENV) expect(env).toHaveProperty(name);
        expect(Object.keys(env).filter((k) => /DEPLOYED_URL|BASE_URL/.test(k))).toEqual([
            "VAMS_BASE_URL",
        ]);
        expect(env.EDH_SESSION_BOOTSTRAP_ENABLED).toBe("true");
        expect(env.EDH_SESSION_SECRET_PREFIX).toBe(SECRET_PREFIX);
        expect(env.EDH_SESSION_KEY_TTL_MINUTES).toBe("480");
        expect(JSON.parse(env.EDH_WORKSTATION_INSTANCE_ROLE_ARNS)).toEqual([roleArn("aws")]);
        expect(SynthResult.flatten(env.EDH_SESSION_SECRET_KMS_KEY_ARN)).toMatch(
            /EdhSessionSecretsKey/
        );
        expect(SynthResult.flatten(env.API_KEY_SERVICE_LAMBDA_FUNCTION_NAME)).toMatch(
            /apiKeyService/
        );
        // CloudFront without a custom domain: the URL is known only after the web stack deploys, so
        // the variable points at the parameter that stack writes.
        expect(env.VAMS_BASE_URL).toBe("ssm:/vams-t1-commercial/web/deployedUrl");
    });

    it("govcloud sets VAMS_BASE_URL from the ALB host", () => {
        const env = envOf(lambdaByHandler(synthBootstrap("govcloud"), WORKSTATION_HANDLER));
        expect(env.VAMS_BASE_URL).toBe("https://vams-t1.example.com");
    });

    it("the workstation service may invoke the apiKeyService function and the launch worker only", () => {
        const s = synthBootstrap("commercial");
        const statements = statementsOf(s, lambdaByHandler(s, WORKSTATION_HANDLER));
        const invoke = statements.filter((st) => actionsOf(st).includes("lambda:InvokeFunction"));
        const resources = invoke.flatMap(resourcesOf);
        // The invoke grant on the launch worker sits beside the cross-call grant.
        expect(resources.some((res) => /apiKeyService/.test(res))).toBe(true);
        for (const res of resources) expect(res).toMatch(/apiKeyService|edhLaunchWorker/);
    });

    it("the deployed web URL parameter is granted by its exact name", () => {
        expect(webUrlParameterReads(synthBootstrap("commercial"), WORKSTATION_HANDLER)).toEqual([
            WEB_URL_PARAM_ARN,
        ]);
    });

    it("with the add-on on and bootstrap off, the flag is false, the cross-call target is still named and neither cross-call grant is made", () => {
        const s = synthEdhOnly("commercial");
        const env = envOf(lambdaByHandler(s, WORKSTATION_HANDLER));
        expect(env.EDH_SESSION_BOOTSTRAP_ENABLED).toBe("false");
        expect(env.EDH_SESSION_SECRET_KMS_KEY_ARN).toBe("");
        expect(JSON.parse(env.EDH_WORKSTATION_INSTANCE_ROLE_ARNS)).toEqual([]);
        // The handler reads the name at import; the IAM grant follows the feature.
        expect(SynthResult.flatten(env.API_KEY_SERVICE_LAMBDA_FUNCTION_NAME)).toMatch(
            /apiKeyService/
        );
        expect(apiKeyInvokeResources(s, WORKSTATION_HANDLER)).toEqual([]);
        // The resource-name prefix read every Lambda holds stays; the web URL parameter read is the
        // bootstrap's own.
        expect(webUrlParameterReads(s, WORKSTATION_HANDLER)).toEqual([]);
    });
});

describe("session bootstrap: binding a session secret to its desktop instance", () => {
    const bindingStatements = (s: SynthResult, handler: string) =>
        statementsOf(s, lambdaByHandler(s, handler)).filter((st) =>
            actionsOf(st).includes("secretsmanager:PutResourcePolicy")
        );

    it.each([
        ["launch worker", LAUNCH_WORKER_HANDLER],
        ["reconciler", RECONCILER_HANDLER],
        ["admin service", ADMIN_HANDLER],
    ])(
        "the %s may put a session secret's resource policy under the name prefix only, and only one that blocks public access",
        (_, handler) => {
            const s = synthBootstrap("commercial");
            const binding = bindingStatements(s, handler);
            expect(binding.length).toBeGreaterThan(0);
            for (const st of binding) {
                expect(actionsOf(st)).toEqual(["secretsmanager:PutResourcePolicy"]);
                expect(resourcesOf(st)).toEqual([prefixArn(s)]);
                expect(st.Condition).toEqual({
                    Bool: { "secretsmanager:BlockPublicPolicy": "true" },
                });
            }
        }
    );

    it("the launch worker receives the seven bootstrap variables with bootstrap on and off", () => {
        for (const s of [synthBootstrap("commercial"), synthEdhOnly("commercial")]) {
            const env = envOf(lambdaByHandler(s, LAUNCH_WORKER_HANDLER));
            for (const name of BOOTSTRAP_ENV) expect(env).toHaveProperty(name);
        }
    });

    it("bootstrap off grants the launch worker no lifecycle action", () => {
        expectAbsent(
            "secret lifecycle statements on the launch worker",
            lifecycleStatements(synthEdhOnly("commercial"), LAUNCH_WORKER_HANDLER),
            {
                description: "the bootstrap-enabled synth grants them",
                count: lifecycleStatements(synthBootstrap("commercial"), LAUNCH_WORKER_HANDLER)
                    .length,
            }
        );
    });
});

describe("session bootstrap: dedicated CMK", () => {
    const keysOf = (s: SynthResult) =>
        s.where("AWS::KMS::Key", (r) => /EdhSessionSecretsKey/.test(r.logicalId));

    it.each(["commercial", "govcloud"] as TemplateName[])(
        "%s: the key rotates and lets the instance roles decrypt only through Secrets Manager",
        (name) => {
            const s = synthBootstrap(name);
            const keys = keysOf(s);
            expect(keys).toHaveLength(1);
            expect(keys[0].properties.EnableKeyRotation).toBe(true);
            const statements: any[] = keys[0].properties.KeyPolicy.Statement;
            // The account-root administration statement CDK adds, plus the instance-role grant.
            expect(statements.length).toBeGreaterThanOrEqual(2);
            const grant = statements.find(
                (st) => st.Sid === "WorkstationInstanceRolesDecryptViaSecretsManager"
            );
            expect(grant).toBeDefined();
            expect(([] as string[]).concat(grant.Principal.AWS)).toEqual([roleArn(s.partition)]);
            expect(actionsOf(grant).sort()).toEqual(["kms:Decrypt", "kms:DescribeKey"]);
            expect(grant.Condition.StringEquals["kms:ViaService"]).toBe(
                `secretsmanager.${s.region}.amazonaws.com`
            );
            expect(grant.Condition.ArnLike["kms:EncryptionContext:SecretARN"]).toBe(prefixArn(s));
        }
    );

    it("the EDH-only synth emits no session-secrets key", () => {
        expectAbsent("EdhSessionSecretsKey", keysOf(synthEdhOnly("commercial")), {
            description: "the bootstrap-enabled synth emits the key",
            count: keysOf(synthBootstrap("commercial")).length,
        });
    });
});

describe("session bootstrap: secret lifecycle IAM", () => {
    it("the workstation service holds exactly the six lifecycle actions, on the name prefix only", () => {
        const s = synthBootstrap("commercial");
        expect(lifecycleActions(s, WORKSTATION_HANDLER)).toEqual(SECRET_LIFECYCLE_ACTIONS);
        for (const st of lifecycleStatements(s, WORKSTATION_HANDLER)) {
            expect(resourcesOf(st)).toEqual([prefixArn(s)]);
        }
    });

    it("govcloud renders the prefix ARN in its own partition", () => {
        const s = synthBootstrap("govcloud");
        const lifecycle = lifecycleStatements(s, WORKSTATION_HANDLER);
        expect(lifecycle.length).toBeGreaterThan(0);
        for (const st of lifecycle) {
            expect(resourcesOf(st)).toEqual([
                `arn:aws-us-gov:secretsmanager:us-gov-west-1:${ACCOUNT}:secret:${SECRET_PREFIX}*`,
            ]);
        }
    });

    it("bootstrap off grants no lifecycle action", () => {
        expectAbsent(
            "secret lifecycle statements on the workstation service",
            lifecycleStatements(synthEdhOnly("commercial"), WORKSTATION_HANDLER),
            {
                description: "the bootstrap-enabled synth grants them",
                count: lifecycleStatements(synthBootstrap("commercial"), WORKSTATION_HANDLER)
                    .length,
            }
        );
    });
});

describe("session bootstrap: reconciler", () => {
    const schedules = (s: SynthResult) =>
        s.where("AWS::Events::Rule", (r) => r.properties.ScheduleExpression === "rate(15 minutes)");

    it.each([
        ["on", synthBootstrap],
        ["off", synthEdhOnly],
    ])(
        "with session bootstrap %s, runs every 15 minutes with the batch-profile timeout",
        (_, synth) => {
            const s = synth("commercial");
            const reconciler = lambdaByHandler(s, RECONCILER_HANDLER);
            expect(reconciler.properties.Timeout).toBe(900);
            const rules = schedules(s);
            expect(rules).toHaveLength(1);
            expect(SynthResult.flatten(rules[0].properties.Targets[0].Arn)).toContain(
                reconciler.logicalId
            );
        }
    );

    it("receives the same bootstrap environment and secret grants as the workstation service", () => {
        const s = synthBootstrap("commercial");
        const env = envOf(lambdaByHandler(s, RECONCILER_HANDLER));
        for (const name of BOOTSTRAP_ENV) expect(env).toHaveProperty(name);
        expect(env.EDH_SESSION_BOOTSTRAP_ENABLED).toBe("true");
        expect(SynthResult.flatten(env.API_KEY_SERVICE_LAMBDA_FUNCTION_NAME)).toMatch(
            /apiKeyService/
        );
        expect(SynthResult.flatten(env.EDH_SESSION_SECRET_KMS_KEY_ARN)).toMatch(
            /EdhSessionSecretsKey/
        );
        expect(lifecycleActions(s, RECONCILER_HANDLER)).toEqual(SECRET_LIFECYCLE_ACTIONS);
    });

    it("the reconciler may invoke the apiKeyService function and never the launch worker", () => {
        const s = synthBootstrap("commercial");
        const statements = statementsOf(s, lambdaByHandler(s, RECONCILER_HANDLER));
        const resources = statements
            .filter((st) => actionsOf(st).includes("lambda:InvokeFunction"))
            .flatMap(resourcesOf);
        expect(resources.some((res) => /apiKeyService/.test(res))).toBe(true);
        for (const res of resources) expect(res).not.toMatch(/edhLaunchWorker/);
    });

    it("with session bootstrap off, the reconciler carries the flag as false and keeps the apiKeyService invoke and the secret revoke actions, on the name prefix only", () => {
        const off = synthEdhOnly("commercial");
        const env = envOf(lambdaByHandler(off, RECONCILER_HANDLER));
        for (const name of BOOTSTRAP_ENV) expect(env).toHaveProperty(name);
        expect(env.EDH_SESSION_BOOTSTRAP_ENABLED).toBe("false");
        // A key or secret left by a deployment that had session bootstrap on is still taken back
        // once its desktop is gone, so the revoke path stays granted without the feature.
        expect(apiKeyInvokeResources(off, RECONCILER_HANDLER).length).toBeGreaterThan(0);
        expect(lifecycleActions(off, RECONCILER_HANDLER)).toEqual(SECRET_REVOKE_ACTIONS);
        for (const st of lifecycleStatements(off, RECONCILER_HANDLER)) {
            expect(resourcesOf(st)).toEqual([prefixArn(off)]);
        }
    });

    it("with session bootstrap off, the reconciler holds no minting grant", () => {
        const off = synthEdhOnly("commercial");
        const on = synthBootstrap("commercial");
        expectAbsent(
            "secret minting statements on the reconciler",
            mintStatements(off, RECONCILER_HANDLER),
            {
                description: "the bootstrap-enabled synth grants them",
                count: mintStatements(on, RECONCILER_HANDLER).length,
            }
        );
        expectAbsent(
            "the deployed web URL parameter read on the reconciler",
            webUrlParameterReads(off, RECONCILER_HANDLER),
            {
                description: "the bootstrap-enabled synth grants it",
                count: webUrlParameterReads(on, RECONCILER_HANDLER).length,
            }
        );
    });
});

describe("session bootstrap: admin service", () => {
    it("receives the bootstrap environment and, with session bootstrap on, the apiKeyService invoke and the lifecycle grants", () => {
        const s = synthBootstrap("commercial");
        const env = envOf(lambdaByHandler(s, ADMIN_HANDLER));
        for (const name of BOOTSTRAP_ENV) expect(env).toHaveProperty(name);
        expect(env.EDH_SESSION_BOOTSTRAP_ENABLED).toBe("true");
        expect(apiKeyInvokeResources(s, ADMIN_HANDLER).length).toBeGreaterThan(0);
        expect(lifecycleActions(s, ADMIN_HANDLER)).toEqual(SECRET_LIFECYCLE_ACTIONS);
        for (const st of lifecycleStatements(s, ADMIN_HANDLER)) {
            expect(resourcesOf(st)).toEqual([prefixArn(s)]);
        }
    });

    it("with session bootstrap off, carries the flag as false and holds neither grant", () => {
        const off = synthEdhOnly("commercial");
        const on = synthBootstrap("commercial");
        expect(envOf(lambdaByHandler(off, ADMIN_HANDLER)).EDH_SESSION_BOOTSTRAP_ENABLED).toBe(
            "false"
        );
        // The revoke-only grant is the reconciler's alone; the admin service revokes inline and so
        // follows the feature like the service and the worker.
        expectAbsent(
            "an apiKeyService invoke on the admin service",
            apiKeyInvokeResources(off, ADMIN_HANDLER),
            {
                description: "the bootstrap-enabled synth grants it",
                count: apiKeyInvokeResources(on, ADMIN_HANDLER).length,
            }
        );
        expectAbsent(
            "secret lifecycle statements on the admin service",
            lifecycleStatements(off, ADMIN_HANDLER),
            {
                description: "the bootstrap-enabled synth grants them",
                count: lifecycleStatements(on, ADMIN_HANDLER).length,
            }
        );
    });
});
