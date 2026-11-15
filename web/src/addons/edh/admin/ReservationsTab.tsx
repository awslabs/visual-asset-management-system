/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useMemo, useRef, useState } from "react";
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
import { BusyButton, BusyInput } from "../../../features/orchestration/components/BusyControls";
import { useInFlight } from "../components/useInFlight";
import {
    useRemovedRowRef,
    useReturnFocus,
    type ReturnFocusTo,
} from "../../../features/orchestration/components/useReturnFocus";
import { edhSubnetNote } from "../capacityReservations";
import type { CapacityReservationView } from "../types";
import ConfirmActionDialog, { controlNamed } from "./ConfirmActionDialog";
import {
    CAPACITY_RESERVATIONS_ROUTE,
    CAPACITY_RESERVATION_ROUTE,
    NO_RESERVATIONS_TEXT,
    RESERVATIONS_DESCRIPTION,
    formatEndDate,
    freeOfTotal,
    reservationStateText,
} from "./reservationDisplay";

export { CAPACITY_RESERVATIONS_ROUTE, CAPACITY_RESERVATION_ROUTE };

// The refresh button's accessible name, which an unregister also returns focus to.
const REFRESH_LABEL = "Refresh capacity reservations";

/** The EC2 reservation id shape the request model takes. */
export const CAPACITY_RESERVATION_ID_PATTERN = /^cr-[0-9a-f]{17}$/;
/** The display-name charset, 1-64 characters, as the request model takes a label. */
export const CAPACITY_RESERVATION_LABEL_PATTERN = /^[a-zA-Z0-9\-._\s]{1,64}$/;

const ID_MESSAGE = "Enter an EC2 capacity reservation ID (cr- and 17 hex digits).";
const LABEL_MESSAGE =
    "Label must be 1-64 letters, digits, spaces, hyphens, periods or underscores.";

const NO_RESERVATIONS: CapacityReservationView[] = [];

interface FormState {
    capacityReservationId: string;
    label: string;
    fallbackToOnDemand: boolean;
    enabled: boolean;
}

interface ReservationFormDialogProps {
    /** The reservation edited; absent to register a new one. */
    editing?: CapacityReservationView;
    /** Whether the save is in flight: the dialog stays open, and its fields and buttons keep focus. */
    busy: boolean;
    error: string | null;
    onSubmit: (form: FormState) => void;
    onCancel: () => void;
    /** Picks the element focus returns to as the dialog closes; the opener without it. */
    returnFocusTo?: ReturnFocusTo;
}

/** Register a reservation, or edit a registered one's label and switches (never its id). */
const ReservationFormDialog: React.FC<ReservationFormDialogProps> = ({
    editing,
    busy,
    error,
    onSubmit,
    onCancel,
    returnFocusTo,
}) => {
    const returnFocusRef = useReturnFocus(true, returnFocusTo);
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
        // A submit while the last one is in flight sends nothing.
        if (busy) return;
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
            busy={busy}
            title={editing ? `Edit ${editing.label}` : "Register capacity reservation"}
            returnFocusRef={returnFocusRef}
            footer={
                <>
                    <BusyButton busy={busy} onClick={onCancel} className={btnSecondary}>
                        Cancel
                    </BusyButton>
                    <BusyButton
                        busy={busy}
                        busyLabel={editing ? "Saving..." : "Registering..."}
                        onClick={() => submit()}
                        className={btnPrimary}
                    >
                        {editing ? "Save" : "Register"}
                    </BusyButton>
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
                    <BusyInput
                        id={`${idBase}-id`}
                        type="text"
                        value={form.capacityReservationId}
                        onChange={(e) =>
                            setForm((prev) => ({ ...prev, capacityReservationId: e.target.value }))
                        }
                        // An edit never changes the id.
                        disabled={!!editing}
                        busy={busy}
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
                    <BusyInput
                        id={`${idBase}-label`}
                        type="text"
                        value={form.label}
                        maxLength={64}
                        onChange={(e) => setForm((prev) => ({ ...prev, label: e.target.value }))}
                        busy={busy}
                        aria-invalid={shown.label ? true : undefined}
                        aria-describedby={shown.label ? `${idBase}-label-error` : undefined}
                        className={`${control} block w-full`}
                    />
                    {fieldError("label")}
                </div>
                <div>
                    <label className="flex items-center gap-2 text-sm text-text-primary">
                        <BusyInput
                            type="checkbox"
                            checked={form.fallbackToOnDemand}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    fallbackToOnDemand: e.target.checked,
                                }))
                            }
                            busy={busy}
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
                        <BusyInput
                            type="checkbox"
                            checked={form.enabled}
                            onChange={(e) =>
                                setForm((prev) => ({ ...prev, enabled: e.target.checked }))
                            }
                            busy={busy}
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

