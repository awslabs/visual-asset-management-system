/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useState } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import Stepper from "../../../features/orchestration/components/Stepper";
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
    useEdhCatalogImages,
    useEdhCatalogLease,
    useEdhInstanceTypes,
    useWorkstationMutations,
} from "../api/queries";
import { LAUNCH_OUTCOME_UNKNOWN_MESSAGE } from "../api/workstations";
import type {
    CatalogLeasePolicy,
    EdhCatalogImage,
    EdhCatalogImageCategory,
    EdhInstanceType,
    EdhOsFamily,
    SessionBootstrapOutcome,
} from "../types";
import { hoursWord, osFamilyLabel } from "../labels";
import { useInFlight } from "../components/useInFlight";

interface LaunchWorkstationWizardProps {
    open: boolean;
    onClose: () => void;
    /** Source of the launch's idempotency key; injected by tests, `crypto.randomUUID` otherwise. */
    generateBootstrapId?: () => string;
}

export type LaunchStep = "image" | "size" | "review";

export interface LaunchForm {
    imageId: string;
    instanceType: string;
    rootSizeGb: number;
    name: string;
    leaseHours?: number;
}

export type LaunchField = keyof LaunchForm;

/** One message per failing field, in the wording shown under it. */
export type LaunchFieldErrors = Partial<Record<LaunchField, string>>;

/** Mirrors the request model: `object_name_pattern` for the name, the root-size bounds in GB. */
export const WORKSTATION_NAME_PATTERN = /^[a-zA-Z0-9\-._\s]{1,256}$/;
export const ROOT_SIZE_GB_MIN = 10;
export const ROOT_SIZE_GB_MAX = 2048;
export const DEFAULT_ROOT_SIZE_GB = 100;

const STEPS: { id: LaunchStep; label: string }[] = [
    { id: "image", label: "Image" },
    { id: "size", label: "Name and size" },
    { id: "review", label: "Review" },
];

// The fields each step collects; Review collects none and summarises them all.
const STEP_FIELDS: Record<LaunchStep, LaunchField[]> = {
    image: ["imageId"],
    size: ["name", "instanceType", "rootSizeGb", "leaseHours"],
    review: ["imageId", "name", "instanceType", "rootSizeGb", "leaseHours"],
};

/**
 * Every failing field of the form; the lease policy bounds an expiry the user may choose,
 * `rootSizeMin` is the chosen image's root disk floor (see `rootSizeFloor`), and
 * `instanceTypeRunsImage` is false when the chosen type does not run the chosen image.
 */
export function validateLaunchForm(
    form: LaunchForm,
    policy?: CatalogLeasePolicy,
    rootSizeMin = ROOT_SIZE_GB_MIN,
    instanceTypeRunsImage?: boolean
): LaunchFieldErrors {
    const errors: LaunchFieldErrors = {};
    if (!form.imageId) {
        errors.imageId = "Choose an image.";
    }
    if (!WORKSTATION_NAME_PATTERN.test(form.name.trim())) {
        errors.name =
            "Name must be 1-256 letters, digits, spaces, hyphens, periods or underscores.";
    }
    if (!form.instanceType) {
        errors.instanceType = "Choose an instance type.";
    } else if (instanceTypeRunsImage === false) {
        errors.instanceType = "Choose an instance type that runs this image's architecture.";
    }
    if (
        !Number.isInteger(form.rootSizeGb) ||
        form.rootSizeGb < rootSizeMin ||
        form.rootSizeGb > ROOT_SIZE_GB_MAX
    ) {
        const forImage = rootSizeMin > ROOT_SIZE_GB_MIN ? " for this image" : "";
        errors.rootSizeGb = `Root disk size must be a whole number between ${rootSizeMin} and ${ROOT_SIZE_GB_MAX} GB${forImage}.`;
    }
    if (
        policy?.enabled &&
        !policy.forced &&
        form.leaseHours !== undefined &&
        (!Number.isInteger(form.leaseHours) ||
            form.leaseHours < 1 ||
            form.leaseHours > policy.leaseHours)
    ) {
        errors.leaseHours = `Expires in must be a whole number of hours from 1 to ${policy.leaseHours}`;
    }
    return errors;
}

