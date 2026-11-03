/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What the launch wizard offers a user to choose from: the image groups and cards of the Image step,
 * the instance type options with their vCPUs, memory and GPUs, the recipe's preselected type, the
 * default workstation name, and Cancel on every step.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LaunchWorkstationWizard, {
    INSTANCE_TYPE_GROUP_LABELS,
    groupInstanceTypes,
    imageNameCarriesVersion,
    preselectedInstanceType,
} from "./LaunchWorkstationWizard";
import { instanceTypeOptionLabel } from "../labels";
import * as queries from "../api/queries";
import type { EdhCatalogImage, EdhInstanceType } from "../types";

jest.mock("../api/queries", () => ({
    useEdhCatalogImages: jest.fn(),
    useEdhInstanceTypes: jest.fn(),
    useWorkstationMutations: jest.fn(),
    useEdhCatalogLease: jest.fn(() => ({ data: undefined, isLoading: false, error: null })),
    useEdhCatalogCapacityReservations: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

const BOOTSTRAP_ID = "11111111-1111-4111-8111-111111111111";

const ISAAC: EdhCatalogImage = {
    imageId: "90",
    name: "VAMS Isaac Sim v4",
    baseOs: "ubuntu2404",
    osFamily: "linux",
    architecture: "x86_64",
    rootSize: 150,
    description: "NVIDIA Isaac Sim 6.2 for robotics simulation, with the VAMS CLI.",
    category: "amiRecipe",
    recipeId: "isaacsim",
    recipeDisplayName: "NVIDIA Isaac Sim desktop",
    recipeVersion: 4,
    launchInstanceTypes: ["g5.2xlarge", "g6e.xlarge", "g5.xlarge"],
    defaultInstanceType: "g5.2xlarge",
};

const AL2023: EdhCatalogImage = {
    imageId: "35",
    name: "AL2023 desktop",
    baseOs: "amazonlinux2023",
    osFamily: "linux",
    architecture: "x86_64",
    rootSize: 60,
    description: null,
    category: "amiRecipe",
    recipeId: "al2023-desktop",
    recipeDisplayName: "Amazon Linux 2023 desktop",
    recipeVersion: 5,
};

const UBUNTU: EdhCatalogImage = {
    imageId: "12",
    name: "Ubuntu 22",
    baseOs: "ubuntu2204",
    osFamily: "linux",
    architecture: "x86_64",
    rootSize: 100,
    description: "Default Ubuntu image",
    category: "baseline",
};

const A10G = { count: 1, manufacturer: "NVIDIA", name: "A10G", memoryGiB: 24 };

/** The deployment's types with EC2's facts, listed in the catalog's name order. */
const TYPES_WITH_FACTS: EdhInstanceType[] = [
    { instanceType: "c6i.large", architectures: ["x86_64"], vcpus: 2, memoryGiB: 4, gpus: [] },
    {
        instanceType: "g5.12xlarge",
        architectures: ["x86_64"],
        vcpus: 48,
        memoryGiB: 192,
        gpus: [{ ...A10G, count: 4 }],
    },
    {
        instanceType: "g5.2xlarge",
        architectures: ["x86_64"],
        vcpus: 8,
        memoryGiB: 32,
        gpus: [A10G],
    },
    { instanceType: "g5.xlarge", architectures: ["x86_64"], vcpus: 4, memoryGiB: 16, gpus: [A10G] },
    {
        instanceType: "g6e.xlarge",
        architectures: ["x86_64"],
        vcpus: 4,
        memoryGiB: 32,
        gpus: [{ count: 1, manufacturer: "NVIDIA", name: "L40S", memoryGiB: 44.99 }],
    },
    { instanceType: "m7i.large", architectures: ["x86_64"], vcpus: 2, memoryGiB: 8, gpus: [] },
    {
        instanceType: "m7i.12xlarge",
        architectures: ["x86_64"],
        vcpus: 48,
        memoryGiB: 192,
        gpus: [],
    },
];

const TYPES_WITHOUT_FACTS: EdhInstanceType[] = TYPES_WITH_FACTS.map(
    ({ instanceType, architectures }) => ({ instanceType, architectures })
);

/** The catalog's name order (`naturalCompare`), which the wizard keeps when no type has facts. */
const NAME_ORDER = [
    "c6i.large",
    "g5.2xlarge",
    "g5.12xlarge",
    "g5.xlarge",
    "g6e.xlarge",
    "m7i.12xlarge",
    "m7i.large",
];

const loaded = (data: any[]) => ({ data, isLoading: false, error: null });

function mountWizard({
    images = [ISAAC, AL2023, UBUNTU],
    types = TYPES_WITH_FACTS,
    existingNames,
    onClose = jest.fn(),
    mutateAsync = jest.fn(),
}: {
    images?: EdhCatalogImage[];
    types?: EdhInstanceType[];
    existingNames?: string[];
    onClose?: jest.Mock;
    mutateAsync?: jest.Mock;
} = {}) {
    (queries.useEdhCatalogImages as jest.Mock).mockReturnValue(loaded(images));
    (queries.useEdhInstanceTypes as jest.Mock).mockReturnValue(loaded(types));
    (queries.useWorkstationMutations as jest.Mock).mockReturnValue({
        createWorkstation: { mutateAsync, isPending: false },
    });
    render(
        <LaunchWorkstationWizard
            open={true}
            onClose={onClose}
            generateBootstrapId={() => BOOTSTRAP_ID}
            existingNames={existingNames}
        />
    );
    return { onClose, mutateAsync };
}

const instanceTypeSelect = () => screen.getByLabelText("Instance type") as HTMLSelectElement;
const optionTexts = () => Array.from(instanceTypeSelect().options).map((o) => o.textContent);
const optionValues = () => Array.from(instanceTypeSelect().options).map((o) => o.value);

async function sizeStepFor(user: ReturnType<typeof userEvent.setup>, image: RegExp) {
    await user.click(screen.getByRole("radio", { name: image }));
    await user.click(screen.getByRole("button", { name: "Next" }));
}

beforeEach(() => jest.clearAllMocks());

describe("instance type option text", () => {
    it("reads the vCPUs, memory and each kind of GPU", () => {
        expect(instanceTypeOptionLabel(TYPES_WITH_FACTS[2])).toBe(
            "g5.2xlarge — 8 vCPU, 32 GiB, 1 × NVIDIA A10G (24 GiB)"
        );
        expect(instanceTypeOptionLabel(TYPES_WITH_FACTS[1])).toBe(
            "g5.12xlarge — 48 vCPU, 192 GiB, 4 × NVIDIA A10G (24 GiB)"
        );
        expect(instanceTypeOptionLabel(TYPES_WITH_FACTS[4])).toBe(
            "g6e.xlarge — 4 vCPU, 32 GiB, 1 × NVIDIA L40S (44.99 GiB)"
        );
        expect(instanceTypeOptionLabel(TYPES_WITH_FACTS[0])).toBe("c6i.large — 2 vCPU, 4 GiB");
    });

    it("is the bare type name without facts, and leaves out a fact EC2 did not state", () => {
        expect(instanceTypeOptionLabel({ instanceType: "g5.xlarge" })).toBe("g5.xlarge");
        expect(instanceTypeOptionLabel({ instanceType: "g5.xlarge", gpus: [] })).toBe("g5.xlarge");
        expect(
            instanceTypeOptionLabel({
                instanceType: "p5.48xlarge",
                gpus: [{ count: 8, name: "H100" }],
            })
        ).toBe("p5.48xlarge — 8 × H100");
        // A GPU name that already names its maker is not repeated.
        expect(
            instanceTypeOptionLabel({
                instanceType: "g4ad.xlarge",
                vcpus: 4,
                gpus: [{ count: 1, manufacturer: "AMD", name: "AMD Radeon Pro V520" }],
            })
        ).toBe("g4ad.xlarge — 4 vCPU, 1 × AMD Radeon Pro V520");
    });
});

describe("instance type grouping", () => {
    it("lists GPU types first, then the rest, each by family and then by size", () => {
        expect(
            groupInstanceTypes(TYPES_WITH_FACTS).map((g) => [
                g.label,
                g.types.map((t) => t.instanceType),
            ])
        ).toEqual([
            [
                INSTANCE_TYPE_GROUP_LABELS.gpu,
                ["g5.xlarge", "g5.2xlarge", "g5.12xlarge", "g6e.xlarge"],
            ],
            [INSTANCE_TYPE_GROUP_LABELS.other, ["c6i.large", "m7i.large", "m7i.12xlarge"]],
        ]);
    });

    it("keeps the catalog's name order, ungrouped, without facts", () => {
        const groups = groupInstanceTypes(TYPES_WITHOUT_FACTS);
        expect(groups.map((g) => [g.label, g.types.map((t) => t.instanceType)])).toEqual([
            [null, NAME_ORDER],
        ]);
        expect(groupInstanceTypes([])).toEqual([]);
    });

    it("is one ungrouped list when every type is of one kind", () => {
        const gpuOnly = TYPES_WITH_FACTS.filter((t) => (t.gpus ?? []).length > 0);
        expect(groupInstanceTypes(gpuOnly)).toEqual([
            {
                label: null,
                types: ["g5.xlarge", "g5.2xlarge", "g5.12xlarge", "g6e.xlarge"].map((name) =>
                    gpuOnly.find((t) => t.instanceType === name)
                ),
            },
        ]);
    });

    it("renders the groups as option groups in the select", async () => {
        const user = userEvent.setup();
        mountWizard();
        await sizeStepFor(user, /Ubuntu 22/);

        const groups = within(instanceTypeSelect()).getAllByRole("group");
        expect(groups.map((g) => g.getAttribute("label"))).toEqual([
            INSTANCE_TYPE_GROUP_LABELS.gpu,
            INSTANCE_TYPE_GROUP_LABELS.other,
        ]);
        expect(
            within(groups[0])
                .getAllByRole("option")
                .map((o) => o.textContent)
        ).toEqual([
            "g5.xlarge — 4 vCPU, 16 GiB, 1 × NVIDIA A10G (24 GiB)",
            "g5.2xlarge — 8 vCPU, 32 GiB, 1 × NVIDIA A10G (24 GiB)",
            "g5.12xlarge — 48 vCPU, 192 GiB, 4 × NVIDIA A10G (24 GiB)",
            "g6e.xlarge — 4 vCPU, 32 GiB, 1 × NVIDIA L40S (44.99 GiB)",
        ]);
        // The value is the type name whatever the option reads.
        expect(optionValues()).toEqual([
            "",
            "g5.xlarge",
            "g5.2xlarge",
            "g5.12xlarge",
            "g6e.xlarge",
            "c6i.large",
            "m7i.large",
            "m7i.12xlarge",
        ]);
    });

    it("offers the bare type names in name order when the catalog states no facts", async () => {
        const user = userEvent.setup();
        mountWizard({ types: TYPES_WITHOUT_FACTS });
        await sizeStepFor(user, /Ubuntu 22/);

        expect(within(instanceTypeSelect()).queryAllByRole("group")).toEqual([]);
        expect(optionTexts()).toEqual(["Choose an instance type", ...NAME_ORDER]);
    });
});

describe("the recipe's instance type", () => {
    it("is chosen when the image is chosen, and launched unless the user changes it", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest
            .fn()
            .mockResolvedValue({ bootstrapId: BOOTSTRAP_ID, state: "CREATING" });
        mountWizard({ mutateAsync });
        await sizeStepFor(user, /VAMS Isaac Sim v4/);

        expect(instanceTypeSelect()).toHaveValue("g5.2xlarge");
        expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
        await user.click(screen.getByRole("button", { name: "Next" }));
        expect(
            within(screen.getByRole("dialog").querySelector("dl") as HTMLElement).getByText(
                "g5.2xlarge — 8 vCPU, 32 GiB, 1 × NVIDIA A10G (24 GiB)"
            )
        ).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Launch" }));
        await waitFor(() =>
            expect(mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({ imageId: "90", instanceType: "g5.2xlarge" })
            )
        );
    });

    it("gives way to the user's own choice, which a change of image keeps when it still applies", async () => {
        const user = userEvent.setup();
        mountWizard();
        await sizeStepFor(user, /VAMS Isaac Sim v4/);
        await user.selectOptions(instanceTypeSelect(), "g6e.xlarge");
        expect(instanceTypeSelect()).toHaveValue("g6e.xlarge");

        await user.click(screen.getByRole("button", { name: "Back" }));
        await sizeStepFor(user, /Ubuntu 22/);
        expect(instanceTypeSelect()).toHaveValue("g6e.xlarge");
    });

    it("is not carried to an image without a default", async () => {
        const user = userEvent.setup();
        mountWizard();
        await sizeStepFor(user, /VAMS Isaac Sim v4/);
        expect(instanceTypeSelect()).toHaveValue("g5.2xlarge");

        await user.click(screen.getByRole("button", { name: "Back" }));
        await sizeStepFor(user, /Ubuntu 22/);
        expect(instanceTypeSelect()).toHaveValue("");
        expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    });

    it("leaves the prompt for an image without a default, or whose default the wizard does not offer", async () => {
        const user = userEvent.setup();
        mountWizard({
            images: [{ ...ISAAC, defaultInstanceType: "p5.48xlarge" }, UBUNTU],
        });
        await sizeStepFor(user, /VAMS Isaac Sim v4/);
        expect(instanceTypeSelect()).toHaveValue("");

        expect(preselectedInstanceType(UBUNTU, TYPES_WITH_FACTS)).toBe("");
        expect(preselectedInstanceType(ISAAC, TYPES_WITH_FACTS)).toBe("g5.2xlarge");
        expect(preselectedInstanceType(ISAAC, [])).toBe("");
        expect(preselectedInstanceType(undefined, TYPES_WITH_FACTS)).toBe("");
    });
});

describe("the default workstation name", () => {
    it("is the short image name and the lowest number the caller's workstations leave free", async () => {
        const user = userEvent.setup();
        mountWizard({
            existingNames: ["vams-isaac-sim-v4-1", "vams-isaac-sim-v4-2", "ubuntu-22-4", "Dev box"],
        });
        await sizeStepFor(user, /VAMS Isaac Sim v4/);
        expect(screen.getByLabelText("Workstation name")).toHaveValue("vams-isaac-sim-v4-3");

        await user.click(screen.getByRole("button", { name: "Back" }));
        await sizeStepFor(user, /Ubuntu 22/);
        expect(screen.getByLabelText("Workstation name")).toHaveValue("ubuntu-22-1");
    });

    it("can still be edited, and a typed name is kept across a change of image", async () => {
        const user = userEvent.setup();
        mountWizard();
        await sizeStepFor(user, /Ubuntu 22/);
        await user.clear(screen.getByLabelText("Workstation name"));
        await user.type(screen.getByLabelText("Workstation name"), "Render box");

        await user.click(screen.getByRole("button", { name: "Back" }));
        await sizeStepFor(user, /AL2023 desktop/);
        expect(screen.getByLabelText("Workstation name")).toHaveValue("Render box");
    });
});

describe("the Image step", () => {
    it("groups the images under VAMS desktops and Standard images", () => {
        mountWizard();
        const desktops = screen.getByRole("group", { name: "VAMS desktops" });
        const standard = screen.getByRole("group", { name: "Standard images" });
        expect(
            within(desktops)
                .getAllByRole("radio")
                .map((r) => (r as HTMLInputElement).value)
        ).toEqual(["35", "90"]);
        expect(
            within(standard)
                .getAllByRole("radio")
                .map((r) => (r as HTMLInputElement).value)
        ).toEqual(["12"]);
        expect(screen.queryByText("Custom AMI Recipe")).not.toBeInTheDocument();
        expect(screen.queryByText("Baseline Image")).not.toBeInTheDocument();
    });

    it("names the step once: the image choice's own heading is for screen readers only", () => {
        mountWizard();
        const choice = screen.getByRole("group", { name: "Image" });
        const legend = choice.querySelector("legend") as HTMLElement;
        expect(legend).toHaveTextContent("Image");
        expect(legend).toHaveClass("sr-only");
    });

    it("badges the build version only on an image whose name does not already say it", () => {
        mountWizard();
        const isaac = screen.getByRole("radio", { name: /VAMS Isaac Sim v4/ }).closest("label");
        const al2023 = screen.getByRole("radio", { name: /AL2023 desktop/ }).closest("label");
        expect(within(isaac as HTMLElement).getAllByText(/v4/)).toHaveLength(1);
        expect(within(al2023 as HTMLElement).getByText("v5")).toBeInTheDocument();
    });

    it("tells a version in the name from one inside another word or number", () => {
        expect(imageNameCarriesVersion("VAMS AL2023 Desktop v3", 3)).toBe(true);
        expect(imageNameCarriesVersion("isaacsim-6-2-v14", 14)).toBe(true);
        expect(imageNameCarriesVersion("Desktop V3", 3)).toBe(true);
        expect(imageNameCarriesVersion("Desktop v30", 3)).toBe(false);
        expect(imageNameCarriesVersion("dev3 desktop", 3)).toBe(false);
        expect(imageNameCarriesVersion("AL2023 desktop", 5)).toBe(false);
    });
});

describe("Cancel", () => {
    it.each(["image", "size", "review"] as const)(
        "closes the dialog from the %s step and launches nothing",
        async (step) => {
            const user = userEvent.setup();
            const { onClose, mutateAsync } = mountWizard();
            if (step !== "image") {
                await sizeStepFor(user, /VAMS Isaac Sim v4/);
            }
            if (step === "review") {
                await user.click(screen.getByRole("button", { name: "Next" }));
                expect(screen.getByRole("button", { name: "Launch" })).toBeInTheDocument();
            }
            await user.click(screen.getByRole("button", { name: "Cancel" }));
            expect(onClose).toHaveBeenCalledTimes(1);
            expect(mutateAsync).not.toHaveBeenCalled();
        }
    );
});
