/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris Spatial Streaming add-on, synthesized from the commercial template three ways: off, in
 * static mode with the upload pipeline (shared with mirisUploadVpcPlacement), and in perView mode
 * with the deployment CMK and no upload pipeline.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";
import {
    MIRIS_TEST_SECRET_ARN as SECRET_ARN,
    MIRIS_TEST_VIEWER_KEY as PLACEHOLDER_VIEWER_KEY,
    mirisStaticWithUpload,
} from "./mirisUploadHarness";

const STATUS_ROUTE = "/addon/miris/asset-status";
const VIEWER_KEY_ROUTE = "/addon/miris/viewer-key";
const STATUS_HANDLER = "handlers.addon.miris.getAssetStatus.lambda_handler";
const MINT_HANDLER = "handlers.addon.miris.mintViewerKey.lambda_handler";
const CLEANUP_HANDLER = "handlers.addon.miris.viewerKeyCleanup.lambda_handler";
const UPLOAD_EXECUTE_HANDLER = "vamsExecuteMirisUpload.lambda_handler";
const UPLOAD_GATE_HANDLER = "mirisUploadGate.lambda_handler";
const COMPLIANCE_TABLE = /ComplianceAssetStateStorageTable/;
const PARAM_SUFFIX = "/resourceNames/addon/dynamoTables/mirisViewerKeyStorage";
const SCHEMA_ID = "miris-stream-file-schema";
const WRITE_ACTION = /^dynamodb:(PutItem|UpdateItem|DeleteItem|BatchWriteItem|\*)$/;
const VIEWER_KEY_TABLE = "MirisViewerKeyStorageTable";
const METRICS_NAMESPACE = expect.stringMatching(/^VAMS\/[A-Za-z0-9-]+\/Miris$/);

function perViewWithKms(c: any) {
    c.app.webUi.allowUnsafeEvalFeatures = true;
    c.app.useKmsCmkEncryption.enabled = true;
    Object.assign(c.app.addons.useMirisStreaming, {
        enabled: true,
        viewerKeyMode: "perView",
        viewerKeyTtlMinutes: 7,
        apiKeySecretArn: SECRET_ARN,
    });
}

function restApiPaths(synth: SynthResult): Record<string, Record<string, unknown>> {
    const apis = synth.ofType("AWS::ApiGateway::RestApi");
    expect(apis).toHaveLength(1);
    return (apis[0].properties as any).Body.paths;
}

const functionsWithHandler = (synth: SynthResult, handler: string): Resource[] =>
    synth.where("AWS::Lambda::Function", (r) => r.properties.Handler === handler);

function onlyFunction(synth: SynthResult, handler: string): Resource {
    const fns = functionsWithHandler(synth, handler);
    expect(fns).toHaveLength(1);
    return fns[0];
}

/** The Miris variables a Lambda carries; the shared helpers add their own beside them. */
function mirisEnvironment(fn: Resource): Record<string, string> {
    const variables: Record<string, string> = fn.properties.Environment?.Variables ?? {};
    return Object.fromEntries(Object.entries(variables).filter(([k]) => k.startsWith("MIRIS_")));
}

/** Every statement on a role: its inline Policies and any AWS::IAM::Policy naming it. */
function roleStatements(synth: SynthResult, stack: string, roleId: string): any[] {
    const [role] = synth.where(
        "AWS::IAM::Role",
        (r) => r.stack === stack && r.logicalId === roleId
    );
    return [
        ...(role?.properties.Policies ?? []).flatMap((p: any) => p.PolicyDocument.Statement),
        ...synth
            .where(
                "AWS::IAM::Policy",
                (p) =>
                    p.stack === stack &&
                    (p.properties.Roles ?? []).some((r: any) => r?.Ref === roleId)
            )
            .flatMap((p) => p.properties.PolicyDocument.Statement as any[]),
    ];
}

