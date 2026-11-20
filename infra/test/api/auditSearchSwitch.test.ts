/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as Config from "../../config/config";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";

/**
 * `app.auditLogging.search.enabled` (default true; false in the EU Sovereign template, where Amazon
 * CloudWatch Logs Insights is not available) decides whether audit log search is deployed.
 *
 * Off: no ApiBuilder3 nested stack, no audit Lambda, no /audit route in the REST API, no
 * `logs:StartQuery` grant, no query table, no table-name SSM parameter, and no AUDITLOGSEARCH switch in
 * the feature list or the listing Lambdas. The `auditLog` permission object type and the seeded admin
 * constraint stay (core authorization, inert without the routes), and so does the recording of audit
 * events (the nine log groups and the writer grants).
 * On: all of that is present, as before the switch existed.
 */
jest.setTimeout(900_000);

const PARTITIONS: TemplateName[] = ["commercial", "govcloud", "eusovereign"];
const AUDIT_HANDLER = "handlers.audit.auditLogService.lambda_handler";

const setSearch = (enabled: boolean) => (c: any) => {
    c.app.auditLogging.search.enabled = enabled;
};
const synth = (template: TemplateName, enabled: boolean): SynthResult =>
    synthTemplate(template, { mutate: setSearch(enabled), mutateKey: `audit-search-${enabled}` });

const allText = (s: SynthResult): string => JSON.stringify(s.templates);
const hasApiBuilder3 = (s: SynthResult): boolean =>
    Object.keys(s.templates).some((key) => /ApiBuilder3/.test(key));
const auditFunctions = (s: SynthResult) =>
    s.where("AWS::Lambda::Function", (r) => r.properties.Handler === AUDIT_HANDLER);
const queryTables = (s: SynthResult) =>
    s.where("AWS::DynamoDB::Table", (r) => r.logicalId.startsWith("AuditLogQueriesStorageTable"));
const apiPaths = (s: SynthResult): string[] =>
    Object.keys(s.ofType("AWS::ApiGateway::RestApi")[0].properties.Body?.paths ?? {});

describe.each(PARTITIONS)("audit log search switched off (%s)", (template) => {
    const s = synth(template, false);

    test("nothing of the search deployment exists", () => {
        expect(hasApiBuilder3(s)).toBe(false);
        expect(auditFunctions(s)).toHaveLength(0);
        expect(queryTables(s)).toHaveLength(0);
        expect(apiPaths(s).filter((p) => p.startsWith("/audit"))).toEqual([]);
        expect(allText(s)).not.toContain("logs:StartQuery");
        expect(allText(s)).not.toContain("dynamoTables/auditLogQueriesStorage");
        expect(allText(s)).not.toContain("AUDIT_LOG_QUERIES_STORAGE_TABLE_NAME");
    });

    test("the switch is absent from the feature list and the listing Lambdas", () => {
        expect(allText(s)).not.toContain("AUDITLOGSEARCH");
    });

    test("control: the rest of the API is still deployed and the audit log groups still record", () => {
        expect(apiPaths(s)).toContain("/auth/routes/api");
        expect(apiPaths(s).length).toBeGreaterThan(50);
        expect(
            s
                .ofType("AWS::Logs::LogGroup")
                .filter(
                    (r) =>
                        /AuditLogGroup/.test(r.logicalId) && !/OrchestrationBus/.test(r.logicalId)
                )
        ).toHaveLength(9);
        expect(allText(s)).toContain("logs:PutLogEvents");
    });

    test("the auditLog permission object type stays seeded for the admin role", () => {
        expect(allText(s)).toContain("auditLog");
    });
});

describe.each(["commercial", "govcloud"] as TemplateName[])(
    "audit log search switched on (%s)",
    (template) => {
        const s = synth(template, true);

        test("ApiBuilder3, the Lambda, the table, the routes and the grants exist", () => {
            expect(hasApiBuilder3(s)).toBe(true);
            expect(auditFunctions(s)).toHaveLength(1);
            expect(queryTables(s)).toHaveLength(1);
            expect(allText(s)).toContain("logs:StartQuery");
            expect(allText(s)).toContain("dynamoTables/auditLogQueriesStorage");
            expect(apiPaths(s)).toEqual(
                expect.arrayContaining([
                    "/audit/log-groups",
                    "/audit/queries",
                    "/audit/queries/{queryId}",
                    "/audit/queries/{queryId}/export",
                ])
            );
        });

        test("the switch reaches the feature list and the listing Lambdas", () => {
            expect(allText(s)).toContain("AUDITLOGSEARCH");
        });
    }
);

