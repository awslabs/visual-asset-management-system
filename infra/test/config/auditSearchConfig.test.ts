/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.auditLogging.search.*`: the on/off switch and the limits of audit log search. `enabled` is true
 * when absent (false in aws-eusc, where Amazon CloudWatch Logs Insights is not available; an explicit
 * true there is refused). Each limit takes its default when absent, and a value that is not an
 * integer in range is a configuration error. The ConfigBuilder mirrors the defaults and the rules, and
 * the shipped config files carry the defaults.
 */

import * as Config from "../../config/config";
import configJson from "../../config/config.json";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

function configWith(search: unknown, apiTimeout?: number, partition?: string): any {
    const config: any = {
        env: partition ? { partition } : {},
        app: { auditLogging: { recordReads: true }, api: { apiGatewayRest: {} } },
    };
    if (search !== undefined) config.app.auditLogging.search = search;
    if (apiTimeout !== undefined) config.app.api.apiGatewayRest.apiGatewayTimeoutTime = apiTimeout;
    return config;
}

const resolved = (search: unknown, apiTimeout?: number, partition?: string) => {
    const config = configWith(search, apiTimeout, partition);
    Config.resolveAuditLoggingConfig(config);
    return config.app.auditLogging.search;
};

describe("resolveAuditLoggingConfig: search limits", () => {
    test.each([[undefined], [null], [{}]])("an absent block (%p) takes every default", (search) => {
        expect(resolved(search)).toEqual({ enabled: true, ...Config.AUDIT_SEARCH_DEFAULTS });
    });

    test("enabled is true when absent and keeps an explicit boolean", () => {
        expect(resolved({}).enabled).toBe(true);
        expect(resolved({ enabled: true }, undefined, "aws").enabled).toBe(true);
        expect(resolved({ enabled: false }).enabled).toBe(false);
        expect(resolved({ enabled: null }, undefined, "aws-us-gov").enabled).toBe(true);
    });

    test.each(["true", "false", 1, 0, [], {}])(
        "refuses an enabled that is not a boolean: %p",
        (v) => {
            expect(() => resolved({ enabled: v })).toThrow(
                /^Configuration Error: app\.auditLogging\.search\.enabled must be true or false/
            );
        }
    );

    test("aws-eusc: absent means off, false is accepted, true is refused with the reason", () => {
        expect(resolved({}, undefined, "aws-eusc").enabled).toBe(false);
        expect(resolved(undefined, undefined, "aws-eusc").enabled).toBe(false);
        expect(resolved({ enabled: false }, undefined, "aws-eusc").enabled).toBe(false);
        expect(() => resolved({ enabled: true }, undefined, "aws-eusc")).toThrow(
            /CloudWatch Logs Insights, which is not available in the 'aws-eusc' partition/
        );
    });

    test("the other partitions (including aws-us-gov) accept true", () => {
        for (const partition of ["aws", "aws-us-gov"]) {
            expect(resolved({ enabled: true }, undefined, partition).enabled).toBe(true);
        }
    });

    test("an explicit value is kept and the others default", () => {
        expect(resolved({ maxRows: 2500 })).toEqual({
            enabled: true,
            ...Config.AUDIT_SEARCH_DEFAULTS,
            maxRows: 2500,
        });
    });

    test.each([
        ["maxQueryWindowDays", 0],
        ["maxQueryWindowDays", 367],
        ["maxRows", 10001],
        ["maxRows", 0],
        ["maxConcurrentQueries", 21],
        ["queryDeadlineSeconds", 901],
        ["queryDeadlineSeconds", 9],
        ["maxContainsTermLength", 129],
        ["maxContainsTermLength", 2],
        ["maxContainsTerms", 6],
        ["handleRetentionMinutes", 4],
        ["handleRetentionMinutes", 1441],
        ["handleRetentionMinutes", 0],
        ["handleRetentionMinutes", 60.5],
        ["handleRetentionMinutes", "60"],
        ["maxRows", 1.5],
        ["maxRows", "100"],
        ["maxRows", true],
        ["maxRows", []],
    ])("refuses %s = %p", (key, value) => {
        expect(() => resolved({ [key as string]: value })).toThrow(
            new RegExp(
                `^Configuration Error: app\\.auditLogging\\.search\\.${key} must be an integer`
            )
        );
    });

    test.each([true, "x", 3, ["x"]])(
        "refuses a search block that is not an object: %p",
        (value) => {
            expect(() => resolved(value)).toThrow(/app\.auditLogging\.search must be an object/);
        }
    );

    test("the per-user cap is at most the VAMS-wide cap", () => {
        expect(() => resolved({ maxConcurrentQueries: 3, maxConcurrentQueriesPerUser: 4 })).toThrow(
            /maxConcurrentQueriesPerUser must be an integer from 1 to 3/
        );
        expect(
            resolved({ maxConcurrentQueries: 3, maxConcurrentQueriesPerUser: 3 })
                .maxConcurrentQueriesPerUser
        ).toBe(3);
    });

    test("a lowered VAMS-wide cap lowers the default per-user cap with it", () => {
        expect(resolved({ maxConcurrentQueries: 1 }).maxConcurrentQueriesPerUser).toBe(1);
    });

    test("the long poll stays below the API timeout", () => {
        expect(resolved({}, 29).longPollWaitSeconds).toBe(20);
        expect(() => resolved({ longPollWaitSeconds: 26 }, 300)).toThrow(
            /longPollWaitSeconds must be an integer from 0 to 25/
        );
        expect(() => resolved({ longPollWaitSeconds: 21 }, 20)).toThrow(/from 0 to 16/);
        expect(resolved({ longPollWaitSeconds: 0 }, 29).longPollWaitSeconds).toBe(0);
    });

    test("the handle retention defaults to 60 minutes and accepts 5 to 1440", () => {
        expect(resolved({}).handleRetentionMinutes).toBe(60);
        expect(Config.AUDIT_SEARCH_DEFAULTS.handleRetentionMinutes).toBe(60);
        expect(resolved({ handleRetentionMinutes: 5 }).handleRetentionMinutes).toBe(5);
        expect(resolved({ handleRetentionMinutes: 1440 }).handleRetentionMinutes).toBe(1440);
        expect(resolved({ maxRows: 10 }).handleRetentionMinutes).toBe(60);
    });

    test("a config that lacks the block behaves with the defaults", () => {
        const config: any = { app: { api: { apiGatewayRest: {} } } };
        Config.resolveAuditLoggingConfig(config);
        expect(config.app.auditLogging).toEqual({
            recordReads: true,
            search: { enabled: true, ...Config.AUDIT_SEARCH_DEFAULTS },
        });
    });
});

describe("the shipped config files and the ConfigBuilder", () => {
    test.each([
        ["config.json", configJson],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s carries the defaults", (name, file: any) => {
        expect(file.app.auditLogging.search).toEqual({
            // On everywhere except the EU Sovereign template (no CloudWatch Logs Insights there).
            enabled: !name.includes("eusovereign"),
            ...Config.AUDIT_SEARCH_DEFAULTS,
        });
    });

    test("the ceilings are the ones the Lambda holds", () => {
        expect(Config.AUDIT_SEARCH_CEILINGS.maxRows).toBe(10000);
        expect(Config.AUDIT_SEARCH_CEILINGS.maxQueryWindowDays).toBe(366);
    });

    const fired = (mutate: (cfg: any) => void) => {
        const cfg: any = makeDefaultConfig("commercial");
        mutate(cfg);
        return evaluateRules(cfg)
            .filter((rule: any) => String(rule.id).startsWith("audit-search-"))
            .map((rule: any) => rule.id);
    };

    test("the defaults raise no rule", () => {
        expect(fired(() => undefined)).toEqual([]);
    });

    test("an out-of-range value raises its rule, in line with config.ts", () => {
        expect(fired((c) => (c.app.auditLogging.search.maxRows = 10001))).toEqual([
            "audit-search-maxRows-in-range",
        ]);
        expect(fired((c) => (c.app.auditLogging.search.maxRows = "5"))).toEqual([
            "audit-search-maxRows-in-range",
        ]);
        expect(fired((c) => (c.app.auditLogging.search.maxRows = 10000))).toEqual([]);
        expect(
            fired((c) => {
                c.app.auditLogging.search.maxConcurrentQueries = 3;
                c.app.auditLogging.search.maxConcurrentQueriesPerUser = 4;
            })
        ).toEqual(["audit-search-per-user-cap-within-vams-wide"]);
        expect(fired((c) => (c.app.auditLogging.search.longPollWaitSeconds = 26))).toEqual([
            "audit-search-long-poll-within-api-timeout",
        ]);
    });

    test("the builder presets match: on, except off in the EU Sovereign preset", () => {
        expect((makeDefaultConfig("commercial") as any).app.auditLogging.search.enabled).toBe(true);
        expect((makeDefaultConfig("govcloud") as any).app.auditLogging.search.enabled).toBe(true);
        expect((makeDefaultConfig("eusovereign") as any).app.auditLogging.search.enabled).toBe(
            false
        );
    });

    test("the builder refuses enabled true in the EU Sovereign Cloud and a non-boolean", () => {
        const ids = (mutate: (cfg: any) => void, profile: any = "commercial") => {
            const cfg: any = makeDefaultConfig(profile);
            mutate(cfg);
            return evaluateRules(cfg)
                .filter(
                    (rule: any) =>
                        String(rule.id).startsWith("audit-search-enabled") ||
                        rule.id === "audit-search-not-in-eusc"
                )
                .map((rule: any) => rule.id);
        };
        expect(ids(() => undefined, "eusovereign")).toEqual([]);
        expect(ids((c) => (c.app.auditLogging.search.enabled = true), "eusovereign")).toEqual([
            "audit-search-not-in-eusc",
        ]);
        expect(ids((c) => (c.app.auditLogging.search.enabled = "yes"))).toEqual([
            "audit-search-enabled-boolean",
        ]);
        expect(ids((c) => delete c.app.auditLogging.search.enabled)).toEqual([]);
    });

    test("a missing search block raises nothing", () => {
        expect(fired((c) => delete c.app.auditLogging.search)).toEqual([]);
    });
});
