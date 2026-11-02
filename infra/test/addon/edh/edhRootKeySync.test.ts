/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The automatic EDH root key sync, asserted on synthesized templates.
 *
 * EDH regenerates its root key each time its web interface starts and publishes it nowhere, so with
 * `edhRootKeyAutoSync` on (its default: unset is on, only an explicit false turns it off) the add-on
 * deploys a VAMS-owned Run Command document that encrypts that one key file on the controller to the public
 * half of a VAMS-owned AWS KMS RSA key, and a sync Lambda that runs it, decrypts the ciphertext and writes the
 * key into the existing root-key secret. The suite pins:
 *
 *   * A10: the document, the KMS key, the sync function, its role, its alarm and the triggers exist when the
 *     field is unset and when it is true; with it false none of them exists and every other resource of the
 *     deployment is the same as with it on, but for the trigger's environment value and invoke grant;
 *   * A11: the document's one parameter is the base64 public key, held to base64 characters, and its one
 *     aws:runShellScript step is the repository script with the configured cluster's key file;
 *   * A12: the sync role's grants, action by action, are exactly the scoped ones, with no `*` resource
 *     but the two reads that accept none; only the sync role may decrypt with the KMS key; only the
 *     triggering roles may invoke the sync;
 *   * the sync function runs one at a time without retries and pins the document version, and an alarm
 *     reads the failure metric it writes;
 *   * A13: the GovCloud partition synthesizes the same resources with its own ARNs.
 *
 * The CDK Nag suppressions are held by `edhNag.test.ts`.
 */

import * as fs from "fs";
import * as path from "path";
import {
    ACCOUNT,
    Resource,
    SynthResult,
    TemplateName,
    actionsOf,
    expectAbsent,
    handlerOf,
    lambdaByHandler,
    resolveReference,
    statementsOf,
    synthTemplate,
} from "../../support/templateSynth";
import { edhFunctions, enableEdh, inEdhStack } from "./support/edhSynth";

// A full-app synth is ~20 s and this file needs four of them.
jest.setTimeout(900_000);

const REPO = path.join(__dirname, "..", "..", "..", "..");
const SYNC_HANDLER = "handlers.addon.edh.edhRootKeySync.lambda_handler";
/** The cluster id the EDH synth configures (`support/edhSynth.ts`). */
const CLUSTER_ID = "edh-t1";
const KEY_FILE = `/opt/edh/${CLUSTER_ID}/cluster_manager/web_interface/keys/admin_api_key.txt`;
const SCRIPT = path.join(REPO, "backendAddons", "edh", "rootKeySync", "read-root-key.sh");
const BACKEND_SYNC = path.join(
    REPO,
    "backend",
    "backend",
    "handlers",
    "addon",
    "edh",
    "edhRootKeySync.py"
);
const SYNC_ENV = "EDH_ROOT_KEY_SYNC_FUNCTION_NAME";
const LOCK_KEY = "SETTINGS#rootKeySync";
/** The resource types the sync adds to the add-on's nested template. */
const SYNC_TYPES = [
    "AWS::SSM::Document",
    "AWS::KMS::Key",
    "AWS::CloudWatch::Alarm",
    "AWS::Lambda::EventInvokeConfig",
];

const synthWith = (name: TemplateName, value: boolean | undefined, key: string): SynthResult =>
    synthTemplate(name, {
        mutate: (c: any) => {
            enableEdh(c);
            const edh = c.app.addons.useEdhWorkstations;
            if (value === undefined) delete edh.edhRootKeyAutoSync;
            else edh.edhRootKeyAutoSync = value;
        },
        mutateKey: key,
    });

const unsetSynth = () => synthWith("commercial", undefined, "edhRootKeySyncUnset");
const onSynth = () => synthWith("commercial", true, "edhRootKeySyncOn");
const offSynth = () => synthWith("commercial", false, "edhRootKeySyncOff");
const govSynth = () => synthWith("govcloud", true, "edhRootKeySyncGovCloud");

const documents = (s: SynthResult): Resource[] => s.where("AWS::SSM::Document", inEdhStack);
const keys = (s: SynthResult): Resource[] =>
    s.where("AWS::KMS::Key", (r) => inEdhStack(r) && r.properties.KeyUsage === "ENCRYPT_DECRYPT");
