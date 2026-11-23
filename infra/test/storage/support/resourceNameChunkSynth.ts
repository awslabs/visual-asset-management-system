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

/** The explicit DependsOn of a chunk, sorted. */
export const dependsOnOf = (chunk: SynthChunk): string[] =>
    ([] as string[]).concat(chunk.resource.raw.DependsOn ?? []).sort();

/** The root-template resources whose outputs any chunk reads (`Fn::GetAtt [<id>, Outputs.*]`). */
export function stacksReadByChunks(chunks: SynthChunk[]): string[] {
    const ids = new Set<string>();
    const visit = (value: unknown): void => {
        if (Array.isArray(value)) value.forEach(visit);
        else if (value && typeof value === "object") {
            const getAtt = (value as any)["Fn::GetAtt"];
            if (Array.isArray(getAtt) && /^Outputs\./.test(String(getAtt[1]))) ids.add(getAtt[0]);
            Object.values(value).forEach(visit);
        }
    };
    chunks.forEach((c) => visit(c.resource.properties.Value));
    return [...ids].sort();
}

/**
 * Resource logical ids of one template that sit on a dependency cycle, following DependsOn and
 * every Ref / Fn::GetAtt / Fn::Sub reference to another resource of the same template. Empty when
 * the template is acyclic.
 */
export function dependencyCycleIn(template: any): string[] {
    const resources: Record<string, any> = template.Resources ?? {};
    const edges = new Map<string, Set<string>>();
    const refsIn = (value: unknown, out: Set<string>): void => {
        if (Array.isArray(value)) value.forEach((v) => refsIn(v, out));
        else if (value && typeof value === "object") {
            const v = value as any;
            if (typeof v.Ref === "string" && v.Ref in resources) out.add(v.Ref);
            if (Array.isArray(v["Fn::GetAtt"]) && v["Fn::GetAtt"][0] in resources)
                out.add(v["Fn::GetAtt"][0]);
            if (typeof v["Fn::Sub"] === "string") {
                for (const m of v["Fn::Sub"].matchAll(/\$\{([A-Za-z0-9]+)[.}]/g))
                    if (m[1] in resources) out.add(m[1]);
            }
            Object.values(v).forEach((x) => refsIn(x, out));
        }
    };
    for (const [id, r] of Object.entries(resources)) {
        const out = new Set<string>(([] as string[]).concat(r.DependsOn ?? []));
        refsIn(r.Properties ?? {}, out);
        edges.set(id, out);
    }
    const state = new Map<string, "open" | "done">();
    const cycle: string[] = [];
    const walk = (id: string, path: string[]): boolean => {
        if (state.get(id) === "done") return false;
        if (state.get(id) === "open") {
            cycle.push(...path.slice(path.indexOf(id)));
            return true;
        }
        state.set(id, "open");
        for (const next of edges.get(id) ?? []) if (walk(next, [...path, id])) return true;
        state.set(id, "done");
        return false;
    };
    for (const id of edges.keys()) if (walk(id, [])) break;
    return cycle;
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
