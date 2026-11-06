/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import { btnSecondary } from "../../../features/orchestration/components/controlStyles";
import { toastErrorMessage } from "../../../features/orchestration/components/ToastProvider";
import {
    useEdhAdminCapacityReservations,
    useEdhAdminInstances,
    useEdhCapacity,
} from "../api/adminQueries";
import type { EdhAdminInstance, EdhDesktopProfile } from "../api/admin";
import type { CapacityReservationView } from "../types";
import { edhStateLabel } from "../labels";
import {
    ADMIN_INSTANCES_ROUTE,
    CAPACITY_RESERVATIONS_ROUTE,
    NO_RESERVATIONS_TEXT,
    formatEndDate,
    inUseOfTotal,
    isRunningSession,
    reservationStateText,
    reservedSlotsText,
    workstationsInReservation,
} from "./reservationDisplay";

const Tile: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => {
    const labelId = useId();
    return (
        <div
            role="group"
            aria-labelledby={labelId}
            className="orch-outline p-4 rounded bg-surface-container border border-border-default"
        >
            <div id={labelId} className="text-sm text-text-secondary">
                {label}
            </div>
            <div className="text-2xl font-bold text-text-primary">{value}</div>
        </div>
    );
};

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

/** Whether an instance family is a GPU one: the graphics (g, gr) and accelerated (p) families. */
const isGpuFamily = (family: string) => /^[gp]/.test(family);

const SIZE_WORDS = ["nano", "micro", "small", "medium", "large"];

/**
 * Where an EC2 size sits in size order: nano to large, then xlarge and the `<n>xlarge` sizes by n,
 * then the metal sizes, the plain `metal` last. A size outside that scheme sorts after all of them.
 */
const sizeRank = (size: string): number => {
    const word = SIZE_WORDS.indexOf(size);
    if (word >= 0) return word;
    const xlarge = /^(\d*)xlarge$/.exec(size);
    if (xlarge) return SIZE_WORDS.length + (xlarge[1] ? Number(xlarge[1]) : 1);
    if (size === "metal") return 200000;
    const metal = /^metal-(\d+)xl$/.exec(size);
    if (metal) return 100000 + Number(metal[1]);
    return Number.POSITIVE_INFINITY;
};

const byName = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });

/**
 * Instance types by family (the name before the dot, such as `g6e` or `m7i`): the GPU families
 * first, each part in name order, and each family's sizes in size order.
 */
export function instanceTypesByFamily(types: string[]): Array<{ family: string; sizes: string[] }> {
    const families = new Map<string, string[]>();
    for (const type of types) {
        const dot = type.indexOf(".");
        const family = dot < 0 ? type : type.slice(0, dot);
        const size = dot < 0 ? "" : type.slice(dot + 1);
        families.set(family, [...(families.get(family) ?? []), size]);
    }
    return Array.from(families, ([family, sizes]) => ({
        family,
        sizes: Array.from(new Set(sizes))
            .filter((size) => size !== "")
            .sort((a, b) => sizeRank(a) - sizeRank(b) || byName(a, b)),
    })).sort(
        (a, b) =>
            Number(isGpuFamily(b.family)) - Number(isGpuFamily(a.family)) ||
            byName(a.family, b.family)
    );
}

