/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useState } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import {
    btnPrimary,
    btnSecondary,
    control,
} from "../../../features/orchestration/components/controlStyles";
import type {
    EdhDesktopProfile,
    EdhImageKind,
    RegisterSimulationImageRequest,
    RegisterVdiImageRequest,
} from "../api/admin";

export type RegisterImageBody = RegisterVdiImageRequest | RegisterSimulationImageRequest;

interface RegisterImageDialogProps {
    kind: EdhImageKind;
    open: boolean;
    busy: boolean;
    onOpenChange: (open: boolean) => void;
    onSubmit: (body: RegisterImageBody) => Promise<void>;
    /** The virtual desktop profiles a VDI image may name; offered as a picker when present. */
    profiles?: EdhDesktopProfile[];
}

// The same shape the backend model checks (models/edh.py edh_ami_id_pattern).
const AMI_PATTERN = /^ami-[0-9a-f]{8,17}$/;

/**
 * The base OS keys EDH accepts for a dcv software stack (`DCV_BASE_OS` in EDH's config), with the
 * names EDH shows for them; the keys EDH itself hides are left out.
 */
export const BASE_OS_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
    { value: "ubuntu2404", label: "Ubuntu 24.04" },
    { value: "ubuntu2204", label: "Ubuntu 22.04" },
    { value: "amazonlinux2", label: "Amazon Linux 2" },
    { value: "amazonlinux2023", label: "Amazon Linux 2023" },
    { value: "rocky9", label: "Rocky Linux 9" },
    { value: "rocky8", label: "Rocky Linux 8" },
    { value: "rhel9", label: "Red Hat Enterprise Linux 9" },
    { value: "rhel8", label: "Red Hat Enterprise Linux 8" },
    { value: "windows2019", label: "Windows Server 2019" },
    { value: "windows2022", label: "Windows Server 2022" },
    { value: "windows2025", label: "Windows Server 2025" },
];

/** The EC2 tenancies EDH accepts without a dedicated host id; an empty value leaves it to EDH. */
export const LAUNCH_TENANCY_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
    { value: "", label: "Default (shared hardware)" },
    { value: "dedicated", label: "Dedicated instance" },
];

type FieldName = "amiId" | "stackName" | "baseOs" | "rootSize" | "profileId" | "userDataTemplateId";

type FieldErrors = Partial<Record<FieldName, string>>;

interface FormValues {
    amiId: string;
    stackName: string;
    baseOs: string;
    rootSize: string;
    profileId: string;
    userDataTemplateId: string;
}

/** One message per failing field, in the wording shown under it. */
export function validateRegisterImage(
    values: FormValues,
    kind: EdhImageKind,
    profilePicker: boolean,
    offeredProfileIds: readonly string[] = []
): FieldErrors {
    const errors: FieldErrors = {};
    if (!AMI_PATTERN.test(values.amiId.trim())) {
        errors.amiId =
            "Must look like ami- followed by 8 to 17 hex characters, e.g. ami-0123456789abcdef0.";
    }
    if (values.stackName.trim() === "") {
        errors.stackName = "Enter a stack name.";
    }
    if (kind === "vdi" && values.baseOs.trim() === "") {
        errors.baseOs = "Choose a base OS.";
    }
    const rootSize = Number(values.rootSize);
    if (values.rootSize.trim() === "" || !Number.isInteger(rootSize) || rootSize < 1) {
        errors.rootSize = "Enter a whole number of GB, 1 or greater.";
    }
    const profile = Number(values.profileId);
    if (profilePicker) {
        // A value typed into the number field before the picker replaced it is not one the picker offers.
        if (!offeredProfileIds.includes(values.profileId.trim())) {
            errors.profileId = "Choose a profile.";
        }
    } else if (values.profileId.trim() === "" || !Number.isInteger(profile) || profile < 0) {
        errors.profileId = "Enter the profile id, a whole number 0 or greater.";
    }
    if (kind === "simulation") {
        const template = Number(values.userDataTemplateId);
        if (
            values.userDataTemplateId.trim() === "" ||
            !Number.isInteger(template) ||
            template < 1
        ) {
            errors.userDataTemplateId = "Enter a whole number, 1 or greater.";
        }
    }
    return errors;
}

/** The base OS as this dialog names it; an unlisted key is shown as it is. */
export const baseOsLabel = (baseOs: string | null | undefined) =>
    BASE_OS_OPTIONS.find((option) => option.value === baseOs)?.label || baseOs || "—";

const profileOptionLabel = (profile: EdhDesktopProfile) =>
    profile.profile_name ? `${profile.profile_name} (id ${profile.id})` : `id ${profile.id}`;

export const labelClass = "block text-sm text-text-primary";

