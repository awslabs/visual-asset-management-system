/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { resolvePipelineParams, findUnmatchedTags, missingRequiredTags } from "./resolveTemplate";
import { Pipeline, Template, TagSchemaField } from "../types";

describe("resolveTemplate", () => {
    describe("findUnmatchedTags", () => {
        it("flags an unmatched {{tag}} in the body", () => {
            expect(
                findUnmatchedTags(
                    '{"a":"{{ missing }}"}',
                    new Set(["provided"]),
                    new Set(["executionId"])
                )
            ).toEqual(["missing"]);
        });

        it("does not flag tags that are provided", () => {
            expect(
                findUnmatchedTags(
                    '{"a":"{{ provided }}"}',
                    new Set(["provided"]),
                    new Set(["executionId"])
                )
            ).toEqual([]);
        });

        it("does not flag system tags", () => {
            expect(
                findUnmatchedTags(
                    '{"a":"{{ executionId }}"}',
                    new Set(["provided"]),
                    new Set(["executionId"])
                )
            ).toEqual([]);
        });

        it("does not flag metadata_ prefixed dynamic tags", () => {
            expect(
                findUnmatchedTags(
                    '{"a":"{{ metadata_customField }}"}',
                    new Set([]),
                    new Set(["executionId"])
                )
            ).toEqual([]);
        });

        it("handles multiple tags", () => {
            const body = '{"a":"{{ tag1 }}", "b":"{{ tag2 }}", "c":"{{ tag3 }}"}';
            expect(findUnmatchedTags(body, new Set(["tag1", "tag3"]), new Set())).toEqual(["tag2"]);
        });

        it("handles whitespace in tags", () => {
            expect(findUnmatchedTags('{"a":"{{  spaced  }}"}', new Set([]), new Set())).toEqual([
                "spaced",
            ]);
        });
    });

    describe("missingRequiredTags", () => {
        it("returns empty array when all required tags are provided", () => {
            const schema: TagSchemaField[] = [
                { tagKey: "required1", type: "string", required: true },
                { tagKey: "required2", type: "string", required: true },
            ];
            const tags = [
                { key: "required1", value: "value1" },
                { key: "required2", value: "value2" },
            ];
            expect(missingRequiredTags(schema, tags)).toEqual([]);
        });

        it("returns missing required tags", () => {
            const schema: TagSchemaField[] = [
                { tagKey: "required1", type: "string", required: true },
                { tagKey: "required2", type: "string", required: true },
            ];
            const tags = [{ key: "required1", value: "value1" }];
            expect(missingRequiredTags(schema, tags)).toEqual(["required2"]);
        });

        it("does not flag required tags with defaults", () => {
            const schema: TagSchemaField[] = [
                { tagKey: "required1", type: "string", required: true, default: "defaultValue" },
            ];
            const tags: { key: string; value: any }[] = [];
            expect(missingRequiredTags(schema, tags)).toEqual([]);
        });

        it("flags required tags with empty/null/undefined values", () => {
            const schema: TagSchemaField[] = [
                { tagKey: "required1", type: "string", required: true },
                { tagKey: "required2", type: "string", required: true },
                { tagKey: "required3", type: "string", required: true },
            ];
            const tags = [
                { key: "required1", value: "" },
                { key: "required2", value: null },
                { key: "required3", value: undefined },
            ];
            expect(missingRequiredTags(schema, tags)).toEqual([
                "required1",
                "required2",
                "required3",
            ]);
        });

        it("does not flag optional tags", () => {
            const schema: TagSchemaField[] = [
                { tagKey: "optional1", type: "string", required: false },
                { tagKey: "optional2", type: "string" },
            ];
            const tags: { key: string; value: any }[] = [];
            expect(missingRequiredTags(schema, tags)).toEqual([]);
        });
    });

    describe("resolvePipelineParams", () => {
        it("rejects override when allowCustomTemplateOverride is false", () => {
            const r = resolvePipelineParams({
                pipeline: { systemConfig: { allowCustomTemplateOverride: false } } as any,
                templateId: "t",
                customTemplateOverride: "{}",
                tags: [],
            });
            expect(r.errors.length).toBeGreaterThan(0);
            expect(r.errors.some((e) => /allow/i.test(e))).toBe(true);
        });

        it("rejects template-less override when requireTemplate is true", () => {
            const r = resolvePipelineParams({
                pipeline: {
                    systemConfig: { allowCustomTemplateOverride: true, requireTemplate: true },
                } as any,
                customTemplateOverride: "{}",
                tags: [],
            });
            expect(r.errors.some((e) => /require/i.test(e))).toBe(true);
        });

        it("rejects no-template execution when requireTemplate is true", () => {
            const r = resolvePipelineParams({
                pipeline: { systemConfig: { requireTemplate: true } } as any,
                tags: [],
            });
            expect(r.errors.some((e) => /require/i.test(e))).toBe(true);
        });

        it("rejects customEditedBody when template does not allow custom edit", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                allowCustomEdit: false,
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
                customEditedBody: '{"edited":true}',
            });
            expect(r.errors.some((e) => /allow custom editing/i.test(e))).toBe(true);
        });

        it("case 1: templateId + tags is valid", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(1);
            expect(r.params.templateId).toBe("t");
        });

        it("case 1: detects reserved key collision", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [{ key: "executionId", value: "bad" }],
            });
            expect(r.errors.some((e) => /reserved/i.test(e))).toBe(true);
        });

        it("case 1: detects unmatched tags in template body", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: '{"a":"{{ missing }}"}',
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
            });
            expect(r.errors.some((e) => /unmatched/i.test(e) && /missing/i.test(e))).toBe(true);
        });

        it("case 1: detects missing required tags", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                tagSchema: [{ tagKey: "required1", type: "string", required: true }],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
            });
            expect(r.errors.some((e) => /required/i.test(e) && /required1/i.test(e))).toBe(true);
        });

        it("case 2: templateId + override is valid when allowOverride is true", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: { allowCustomTemplateOverride: true } } as any,
                template,
                templateId: "t",
                tags: [],
                customTemplateOverride: '{"override":true}',
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(2);
            expect(r.params.customTemplateOverride).toBe('{"override":true}');
        });

        it("case 2: validates tags against template schema and unmatched tags against override body", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                tagSchema: [{ tagKey: "required1", type: "string", required: true }],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: { allowCustomTemplateOverride: true } } as any,
                template,
                templateId: "t",
                tags: [{ key: "required1", value: "val" }],
                customTemplateOverride: '{"a":"{{ missing }}"}',
            });
            expect(r.errors.some((e) => /unmatched/i.test(e) && /missing/i.test(e))).toBe(true);
        });

        it("case 3: template-less override is valid when allowOverride and !requireTemplate", () => {
            const r = resolvePipelineParams({
                pipeline: {
                    systemConfig: { allowCustomTemplateOverride: true, requireTemplate: false },
                } as any,
                tags: [],
                customTemplateOverride: '{"override":true}',
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(3);
        });

        it("case 3: detects reserved key collision without schema", () => {
            const r = resolvePipelineParams({
                pipeline: {
                    systemConfig: { allowCustomTemplateOverride: true, requireTemplate: false },
                } as any,
                tags: [{ key: "assetDataObject", value: "bad" }],
                customTemplateOverride: "{}",
            });
            expect(r.errors.some((e) => /reserved/i.test(e))).toBe(true);
        });

        it("case 3: detects unmatched tags in override body", () => {
            const r = resolvePipelineParams({
                pipeline: {
                    systemConfig: { allowCustomTemplateOverride: true, requireTemplate: false },
                } as any,
                tags: [],
                customTemplateOverride: '{"a":"{{ missing }}"}',
            });
            expect(r.errors.some((e) => /unmatched/i.test(e) && /missing/i.test(e))).toBe(true);
        });

        it("case 4: no-template no-override is valid when !requireTemplate", () => {
            const r = resolvePipelineParams({
                pipeline: { systemConfig: { requireTemplate: false } } as any,
                tags: [],
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(4);
        });

        it("case 4: allows tags (system vars only scenario)", () => {
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                tags: [{ key: "customTag", value: "value" }],
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(4);
            expect(r.params.templateTags).toEqual([{ key: "customTag", value: "value" }]);
        });

        it("case 5: allows customEditedBody when template.allowCustomEdit is true", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                allowCustomEdit: true,
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
                customEditedBody: '{"edited":true}',
            });
            expect(r.errors).toEqual([]);
            expect(r.mode).toBe(5);
            expect(r.params.customTemplateOverride).toBe('{"edited":true}');
        });

        it("case 5: validates unmatched tags against edited body", () => {
            const template: Template = {
                databaseId: "db",
                pipelineId: "p",
                templateId: "t",
                templateName: "Test",
                configFormat: "json",
                configBody: "{}",
                allowCustomEdit: true,
                tagSchema: [],
            };
            const r = resolvePipelineParams({
                pipeline: { systemConfig: {} } as any,
                template,
                templateId: "t",
                tags: [],
                customEditedBody: '{"a":"{{ missing }}"}',
            });
            expect(r.errors.some((e) => /unmatched/i.test(e) && /missing/i.test(e))).toBe(true);
        });
    });
});