/** The reasons `step` cannot be left yet; empty when it can. Review is left only by a valid launch. */
export function validateLaunchStep(
    step: LaunchStep,
    form: LaunchForm,
    policy?: CatalogLeasePolicy,
    rootSizeMin = ROOT_SIZE_GB_MIN,
    instanceTypeRunsImage?: boolean
): string[] {
    const errors = validateLaunchForm(form, policy, rootSizeMin, instanceTypeRunsImage);
    return STEP_FIELDS[step].flatMap((field) => (errors[field] ? [errors[field] as string] : []));
}

const clampRootSize = (gb: number) =>
    Math.min(ROOT_SIZE_GB_MAX, Math.max(ROOT_SIZE_GB_MIN, Math.round(gb)));

/**
 * The smallest root disk the image launches with: the root size EDH registered for it when that is
 * a whole number above the minimum, capped at the maximum; otherwise the minimum. The service holds
 * a launch to the same floor, so an image registered above the maximum is refused at launch.
 */
export function rootSizeFloor(image?: EdhCatalogImage): number {
    const raw: unknown = image?.rootSize;
    let size = NaN;
    if (typeof raw === "number") size = raw;
    else if (typeof raw === "string" && /^\s*\d+\s*$/.test(raw)) size = Number(raw);
    if (!Number.isInteger(size) || size <= ROOT_SIZE_GB_MIN) return ROOT_SIZE_GB_MIN;
    return Math.min(size, ROOT_SIZE_GB_MAX);
}

/** The root size a launch of the image starts with: its registered size within the bounds. */
const initialRootSize = (image: EdhCatalogImage) => {
    const size = Number(image.rootSize ?? DEFAULT_ROOT_SIZE_GB);
    return Number.isFinite(size) ? clampRootSize(size) : DEFAULT_ROOT_SIZE_GB;
};

/** The name a launch starts with: the image's own, when the request model accepts it as a name. */
export const defaultWorkstationName = (image: EdhCatalogImage): string =>
    WORKSTATION_NAME_PATTERN.test(image.name) ? image.name : "";

/**
 * Whether an instance type runs an image: the deployment's desktop profile lists it under the
 * image's architecture. Either side unknown counts as a match, leaving the launch to decide.
 */
export function instanceTypeRunsImage(
    type: EdhInstanceType,
    image?: EdhCatalogImage | null
): boolean {
    const architecture = image?.architecture;
    const architectures = type.architectures ?? [];
    return !architecture || architectures.length === 0 || architectures.includes(architecture);
}

const defaultGenerateBootstrapId = () => crypto.randomUUID();

const DIGIT_RUN = /([0-9]+)/;

/** Compares two strings by Unicode code point rather than by UTF-16 unit. */
const compareCodePoints = (a: string, b: string): number => {
    const x = Array.from(a);
    const y = Array.from(b);
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
        const diff = (x[i].codePointAt(0) ?? 0) - (y[i].codePointAt(0) ?? 0);
        if (diff !== 0) return diff;
    }
    return x.length - y.length;
};

/** Compares two runs of digits by the numbers they write. */
const compareDigitRuns = (a: string, b: string): number => {
    const x = a.replace(/^0+/, "");
    const y = b.replace(/^0+/, "");
    if (x.length !== y.length) return x.length - y.length;
    if (x === y) return 0;
    return x < y ? -1 : 1;
};

/**
 * The catalog API's name order: each run of digits compared as a number and the text between them
 * lowercased and compared by code point, so g5.2xlarge sorts before g5.12xlarge and the wizard keeps
 * the order the API and the CLI show.
 */