interface FieldProps {
    id: string;
    label: string;
    /** Shown under the control in every state. */
    hint?: string;
    /** Shown under the control in the error tone; the control names it through aria-describedby. */
    error?: string;
    children: (describedBy: string | undefined) => React.ReactNode;
}

/** One labelled control with its hint and its validation message, both kept out of the label text. */
export const Field: React.FC<FieldProps> = ({ id, label, hint, error, children }) => {
    const hintId = `${id}-hint`;
    const errorId = `${id}-error`;
    const describedBy =
        [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
    return (
        <div>
            <label htmlFor={id} className={labelClass}>
                <span className="block mb-1">{label}</span>
                {children(describedBy)}
            </label>
            {hint && (
                <p id={hintId} className="text-xs text-text-secondary mt-1">
                    {hint}
                </p>
            )}
            {error && (
                <p id={errorId} className="text-vams-error text-sm mt-1">
                    {error}
                </p>
            )}
        </div>
    );
};

/**
 * Register a VDI (dcv) or simulation (target_nodes) software stack. Text and number fields only:
 * the body is JSON, and EDH's optional thumbnail part is not offered. A simulation stack names the
 * EDH user-data template it boots with; EDH's protected default template is 1. Validation messages
 * appear under their fields after the first Register attempt.
 */
const RegisterImageDialog: React.FC<RegisterImageDialogProps> = ({
    kind,
    open,
    busy,
    onOpenChange,
    onSubmit,
    profiles,
}) => {
    const isVdi = kind === "vdi";
    const formId = useId();
    const amiIdId = useId();
    const stackNameId = useId();
    const baseOsId = useId();
    const rootSizeId = useId();
    const profileIdId = useId();
    const launchTenancyId = useId();
    const userDataTemplateIdId = useId();
    const userDataVariablesId = useId();
    const descriptionId = useId();

    const [amiId, setAmiId] = useState("");
    const [stackName, setStackName] = useState("");
    const [baseOs, setBaseOs] = useState("");
    const [rootSize, setRootSize] = useState("100");
    const [profileId, setProfileId] = useState("");
    const [launchTenancy, setLaunchTenancy] = useState("");
    const [userDataTemplateId, setUserDataTemplateId] = useState("1");
    const [userDataVariables, setUserDataVariables] = useState("");
    const [description, setDescription] = useState("");
    const [submitted, setSubmitted] = useState(false);

    // The picker is offered for a VDI image when the capacity view handed over profiles.
    const profilePicker = isVdi && !!profiles && profiles.length > 0;
    const errors = validateRegisterImage(
        { amiId, stackName, baseOs, rootSize, profileId, userDataTemplateId },
        kind,
        profilePicker,
        profilePicker ? profiles!.map((profile) => String(profile.id)) : []
    );
    const valid = Object.keys(errors).length === 0;
    const shown: FieldErrors = submitted ? errors : {};
    const shownCount = Object.keys(shown).length;

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitted(true);
        if (!valid) return;
        const shared = {
            amiId: amiId.trim(),
            stackName: stackName.trim(),
            rootSize: Number(rootSize),
            ...(description.trim() ? { description: description.trim() } : {}),
        };
        const body: RegisterImageBody = isVdi
            ? {
                  ...shared,
                  baseOs: baseOs.trim(),
                  virtualDesktopProfileId: Number(profileId),
                  ...(launchTenancy ? { launchTenancy } : {}),
              }
            : {
                  ...shared,
                  targetNodeProfileId: Number(profileId),
                  targetNodeUserDataId: Number(userDataTemplateId),
                  ...(userDataVariables.trim()
                      ? { userDataVariables: userDataVariables.trim() }
                      : {}),
              };
        await onSubmit(body);
    };

    return (
        <Dialog
            open={open}
            onOpenChange={onOpenChange}
            title={isVdi ? "Register VDI image" : "Register simulation image"}
            footer={
                <>
                    <button
                        type="button"
                        className={btnSecondary}
                        onClick={() => onOpenChange(false)}
                    >
                        Cancel
                    </button>
                    <button type="submit" form={formId} className={btnPrimary} disabled={busy}>
                        Register
                    </button>
                </>
            }
        >
            <form id={formId} onSubmit={submit} noValidate className="space-y-3">
                {shownCount > 0 && (
                    <div role="alert" className="text-sm text-vams-error">
                        Correct the {shownCount === 1 ? "highlighted field" : "highlighted fields"}{" "}
                        below.
                    </div>
                )}
                <Field id={amiIdId} label="AMI ID" error={shown.amiId}>
                    {(describedBy) => (
                        <input
                            id={amiIdId}
                            className={`${control} w-full`}
                            value={amiId}
                            onChange={(e) => setAmiId(e.target.value)}
                            placeholder="ami-0123456789abcdef0"
                            aria-invalid={!!shown.amiId}
                            aria-describedby={describedBy}
                        />
                    )}
                </Field>
                <Field id={stackNameId} label="Stack name" error={shown.stackName}>
                    {(describedBy) => (
                        <input
                            id={stackNameId}
                            className={`${control} w-full`}
                            value={stackName}
                            onChange={(e) => setStackName(e.target.value)}
                            aria-invalid={!!shown.stackName}
                            aria-describedby={describedBy}
                        />
                    )}
                </Field>
                {isVdi && (
                    <Field id={baseOsId} label="Base OS" error={shown.baseOs}>
                        {(describedBy) => (
                            <select
                                id={baseOsId}
                                className={`${control} w-full`}
                                value={baseOs}
                                onChange={(e) => setBaseOs(e.target.value)}
                                aria-invalid={!!shown.baseOs}
                                aria-describedby={describedBy}
                            >
                                <option value="">Choose a base OS</option>
                                {BASE_OS_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>
                                        {option.label}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                )}
                <Field id={rootSizeId} label="Root size (GB)" error={shown.rootSize}>
                    {(describedBy) => (
                        <input
                            id={rootSizeId}
                            type="number"
                            min={1}
                            className={`${control} w-full`}
                            value={rootSize}
                            onChange={(e) => setRootSize(e.target.value)}
                            aria-invalid={!!shown.rootSize}
                            aria-describedby={describedBy}
                        />
                    )}
                </Field>
                {profilePicker ? (
                    <Field id={profileIdId} label="Virtual desktop profile" error={shown.profileId}>
                        {(describedBy) => (
                            <select
                                id={profileIdId}
                                className={`${control} w-full`}
                                value={profileId}
                                onChange={(e) => setProfileId(e.target.value)}
                                aria-invalid={!!shown.profileId}
                                aria-describedby={describedBy}
                            >
                                <option value="">Choose a profile</option>
                                {profiles!.map((profile) => (
                                    <option key={String(profile.id)} value={String(profile.id)}>
                                        {profileOptionLabel(profile)}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                ) : (
                    <Field
                        id={profileIdId}
                        label={isVdi ? "Virtual desktop profile ID" : "Target node profile ID"}
                        hint={
                            isVdi
                                ? "Profile ids are listed on the Capacity tab."
                                : "The id of an EDH target-node profile."
                        }
                        error={shown.profileId}
                    >
                        {(describedBy) => (
                            <input
                                id={profileIdId}
                                type="number"
                                min={0}
                                className={`${control} w-full`}
                                value={profileId}
                                onChange={(e) => setProfileId(e.target.value)}
                                aria-invalid={!!shown.profileId}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                )}
                {isVdi ? (
                    <Field id={launchTenancyId} label="Launch tenancy">
                        {(describedBy) => (
                            <select
                                id={launchTenancyId}
                                className={`${control} w-full`}
                                value={launchTenancy}
                                onChange={(e) => setLaunchTenancy(e.target.value)}
                                aria-describedby={describedBy}
                            >
                                {LAUNCH_TENANCY_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>
                                        {option.label}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                ) : (
                    <>
                        <Field
                            id={userDataTemplateIdId}
                            label="User data template ID"
                            error={shown.userDataTemplateId}
                        >
                            {(describedBy) => (
                                <input
                                    id={userDataTemplateIdId}
                                    type="number"
                                    min={1}
                                    className={`${control} w-full`}
                                    value={userDataTemplateId}
                                    onChange={(e) => setUserDataTemplateId(e.target.value)}
                                    aria-invalid={!!shown.userDataTemplateId}
                                    aria-describedby={describedBy}
                                />
                            )}
                        </Field>
                        <Field
                            id={userDataVariablesId}
                            label="User data variables (optional)"
                            hint="key=value pairs separated by commas, e.g. env=prod,team=sim."
                        >
                            {(describedBy) => (
                                <textarea
                                    id={userDataVariablesId}
                                    className={`${control} w-full`}
                                    rows={3}
                                    value={userDataVariables}
                                    onChange={(e) => setUserDataVariables(e.target.value)}
                                    placeholder="env=prod,team=sim"
                                    aria-describedby={describedBy}
                                />
                            )}
                        </Field>
                    </>
                )}
                <Field id={descriptionId} label="Description (optional)">
                    {(describedBy) => (
                        <input
                            id={descriptionId}
                            className={`${control} w-full`}
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            aria-describedby={describedBy}
                        />
                    )}
                </Field>
            </form>
        </Dialog>
    );
};

export default RegisterImageDialog;
