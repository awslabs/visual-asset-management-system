/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LaunchWorkstationWizard, {
    DEFAULT_ROOT_SIZE_GB,
    ROOT_SIZE_GB_MAX,
    ROOT_SIZE_GB_MIN,
    defaultWorkstationName,
    instanceTypeRunsImage,
    rootSizeFloor,
    validateLaunchForm,
    validateLaunchStep,
} from "./LaunchWorkstationWizard";
import * as queries from "../api/queries";
import { LAUNCH_OUTCOME_UNKNOWN_MESSAGE } from "../api/workstations";
import type { CatalogLeasePolicy, EdhCatalogImage, EdhInstanceType } from "../types";

jest.mock("../api/queries", () => ({
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
}));

const BOOTSTRAP_ID = "11111111-1111-4111-8111-111111111111";

const LEASE_ON: CatalogLeasePolicy = {
    enabled: true,
    leaseHours: 8,
    forced: false,
    graceHours: 24,
};

const LEASE_BOUND_MESSAGE = "Expires in must be a whole number of hours from 1 to 8";

const ARCHITECTURE_MISMATCH_MESSAGE =
    "Choose an instance type that runs this image's architecture.";

const LARGER_TYPES_HINT =
    "Larger types carry more vCPUs and GPU memory and cost more while the workstation runs.";

const IMAGES = [
    {
        imageId: "42",
        name: "Ubuntu GPU desktop",
        baseOs: "ubuntu2204",
        osFamily: "linux",
        rootSize: 120,
        description: "CUDA 12, Blender, Isaac Sim",
        architecture: "x86_64",
    },
    {
        imageId: "43",
        name: "Windows CAD desktop",
        baseOs: "windows2022",
        osFamily: "windows",
        rootSize: null,
        description: null,
        architecture: null,
    },
];

const loaded = (data: any[]) => ({ data, isLoading: false, error: null });

const lease = (policy?: CatalogLeasePolicy) => ({ data: policy, isLoading: false, error: null });

/** Image, then the name and size step, which is where the lease field lives. */
async function walkToSizeStep(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
    await user.click(screen.getByRole("button", { name: "Next" }));
}

const reviewSummary = () => screen.getByRole("dialog").querySelector("dl") as HTMLElement;

function mountWizard(
    mutateAsync: jest.Mock,
    onClose = jest.fn(),
    generateBootstrapId: () => string = () => BOOTSTRAP_ID
) {
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(IMAGES));
    (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
        loaded([
            { instanceType: "g5.xlarge", architectures: ["x86_64"] },
            { instanceType: "g5.2xlarge", architectures: ["x86_64"] },
        ])
    );
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
        createWorkstation: { mutateAsync, isPending: false },
    });
    render(
        <LaunchWorkstationWizard
            open={true}
            onClose={onClose}
            generateBootstrapId={generateBootstrapId}
        />
    );
    return { onClose };
}

/** Image, then the name and size, then Review: the name is typed over the image's default. */
async function walkToReview(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.clear(screen.getByLabelText("Workstation name"));
    await user.type(screen.getByLabelText("Workstation name"), "Dev box");
    await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
    await user.click(screen.getByRole("button", { name: "Next" }));
}