const countWord = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** One architecture's allowed types: a count that expands into the types by family. */
const InstanceTypeGroup: React.FC<{ architecture: string | null; instanceTypes: string[] }> = ({
    architecture,
    instanceTypes,
}) => {
    const [open, setOpen] = useState(false);
    const listId = useId();
    const count = countWord(instanceTypes.length, "type");
    return (
        <div>
            <button
                type="button"
                aria-expanded={open}
                aria-controls={listId}
                onClick={() => setOpen((o) => !o)}
                className="inline-flex items-center gap-1.5 bg-transparent border-0 p-0 text-sm text-text-primary cursor-pointer rounded hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
                <span aria-hidden="true" className={open ? "inline-block rotate-90" : undefined}>
                    ▶
                </span>
                <span>{architecture === null ? count : `${architecture}: ${count}`}</span>
            </button>
            {open && (
                <ul id={listId} className="mt-1 ml-5 space-y-0.5 text-sm max-w-xl">
                    {instanceTypesByFamily(instanceTypes).map(({ family, sizes }) => (
                        <li key={family}>
                            <span className="font-semibold">{family}</span>
                            {sizes.length > 0 && (
                                <span className="text-text-secondary">: {sizes.join(", ")}</span>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
};

const profileColumns: ColumnDef<EdhDesktopProfile>[] = [
    {
        accessorKey: "id",
        header: "Id",
        cell: ({ row }) => <span className="font-mono text-sm">{String(row.original.id)}</span>,
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
        cell: ({ row }) => {
            const groups = profileInstanceTypesByArchitecture(row.original).filter(
                (group) => group.instanceTypes.length > 0
            );
            if (groups.length === 0) return "—";
            return (
                <div className="space-y-1 min-w-[10rem]">
                    {groups.map((group) => (
                        <InstanceTypeGroup key={group.architecture ?? ""} {...group} />
                    ))}
                </div>
            );
        },
    },
    {
        accessorKey: "max_root_size",
        header: "Max root size (GB)",
        cell: ({ row }) => row.original.max_root_size ?? "—",
    },
    {
        id: "subnets",
        header: "Subnets",
        cell: ({ row }) => {
            const subnets = profileSubnets(row.original);
            if (subnets.length === 0) return "—";
            return (
                <ul className="space-y-0.5">
                    {subnets.map((subnet) => (
                        <li key={subnet} className="font-mono text-xs whitespace-nowrap">
                            {subnet}
                        </li>
                    ))}
                </ul>
            );
        },
    },
];

const NO_SESSIONS: EdhAdminInstance[] = [];
const NO_RESERVATIONS: CapacityReservationView[] = [];

/** A query's value for a summary card: `…` while it loads, `—` once it failed. */
const tileValue = (
    query: { isLoading: boolean; error: unknown },
    value: () => React.ReactNode
): React.ReactNode => (query.isLoading ? "…" : query.error ? "—" : value());

const Section: React.FC<{ title: string; action?: React.ReactNode; children: React.ReactNode }> = ({
    title,
    action,
    children,
}) => {
    const headingId = useId();
    return (
        <section aria-labelledby={headingId} className="space-y-2">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <h2 id={headingId} className="text-text-primary text-base font-semibold">
                    {title}
                </h2>
                {action}
            </div>
            {children}
        </section>
    );
};

const Muted: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <div className="text-sm text-text-secondary">{children}</div>
);

const LoadError: React.FC<{ what: string; error: unknown }> = ({ what, error }) => (
    <div role="alert" className="p-4 text-vams-error">
        Error loading {what}: {toastErrorMessage(error)}
    </div>
);

interface CapacityTabProps {
    can: (method: string, pathTemplate: string) => boolean;
    /** Opens the Reservations tab. */
    onManageReservations: () => void;
}

/**
 * The cluster's capacity at a glance: the session counts EDH reports, the registered capacity
 * reservations and how full EC2 says each is, the running workstations by instance type, and the
 * virtual desktop profiles. Each part reads its own route; a part the role may not read is left
 * out, and a part whose read fails reports it in place.
 */
const CapacityTab: React.FC<CapacityTabProps> = ({ can, onManageReservations }) => {
    const canSessions = can("GET", ADMIN_INSTANCES_ROUTE);
    const canReservations = can("GET", CAPACITY_RESERVATIONS_ROUTE);
    const capacity = useEdhCapacity();
    const sessionsQuery = useEdhAdminInstances(canSessions);
    const reservationsQuery = useEdhAdminCapacityReservations(canReservations);
    const sessions = sessionsQuery.data ?? NO_SESSIONS;
    const reservations = reservationsQuery.data ?? NO_RESERVATIONS;
    const sessionsKnown = !sessionsQuery.isLoading && !sessionsQuery.error;
    const reservationsKnown = !reservationsQuery.isLoading && !reservationsQuery.error;

    const refresh = () => {
        capacity.refetch();
        if (canSessions) sessionsQuery.refetch();
        if (canReservations) reservationsQuery.refetch();
    };
    const refreshing =
        capacity.isFetching ||
        (canSessions && sessionsQuery.isFetching) ||
        (canReservations && reservationsQuery.isFetching);

    const running = useMemo(() => sessions.filter(isRunningSession), [sessions]);

    const reservationColumns = useMemo<ColumnDef<CapacityReservationView>[]>(
        () => [
            {
                accessorKey: "label",
                header: "Label",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">{row.original.label}</span>
                ),
            },
            {
                accessorKey: "instanceType",
                header: "Instance type",
                cell: ({ row }) => row.original.instanceType || "—",
            },
            {
                accessorKey: "availabilityZone",
                header: "Availability Zone",
                cell: ({ row }) => row.original.availabilityZone || "—",
            },
            {
                accessorKey: "state",
                header: "State",
                cell: ({ row }) => reservationStateText(row.original.state),
            },
            {
                id: "inUse",
                header: "In use / total",
                cell: ({ row }) => inUseOfTotal(row.original),
            },
            ...(canSessions
                ? [
                      {
                          id: "workstations",
                          header: "VAMS workstations",
                          cell: ({ row }) =>
                              sessionsKnown
                                  ? workstationsInReservation(
                                        sessions,
                                        row.original.capacityReservationId
                                    )
                                  : "—",
                      } as ColumnDef<CapacityReservationView>,
                  ]
                : []),
            {
                accessorKey: "endDate",
                header: "End date",
                cell: ({ row }) => (
                    <span className="text-sm">{formatEndDate(row.original.endDate)}</span>
                ),
            },
            {
                accessorKey: "enabled",
                header: "Enabled",
                cell: ({ row }) => (row.original.enabled ? "Yes" : "No"),
            },
        ],
        [canSessions, sessionsKnown, sessions]
    );

    // The running workstations of each instance type, most first, and the active reservations of it.
    const byType = useMemo(() => {
        const counts = new Map<string, number>();
        for (const session of running) {
            const type = session.instanceType || "—";
            counts.set(type, (counts.get(type) ?? 0) + 1);
        }
        return Array.from(counts, ([instanceType, count]) => ({ instanceType, count })).sort(
            (a, b) => b.count - a.count || byName(a.instanceType, b.instanceType)
        );
    }, [running]);

    const typeColumns = useMemo<ColumnDef<{ instanceType: string; count: number }>[]>(() => {
        // The active registered reservations of the type, with how full EC2 says each is.
        const coverage = (instanceType: string): string => {
            if (!reservationsKnown) return "—";
            const covering = reservations.filter(
                (r) => r.state === "active" && r.instanceType === instanceType
            );
            if (covering.length === 0) return "No active reservation";
            return covering.map((r) => `${r.label} (${inUseOfTotal(r)} in use)`).join(", ");
        };
        return [
            { accessorKey: "instanceType", header: "Instance type" },
            { accessorKey: "count", header: "Running" },
            ...(canReservations
                ? [
                      {
                          id: "reservation",
                          header: "Active reservation",
                          cell: ({ row }) => coverage(row.original.instanceType),
                      } as ColumnDef<{ instanceType: string; count: number }>,
                  ]
                : []),
        ];
    }, [canReservations, reservationsKnown, reservations]);

    const byState = Object.entries(capacity.data?.byState ?? {}).sort(([a], [b]) =>
        a.localeCompare(b)
    );
    const profiles = capacity.data?.profiles ?? [];

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-2">
                <RefreshButton onClick={refresh} busy={refreshing} ariaLabel="Refresh capacity" />
                <span className="text-sm text-text-secondary">
                    {canReservations ? "Live from EDH and EC2" : "Live from EDH"}
                </span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4">
                <Tile
                    label="Total sessions"
                    value={tileValue(capacity, () => capacity.data?.totalSessions ?? 0)}
                />
                {canSessions && (
                    <Tile label="Running" value={tileValue(sessionsQuery, () => running.length)} />
                )}
                <Tile
                    label="Distinct owners"
                    value={tileValue(capacity, () => capacity.data?.distinctOwners ?? 0)}
                />
                {canReservations && (
                    <>
                        <Tile
                            label="Registered reservations"
                            value={tileValue(reservationsQuery, () => reservations.length)}
                        />
                        <Tile
                            label="Reserved slots"
                            value={tileValue(reservationsQuery, () =>
                                reservedSlotsText(reservations)
                            )}
                        />
                    </>
                )}
            </div>

            {capacity.isLoading ? (
                <div className="p-8 text-center text-text-secondary">Loading capacity...</div>
            ) : capacity.error ? (
                <LoadError what="capacity" error={capacity.error} />
            ) : (
                <div className="orch-outline p-4 rounded bg-surface-container border border-border-default">
                    <Section title="Sessions by EDH state">
                        {byState.length === 0 ? (
                            <Muted>No sessions.</Muted>
                        ) : (
                            <ul className="text-sm text-text-primary space-y-1">
                                {byState.map(([state, count]) => (
                                    <li key={state}>
                                        {edhStateLabel(state)}: {count}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </Section>
                </div>
            )}

            {canReservations && (
                <Section
                    title="Capacity reservations"
                    action={
                        <button
                            type="button"
                            className={btnSecondary}
                            onClick={onManageReservations}
                        >
                            Manage reservations
                        </button>
                    }
                >
                    {reservationsQuery.isLoading ? (
                        <Muted>Loading capacity reservations...</Muted>
                    ) : reservationsQuery.error ? (
                        <LoadError what="capacity reservations" error={reservationsQuery.error} />
                    ) : reservations.length === 0 ? (
                        <Muted>{NO_RESERVATIONS_TEXT}</Muted>
                    ) : (
                        <DataTable
                            columns={reservationColumns}
                            rows={reservations}
                            ariaLabel="Capacity reservation use"
                            getRowId={(r) => r.capacityReservationId}
                            filtering={false}
                        />
                    )}
                </Section>
            )}

            {canSessions && (
                <Section title="Running workstations by instance type">
                    {sessionsQuery.isLoading ? (
                        <Muted>Loading sessions...</Muted>
                    ) : sessionsQuery.error ? (
                        <LoadError what="sessions" error={sessionsQuery.error} />
                    ) : byType.length === 0 ? (
                        <Muted>No workstations are running.</Muted>
                    ) : (
                        <DataTable
                            columns={typeColumns}
                            rows={byType}
                            ariaLabel="Running workstations by instance type"
                            getRowId={(row) => row.instanceType}
                            filtering={false}
                        />
                    )}
                </Section>
            )}

            {!capacity.isLoading && !capacity.error && (
                <Section title="Virtual desktop profiles">
                    <p className="text-sm text-text-secondary">
                        A registered VDI image names one of these profiles by its id.
                    </p>
                    {profiles.length === 0 ? (
                        <Muted>No profiles returned.</Muted>
                    ) : (
                        <DataTable
                            columns={profileColumns}
                            rows={profiles}
                            ariaLabel="Virtual desktop profiles"
                            getRowId={(profile) => String(profile.id)}
                            filtering={false}
                        />
                    )}
                </Section>
            )}
        </div>
    );
};

export default CapacityTab;