const alarms = (s: SynthResult): Resource[] => s.where("AWS::CloudWatch::Alarm", inEdhStack);
const alarmOn = (s: SynthResult, metricName: string): Resource =>
    only(alarms(s).filter((a) => a.properties.MetricName === metricName));
const syncFunctions = (s: SynthResult): Resource[] =>
    s.where("AWS::Lambda::Function", (r) => inEdhStack(r) && handlerOf(r) === SYNC_HANDLER);
const envOf = (fn: Resource): Record<string, any> => fn.properties.Environment?.Variables ?? {};
const rootKeySecretArn = (s: SynthResult) =>
    `arn:${s.partition}:secretsmanager:${s.region}:${ACCOUNT}:secret:edh-root-AbCdEf`;
/** Every add-on Lambda that calls EDH: every one but the sync function itself. */
const edhCallers = (s: SynthResult): Resource[] =>
    edhFunctions(s).filter((r) => handlerOf(r) !== SYNC_HANDLER);
const only = <T>(items: T[]): T => {
    expect(items).toHaveLength(1);
    return items[0];
};
const theSync = (s: SynthResult) => lambdaByHandler(s, SYNC_HANDLER);
const theDocument = (s: SynthResult) => only(documents(s));
const theKey = (s: SynthResult) => only(keys(s));

/** One allowed (action, resource, condition) of a role, so a merged or split statement reads the same. */
interface Grant {
    action: string;
    resource: string;
    condition: string;
}
const grantsOfStatements = (statements: any[]): Grant[] =>
    statements
        .filter((st) => (st.Effect ?? "Allow") === "Allow")
        .flatMap((st) =>
            actionsOf(st).flatMap((action) =>
                ([] as unknown[]).concat(st.Resource ?? []).map((r) => ({
                    action,
                    resource: SynthResult.flatten(r),
                    condition: JSON.stringify(st.Condition ?? null),
                }))
            )
        );
const grantsOf = (s: SynthResult, fn: Resource): Grant[] => grantsOfStatements(statementsOf(s, fn));
const syncGrants = (s: SynthResult) => grantsOf(s, theSync(s));
const withAction = (grants: Grant[], action: string) => grants.filter((g) => g.action === action);
const allGrants = (s: SynthResult): Grant[] =>
    grantsOfStatements(
        s
            .ofType("AWS::IAM::Policy")
            .concat(s.ofType("AWS::IAM::ManagedPolicy"))
            .flatMap((p) => (p.properties.PolicyDocument?.Statement ?? []) as any[])
    );
/** A Python module constant's string value, read from the backend source. */
const pythonConstant = (file: string, name: string): string => {
    const match = new RegExp(`^${name} = "([^"]*)"`, "m").exec(fs.readFileSync(file, "utf8"));
    expect(match).not.toBeNull();
    return (match as RegExpExecArray)[1];
};