export const naturalCompare = (a: string, b: string): number => {
    const x = a.split(DIGIT_RUN);
    const y = b.split(DIGIT_RUN);
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
        const diff =
            i % 2 === 1
                ? compareDigitRuns(x[i], y[i])
                : compareCodePoints(x[i].toLowerCase(), y[i].toLowerCase());
        if (diff !== 0) return diff;
    }
    return x.length - y.length;
};

/** The image step's groups, in the order shown. */
export const IMAGE_GROUPS: ReadonlyArray<{ category: EdhCatalogImageCategory; label: string }> = [
    { category: "amiRecipe", label: "Custom AMI Recipe" },
    { category: "baseline", label: "Baseline Image" },
];

/** The catalog in its groups, each sorted by name; a group without images is left out. */
export function groupCatalogImages(images: EdhCatalogImage[]) {
    return IMAGE_GROUPS.map((group) => ({
        ...group,
        images: images
            .filter(
                (image) =>
                    (image.category === "amiRecipe" ? "amiRecipe" : "baseline") === group.category
            )
            .sort((a, b) => naturalCompare(a.name, b.name)),
    })).filter((group) => group.images.length > 0);
}

/** Title of the sign-in warning a launch, start or renew shows (see `sessionBootstrapNotice`). */
export const SESSION_BOOTSTRAP_NOTICE_TITLE = "Workstation sign-in not provisioned";

/**
 * The warning for a successful launch or start that left no VAMS sign-in on the desktop, or for a
 * renew that placed no new one on a running desktop, where a sign-in it still holds keeps working
 * until it expires.
 */
export function sessionBootstrapNotice(
    outcome: SessionBootstrapOutcome | undefined,
    action: "start" | "renew" = "start",
    osFamily?: EdhOsFamily
): string | null {
    // A Windows desktop takes no VAMS sign-in, so there is nothing to warn about.
    if (osFamily === "windows") return null;
    const renew = action === "renew";
    switch (outcome) {
        case "skipped":
            return renew
                ? "Your account holds no roles, so no new VAMS sign-in was placed on the workstation. Ask an administrator to assign a role; once it is active, renew the lease again."
                : "Your account holds no roles, so no VAMS sign-in was placed on the workstation. Ask an administrator to assign a role; once it is active, stop and start it.";
        case "unavailable":
            return renew
                ? "A new VAMS sign-in could not be provisioned for the workstation. A sign-in it still holds keeps working until it expires; renew the lease again to retry."
                : "The VAMS sign-in for the workstation could not be provisioned. The workstation still starts; stop and start it to try again.";
        default:
            return null;
    }
}

const fieldErrorClass = "text-vams-error text-sm mt-1";

/** What Review says about the expiry: the forced window, the chosen one, or none at all. */
const leaseSummary = (
    policy: CatalogLeasePolicy | undefined,
    leaseHours: number | undefined
): string | null => {
    if (!policy?.enabled) return null;
    if (policy.forced) return `${hoursWord(policy.leaseHours)} (set by administrator)`;
    return leaseHours !== undefined ? hoursWord(leaseHours) : "No expiry";
};

