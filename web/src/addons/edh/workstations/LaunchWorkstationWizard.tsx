/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useRef, useState } from "react";
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
    useEdhCatalogCapacityReservations,
    useEdhCatalogImages,
    useEdhCatalogLease,
    useEdhInstanceTypes,
    useWorkstationMutations,
} from "../api/queries";
import { LAUNCH_OUTCOME_UNKNOWN_MESSAGE } from "../api/workstations";
import type {
    CapacityReservationView,
    CatalogLeasePolicy,
    EdhCatalogImage,
    EdhCatalogImageCategory,
    EdhInstanceType,
    EdhOsFamily,
    SessionBootstrapOutcome,
} from "../types";
import { hasGpus, hoursWord, instanceTypeOptionLabel, osFamilyLabel } from "../labels";
import {
    ON_DEMAND_LABEL,
    VIEW_ONLY_HINT,
    matchingReservations,
    preselectReservation,
    reservationLaunchAllowed,
    reservationOptionLabel,
    reservationReviewText,
    reservationSelectable,
    reservationSuitsLaunch,
} from "../capacityReservations";
import { BusyButton } from "../../../features/orchestration/components/BusyControls";
import { useInFlight } from "../components/useInFlight";
import { useReturnFocus } from "../../../features/orchestration/components/useReturnFocus";