describe("validateLaunchStep", () => {
    const form = { imageId: "42", instanceType: "g5.xlarge", rootSizeGb: 100, name: "Dev box" };

    it("requires an image on the Image step", () => {
        expect(validateLaunchStep("image", { ...form, imageId: "" })).toEqual(["Choose an image."]);
        expect(validateLaunchStep("image", form)).toEqual([]);
    });

    it("requires an instance type and a whole root size within bounds on the Name and size step", () => {
        expect(validateLaunchStep("size", { ...form, instanceType: "" })).toEqual([
            "Choose an instance type.",
        ]);
        expect(
            validateLaunchStep("size", { ...form, rootSizeGb: ROOT_SIZE_GB_MIN - 1 })
        ).toHaveLength(1);
        expect(
            validateLaunchStep("size", { ...form, rootSizeGb: ROOT_SIZE_GB_MAX + 1 })
        ).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, rootSizeGb: 99.5 })).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, rootSizeGb: ROOT_SIZE_GB_MIN })).toEqual([]);
        expect(validateLaunchStep("size", { ...form, rootSizeGb: ROOT_SIZE_GB_MAX })).toEqual([]);
    });

    it("applies the object-name pattern to the trimmed name on the Name and size step", () => {
        expect(validateLaunchStep("size", { ...form, name: "" })).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, name: "bad/name" })).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, name: "x".repeat(257) })).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, name: "  Dev box_1.2-a  " })).toEqual([]);
        // The name is not collected on the Image step.
        expect(validateLaunchStep("image", { ...form, name: "" })).toEqual([]);
    });

    it("blocks the launch on Review for any failing field, one message each", () => {
        expect(validateLaunchStep("review", form)).toEqual([]);
        expect(validateLaunchStep("review", { ...form, name: "" })).toHaveLength(1);
        expect(
            validateLaunchStep("review", { ...form, imageId: "", instanceType: "", name: "" })
        ).toHaveLength(3);
        expect(validateLaunchForm({ ...form, instanceType: "", rootSizeGb: 1 })).toEqual({
            instanceType: "Choose an instance type.",
            rootSizeGb: `Root disk size must be a whole number between ${ROOT_SIZE_GB_MIN} and ${ROOT_SIZE_GB_MAX} GB.`,
        });
    });

    it("bounds an offered expiry to the policy's window and leaves it alone otherwise", () => {
        expect(validateLaunchStep("size", { ...form, leaseHours: 4 }, LEASE_ON)).toEqual([]);
        expect(validateLaunchStep("size", { ...form, leaseHours: 8 }, LEASE_ON)).toEqual([]);
        expect(validateLaunchStep("size", { ...form, leaseHours: 9 }, LEASE_ON)).toEqual([
            LEASE_BOUND_MESSAGE,
        ]);
        expect(validateLaunchStep("size", { ...form, leaseHours: 0 }, LEASE_ON)).toHaveLength(1);
        expect(validateLaunchStep("size", { ...form, leaseHours: 1.5 }, LEASE_ON)).toHaveLength(1);
        // A blank field is the ordinary case: the launch asks for no expiry of its own.
        expect(validateLaunchStep("size", form, LEASE_ON)).toEqual([]);
        // There is nothing to bound where the field is not offered.
        expect(
            validateLaunchStep("size", { ...form, leaseHours: 9 }, { ...LEASE_ON, forced: true })
        ).toEqual([]);
        expect(
            validateLaunchStep("size", { ...form, leaseHours: 9 }, { ...LEASE_ON, enabled: false })
        ).toEqual([]);
        expect(validateLaunchStep("size", { ...form, leaseHours: 9 })).toEqual([]);
        // Review carries the same message, so no value reaches the launch unchecked.
        expect(validateLaunchStep("review", { ...form, leaseHours: 9 }, LEASE_ON)).toEqual([
            LEASE_BOUND_MESSAGE,
        ]);
        expect(validateLaunchForm({ ...form, leaseHours: 9 }, LEASE_ON)).toEqual({
            leaseHours: LEASE_BOUND_MESSAGE,
        });
    });

    it("takes the image's name as the default only when the name pattern accepts it", () => {
        expect(defaultWorkstationName(IMAGES[0] as any)).toBe("Ubuntu GPU desktop");
        expect(defaultWorkstationName({ ...IMAGES[0], name: "cad/2026 (beta)" } as any)).toBe("");
    });

    it("refuses a chosen instance type that does not run the image, once one is chosen", () => {
        expect(validateLaunchForm(form, undefined, undefined, false)).toEqual({
            instanceType: ARCHITECTURE_MISMATCH_MESSAGE,
        });
        expect(validateLaunchForm(form, undefined, undefined, true)).toEqual({});
        expect(validateLaunchForm(form)).toEqual({});
        // Asking for a type comes first.
        expect(
            validateLaunchForm({ ...form, instanceType: "" }, undefined, undefined, false)
        ).toEqual({ instanceType: "Choose an instance type." });
        // The step that collects the type, and Review, carry the same message.
        expect(validateLaunchStep("size", form, undefined, undefined, false)).toEqual([
            ARCHITECTURE_MISMATCH_MESSAGE,
        ]);
        expect(validateLaunchStep("review", form, undefined, undefined, false)).toEqual([
            ARCHITECTURE_MISMATCH_MESSAGE,
        ]);
        expect(validateLaunchStep("image", form, undefined, undefined, false)).toEqual([]);
    });
});

describe("instanceTypeRunsImage", () => {
    const BASE_IMAGE: EdhCatalogImage = {
        imageId: "80",
        name: "Desktop",
        baseOs: "amazonlinux2023",
        osFamily: "linux",
        rootSize: null,
        description: null,
        category: "baseline",
    };
    const image = (fields: Partial<EdhCatalogImage> = {}): EdhCatalogImage => ({
        ...BASE_IMAGE,
        ...fields,
    });
    const type = (architectures?: string[]): EdhInstanceType =>
        architectures
            ? { instanceType: "m7g.large", architectures }
            : { instanceType: "m7g.large" };

    it("is true for a type listed under the image's architecture", () => {
        expect(instanceTypeRunsImage(type(["arm64"]), image({ architecture: "arm64" }))).toBe(true);
        expect(
            instanceTypeRunsImage(type(["x86_64", "arm64"]), image({ architecture: "arm64" }))
        ).toBe(true);
    });

    it("is false for a type listed only under other architectures", () => {
        expect(instanceTypeRunsImage(type(["arm64"]), image({ architecture: "x86_64" }))).toBe(
            false
        );
        // EDH's own rule is an exact match: its i386 group does not run an x86_64 image.
        expect(instanceTypeRunsImage(type(["i386"]), image({ architecture: "x86_64" }))).toBe(
            false
        );
    });

    it.each<[string, EdhInstanceType, EdhCatalogImage | null | undefined]>([
        ["a null image architecture", type(["arm64"]), image({ architecture: null })],
        ["an absent image architecture", type(["arm64"]), image()],
        ["empty architectures", type([]), image({ architecture: "x86_64" })],
        ["absent architectures", type(), image({ architecture: "x86_64" })],
        ["no image", type(["arm64"]), undefined],
        ["a null image", type(["arm64"]), null],
    ])("is true for %s", (_case, instanceType, chosen) => {
        expect(instanceTypeRunsImage(instanceType, chosen)).toBe(true);
    });
});