describe("A10: the sync is deployed by default", () => {
    test.each([
        ["unset", unsetSynth],
        ["true", onSynth],
    ])(
        "with the field %s the document, key, function, alarm and triggers exist",
        (_label, synth) => {
            const s = synth();
            expect(documents(s)).toHaveLength(1);
            expect(keys(s)).toHaveLength(1);
            expect(syncFunctions(s)).toHaveLength(1);
            expect(alarms(s)).toHaveLength(2);
            expect(edhCallers(s).filter((fn) => SYNC_ENV in envOf(fn)).length).toBe(
                edhCallers(s).length
            );
        }
    );

    test("with the field false nothing of the sync exists", () => {
        const s = offSynth();
        const on = onSynth();
        for (const [subject, query] of [
            ["the sync document", documents],
            ["the encryption key", keys],
            ["the sync function", syncFunctions],
            ["the failure alarm", alarms],
        ] as Array<[string, (x: SynthResult) => Resource[]]>) {
            expectAbsent(subject, query(s), {
                description: `${subject} with the sync on`,
                count: query(on).length,
            });
        }
        const naming = (x: SynthResult) => edhFunctions(x).filter((fn) => SYNC_ENV in envOf(fn));
        expectAbsent("an add-on Lambda naming a sync function", naming(s), {
            description: "add-on Lambdas naming it with the sync on",
            count: naming(on).length,
        });
        expectAbsent("a Run Command grant", withAction(allGrants(s), "ssm:SendCommand"), {
            description: "the sync role's Run Command grant with the sync on",
            count: withAction(allGrants(on), "ssm:SendCommand").length,
        });
        const writesRootKey = (x: SynthResult) =>
            withAction(allGrants(x), "secretsmanager:PutSecretValue").filter(
                (g) => g.resource === rootKeySecretArn(x)
            );
        expectAbsent("a write grant on the root-key secret", writesRootKey(s), {
            description: "the sync role's write grant with the sync on",
            count: writesRootKey(on).length,
        });
    });

    test("with the field false every other resource is the one the sync-on template has", () => {
        const on = onSynth();
        const off = offSynth();
        const sync = theSync(on);
        // What the sync adds: its resources, its role and policy, and its stack output.
        const syncOwned = (r: Resource) =>
            r.logicalId.startsWith("EdhRootKeySync") ||
            r.logicalId.startsWith(sync.logicalId.replace(/[0-9A-F]{8}$/, ""));
        const key = (r: Resource) => `${r.stack}/${r.logicalId}`;
        const offKeys = new Set(off.resources.map(key));
        const added = on.resources.filter((r) => !offKeys.has(key(r)));
        expect(added.length).toBeGreaterThan(0);
        // The KMS interface endpoint the sync calls is added to the VPC stack (edhVpcEndpoints.test.ts).
        const syncEndpoint = (r: Resource) =>
            r.type === "AWS::EC2::VPCEndpoint" && /^KMSEndpoint/.test(r.logicalId);
        expect(
            added.filter((r) => !(inEdhStack(r) && syncOwned(r)) && !syncEndpoint(r)).map(key)
        ).toEqual([]);
        expect([...new Set(added.map((r) => r.type))].sort()).toEqual(
            expect.arrayContaining(SYNC_TYPES)
        );
        const onKeys = new Set(on.resources.map(key));
        expect(off.resources.filter((r) => !onKeys.has(key(r))).map(key)).toEqual([]);

        // A resource both carry is identical once the trigger's two traces are taken out: the environment
        // value naming the sync function and the statement allowing its invoke. A nested stack's template URL
        // and the catalog registration's per-synth timestamp differ between any two synths.
        // Several custom resources stamp the synth time, as an ISO string or epoch milliseconds.
        const normalize = (r: Resource): string => {
            const props = JSON.parse(JSON.stringify(r.properties));
            delete props.TemplateURL;
            if (props.Environment?.Variables) delete props.Environment.Variables[SYNC_ENV];
            if (props.PolicyDocument?.Statement) {
                const mentionsSync = (value: unknown) =>
                    JSON.stringify(value).includes(sync.logicalId);
                props.PolicyDocument.Statement = props.PolicyDocument.Statement.map((st: any) =>
                    Array.isArray(st.Resource)
                        ? { ...st, Resource: st.Resource.filter((x: unknown) => !mentionsSync(x)) }
                        : st
                ).filter(
                    (st: any) =>
                        !(Array.isArray(st.Resource)
                            ? st.Resource.length === 0
                            : mentionsSync(st.Resource))
                );
                // A statement left with one resource is written as a string, as CDK writes it.
                for (const st of props.PolicyDocument.Statement) {
                    if (Array.isArray(st.Resource) && st.Resource.length === 1)
                        st.Resource = st.Resource[0];
                }
            }
            return JSON.stringify(props)
                .replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z/g, "<time>")
                .replace(/"\d{13}"/g, '"<ms>"');
        };
        const offByKey = new Map(off.resources.map((r) => [key(r), r]));
        const changed = on.resources
            .filter((r) => offByKey.has(key(r)))
            .filter((r) => normalize(r) !== normalize(offByKey.get(key(r)) as Resource))
            .map(key);
        expect(changed).toEqual([]);
    });
});

