/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useMemo, useState } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import Dialog from "../../../features/orchestration/components/Dialog";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import {
    btnPrimary,
    btnSecondary,
    control,
} from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import {
    useEdhAdminCapacityReservations,
    useEdhCapacityReservationMutations,
} from "../api/adminQueries";
import { useInFlight } from "../components/useInFlight";
import { edhSubnetNote } from "../capacityReservations";
import type { CapacityReservationView } from "../types";
import ConfirmActionDialog from "./ConfirmActionDialog";

// Route templates as `backend/backend/common/addonApiRoutes.py` declares them.
export const CAPACITY_RESERVATIONS_ROUTE = "/addon/edh/admin/capacity-reservations";
export const CAPACITY_RESERVATION_ROUTE =
    "/addon/edh/admin/capacity-reservations/{capacityReservationId}";

/** The EC2 reservation id shape the request model takes. */
export const CAPACITY_RESERVATION_ID_PATTERN = /^cr-[0-9a-f]{17}$/;
/** The display-name charset, 1-64 characters, as the request model takes a label. */
export const CAPACITY_RESERVATION_LABEL_PATTERN = /^[a-zA-Z0-9\-._\s]{1,64}$/;

const ID_MESSAGE = "Enter an EC2 capacity reservation ID (cr- and 17 hex digits).";
const LABEL_MESSAGE =
    "Label must be 1-64 letters, digits, spaces, hyphens, periods or underscores.";

/** The words the State column shows for the two states VAMS adds to EC2's own. */
const STATE_TEXT: Record<string, string> = {
    notFound: "Not found in EC2",
    unknown: "Unknown (EC2 read failed)",
};

const NO_RESERVATIONS: CapacityReservationView[] = [];

const formatDate = (iso: string | null | undefined) => {
    if (!iso) return "No end date";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

const freeOfTotal = (r: CapacityReservationView) =>
    r.availableInstanceCount == null || r.totalInstanceCount == null
        ? "—"
        : `${r.availableInstanceCount}/${r.totalInstanceCount}`;

interface FormState {
    capacityReservationId: string;
    label: string;
    fallbackToOnDemand: boolean;
    enabled: boolean;
}

interface ReservationFormDialogProps {
    /** The reservation edited; absent to register a new one. */
    editing?: CapacityReservationView;
    busy: boolean;
    error: string | null;
    onSubmit: (form: FormState) => void;
    onCancel: () => void;
}

/** Register a reservation, or edit a registered one's label and switches (never its id). */
const ReservationFormDialog: React.FC<ReservationFormDialogProps> = ({
    editing,
    busy,
    error,
    onSubmit,
    onCancel,
}) => {
    const idBase = useId();
    const [form, setForm] = useState<FormState>({
        capacityReservationId: editing?.capacityReservationId ?? "",
        label: editing?.label ?? "",
        fallbackToOnDemand: editing?.fallbackToOnDemand ?? true,
        enabled: editing?.enabled ?? true,
    });
    const [submitted, setSubmitted] = useState(false);

    const errors: Partial<Record<"capacityReservationId" | "label", string>> = {};
    if (!editing && !CAPACITY_RESERVATION_ID_PATTERN.test(form.capacityReservationId.trim())) {
        errors.capacityReservationId = ID_MESSAGE;
    }
    if (!CAPACITY_RESERVATION_LABEL_PATTERN.test(form.label.trim())) {
        errors.label = LABEL_MESSAGE;
    }
    const shown = submitted ? errors : {};

    const submit = (e?: React.FormEvent) => {
        e?.preventDefault();
        setSubmitted(true);
        if (Object.keys(errors).length > 0) return;
        onSubmit({
            ...form,
            capacityReservationId: form.capacityReservationId.trim(),
            label: form.label.trim(),
        });
    };

    const fieldError = (key: "capacityReservationId" | "label") =>
        shown[key] ? (
            <p id={`${idBase}-${key}-error`} className="text-vams-error text-sm mt-1">
                {shown[key]}
            </p>
        ) : null;

    return (
        <Dialog
            open
            onOpenChange={(isOpen) => !isOpen && onCancel()}
            title={editing ? `Edit ${editing.label}` : "Register capacity reservation"}
            footer={
                <>
                    <button type="button" onClick={onCancel} className={btnSecondary}>
                        Cancel
                    </button>
                    <button
                        type="button"
                        onClick={() => submit()}
                        disabled={busy}
                        className={btnPrimary}
                    >
                        {editing ? "Save" : "Register"}
                    </button>
                </>
            }
        >
            <form onSubmit={submit} className="space-y-4">
                <div>
                    <label
                        htmlFor={`${idBase}-id`}
                        className="block text-sm text-text-primary mb-1"
                    >
                        Capacity reservation ID
                    </label>
                    <input
                        id={`${idBase}-id`}
                        type="text"
                        value={form.capacityReservationId}
                        onChange={(e) =>
                            setForm((prev) => ({ ...prev, capacityReservationId: e.target.value }))
                        }
                        disabled={!!editing || busy}
                        placeholder="cr-0123456789abcdef0"
                        aria-invalid={shown.capacityReservationId ? true : undefined}
                        aria-describedby={
                            shown.capacityReservationId
                                ? `${idBase}-id-hint ${idBase}-capacityReservationId-error`
                                : `${idBase}-id-hint`
                        }
                        className={`${control} block w-full font-mono`}
                    />
                    <p id={`${idBase}-id-hint`} className="text-xs text-text-secondary mt-1">
                        A reservation in this deployment&apos;s account and Region. Registering it
                        changes nothing in EC2.
                    </p>
                    {fieldError("capacityReservationId")}
                </div>
                <div>
                    <label
                        htmlFor={`${idBase}-label`}
                        className="block text-sm text-text-primary mb-1"
                    >
                        Label
                    </label>
                    <input
                        id={`${idBase}-label`}
                        type="text"
                        value={form.label}
                        maxLength={64}
                        onChange={(e) => setForm((prev) => ({ ...prev, label: e.target.value }))}
                        disabled={busy}
                        aria-invalid={shown.label ? true : undefined}
                        aria-describedby={shown.label ? `${idBase}-label-error` : undefined}
                        className={`${control} block w-full`}
                    />
                    {fieldError("label")}
                </div>
                <div>
                    <label className="flex items-center gap-2 text-sm text-text-primary">
                        <input
                            type="checkbox"
                            checked={form.fallbackToOnDemand}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    fallbackToOnDemand: e.target.checked,
                                }))
                            }
                            disabled={busy}
                            className="rounded"
                        />
                        <span>Fall back to on-demand when full</span>
                    </label>
                    <p className="text-xs text-text-secondary mt-1">
                        On: a launch into a full reservation runs on-demand. Off: it is refused.
                    </p>
                </div>
                <div>
                    <label className="flex items-center gap-2 text-sm text-text-primary">
                        <input
                            type="checkbox"
                            checked={form.enabled}
                            onChange={(e) =>
                                setForm((prev) => ({ ...prev, enabled: e.target.checked }))
                            }
                            disabled={busy}
                            className="rounded"
                        />
                        <span>Enabled</span>
                    </label>
                    <p className="text-xs text-text-secondary mt-1">
                        Only an enabled reservation is offered in the launch wizard.
                    </p>
                </div>
                {error && (
                    <div
                        role="alert"
                        className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded"
                    >
                        {error}
                    </div>
                )}
            </form>
        </Dialog>
    );
};

