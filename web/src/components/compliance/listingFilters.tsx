/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Shared pieces of the compliance listings' server-side filters: filter values kept in the URL
 * (so a filtered view can be reloaded and shared), a debounce for free-text search, and the
 * database filter.
 */

import React, { useCallback, useEffect, useState } from "react";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Synonyms from "../../synonyms";
import { fetchAllDatabases } from "../../services/APIService";
import { useAllowedRoutes } from "../../features/orchestration/permissions/useAllowedRoutes";

/** Wait after the last keystroke before a text filter reaches the server. */
export const TEXT_FILTER_DEBOUNCE_MS = 350;

/** The route behind the database filter's options; without it the filter is a plain input. */
export const DATABASE_LIST_ROUTE = "/database";

const hashQuery = (): URLSearchParams => {
    const hash = window.location.hash || "";
    const index = hash.indexOf("?");
    return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : "");
};

/** The filter values the URL (the query part of the hash route) carries for `keys`. */
export const readUrlFilters = <K extends string>(keys: readonly K[]): Record<K, string> => {
    const query = hashQuery();
    return keys.reduce((values, key) => {
        values[key] = query.get(key) || "";
        return values;
    }, {} as Record<K, string>);
};

/**
 * Write filter values into the hash route's query, replacing the history entry. Empty values are
 * removed; other query parameters are kept.
 */
export const writeUrlFilters = (values: Record<string, string>): void => {
    const hash = window.location.hash || "#/";
    const base = hash.split("?")[0];
    const query = hashQuery();
    Object.entries(values).forEach(([key, value]) => {
        if (value) {
            query.set(key, value);
        } else {
            query.delete(key);
        }
    });
    const search = query.toString();
    const url = `${window.location.pathname}${window.location.search}${base}${
        search ? `?${search}` : ""
    }`;
    window.history.replaceState(window.history.state, "", url);
};

/**
 * Filter state initialised from, and written back to, the URL. A filter changed on the page
 * replaces the history entry (Back leaves the page rather than stepping through filters). When the
 * URL itself changes while the page is open — an in-app link or a URL pasted into the tab, which
 * change the hash, or Back / Forward between two entries of the page — the values follow it, so
 * the page reloads for the filters the URL now names.
 */
export const useUrlFilters = <K extends string>(
    keys: readonly K[]
): [Record<K, string>, (patch: Partial<Record<K, string>>) => void] => {
    const [values, setValues] = useState<Record<K, string>>(() => readUrlFilters(keys));
    const update = useCallback((patch: Partial<Record<K, string>>) => {
        setValues((previous) => {
            const next = { ...previous, ...patch } as Record<K, string>;
            writeUrlFilters(next);
            return next;
        });
    }, []);
    useEffect(() => {
        const follow = () => {
            const fromUrl = readUrlFilters(keys);
            // Unchanged values keep their object, so a hash change that names the same filters
            // (or another page's hash, as this one unmounts) starts no reload.
            setValues((previous) =>
                keys.every((key) => previous[key] === fromUrl[key]) ? previous : fromUrl
            );
        };
        window.addEventListener("hashchange", follow);
        window.addEventListener("popstate", follow);
        return () => {
            window.removeEventListener("hashchange", follow);
            window.removeEventListener("popstate", follow);
        };
    }, [keys]);
    return [values, update];
};

/** `value`, once it has stopped changing for `delayMs`. */
export const useDebouncedValue = <T,>(value: T, delayMs: number = TEXT_FILTER_DEBOUNCE_MS): T => {
    const [debounced, setDebounced] = useState(value);
    useEffect(() => {
        const timer = setTimeout(() => setDebounced(value), delayMs);
        return () => clearTimeout(timer);
    }, [value, delayMs]);
    return debounced;
};

/**
 * A text filter whose input updates at once and whose committed value (`onCommit`) follows the
 * debounce, so typing does not send a request per keystroke.
 */
export const TextFilterInput: React.FC<{
    value: string;
    onCommit: (value: string) => void;
    placeholder: string;
    ariaLabel: string;
}> = ({ value, onCommit, placeholder, ariaLabel }) => {
    const [draft, setDraft] = useState(value);
    const debounced = useDebouncedValue(draft);
    useEffect(() => {
        setDraft(value);
    }, [value]);
    useEffect(() => {
        if (debounced !== value) {
            onCommit(debounced);
        }
        // Only a settled draft commits; a changed committed value resets the draft above.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [debounced]);
    return (
        <Input
            type="search"
            value={draft}
            placeholder={placeholder}
            ariaLabel={ariaLabel}
            onChange={({ detail }) => setDraft(detail.value)}
        />
    );
};

export const ALL_DATABASES_LABEL = `All ${Synonyms.databases}`;

/**
 * The database filter: a Select of the databases the caller may list, or a text input of a
 * database id when the caller may not call the database listing. "" is every database.
 */
export const DatabaseFilter: React.FC<{
    value: string;
    onChange: (databaseId: string) => void;
}> = ({ value, onChange }) => {
    const { can: canCallRoute } = useAllowedRoutes();
    const canListDatabases = canCallRoute("GET", DATABASE_LIST_ROUTE);
    const [databaseIds, setDatabaseIds] = useState<string[]>([]);

    useEffect(() => {
        if (!canListDatabases) return;
        let active = true;
        fetchAllDatabases().then((databases: any) => {
            if (active && Array.isArray(databases)) {
                setDatabaseIds(
                    databases
                        .map((d: any) => d.databaseId)
                        .filter((id: any) => typeof id === "string")
                        .sort()
                );
            }
        });
        return () => {
            active = false;
        };
    }, [canListDatabases]);

    if (!canListDatabases) {
        return (
            <TextFilterInput
                value={value}
                onCommit={onChange}
                placeholder={`${Synonyms.Database} ID`}
                ariaLabel={`Filter by ${Synonyms.database}`}
            />
        );
    }
    const options = [
        { value: "", label: ALL_DATABASES_LABEL },
        ...databaseIds.map((id) => ({ value: id, label: id })),
    ];
    if (value && !databaseIds.includes(value)) {
        options.push({ value, label: value });
    }
    return (
        <Select
            selectedOption={options.find((option) => option.value === value) || options[0]}
            options={options}
            ariaLabel={`Filter by ${Synonyms.database}`}
            onChange={({ detail }) => onChange(detail.selectedOption.value || "")}
        />
    );
};
