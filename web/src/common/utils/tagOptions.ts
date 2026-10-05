/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Scope naming lives in one place so tables, selectors, badges and page headers agree.
export { GLOBAL_SCOPE, isGlobalScope, scopeLabel } from "./databaseScope";
import { isGlobalScope, scopeLabel } from "./databaseScope";

/**
 * Separates the two halves of the grouping key. Written as an escape, never as a literal byte: a
 * raw NUL makes git treat the whole file as binary (no diff, no three-way merge, no blame), and any
 * tool that normalizes control characters would change the separator without a trace.
 */
const GROUP_KEY_SEPARATOR = "\u0000";

/**
 * The tag-type list the asset page caches in localStorage under `tagTypes`. Empty until that fetch
 * has stored a list, and when the stored value is not a list: a failed fetch is stored as returned.
 */
export function readCachedTagTypes(): any[] {
    try {
        const parsed = JSON.parse(localStorage.getItem("tagTypes") || "[]");
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

interface TagOption {
    label: string;
    value: string;
}

interface TagOptionGroup {
    label: string;
    options: TagOption[];
}

/**
 * Builds the asset form's tag picker: tags grouped by tag type, each labelled with its scope.
 *
 * An asset resolves tag names within its own database plus GLOBAL, so the picker legitimately mixes
 * the two scopes and the label is the only thing that tells them apart — two databases may each own a
 * tag of the same name, and a bare name would be ambiguous.
 *
 * Ordering is GLOBAL first, then database-scoped, alphabetical within each band, applied to both the
 * groups and the tags inside them, so the shared vocabulary reads before the local additions.
 *
 * The option **value** stays the bare `tagName`: that is what an asset stores, and decorating it would
 * change what gets submitted.
 *
 * @param tags     tag records carrying tagName / tagTypeName / databaseId
 * @param tagTypes tag-type records, used for each group's `required` flag and its own scope
 */
export function buildTagOptionGroups(tags: any[], tagTypes: any[] = []): TagOptionGroup[] {
    const byType: Record<
        string,
        { tagTypeName: string; required: string; databaseId?: string; items: any[] }
    > = {};

    // Grouped by SCOPE + tag type name, not by name alone. The same name may exist as a GLOBAL tag
    // type and as a database-specific one (creating the global entry over a database name is
    // allowed, with a warning), and merging them would hide one scope behind the other.
    for (const tag of tags || []) {
        const typeName = tag?.tagTypeName || "Uncategorized";
        const tagScope = scopeLabel(tag?.databaseId);
        const key = `${tagScope}${GROUP_KEY_SEPARATOR}${typeName}`;
        if (!byType[key]) {
            const typeInfo =
                (tagTypes || []).find(
                    (t: any) =>
                        t?.tagTypeName === typeName && scopeLabel(t?.databaseId) === tagScope
                ) || (tagTypes || []).find((t: any) => t?.tagTypeName === typeName);
            byType[key] = {
                tagTypeName: typeName,
                required: typeInfo?.required || "False",
                // Filled in after grouping from the tags themselves — see below.
                databaseId: typeInfo?.databaseId,
                items: [],
            };
        }
        byType[key].items.push(tag);
    }

    // A tag's type must live in the tag's own scope, so the tags are the authority on what scope the
    // group is in. The tag-type records the callers pass come from a cached list that predates
    // per-database namespacing and often carries no databaseId at all, which made a database-scoped
    // tag type render as GLOBAL. Since grouping is keyed by scope, a group's tags always agree; the
    // recorded tag-type scope is only consulted if a caller passes tags with no scope at all.
    for (const group of Object.values(byType)) {
        const scopes = new Set(group.items.map((tag: any) => scopeLabel(tag?.databaseId)));
        if (scopes.size === 1) {
            group.databaseId = [...scopes][0];
        }
    }
    /** GLOBAL before database-scoped, then alphabetical. */
    const byScopeThenName = (
        aScope: string | undefined,
        aName: string,
        bScope: string | undefined,
        bName: string
    ) => {
        const aGlobal = isGlobalScope(aScope);
        const bGlobal = isGlobalScope(bScope);
        if (aGlobal !== bGlobal) {
            return aGlobal ? -1 : 1;
        }
        return (aName || "").localeCompare(bName || "");
    };

    return Object.values(byType)
        .filter((group) => group.items.length > 0)
        .sort((a, b) => byScopeThenName(a.databaseId, a.tagTypeName, b.databaseId, b.tagTypeName))
        .map((group) => {
            const requiredSuffix = group.required === "True" ? " [required]" : "";
            return {
                label: `${group.tagTypeName} (${scopeLabel(group.databaseId)})${requiredSuffix}`,
                options: group.items
                    .slice()
                    .sort((a: any, b: any) =>
                        byScopeThenName(a?.databaseId, a?.tagName, b?.databaseId, b?.tagName)
                    )
                    .map((tag: any) => ({
                        label: `${tag.tagName} (${scopeLabel(tag.databaseId)})`,
                        value: tag.tagName,
                    })),
            };
        });
}
