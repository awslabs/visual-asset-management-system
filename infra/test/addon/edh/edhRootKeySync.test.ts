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
 * half of an RSA key the sync Lambda generates for each run, and the sync Lambda that runs it, decrypts the
 * ciphertext in memory and writes the key into the existing root-key secret. The suite pins:
 *
 *   * A10: the document, the sync function, its role and the triggers exist when the
 *     field is unset and when it is true; with it false none of them exists and every other resource of the
 *     deployment is the same as with it on, but for the trigger's environment value and invoke grant, a
 *     comparison that holds whatever synths ran before it in the process;
 *   * A11: the document's one parameter is the base64 public key, held to base64 characters, and its one
 *     aws:runShellScript step is the repository script with the configured cluster's key file;
 *   * A12: the sync role's grants, action by action, are exactly the scoped ones, with no `*` resource
 *     but the two reads that accept none; only the triggering roles may invoke the sync;
 *   * S-10: no AWS KMS key, no KMS decrypt path on any role and no KMS endpoint for the sync;
 *   * R3-1, R3-2: the sync carries the base layer and the add-on's own `cryptography` layer, which no other
 *     function carries, which is absent with the sync off, which is bundled as the core layers are, and whose
 *     Poetry project pins exactly `cryptography`, `cffi` and `pycparser`, none of them a base-layer package;
 *   * the sync function runs one at a time without retries and pins the document version;
 *   * no CloudWatch alarm, with the sync on or off (owner decision 2026-11-03): the function's outcome log line
 *     and `RootKeySyncFailures` metric are the backend suite's;
 *   * A13: the GovCloud partition synthesizes the same resources with its own ARNs.
 *
 * The CDK Nag suppressions are held by `edhNag.test.ts`.
 */

import * as crypto from "crypto";
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
    "AWS::Lambda::EventInvokeConfig",
    "AWS::Lambda::LayerVersion",
];
/** The sync's own layer project and the base layer's. */
const SYNC_LAYER_PROJECT = path.join(REPO, "backendAddons", "edh", "rootKeySync", "lambdaLayer");
const BASE_LAYER_PROJECT = path.join(REPO, "backend", "lambdaLayers", "base");
/** The requirements the sync layer exports: the authorizer layer's pins and markers for these three. */
const SYNC_LAYER_REQUIREMENTS = [
    'cffi==2.0.0 ; python_version >= "3.12" and python_version < "4.0" and platform_python_implementation != "PyPy"',
    'cryptography==50.0.1 ; python_version >= "3.12" and python_version < "4.0"',
    'pycparser==2.22 ; python_version >= "3.12" and python_version < "4.0" and platform_python_implementation != "PyPy" and implementation_name != "PyPy"',
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
/** The sync on with the deployment-wide VPC rule on: every Lambda in the VPC. */
const allLambdasInVpcSynth = (): SynthResult =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            enableEdh(c);
            c.app.addons.useEdhWorkstations.edhRootKeyAutoSync = true;
            c.app.useGlobalVpc.useForAllLambdas = true;
        },
        mutateKey: "edhRootKeySyncAllLambdasInVpc",
    });

const documents = (s: SynthResult): Resource[] => s.where("AWS::SSM::Document", inEdhStack);
/** Every AWS KMS key of the add-on's nested template but the session secrets' CMK, which the add-on owns apart. */
const keys = (s: SynthResult): Resource[] =>
    s.where(
        "AWS::KMS::Key",
        (r) => inEdhStack(r) && !r.logicalId.startsWith("EdhSessionSecretsKey")
    );
/** Every CloudWatch alarm in the add-on's nested template, by type and by a text search of each resource. */
const alarms = (s: SynthResult): Resource[] =>
    s.resources.filter(
        (r) =>
            inEdhStack(r) &&
            (r.type === "AWS::CloudWatch::Alarm" ||
                JSON.stringify(r.raw).includes("AWS::CloudWatch::Alarm"))
    );
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