describe("A11: the Run Command document", () => {
    test("is a Command document with one base64 parameter and one shell step", () => {
        const doc = theDocument(onSynth());
        expect(doc.properties.DocumentType).toBe("Command");
        expect(doc.properties.UpdateMethod).toBe("Replace");
        const content = doc.properties.Content;
        expect(content.schemaVersion).toBe("2.2");
        expect(Object.keys(content).sort()).toEqual([
            "description",
            "mainSteps",
            "parameters",
            "schemaVersion",
        ]);
        expect(Object.keys(content.parameters)).toEqual(["publicKey"]);
        const parameter = content.parameters.publicKey;
        expect(parameter.type).toBe("String");
        expect(parameter.default).toBeUndefined();
        expect(parameter.allowedPattern).toBe("^[A-Za-z0-9+/]{1,2048}={0,2}$");
        expect(content.mainSteps).toHaveLength(1);
        const step = content.mainSteps[0];
        expect(step.action).toBe("aws:runShellScript");
        expect(step.precondition).toEqual({ StringEquals: ["platformType", "Linux"] });
        expect(Object.keys(step.inputs).sort()).toEqual(["runCommand", "timeoutSeconds"]);
    });

    test("the allowed pattern admits a base64 public key and nothing a shell reads", () => {
        const pattern = new RegExp(
            theDocument(onSynth()).properties.Content.parameters.publicKey.allowedPattern
        );
        expect(pattern.test("MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA0+/xyz==")).toBe(true);
        for (const bad of [
            "",
            "abc'def",
            "abc def",
            "$(id)",
            "abc;id",
            "a\nb",
            "{{ssm:/x}}",
            "a`b`",
            "==abc",
        ]) {
            expect([bad, pattern.test(bad)]).toEqual([bad, false]);
        }
    });

    test("its step is the repository script with the configured cluster's key file, which it only encrypts", () => {
        const step = theDocument(onSynth()).properties.Content.mainSteps[0];
        const expected = fs
            .readFileSync(SCRIPT, "utf8")
            .split(/\r?\n/)
            .filter((l) => l.trim() !== "" && !l.trimStart().startsWith("#"))
            .map((l) => l.split("__EDH_ROOT_KEY_FILE__").join(KEY_FILE));
        expect(step.inputs.runCommand).toEqual(expected);
        const text = step.inputs.runCommand.join("\n");
        // The only substitution is the one parameter; the key file is read once, as openssl's input.
        expect(text.match(/\{\{[^}]*\}\}/g)).toEqual(["{{ publicKey }}"]);
        expect(text.split(KEY_FILE)).toHaveLength(2);
        expect(text).toContain(`-in '${KEY_FILE}'`);
        expect(text).toMatch(
            /openssl pkeyutl -encrypt -pubin .*-pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256 -pkeyopt rsa_mgf1_md:sha256/
        );
        // Nothing prints the key file: the one line that writes to standard output prints the ciphertext.
        expect(text).not.toMatch(/\bcat\b|\becho\b/);
        expect(step.inputs.runCommand.filter((l: string) => /^base64 -w 0 /.test(l))).toEqual([
            'base64 -w 0 "$workdir/root-key.enc"',
        ]);
        expect(text).toContain("umask 077");
        expect(text).toContain("trap 'rm -rf \"$workdir\"' EXIT");
    });

    test("the sync function runs that document at the version the deployment created", () => {
        const s = onSynth();
        const env = envOf(theSync(s));
        expect(env.EDH_ROOT_KEY_DOCUMENT_NAME).toEqual({ Ref: theDocument(s).logicalId });
        expect(env.EDH_ROOT_KEY_DOCUMENT_VERSION).toBe("1");
    });
});

