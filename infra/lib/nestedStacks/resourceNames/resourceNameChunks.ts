/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Resource-name chunk parameters. Core-owned.
 *
 * Every registered resource name (the core registry, plus the add-on and custom names recorded by
 * publishLaneResourceName()) is published a second time as JSON in numbered String parameters
 * `<resourceNamesSSMParamPrefix>Chunks/<i>`, i = 0..n-1:
 *
 *     {"v":1,"n":<n>,"i":<i>,"l":"<layout id>","m":{"<key>":"<name>",...}}
 *
 * so a Lambda cold start reads all of them with one GetParameters call per 10 chunks
 * (backend/backend/common/resourceNames.py) instead of paging the prefix 10 parameters at a time.
 * The path is a sibling of the prefix, so the prefix sweep and the deployment tooling never see the
 * chunks, and the per-key parameters stay published for them, for external bridge stacks and for
 * rollback.
 *
 * - Every chunk is a resource of the root core stack, reading nested values through the nested-stack
 *   outputs CDK generates (the storage outputs ResourceNamesBuilder already reads, plus one per lane
 *   value). A chunk's parameter name is its index, and the index of a later chunk moves whenever an
 *   earlier source gains or loses a chunk (an add-on turned on, more core names), so a chunk built in
 *   the stack that owns its names would move between stacks; CloudFormation creates the new
 *   resource before the old stack deletes its copy, and the create fails with ParameterAlreadyExists.
 *   In one stack the construct id is the index, so a layout change only updates a chunk in place,
 *   adds the highest one or removes it.
 * - Names are packed per source (the core registry, then each lane stack in the order it first
 *   published), so toggling an add-on adds or removes that source's chunks and leaves the others'
 *   contents as they were.
 * - Values are deploy-time tokens, so chunks are sized by the longest each value can be: the
 *   descriptor's `maxLength`, the literal's own length, or the limit of the key's category
 *   (TOKEN_MAX_LENGTH_BY_CATEGORY; UNKNOWN_TOKEN_MAX_LENGTH otherwise). Every chunk's worst case
 *   stays within the 4,096-character standard-tier limit.
 * - The layout id is a hash of n and the ordered keys of every chunk, so a reader that meets chunks
 *   of two layouts (a deployment mid-update) can tell, and reads the prefix instead.
 */

import { Annotations, Stack, Token } from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { createHash } from "crypto";
import * as Config from "../../../config/config";
import { ResourceNameRegistry } from "./resourceNameRegistry";

/** The `v` every chunk carries; backend CHUNK_FORMAT_VERSION. */
export const RESOURCE_NAME_CHUNK_FORMAT_VERSION = 1;

/** Standard-tier parameter value limit. */
export const RESOURCE_NAME_CHUNK_MAX_CHARS = 4096;

/** Most chunks a deployment may publish; backend CHUNK_COUNT_LIMIT, above which a reader refuses. */
export const RESOURCE_NAME_CHUNK_COUNT_LIMIT = 100;

/** Longest a deploy-time value can be, by the key segment naming its category. */
export const TOKEN_MAX_LENGTH_BY_CATEGORY: Readonly<Record<string, number>> = {
    dynamoTables: 255, // DynamoDB table name
    s3Buckets: 63, // S3 bucket name
    cloudwatchLogGroups: 512, // CloudWatch Logs log group name
};

/** A deploy-time value under any other category, which may be an ARN (2,048 characters at most). */
export const UNKNOWN_TOKEN_MAX_LENGTH = 2048;

/** Characters an SSM parameter name allows, which JSON carries without escaping. */
const SSM_KEY_PATTERN = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*$/;

const LAYOUT_ID_LENGTH = 12;

/** Header with the widest count and index the count limit allows, for sizing before numbering. */
const HEADER_RESERVE = header(99999, 99999, "x".repeat(LAYOUT_ID_LENGTH)).length;

export interface ResourceNameChunkEntry {
    key: string;
    value: string;
    maxLength?: number;
}

export interface ResourceNameChunkGroup {
    /** The stack holding the per-key parameters of the group's names; packing never mixes sources. */
    source: Stack;
    entries: ResourceNameChunkEntry[];
}

