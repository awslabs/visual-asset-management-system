/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useId, useRef, useState } from "react";
import Dialog from "../../../features/orchestration/components/Dialog";
import ConfigEditor from "../../../features/orchestration/components/ConfigEditor";
import {
    btnPrimary,
    btnSecondary,
    control,
} from "../../../features/orchestration/components/controlStyles";
import { BASE_OS_OPTIONS, Field, labelClass } from "./RegisterImageDialog";
import { OS_FAMILY_LABEL } from "../labels";
import type {
    EdhAmiRecipe,
    EdhAmiRecipeComponent,
    EdhAmiRecipeKind,
    EdhAmiRecipeRequest,
    EdhArchitecture,
    EdhOsFamily,
} from "../types";

interface AmiRecipeFormDialogProps {
    open: boolean;
    busy: boolean;
    /** The recipe being edited; absent for a new one. A config recipe opens read-only. */
    initial?: EdhAmiRecipe;
    /** The recipe a new one is duplicated from; the form opens in create mode prefilled from it. */
    duplicateSource?: EdhAmiRecipe;
    onOpenChange: (open: boolean) => void;
    onSubmit: (body: EdhAmiRecipeRequest) => Promise<void>;
}

/** The base OS keys of each family, from the set EDH accepts for a dcv software stack. */
export const BASE_OS_BY_FAMILY: Record<
    EdhOsFamily,
    ReadonlyArray<{ value: string; label: string }>
> = {
    linux: BASE_OS_OPTIONS.filter((option) => !option.value.startsWith("windows")),
    windows: BASE_OS_OPTIONS.filter((option) => option.value.startsWith("windows")),
};

const KIND_OPTIONS: ReadonlyArray<{ value: EdhAmiRecipeKind; label: string }> = [
    { value: "vdi", label: "VDI desktop" },
    { value: "simulation", label: "Simulation" },
];

const ARCHITECTURE_OPTIONS: ReadonlyArray<{ value: EdhArchitecture; label: string }> = [
    { value: "x86_64", label: "x86_64" },
    { value: "arm64", label: "arm64" },
];

/** The architectures offered for an OS family; a stored value outside them stays listed. */
const architectureOptions = (osFamily: string, current: string) => {
    if (osFamily !== "windows") return ARCHITECTURE_OPTIONS;
    const offered = (value: string) => value === "x86_64" || value === current;
    return ARCHITECTURE_OPTIONS.filter((option) => offered(option.value));
};

// The shapes the backend model checks (models/edh.py).
const RECIPE_ID = /^[a-z0-9][a-z0-9-]{1,39}$/;
const PARENT =
    /^(ami-[0-9a-f]{8,17}|ssm:\/aws\/service\/[A-Za-z0-9/_.-]{1,1000}|edhStack:[A-Za-z0-9\-._ ]{1,256})$/;
const INSTANCE_TYPE = /^[a-z0-9-]+\.[a-z0-9-]+$/;
const INSTANCE_TYPE_MAX_LENGTH = 64;
const MAX_BUILD_INSTANCE_TYPES = 8;
const MAX_LAUNCH_INSTANCE_TYPES = 64;
const ROOT_SIZE_GB_MIN = 10;
const ROOT_SIZE_GB_MAX = 2048;
const KEEP_VERSIONS_MIN = 1;
const KEEP_VERSIONS_MAX = 20;
const MAX_COMPONENTS = 20;
const MAX_DISPLAY_NAME = 256;
const MAX_STACK_NAME = 200;
const MAX_DESCRIPTION = 256;

/** One entry of the Components list: a managed component ARN, or an inline document. */
export interface AmiRecipeComponentEntry {
    mode: "arn" | "inline";
    arn: string;
    name: string;
    data: string;
}

interface FormValues {
    recipeId: string;
    displayName: string;
    kind: string;
    osFamily: string;
    baseOs: string;
    architecture: string;
    parentImage: string;
    buildInstanceType: string;
    /** The launch instance types as typed, separated by commas; empty restricts nothing. */
    launchInstanceTypes: string;
    rootSizeGb: string;
    stackName: string;
    profileId: string;
    keepVersions: string;
    recipeRevision: string;
    description: string;
    components: ReadonlyArray<AmiRecipeComponentEntry>;
}