describe("the encryption key", () => {
    test("is a VAMS-owned RSA key for OAEP encryption that only the sync role may read and decrypt with", () => {
        const s = onSynth();
        const key = theKey(s);
        expect(key.properties.KeySpec).toBe("RSA_3072");
        expect(key.properties.KeyUsage).toBe("ENCRYPT_DECRYPT");
        expect(key.properties.EnableKeyRotation).toBeUndefined();
        expect(envOf(theSync(s)).EDH_ROOT_KEY_ENCRYPTION_KEY_ARN).toEqual({
            "Fn::GetAtt": [key.logicalId, "Arn"],
        });
        const statements: any[] = key.properties.KeyPolicy.Statement;
        expect(statements.map((st) => st.Sid).sort()).toEqual([
            "AccountAdministersTheKey",
            "SyncFunctionDecryptsWithOaepSha256",
            "SyncFunctionReadsThePublicKey",
        ]);
        const sid = (name: string) => statements.find((st) => st.Sid === name);
        const syncRole = { "Fn::GetAtt": [theSync(s).properties.Role["Fn::GetAtt"][0], "Arn"] };
        expect(sid("SyncFunctionDecryptsWithOaepSha256")).toEqual({
            Sid: "SyncFunctionDecryptsWithOaepSha256",
            Effect: "Allow",
            Principal: { AWS: syncRole },
            Action: "kms:Decrypt",
            Resource: "*",
            Condition: { StringEquals: { "kms:EncryptionAlgorithm": "RSAES_OAEP_SHA_256" } },
        });
        expect(sid("SyncFunctionReadsThePublicKey")).toEqual({
            Sid: "SyncFunctionReadsThePublicKey",
            Effect: "Allow",
            Principal: { AWS: syncRole },
            Action: "kms:GetPublicKey",
            Resource: "*",
        });
    });

    test("the account administers the key and holds no cryptographic or grant action on it", () => {
        const admin = theKey(onSynth()).properties.KeyPolicy.Statement.find(
            (st: any) => st.Sid === "AccountAdministersTheKey"
        );
        expect(SynthResult.flatten(admin.Principal.AWS)).toMatch(/:iam::123456789012:root$/);
        const actions = actionsOf(admin);
        expect(actions).toEqual(
            expect.arrayContaining(["kms:PutKeyPolicy", "kms:ScheduleKeyDeletion"])
        );
        for (const action of actions) expect(action).not.toMatch(/\*/);
        for (const forbidden of [
            "kms:Decrypt",
            "kms:Encrypt",
            "kms:ReEncryptFrom",
            "kms:ReEncryptTo",
            "kms:GenerateDataKey",
            "kms:GenerateDataKeyPair",
            "kms:GetPublicKey",
            "kms:CreateGrant",
            "kms:Sign",
        ]) {
            expect(actions).not.toContain(forbidden);
        }
    });

    test("no identity policy grants a cryptographic action on the key", () => {
        const s = onSynth();
        const key = theKey(s);
        const onKey = allGrants(s).filter((g) => g.resource.includes(key.logicalId));
        expect(onKey).toEqual([]);
    });
});

describe("the sync function", () => {
    test("runs one at a time, is not retried and carries only what it reads", () => {
        const s = onSynth();
        const fn = theSync(s);
        expect(fn.properties.ReservedConcurrentExecutions).toBe(1);
        const env = envOf(fn);
        expect(Object.keys(env)).toEqual(
            expect.arrayContaining([
                "EDH_ADMIN_KEY_SECRET_ARN",
                "EDH_CLUSTER_ID",
                "EDH_ROOT_KEY_DOCUMENT_NAME",
                "EDH_ROOT_KEY_DOCUMENT_VERSION",
                "EDH_ROOT_KEY_ENCRYPTION_KEY_ARN",
                "VAMS_RESOURCE_PARAM_PREFIX",
            ])
        );
        expect(env.EDH_CLUSTER_ID).toBe(CLUSTER_ID);
        expect(env.EDH_ADMIN_KEY_SECRET_ARN).toBe(rootKeySecretArn(s));
        // It never calls EDH and never invokes itself.
        expect(env).not.toHaveProperty("EDH_API_ENDPOINT");
        expect(env).not.toHaveProperty(SYNC_ENV);
        const asyncConfig = only(
            s.where(
                "AWS::Lambda::EventInvokeConfig",
                (r) => inEdhStack(r) && r.properties.FunctionName?.Ref === fn.logicalId
            )
        );
        expect(asyncConfig.properties.MaximumRetryAttempts).toBe(0);
    });

    test("runs in the VPC behind the add-on security group", () => {
        const s = onSynth();
        const fn = theSync(s);
        const caller = lambdaByHandler(s, "handlers.addon.edh.edhSessionReconciler.lambda_handler");
        expect(fn.properties.VpcConfig.SecurityGroupIds).toEqual(
            caller.properties.VpcConfig.SecurityGroupIds
        );
    });

    test("an alarm counts the failure outcomes of the metric the function writes", () => {
        const s = onSynth();
        const alarm = alarmOn(s, pythonConstant(BACKEND_SYNC, "METRIC_FAILURES"));
        expect(alarm.properties.Namespace).toBe(pythonConstant(BACKEND_SYNC, "METRIC_NAMESPACE"));
        expect(alarm.properties.MetricName).toBe(pythonConstant(BACKEND_SYNC, "METRIC_FAILURES"));
        expect(alarm.properties.Dimensions).toEqual([
            {
                Name: pythonConstant(BACKEND_SYNC, "METRIC_DIMENSION"),
                Value: { Ref: theSync(s).logicalId },
            },
        ]);
        expect(alarm.properties.Statistic).toBe("Sum");
        expect(alarm.properties.Period).toBe(3600);
        expect(alarm.properties.Threshold).toBe(3);
        expect(alarm.properties.ComparisonOperator).toBe("GreaterThanOrEqualToThreshold");
        expect(alarm.properties.TreatMissingData).toBe("notBreaching");
    });
});