const functionStatements = (synth: SynthResult, handler: string): any[] => {
    const fn = onlyFunction(synth, handler);
    return roleStatements(synth, fn.stack, fn.properties.Role["Fn::GetAtt"][0]);
};

const actionsOf = (statement: any): string[] => [statement.Action].flat();
const writes = (statements: any[]) =>
    statements.flatMap(actionsOf).filter((a) => WRITE_ACTION.test(a));

/** The Secrets Manager grants are GetSecretValue on exactly `resources`. */
function expectExactSecretGrant(statements: any[], resources: unknown[] = [SECRET_ARN]) {
    const secrets = statements.filter((s) =>
        actionsOf(s).some((a) => a.toLowerCase().startsWith("secretsmanager:"))
    );
    for (const statement of secrets) {
        expect(statement.Effect).toBe("Allow");
        expect(actionsOf(statement)).toEqual(["secretsmanager:GetSecretValue"]);
    }
    const granted = secrets.flatMap((s) => [s.Resource].flat());
    expect(granted).toHaveLength(resources.length);
    expect(granted).toEqual(expect.arrayContaining(resources));
}

/** The secret VAMS creates for the static viewer key. */
const viewerKeySecrets = (synth: SynthResult): Resource[] =>
    synth.where("AWS::SecretsManager::Secret", (r) =>
        r.logicalId.startsWith("MirisViewerKeySecret")
    );

const viewerKeyTables = (synth: SynthResult): Resource[] =>
    synth.where("AWS::DynamoDB::Table", (r) =>
        (r.properties.KeySchema ?? []).some((k: any) => k.AttributeName === "mirisViewerKeyId")
    );

const viewerKeyParams = (synth: SynthResult): Resource[] =>
    synth.where("AWS::SSM::Parameter", (r) =>
        SynthResult.flatten(r.properties.Name).endsWith(PARAM_SUFFIX)
    );

const createCall = (resource: Resource): any =>
    JSON.parse(SynthResult.flatten(resource.properties.Create));

/** The read-only role's seeded constraint item whose id contains `constraintSuffix`. */
function roleConstraintCriteria(synth: SynthResult, constraintSuffix: string): any[] {
    const items = synth
        .ofType("Custom::AWS")
        .map((r) => createCall(r).parameters?.Item)
        .filter((item) => item?.constraintId?.S?.includes(`_basicro_${constraintSuffix}`));
    expect(items).toHaveLength(1);
    return JSON.parse(items[0].criteriaOr.S);
}

/** Whether the feature-switch custom resource records `feature`. */
const recordsFeature = (synth: SynthResult, feature: string): boolean =>
    synth
        .ofType("Custom::AWS")
        .some((r) => JSON.stringify(r.properties.Create).includes(`-${feature}\\"`));

/** The add-on's CloudWatch alarms, by logical id prefix. */
const mirisAlarms = (synth: SynthResult): Resource[] =>
    synth.ofType("AWS::CloudWatch::Alarm").filter((r) => r.logicalId.startsWith("Miris"));

/** The auth failure alarm watches the namespace every Miris Lambda publishes to, and has no action. */
function expectAuthFailureAlarm(synth: SynthResult) {
    const alarms = mirisAlarms(synth).filter(
        (a) => a.properties.MetricName === "MirisAuthFailures"
    );
    expect(alarms).toHaveLength(1);
    const [alarm] = alarms;
    const namespaces = [STATUS_HANDLER, MINT_HANDLER, CLEANUP_HANDLER]
        .flatMap((h) => functionsWithHandler(synth, h))
        .map((fn) => mirisEnvironment(fn).MIRIS_METRICS_NAMESPACE);
    expect(namespaces.length).toBeGreaterThanOrEqual(2);
    expect(new Set(namespaces)).toEqual(new Set([alarm.properties.Namespace]));
    expect(alarm.properties).toMatchObject({
        Statistic: "Sum",
        Period: 300,
        Threshold: 0,
        ComparisonOperator: "GreaterThanThreshold",
        EvaluationPeriods: 1,
        TreatMissingData: "notBreaching",
    });
    expect(alarm.properties.Dimensions).toBeUndefined();
    expect(alarm.properties.AlarmActions).toBeUndefined();
}