describe("LaunchWorkstationWizard", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // Re-seeded here because clearAllMocks keeps a previous test's return value.
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease());
    });

    it("keeps Next disabled until the current step is valid", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());

        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
        // The Launch control lives on the Review step only.
        expect(screen.queryByRole("button", { name: "Launch" })).not.toBeInTheDocument();
    });

    it("names the steps Image, Name and size, Review, and collects the name before Review", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());
        expect(screen.getByText("Name and size")).toBeInTheDocument();
        expect(screen.queryByLabelText("Workstation name")).not.toBeInTheDocument();
        // The image's OS chip carries the word the list shows, not the stored token.
        expect(screen.getByText("Windows")).toBeInTheDocument();
        expect(screen.queryByText("windows")).not.toBeInTheDocument();

        await walkToReview(user);

        // Review is a summary: no field, the name among the rows, and the launch control.
        expect(screen.queryByLabelText("Workstation name")).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Launch" })).toBeEnabled();
        const summary = screen.getByRole("dialog").querySelector("dl") as HTMLElement;
        expect(within(summary).getByText("Name").nextSibling).toHaveTextContent("Dev box");
        expect(within(summary).getByText("Instance type").nextSibling).toHaveTextContent(
            "g5.xlarge"
        );
        expect(within(summary).getByText("OS").nextSibling).toHaveTextContent("Linux");
    });

    it("shows no message on entering a step, and names a field's message in the error tone once it is left", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());
        await user.click(screen.getByRole("radio", { name: /Windows CAD desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));

        // Nothing has been left yet: the disabled Next is the only signal.
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        expect(document.querySelector(".text-vams-error")).toBeNull();
        expect(screen.getByLabelText("Instance type")).not.toHaveAttribute("aria-invalid");

        const name = screen.getByLabelText("Workstation name");
        await user.clear(name);
        await user.tab();
        const message = screen.getByText(/Name must be 1-256 letters/);
        expect(message).toHaveClass("text-vams-error");
        expect(name).toHaveAttribute("aria-invalid", "true");
        expect(name).toHaveAttribute("aria-describedby", message.id);
        // The untouched select still carries no message.
        expect(screen.queryByText("Choose an instance type.")).not.toBeInTheDocument();

        await user.type(name, "Dev box");
        expect(screen.queryByText(/Name must be 1-256 letters/)).not.toBeInTheDocument();
        expect(name).not.toHaveAttribute("aria-invalid");

        const instanceType = screen.getByLabelText("Instance type");
        instanceType.focus();
        await user.tab();
        expect(screen.getByText("Choose an instance type.")).toHaveClass("text-vams-error");
        expect(instanceType).toHaveAttribute("aria-invalid", "true");
        expect(instanceType).toHaveAccessibleDescription(
            /allows for the desktop profile.*Choose an instance type\./
        );
    });

    it("prefills the name from the chosen image and keeps a name the user typed", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());

        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Workstation name")).toHaveValue("Ubuntu GPU desktop");

        // Still the image's default, so a new image renames it.
        await user.click(screen.getByRole("button", { name: "Back" }));
        await user.click(screen.getByRole("radio", { name: /Windows CAD desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Workstation name")).toHaveValue("Windows CAD desktop");

        await user.clear(screen.getByLabelText("Workstation name"));
        await user.type(screen.getByLabelText("Workstation name"), "Dev box");
        await user.click(screen.getByRole("button", { name: "Back" }));
        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Workstation name")).toHaveValue("Dev box");
    });

    it("seeds the root size from the chosen image and falls back to the default", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());

        await user.click(screen.getByRole("radio", { name: /Windows CAD desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Root disk size (GB)")).toHaveValue(DEFAULT_ROOT_SIZE_GB);

        await user.click(screen.getByRole("button", { name: "Back" }));
        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(screen.getByLabelText("Root disk size (GB)")).toHaveValue(120);
    });

    it("posts the launch body with the injected bootstrapId and the image's osFamily, then closes", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        const { onClose } = mountWizard(mutateAsync);

        await walkToReview(user);
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() =>
            expect(mutateAsync).toHaveBeenCalledWith({
                bootstrapId: BOOTSTRAP_ID,
                imageId: "42",
                instanceType: "g5.xlarge",
                name: "Dev box",
                rootSizeGb: 120,
                osFamily: "linux",
            })
        );
        expect(mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    });

    it("sends one launch for a double-click on Launch", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn(() => new Promise<never>(() => undefined));
        mountWizard(mutateAsync);

        await walkToReview(user);
        // Both clicks land before a re-render, as a double-click's do.
        const launch = screen.getByRole("button", { name: "Launch" });
        fireEvent.click(launch);
        fireEvent.click(launch);
        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        expect(mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("closes on an unknown launch outcome instead of reporting a failure", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockRejectedValue(new Error(LAUNCH_OUTCOME_UNKNOWN_MESSAGE));
        const { onClose } = mountWizard(mutateAsync);

        await walkToReview(user);
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("stays open and shows the message when the launch is rejected", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockRejectedValue(new Error("instanceType is not permitted in this deployment"));
        const { onClose } = mountWizard(mutateAsync);

        await walkToReview(user);
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent(
                "instanceType is not permitted in this deployment"
            )
        );
        expect(onClose).not.toHaveBeenCalled();
        // Launch stays available for a retry; key stability across it is covered below.
        expect(screen.getByRole("button", { name: "Launch" })).toBeEnabled();
    });

    it("re-sends the same bootstrapId when Launch is retried after a rejection", async () => {
        const user = userEvent.setup();
        let minted = 0;
        // Distinct value per call, so a per-render regeneration would show up as a different id.
        const generateBootstrapId = jest.fn(
            () => `00000000-0000-4000-8000-${String(++minted).padStart(12, "0")}`
        );
        const mutateAsync = jest
            .fn()
            .mockRejectedValueOnce(new Error("EDH is busy"))
            .mockResolvedValueOnce({ bootstrapId: "unused", state: "CREATING" });
        const { onClose } = mountWizard(mutateAsync, jest.fn(), generateBootstrapId);

        await walkToReview(user);
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("EDH is busy"));
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
        const [first, second] = mutateAsync.mock.calls.map((call) => call[0].bootstrapId);
        expect(second).toBe(first);
        expect(generateBootstrapId).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    });

    it("offers an optional expiry with the policy's cap when the policy allows one to be chosen", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        mountWizard(jest.fn());
        expect(queries.useEdhCatalogLease).toHaveBeenCalledWith(true);

        await walkToSizeStep(user);

        const field = screen.getByLabelText("Expires in (hours)");
        expect(field).toHaveValue(null);
        expect(field).toHaveAccessibleDescription(
            "Optional. Up to 8 hours; leave blank for no expiry."
        );
    });

    it("names the bound once the expiry field is left, and blocks Next until it holds", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        mountWizard(jest.fn());
        await walkToSizeStep(user);
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");

        const field = screen.getByLabelText("Expires in (hours)");
        await user.type(field, "9");
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        // Nothing has been left yet, so the disabled Next is the only signal.
        expect(screen.queryByText(LEASE_BOUND_MESSAGE)).not.toBeInTheDocument();

        await user.tab();
        expect(screen.getByText(LEASE_BOUND_MESSAGE)).toHaveClass("text-vams-error");
        expect(field).toHaveAttribute("aria-invalid", "true");
        expect(field).toHaveAccessibleDescription(
            `Optional. Up to 8 hours; leave blank for no expiry. ${LEASE_BOUND_MESSAGE}`
        );

        await user.clear(field);
        await user.type(field, "8");
        expect(screen.queryByText(LEASE_BOUND_MESSAGE)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
    });

    it("states the administrator's window instead of a field when the policy forces one", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(
            lease({ ...LEASE_ON, forced: true })
        );
        mountWizard(jest.fn());
        await walkToSizeStep(user);

        expect(screen.queryByLabelText("Expires in (hours)")).not.toBeInTheDocument();
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(within(reviewSummary()).getByText("Expires in").nextSibling).toHaveTextContent(
            "8 hours (set by administrator)"
        );
    });

    it("says nothing about an expiry when leases are off", async () => {
        const user = userEvent.setup();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(
            lease({ ...LEASE_ON, enabled: false })
        );
        mountWizard(jest.fn());
        await walkToSizeStep(user);

        expect(screen.queryByLabelText("Expires in (hours)")).not.toBeInTheDocument();
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(within(reviewSummary()).queryByText("Expires in")).not.toBeInTheDocument();
    });

    it("carries a chosen expiry into the summary and the launch body", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        mountWizard(mutateAsync);

        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.clear(screen.getByLabelText("Workstation name"));
        await user.type(screen.getByLabelText("Workstation name"), "Dev box");
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        await user.type(screen.getByLabelText("Expires in (hours)"), "4");
        await user.click(screen.getByRole("button", { name: "Next" }));

        expect(within(reviewSummary()).getByText("Expires in").nextSibling).toHaveTextContent(
            "4 hours"
        );
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() =>
            expect(mutateAsync).toHaveBeenCalledWith({
                bootstrapId: BOOTSTRAP_ID,
                imageId: "42",
                instanceType: "g5.xlarge",
                name: "Dev box",
                rootSizeGb: 120,
                osFamily: "linux",
                leaseHours: 4,
            })
        );
    });

    it("leaves the expiry out of the launch body when the field is blank", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        mountWizard(mutateAsync);

        await walkToReview(user);
        expect(within(reviewSummary()).getByText("Expires in").nextSibling).toHaveTextContent(
            "No expiry"
        );
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(Object.keys(mutateAsync.mock.calls[0][0])).not.toContain("leaseHours");
    });

    it("leaves a value typed before the policy changed out of the launch body", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease(LEASE_ON));
        mountWizard(mutateAsync);

        await user.click(screen.getByRole("radio", { name: /Ubuntu GPU desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.clear(screen.getByLabelText("Workstation name"));
        await user.type(screen.getByLabelText("Workstation name"), "Dev box");
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        await user.type(screen.getByLabelText("Expires in (hours)"), "4");

        // Leases go off while the wizard is open: the next render no longer offers an expiry.
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(
            lease({ ...LEASE_ON, enabled: false })
        );
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(within(reviewSummary()).queryByText("Expires in")).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(Object.keys(mutateAsync.mock.calls[0][0])).not.toContain("leaseHours");
    });

    it("tells the user when the catalog offers nothing to launch", () => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded([]));
        (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(loaded([]));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
            createWorkstation: { mutateAsync: jest.fn(), isPending: false },
        });
        render(<LaunchWorkstationWizard open={true} onClose={jest.fn()} />);
        expect(screen.getByText("No images are available to launch.")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    });
});