describe("S-2: a run that dies without an outcome line", () => {
    test("raises the Errors alarm on the function itself, which counts timeouts and failed starts", () => {
        const s = onSynth();
        const alarm = alarmOn(s, "Errors");
        expect(alarm.properties.Namespace).toBe("AWS/Lambda");
        expect(alarm.properties.Dimensions).toEqual([
            { Name: "FunctionName", Value: { Ref: theSync(s).logicalId } },
        ]);
        expect(alarm.properties.Statistic).toBe("Sum");
        expect(alarm.properties.Period).toBe(3600);
        expect(alarm.properties.Threshold).toBe(1);
        expect(alarm.properties.ComparisonOperator).toBe("GreaterThanOrEqualToThreshold");
        // An hour without an invocation has no error to report; an invocation that dies is a data point.
        expect(alarm.properties.TreatMissingData).toBe("notBreaching");
        expect(alarm.properties.AlarmActions).toBeUndefined();
    });

    test("both alarm names are stack outputs, to add an action to", () => {
        const s = onSynth();
        const outputs = Object.values(s.templates)
            .flatMap((tpl: any) => Object.keys(tpl.Outputs ?? {}))
            .filter((k) => /^EdhRootKeySync(Failure|Errors)AlarmName$/.test(k));
        expect(outputs.sort()).toEqual([
            "EdhRootKeySyncErrorsAlarmName",
            "EdhRootKeySyncFailureAlarmName",
        ]);
    });
});