type FieldName = keyof FormValues;

type FieldErrors = Partial<Record<FieldName, string>>;

/** A whole number in the closed range, from a field that holds it as text. */
const wholeNumberIn = (value: string, min: number, max: number) => {
    const parsed = Number(value);
    return value.trim() !== "" && Number.isInteger(parsed) && parsed >= min && parsed <= max;
};

/** The build instance types the field holds: one, or several separated by commas. */
const splitInstanceTypes = (text: string) => text.split(",").map((type) => type.trim());

/** The launch instance types the field holds; an empty field holds none. */
const splitLaunchInstanceTypes = (text: string) =>
    text.trim() === "" ? [] : splitInstanceTypes(text);

/** One message per failing field, in the wording shown under it. */
export function validateAmiRecipe(values: FormValues): FieldErrors {
    const errors: FieldErrors = {};
    if (!RECIPE_ID.test(values.recipeId.trim())) {
        errors.recipeId =
            "Lowercase letters, digits and hyphens, 2 to 40 characters, starting with a letter or a digit.";
    }
    if (values.displayName.trim() === "") {
        errors.displayName = "Enter a display name.";
    } else if (values.displayName.trim().length > MAX_DISPLAY_NAME) {
        errors.displayName = `${MAX_DISPLAY_NAME} characters at most.`;
    }
    if (values.kind === "vdi") {
        const baseOs = values.baseOs.trim();
        const offered = BASE_OS_BY_FAMILY[values.osFamily as EdhOsFamily] ?? [];
        if (baseOs === "") {
            errors.baseOs = "Choose a base OS.";
        } else if (!offered.some((option) => option.value === baseOs)) {
            errors.baseOs = "That base OS belongs to the other OS family.";
        }
    }
    // The Amazon DCV server for Windows runs on x86_64 only.
    if (values.osFamily === "windows" && values.architecture !== "x86_64") {
        errors.architecture = "Windows recipes support x86_64 only.";
    }
    if (!PARENT.test(values.parentImage.trim())) {
        errors.parentImage =
            "Must be an AMI id, an ssm:/aws/service/ parameter path, or edhStack: followed by an EDH stack name.";
    }
    const instanceTypes = splitInstanceTypes(values.buildInstanceType);
    if (
        instanceTypes.length > MAX_BUILD_INSTANCE_TYPES ||
        new Set(instanceTypes).size !== instanceTypes.length ||
        !instanceTypes.every(
            (type) => type.length <= INSTANCE_TYPE_MAX_LENGTH && INSTANCE_TYPE.test(type)
        )
    ) {
        errors.buildInstanceType = `Enter an EC2 instance type, e.g. m7i.large, or up to ${MAX_BUILD_INSTANCE_TYPES} different ones separated by commas.`;
    }
    const launchTypes = splitLaunchInstanceTypes(values.launchInstanceTypes);
    if (
        launchTypes.length > MAX_LAUNCH_INSTANCE_TYPES ||
        new Set(launchTypes).size !== launchTypes.length ||
        !launchTypes.every(
            (type) => type.length <= INSTANCE_TYPE_MAX_LENGTH && INSTANCE_TYPE.test(type)
        )
    ) {
        errors.launchInstanceTypes = `Enter up to ${MAX_LAUNCH_INSTANCE_TYPES} different EC2 instance types separated by commas, e.g. g5.2xlarge, g6e.xlarge, or leave it empty.`;
    }
    if (!wholeNumberIn(values.rootSizeGb, ROOT_SIZE_GB_MIN, ROOT_SIZE_GB_MAX)) {
        errors.rootSizeGb = `Enter a whole number of GB between ${ROOT_SIZE_GB_MIN} and ${ROOT_SIZE_GB_MAX}.`;
    }
    if (values.stackName.trim() === "") {
        errors.stackName = "Enter a stack name.";
    } else if (values.stackName.trim().length > MAX_STACK_NAME) {
        errors.stackName = `${MAX_STACK_NAME} characters at most.`;
    }
    if (!wholeNumberIn(values.profileId, 0, Number.MAX_SAFE_INTEGER)) {
        errors.profileId = "Enter the profile id, a whole number 0 or greater.";
    }
    if (
        values.keepVersions.trim() !== "" &&
        !wholeNumberIn(values.keepVersions, KEEP_VERSIONS_MIN, KEEP_VERSIONS_MAX)
    ) {
        errors.keepVersions = `Enter a whole number between ${KEEP_VERSIONS_MIN} and ${KEEP_VERSIONS_MAX}, or leave it empty.`;
    }
    if (
        values.recipeRevision.trim() !== "" &&
        !wholeNumberIn(values.recipeRevision, 1, Number.MAX_SAFE_INTEGER)
    ) {
        errors.recipeRevision = "Enter a whole number, 1 or greater, or leave it empty.";
    }
    if (values.description.trim().length > MAX_DESCRIPTION) {
        errors.description = `${MAX_DESCRIPTION} characters at most.`;
    }
    const components = values.components;
    if (components.length === 0) {
        errors.components = "Add at least one component.";
    } else if (components.length > MAX_COMPONENTS) {
        errors.components = `At most ${MAX_COMPONENTS} components.`;
    } else if (
        components.some((entry) => entry.mode === "arn" && !entry.arn.trim().startsWith("arn:"))
    ) {
        errors.components = "A managed component is named by its Image Builder component ARN.";
    } else if (
        components.some(
            (entry) =>
                entry.mode === "inline" && (entry.name.trim() === "" || entry.data.trim() === "")
        )
    ) {
        errors.components = "An inline component needs a name and a document.";
    } else if (
        components.some((entry) => entry.mode === "inline" && !entry.data.includes("schemaVersion"))
    ) {
        errors.components = "An inline component document must declare schemaVersion.";
    }
    return errors;
}