export interface PlannedResourceNameChunk {
    index: number;
    source: Stack;
    entries: ResourceNameChunkEntry[];
    /** Longest the rendered value can be once every token is resolved. */
    worstCaseChars: number;
}

export interface ResourceNameChunkPlan {
    count: number;
    layoutId: string;
    chunks: PlannedResourceNameChunk[];
}

/** The chunk parameter path: a sibling of the resource-name prefix. */
export function resourceNameChunkPrefix(config: Config.Config): string {
    return `${config.resourceNamesSSMParamPrefix}Chunks`;
}

function header(count: number, index: number, layoutId: string): string {
    return `{"v":${RESOURCE_NAME_CHUNK_FORMAT_VERSION},"n":${count},"i":${index},"l":"${layoutId}","m":{`;
}

/** Longest `"<value>"` can be in a chunk once resolved, quotes included. */
export function worstCaseValueChars(entry: ResourceNameChunkEntry): number {
    if (!Token.isUnresolved(entry.value)) {
        return JSON.stringify(entry.value).length;
    }
    if (entry.maxLength !== undefined) {
        return entry.maxLength + 2;
    }
    const categories = entry.key.split("/").slice(0, -1);
    const category = categories.find((segment) => segment in TOKEN_MAX_LENGTH_BY_CATEGORY);
    return (
        (category ? TOKEN_MAX_LENGTH_BY_CATEGORY[category] : UNKNOWN_TOKEN_MAX_LENGTH) + '""'.length
    );
}

/** Longest `"<key>":"<value>",` can be. */
function worstCaseEntryChars(entry: ResourceNameChunkEntry): number {
    return JSON.stringify(entry.key).length + ":".length + worstCaseValueChars(entry) + ",".length;
}

function worstCaseChunkChars(entries: ResourceNameChunkEntry[], headerChars: number): number {
    const body = entries.reduce((sum, entry) => sum + worstCaseEntryChars(entry), 0);
    // The last entry has no comma; the map and the object close with "}}".
    return headerChars + body - (entries.length > 0 ? 1 : 0) + "}}".length;
}

/**
 * Pack each group into chunks, in order, starting a new chunk when the next name would not fit;
 * number the chunks 0..n-1 across groups; compute the layout id. Throws when a key cannot be
 * carried, a single name cannot fit a chunk, the count passes RESOURCE_NAME_CHUNK_COUNT_LIMIT, or a
 * planned chunk's worst case passes RESOURCE_NAME_CHUNK_MAX_CHARS.
 */
export function planResourceNameChunks(groups: ResourceNameChunkGroup[]): ResourceNameChunkPlan {
    const packed: { source: Stack; entries: ResourceNameChunkEntry[] }[] = [];
    for (const group of groups) {
        let current: ResourceNameChunkEntry[] = [];
        for (const entry of group.entries) {
            if (!SSM_KEY_PATTERN.test(entry.key)) {
                throw new Error(
                    `Resource-name chunks: key "${entry.key}" is not a valid SSM parameter path`
                );
            }
            const alone = worstCaseChunkChars([entry], HEADER_RESERVE);
            if (alone > RESOURCE_NAME_CHUNK_MAX_CHARS) {
                throw new Error(
                    `Resource-name chunks: "${entry.key}" can be ${worstCaseValueChars(entry)} ` +
                        `characters, which no ${RESOURCE_NAME_CHUNK_MAX_CHARS}-character chunk holds`
                );
            }
            const next = [...current, entry];
            if (
                current.length > 0 &&
                worstCaseChunkChars(next, HEADER_RESERVE) > RESOURCE_NAME_CHUNK_MAX_CHARS
            ) {
                packed.push({ source: group.source, entries: current });
                current = [entry];
            } else {
                current = next;
            }
        }
        if (current.length > 0) {
            packed.push({ source: group.source, entries: current });
        }
    }

    const count = packed.length;
    if (count > RESOURCE_NAME_CHUNK_COUNT_LIMIT) {
        throw new Error(
            `Resource-name chunks: ${count} chunks exceed the ${RESOURCE_NAME_CHUNK_COUNT_LIMIT} ` +
                `the backend reads`
        );
    }
    const layoutId = createHash("sha256")
        .update(JSON.stringify([count, packed.map((chunk) => chunk.entries.map((e) => e.key))]))
        .digest("hex")
        .slice(0, LAYOUT_ID_LENGTH);

    const chunks = packed.map((chunk, index) => ({
        index,
        source: chunk.source,
        entries: chunk.entries,
        worstCaseChars: worstCaseChunkChars(chunk.entries, header(count, index, layoutId).length),
    }));
    chunks.forEach((chunk, position) => {
        if (chunk.index !== position) {
            throw new Error(`Resource-name chunks: chunk ${chunk.index} is out of sequence`);
        }
        if (chunk.worstCaseChars > RESOURCE_NAME_CHUNK_MAX_CHARS) {
            throw new Error(
                `Resource-name chunks: chunk ${chunk.index} can reach ${chunk.worstCaseChars} ` +
                    `characters, over the ${RESOURCE_NAME_CHUNK_MAX_CHARS}-character limit`
            );
        }
    });
    return { count, layoutId, chunks };
}