describe("Miris add-on disabled (stock commercial template)", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    test("no template names Miris, no route is registered and no switch is recorded", () => {
        // Control: the add-on nested stack and the API are synthesized.
        expect(
            synth.where("AWS::Lambda::Function", (r) =>
                r.logicalId.startsWith("AddonNestedStackEmptyLambda")
            )
        ).toHaveLength(1);
        expect(Object.keys(restApiPaths(synth)).length).toBeGreaterThan(50);

        expect(synth.grep(/miris/i)).toEqual([]);
        expect(restApiPaths(synth)[STATUS_ROUTE]).toBeUndefined();
        expect(recordsFeature(synth, "MIRIS_ADDON")).toBe(false);
    });
});

describe("Miris add-on in static mode with the upload pipeline", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: mirisStaticWithUpload,
            mutateKey: "miris-static-upload",
        });
    });

    test("both routes are registered and both switches recorded", () => {
        const paths = restApiPaths(synth);
        const methods = (path: string) =>
            Object.keys(paths[path] ?? {}).filter((m) => m !== "options");
        expect(methods(STATUS_ROUTE)).toEqual(["get"]);
        expect(methods(VIEWER_KEY_ROUTE)).toEqual(["post"]);
        expect(recordsFeature(synth, "MIRIS_ADDON")).toBe(true);
        expect(recordsFeature(synth, "MIRIS_UPLOAD")).toBe(true);
        // Control: the detector reads the same record for a core switch the template enables.
        expect(recordsFeature(synth, "CLOUDFRONTDEPLOY")).toBe(true);
    });

    test("the status and viewer key Lambdas carry the viewer key settings", () => {
        const [secret] = viewerKeySecrets(synth);
        const shared = {
            MIRIS_VIEWER_KEY_MODE: "static",
            MIRIS_VIEWER_KEY_SECRET_ARN: { Ref: secret.logicalId },
            MIRIS_API_KEY_SECRET_ARN: SECRET_ARN,
            MIRIS_API_BASE_URL: "https://app.miris.com",
            MIRIS_METRICS_NAMESPACE: METRICS_NAMESPACE,
        };
        const status = onlyFunction(synth, STATUS_HANDLER);
        expect(mirisEnvironment(status)).toEqual(shared);
        expect(mirisEnvironment(onlyFunction(synth, MINT_HANDLER))).toEqual({
            ...shared,
            MIRIS_VIEWER_KEY_TTL_MINUTES: "7",
        });
        // Table names resolve through SSM, not per-table environment variables.
        const variables = status.properties.Environment.Variables;
        expect(variables.VAMS_RESOURCE_PARAM_PREFIX).toBeDefined();
        expect(Object.keys(variables).filter((k) => k.endsWith("_TABLE_NAME"))).toEqual([]);
    });

    test.each([MINT_HANDLER, UPLOAD_EXECUTE_HANDLER])(
        "with the quarantine block on, %s carries it and reads the compliance asset state",
        (handler) => {
            const variables = onlyFunction(synth, handler).properties.Environment.Variables;
            expect(variables.COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD).toBe("true");
            const reads = JSON.stringify(
                functionStatements(synth, handler).filter((s) =>
                    actionsOf(s).includes("dynamodb:GetItem")
                )
            );
            expect(reads).toMatch(COMPLIANCE_TABLE);
        }
    );

    test("the upload's execute function reads one asset record and queries only the location index", () => {
        const onAssetTable = functionStatements(synth, UPLOAD_EXECUTE_HANDLER).filter((s) =>
            /AssetStorageTable/.test(JSON.stringify(s.Resource))
        );
        const byAction = (action: string) =>
            onAssetTable
                .filter((s) => actionsOf(s).includes(action))
                .flatMap((s) => [s.Resource].flat().map((r: unknown) => JSON.stringify(r)));
        expect([...new Set(onAssetTable.flatMap(actionsOf))].sort()).toEqual([
            "dynamodb:GetItem",
            "dynamodb:Query",
        ]);
        expect(byAction("dynamodb:GetItem").length).toBeGreaterThan(0);
        expect(byAction("dynamodb:GetItem").filter((r) => r.includes("/index/"))).toEqual([]);
        expect(byAction("dynamodb:Query").length).toBeGreaterThan(0);
        for (const resource of byAction("dynamodb:Query")) {
            expect(resource).toContain("/index/AssetLocationGSI");
        }
    });

    test("alarms watch Miris auth failures and the upload's end-rule queue; static mode has no cleanup queue", () => {
        expectAuthFailureAlarm(synth);
        expect(
            mirisAlarms(synth)
                .map((a) => a.properties.AlarmName)
                .sort()
        ).toEqual([
            expect.stringMatching(/-mirisAuthFailures$/),
            expect.stringMatching(/-mirisUploadEndRuleDLQ$/),
        ]);
    });

    test.each([STATUS_HANDLER, MINT_HANDLER])(
        "%s reads exactly the two secrets and writes no DynamoDB data",
        (handler) => {
            const statements = functionStatements(synth, handler);
            const [secret] = viewerKeySecrets(synth);
            expectExactSecretGrant(statements, [SECRET_ARN, { Ref: secret.logicalId }]);
            expect(writes(statements)).toEqual([]);
            const reads = JSON.stringify(
                statements.filter((s) => actionsOf(s).includes("dynamodb:GetItem"))
            );
            expect(reads).toMatch(/AssetStorageTable/);
            expect(reads).toMatch(/AssetFileMetadataStorageTableV2/);
        }
    );

    test("the status Lambda lists the asset buckets and reads only the manifest prefix", () => {
        const s3Statements = functionStatements(synth, STATUS_HANDLER).filter((s) =>
            actionsOf(s).some((a) => a.startsWith("s3:"))
        );
        expect(s3Statements).toHaveLength(2);
        const [listing] = s3Statements.filter((s) => actionsOf(s).includes("s3:ListBucket"));
        // Bucket ARNs only: a listing grant carries no object wildcard.
        expect(actionsOf(listing)).toEqual(["s3:ListBucket"]);
        const buckets = [listing.Resource].flat();
        expect(buckets.length).toBeGreaterThan(0);
        for (const bucket of buckets) {
            expect(SynthResult.flatten(bucket)).not.toContain("/");
            expect(SynthResult.flatten(bucket)).not.toContain("*");
        }
        const [read] = s3Statements.filter((s) => !actionsOf(s).includes("s3:ListBucket"));
        expect(actionsOf(read)).toEqual(["s3:GetObject"]);
        const objects = [read.Resource].flat();
        expect(objects).toHaveLength(1);
        expect(SynthResult.flatten(objects[0])).toMatch(
            /AssetAuxiliaryBucket[A-Za-z0-9]*\.?Arn\W.*\/pipelines\/miris-upload\/manifests\/\*$/
        );
        // The buckets table, for the asset's bucket name and Region.
        const reads = JSON.stringify(
            functionStatements(synth, STATUS_HANDLER).filter((s) =>
                actionsOf(s).includes("dynamodb:Query")
            )
        );
        expect(reads).toMatch(/S3AssetBucketsStorageTable/i);
    });

    test("the execute function writes, and the gate reads, only the run's nested-locations object", () => {
        const nestedObject = (handler: string, action: string) =>
            functionStatements(synth, handler)
                .filter((s) => actionsOf(s).includes(action))
                .flatMap((s) => [s.Resource].flat().map((r: unknown) => SynthResult.flatten(r)))
                .filter((r) => /nested-locations/.test(r));
        const writes = functionStatements(synth, UPLOAD_EXECUTE_HANDLER).filter((s) =>
            actionsOf(s).includes("s3:PutObject")
        );
        expect(writes).toHaveLength(1);
        expect(actionsOf(writes[0])).toEqual(["s3:PutObject"]);
        const [written] = nestedObject(UPLOAD_EXECUTE_HANDLER, "s3:PutObject");
        expect(written).toMatch(
            /AssetAuxiliaryBucket[A-Za-z0-9]*\.?Arn\W.*\/pipelines\/miris-upload\/nested-locations\/\*$/
        );
        expect(nestedObject(UPLOAD_GATE_HANDLER, "s3:GetObject")).toEqual([written]);
    });

    test("the viewer key Lambda touches no S3 bucket", () => {
        const actions = functionStatements(synth, MINT_HANDLER)
            .flatMap(actionsOf)
            .filter((a) => a.startsWith("s3:"));
        expect(actions).toEqual([]);
    });

    test("the viewer key is in a secret filled at deploy time, and in no template or environment", () => {
        const secrets = viewerKeySecrets(synth);
        expect(secrets).toHaveLength(1);
        expect(secrets[0].properties.SecretString).toBeUndefined();
        const populate = synth.where(
            "AWS::CloudFormation::CustomResource",
            (r) =>
                JSON.stringify(r.properties.SecretArn ?? "") ===
                JSON.stringify({ Ref: secrets[0].logicalId })
        );
        expect(populate).toHaveLength(1);
        // Control: the placeholder is a value the synth would carry if it leaked.
        expect(PLACEHOLDER_VIEWER_KEY.length).toBeGreaterThan(8);
        expect(synth.grep(PLACEHOLDER_VIEWER_KEY)).toEqual([]);
    });

    test("no viewer key table or cleanup resource is deployed, and no output holds the key", () => {
        expect(viewerKeyTables(synth)).toEqual([]);
        expect(viewerKeyParams(synth)).toEqual([]);
        expect(synth.grep("MirisViewerKeyCleanup")).toEqual([]);
        const outputs = Object.values(synth.templates).map((t: any) =>
            JSON.stringify(t.Outputs ?? {})
        );
        expect(outputs.filter((o) => o.includes(PLACEHOLDER_VIEWER_KEY))).toEqual([]);
    });

    test("the basic read-only role is seeded with both routes", () => {
        const route = (value: string) =>
            expect.objectContaining({ field: "route__path", operator: "equals", value });
        expect(roleConstraintCriteria(synth, "allow_get_apis")).toContainEqual(route(STATUS_ROUTE));
        expect(roleConstraintCriteria(synth, "allow_post_apis")).toContainEqual(
            route(VIEWER_KEY_ROUTE)
        );
    });

    test("one custom resource seeds the schema, on a role that can only put schema items", () => {
        const seeds = synth
            .ofType("Custom::AWS")
            .filter((r) => createCall(r).parameters?.Item?.metadataSchemaId?.S === SCHEMA_ID);
        expect(seeds).toHaveLength(1);
        expect(seeds[0].properties.Delete).toBeUndefined();

        const [role] = synth.where(
            "AWS::IAM::Role",
            (r) =>
                r.stack === seeds[0].stack &&
                r.logicalId.startsWith("MirisStreamSchemaCustomResourceRole")
        );
        // Control: the seed's provider runs on this role.
        expect(
            synth.where(
                "AWS::Lambda::Function",
                (f) =>
                    f.stack === role.stack &&
                    f.properties.Role?.["Fn::GetAtt"]?.[0] === role.logicalId
            )
        ).toHaveLength(1);
        const statements = roleStatements(synth, role.stack, role.logicalId);
        expect(statements).toHaveLength(1);
        expect(statements[0].Action).toBe("dynamodb:PutItem");
        expect(SynthResult.flatten(statements[0].Resource)).toMatch(/MetadataSchemaStorageTableV2/);
    });
});