/** The stored components as list entries; a new recipe starts with one empty managed component. */
const toEntries = (components: EdhAmiRecipeComponent[] | undefined) =>
    (components ?? [{ arn: "" }]).map((component, index) => ({
        id: index,
        mode: "arn" in component ? ("arn" as const) : ("inline" as const),
        arn: "arn" in component ? component.arn : "",
        name: "arn" in component ? "" : component.name,
        data: "arn" in component ? "" : component.data,
    }));

/**
 * Create, edit or read an AMI recipe. The fields mirror `AmiRecipeRequestModel`, so a body this
 * dialog builds is one the backend accepts; validation messages appear under their fields after the
 * first submit attempt. A recipe that came from the deployment configuration opens read-only —
 * it is changed in the configuration and redeployed, not here. A duplicate opens as a new recipe
 * prefilled from its source and takes the source's bundled files, which are shown but never sent.
 */
const AmiRecipeFormDialog: React.FC<AmiRecipeFormDialogProps> = ({
    open,
    busy,
    initial,
    duplicateSource,
    onOpenChange,
    onSubmit,
}) => {
    const readOnly = initial?.source === "config";
    const source = initial ? undefined : duplicateSource;
    // The recipe the fields start from: the one opened, or the one being duplicated.
    const seed = initial ?? source;
    const formId = useId();
    const fieldId = useId();
    const id = (name: string) => `${fieldId}-${name}`;

    const [recipeId, setRecipeId] = useState(initial?.recipeId ?? "");
    const [displayName, setDisplayName] = useState(
        source ? `${source.displayName} (copy)` : initial?.displayName ?? ""
    );
    const [kind, setKind] = useState<string>(seed?.kind ?? "vdi");
    const [osFamily, setOsFamily] = useState<string>(seed?.osFamily ?? "linux");
    const [baseOs, setBaseOs] = useState(seed?.baseOs ?? "");
    const [architecture, setArchitecture] = useState<string>(seed?.architecture ?? "x86_64");
    const [parentImage, setParentImage] = useState(seed?.parentImage ?? "");
    const [buildInstanceType, setBuildInstanceType] = useState(() => {
        const stored = seed?.buildInstanceType ?? "";
        return Array.isArray(stored) ? stored.join(", ") : stored;
    });
    const [launchInstanceTypes, setLaunchInstanceTypes] = useState(() =>
        (seed?.launchInstanceTypes ?? []).join(", ")
    );
    const [rootSizeGb, setRootSizeGb] = useState(String(seed?.rootSizeGb ?? 100));
    const [stackName, setStackName] = useState(
        source ? `${source.stackName} Copy` : initial?.stackName ?? ""
    );
    const [profileId, setProfileId] = useState(seed ? String(seed.profileId) : "");
    const [keepVersions, setKeepVersions] = useState(
        seed?.keepVersions != null ? String(seed.keepVersions) : ""
    );
    const [recipeRevision, setRecipeRevision] = useState(String(seed?.recipeRevision ?? 1));
    const [description, setDescription] = useState(seed?.description ?? "");
    const [components, setComponents] = useState(() => toEntries(seed?.components));
    const [submitted, setSubmitted] = useState(false);
    // The React key of the next entry added; an index would move under a removal.
    const nextEntryId = useRef(components.length);

    const values: FormValues = {
        recipeId,
        displayName,
        kind,
        osFamily,
        baseOs,
        architecture,
        parentImage,
        buildInstanceType,
        launchInstanceTypes,
        rootSizeGb,
        stackName,
        profileId,
        keepVersions,
        recipeRevision,
        description,
        components,
    };
    const errors = validateAmiRecipe(values);
    const valid = Object.keys(errors).length === 0;
    const shown: FieldErrors = submitted ? errors : {};
    const shownCount = Object.keys(shown).length;
    // A stored Windows recipe's own architecture stays listed, so arm64 is flagged, not rewritten.
    const architectures = architectureOptions(
        osFamily,
        seed?.osFamily === "windows" ? seed.architecture : ""
    );
    const bundledIds = (seed?.assets ?? []).map((asset) => asset.id).join(", ");
    const bundledFrom = source?.recipeId ?? initial?.duplicatedFrom;

    const patchComponent = (index: number, patch: Partial<AmiRecipeComponentEntry>) =>
        setComponents((entries) =>
            entries.map((entry, at) => (at === index ? { ...entry, ...patch } : entry))
        );

    const addComponent = () =>
        setComponents((entries) => [
            ...entries,
            { id: nextEntryId.current++, mode: "arn" as const, arn: "", name: "", data: "" },
        ]);

    const removeComponent = (index: number) =>
        setComponents((entries) => entries.filter((_, at) => at !== index));

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitted(true);
        if (!valid || readOnly) return;
        const instanceTypes = splitInstanceTypes(buildInstanceType);
        const launchTypes = splitLaunchInstanceTypes(launchInstanceTypes);
        const body: EdhAmiRecipeRequest = {
            recipeId: recipeId.trim(),
            displayName: displayName.trim(),
            kind: kind as EdhAmiRecipeKind,
            osFamily: osFamily as EdhOsFamily,
            ...(kind === "vdi" ? { baseOs: baseOs.trim() } : {}),
            architecture: architecture as EdhArchitecture,
            parentImage: parentImage.trim(),
            buildInstanceType: instanceTypes.length === 1 ? instanceTypes[0] : instanceTypes,
            // Always a list; an empty field sends none, which the service stores as no restriction.
            ...(launchTypes.length > 0 ? { launchInstanceTypes: launchTypes } : {}),
            rootSizeGb: Number(rootSizeGb),
            stackName: stackName.trim(),
            profileId: Number(profileId),
            components: components.map((entry) =>
                entry.mode === "arn"
                    ? { arn: entry.arn.trim() }
                    : { name: entry.name.trim(), data: entry.data }
            ),
            ...(keepVersions.trim() ? { keepVersions: Number(keepVersions) } : {}),
            ...(recipeRevision.trim() ? { recipeRevision: Number(recipeRevision) } : {}),
            ...(description.trim() ? { description: description.trim() } : {}),
            // No field edits the parameters, so the body carries the ones the components may name.
            ...(seed?.parameters && Object.keys(seed.parameters).length > 0
                ? { parameters: seed.parameters }
                : {}),
            // The service rebuilds the stored item from this body, so a recipe disabled elsewhere
            // stays disabled only while the body carries the state it was found in.
            ...(initial ? { enabled: initial.enabled } : {}),
            ...(source ? { duplicateOf: source.recipeId } : {}),
        };
        await onSubmit(body);
    };

    const title = readOnly
        ? "View recipe"
        : initial
        ? "Edit recipe"
        : source
        ? "Duplicate recipe"
        : "New recipe";

    return (
        <Dialog
            open={open}
            onOpenChange={onOpenChange}
            title={title}
            size="lg"
            footer={
                readOnly ? (
                    <button
                        type="button"
                        className={btnSecondary}
                        onClick={() => onOpenChange(false)}
                    >
                        Close
                    </button>
                ) : (
                    <>
                        <button
                            type="button"
                            className={btnSecondary}
                            onClick={() => onOpenChange(false)}
                        >
                            Cancel
                        </button>
                        <button type="submit" form={formId} className={btnPrimary} disabled={busy}>
                            {initial ? "Save recipe" : "Create recipe"}
                        </button>
                    </>
                )
            }
        >
            <form id={formId} onSubmit={submit} noValidate className="space-y-3">
                {shownCount > 0 && (
                    <div role="alert" className="text-sm text-vams-error">
                        Correct the {shownCount === 1 ? "highlighted field" : "highlighted fields"}{" "}
                        below.
                    </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <Field
                        id={id("recipeId")}
                        label="Recipe id"
                        hint={initial ? undefined : "Lowercase letters, digits and hyphens."}
                        error={shown.recipeId}
                    >
                        {(describedBy) => (
                            <input
                                id={id("recipeId")}
                                className={`${control} w-full`}
                                value={recipeId}
                                onChange={(e) => setRecipeId(e.target.value)}
                                placeholder="al2023-desktop"
                                disabled={!!initial}
                                aria-invalid={!!shown.recipeId}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field id={id("displayName")} label="Display name" error={shown.displayName}>
                        {(describedBy) => (
                            <input
                                id={id("displayName")}
                                className={`${control} w-full`}
                                value={displayName}
                                onChange={(e) => setDisplayName(e.target.value)}
                                disabled={readOnly}
                                aria-invalid={!!shown.displayName}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field id={id("kind")} label="Kind" error={shown.kind}>
                        {(describedBy) => (
                            <select
                                id={id("kind")}
                                className={`${control} w-full`}
                                value={kind}
                                onChange={(e) => setKind(e.target.value)}
                                disabled={readOnly}
                                aria-describedby={describedBy}
                            >
                                {KIND_OPTIONS.map((option) => (
                                    <option key={option.value} value={option.value}>
                                        {option.label}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                    <Field id={id("osFamily")} label="OS family" error={shown.osFamily}>
                        {(describedBy) => (
                            <select
                                id={id("osFamily")}
                                className={`${control} w-full`}
                                value={osFamily}
                                onChange={(e) => {
                                    setOsFamily(e.target.value);
                                    // Amazon DCV for Windows is x86_64 only.
                                    if (e.target.value === "windows") setArchitecture("x86_64");
                                    // The families offer disjoint base OS keys, so the chosen one
                                    // is no longer among the options.
                                    setBaseOs("");
                                }}
                                disabled={readOnly}
                                aria-describedby={describedBy}
                            >
                                {(Object.keys(OS_FAMILY_LABEL) as EdhOsFamily[]).map((family) => (
                                    <option key={family} value={family}>
                                        {OS_FAMILY_LABEL[family]}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                    {kind === "vdi" && (
                        <Field id={id("baseOs")} label="Base OS" error={shown.baseOs}>
                            {(describedBy) => (
                                <select
                                    id={id("baseOs")}
                                    className={`${control} w-full`}
                                    value={baseOs}
                                    onChange={(e) => setBaseOs(e.target.value)}
                                    disabled={readOnly}
                                    aria-invalid={!!shown.baseOs}
                                    aria-describedby={describedBy}
                                >
                                    <option value="">Choose a base OS</option>
                                    {(BASE_OS_BY_FAMILY[osFamily as EdhOsFamily] ?? []).map(
                                        (option) => (
                                            <option key={option.value} value={option.value}>
                                                {option.label}
                                            </option>
                                        )
                                    )}
                                </select>
                            )}
                        </Field>
                    )}
                    <Field id={id("architecture")} label="Architecture" error={shown.architecture}>
                        {(describedBy) => (
                            <select
                                id={id("architecture")}
                                className={`${control} w-full`}
                                value={architecture}
                                onChange={(e) => setArchitecture(e.target.value)}
                                disabled={readOnly}
                                aria-invalid={!!shown.architecture}
                                aria-describedby={describedBy}
                            >
                                {architectures.map((option) => (
                                    <option key={option.value} value={option.value}>
                                        {option.label}
                                    </option>
                                ))}
                            </select>
                        )}
                    </Field>
                    <Field
                        id={id("parentImage")}
                        label="Parent image"
                        hint="ami-…, ssm:/aws/service/…, or edhStack:<stack name>"
                        error={shown.parentImage}
                    >
                        {(describedBy) => (
                            <input
                                id={id("parentImage")}
                                className={`${control} w-full`}
                                value={parentImage}
                                onChange={(e) => setParentImage(e.target.value)}
                                disabled={readOnly}
                                aria-invalid={!!shown.parentImage}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("buildInstanceType")}
                        label="Build instance type"
                        hint="Each type must run the recipe's architecture. Several, separated by commas, let Image Builder choose among them by availability."
                        error={shown.buildInstanceType}
                    >
                        {(describedBy) => (
                            <input
                                id={id("buildInstanceType")}
                                className={`${control} w-full`}
                                value={buildInstanceType}
                                onChange={(e) => setBuildInstanceType(e.target.value)}
                                placeholder="m7i.large"
                                disabled={readOnly}
                                aria-invalid={!!shown.buildInstanceType}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("launchInstanceTypes")}
                        label="Launch instance types"
                        hint="The instance types a desktop of this recipe's image may launch on, separated by commas. Leave it empty to offer every type the deployment allows."
                        error={shown.launchInstanceTypes}
                    >
                        {(describedBy) => (
                            <input
                                id={id("launchInstanceTypes")}
                                className={`${control} w-full`}
                                value={launchInstanceTypes}
                                onChange={(e) => setLaunchInstanceTypes(e.target.value)}
                                placeholder="g5.2xlarge, g6e.xlarge"
                                disabled={readOnly}
                                aria-invalid={!!shown.launchInstanceTypes}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("rootSizeGb")}
                        label="Root disk size (GB)"
                        error={shown.rootSizeGb}
                    >
                        {(describedBy) => (
                            <input
                                id={id("rootSizeGb")}
                                type="number"
                                className={`${control} w-full`}
                                value={rootSizeGb}
                                onChange={(e) => setRootSizeGb(e.target.value)}
                                min={ROOT_SIZE_GB_MIN}
                                max={ROOT_SIZE_GB_MAX}
                                disabled={readOnly}
                                aria-invalid={!!shown.rootSizeGb}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("stackName")}
                        label="Stack name"
                        hint="The name the built AMI is registered in the EDH catalog under."
                        error={shown.stackName}
                    >
                        {(describedBy) => (
                            <input
                                id={id("stackName")}
                                className={`${control} w-full`}
                                value={stackName}
                                onChange={(e) => setStackName(e.target.value)}
                                disabled={readOnly}
                                aria-invalid={!!shown.stackName}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field id={id("profileId")} label="Profile id" error={shown.profileId}>
                        {(describedBy) => (
                            <input
                                id={id("profileId")}
                                type="number"
                                className={`${control} w-full`}
                                value={profileId}
                                onChange={(e) => setProfileId(e.target.value)}
                                min={0}
                                disabled={readOnly}
                                aria-invalid={!!shown.profileId}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("keepVersions")}
                        label="Keep versions"
                        hint="Older builds beyond this many are retired after a build succeeds."
                        error={shown.keepVersions}
                    >
                        {(describedBy) => (
                            <input
                                id={id("keepVersions")}
                                type="number"
                                className={`${control} w-full`}
                                value={keepVersions}
                                onChange={(e) => setKeepVersions(e.target.value)}
                                min={KEEP_VERSIONS_MIN}
                                max={KEEP_VERSIONS_MAX}
                                disabled={readOnly}
                                aria-invalid={!!shown.keepVersions}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                    <Field
                        id={id("recipeRevision")}
                        label="Recipe revision"
                        hint="Raise it to rebuild from an unchanged recipe."
                        error={shown.recipeRevision}
                    >
                        {(describedBy) => (
                            <input
                                id={id("recipeRevision")}
                                type="number"
                                className={`${control} w-full`}
                                value={recipeRevision}
                                onChange={(e) => setRecipeRevision(e.target.value)}
                                min={1}
                                disabled={readOnly}
                                aria-invalid={!!shown.recipeRevision}
                                aria-describedby={describedBy}
                            />
                        )}
                    </Field>
                </div>
                <Field id={id("description")} label="Description" error={shown.description}>
                    {(describedBy) => (
                        <input
                            id={id("description")}
                            className={`${control} w-full`}
                            value={description}
                            onChange={(e) => setDescription(e.target.value)}
                            disabled={readOnly}
                            aria-invalid={!!shown.description}
                            aria-describedby={describedBy}
                        />
                    )}
                </Field>

                {bundledIds && (
                    <p className="text-sm text-text-secondary">
                        {bundledFrom
                            ? `Bundled files from ${bundledFrom}: ${bundledIds}`
                            : `Bundled files: ${bundledIds}`}
                    </p>
                )}

                <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                        <span className={labelClass}>Components</span>
                        {!readOnly && (
                            <button type="button" className={btnSecondary} onClick={addComponent}>
                                Add component
                            </button>
                        )}
                    </div>
                    {shown.components && (
                        <p className="text-vams-error text-sm">{shown.components}</p>
                    )}
                    {components.map((entry, index) => (
                        <div
                            key={entry.id}
                            className="border border-border-default rounded p-3 space-y-2"
                        >
                            <div className="flex items-end justify-between gap-2">
                                <Field
                                    id={id(`component-${entry.id}-mode`)}
                                    label={`Component ${index + 1} type`}
                                >
                                    {() => (
                                        <select
                                            id={id(`component-${entry.id}-mode`)}
                                            className={control}
                                            value={entry.mode}
                                            onChange={(e) =>
                                                patchComponent(index, {
                                                    mode: e.target.value as "arn" | "inline",
                                                })
                                            }
                                            disabled={readOnly}
                                        >
                                            <option value="arn">Managed component ARN</option>
                                            <option value="inline">Inline document</option>
                                        </select>
                                    )}
                                </Field>
                                {!readOnly && (
                                    <button
                                        type="button"
                                        className={btnSecondary}
                                        aria-label={`Remove component ${index + 1}`}
                                        onClick={() => removeComponent(index)}
                                    >
                                        Remove
                                    </button>
                                )}
                            </div>
                            {entry.mode === "arn" ? (
                                <Field
                                    id={id(`component-${entry.id}-arn`)}
                                    label={`Component ${index + 1} ARN`}
                                >
                                    {() => (
                                        <input
                                            id={id(`component-${entry.id}-arn`)}
                                            className={`${control} w-full font-mono text-sm`}
                                            value={entry.arn}
                                            onChange={(e) =>
                                                patchComponent(index, { arn: e.target.value })
                                            }
                                            placeholder="arn:aws:imagebuilder:us-east-1:aws:component/update-linux/x.x.x"
                                            disabled={readOnly}
                                        />
                                    )}
                                </Field>
                            ) : (
                                <>
                                    <Field
                                        id={id(`component-${entry.id}-name`)}
                                        label={`Component ${index + 1} name`}
                                    >
                                        {() => (
                                            <input
                                                id={id(`component-${entry.id}-name`)}
                                                className={`${control} w-full`}
                                                value={entry.name}
                                                onChange={(e) =>
                                                    patchComponent(index, { name: e.target.value })
                                                }
                                                disabled={readOnly}
                                            />
                                        )}
                                    </Field>
                                    <div>
                                        <span className={`${labelClass} mb-1`}>
                                            Component {index + 1} document
                                        </span>
                                        <ConfigEditor
                                            value={entry.data}
                                            language="yaml"
                                            readOnly={readOnly}
                                            height="220px"
                                            onChange={(value) =>
                                                patchComponent(index, { data: value ?? "" })
                                            }
                                        />
                                    </div>
                                </>
                            )}
                        </div>
                    ))}
                </div>
            </form>
        </Dialog>
    );
};

export default AmiRecipeFormDialog;