/** The chunk's parameter value: literal JSON joined with the value tokens. */
export function renderResourceNameChunk(
    plan: ResourceNameChunkPlan,
    chunk: PlannedResourceNameChunk
): string {
    const entries = chunk.entries.map((entry) => {
        // A token resolves to an AWS resource name or ARN, which holds no character JSON escapes;
        // a literal is escaped here.
        const value = Token.isUnresolved(entry.value)
            ? `"${entry.value}"`
            : JSON.stringify(entry.value);
        return `${JSON.stringify(entry.key)}:${value}`;
    });
    return `${header(plan.count, chunk.index, plan.layoutId)}${entries.join(",")}}}`;
}

/**
 * Group every registered name by the stack that holds its per-key parameter (core names first, in
 * registration order, then each lane stack in the order it first published), plan the chunks and
 * create one standard-tier String parameter per chunk in `scope`, the root core stack.
 *
 * Call once, after every stack that publishes resource names has been built.
 */
export function buildResourceNameChunks(props: {
    config: Config.Config;
    registry: ResourceNameRegistry;
    /** The root core stack, which holds every chunk. */
    scope: Stack;
    /** The stack that publishes the core per-key parameters (ResourceNamesBuilderNestedStack). */
    coreSource: Stack;
}): ResourceNameChunkPlan {
    if (props.scope.nested) {
        throw new Error(
            "Resource-name chunks: build them in the root core stack, not in a nested stack, so " +
                "a layout change never moves a chunk between stacks"
        );
    }
    const groups: ResourceNameChunkGroup[] = [
        {
            source: props.coreSource,
            entries: props.registry.list().map((d) => ({
                key: d.paramKey,
                value: d.value,
                maxLength: d.maxLength,
            })),
        },
    ];
    for (const lane of props.registry.laneEntries()) {
        if (Token.isUnresolved(lane.paramKey)) {
            // A key only known at deploy time cannot be sized or named here; its per-key
            // parameter still answers the backend, one GetParameter per container.
            Annotations.of(lane.owner).addWarningV2(
                "@vams/resourceNameChunks:tokenKey",
                `A ${lane.lane} resource name with a deploy-time key is published per key only`
            );
            continue;
        }
        let group = groups.find((g) => g.source === lane.owner);
        if (!group) {
            group = { source: lane.owner, entries: [] };
            groups.push(group);
        }
        group.entries.push({ key: lane.paramKey, value: lane.value });
    }

    const plan = planResourceNameChunks(groups.filter((g) => g.entries.length > 0));
    const prefix = resourceNameChunkPrefix(props.config);
    for (const chunk of plan.chunks) {
        new ssm.StringParameter(props.scope, `ResourceNameChunk${chunk.index}`, {
            parameterName: `${prefix}/${chunk.index}`,
            stringValue: renderResourceNameChunk(plan, chunk),
            tier: ssm.ParameterTier.STANDARD,
        });
    }
    return plan;
}