const LaunchWorkstationWizard: React.FC<LaunchWorkstationWizardProps> = ({
    open,
    onClose,
    generateBootstrapId = defaultGenerateBootstrapId,
}) => {
    const toast = useToast();
    // One idempotency key per wizard instance: a retry after a rejected launch re-sends the same
    // key, so the service answers with the existing row instead of a second desktop.
    const [bootstrapId] = useState(() => generateBootstrapId());
    const [step, setStep] = useState<LaunchStep>("image");
    const [form, setForm] = useState<LaunchForm>({
        imageId: "",
        instanceType: "",
        rootSizeGb: DEFAULT_ROOT_SIZE_GB,
        name: "",
        leaseHours: undefined,
    });
    // A field's message shows once the field has been left, not on entering the step: until then
    // the disabled Next is the signal, as in the orchestration wizards.
    const [touched, setTouched] = useState<Partial<Record<LaunchField, boolean>>>({});
    const [launchError, setLaunchError] = useState<string | null>(null);
    const instanceTypeId = useId();
    const rootSizeId = useId();
    const nameId = useId();
    const leaseHoursId = useId();
    const imageGroupId = useId();

    const images = useEdhCatalogImages(open);
    const instanceTypes = useEdhInstanceTypes(open);
    const leasePolicy = useEdhCatalogLease(open);
    const { createWorkstation } = useWorkstationMutations();
    const { claim, release } = useInFlight();

    const imageList = images.data ?? [];
    const instanceTypeList = [...(instanceTypes.data ?? [])].sort((a, b) =>
        naturalCompare(a.instanceType, b.instanceType)
    );
    // Absent until the policy answers, which reads as no leases: nothing about an expiry is shown
    // or asked for until the terms are known.
    const policy = leasePolicy.data;
    const selectedImage = imageList.find((image) => image.imageId === form.imageId);
    const imageArchitecture = selectedImage?.architecture || null;
    const offeredInstanceTypes = instanceTypeList.filter((t) =>
        instanceTypeRunsImage(t, selectedImage)
    );
    // A type the loaded list does not hold is left to the launch to decide.
    const chosenInstanceType = instanceTypeList.find((t) => t.instanceType === form.instanceType);
    const chosenTypeRunsImage =
        !chosenInstanceType || instanceTypeRunsImage(chosenInstanceType, selectedImage);
    const rootSizeMin = rootSizeFloor(selectedImage);
    const stepIndex = STEPS.findIndex((s) => s.id === step);
    const fieldErrors = validateLaunchForm(form, policy, rootSizeMin, chosenTypeRunsImage);
    const stepErrors = validateLaunchStep(step, form, policy, rootSizeMin, chosenTypeRunsImage);
    const shownError = (field: LaunchField) => (touched[field] ? fieldErrors[field] : undefined);
    const touch = (field: LaunchField) => setTouched((prev) => ({ ...prev, [field]: true }));

    const chooseImage = (image: EdhCatalogImage) =>
        setForm((prev) => {
            // The name follows the image until the user has typed one of their own.
            const previous = imageList.find((i) => i.imageId === prev.imageId);
            const ownName =
                prev.name.trim() !== "" &&
                (!previous || prev.name !== defaultWorkstationName(previous));
            // A chosen type the new image does not run is cleared; one it runs is kept.
            const chosen = instanceTypeList.find((t) => t.instanceType === prev.instanceType);
            return {
                ...prev,
                imageId: image.imageId,
                instanceType:
                    chosen && !instanceTypeRunsImage(chosen, image) ? "" : prev.instanceType,
                rootSizeGb: initialRootSize(image),
                name: ownName ? prev.name : defaultWorkstationName(image),
            };
        });

    const handleLaunch = async () => {
        if (!selectedImage) {
            setLaunchError("The chosen image is no longer in the catalog.");
            return;
        }
        if (!claim("launch")) return;
        setLaunchError(null);
        const name = form.name.trim();
        try {
            const record = await createWorkstation.mutateAsync({
                bootstrapId,
                imageId: form.imageId,
                instanceType: form.instanceType,
                name,
                rootSizeGb: form.rootSizeGb,
                osFamily: selectedImage.osFamily,
                ...(policy?.enabled && !policy.forced && form.leaseHours !== undefined
                    ? { leaseHours: form.leaseHours }
                    : {}),
            });
            // The wizard closes on success, so the toasts are the only confirmation.
            toast.success("Workstation launch requested", { description: name });
            const notice = sessionBootstrapNotice(
                record.sessionBootstrap,
                "start",
                selectedImage.osFamily
            );
            if (notice) {
                // Persists until dismissed: the user has to act on it (stop/start, or ask an admin).
                toast.warning(SESSION_BOOTSTRAP_NOTICE_TITLE, {
                    description: notice,
                    duration: 0,
                });
            }
            onClose();
        } catch (err) {
            const message = toastErrorMessage(err, "Launch failed.");
            if (message === LAUNCH_OUTCOME_UNKNOWN_MESSAGE) {
                // The request may have gone through; the list poll settles it. Closing here rather
                // than inviting a retry, which the same bootstrapId would answer identically.
                toast.info("Launch outcome unknown", { description: message });
                onClose();
                return;
            }
            // Inline on the Review step (next to the Launch button that failed) AND a toast.
            setLaunchError(message);
            toast.error("Launch failed", { description: `${name}: ${message}` });
        } finally {
            release("launch");
        }
    };

    const renderImageStep = () => {
        if (images.isLoading) {
            return (
                <p role="status" className="text-text-secondary">
                    Loading images…
                </p>
            );
        }
        if (images.error) {
            return (
                <p role="alert" className="text-vams-error">
                    Error loading images: {toastErrorMessage(images.error)}
                </p>
            );
        }
        if (imageList.length === 0) {
            return <p className="text-text-secondary">No images are available to launch.</p>;
        }
        return (
            <fieldset className="space-y-4 border-0 p-0 m-0">
                <legend className="text-sm font-semibold text-text-primary mb-2">Image</legend>
                {groupCatalogImages(imageList).map((group) => {
                    const headingId = `${imageGroupId}-${group.category}`;
                    return (
                        <div
                            key={group.category}
                            role="group"
                            aria-labelledby={headingId}
                            className="space-y-2"
                        >
                            <p id={headingId} className="text-sm font-semibold text-text-secondary">
                                {group.label}
                            </p>
                            {group.images.map((image) => (
                                <label
                                    key={image.imageId}
                                    className="orch-outline flex items-start gap-3 p-3 border border-border-default rounded cursor-pointer hover:bg-surface-hover"
                                >
                                    <input
                                        type="radio"
                                        name="edh-image"
                                        value={image.imageId}
                                        checked={form.imageId === image.imageId}
                                        onChange={() => chooseImage(image)}
                                        className="mt-1"
                                    />
                                    <span className="flex-1">
                                        <span className="font-semibold text-text-primary">
                                            {image.name}
                                        </span>
                                        <span className="ml-2 px-2 py-0.5 text-xs rounded bg-surface-secondary text-text-secondary">
                                            {osFamilyLabel(image.osFamily)}
                                        </span>
                                        {image.architecture ? (
                                            <span className="ml-2 px-2 py-0.5 text-xs rounded bg-surface-secondary text-text-secondary">
                                                {image.architecture}
                                            </span>
                                        ) : null}
                                        {image.category === "amiRecipe" &&
                                            image.recipeVersion != null && (
                                                <span className="ml-2 px-2 py-0.5 text-xs rounded bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                                                    {`v${image.recipeVersion}`}
                                                </span>
                                            )}
                                        {image.description && (
                                            <span className="block text-sm text-text-secondary mt-1">
                                                {image.description}
                                            </span>
                                        )}
                                    </span>
                                </label>
                            ))}
                        </div>
                    );
                })}
            </fieldset>
        );
    };

    const renderSizeStep = () => {
        const nameError = shownError("name");
        const instanceTypeError = shownError("instanceType");
        const rootSizeError = shownError("rootSizeGb");
        const leaseHoursError = shownError("leaseHours");
        const nameErrorId = `${nameId}-error`;
        const instanceTypeHintId = `${instanceTypeId}-hint`;
        const instanceTypeErrorId = `${instanceTypeId}-error`;
        const rootSizeErrorId = `${rootSizeId}-error`;
        const leaseHoursHintId = `${leaseHoursId}-hint`;
        const leaseHoursErrorId = `${leaseHoursId}-error`;
        return (
            <div className="space-y-4">
                <div>
                    <label htmlFor={nameId} className="block text-sm text-text-primary mb-1">
                        Workstation name
                    </label>
                    <input
                        id={nameId}
                        type="text"
                        required
                        value={form.name}
                        maxLength={256}
                        onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                        onBlur={() => touch("name")}
                        aria-invalid={nameError ? true : undefined}
                        aria-describedby={nameError ? nameErrorId : undefined}
                        className={`${control} block w-full`}
                    />
                    {nameError && (
                        <p id={nameErrorId} className={fieldErrorClass}>
                            {nameError}
                        </p>
                    )}
                </div>
                <div>
                    <label
                        htmlFor={instanceTypeId}
                        className="block text-sm text-text-primary mb-1"
                    >
                        Instance type
                    </label>
                    <select
                        id={instanceTypeId}
                        required
                        value={form.instanceType}
                        onChange={(e) =>
                            setForm((prev) => ({ ...prev, instanceType: e.target.value }))
                        }
                        onBlur={() => touch("instanceType")}
                        disabled={instanceTypes.isLoading}
                        aria-invalid={instanceTypeError ? true : undefined}
                        aria-describedby={
                            instanceTypeError
                                ? `${instanceTypeHintId} ${instanceTypeErrorId}`
                                : instanceTypeHintId
                        }
                        className={`${control} block w-full`}
                    >
                        <option value="">
                            {instanceTypes.isLoading
                                ? "Loading instance types…"
                                : "Choose an instance type"}
                        </option>
                        {offeredInstanceTypes.map((t) => (
                            <option key={t.instanceType} value={t.instanceType}>
                                {t.instanceType}
                            </option>
                        ))}
                    </select>
                    <p id={instanceTypeHintId} className="mt-1 text-sm text-text-secondary">
                        {imageArchitecture
                            ? `The instance types this deployment allows that run the image's architecture (${imageArchitecture}).`
                            : "The instance types this deployment allows for the desktop profile."}{" "}
                        Larger types carry more vCPUs and GPU memory and cost more while the
                        workstation runs.
                    </p>
                    {instanceTypeError && (
                        <p id={instanceTypeErrorId} className={fieldErrorClass}>
                            {instanceTypeError}
                        </p>
                    )}
                    {instanceTypes.error && (
                        <p role="alert" className="mt-1 text-sm text-vams-error">
                            Error loading instance types: {toastErrorMessage(instanceTypes.error)}
                        </p>
                    )}
                    {!instanceTypes.isLoading &&
                        !instanceTypes.error &&
                        instanceTypeList.length === 0 && (
                            <p className="mt-1 text-sm text-text-secondary">
                                No instance types are available in this deployment.
                            </p>
                        )}
                    {instanceTypeList.length > 0 && offeredInstanceTypes.length === 0 && (
                        <p className="mt-1 text-sm text-text-secondary">
                            {`No instance type in this deployment runs this image's architecture (${imageArchitecture}). Choose another image or ask an administrator.`}
                        </p>
                    )}
                </div>
                <div>
                    <label htmlFor={rootSizeId} className="block text-sm text-text-primary mb-1">
                        Root disk size (GB)
                    </label>
                    <input
                        id={rootSizeId}
                        type="number"
                        required
                        min={rootSizeMin}
                        max={ROOT_SIZE_GB_MAX}
                        step={1}
                        value={form.rootSizeGb}
                        onChange={(e) =>
                            setForm((prev) => ({ ...prev, rootSizeGb: Number(e.target.value) }))
                        }
                        onBlur={() => touch("rootSizeGb")}
                        aria-invalid={rootSizeError ? true : undefined}
                        aria-describedby={rootSizeError ? rootSizeErrorId : undefined}
                        className={`${control} block w-40`}
                    />
                    {rootSizeError && (
                        <p id={rootSizeErrorId} className={fieldErrorClass}>
                            {rootSizeError}
                        </p>
                    )}
                </div>
                {policy?.enabled && !policy.forced && (
                    <div>
                        <label
                            htmlFor={leaseHoursId}
                            className="block text-sm text-text-primary mb-1"
                        >
                            Expires in (hours)
                        </label>
                        <input
                            id={leaseHoursId}
                            type="number"
                            min={1}
                            max={policy.leaseHours}
                            step={1}
                            value={form.leaseHours ?? ""}
                            onChange={(e) =>
                                setForm((prev) => ({
                                    ...prev,
                                    leaseHours: e.target.value ? Number(e.target.value) : undefined,
                                }))
                            }
                            onBlur={() => touch("leaseHours")}
                            aria-invalid={leaseHoursError ? true : undefined}
                            aria-describedby={
                                leaseHoursError
                                    ? `${leaseHoursHintId} ${leaseHoursErrorId}`
                                    : leaseHoursHintId
                            }
                            className={`${control} block w-40`}
                        />
                        <p id={leaseHoursHintId} className="mt-1 text-sm text-text-secondary">
                            Optional. Up to {hoursWord(policy.leaseHours)}; leave blank for no
                            expiry.
                        </p>
                        {leaseHoursError && (
                            <p id={leaseHoursErrorId} className={fieldErrorClass}>
                                {leaseHoursError}
                            </p>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const renderReviewStep = () => {
        const expiry = leaseSummary(policy, form.leaseHours);
        return (
            <div className="space-y-4">
                <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-sm">
                    <dt className="text-text-secondary">Name</dt>
                    <dd className="text-text-primary">{form.name.trim()}</dd>
                    <dt className="text-text-secondary">Image</dt>
                    <dd className="text-text-primary">{selectedImage?.name ?? form.imageId}</dd>
                    <dt className="text-text-secondary">OS</dt>
                    <dd className="text-text-primary">{osFamilyLabel(selectedImage?.osFamily)}</dd>
                    <dt className="text-text-secondary">Architecture</dt>
                    <dd className="text-text-primary">{imageArchitecture ?? "-"}</dd>
                    <dt className="text-text-secondary">Instance type</dt>
                    <dd className="text-text-primary">{form.instanceType}</dd>
                    <dt className="text-text-secondary">Root disk</dt>
                    <dd className="text-text-primary">{form.rootSizeGb} GB</dd>
                    {expiry && (
                        <>
                            <dt className="text-text-secondary">Expires in</dt>
                            <dd className="text-text-primary">{expiry}</dd>
                        </>
                    )}
                </dl>
                {launchError && (
                    <div
                        role="alert"
                        className="p-4 bg-red-100 dark:bg-red-900/20 border border-red-400 dark:border-red-700 rounded text-red-900 dark:text-red-200"
                    >
                        <strong>Launch failed:</strong> {launchError}
                    </div>
                )}
            </div>
        );
    };

    const renderStep = () => {
        if (step === "image") return renderImageStep();
        if (step === "size") return renderSizeStep();
        return renderReviewStep();
    };

    const footer = (
        <div className="flex gap-2">
            {stepIndex > 0 && (
                <button onClick={() => setStep(STEPS[stepIndex - 1].id)} className={btnSecondary}>
                    Back
                </button>
            )}
            {stepIndex < STEPS.length - 1 && (
                <button
                    onClick={() => setStep(STEPS[stepIndex + 1].id)}
                    disabled={stepErrors.length > 0}
                    className={btnPrimary}
                >
                    Next
                </button>
            )}
            {stepIndex === STEPS.length - 1 && (
                <button
                    onClick={handleLaunch}
                    disabled={createWorkstation.isPending || stepErrors.length > 0}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    {createWorkstation.isPending ? "Launching..." : "Launch"}
                </button>
            )}
        </div>
    );

    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onClose()}
            title="Launch workstation"
            footer={footer}
        >
            <div className="space-y-4">
                <Stepper steps={STEPS} current={step} />
                <div className="min-h-[280px]">{renderStep()}</div>
            </div>
        </Dialog>
    );
};

export default LaunchWorkstationWizard;
