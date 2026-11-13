/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `app.auditLogging.recordReads` decides whether read actions (gets, lists, searches) write audit
 * records. An absent block or key records reads, so a config.json written before the switch keeps the
 * behaviour after an upgrade; a value that is not a JSON boolean is a configuration error. Every shipped
 * config file carries the switch set to true, and the ConfigBuilder mirrors the default and the rule.
 *
 * CDK hands the value to every Lambda function that resolves VAMS resource names, the audit log
 * groups included, as AUDIT_RECORD_READS. That is every function able to write an audit record:
 * the audit writer finds its log groups through VAMS_RESOURCE_PARAM_PREFIX.
 */

import * as Config from "../../config/config";
import configJson from "../../config/config.json";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { synthTemplate, SynthResult } from "../support/templateSynth";

const RULE_ID = "audit-logging-record-reads-boolean";

function appWith(auditLogging: unknown, omit = false): any {
    const app: any = { webUi: {} };
    if (!omit) app.auditLogging = auditLogging;
    return { app };
}

describe("resolveAuditLoggingConfig", () => {
    test.each([
        ["the block is absent", appWith(undefined, true)],
        ["the block is null", appWith(null)],
        ["the key is absent", appWith({})],
        ["the key is null", appWith({ recordReads: null })],
    ])("records reads when %s", (_label, config) => {
        Config.resolveAuditLoggingConfig(config);
        expect(config.app.auditLogging).toEqual({ recordReads: true });
    });

    test.each([true, false])("keeps an explicit %s", (value) => {
        const config = appWith({ recordReads: value });
        Config.resolveAuditLoggingConfig(config);
        expect(config.app.auditLogging.recordReads).toBe(value);
    });

    test.each(["false", "true", 0, 1, [], {}])("refuses the non-boolean %p", (value) => {
        expect(() => Config.resolveAuditLoggingConfig(appWith({ recordReads: value }))).toThrow(
            /^Configuration Error: app\.auditLogging\.recordReads must be true or false/
        );
    });

    test.each([true, "on", ["x"]])("refuses a block that is not an object: %p", (value) => {
        expect(() => Config.resolveAuditLoggingConfig(appWith(value))).toThrow(
            /^Configuration Error: app\.auditLogging must be an object/
        );
    });
});

describe("the shipped config files and the ConfigBuilder", () => {
    test.each([
        ["config.json", configJson],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s records reads", (_name, file: any) => {
        expect(file.app.auditLogging).toEqual({ recordReads: true });
    });

    test.each(["commercial", "govcloud", "eusovereign"] as const)(
        "the %s preset records reads",
        (profile) => {
            expect((makeDefaultConfig(profile) as any).app.auditLogging).toEqual({
                recordReads: true,
            });
        }
    );

    test("the ConfigBuilder refuses a non-boolean and accepts a boolean or nothing", () => {
        const fired = (value: unknown) => {
            const cfg: any = makeDefaultConfig("commercial");
            if (value === undefined) delete cfg.app.auditLogging;
            else cfg.app.auditLogging.recordReads = value;
            return evaluateRules(cfg).some((rule: any) => rule.id === RULE_ID);
        };
        expect(fired("false")).toBe(true);
        expect(fired(1)).toBe(true);
        expect(fired(true)).toBe(false);
        expect(fired(false)).toBe(false);
        expect(fired(undefined)).toBe(false);
    });
});

function lambdaEnvironments(s: SynthResult): { id: string; env: Record<string, any> }[] {
    return s.ofType("AWS::Lambda::Function").map((fn) => ({
        id: `${fn.stack}/${fn.logicalId}`,
        env: fn.properties.Environment?.Variables ?? {},
    }));
}

describe("CDK passes the switch to every function that writes audit records", () => {
    const shipped = () => synthTemplate("commercial");
    const off = () =>
        synthTemplate("commercial", {
            mutate: (c: any) => (c.app.auditLogging = { recordReads: false }),
            mutateKey: "auditLoggingRecordReadsOff",
        });
    const absent = () =>
        synthTemplate("commercial", {
            mutate: (c: any) => delete c.app.auditLogging,
            mutateKey: "auditLoggingBlockAbsent",
        });

    test("every function that resolves VAMS resource names carries AUDIT_RECORD_READS=true", () => {
        const functions = lambdaEnvironments(shipped()).filter(
            (fn) => fn.env.VAMS_RESOURCE_PARAM_PREFIX !== undefined
        );
        // Control: the filter matched the handler functions, not nothing.
        expect(functions.length).toBeGreaterThan(50);
        const missing = functions.filter((fn) => fn.env.AUDIT_RECORD_READS !== "true");
        expect(missing.map((fn) => fn.id)).toEqual([]);
    });

    test("recordReads false sets the variable to false on the same functions", () => {
        const on = lambdaEnvironments(shipped()).filter(
            (fn) => fn.env.AUDIT_RECORD_READS !== undefined
        );
        const offFns = lambdaEnvironments(off()).filter(
            (fn) => fn.env.AUDIT_RECORD_READS !== undefined
        );
        expect(offFns.map((fn) => fn.id).sort()).toEqual(on.map((fn) => fn.id).sort());
        expect(offFns.every((fn) => fn.env.AUDIT_RECORD_READS === "false")).toBe(true);
    });

    // Values that differ between any two synths of one config: a custom resource's Timestamp, the
    // seed rows' ISO creation times, the asset and nested-template hashes that embed them, and the
    // order of a nested stack's parameters. Keys are sorted before comparing.
    const canonical = (value: any): any =>
        Array.isArray(value)
            ? value.map(canonical)
            : value && typeof value === "object"
            ? Object.fromEntries(
                  Object.keys(value)
                      .sort()
                      .map((key) => [key, canonical(value[key])])
              )
            : value;
    const normalized = (r: any) =>
        JSON.stringify(canonical(r.raw))
            .replace(/"Timestamp":"\d+"/g, '"Timestamp":"<synth time>"')
            .replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z/g, "<iso time>")
            .replace(/[0-9a-f]{64}/g, "<hash>");

    test("a config without the block synthesizes the shipped default", () => {
        const before = new Map(
            shipped().resources.map((r) => [`${r.stack}/${r.logicalId}`, normalized(r)])
        );
        const after = new Map(
            absent().resources.map((r) => [`${r.stack}/${r.logicalId}`, normalized(r)])
        );
        expect([...after.keys()].sort()).toEqual([...before.keys()].sort());
        const differing = [...after.keys()].filter((id) => after.get(id) !== before.get(id));
        expect(differing).toEqual([]);
    });
});