describe("A12: the sync role's grants", () => {
    test("SendCommand names the sync's document, and the instances only under EDH's controller tags for this cluster", () => {
        const s = onSynth();
        const sends = withAction(syncGrants(s), "ssm:SendCommand");
        expect(sends).toHaveLength(2);
        const documentArn = `arn:\${AWS::Partition}:ssm:${s.region}:${ACCOUNT}:document/\${${
            theDocument(s).logicalId
        }}`;
        const onDocument = sends.find((g) => g.resource.includes(":document/"));
        expect(onDocument).toEqual({
            action: "ssm:SendCommand",
            resource: documentArn,
            condition: "null",
        });
        const onInstances = sends.find((g) => g !== onDocument) as Grant;
        expect(onInstances.resource).toBe(
            `arn:\${AWS::Partition}:ec2:${s.region}:${ACCOUNT}:instance/*`
        );
        expect(JSON.parse(onInstances.condition)).toEqual({
            StringEquals: {
                "ssm:resourceTag/edh:NodeType": "controller",
                "ssm:resourceTag/aws:cloudformation:stack-name": CLUSTER_ID,
            },
        });
    });

    test("no role holds SendCommand on a `*` resource, on another document, or on instances without the tag condition", () => {
        const s = onSynth();
        const sends = withAction(allGrants(s), "ssm:SendCommand");
        expect(sends.length).toBeGreaterThan(0);
        for (const g of sends) {
            expect(g.resource).not.toBe("*");
            expect(g.resource).toMatch(/:document\/\$\{EdhRootKeySync|:instance\/\*$/);
            if (g.resource.endsWith(":instance/*")) expect(g.condition).toMatch(/resourceTag/);
        }
    });

    test("the only `*` resources are the two reads that accept no resource ARN", () => {
        const s = onSynth();
        const wildcard = syncGrants(s).filter((g) => g.resource === "*");
        expect(wildcard.map((g) => [g.action, g.condition]).sort()).toEqual([
            ["ec2:DescribeInstances", "null"],
            ["ssm:GetCommandInvocation", "null"],
        ]);
    });

    test("ssm:GetParameter reads the controller-id parameter by its exact name, beside the resource-name prefix", () => {
        const s = onSynth();
        const reads = withAction(syncGrants(s), "ssm:GetParameter").map((g) => g.resource);
        const edhReads = reads.filter((r) => r.includes(":parameter/edh/"));
        expect(edhReads).toEqual([
            `arn:\${AWS::Partition}:ssm:${s.region}:${ACCOUNT}:parameter/edh/${CLUSTER_ID}/configuration/ControllerInstanceId`,
        ]);
        for (const other of reads.filter((r) => !r.includes(":parameter/edh/"))) {
            expect(other).toMatch(/:parameter\/vams-t1-commercial\/resourceNames(\/\*)?$/);
        }
    });

    test("the secret is read and written on the root-key secret alone, with no other Secrets Manager action", () => {
        const s = onSynth();
        const secretGrants = syncGrants(s).filter((g) => g.action.startsWith("secretsmanager:"));
        expect(secretGrants.map((g) => [g.action, g.resource, g.condition]).sort()).toEqual([
            ["secretsmanager:GetSecretValue", rootKeySecretArn(s), "null"],
            ["secretsmanager:PutSecretValue", rootKeySecretArn(s), "null"],
        ]);
    });

    test("KMS: the secret's key only through Secrets Manager for that secret, and no other KMS key but the storage key", () => {
        const s = onSynth();
        const kmsGrants = syncGrants(s).filter((g) => g.action.startsWith("kms:"));
        const viaSecretsManager = kmsGrants.filter((g) => g.condition.includes("SecretARN"));
        expect(viaSecretsManager.map((g) => g.action).sort()).toEqual([
            "kms:Decrypt",
            "kms:GenerateDataKey",
        ]);
        for (const g of viaSecretsManager) {
            expect(g.resource).toBe(`arn:\${AWS::Partition}:kms:${s.region}:${ACCOUNT}:key/*`);
            expect(JSON.parse(g.condition)).toEqual({
                StringEquals: {
                    "kms:ViaService": `secretsmanager.${s.region}.amazonaws.com`,
                    "kms:EncryptionContext:SecretARN": rootKeySecretArn(s),
                },
            });
        }
        // The rest is the storage key grant every VAMS Lambda carries for the CMK-encrypted tables.
        const others = kmsGrants.filter((g) => !g.condition.includes("SecretARN"));
        for (const g of others) {
            expect(g.resource).not.toMatch(/\*$/);
            expect(g.resource).not.toContain(theKey(s).logicalId);
        }
    });

    test("the rate limit is one conditional write on the one reserved row of the session table", () => {
        const s = onSynth();
        const dynamo = syncGrants(s).filter((g) => g.action.startsWith("dynamodb:"));
        expect(dynamo.map((g) => g.action)).toEqual(["dynamodb:UpdateItem"]);
        expect(JSON.parse(dynamo[0].condition)).toEqual({
            "ForAllValues:StringEquals": { "dynamodb:LeadingKeys": [LOCK_KEY] },
        });
        const statement = statementsOf(s, theSync(s)).find((st) =>
            actionsOf(st).includes("dynamodb:UpdateItem")
        );
        const table = resolveReference(s, theSync(s).stack, statement.Resource);
        expect(table?.type).toBe("AWS::DynamoDB::Table");
        expect(table?.logicalId).toMatch(/^EdhSessionStorageTable/);
    });

    test("the role holds no action beyond those above, logging and the VPC network interface aside", () => {
        const s = onSynth();
        const actions = [...new Set(syncGrants(s).map((g) => g.action))].sort();
        const allowed = new Set([
            "ssm:SendCommand",
            "ssm:GetCommandInvocation",
            "ssm:GetParameter",
            "ssm:GetParameters",
            "ssm:GetParametersByPath",
            "ec2:DescribeInstances",
            "secretsmanager:GetSecretValue",
            "secretsmanager:PutSecretValue",
            "dynamodb:UpdateItem",
        ]);
        expect(actions.filter((a) => !allowed.has(a) && !a.startsWith("kms:"))).toEqual([]);
    });

    test("no other role may write the root-key secret", () => {
        const s = onSynth();
        const writers = edhCallers(s).filter((fn) =>
            withAction(grantsOf(s, fn), "secretsmanager:PutSecretValue").some(
                (g) => g.resource === rootKeySecretArn(s)
            )
        );
        expect(writers).toEqual([]);
    });
});

describe("A12: the trigger", () => {
    test("every add-on Lambda that calls EDH names the sync function and may invoke it", () => {
        const s = onSynth();
        const sync = theSync(s);
        const callers = edhCallers(s);
        expect(callers.length).toBeGreaterThanOrEqual(5);
        for (const fn of callers) {
            expect([handlerOf(fn), envOf(fn)[SYNC_ENV]]).toEqual([
                handlerOf(fn),
                { Ref: sync.logicalId },
            ]);
            const invokes = withAction(grantsOf(s, fn), "lambda:InvokeFunction").filter((g) =>
                g.resource.includes(sync.logicalId)
            );
            expect([handlerOf(fn), invokes.length]).toEqual([handlerOf(fn), 2]);
        }
    });

    test("no role outside the add-on's EDH callers may invoke the sync function", () => {
        const s = onSynth();
        const sync = theSync(s);
        const callerRoles = new Set(edhCallers(s).map((fn) => fn.properties.Role["Fn::GetAtt"][0]));
        const outside = s
            .ofType("AWS::IAM::Policy")
            .filter((p) => JSON.stringify(p.properties.PolicyDocument).includes(sync.logicalId))
            .filter(
                (p) =>
                    !inEdhStack(p) ||
                    !(p.properties.Roles ?? []).every((ref: any) => callerRoles.has(ref.Ref))
            );
        expect(outside.map((p) => p.logicalId)).toEqual([]);
    });
});

describe("A13: partitions", () => {
    test("GovCloud synthesizes the document, the key, the function and grants in its own partition and Region", () => {
        const s = govSynth();
        expect(s.partition).toBe("aws-us-gov");
        expect(documents(s)).toHaveLength(1);
        expect(keys(s)).toHaveLength(1);
        expect(syncFunctions(s)).toHaveLength(1);
        expect(alarms(s)).toHaveLength(2);
        const sends = withAction(syncGrants(s), "ssm:SendCommand").map((g) => g.resource);
        expect(sends).toContain(`arn:\${AWS::Partition}:ec2:us-gov-west-1:${ACCOUNT}:instance/*`);
        expect(
            withAction(syncGrants(s), "secretsmanager:PutSecretValue").map((g) => g.resource)
        ).toEqual([rootKeySecretArn(s)]);
        expect(rootKeySecretArn(s)).toMatch(/^arn:aws-us-gov:secretsmanager:us-gov-west-1:/);
        const viaService = withAction(syncGrants(s), "kms:GenerateDataKey").map(
            (g) => JSON.parse(g.condition).StringEquals["kms:ViaService"]
        );
        expect(viaService).toEqual(["secretsmanager.us-gov-west-1.amazonaws.com"]);
        expect(
            SynthResult.flatten(theKey(s).properties.KeyPolicy.Statement[0].Principal.AWS)
        ).toMatch(/^arn:(aws-us-gov|\$\{AWS::Partition\}):iam::123456789012:root$/);
    });
});