describe("rootSizeFloor", () => {
    const form = { imageId: "42", instanceType: "g5.xlarge", rootSizeGb: 100, name: "Dev box" };
    const withRootSize = (rootSize: unknown) =>
        ({ ...IMAGES[0], rootSize } as unknown as Parameters<typeof rootSizeFloor>[0]);
    const NOT_A_FLOOR = [null, NaN, "abc", "", 0, 5, 120.5, "12.5", true];

    it("is the image's registered root size when it is a whole number above the minimum", () => {
        expect(rootSizeFloor(withRootSize(120))).toBe(120);
        expect(rootSizeFloor(withRootSize(" 120 "))).toBe(120);
        expect(rootSizeFloor(withRootSize(5000))).toBe(ROOT_SIZE_GB_MAX);
    });

    it.each(NOT_A_FLOOR)("is the minimum for a registered root size of %p", (rootSize) => {
        expect(rootSizeFloor(withRootSize(rootSize))).toBe(ROOT_SIZE_GB_MIN);
    });

    it("is the minimum without an image", () => {
        expect(rootSizeFloor(undefined)).toBe(ROOT_SIZE_GB_MIN);
    });

    it("names the image's floor in the root size message", () => {
        expect(validateLaunchForm(form, undefined, 120).rootSizeGb).toBe(
            "Root disk size must be a whole number between 120 and 2048 GB for this image."
        );
        expect(validateLaunchForm({ ...form, rootSizeGb: 120 }, undefined, 120)).toEqual({});
    });
});