interface LaunchWorkstationWizardProps {
    open: boolean;
    onClose: () => void;
    /** Source of the launch's idempotency key; injected by tests, `crypto.randomUUID` otherwise. */
    generateBootstrapId?: () => string;
    /** The names of the caller's listed workstations, which the default name numbers past. */
    existingNames?: readonly string[];
    /**
     * Picks the element focus returns to as the wizard closes, given the element that opened it (see
     * `ReturnFocusTo`) and whether it closed after sending the launch; the opener without it.
     */
    returnFocusTo?: (
        opener: HTMLElement | null,
        launched: boolean
    ) => HTMLElement | null | undefined;
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
 * `rootSizeMin` is the chosen image's root disk floor (see `rootSizeFloor`),
 * `instanceTypeRunsImage` is false when the chosen type does not run the chosen image, and
 * `instanceTypeOfferedForImage` is false when the chosen image's recipe does not offer it.
 */
export function validateLaunchForm(
    form: LaunchForm,
    policy?: CatalogLeasePolicy,
    rootSizeMin = ROOT_SIZE_GB_MIN,
    instanceTypeRunsImage?: boolean,
    instanceTypeOfferedForImage?: boolean
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
    } else if (instanceTypeOfferedForImage === false) {
        errors.instanceType = "Choose an instance type that this image's recipe offers.";
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
    instanceTypeRunsImage?: boolean,
    instanceTypeOfferedForImage?: boolean
): string[] {
    const errors = validateLaunchForm(
        form,
        policy,
        rootSizeMin,
        instanceTypeRunsImage,
        instanceTypeOfferedForImage
    );
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

/** The longest short image name a default workstation name starts with. */
export const WORKSTATION_NAME_STEM_MAX = 40;

/**
 * The short form of an image name a default workstation name starts with: lower-cased, each run of
 * characters other than `a-z` and `0-9` replaced by one hyphen, trimmed of hyphens, and cut to
 * `WORKSTATION_NAME_STEM_MAX` characters (`VAMS AL2023 Desktop v3` reads `vams-al2023-desktop-v3`).
 */
export const workstationNameStem = (imageName: string): string =>
    imageName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, WORKSTATION_NAME_STEM_MAX)
        .replace(/-+$/, "");

/**
 * The name a launch of the image starts with: `<short image name>-<n>`, where n is the lowest number
 * from 1 that no name in `existingNames` (the caller's listed workstations) already takes with that
 * prefix; empty when the image name holds no letter or digit.
 */
export const defaultWorkstationName = (
    image: Pick<EdhCatalogImage, "name">,
    existingNames: readonly string[] = []
): string => {
    const stem = workstationNameStem(image.name ?? "");
    if (!stem) return "";
    const taken = new Set(existingNames.map((name) => name.trim().toLowerCase()));
    let n = 1;
    while (taken.has(`${stem}-${n}`)) n++;
    return `${stem}-${n}`;
};

/** Whether an image name already says the build version (`… v3` for version 3), as a word of its own. */
export const imageNameCarriesVersion = (name: string, version: number): boolean =>
    new RegExp(`(^|[^a-z0-9])v${version}($|[^0-9])`, "i").test(name);

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

/**
 * Whether the image's recipe offers an instance type: the image's `launchInstanceTypes` name it. An
 * image that carries no list (a baseline image, or a recipe that lists none) offers every type, and
 * the deployment's list and the architecture rule decide. The launch holds the type to the same list.
 */
export function instanceTypeOfferedForImage(
    type: Pick<EdhInstanceType, "instanceType">,
    image?: EdhCatalogImage | null
): boolean {
    const offered = image?.launchInstanceTypes;
    return !offered || offered.length === 0 || offered.includes(type.instanceType);
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
    { category: "amiRecipe", label: "VAMS desktops" },
    { category: "baseline", label: "Standard images" },
];

/** The family of an instance type: the part before the dot (`g5` of `g5.2xlarge`). */
const instanceFamily = (instanceType: string): string => instanceType.split(".")[0];

const orderOf = (value: number | undefined): number =>
    typeof value === "number" && Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;

/** Family by name, then size by vCPUs and memory (a type without facts last), then the type's name. */
const compareInstanceTypes = (a: EdhInstanceType, b: EdhInstanceType): number =>
    naturalCompare(instanceFamily(a.instanceType), instanceFamily(b.instanceType)) ||
    orderOf(a.vcpus) - orderOf(b.vcpus) ||
    orderOf(a.memoryGiB) - orderOf(b.memoryGiB) ||
    naturalCompare(a.instanceType, b.instanceType);

/** The instance type select's groups, in the order shown. */
export const INSTANCE_TYPE_GROUP_LABELS = {
    gpu: "GPU instance types",
    other: "Other instance types",
} as const;

/**
 * The instance types as the wizard offers them. Once the catalog states facts for any of them, the
 * types with GPUs come first and the rest follow, each group by family and then by size; a list with
 * types of one kind only is one ungrouped list in that order. Without facts (EC2 could not be read)
 * the list is the catalog's name order, ungrouped.
 */
export function groupInstanceTypes(
    types: EdhInstanceType[]
): Array<{ label: string | null; types: EdhInstanceType[] }> {
    const withFacts = types.some(
        (t) => t.gpus !== undefined || t.vcpus !== undefined || t.memoryGiB !== undefined
    );
    if (!withFacts) {
        return types.length > 0
            ? [
                  {
                      label: null,
                      types: [...types].sort((a, b) =>
                          naturalCompare(a.instanceType, b.instanceType)
                      ),
                  },
              ]
            : [];
    }
    const gpu = types.filter(hasGpus).sort(compareInstanceTypes);
    const other = types.filter((t) => !hasGpus(t)).sort(compareInstanceTypes);
    if (gpu.length === 0 || other.length === 0) {
        return [{ label: null, types: gpu.length > 0 ? gpu : other }];
    }
    return [
        { label: INSTANCE_TYPE_GROUP_LABELS.gpu, types: gpu },
        { label: INSTANCE_TYPE_GROUP_LABELS.other, types: other },
    ];
}

/**
 * The instance type a launch of the image starts with: the image's `defaultInstanceType` when the
 * wizard offers it, else none (the field keeps its prompt).
 */
export const preselectedInstanceType = (
    image: EdhCatalogImage | undefined | null,
    offered: Pick<EdhInstanceType, "instanceType">[]
): string => {
    const preferred = image?.defaultInstanceType;
    return preferred && offered.some((t) => t.instanceType === preferred) ? preferred : "";
};

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

/**
 * The Capacity choice kept across a change of instance type or image: none yet (`null`, which
 * preselects) and On-demand (`""`) always are; a reservation only while the new type and family
 * still suit it.
 */
const keepCapacityChoice = (
    choice: string | null,
    reservations: CapacityReservationView[] | undefined,
    instanceType: string,
    osFamily: EdhOsFamily | undefined
): string | null => {
    if (!choice) return choice;
    const kept = (reservations ?? []).some(
        (r) =>
            r.capacityReservationId === choice && reservationSuitsLaunch(r, instanceType, osFamily)
    );
    return kept ? choice : null;
};

// The names a wizard opened without the caller's list numbers past; one instance, a stable default.
const NO_NAMES: readonly string[] = [];

const LaunchWorkstationWizard: React.FC<LaunchWorkstationWizardProps> = ({
    open,
    onClose,
    generateBootstrapId = defaultGenerateBootstrapId,
    existingNames = NO_NAMES,
    returnFocusTo,
}) => {
    const toast = useToast();
    // Whether the wizard closes after sending the launch; read once, as it closes, so moving between
    // steps returns no focus.
    const launchedRef = useRef(false);
    const returnFocusRef = useReturnFocus(open, (opener) =>
        returnFocusTo ? returnFocusTo(opener, launchedRef.current) : opener
    );
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
    // The Capacity the user chose: null until they choose (the first free reservation is then
    // preselected), "" for On-demand, else a reservation id.
    const [capacityChoice, setCapacityChoice] = useState<string | null>(null);
    // Whether the user chose the instance type (the image's default is preselected until they do), and
    // whether they typed the name (it follows the chosen image until they do).
    const [instanceTypeChosen, setInstanceTypeChosen] = useState(false);
    const [nameEdited, setNameEdited] = useState(false);
    const instanceTypeId = useId();
    const capacityId = useId();
    const rootSizeId = useId();
    const nameId = useId();
    const leaseHoursId = useId();
    const imageGroupId = useId();

    const images = useEdhCatalogImages(open);
    const instanceTypes = useEdhInstanceTypes(open);
    const leasePolicy = useEdhCatalogLease(open);
    // A caller the route refuses, or a read that fails, gets no reservations: the launch is on-demand.
    const capacityReservations = useEdhCatalogCapacityReservations(open);
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
    const recipeNarrowsTypes = (selectedImage?.launchInstanceTypes?.length ?? 0) > 0;
    const offeredFor = (image?: EdhCatalogImage | null) =>
        instanceTypeList.filter(
            (t) => instanceTypeRunsImage(t, image) && instanceTypeOfferedForImage(t, image)
        );
    const offeredInstanceTypes = offeredFor(selectedImage);
    // The type the launch is sent with: the user's choice, else, until they choose, the image's
    // default when the wizard offers it.
    const instanceType =
        form.instanceType ||
        (instanceTypeChosen ? "" : preselectedInstanceType(selectedImage, offeredInstanceTypes));
    const launchForm = instanceType === form.instanceType ? form : { ...form, instanceType };
    // A type the loaded list does not hold is left to the launch to decide on the architecture; the
    // recipe's list names types by name, so it is checked whatever the loaded list holds.
    const chosenInstanceType = instanceTypeList.find((t) => t.instanceType === instanceType);
    const chosenTypeRunsImage =
        !chosenInstanceType || instanceTypeRunsImage(chosenInstanceType, selectedImage);
    const chosenTypeOffered =
        !instanceType || instanceTypeOfferedForImage({ instanceType }, selectedImage);
    const rootSizeMin = rootSizeFloor(selectedImage);
    // The reservations the chosen type and the image's family can launch into; the field is shown
    // only when there is one. A choice the list no longer offers reads as no choice.
    const reservationList = capacityReservations.data;
    const offeredReservations = matchingReservations(
        reservationList,
        instanceType,
        selectedImage?.osFamily
    );
    const chosenReservationOffered =
        capacityChoice !== null &&
        offeredReservations.some(
            (r) => r.capacityReservationId === capacityChoice && reservationSelectable(r)
        );
    const capacityValue =
        capacityChoice === "" || chosenReservationOffered
            ? (capacityChoice as string)
            : preselectReservation(offeredReservations);
    const chosenReservation = offeredReservations.find(
        (r) => r.capacityReservationId === capacityValue
    );
    const stepIndex = STEPS.findIndex((s) => s.id === step);
    const fieldErrors = validateLaunchForm(
        launchForm,
        policy,
        rootSizeMin,
        chosenTypeRunsImage,
        chosenTypeOffered
    );
    const stepErrors = validateLaunchStep(
        step,
        launchForm,
        policy,
        rootSizeMin,
        chosenTypeRunsImage,
        chosenTypeOffered
    );
    const shownError = (field: LaunchField) => (touched[field] ? fieldErrors[field] : undefined);
    const touch = (field: LaunchField) => setTouched((prev) => ({ ...prev, [field]: true }));

    const chooseImage = (image: EdhCatalogImage) => {
        // A chosen type the new image does not run, or whose recipe does not offer it, is cleared;
        // one it runs and offers is kept.
        const keepsType = (instanceType: string) => {
            const chosen = instanceTypeList.find((t) => t.instanceType === instanceType);
            return (
                !(chosen && !instanceTypeRunsImage(chosen, image)) &&
                instanceTypeOfferedForImage({ instanceType }, image)
            );
        };
        const typeKept = form.instanceType !== "" && keepsType(form.instanceType);
        setForm((prev) => {
            // The name follows the image until the user has typed one of their own.
            const ownName = nameEdited && prev.name.trim() !== "";
            return {
                ...prev,
                imageId: image.imageId,
                instanceType: keepsType(prev.instanceType) ? prev.instanceType : "",
                rootSizeGb: initialRootSize(image),
                name: ownName ? prev.name : defaultWorkstationName(image, existingNames),
            };
        });
        // A type the new image does not keep gives way to the new image's default.
        if (!typeKept) setInstanceTypeChosen(false);
        // A chosen reservation the new image's family (or a cleared type) does not suit is cleared.
        const nextType = typeKept
            ? form.instanceType
            : preselectedInstanceType(image, offeredFor(image));
        setCapacityChoice((prev) =>
            keepCapacityChoice(prev, reservationList, nextType, image.osFamily)
        );
    };

    const chooseInstanceType = (chosen: string) => {
        setInstanceTypeChosen(true);
        setForm((prev) => ({ ...prev, instanceType: chosen }));
        setCapacityChoice((prev) =>
            keepCapacityChoice(prev, reservationList, chosen, selectedImage?.osFamily)
        );
    };

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
                instanceType,
                name,
                rootSizeGb: form.rootSizeGb,
                osFamily: selectedImage.osFamily,
                ...(policy?.enabled && !policy.forced && form.leaseHours !== undefined
                    ? { leaseHours: form.leaseHours }
                    : {}),
                // On-demand sends no reservation, so a launch without one is the body it always was.
                ...(chosenReservation
                    ? { capacityReservationId: chosenReservation.capacityReservationId }
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
            launchedRef.current = true;
            onClose();
        } catch (err) {
            const message = toastErrorMessage(err, "Launch failed.");
            if (message === LAUNCH_OUTCOME_UNKNOWN_MESSAGE) {
                // The request may have gone through; the list poll settles it. Closing here rather
                // than inviting a retry, which the same bootstrapId would answer identically.
                toast.info("Launch outcome unknown", { description: message });
                launchedRef.current = true;
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
                {/* The stepper above names the step; the legend names the choice for a screen reader. */}
                <legend className="sr-only">Image</legend>
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
                                            image.recipeVersion != null &&
                                            !imageNameCarriesVersion(
                                                image.name,
                                                image.recipeVersion
                                            ) && (
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
                        onChange={(e) => {
                            const name = e.target.value;
                            setNameEdited(true);
                            setForm((prev) => ({ ...prev, name }));
                        }}
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
                        value={instanceType}
                        onChange={(e) => chooseInstanceType(e.target.value)}
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
                        {groupInstanceTypes(offeredInstanceTypes).map((group) => {
                            const options = group.types.map((t) => (
                                <option key={t.instanceType} value={t.instanceType}>
                                    {instanceTypeOptionLabel(t)}
                                </option>
                            ));
                            return group.label ? (
                                <optgroup key={group.label} label={group.label}>
                                    {options}
                                </optgroup>
                            ) : (
                                <React.Fragment key="all">{options}</React.Fragment>
                            );
                        })}
                    </select>
                    <p id={instanceTypeHintId} className="mt-1 text-sm text-text-secondary">
                        {imageArchitecture && recipeNarrowsTypes
                            ? `The instance types this deployment allows that run the image's architecture (${imageArchitecture}) and that its recipe offers.`
                            : imageArchitecture
                            ? `The instance types this deployment allows that run the image's architecture (${imageArchitecture}).`
                            : recipeNarrowsTypes
                            ? "The instance types this deployment allows that the image's recipe offers."
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
                            {recipeNarrowsTypes
                                ? "No instance type this deployment allows is offered for this image. Choose another image or ask an administrator."
                                : `No instance type in this deployment runs this image's architecture (${imageArchitecture}). Choose another image or ask an administrator.`}
                        </p>
                    )}
                </div>
                {offeredReservations.length > 0 && (
                    <div>
                        <label
                            htmlFor={capacityId}
                            className="block text-sm text-text-primary mb-1"
                        >
                            Capacity
                        </label>
                        <select
                            id={capacityId}
                            value={capacityValue}
                            onChange={(e) => setCapacityChoice(e.target.value)}
                            aria-describedby={`${capacityId}-hint`}
                            className={`${control} block w-full`}
                        >
                            <option value="">{ON_DEMAND_LABEL}</option>
                            {offeredReservations.map((r) => (
                                <option
                                    key={r.capacityReservationId}
                                    value={r.capacityReservationId}
                                    disabled={!reservationSelectable(r)}
                                >
                                    {reservationOptionLabel(r)}
                                </option>
                            ))}
                        </select>
                        <p id={`${capacityId}-hint`} className="mt-1 text-sm text-text-secondary">
                            Launch into a capacity reservation an administrator registered for this
                            instance type, or on-demand. A full reservation either launches the
                            workstation on-demand or cannot be chosen, as the administrator set it.
                            {offeredReservations.some((r) => !reservationLaunchAllowed(r)) &&
                                ` ${VIEW_ONLY_HINT}`}
                        </p>
                    </div>
                )}
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
                    <dd className="text-text-primary">
                        {chosenInstanceType
                            ? instanceTypeOptionLabel(chosenInstanceType)
                            : instanceType}
                    </dd>
                    {offeredReservations.length > 0 && (
                        <>
                            <dt className="text-text-secondary">Capacity</dt>
                            <dd className="text-text-primary">
                                {chosenReservation
                                    ? reservationReviewText(chosenReservation)
                                    : ON_DEMAND_LABEL}
                            </dd>
                        </>
                    )}
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

    // While the launch is in flight the wizard stays open; it closes once the launch is answered.
    const launching = createWorkstation.isPending;
    const footer = (
        <div className="flex gap-2">
            {/* Closes the dialog as its X does; nothing is launched. */}
            <BusyButton busy={launching} onClick={onClose} className={btnSecondary}>
                Cancel
            </BusyButton>
            {/* Held with Launch: going back would show editable values that are not the ones being launched. */}
            {stepIndex > 0 && (
                <BusyButton
                    busy={launching}
                    onClick={() => setStep(STEPS[stepIndex - 1].id)}
                    className={btnSecondary}
                >
                    Back
                </BusyButton>
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
                <BusyButton
                    onClick={handleLaunch}
                    busy={launching}
                    busyLabel="Launching..."
                    disabled={stepErrors.length > 0}
                    className="inline-flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm font-bold rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    Launch
                </BusyButton>
            )}
        </div>
    );

    return (
        <Dialog
            open={open}
            onOpenChange={(isOpen) => !isOpen && onClose()}
            busy={launching}
            title="Launch workstation"
            returnFocusRef={returnFocusRef}
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