interface ReservationsTabProps {
    can: (method: string, pathTemplate: string) => boolean;
}

/**
 * The Reservations tab: the registered EC2 capacity reservations a launch may target, with EC2's
 * live view of each, and Register, Edit and Unregister. The registry is VAMS's own; none of these
 * actions changes the reservation in EC2.
 */
const ReservationsTab: React.FC<ReservationsTabProps> = ({ can }) => {
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
    // The tab's controls, where the unregister confirmation finds the element it returns focus to.
    const sectionRef = useRef<HTMLElement>(null);
    // The reservation the unregister removed: its row, and the Unregister button that opened the
    // confirmation, leave the list, so focus returns to the refresh button the tab keeps.
    const unregisteredRef = useRemovedRowRef<string>(removing);
    // The form the last save closed: a registration adds a row, and the empty state's Register
    // button may leave with it, so focus returns to the refresh button.
    const savedDialogRef = useRef<typeof dialog>(null);

    const canRegister = can("POST", CAPACITY_RESERVATIONS_ROUTE);
    const canEdit = can("PUT", CAPACITY_RESERVATION_ROUTE);
    const canUnregister = can("DELETE", CAPACITY_RESERVATION_ROUTE);

    const openDialog = (next: typeof dialog) => {
        setDialogError(null);
        setDialog(next);
    };

    const registerButton = (
        <button
            type="button"
            className={btnPrimary}
            onClick={() => openDialog({ mode: "register" })}
        >
            Register reservation
        </button>
    );

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
                    const state = reservationStateText(row.original.state);
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
                    <span className="text-sm">{formatEndDate(row.original.endDate)}</span>
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
            savedDialogRef.current = dialog;
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
            unregisteredRef.current = target.capacityReservationId;
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
        <section ref={sectionRef} aria-labelledby={headingId} className="space-y-3">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <h2 id={headingId} className="text-text-primary text-lg font-semibold">
                    Capacity reservations
                </h2>
                <div className="flex items-center gap-2">
                    <RefreshButton
                        onClick={() => refetch()}
                        busy={isFetching}
                        ariaLabel={REFRESH_LABEL}
                    />
                    {/* With none registered, the empty state carries the one Register button. */}
                    {canRegister && !isLoading && (!!error || rows.length > 0) && registerButton}
                </div>
            </div>
            <p className="text-sm text-text-secondary max-w-3xl">{RESERVATIONS_DESCRIPTION}</p>

            {isLoading ? (
                <div className="p-8 text-center text-text-secondary">
                    Loading capacity reservations...
                </div>
            ) : error ? (
                <div role="alert" className="p-4 text-vams-error">
                    Error loading capacity reservations: {toastErrorMessage(error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="orch-outline p-8 rounded border border-border-default bg-surface-container flex flex-col items-center gap-3 text-center">
                    <p className="text-text-secondary">{NO_RESERVATIONS_TEXT}</p>
                    {canRegister && registerButton}
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
                    // The Register or Edit button that opened it; an edit keeps its row and button.
                    returnFocusTo={(opener) =>
                        savedDialogRef.current === dialog && dialog.mode === "register"
                            ? controlNamed(sectionRef.current, REFRESH_LABEL)
                            : opener
                    }
                />
            )}

            {removing && (
                <ConfirmActionDialog
                    open
                    title={`Unregister ${removing.label}`}
                    confirmLabel="Unregister"
                    busy={unregister.isPending}
                    busyLabel="Unregistering..."
                    onCancel={() => setRemoving(null)}
                    onConfirm={confirmUnregister}
                    returnFocusTo={(opener) =>
                        unregisteredRef.current === removing.capacityReservationId
                            ? controlNamed(sectionRef.current, REFRESH_LABEL)
                            : opener
                    }
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

export default ReservationsTab;
