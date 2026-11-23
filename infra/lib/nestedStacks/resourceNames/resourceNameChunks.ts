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
 * - Values are deploy-time tokens, so chunks are sized by the longest each value can be: a literal's
 *   own length, else the bound of its exact key (TOKEN_MAX_LENGTH_BY_KEY, for ARN-valued keys), else
 *   the limit of the key's category (TOKEN_MAX_LENGTH_BY_CATEGORY), else UNKNOWN_TOKEN_MAX_LENGTH.
 *   Every chunk's worst case stays within the 4,096-character standard-tier limit.
 * - The layout id is a hash of n and the ordered keys of every chunk, so a reader that meets chunks
 *   of two layouts (a deployment mid-update) can tell, and reads the prefix instead.
 * - Every chunk depends on the same nested stacks: each one any chunk reads a value from. A deploy
 *   that changes the layout then updates all chunks together, after the last of those stacks, so
 *   the time readers meet two layouts is the length of a parallel parameter update.
 */

import { Annotations, CfnResource, Reference, Stack, Token, Tokenization } from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { createHash } from "crypto";
import * as Config from "../../../config/config";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";
import { ResourceNameRegistry } from "./resourceNameRegistry";

/** The `v` every chunk carries; backend CHUNK_FORMAT_VERSION. */
export const RESOURCE_NAME_CHUNK_FORMAT_VERSION = 1;

/** Standard-tier parameter value limit. */
export const RESOURCE_NAME_CHUNK_MAX_CHARS = 4096;

/** Most chunks a deployment may publish; backend CHUNK_COUNT_LIMIT, above which a reader refuses. */
export const RESOURCE_NAME_CHUNK_COUNT_LIMIT = 100;

/** Longest a deploy-time value can be, by the key segment naming its category (AWS name limits). */
export const TOKEN_MAX_LENGTH_BY_CATEGORY: Readonly<Record<string, number>> = {
    dynamoTables: 255, // DynamoDB CreateTable TableName: 3-255 characters
    s3Buckets: 63, // S3 bucket name: 3-63 characters
    cloudwatchLogGroups: 512, // CloudWatch Logs CreateLogGroup logGroupName: 1-512 characters
};

/**
 * A deploy-time value under any other category, which may be an ARN: 2,048 characters, the longest
 * ARN most AWS APIs accept. A key whose value is a known kind of ARN gets its own bound in
 * TOKEN_MAX_LENGTH_BY_KEY instead.
 */
export const UNKNOWN_TOKEN_MAX_LENGTH = 2048;

// ARN components, for bounding `arn:<partition>:<service>:<region>:<account-id>:<resource>`. The
// partition and Region allowances are above the longest in use today (`aws-us-gov`, 10 characters;
// `eusc-de-east-1` and `ap-southeast-5`, 14), so a new partition or Region does not overflow a chunk.
const ARN_PARTITION_MAX = 16;
const ARN_REGION_MAX = 25;
const ARN_ACCOUNT_ID_LENGTH = 12;

/** Longest ARN of `service` whose resource part is `resourcePrefix` followed by a name of `nameMax`. */
export function arnMaxLength(service: string, resourcePrefix: string, nameMax: number): number {
    const separators = ":".length * 5; // arn:<partition>:<service>:<region>:<account>:<resource>
    return (
        "arn".length +
        ARN_PARTITION_MAX +
        service.length +
        ARN_REGION_MAX +
        ARN_ACCOUNT_ID_LENGTH +
        separators +
        resourcePrefix.length +
        nameMax
    );
}

/**
 * An EventBridge event bus ARN, `arn:<partition>:events:<region>:<account>:event-bus/<name>`, with
 * the name at its CreateEventBus limit of 256 characters: 333 characters.
 */
export const EVENT_BUS_ARN_MAX_LENGTH = arnMaxLength("events", "event-bus/", 256);

/**
 * Longest a deploy-time value can be, by its exact key: the registered keys whose values are ARNs,
 * which UNKNOWN_TOKEN_MAX_LENGTH would size at 2,048. Kept here rather than on the storage
 * registrations, so the register() calls stay as they are. A key added to the registry with an ARN
 * value gets an entry here, its bound derived from the AWS limits.
 */
export const TOKEN_MAX_LENGTH_BY_KEY: Readonly<Record<string, number>> = {
    [RESOURCE_PARAM_KEYS.integration.orchestrationBusArn]: EVENT_BUS_ARN_MAX_LENGTH,
};

/** Characters an SSM parameter name allows, which JSON carries without escaping. */
const SSM_KEY_PATTERN = /^[A-Za-z0-9_.-]+(\/[A-Za-z0-9_.-]+)*$/;

const LAYOUT_ID_LENGTH = 12;

/** Header with the widest count and index the count limit allows, for sizing before numbering. */
const HEADER_RESERVE = header(99999, 99999, "x".repeat(LAYOUT_ID_LENGTH)).length;

export interface ResourceNameChunkEntry {
    key: string;
    value: string;
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
    if (entry.key in TOKEN_MAX_LENGTH_BY_KEY) {
        return TOKEN_MAX_LENGTH_BY_KEY[entry.key] + '""'.length;
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
            entries: props.registry.list().map((d) => ({ key: d.paramKey, value: d.value })),
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
    // Every chunk waits for every nested stack any chunk reads, so a deploy that changes the
    // layout updates all of them together, once the last of those stacks is done. A chunk that
    // waited only for its own sources would update earlier (a literal-valued one at once), and
    // readers in between would meet two layouts and fall back to the sweep.
    const readStacks = nestedStacksReadBy(
        props.scope,
        plan.chunks.flatMap((c) => c.entries)
    );
    const prefix = resourceNameChunkPrefix(props.config);
    for (const chunk of plan.chunks) {
        const parameter = new ssm.StringParameter(props.scope, `ResourceNameChunk${chunk.index}`, {
            parameterName: `${prefix}/${chunk.index}`,
            stringValue: renderResourceNameChunk(plan, chunk),
            tier: ssm.ParameterTier.STANDARD,
        });
        for (const stack of readStacks) {
            (parameter.node.defaultChild as CfnResource).addDependency(
                stack.nestedStackResource as CfnResource
            );
        }
    }
    return plan;
}

/**
 * The nested stacks directly under `root` that hold, at any depth, a resource the deploy-time
 * values of `entries` reference, in first-reference order. A literal value references none.
 */
export function nestedStacksReadBy(root: Stack, entries: ResourceNameChunkEntry[]): Stack[] {
    const found: Stack[] = [];
    for (const entry of entries) {
        if (!Token.isUnresolved(entry.value)) continue;
        for (const token of Tokenization.reverseString(entry.value).tokens) {
            if (!(token instanceof Reference)) continue;
            let stack: Stack | undefined = Stack.of(token.target);
            while (stack?.nested && stack.nestedStackParent !== root) {
                stack = stack.nestedStackParent;
            }
            if (stack?.nested && stack.nestedStackResource && !found.includes(stack)) {
                found.push(stack);
            }
        }
    }
    return found;
}