describe("Miris add-on in perView mode with the deployment CMK", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: perViewWithKms,
            mutateKey: "miris-addon-perview-kms",
        });
    });

    test("perView mode creates no viewer key secret", () => {
        expect(viewerKeySecrets(synth)).toEqual([]);
    });

    test("with upload off, no upload pipeline is built and MIRIS_UPLOAD is not recorded", () => {
        expect(recordsFeature(synth, "MIRIS_ADDON")).toBe(true);
        expect(recordsFeature(synth, "MIRIS_UPLOAD")).toBe(false);
        expect(synth.grep(/MirisUpload/)).toEqual([]);
    });

    test("each Lambda carries exactly its Miris variables and reads exactly the secret", () => {
        const shared = {
            MIRIS_API_KEY_SECRET_ARN: SECRET_ARN,
            MIRIS_API_BASE_URL: "https://app.miris.com",
            MIRIS_METRICS_NAMESPACE: METRICS_NAMESPACE,
        };
        expect(mirisEnvironment(onlyFunction(synth, STATUS_HANDLER))).toEqual({
            MIRIS_VIEWER_KEY_MODE: "perView",
            ...shared,
        });
        expect(mirisEnvironment(onlyFunction(synth, MINT_HANDLER))).toEqual({
            MIRIS_VIEWER_KEY_MODE: "perView",
            MIRIS_VIEWER_KEY_TTL_MINUTES: "7",
            ...shared,
        });
        expect(mirisEnvironment(onlyFunction(synth, CLEANUP_HANDLER))).toEqual(shared);
        for (const handler of [STATUS_HANDLER, MINT_HANDLER, CLEANUP_HANDLER]) {
            expectExactSecretGrant(functionStatements(synth, handler));
        }
    });

    test("with the quarantine block off, the viewer key Lambda neither carries it nor reads compliance state", () => {
        const fn = onlyFunction(synth, MINT_HANDLER);
        expect(
            fn.properties.Environment.Variables.COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD
        ).toBeUndefined();
        expect(JSON.stringify(functionStatements(synth, MINT_HANDLER))).not.toMatch(
            COMPLIANCE_TABLE
        );
    });

    test("alarms watch Miris auth failures and the viewer key cleanup dead-letter queue", () => {
        expectAuthFailureAlarm(synth);
        const queues = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith("MirisViewerKeyCleanupDLQ"));
        expect(queues).toHaveLength(1);
        const dlqAlarms = mirisAlarms(synth).filter(
            (a) => a.properties.MetricName === "ApproximateNumberOfMessagesVisible"
        );
        expect(dlqAlarms).toHaveLength(1);
        expect(dlqAlarms[0].properties).toMatchObject({
            Namespace: "AWS/SQS",
            Dimensions: [
                { Name: "QueueName", Value: { "Fn::GetAtt": [queues[0].logicalId, "QueueName"] } },
            ],
            Statistic: "Maximum",
            Threshold: 0,
            ComparisonOperator: "GreaterThanThreshold",
            TreatMissingData: "notBreaching",
        });
        expect(dlqAlarms[0].properties.AlarmActions).toBeUndefined();
        expect(mirisAlarms(synth)).toHaveLength(2);
    });

    test("the viewer key table expires and streams keys, is CMK-encrypted and retained", () => {
        const tables = viewerKeyTables(synth);
        expect(tables).toHaveLength(1);
        const [table] = tables;
        expect(table.stack).toMatch(/MirisBuilder/);
        expect(table.properties.TableName).toBeUndefined();
        expect(table.raw.DeletionPolicy).toBe("Retain");
        expect(table.properties).toMatchObject({
            KeySchema: [{ AttributeName: "mirisViewerKeyId", KeyType: "HASH" }],
            TimeToLiveSpecification: { AttributeName: "ttl", Enabled: true },
            StreamSpecification: { StreamViewType: "KEYS_ONLY" },
            SSESpecification: { SSEType: "KMS", KMSMasterKeyId: expect.anything() },
            PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
        });

        const params = viewerKeyParams(synth);
        expect(params).toHaveLength(1);
        expect(params[0].stack).toBe(table.stack);
    });

    test("only the viewer key Lambda writes to the table, and only PutItem", () => {
        const writers = synth
            .ofType("AWS::IAM::Policy")
            .flatMap((p) => p.properties.PolicyDocument.Statement as any[])
            .filter((s) => JSON.stringify(s.Resource).includes(VIEWER_KEY_TABLE))
            .filter((s) => writes([s]).length > 0);
        expect(writers).toHaveLength(1);
        expect(actionsOf(writers[0])).toEqual(["dynamodb:PutItem"]);
        const onTable = functionStatements(synth, MINT_HANDLER).filter((s) =>
            JSON.stringify(s.Resource).includes(VIEWER_KEY_TABLE)
        );
        expect(onTable.flatMap(actionsOf)).toEqual(["dynamodb:PutItem"]);

        const status = functionStatements(synth, STATUS_HANDLER);
        expect(JSON.stringify(status)).not.toContain(VIEWER_KEY_TABLE);
        expect(writes(status)).toEqual([]);

        const cleanup = functionStatements(synth, CLEANUP_HANDLER);
        const stream = cleanup.find((s) => actionsOf(s).includes("dynamodb:GetRecords"));
        expect(JSON.stringify(stream.Resource)).toContain(VIEWER_KEY_TABLE);
        expect(writes(cleanup)).toEqual([]);
    });

    test("the cleanup mapping takes TTL deletions only, bisects, and has a CMK-encrypted DLQ", () => {
        const cleanupIds = functionsWithHandler(synth, CLEANUP_HANDLER).map((f) => f.logicalId);
        const mappings = synth.where("AWS::Lambda::EventSourceMapping", (r) =>
            cleanupIds.includes(r.properties.FunctionName?.Ref)
        );
        expect(mappings).toHaveLength(1);
        const mapping = mappings[0].properties;
        expect(JSON.stringify(mapping.EventSourceArn)).toContain(VIEWER_KEY_TABLE);
        expect(mapping).toMatchObject({
            StartingPosition: "TRIM_HORIZON",
            BatchSize: 5,
            FunctionResponseTypes: ["ReportBatchItemFailures"],
            BisectBatchOnFunctionError: true,
            MaximumRetryAttempts: 10,
        });
        expect(mapping.FilterCriteria.Filters.map((f: any) => JSON.parse(f.Pattern))).toEqual([
            {
                eventName: ["REMOVE"],
                userIdentity: { type: ["Service"], principalId: ["dynamodb.amazonaws.com"] },
            },
        ]);

        const queueId = mapping.DestinationConfig.OnFailure.Destination["Fn::GetAtt"][0];
        const [queue] = synth.where(
            "AWS::SQS::Queue",
            (q) => q.stack === mappings[0].stack && q.logicalId === queueId
        );
        expect(queue.properties.KmsMasterKeyId).toBeDefined();
        expect(queue.properties.MessageRetentionPeriod).toBe(14 * 24 * 60 * 60);
    });

    test("the storage stack takes no parameter from the add-on stacks", () => {
        // A grant that adds an add-on principal to the shared key's resource policy makes the
        // storage stack consume an add-on output, which CloudFormation rejects as circular.
        const storage = synth.where("AWS::CloudFormation::Stack", (r) =>
            r.logicalId.startsWith("StorageResourcesBuilder")
        );
        expect(storage).toHaveLength(1);
        expect(JSON.stringify(storage[0].properties.Parameters ?? {})).not.toMatch(
            /AddonBuilder|MirisBuilder/
        );
    });
});