/**
 * A resource's properties as A10 compares them across two synths, with the sync trigger's two traces taken
 * out: the environment value naming the sync function and the statement allowing its invoke.
 *
 * What differs between any two synths is set aside too. A nested stack's template URL is a hash of its
 * template. Several custom resources stamp the synth time, as an ISO string or epoch milliseconds.
 *
 * A nested stack's `Parameters` are compared by name and value, not by key order. CDK adds a cross-stack
 * reference parameter when its reference walk first meets the reference. In a policy that
 * `@aws-cdk/aws-iam:minimizePolicies` merged, the walk meets them in the merged statement's resource
 * order, which CDK sorts as unresolved token strings, so `${Token[TOKEN.10002]}` comes before
 * `${Token[TOKEN.9998]}`. The token numbers come from one counter for the whole process, so the order
 * moves with how many synths ran before in that process. The REST API nested stack's four
 * authorization-table parameters change places in the second full synth of a process, whose tokens run
 * past 10000. CloudFormation reads the map by name, so a deployment gets the same parameters either way.
 */
function comparable(r: Resource, syncLogicalId: string): string {
    const props = JSON.parse(JSON.stringify(r.properties));
    delete props.TemplateURL;
    if (r.type === "AWS::CloudFormation::Stack" && props.Parameters) {
        props.Parameters = Object.fromEntries(
            Object.keys(props.Parameters)
                .sort()
                .map((name) => [name, props.Parameters[name]])
        );
    }
    if (props.Environment?.Variables) delete props.Environment.Variables[SYNC_ENV];
    if (props.PolicyDocument?.Statement) {
        const mentionsSync = (value: unknown) => JSON.stringify(value).includes(syncLogicalId);
        props.PolicyDocument.Statement = props.PolicyDocument.Statement.map((st: any) =>
            Array.isArray(st.Resource)
                ? { ...st, Resource: st.Resource.filter((x: unknown) => !mentionsSync(x)) }
                : st
        ).filter(
            (st: any) =>
                !(Array.isArray(st.Resource) ? st.Resource.length === 0 : mentionsSync(st.Resource))
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
}

/** `stack/logicalId` of every resource both synths carry that does not compare the same. */
function changedBetween(a: SynthResult, b: SynthResult, syncLogicalId: string): string[] {
    const key = (r: Resource) => `${r.stack}/${r.logicalId}`;
    const bByKey = new Map(b.resources.map((r) => [key(r), r]));
    return a.resources
        .filter((r) => bByKey.has(key(r)))
        .filter(
            (r) =>
                comparable(r, syncLogicalId) !==
                comparable(bByKey.get(key(r)) as Resource, syncLogicalId)
        )
        .map(key);
}

describe("A10: the sync is deployed by default", () => {
    test.each([
        ["unset", unsetSynth],
        ["true", onSynth],
    ])(
        "with the field %s the document, function and triggers exist, and no KMS key or alarm",
        (_label, synth) => {
            const s = synth();
            expect(documents(s)).toHaveLength(1);
            expect(keys(s).map((k) => k.logicalId)).toEqual([]);
            expect(syncFunctions(s)).toHaveLength(1);
            expect(alarms(s)).toEqual([]);
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
            ["the sync function", syncFunctions],
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
        // What the sync adds: its resources, and its role and policy.
        const syncOwned = (r: Resource) =>
            r.logicalId.startsWith("EdhRootKeySync") ||
            r.logicalId.startsWith(sync.logicalId.replace(/[0-9A-F]{8}$/, ""));
        const key = (r: Resource) => `${r.stack}/${r.logicalId}`;
        const offKeys = new Set(off.resources.map(key));
        const added = on.resources.filter((r) => !offKeys.has(key(r)));
        expect(added.length).toBeGreaterThan(0);
        // No VPC endpoint either: the sync calls no AWS KMS (edhVpcEndpoints.test.ts).
        expect(added.filter((r) => !(inEdhStack(r) && syncOwned(r))).map(key)).toEqual([]);
        expect([...new Set(added.map((r) => r.type))].sort()).toEqual(
            expect.arrayContaining(SYNC_TYPES)
        );
        const onKeys = new Set(on.resources.map(key));
        expect(off.resources.filter((r) => !onKeys.has(key(r))).map(key)).toEqual([]);

        // A resource both carry is identical once the trigger's two traces are taken out (`comparable`).
        expect(changedBetween(on, off, sync.logicalId)).toEqual([]);
    });

    test("a nested stack's Parameters compare by name and value, whatever their key order", () => {
        const on = onSynth();
        const sync = theSync(on).logicalId;
        const stack = on
            .ofType("AWS::CloudFormation::Stack")
            .find(
                (r) =>
                    r.logicalId.startsWith("RestApiNestedStack") && r.stack === "vams-t1-commercial"
            ) as Resource;
        expect(stack).toBeDefined();
        const parameters: Record<string, unknown> = stack.properties.Parameters;
        const names = Object.keys(parameters);
        expect(names.length).toBeGreaterThan(1);
        const withParameters = (p: Record<string, unknown>): Resource => ({
            ...stack,
            properties: { ...stack.properties, Parameters: p },
        });
        const pick = (keys: string[]) => Object.fromEntries(keys.map((k) => [k, parameters[k]]));
        const reference = comparable(stack, sync);
        expect(comparable(withParameters(pick([...names].reverse())), sync)).toBe(reference);
        // A changed value, a missing name and an extra name still differ.
        for (const p of [
            { ...parameters, [names[0]]: "changed" },
            pick(names.slice(1)),
            { ...parameters, extraParameter: parameters[names[0]] },
        ]) {
            expect(comparable(withParameters(p), sync)).not.toBe(reference);
        }
    });

    test("the comparison holds for the same configuration synthesized again later in the process", () => {
        // The sync-on configuration once more, never from the per-run cache, so it is synthesized here,
        // after this file's earlier synths: its CDK token numbers sit where the earlier synths left the
        // counter, as a later file in a CI shard sees them.
        const on = onSynth();
        const again = synthTemplate("commercial", {
            mutate: (c: any) => {
                enableEdh(c);
                c.app.addons.useEdhWorkstations.edhRootKeyAutoSync = true;
            },
            mutateKey: "edhRootKeySyncOnAgain",
            sharedCache: false,
        });
        const key = (r: Resource) => `${r.stack}/${r.logicalId}`;
        expect(again.resources.map(key)).toEqual(on.resources.map(key));
        expect(changedBetween(on, again, theSync(on).logicalId)).toEqual([]);
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
        expect(parameter.allowedPattern).toBe("^[A-Za-z0-9+/]{1,1000}={0,2}$");
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
        // The public half of an RSA_3072 key, as KMS GetPublicKey returns it (DER SubjectPublicKeyInfo).
        const { publicKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 3072 });
        const spki = publicKey.export({ type: "spki", format: "der" }).toString("base64");
        expect(pattern.test(spki)).toBe(true);
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

    test("the allowed pattern is one Systems Manager accepts: no RE2 repeat count above 1000", () => {
        // CreateDocument rejected {1,2048} ("invalid repeat count"), and CloudFormation rolled the stack back.
        const pattern: string = theDocument(onSynth()).properties.Content.parameters.publicKey
            .allowedPattern;
        const counts = [...pattern.matchAll(/\{(\d+)(?:,(\d*))?\}/g)].flatMap((m) =>
            [m[1], m[2]].filter((n) => n !== undefined && n !== "").map(Number)
        );
        expect(counts.length).toBeGreaterThan(0);
        expect(counts.filter((n) => n > 1000)).toEqual([]);
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

    test("S-4: the sync function holds the SHA-256 of the step it verifies before sending, and the pattern", () => {
        const s = onSynth();
        const lines: string[] = theDocument(s).properties.Content.mainSteps[0].inputs.runCommand;
        const digest = crypto.createHash("sha256").update(lines.join("\n")).digest("hex");
        expect(envOf(theSync(s)).EDH_ROOT_KEY_DOCUMENT_STEP_SHA256).toBe(digest);
        expect(theDocument(s).properties.Content.parameters.publicKey.allowedPattern).toBe(
            pythonConstant(BACKEND_SYNC, "PUBLIC_KEY_ALLOWED_PATTERN")
        );
    });

    test("S-4: the role may describe and read that document only", () => {
        const s = onSynth();
        const documentArn = `arn:\${AWS::Partition}:ssm:${s.region}:${ACCOUNT}:document/\${${
            theDocument(s).logicalId
        }}`;
        for (const action of ["ssm:DescribeDocument", "ssm:GetDocument"]) {
            expect([
                action,
                withAction(allGrants(s), action).map((g) => [g.resource, g.condition]),
            ]).toEqual([action, [[documentArn, "null"]]]);
        }
    });

    test("the sync function runs that document at the version the deployment created", () => {
        const s = onSynth();
        const env = envOf(theSync(s));
        expect(env.EDH_ROOT_KEY_DOCUMENT_NAME).toEqual({ Ref: theDocument(s).logicalId });
        expect(env.EDH_ROOT_KEY_DOCUMENT_VERSION).toBe("1");
    });
});

describe("S-10: the run's own key, no KMS key", () => {
    test("no AWS KMS key and no KMS key variable: the function generates its RSA key in memory for each run", () => {
        const s = onSynth();
        expect(keys(s).map((k) => k.logicalId)).toEqual([]);
        // No RSA key anywhere in the deployment.
        expect(
            s.ofType("AWS::KMS::Key").filter((k) => /^RSA/.test(k.properties.KeySpec ?? ""))
        ).toEqual([]);
        expect(envOf(theSync(s))).not.toHaveProperty("EDH_ROOT_KEY_ENCRYPTION_KEY_ARN");
        expect(Object.keys(envOf(theSync(s))).filter((k) => /KMS|ENCRYPTION_KEY/.test(k))).toEqual(
            []
        );
    });

    test("no role holds a KMS decrypt path the sync's ciphertext could use", () => {
        const s = onSynth();
        // No role may read an RSA public half or decrypt with an unconditioned grant.
        expect(withAction(allGrants(s), "kms:GetPublicKey")).toEqual([]);
        const decrypts = withAction(syncGrants(s), "kms:Decrypt");
        expect(decrypts.length).toBeGreaterThan(0);
        for (const g of decrypts) {
            expect(JSON.parse(g.condition).StringEquals["kms:ViaService"]).toBe(
                `secretsmanager.${s.region}.amazonaws.com`
            );
        }
    });
});

/** The add-on's own layer: the one AWS::Lambda::LayerVersion of the add-on's nested template. */
const syncLayers = (s: SynthResult): Resource[] => s.where("AWS::Lambda::LayerVersion", inEdhStack);
const layerRefsOf = (fn: Resource): string[] =>
    (fn.properties.Layers ?? []).map((l: unknown) => JSON.stringify(l));
/** `[[package]]` names and versions of a poetry.lock. */
const lockedPackages = (lockFile: string): Array<[string, string]> =>
    [
        ...fs
            .readFileSync(lockFile, "utf8")
            .matchAll(/^\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"/gm),
    ].map((m) => [m[1].toLowerCase().replace(/[-_.]+/g, "-"), m[2]]);
/** The `bundling: { ... }` block of a layer construct's source, whitespace collapsed. */
const bundlingBlock = (file: string): string => {
    const text = fs.readFileSync(file, "utf8");
    const start = text.indexOf("bundling: {");
    expect(start).toBeGreaterThan(-1);
    const end = text.indexOf("platform:", start);
    return text.slice(start, text.indexOf("\n", end)).replace(/\s+/g, " ").trim();
};

describe("R3-1, R3-2: the sync's own cryptography layer", () => {
    test("the sync carries exactly its own layer and the base layer, base last", () => {
        const s = onSynth();
        const layer = only(syncLayers(s));
        expect(layer.logicalId).toMatch(/^EdhRootKeySyncLayerCryptographyLayer[0-9A-F]{8}$/);
        const refs = layerRefsOf(theSync(s));
        expect(refs).toEqual([
            JSON.stringify({ Ref: layer.logicalId }),
            expect.stringMatching(/VAMSLayerBase/),
        ]);
        // Neither the authorizer layer nor any layer but these two.
        expect(refs.filter((l) => /VAMSLayerAuthorizer/.test(l))).toEqual([]);
    });

    test("no other function of the deployment carries the sync's layer", () => {
        const s = onSynth();
        const layer = only(syncLayers(s));
        const carriers = s
            .ofType("AWS::Lambda::Function")
            .filter((fn) => JSON.stringify(fn.properties.Layers ?? []).includes(layer.logicalId))
            .map(handlerOf);
        expect(carriers).toEqual([SYNC_HANDLER]);
        // Every other add-on function carries the base layer alone.
        const base = layerRefsOf(theSync(s))[1];
        for (const fn of edhCallers(s)) {
            expect([handlerOf(fn), layerRefsOf(fn)]).toEqual([handlerOf(fn), [base]]);
        }
    });

    test("with the sync off the layer does not exist", () => {
        expectAbsent("the sync's layer with the sync off", syncLayers(offSynth()), {
            description: "the sync's layer with the sync on",
            count: syncLayers(onSynth()).length,
        });
    });

    test("it is built for the sync function's runtime and architecture, and bundled as the core layers are", () => {
        const s = onSynth();
        const layer = only(syncLayers(s));
        expect(layer.properties.CompatibleRuntimes).toEqual([theSync(s).properties.Runtime]);
        // Both default to x86_64; the layer is bundled for linux/amd64, as the core layers are.
        expect(theSync(s).properties.Architectures ?? ["x86_64"]).toEqual(["x86_64"]);
        expect(layer.properties.CompatibleArchitectures ?? ["x86_64"]).toEqual(["x86_64"]);
        const authorizer = only(
            s.where(
                "AWS::Lambda::LayerVersion",
                (r) => r.properties.LayerName === "vams_layer_authorizer"
            )
        );
        expect(layer.properties.CompatibleRuntimes).toEqual(
            authorizer.properties.CompatibleRuntimes
        );
        // The same build image, user, command and platform as the core layer builder.
        const layerSources = path.join(REPO, "infra", "lib", "nestedStacks");
        expect(
            bundlingBlock(
                path.join(
                    layerSources,
                    "addon",
                    "edhWorkstations",
                    "constructs",
                    "edhRootKeySyncLayer-construct.ts"
                )
            )
        ).toBe(
            bundlingBlock(
                path.join(layerSources, "apiLambda", "lambdaLayersBuilder-nestedStack.ts")
            )
        );
    });

    test("its Poetry project pins exactly cryptography, cffi and pycparser at the authorizer layer's versions", () => {
        const requirements = fs
            .readFileSync(path.join(SYNC_LAYER_PROJECT, "requirements.txt"), "utf8")
            .split("\n")
            .filter((line) => line.trim() !== "");
        expect(requirements).toEqual(SYNC_LAYER_REQUIREMENTS);
        expect(lockedPackages(path.join(SYNC_LAYER_PROJECT, "poetry.lock")).sort()).toEqual([
            ["cffi", "2.0.0"],
            ["cryptography", "50.0.1"],
            ["pycparser", "2.22"],
        ]);
        const pyproject = fs.readFileSync(path.join(SYNC_LAYER_PROJECT, "pyproject.toml"), "utf8");
        const deps = pyproject.slice(
            pyproject.indexOf("[tool.poetry.dependencies]"),
            pyproject.indexOf("[build-system]")
        );
        expect(
            deps
                .split("\n")
                .filter((line) => /^[A-Za-z]/.test(line))
                .map((line) => line.split("=")[0].trim())
                .sort()
        ).toEqual(["cffi", "cryptography", "pycparser", "python"]);
        // The layer directory is the Poetry project and nothing else: the bundling copies all of it.
        expect(fs.readdirSync(SYNC_LAYER_PROJECT).sort()).toEqual([
            "poetry.lock",
            "pyproject.toml",
            "requirements.txt",
        ]);
    });

    test("none of its packages is a base-layer package, so neither layer replaces the other's package files or metadata", () => {
        const base = lockedPackages(path.join(BASE_LAYER_PROJECT, "poetry.lock"));
        // Control: the parser reads the base lock.
        expect(base.length).toBeGreaterThan(10);
        const baseNames = new Set(base.map(([name]) => name));
        const shared = lockedPackages(path.join(SYNC_LAYER_PROJECT, "poetry.lock")).filter(
            ([name]) => baseNames.has(name)
        );
        expect(shared).toEqual([]);
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

    test("S54: with useForAllLambdas false it runs outside the VPC, and every other add-on Lambda stays in it", () => {
        const s = onSynth();
        expect(theSync(s).properties.VpcConfig).toBeUndefined();
        const callers = edhCallers(s);
        expect(callers.length).toBeGreaterThan(0);
        for (const fn of callers) {
            expect([handlerOf(fn), fn.properties.VpcConfig === undefined]).toEqual([
                handlerOf(fn),
                false,
            ]);
        }
    });

    test("S54: with useGlobalVpc.enabled and useForAllLambdas it runs in the isolated subnets behind the add-on security group", () => {
        const s = allLambdasInVpcSynth();
        const fn = theSync(s);
        const caller = lambdaByHandler(s, "handlers.addon.edh.edhSessionReconciler.lambda_handler");
        expect(fn.properties.VpcConfig).toBeDefined();
        expect(fn.properties.VpcConfig.SubnetIds).toEqual(caller.properties.VpcConfig.SubnetIds);
        expect(fn.properties.VpcConfig.SecurityGroupIds).toEqual(
            caller.properties.VpcConfig.SecurityGroupIds
        );
        expect(SynthResult.flatten(fn.properties.VpcConfig.SecurityGroupIds)).toContain(
            "EdhAddonLambdaSecurityGroup"
        );
        for (const subnetId of fn.properties.VpcConfig.SubnetIds) {
            const subnet = resolveReference(s, fn.stack, subnetId);
            expect(subnet?.properties.Tags).toEqual(
                expect.arrayContaining([{ Key: "aws-cdk:subnet-type", Value: "Isolated" }])
            );
        }
    });
});

describe("No CloudWatch alarm (owner decision 2026-11-03)", () => {
    test.each([
        ["unset", unsetSynth],
        ["true", onSynth],
        ["false", offSynth],
        ["true in GovCloud", govSynth],
    ])(
        "with the field %s the add-on creates no alarm and names none in an output",
        (_label, synth) => {
            const s = synth();
            expect(alarms(s).map((r) => r.logicalId)).toEqual([]);
            const alarmOutputs = Object.values(s.templates)
                .flatMap((tpl: any) => Object.keys(tpl.Outputs ?? {}))
                .filter((k) => /^EdhRootKeySync.*Alarm/.test(k));
            expect(alarmOutputs).toEqual([]);
        }
    );
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
        // Without the customer managed key there is no other KMS grant (S-5 covers the CMK cell).
        expect(kmsGrants.filter((g) => !g.condition.includes("SecretARN"))).toEqual([]);
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
            "ssm:DescribeDocument",
            "ssm:GetDocument",
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

describe("S-5: the storage key", () => {
    test("GovCloud (customer managed key on): only through DynamoDB, without CreateGrant or ReEncrypt", () => {
        const s = govSynth();
        const kmsGrants = syncGrants(s).filter((g) => g.action.startsWith("kms:"));
        const storageKey = kmsGrants.filter((g) => !g.condition.includes("SecretARN"));
        expect(storageKey.length).toBeGreaterThan(0);
        expect([...new Set(storageKey.map((g) => g.action))].sort()).toEqual([
            "kms:Decrypt",
            "kms:Encrypt",
            "kms:GenerateDataKey",
        ]);
        for (const g of storageKey) {
            expect(JSON.parse(g.condition)).toEqual({
                StringEquals: { "kms:ViaService": "dynamodb.us-gov-west-1.amazonaws.com" },
            });
            expect(g.resource).not.toMatch(/\*$/);
        }
        expect(
            kmsGrants.filter((g) => /CreateGrant|ReEncrypt|ListKeys|ListAliases/.test(g.action))
        ).toEqual([]);
    });

    test("commercial (no customer managed key): no storage key statement", () => {
        const s = onSynth();
        const storageKey = syncGrants(s).filter(
            (g) => g.action.startsWith("kms:") && !g.condition.includes("SecretARN")
        );
        expect(storageKey).toEqual([]);
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
    test("GovCloud synthesizes the document, the function and grants in its own partition and Region, and no KMS key", () => {
        const s = govSynth();
        expect(s.partition).toBe("aws-us-gov");
        expect(documents(s)).toHaveLength(1);
        expect(keys(s).map((k) => k.logicalId)).toEqual([]);
        expect(syncFunctions(s)).toHaveLength(1);
        expect(alarms(s)).toEqual([]);
        const sends = withAction(syncGrants(s), "ssm:SendCommand").map((g) => g.resource);
        expect(sends).toContain(`arn:\${AWS::Partition}:ec2:us-gov-west-1:${ACCOUNT}:instance/*`);
        expect(
            withAction(syncGrants(s), "secretsmanager:PutSecretValue").map((g) => g.resource)
        ).toEqual([rootKeySecretArn(s)]);
        expect(rootKeySecretArn(s)).toMatch(/^arn:aws-us-gov:secretsmanager:us-gov-west-1:/);
        const viaService = withAction(syncGrants(s), "kms:GenerateDataKey")
            .filter((g) => g.condition.includes("SecretARN"))
            .map((g) => JSON.parse(g.condition).StringEquals["kms:ViaService"]);
        expect(viaService).toEqual(["secretsmanager.us-gov-west-1.amazonaws.com"]);
        expect(withAction(allGrants(s), "kms:GetPublicKey")).toEqual([]);
    });
});