interface CapacityReservationsSectionProps {
    can: (method: string, pathTemplate: string) => boolean;
}

/**
 * The Settings tab's Capacity reservations: the registered EC2 capacity reservations a launch may
 * target, with EC2's live view of each, and Register, Edit and Unregister. The registry is VAMS's
 * own; none of these actions changes the reservation in EC2.
 */
const CapacityReservationsSection: React.FC<CapacityReservationsSectionProps> = ({ can }) => {
    const toast = useToast();
    const headingId = useId();
    const { data, isLoading, error, refetch, isFetching } = useEdhAdminCapacityReservations();
    const rows = data ?? NO_RESERVATIONS;
    const { register, update, unregister } = useEdhCapacityReservationMutations();
    const { claim, release } = useInFlight();
    const [dialog, setDialog] = useState<
        { mode: "register" } | { mode: "edit"; reservation: CapacityReservationView } | null
    >(null);
    const [dialogError, setDialogError] = useState<string | null>(null);
    const [removing, setRemoving] = useState<CapacityReservationView | null>(null);

    const canRegister = can("POST", CAPACITY_RESERVATIONS_ROUTE);
    const canEdit = can("PUT", CAPACITY_RESERVATION_ROUTE);
    const canUnregister = can("DELETE", CAPACITY_RESERVATION_ROUTE);

    const openDialog = (next: typeof dialog) => {
        setDialogError(null);
        setDialog(next);
    };

    const columns = useMemo<ColumnDef<CapacityReservationView>[]>(
        () => [
            {
                accessorKey: "label",
                header: "Label",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">{row.original.label}</span>
                ),
            },
            {
                accessorKey: "capacityReservationId",
                header: "Reservation",
                cell: ({ row }) => (
                    <span className="font-mono text-xs">{row.original.capacityReservationId}</span>
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
                accessorKey: "instancePlatform",
                header: "Platform",
                cell: ({ row }) => row.original.instancePlatform || "—",
            },
            {
                accessorKey: "state",
                header: "State",
                cell: ({ row }) => {
                    const note = edhSubnetNote(row.original);
                    const state = STATE_TEXT[row.original.state] ?? row.original.state;
                    if (!note) return state;
                    // The words carry the warning; the colour and the sign only repeat it.
                    return (
                        <>
                            <span className="block">{state}</span>
                            <span className="flex items-center gap-1 text-xs text-yellow-800 dark:text-yellow-300">
                                <span aria-hidden="true">⚠</span>
                                <span>{note}</span>
                            </span>
                        </>
                    );
                },
            },
            {
                id: "free",
                header: "Free / total",
                cell: ({ row }) => freeOfTotal(row.original),
            },
            {
                accessorKey: "endDate",
                header: "End date",
                cell: ({ row }) => (
                    <span className="text-sm">{formatDate(row.original.endDate)}</span>
                ),
            },
            {
                accessorKey: "fallbackToOnDemand",
                header: "Fallback",
                cell: ({ row }) => (row.original.fallbackToOnDemand ? "On-demand" : "Refuse"),
            },
            {
                accessorKey: "enabled",
                header: "Enabled",
                cell: ({ row }) => (row.original.enabled ? "Yes" : "No"),
            },
            ...(canEdit || canUnregister
                ? [
                      {
                          id: "actions",
                          header: "Actions",
                          cell: ({ row }) => (
                              <div className="flex gap-2">
                                  {canEdit && (
                                      <button
                                          type="button"
                                          className={btnSecondary}
                                          aria-label={`Edit ${row.original.label}`}
                                          onClick={() => {
                                              setDialogError(null);
                                              setDialog({
                                                  mode: "edit",
                                                  reservation: row.original,
                                              });
                                          }}
                                      >
                                          Edit
                                      </button>
                                  )}
                                  {canUnregister && (
                                      <button
                                          type="button"
                                          className={btnSecondary}
                                          aria-label={`Unregister ${row.original.label}`}
                                          onClick={() => setRemoving(row.original)}
                                      >
                                          Unregister
                                      </button>
                                  )}
                              </div>
                          ),
                      } as ColumnDef<CapacityReservationView>,
                  ]
                : []),
        ],
        [canEdit, canUnregister]
    );

    const save = async (form: FormState) => {
        if (!dialog || !claim("save")) return;
        setDialogError(null);
        try {
            if (dialog.mode === "register") {
                await register.mutateAsync(form);
                toast.success("Capacity reservation registered", { description: form.label });
            } else {
                await update.mutateAsync({
                    capacityReservationId: dialog.reservation.capacityReservationId,
                    body: {
                        label: form.label,
                        fallbackToOnDemand: form.fallbackToOnDemand,
                        enabled: form.enabled,
                    },
                });
                toast.success("Capacity reservation saved", { description: form.label });
            }
            setDialog(null);
        } catch (err) {
            // The dialog stays open with the reason, so the administrator can correct the id.
            setDialogError(toastErrorMessage(err));
        } finally {
            release("save");
        }
    };

    const confirmUnregister = async () => {
        if (!removing || !claim(removing.capacityReservationId)) return;
        const target = removing;
        try {
            await unregister.mutateAsync(target.capacityReservationId);
            toast.success("Capacity reservation unregistered", { description: target.label });
        } catch (err) {
            toast.error("Unregister failed", {
                description: `${target.label}: ${toastErrorMessage(err)}`,
            });
        } finally {
            setRemoving(null);
            release(target.capacityReservationId);
        }
    };

    return (
        <section aria-labelledby={headingId} className="space-y-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <h2 id={headingId} className="text-text-primary text-lg font-semibold">
                    Capacity reservations
                </h2>
                <div className="flex items-center gap-2">
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel="Refresh capacity reservations"
                    />
                    {canRegister && (
                        <button
                            type="button"
                            className={btnPrimary}
                            onClick={() => openDialog({ mode: "register" })}
                        >
                            Register reservation
                        </button>
                    )}
                </div>
            </div>
            <p className="text-sm text-text-secondary max-w-3xl">
                The EC2 capacity reservations users may launch workstations into. A user sees a
                reservation only when their role grants it, and only while it is enabled, active,
                and in an Availability Zone where EDH has a private subnet. Without a registered
                reservation every launch is on-demand.
            </p>

            {isLoading ? (
                <div className="p-8 text-center text-text-secondary">
                    Loading capacity reservations...
                </div>
            ) : error ? (
                <div role="alert" className="p-4 text-vams-error">
                    Error loading capacity reservations: {toastErrorMessage(error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">
                    No capacity reservations are registered. Every launch is on-demand.
                </div>
            ) : (
                <DataTable
                    columns={columns}
                    rows={rows}
                    ariaLabel="Capacity reservations"
                    getRowId={(r) => r.capacityReservationId}
                    filtering={false}
                />
            )}

            {dialog && (
                <ReservationFormDialog
                    key={dialog.mode === "edit" ? dialog.reservation.capacityReservationId : "new"}
                    editing={dialog.mode === "edit" ? dialog.reservation : undefined}
                    busy={register.isPending || update.isPending}
                    error={dialogError}
                    onSubmit={save}
                    onCancel={() => setDialog(null)}
                />
            )}

            {removing && (
                <ConfirmActionDialog
                    open
                    title={`Unregister ${removing.label}`}
                    confirmLabel="Unregister"
                    busy={unregister.isPending}
                    onCancel={() => setRemoving(null)}
                    onConfirm={confirmUnregister}
                    message={
                        <p>
                            Unregister this reservation? VAMS stops offering it in the launch
                            wizard. The EC2 reservation itself is not changed, and workstations
                            launched into it keep running.
                        </p>
                    }
                />
            )}
        </section>
    );
};

export default CapacityReservationsSection;
