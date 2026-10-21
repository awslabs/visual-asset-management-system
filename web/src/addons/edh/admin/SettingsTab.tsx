/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useId, useState } from "react";
import { btnPrimary, control } from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useEdhAdminSettings, useUpdateEdhAdminSettings } from "../api/adminQueries";
import { useInFlight } from "../components/useInFlight";

interface SettingsTabProps {
    can: (method: string, pathTemplate: string) => boolean;
}

const formatDate = (iso: string | null | undefined) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

const SettingsTab: React.FC<SettingsTabProps> = ({ can }) => {
    const toast = useToast();
    const { data, isLoading, error } = useEdhAdminSettings();
    const mutation = useUpdateEdhAdminSettings();
    const { claim, release } = useInFlight();

    const idBase = useId();
    const leaseHoursId = `${idBase}-leaseHours`;
    const leaseHoursHintId = `${idBase}-leaseHours-hint`;
    const leaseHoursErrorId = `${idBase}-leaseHours-error`;
    const graceHoursId = `${idBase}-graceHours`;
    const graceHoursHintId = `${idBase}-graceHours-hint`;
    const graceHoursErrorId = `${idBase}-graceHours-error`;

    const [enabled, setEnabled] = useState(true);
    const [leaseHours, setLeaseHours] = useState("8");
    const [forced, setForced] = useState(false);
    const [graceHours, setGraceHours] = useState("24");
    const [submitted, setSubmitted] = useState(false);

    useEffect(() => {
        if (data) {
            setEnabled(data.enabled);
            setLeaseHours(String(data.leaseHours));
            setForced(data.forced);
            setGraceHours(String(data.graceHours));
        }
    }, [data]);

    const errors: Record<string, string> = {};
    const leaseHoursNum = Number(leaseHours);
    const graceHoursNum = Number(graceHours);
    // Number("") is 0, so an empty field is refused ahead of the bounds rather than saved as none.
    const blankGrace = graceHours.trim() === "";

    if (!Number.isInteger(leaseHoursNum) || leaseHoursNum < 1 || leaseHoursNum > 720) {
        errors.leaseHours = "Lease hours must be between 1 and 720.";
    }
    if (
        blankGrace ||
        !Number.isInteger(graceHoursNum) ||
        graceHoursNum < 0 ||
        graceHoursNum > 168
    ) {
        errors.graceHours = "Grace hours must be between 0 and 168.";
    }

    const valid = Object.keys(errors).length === 0;
    const shown = submitted ? errors : {};
    const canSave = can("PUT", "/addon/edh/admin/settings");
    const disabled = mutation.isPending || !canSave;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitted(true);
        if (!valid || !claim("save")) return;

        try {
            await mutation.mutateAsync({
                enabled,
                leaseHours: leaseHoursNum,
                forced,
                graceHours: graceHoursNum,
            });
            toast.success("Lease policy saved");
            setSubmitted(false);
        } catch (err) {
            toast.error("Save failed", { description: toastErrorMessage(err) });
        } finally {
            release("save");
        }
    };

    if (isLoading) {
        return <div className="p-8 text-center text-text-secondary">Loading settings...</div>;
    }

    if (error) {
        return (
            <div className="p-4 text-vams-error">
                Error loading settings: {toastErrorMessage(error)}
            </div>
        );
    }

    if (!data) {
        return <div className="p-8 text-center text-text-secondary">No settings available.</div>;
    }

    return (
        <div className="space-y-4">
            <form onSubmit={handleSubmit} className="space-y-4 max-w-2xl">
                <div>
                    <label
                        htmlFor="enabled"
                        className="flex items-center gap-2 text-sm text-text-primary"
                    >
                        <input
                            type="checkbox"
                            id="enabled"
                            checked={enabled}
                            onChange={(e) => setEnabled(e.target.checked)}
                            disabled={disabled}
                            className="rounded"
                        />
                        <span>Enabled</span>
                    </label>
                    <p className="text-xs text-text-secondary mt-1">
                        When enabled, workstations are subject to time-boxed leases.
                    </p>
                </div>

                <div>
                    <label htmlFor={leaseHoursId} className="block text-sm text-text-primary mb-1">
                        Lease hours
                    </label>
                    <input
                        type="number"
                        id={leaseHoursId}
                        value={leaseHours}
                        onChange={(e) => setLeaseHours(e.target.value)}
                        disabled={disabled}
                        min={1}
                        max={720}
                        className={`${control} w-full max-w-xs`}
                        aria-invalid={!!shown.leaseHours}
                        aria-describedby={[
                            leaseHoursHintId,
                            shown.leaseHours ? leaseHoursErrorId : undefined,
                        ]
                            .filter(Boolean)
                            .join(" ")}
                    />
                    <p id={leaseHoursHintId} className="text-xs text-text-secondary mt-1">
                        Hours until a workstation lease expires (1-720).
                    </p>
                    {shown.leaseHours && (
                        <p id={leaseHoursErrorId} className="text-vams-error text-sm mt-1">
                            {shown.leaseHours}
                        </p>
                    )}
                </div>

                <div>
                    <label
                        htmlFor="forced"
                        className="flex items-center gap-2 text-sm text-text-primary"
                    >
                        <input
                            type="checkbox"
                            id="forced"
                            checked={forced}
                            onChange={(e) => setForced(e.target.checked)}
                            disabled={disabled}
                            className="rounded"
                        />
                        <span>Force on all launches</span>
                    </label>
                    <p className="text-xs text-text-secondary mt-1">
                        When forced, users cannot override the lease hours at launch time.
                    </p>
                </div>

                <div>
                    <label htmlFor={graceHoursId} className="block text-sm text-text-primary mb-1">
                        Grace hours
                    </label>
                    <input
                        type="number"
                        id={graceHoursId}
                        value={graceHours}
                        onChange={(e) => setGraceHours(e.target.value)}
                        disabled={disabled}
                        min={0}
                        max={168}
                        className={`${control} w-full max-w-xs`}
                        aria-invalid={!!shown.graceHours}
                        aria-describedby={[
                            graceHoursHintId,
                            shown.graceHours ? graceHoursErrorId : undefined,
                        ]
                            .filter(Boolean)
                            .join(" ")}
                    />
                    <p id={graceHoursHintId} className="text-xs text-text-secondary mt-1">
                        Hours after lease expiry before the workstation is deleted (0-168).
                    </p>
                    {shown.graceHours && (
                        <p id={graceHoursErrorId} className="text-vams-error text-sm mt-1">
                            {shown.graceHours}
                        </p>
                    )}
                </div>

                <div className="flex items-center gap-2">
                    <button type="submit" className={btnPrimary} disabled={disabled}>
                        Save
                    </button>
                    {!canSave && (
                        <span className="text-sm text-text-secondary">
                            You do not have permission to save settings.
                        </span>
                    )}
                </div>
            </form>

            {data.updatedBy && data.updatedAt && (
                <div className="text-sm text-text-secondary mt-4">
                    Last changed by {data.updatedBy} at {formatDate(data.updatedAt)}
                </div>
            )}
        </div>
    );
};

export default SettingsTab;
