/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import { toastErrorMessage } from "../../../features/orchestration/components/ToastProvider";
import { useEdhCapacity } from "../api/adminQueries";
import type { EdhDesktopProfile } from "../api/admin";
import { edhStateLabel } from "../labels";

const Tile: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
    <div className="orch-outline p-4 rounded bg-surface-container border border-border-default">
        <div className="text-sm text-text-secondary">{label}</div>
        <div className="text-2xl font-bold text-text-primary">{value}</div>
    </div>
);

const splitList = (value: unknown): string[] =>
    typeof value === "string"
        ? value
              .split(",")
              .map((item) => item.trim())
              .filter((item) => item !== "")
        : Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];

/** A profile's allowed instance types as EDH stores them, a JSON string parsed. */
const allowedInstanceTypes = (profile: EdhDesktopProfile): unknown => {
    const allowed: unknown = profile.allowed_instance_types;
    if (typeof allowed === "string") {
        try {
            return JSON.parse(allowed);
        } catch (e) {
            // A plain comma-separated list, read as it is.
        }
    }
    return allowed;
};

/**
 * The instance types a profile allows: the types of every group of the per-architecture lists EDH
 * stores as a JSON string; a plain list or comma-separated string as it comes; else the pattern EDH
 * derived them from.
 */
export function profileInstanceTypes(profile: EdhDesktopProfile): string[] {
    const allowed = allowedInstanceTypes(profile);
    if (Array.isArray(allowed) || typeof allowed === "string") {
        const list = splitList(allowed);
        if (list.length > 0) return list;
    } else if (allowed && typeof allowed === "object") {
        const list = Object.values(allowed as Record<string, unknown>).flatMap(splitList);
        if (list.length > 0) return list;
    }
    return splitList(profile.pattern_allowed_instance_types);
}

/**
 * The instance types a profile allows, by architecture: one group per architecture that lists a
 * type, in the order EDH stores them; any other form is one group of no architecture holding
 * `profileInstanceTypes`.
 */
export function profileInstanceTypesByArchitecture(
    profile: EdhDesktopProfile
): Array<{ architecture: string | null; instanceTypes: string[] }> {
    const allowed = allowedInstanceTypes(profile);
    if (allowed && typeof allowed === "object" && !Array.isArray(allowed)) {
        const groups = Object.entries(allowed as Record<string, unknown>)
            .map(([architecture, value]) => ({ architecture, instanceTypes: splitList(value) }))
            .filter((group) => group.instanceTypes.length > 0);
        if (groups.length > 0) return groups;
    }
    return [{ architecture: null, instanceTypes: profileInstanceTypes(profile) }];
}

/** The subnets a profile launches into. */
export function profileSubnets(profile: EdhDesktopProfile): string[] {
    return splitList(profile.allowed_subnet_ids);
}

const joined = (items: string[]) => (items.length === 0 ? "—" : items.join(", "));

/** Each group as `<architecture>: <types>`, a group of no architecture as its list alone. */
const byArchitecture = (groups: ReturnType<typeof profileInstanceTypesByArchitecture>) =>
    groups
        .map((group) =>
            group.architecture === null
                ? joined(group.instanceTypes)
                : `${group.architecture}: ${group.instanceTypes.join(", ")}`
        )
        .join(" · ");

const CapacityTab: React.FC = () => {
    const { data, isLoading, error, refetch, isFetching } = useEdhCapacity();

    const columns = useMemo<ColumnDef<EdhDesktopProfile>[]>(
        () => [
            {
                accessorKey: "id",
                header: "Id",
                cell: ({ row }) => (
                    <span className="font-mono text-sm">{String(row.original.id)}</span>
                ),
            },
            {
                accessorKey: "profile_name",
                header: "Name",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">
                        {row.original.profile_name || "—"}
                    </span>
                ),
            },
            {
                id: "instanceTypes",
                header: "Allowed instance types",
                cell: ({ row }) => (
                    <span className="text-sm">
                        {byArchitecture(profileInstanceTypesByArchitecture(row.original))}
                    </span>
                ),
            },
            {
                accessorKey: "max_root_size",
                header: "Max root size (GB)",
                cell: ({ row }) => row.original.max_root_size ?? "—",
            },
            {
                id: "subnets",
                header: "Subnets",
                cell: ({ row }) => (
                    <span className="font-mono text-xs">
                        {joined(profileSubnets(row.original))}
                    </span>
                ),
            },
        ],
        []
    );

    if (isLoading) {
        return <div className="p-8 text-center text-text-secondary">Loading capacity...</div>;
    }
    if (error) {
        return (
            <div className="p-4 text-vams-error">
                Error loading capacity: {toastErrorMessage(error)}
            </div>
        );
    }
    if (!data) {
        return (
            <div className="p-8 text-center text-text-secondary">No capacity data returned.</div>
        );
    }

    // Either breakdown may be absent from the body; each tile stands on its own.
    const byState = data.byState ?? {};
    const profiles = data.profiles ?? [];
    const states = Object.entries(byState).sort(([a], [b]) => a.localeCompare(b));

    return (
        <div className="space-y-4">
            <div className="flex items-center gap-2">
                <RefreshButton
                    onClick={() => refetch()}
                    busy={isFetching}
                    ariaLabel="Refresh capacity"
                />
                <span className="text-sm text-text-secondary">Live from EDH</span>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Tile label="Total sessions" value={data.totalSessions} />
                <Tile label="Distinct owners" value={data.distinctOwners} />
                <Tile label="Virtual desktop profiles" value={profiles.length} />
            </div>
            <div className="orch-outline p-4 rounded bg-surface-container border border-border-default">
                <div className="font-semibold text-text-primary mb-2">Sessions by EDH state</div>
                {states.length === 0 ? (
                    <div className="text-sm text-text-secondary">No sessions.</div>
                ) : (
                    <ul className="text-sm text-text-primary space-y-1">
                        {states.map(([state, count]) => (
                            <li key={state}>
                                {edhStateLabel(state)}: {count}
                            </li>
                        ))}
                    </ul>
                )}
            </div>
            <div className="space-y-2">
                <div className="font-semibold text-text-primary">Virtual desktop profiles</div>
                <p className="text-sm text-text-secondary">
                    A registered VDI image names one of these profiles by its id.
                </p>
                {profiles.length === 0 ? (
                    <div className="text-sm text-text-secondary">No profiles returned.</div>
                ) : (
                    <DataTable
                        columns={columns}
                        rows={profiles}
                        ariaLabel="Virtual desktop profiles"
                        getRowId={(profile) => String(profile.id)}
                        filtering={false}
                    />
                )}
            </div>
        </div>
    );
};

export default CapacityTab;
