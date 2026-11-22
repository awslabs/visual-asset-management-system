/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Reading the resource-name chunk parameters out of a full synth, shared by the chunk suites
 * (`resourceNameChunks.test.ts`, `custom/customResourceNameChunks.test.ts`, the Nag matrix).
 */

import { Resource, SynthResult } from "../../support/templateSynth";

/** CloudFormation per-template quotas (not adjustable). */
export const TEMPLATE_OUTPUT_LIMIT = 200;
export const TEMPLATE_RESOURCE_LIMIT = 500;
export const TEMPLATE_PARAMETER_LIMIT = 200;
export const TEMPLATE_BODY_LIMIT_BYTES = 1_000_000;

export interface SynthChunk {
    index: number;
    resource: Resource;
    stack: string;
    logicalId: string;
    flat: string;
    parsed: any;
    refs: string[];
    tier: string;
}

export const prefixOf = (s: SynthResult): string => `/vams-t1-${s.name}/resourceNames`;

/** The template key of the root core stack (its construct id is the stack name). */
export const rootStackKey = (s: SynthResult): string => `vams-t1-${s.name}`;

/** The chunk parameters of a synth, sorted by index, parsed with every token replaced. */
export function chunksOf(s: SynthResult): SynthChunk[] {
    const chunkPrefix = `${prefixOf(s)}Chunks/`;
    return s
        .ofType("AWS::SSM::Parameter")
        .filter((r) => SynthResult.flatten(r.properties.Name).startsWith(chunkPrefix))
        .map((r) => {
            const flat = SynthResult.flatten(r.properties.Value);
            return {
                index: Number(SynthResult.flatten(r.properties.Name).slice(chunkPrefix.length)),
                resource: r,
                stack: r.stack,
                logicalId: r.logicalId,
                flat,
                parsed: JSON.parse(flat.replace(/\$\{[^}]+\}/g, "token")),
                refs: flat.match(/\$\{[^}]+\}/g) ?? [],
                tier: r.properties.Tier,
            };
        })
        .sort((a, b) => a.index - b.index);
}

/** Which kind of source a chunk's names come from, read from its keys. */
export function sourceOf(chunk: SynthChunk): "core" | "addon" | "custom" | "mixed" {
    const kinds = new Set(
        Object.keys(chunk.parsed.m).map((key) =>
            key.startsWith("addon/") ? "addon" : key.startsWith("custom/") ? "custom" : "core"
        )
    );
    return kinds.size === 1 ? ([...kinds][0] as "core" | "addon" | "custom") : "mixed";
}

/**
 * The raw value of each key in a chunk: the property value that follows `"<key>":"` in the chunk's
 * Fn::Join, or the literal string for a value CDK resolved at synth.
 */
export function rawChunkValues(chunk: SynthChunk): Map<string, unknown> {
    const value = chunk.resource.properties.Value;
    const out = new Map<string, unknown>();
    if (typeof value === "string") {
        for (const [k, v] of Object.entries<string>(JSON.parse(value).m)) out.set(k, v);
        return out;
    }
    const parts: unknown[] = value["Fn::Join"][1];
    // Literal names sit inside the string parts; tokens follow the part that opens their key.
    for (const [k, v] of Object.entries<string>(chunk.parsed.m)) {
        if (v !== "token") out.set(k, v);
    }
    parts.forEach((part, i) => {
        if (typeof part === "string") return;
        const before = parts[i - 1];
        const key = typeof before === "string" ? /"([^"]+)":"$/.exec(before)?.[1] : undefined;
        if (key) out.set(key, part);
    });
    return out;
}

/** Parameter, output and resource counts and the body size of one template. */
export function templateFigures(s: SynthResult, stackKey: string) {
    const tpl = s.templates[stackKey];
    return {
        parameters: Object.keys(tpl.Parameters ?? {}).sort(),
        outputs: Object.keys(tpl.Outputs ?? {}).length,
        resources: Object.keys(tpl.Resources ?? {}).length,
        bytes: Buffer.byteLength(JSON.stringify(tpl)),
    };
}

/**
 * Every chunk is a resource of the root core stack under the construct id `ResourceNameChunk<i>`, so
 * the parameter named `<prefix>Chunks/<i>` belongs to one logical resource of one stack in every
 * configuration and a layout change can only update it in place. Returns the chunks.
 */
export function expectChunksInRootStack(s: SynthResult): SynthChunk[] {
    const chunks = chunksOf(s);
    expect(chunks.length).toBeGreaterThan(0);
    const root = rootStackKey(s);
    expect(s.templates[root]).toBeDefined();
    for (const chunk of chunks) {
        expect({ index: chunk.index, stack: chunk.stack }).toEqual({
            index: chunk.index,
            stack: root,
        });
        expect(chunk.logicalId).toMatch(new RegExp(`^ResourceNameChunk${chunk.index}[0-9A-F]{8}$`));
    }
    // No nested template holds a chunk under any construct id.
    const elsewhere = s.resources.filter(
        (r) => r.stack !== root && /^ResourceNameChunk\d+/.test(r.logicalId)
    );
    expect(elsewhere).toEqual([]);
    return chunks;
}

/**
 * The root template takes no CloudFormation parameter beyond CDK's bootstrap version (the chunks
 * read nested values through nested-stack outputs, never through parameters), and stays within the
 * per-template quotas.
 */
export function expectRootTemplateWithinLimits(s: SynthResult): void {
    const figures = templateFigures(s, rootStackKey(s));
    expect(figures.parameters).toEqual(["BootstrapVersion"]);
    expect(figures.outputs).toBeLessThanOrEqual(TEMPLATE_OUTPUT_LIMIT);
    expect(figures.resources).toBeLessThanOrEqual(TEMPLATE_RESOURCE_LIMIT);
    expect(figures.bytes).toBeLessThan(TEMPLATE_BODY_LIMIT_BYTES);
}