describe("LaunchWorkstationWizard root disk floor", () => {
    it("holds the root size to the chosen image's floor and follows a change of image", async () => {
        const user = userEvent.setup();
        mountWizard(jest.fn());
        const rootSize = () => screen.getByLabelText("Root disk size (GB)");

        await walkToSizeStep(user);
        expect(rootSize()).toHaveAttribute("min", "120");
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

        await user.clear(rootSize());
        await user.type(rootSize(), "100");
        await user.tab();
        expect(
            screen.getByText(
                "Root disk size must be a whole number between 120 and 2048 GB for this image."
            )
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();

        await user.click(screen.getByRole("button", { name: "Back" }));
        await user.click(screen.getByRole("radio", { name: /Windows CAD desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(rootSize()).toHaveAttribute("min", String(ROOT_SIZE_GB_MIN));
        expect(rootSize()).toHaveValue(DEFAULT_ROOT_SIZE_GB);
    });
});

describe("LaunchWorkstationWizard image groups and instance types", () => {
    // Out of order on purpose: the wizard groups and sorts what the catalog returns.
    const CATALOG = [
        {
            imageId: "43",
            name: "Windows CAD desktop",
            baseOs: "windows2022",
            osFamily: "windows",
            rootSize: null,
            description: null,
            category: "baseline",
        },
        {
            imageId: "36",
            name: "Isaac Sim desktop",
            baseOs: "ubuntu2404",
            osFamily: "linux",
            rootSize: 150,
            description: null,
            category: "amiRecipe",
            recipeId: "isaacsim",
            recipeDisplayName: "NVIDIA Isaac Sim desktop",
            recipeVersion: 10,
        },
        {
            imageId: "51",
            name: "ubuntu 22 GPU",
            baseOs: "ubuntu2204",
            osFamily: "linux",
            rootSize: 100,
            description: null,
            category: "baseline",
        },
        {
            imageId: "35",
            name: "AL2023 desktop",
            baseOs: "amazonlinux2023",
            osFamily: "linux",
            rootSize: 60,
            description: null,
            category: "amiRecipe",
            recipeId: "al2023-desktop",
            recipeDisplayName: "Amazon Linux 2023 desktop",
            recipeVersion: 5,
        },
        {
            imageId: "50",
            name: "Ubuntu 9",
            baseOs: "ubuntu2204",
            osFamily: "linux",
            rootSize: 100,
            description: null,
            category: "baseline",
        },
    ];

    beforeEach(() => {
        jest.clearAllMocks();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease());
    });

    const mountCatalog = (
        images: any[],
        instanceTypes: string[] = ["g5.xlarge"],
        mutateAsync: jest.Mock = jest.fn()
    ) => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(images));
        (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
            loaded(instanceTypes.map((instanceType) => ({ instanceType })))
        );
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
            createWorkstation: { mutateAsync, isPending: false },
        });
        return render(
            <LaunchWorkstationWizard
                open={true}
                onClose={jest.fn()}
                generateBootstrapId={() => BOOTSTRAP_ID}
            />
        );
    };

    const imageIdsIn = (group: HTMLElement) =>
        within(group)
            .getAllByRole("radio")
            .map((radio) => (radio as HTMLInputElement).value);

    it("lists recipe-built images under Custom AMI Recipe first, then the rest under Baseline Image, each sorted by name", () => {
        mountCatalog(CATALOG);
        const custom = screen.getByRole("group", { name: "Custom AMI Recipe" });
        const baseline = screen.getByRole("group", { name: "Baseline Image" });
        expect(
            custom.compareDocumentPosition(baseline) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        expect(imageIdsIn(custom)).toEqual(["35", "36"]);
        // Without regard to case, and with the digits compared as numbers: 9 before 22.
        expect(imageIdsIn(baseline)).toEqual(["50", "51", "43"]);
    });

    it("badges a recipe image with its recipe version and a baseline image with none", () => {
        mountCatalog(CATALOG);
        const custom = screen.getByRole("group", { name: "Custom AMI Recipe" });
        const baseline = screen.getByRole("group", { name: "Baseline Image" });
        expect(within(custom).getByText("v10").closest("label")).toBe(
            screen.getByRole("radio", { name: /Isaac Sim desktop/ }).closest("label")
        );
        expect(within(custom).getByText("v5").closest("label")).toBe(
            screen.getByRole("radio", { name: /AL2023 desktop/ }).closest("label")
        );
        expect(within(baseline).queryByText((text) => /^v[0-9]+$/.test(text))).toBeNull();
    });

    it("leaves out a group with no images, and lists an image without a category as baseline", () => {
        const { unmount } = mountCatalog(IMAGES);
        expect(screen.queryByRole("group", { name: "Custom AMI Recipe" })).not.toBeInTheDocument();
        expect(imageIdsIn(screen.getByRole("group", { name: "Baseline Image" }))).toEqual([
            "42",
            "43",
        ]);
        unmount();

        mountCatalog(CATALOG.filter((image) => image.category === "amiRecipe"));
        expect(screen.queryByRole("group", { name: "Baseline Image" })).not.toBeInTheDocument();
        expect(imageIdsIn(screen.getByRole("group", { name: "Custom AMI Recipe" }))).toEqual([
            "35",
            "36",
        ]);
    });

    it("orders names that differ only in spacing or punctuation as the catalog API does", () => {
        const baseline = CATALOG[2];
        mountCatalog([
            { ...baseline, imageId: "61", name: "Desktop 2" },
            { ...baseline, imageId: "62", name: "Desktop2" },
            { ...baseline, imageId: "63", name: "Ubuntu_22 GPU" },
            { ...baseline, imageId: "64", name: "Ubuntu-22 GPU" },
        ]);
        // The text between the digit runs compares by code point: "desktop" before "desktop ",
        // "-" before "_".
        expect(imageIdsIn(screen.getByRole("group", { name: "Baseline Image" }))).toEqual([
            "62",
            "61",
            "64",
            "63",
        ]);
    });

    it("offers the instance types sorted by name, with the digits compared as numbers", async () => {
        const user = userEvent.setup();
        mountCatalog(CATALOG, [
            "m7i.xlarge",
            "g5.12xlarge",
            "m7i.large",
            "g5.2xlarge",
            "c6i.large",
        ]);
        await user.click(screen.getByRole("radio", { name: /AL2023 desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        const options = Array.from(
            (screen.getByLabelText("Instance type") as HTMLSelectElement).options
        ).map((option) => option.value);
        expect(options).toEqual([
            "",
            "c6i.large",
            "g5.2xlarge",
            "g5.12xlarge",
            "m7i.large",
            "m7i.xlarge",
        ]);
    });

    it("launches a recipe image as it launches any other", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        mountCatalog(CATALOG, ["g5.xlarge"], mutateAsync);
        await user.click(screen.getByRole("radio", { name: /Isaac Sim desktop/ }));
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.selectOptions(screen.getByLabelText("Instance type"), "g5.xlarge");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() =>
            expect(mutateAsync).toHaveBeenCalledWith({
                bootstrapId: BOOTSTRAP_ID,
                imageId: "36",
                instanceType: "g5.xlarge",
                name: "Isaac Sim desktop",
                rootSizeGb: 150,
                osFamily: "linux",
            })
        );
    });
});

describe("LaunchWorkstationWizard by architecture", () => {
    const ARCH_IMAGES = [
        {
            imageId: "70",
            name: "AL2023 x86 desktop",
            baseOs: "amazonlinux2023",
            osFamily: "linux",
            rootSize: 60,
            description: "GNOME desktop",
            category: "baseline",
            architecture: "x86_64",
        },
        {
            imageId: "71",
            name: "AL2023 Graviton desktop",
            baseOs: "amazonlinux2023",
            osFamily: "linux",
            rootSize: 60,
            description: null,
            category: "baseline",
            architecture: "arm64",
        },
        {
            imageId: "72",
            name: "Ubuntu x86 desktop",
            baseOs: "ubuntu2204",
            osFamily: "linux",
            rootSize: 100,
            description: null,
            category: "baseline",
            architecture: "x86_64",
        },
        {
            imageId: "73",
            name: "Legacy desktop",
            baseOs: "ubuntu2204",
            osFamily: "linux",
            rootSize: 100,
            description: null,
            category: "baseline",
            architecture: null,
        },
    ];

    // Out of order on purpose: the wizard sorts what it offers.
    const ARCH_TYPES = [
        { instanceType: "m7i.xlarge", architectures: ["x86_64"] },
        { instanceType: "c7g.16xlarge", architectures: ["arm64"] },
        { instanceType: "g5.12xlarge", architectures: ["x86_64"] },
        { instanceType: "m7g.large", architectures: ["arm64"] },
        { instanceType: "g5.2xlarge", architectures: ["x86_64"] },
        { instanceType: "c7g.2xlarge", architectures: ["arm64"] },
    ];

    const X86_OFFERED = ["", "g5.2xlarge", "g5.12xlarge", "m7i.xlarge"];
    const ARM_OFFERED = ["", "c7g.2xlarge", "c7g.16xlarge", "m7g.large"];

    const ARM_EMPTY_MESSAGE =
        "No instance type in this deployment runs this image's architecture (arm64). Choose another image or ask an administrator.";
    const NO_TYPES_MESSAGE = "No instance types are available in this deployment.";

    beforeEach(() => {
        jest.clearAllMocks();
        (queries.useEdhCatalogLease as jest.Mock).mockReturnValue(lease());
    });

    const wizard = () => (
        <LaunchWorkstationWizard
            open={true}
            onClose={jest.fn()}
            generateBootstrapId={() => BOOTSTRAP_ID}
        />
    );

    const mountArch = (
        images: any[] = ARCH_IMAGES,
        instanceTypes: any[] = ARCH_TYPES,
        mutateAsync: jest.Mock = jest.fn()
    ) => {
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(images));
        (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(loaded(instanceTypes));
        (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
            createWorkstation: { mutateAsync, isPending: false },
        });
        return render(wizard());
    };

    /** Chooses the image whose name starts with `name`, then moves on to the size step. */
    const sizeStepFor = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
        await user.click(screen.getByRole("radio", { name: new RegExp(`^${name}`) }));
        await user.click(screen.getByRole("button", { name: "Next" }));
    };

    const back = (user: ReturnType<typeof userEvent.setup>) =>
        user.click(screen.getByRole("button", { name: "Back" }));

    const instanceTypeSelect = () => screen.getByLabelText("Instance type") as HTMLSelectElement;

    const offeredTypes = () => Array.from(instanceTypeSelect().options).map((o) => o.value);

    const imageCard = (name: string) =>
        screen.getByRole("radio", { name: new RegExp(`^${name}`) }).closest("label") as HTMLElement;

    it("offers only the instance types that run the chosen image, in natural order", async () => {
        const user = userEvent.setup();
        mountArch();

        await sizeStepFor(user, "AL2023 x86 desktop");
        expect(offeredTypes()).toEqual(X86_OFFERED);

        await back(user);
        await sizeStepFor(user, "AL2023 Graviton desktop");
        expect(offeredTypes()).toEqual(ARM_OFFERED);

        // An image of unknown architecture is offered every type.
        await back(user);
        await sizeStepFor(user, "Legacy desktop");
        expect(offeredTypes()).toEqual([
            "",
            "c7g.2xlarge",
            "c7g.16xlarge",
            "g5.2xlarge",
            "g5.12xlarge",
            "m7g.large",
            "m7i.xlarge",
        ]);
    });

    it("clears a chosen type the newly chosen image does not run", async () => {
        const user = userEvent.setup();
        mountArch();
        await sizeStepFor(user, "AL2023 x86 desktop");
        await user.selectOptions(instanceTypeSelect(), "g5.2xlarge");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

        await back(user);
        await sizeStepFor(user, "AL2023 Graviton desktop");
        expect(instanceTypeSelect()).toHaveValue("");
        expect(instanceTypeSelect()).toHaveDisplayValue("Choose an instance type");
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        // The type is gone from the form, not only from the list.
        instanceTypeSelect().focus();
        await user.tab();
        expect(screen.getByText("Choose an instance type.")).toHaveClass("text-vams-error");
        expect(screen.queryByText(ARCHITECTURE_MISMATCH_MESSAGE)).not.toBeInTheDocument();

        // Going back to an image that runs it does not bring it back.
        await back(user);
        await sizeStepFor(user, "AL2023 x86 desktop");
        expect(instanceTypeSelect()).toHaveValue("");
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    });

    it("keeps a chosen type the newly chosen image also runs", async () => {
        const user = userEvent.setup();
        mountArch();
        await sizeStepFor(user, "AL2023 x86 desktop");
        await user.selectOptions(instanceTypeSelect(), "g5.2xlarge");

        await back(user);
        await sizeStepFor(user, "Ubuntu x86 desktop");
        expect(instanceTypeSelect()).toHaveValue("g5.2xlarge");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

        await back(user);
        await sizeStepFor(user, "Legacy desktop");
        expect(instanceTypeSelect()).toHaveValue("g5.2xlarge");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
    });

    it("refuses a chosen type once a catalog refresh shows the image does not run it", async () => {
        const user = userEvent.setup();
        const { rerender } = mountArch();
        await sizeStepFor(user, "Legacy desktop");
        await user.selectOptions(instanceTypeSelect(), "m7g.large");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

        // The image catalog now names the image's architecture, which the chosen type does not run.
        (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(
            loaded(
                ARCH_IMAGES.map((image) =>
                    image.imageId === "73" ? { ...image, architecture: "x86_64" } : image
                )
            )
        );
        rerender(wizard());

        expect(offeredTypes()).toEqual(X86_OFFERED);
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
        instanceTypeSelect().focus();
        await user.tab();
        expect(screen.getByText(ARCHITECTURE_MISMATCH_MESSAGE)).toHaveClass("text-vams-error");
    });

    it("leaves a chosen type the loaded list no longer holds to the launch, across a change of image", async () => {
        const user = userEvent.setup();
        const { rerender } = mountArch();
        await sizeStepFor(user, "Legacy desktop");
        await user.selectOptions(instanceTypeSelect(), "m7g.large");

        // A catalog refresh drops the chosen type, so the wizard cannot tell which images it runs.
        (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(
            loaded(ARCH_TYPES.filter((t) => t.instanceType !== "m7g.large"))
        );
        rerender(wizard());
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();

        await back(user);
        await sizeStepFor(user, "AL2023 x86 desktop");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
        instanceTypeSelect().focus();
        await user.tab();
        expect(screen.queryByText(ARCHITECTURE_MISMATCH_MESSAGE)).not.toBeInTheDocument();
        expect(screen.queryByText("Choose an instance type.")).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(within(reviewSummary()).getByText("Instance type").nextSibling).toHaveTextContent(
            /^m7g\.large$/
        );
    });

    it("says so when no instance type runs the chosen image, and keeps today's message for an empty list", async () => {
        const user = userEvent.setup();
        const { unmount } = mountArch(ARCH_IMAGES, [
            { instanceType: "g5.2xlarge", architectures: ["x86_64"] },
        ]);
        await sizeStepFor(user, "AL2023 Graviton desktop");
        expect(offeredTypes()).toEqual([""]);
        expect(screen.getByText(ARM_EMPTY_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(NO_TYPES_MESSAGE)).not.toBeInTheDocument();
        unmount();

        mountArch(ARCH_IMAGES, []);
        await sizeStepFor(user, "AL2023 Graviton desktop");
        expect(screen.getByText(NO_TYPES_MESSAGE)).toBeInTheDocument();
        expect(screen.queryByText(ARM_EMPTY_MESSAGE)).not.toBeInTheDocument();
    });

    it("names the image's architecture in the instance type hint, when known", async () => {
        const user = userEvent.setup();
        mountArch();
        await sizeStepFor(user, "AL2023 x86 desktop");
        expect(instanceTypeSelect()).toHaveAccessibleDescription(
            `The instance types this deployment allows that run the image's architecture (x86_64). ${LARGER_TYPES_HINT}`
        );

        await back(user);
        await sizeStepFor(user, "Legacy desktop");
        expect(instanceTypeSelect()).toHaveAccessibleDescription(
            `The instance types this deployment allows for the desktop profile. ${LARGER_TYPES_HINT}`
        );
    });

    it("badges an image card with its architecture next to the OS, and an unknown one with none", () => {
        mountArch();
        expect(
            within(imageCard("AL2023 x86 desktop")).getByText("Linux").nextSibling
        ).toHaveTextContent(/^x86_64$/);
        expect(
            within(imageCard("AL2023 Graviton desktop")).getByText("Linux").nextSibling
        ).toHaveTextContent(/^arm64$/);
        const legacy = imageCard("Legacy desktop");
        expect(within(legacy).queryByText(/^(x86_64|arm64)$/)).not.toBeInTheDocument();
        expect(within(legacy).getByText("Linux").nextSibling).toBeNull();
    });

    it("shows the architecture on Review after the OS, or a dash when unknown", async () => {
        const user = userEvent.setup();
        mountArch();
        await sizeStepFor(user, "AL2023 Graviton desktop");
        await user.selectOptions(instanceTypeSelect(), "m7g.large");
        await user.click(screen.getByRole("button", { name: "Next" }));
        const os = within(reviewSummary()).getByText("OS");
        expect(os.nextSibling?.nextSibling).toBe(within(reviewSummary()).getByText("Architecture"));
        expect(within(reviewSummary()).getByText("Architecture").nextSibling).toHaveTextContent(
            /^arm64$/
        );

        await back(user);
        await back(user);
        await sizeStepFor(user, "Legacy desktop");
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(within(reviewSummary()).getByText("Architecture").nextSibling).toHaveTextContent(
            /^-$/
        );
    });

    it("sends the launch body it always sent, with no architecture", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        mountArch(ARCH_IMAGES, ARCH_TYPES, mutateAsync);
        await sizeStepFor(user, "AL2023 Graviton desktop");
        await user.selectOptions(instanceTypeSelect(), "m7g.large");
        await user.click(screen.getByRole("button", { name: "Next" }));
        await user.click(screen.getByRole("button", { name: "Launch" }));

        await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
        expect(mutateAsync).toHaveBeenCalledWith({
            bootstrapId: BOOTSTRAP_ID,
            imageId: "71",
            instanceType: "m7g.large",
            name: "AL2023 Graviton desktop",
            rootSizeGb: 60,
            osFamily: "linux",
        });
        expect(Object.keys(mutateAsync.mock.calls[0][0])).not.toContain("architecture");
        expect(Object.keys(mutateAsync.mock.calls[0][0])).not.toContain("architectures");
    });
});