describe("the shipped templates and the partition rule", () => {
    test("commercial and GovCloud ship with search on, EU Sovereign with it off", () => {
        expect(auditFunctions(synthTemplate("commercial"))).toHaveLength(1);
        expect(auditFunctions(synthTemplate("govcloud"))).toHaveLength(1);
        expect(auditFunctions(synthTemplate("eusovereign"))).toHaveLength(0);
    });

    test("the EU Sovereign template with search on is refused at configuration time", () => {
        const config: any = JSON.parse(JSON.stringify(eusovereignTemplate));
        config.env.partition = "aws-eusc";
        config.app.auditLogging.search.enabled = true;
        expect(() => Config.resolveAuditLoggingConfig(config)).toThrow(
            /CloudWatch Logs Insights, which is not available in the 'aws-eusc' partition/
        );
        config.app.auditLogging.search.enabled = false;
        expect(() => Config.resolveAuditLoggingConfig(config)).not.toThrow();
    });

    test("a config without the key behaves as on, except in EU Sovereign where it is off", () => {
        const absent = (c: any) => {
            delete c.app.auditLogging.search.enabled;
        };
        const commercial = synthTemplate("commercial", {
            mutate: absent,
            mutateKey: "audit-absent",
        });
        const eusc = synthTemplate("eusovereign", { mutate: absent, mutateKey: "audit-absent" });
        expect(auditFunctions(commercial)).toHaveLength(1);
        expect(auditFunctions(eusc)).toHaveLength(0);
    });
});

describe("turning the switch off removes only what search added", () => {
    test("the resource set differs by the search stack, its Lambda, table, parameter and routes", () => {
        const on = synth("commercial", true);
        const off = synth("commercial", false);
        const ids = (s: SynthResult, type: string) => s.ofType(type).map((r) => r.logicalId);
        // The three audit-search types that vanish.
        expect(
            ids(on, "AWS::DynamoDB::Table").filter((id) => /AuditLogQueries/.test(id))
        ).toHaveLength(1);
        expect(
            ids(off, "AWS::DynamoDB::Table").filter((id) => /AuditLogQueries/.test(id))
        ).toHaveLength(0);
        // Everything else a table is: unchanged.
        expect(ids(off, "AWS::DynamoDB::Table").length).toBe(
            ids(on, "AWS::DynamoDB::Table").length - 1
        );
    });
});

describe("cdk-nag: audit log search adds no finding in either mode", () => {
    // The CLI synth of GovCloud and EU Sovereign needs AWS lookups, so the harness runs the nag packs
    // on every cell. A finding that exists without this feature (GovCloud and EU Sovereign carry
    // AwsSolutions-COG8 today) is not this feature's: the cells must show no finding on an audit
    // resource and the same findings with the switch on and off. The EU Sovereign "on" cell is not
    // deployable (getConfig refuses it); it only proves the wiring is clean with that partition's ARNs.
    const AUDIT_RESOURCE = /ApiBuilder3|AuditLogQueries|auditLogService|AuditLogService/;
    const findings = (template: TemplateName, enabled: boolean): string[] =>
        synthTemplate(template, {
            mutate: setSearch(enabled),
            mutateKey: `audit-search-${enabled}`,
            nag: true,
        })
            .errors.map((e) => `${e.path}: ${e.message.slice(0, 120)}`)
            .sort();

    test.each(PARTITIONS)(
        "%s: no finding on an audit resource, and the same findings on and off",
        (template) => {
            const on = findings(template, true);
            const off = findings(template, false);
            expect(on.filter((f) => AUDIT_RESOURCE.test(f))).toEqual([]);
            expect(off.filter((f) => AUDIT_RESOURCE.test(f))).toEqual([]);
            expect(off).toEqual(on);
        }
    );

    test("control: the nag packs ran (the commercial cell reports annotations)", () => {
        const s = synthTemplate("commercial", { nag: true });
        expect(s.warnings.length + s.infos.length + s.errors.length).toBeGreaterThan(0);
    });
});
