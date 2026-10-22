/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AmiRecipeFormDialog, { BASE_OS_BY_FAMILY, validateAmiRecipe } from "./AmiRecipeFormDialog";
import { BASE_OS_OPTIONS } from "./RegisterImageDialog";
import type { EdhAmiRecipe } from "../types";

// The inline component document is edited in Monaco, which does not render under jsdom.
jest.mock("../../../features/orchestration/components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value, onChange, readOnly }: any) => (
        <textarea
            aria-label="component-yaml"
            value={value}
            readOnly={readOnly}
            onChange={(e) => onChange?.(e.target.value)}
        />
    ),
}));

const VALID = {
    recipeId: "al2023-desktop",
    displayName: "AL2023 desktop",
    kind: "vdi",
    osFamily: "linux",
    baseOs: "amazonlinux2023",
    architecture: "x86_64",
    parentImage: "ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
    buildInstanceType: "m7i.large",
    launchInstanceTypes: "",
    rootSizeGb: "60",
    stackName: "VAMS AL2023 Desktop",
    profileId: "1",
    keepVersions: "3",
    recipeRevision: "1",
    description: "",
    components: [
        {
            mode: "arn" as const,
            arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0",
            name: "",
            data: "",
        },
    ],
};

const RECIPE = {
    recipeId: "mine",
    displayName: "Mine",
    kind: "simulation",
    osFamily: "linux",
    architecture: "arm64",
    parentImage: "ami-0123456789abcdef0",
    buildInstanceType: "c7g.large",
    rootSizeGb: 40,
    stackName: "Mine",
    profileId: 2,
    components: [{ name: "setup", data: "schemaVersion: 1.0" }],
    keepVersions: 3,
    recipeRevision: 2,
    source: "admin",
    enabled: true,
    contentHash: "def",
    latestVersion: 1,
    createdAt: "",
    updatedAt: "",
} as unknown as EdhAmiRecipe;

// A shipped recipe as the service lists it: bundled files, parameters and the fields it owns.
const SHIPPED = {
    recipeId: "isaacsim",
    displayName: "NVIDIA Isaac Sim desktop",
    kind: "vdi",
    osFamily: "linux",
    baseOs: "ubuntu2404",
    architecture: "x86_64",
    parentImage:
        "ssm:/aws/service/canonical/ubuntu/server/24.04/stable/20260714/amd64/hvm/ebs-gp3/ami-id",
    buildInstanceType: ["m7i.xlarge", "m6i.xlarge"],
    rootSizeGb: 150,
    stackName: "VAMS Isaac Sim Desktop",
    profileId: 1,
    components: [
        { arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0" },
        { name: "isaacsim", data: "schemaVersion: 1.0" },
    ],
    assets: [
        { id: "vamscli", repoPath: "tools/VamsCLI" },
        { id: "connector", repoPath: "tools/ExternalIntegrations/isaacsim_vams_integration" },
    ],
    assetPrefix: "addon/edh/amiRecipes/abc/isaacsim/assets",
    parameters: { isaacSimVersion: "5.1.0" },
    keepVersions: 3,
    recipeRevision: 1,
    description: "Ubuntu 24.04 with NVIDIA Isaac Sim 5.1",
    source: "config",
    enabled: true,
    contentHash: "abc",
    latestVersion: 10,
    buildOnDeploy: true,
    createdAt: "",
    updatedAt: "",
} as unknown as EdhAmiRecipe;

const renderDialog = (
    props: Partial<React.ComponentProps<typeof AmiRecipeFormDialog>> = {}
): jest.Mock => {
    const onSubmit = (props.onSubmit as jest.Mock) ?? jest.fn().mockResolvedValue(undefined);
    render(
        <AmiRecipeFormDialog
            open
            busy={false}
            onOpenChange={() => undefined}
            {...props}
            onSubmit={onSubmit}
        />
    );
    return onSubmit;
};

describe("validateAmiRecipe", () => {
    it("accepts a recipe the backend model would accept", () => {
        expect(validateAmiRecipe(VALID)).toEqual({});
    });

    it("rejects a recipe id outside the backend pattern", () => {
        expect(validateAmiRecipe({ ...VALID, recipeId: "AL2023_Desktop" }).recipeId).toBeTruthy();
        expect(validateAmiRecipe({ ...VALID, recipeId: "a" }).recipeId).toBeTruthy();
        expect(validateAmiRecipe({ ...VALID, recipeId: "-leading" }).recipeId).toBeTruthy();
    });

    it("requires a base OS for a vdi recipe and asks for none on a simulation recipe", () => {
        expect(validateAmiRecipe({ ...VALID, baseOs: "" }).baseOs).toBeTruthy();
        expect(
            validateAmiRecipe({ ...VALID, kind: "simulation", baseOs: "" }).baseOs
        ).toBeUndefined();
    });

    it("rejects a base OS from the other OS family", () => {
        expect(validateAmiRecipe({ ...VALID, baseOs: "windows2022" }).baseOs).toBeTruthy();
        expect(
            validateAmiRecipe({ ...VALID, osFamily: "windows", baseOs: "windows2022" }).baseOs
        ).toBeUndefined();
    });

    it("rejects a parent image that is not an AMI, an SSM path or an EDH stack", () => {
        expect(validateAmiRecipe({ ...VALID, parentImage: "ubuntu" }).parentImage).toBeTruthy();
        expect(validateAmiRecipe({ ...VALID, parentImage: "ami-0abc" }).parentImage).toBeTruthy();
        for (const parentImage of [
            "ami-0123456789abcdef0",
            "ssm:/aws/service/canonical/x",
            "edhStack:VAMS AL2023 Desktop",
        ]) {
            expect(validateAmiRecipe({ ...VALID, parentImage }).parentImage).toBeUndefined();
        }
    });

    it("rejects an SSM parent outside the public AMI parameters, as the service does", () => {
        for (const parentImage of ["ssm:/team/golden-ami", "ssm:/aws/servicex/ami"]) {
            expect(validateAmiRecipe({ ...VALID, parentImage }).parentImage).toBe(
                "Must be an AMI id, an ssm:/aws/service/ parameter path, or edhStack: followed by an EDH stack name."
            );
        }
    });

    it("rejects a build instance type that is not an EC2 instance type", () => {
        expect(
            validateAmiRecipe({ ...VALID, buildInstanceType: "m7ilarge" }).buildInstanceType
        ).toBeTruthy();
    });

    it("accepts up to eight different build instance types separated by commas, and no more", () => {
        const eight = Array.from({ length: 8 }, (_, n) => `c${n + 1}.large`);
        for (const buildInstanceType of ["m7i.xlarge, m6i.xlarge,m5.xlarge", eight.join(", ")]) {
            expect(
                validateAmiRecipe({ ...VALID, buildInstanceType }).buildInstanceType
            ).toBeUndefined();
        }
        for (const buildInstanceType of [
            "",
            "m7i.large, large",
            "m7i.large, m7i.large",
            "m7i.large,",
            [...eight, "c9.large"].join(", "),
            `m7i.large, ${"m".repeat(63)}.x`,
        ]) {
            expect(validateAmiRecipe({ ...VALID, buildInstanceType }).buildInstanceType).toBe(
                "Enter an EC2 instance type, e.g. m7i.large, or up to 8 different ones separated by commas."
            );
        }
    });

    it("accepts no launch instance types, or up to 64 different ones separated by commas", () => {
        const sixtyFour = Array.from({ length: 64 }, (_, n) => `g${n}.xlarge`);
        for (const launchInstanceTypes of [
            "",
            "   ",
            "g5.2xlarge",
            "g6e.xlarge, g5.2xlarge,gr6.4xlarge",
            sixtyFour.join(", "),
            `g5.2xlarge, ${"m".repeat(62)}.x`,
        ]) {
            expect(
                validateAmiRecipe({ ...VALID, launchInstanceTypes }).launchInstanceTypes
            ).toBeUndefined();
        }
        for (const launchInstanceTypes of [
            "g5.2xlarge, large",
            "g5.2xlarge, g5.2xlarge",
            "g5.2xlarge,",
            "G5.2xlarge",
            [...sixtyFour, "g64.xlarge"].join(", "),
            `g5.2xlarge, ${"m".repeat(63)}.x`,
        ]) {
            expect(validateAmiRecipe({ ...VALID, launchInstanceTypes }).launchInstanceTypes).toBe(
                "Enter up to 64 different EC2 instance types separated by commas, e.g. g5.2xlarge, g6e.xlarge, or leave it empty."
            );
        }
    });

    it("rejects a root disk size outside the bounds EDH allows", () => {
        expect(validateAmiRecipe({ ...VALID, rootSizeGb: "4" }).rootSizeGb).toBeTruthy();
        expect(validateAmiRecipe({ ...VALID, rootSizeGb: "4096" }).rootSizeGb).toBeTruthy();
        expect(validateAmiRecipe({ ...VALID, rootSizeGb: "60.5" }).rootSizeGb).toBeTruthy();
    });

    it("rejects an empty component list and an incomplete component", () => {
        expect(validateAmiRecipe({ ...VALID, components: [] }).components).toBeTruthy();
        expect(
            validateAmiRecipe({
                ...VALID,
                components: [{ mode: "arn", arn: "  ", name: "", data: "" }],
            }).components
        ).toBeTruthy();
        expect(
            validateAmiRecipe({
                ...VALID,
                components: [{ mode: "inline", arn: "", name: "setup", data: "steps: []" }],
            }).components
        ).toBeTruthy();
        expect(
            validateAmiRecipe({
                ...VALID,
                components: [
                    { mode: "inline", arn: "", name: "setup", data: "schemaVersion: 1.0" },
                ],
            }).components
        ).toBeUndefined();
    });

    it("holds the display name and stack name to the lengths the backend model allows", () => {
        expect(
            validateAmiRecipe({ ...VALID, displayName: "d".repeat(256) }).displayName
        ).toBeUndefined();
        expect(validateAmiRecipe({ ...VALID, displayName: "d".repeat(257) }).displayName).toBe(
            "256 characters at most."
        );
        expect(
            validateAmiRecipe({ ...VALID, stackName: "s".repeat(200) }).stackName
        ).toBeUndefined();
        expect(validateAmiRecipe({ ...VALID, stackName: "s".repeat(201) }).stackName).toBe(
            "200 characters at most."
        );
        // The model trims a name before it counts it.
        expect(
            validateAmiRecipe({ ...VALID, stackName: ` ${"s".repeat(200)} ` }).stackName
        ).toBeUndefined();
    });

    it("holds a Windows recipe to x86_64", () => {
        const windows = { ...VALID, osFamily: "windows", baseOs: "windows2022" };
        expect(validateAmiRecipe({ ...windows, architecture: "arm64" }).architecture).toBeTruthy();
        expect(validateAmiRecipe(windows).architecture).toBeUndefined();
        expect(validateAmiRecipe({ ...VALID, architecture: "arm64" }).architecture).toBeUndefined();
    });
});

describe("BASE_OS_BY_FAMILY", () => {
    it("splits the register dialog's base OS keys by family", () => {
        expect(BASE_OS_BY_FAMILY.linux.map((option) => option.value)).toEqual(
            BASE_OS_OPTIONS.filter((option) => !option.value.startsWith("windows")).map(
                (option) => option.value
            )
        );
        expect(BASE_OS_BY_FAMILY.windows.map((option) => option.value)).toEqual([
            "windows2019",
            "windows2022",
            "windows2025",
        ]);
    });
});

describe("AmiRecipeFormDialog", () => {
    it("submits the typed recipe with numbers coerced and components shaped", async () => {
        const onSubmit = renderDialog();
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("Recipe id"), "mine");
        await user.type(screen.getByLabelText("Display name"), "Mine");
        await user.selectOptions(screen.getByLabelText("Kind"), "simulation");
        await user.selectOptions(screen.getByLabelText("Architecture"), "arm64");
        await user.type(screen.getByLabelText("Parent image"), "ami-0123456789abcdef0");
        await user.type(screen.getByLabelText("Build instance type"), "c7g.large");
        await user.type(screen.getByLabelText("Stack name"), "Mine");
        await user.type(screen.getByLabelText("Profile id"), "2");
        // A simulation recipe carries no base OS, so the select is not offered at all.
        expect(screen.queryByLabelText("Base OS")).not.toBeInTheDocument();

        await user.type(
            screen.getByLabelText("Component 1 ARN"),
            "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0"
        );
        await user.click(screen.getByRole("button", { name: "Add component" }));
        await user.selectOptions(screen.getByLabelText("Component 2 type"), "inline");
        await user.type(screen.getByLabelText("Component 2 name"), "setup");
        await user.type(screen.getByLabelText("component-yaml"), "schemaVersion: 1.0");

        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit).toHaveBeenCalledWith({
            recipeId: "mine",
            displayName: "Mine",
            kind: "simulation",
            osFamily: "linux",
            architecture: "arm64",
            parentImage: "ami-0123456789abcdef0",
            buildInstanceType: "c7g.large",
            rootSizeGb: 100,
            stackName: "Mine",
            profileId: 2,
            components: [
                { arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0" },
                { name: "setup", data: "schemaVersion: 1.0" },
            ],
            recipeRevision: 1,
        });
    });

    it("shows no validation message before the first attempt, then flags the failing field", async () => {
        const onSubmit = renderDialog({ onSubmit: jest.fn() });
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("Recipe id"), "Not Valid");
        await user.click(screen.getByRole("button", { name: "Create recipe" }));

        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.getByRole("alert")).toBeInTheDocument();
        expect(screen.getByLabelText("Recipe id")).toHaveAttribute("aria-invalid", "true");
        expect(screen.getByLabelText("Display name")).toHaveAttribute("aria-invalid", "true");
    });

    it("keeps the recipe id of an existing recipe and loads its fields", async () => {
        const onSubmit = renderDialog({ initial: RECIPE });
        expect(screen.getByRole("dialog")).toHaveTextContent("Edit recipe");
        expect(screen.getByLabelText("Recipe id")).toBeDisabled();
        expect(screen.getByLabelText("Recipe id")).toHaveValue("mine");
        expect(screen.getByLabelText("Display name")).toHaveValue("Mine");
        expect(screen.getByLabelText("Kind")).toHaveValue("simulation");
        expect(screen.getByLabelText("Root disk size (GB)")).toHaveValue(40);
        expect(screen.getByLabelText("Component 1 name")).toHaveValue("setup");
        expect(screen.getByLabelText("component-yaml")).toHaveValue("schemaVersion: 1.0");

        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0]).toMatchObject({
            recipeId: "mine",
            rootSizeGb: 40,
            recipeRevision: 2,
            components: [{ name: "setup", data: "schemaVersion: 1.0" }],
            // The recipe stays as it was found: the backend rebuilds the item from this body.
            enabled: true,
        });
    });

    it("sends one build instance type as a string and several as a list, in the order typed", async () => {
        const onSubmit = renderDialog({ initial: RECIPE });
        const user = userEvent.setup();
        const field = screen.getByLabelText("Build instance type");
        expect(field).toHaveValue("c7g.large");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0].buildInstanceType).toBe("c7g.large");

        await user.clear(field);
        await user.type(field, "c7g.large, c6g.large , m6g.large");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
        expect(onSubmit.mock.calls[1][0].buildInstanceType).toEqual([
            "c7g.large",
            "c6g.large",
            "m6g.large",
        ]);
    });

    it("tells the admin a build instance type must run the recipe's architecture", () => {
        renderDialog();
        expect(screen.getByLabelText("Build instance type")).toHaveAccessibleDescription(
            "Each type must run the recipe's architecture. Several, separated by commas, let Image Builder choose among them by availability."
        );
    });

    it("shows a recipe whose build instance type is a list as the types separated by commas", async () => {
        const listed = { ...RECIPE, buildInstanceType: ["c7g.large", "c6g.large"] } as EdhAmiRecipe;
        const onSubmit = renderDialog({ initial: listed });
        expect(screen.getByLabelText("Build instance type")).toHaveValue("c7g.large, c6g.large");
        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0].buildInstanceType).toEqual(["c7g.large", "c6g.large"]);
    });

    it("leaves the launch instance types out of the body until some are typed, then sends a list", async () => {
        const onSubmit = renderDialog({ initial: RECIPE });
        const user = userEvent.setup();
        const field = screen.getByLabelText("Launch instance types");
        expect(field).toHaveValue("");
        expect(field).toHaveAccessibleDescription(
            "The instance types a desktop of this recipe's image may launch on, separated by commas. Leave it empty to offer every type the deployment allows."
        );
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0]).not.toHaveProperty("launchInstanceTypes");

        await user.type(field, "g6e.xlarge , g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
        expect(onSubmit.mock.calls[1][0].launchInstanceTypes).toEqual(["g6e.xlarge", "g5.2xlarge"]);

        // One type is still a list.
        await user.clear(field);
        await user.type(field, "g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(3));
        expect(onSubmit.mock.calls[2][0].launchInstanceTypes).toEqual(["g5.2xlarge"]);
    });

    it("loads a stored list separated by commas, and clearing it sends none", async () => {
        const listed = {
            ...RECIPE,
            launchInstanceTypes: ["g5.2xlarge", "g6e.xlarge"],
        } as EdhAmiRecipe;
        const onSubmit = renderDialog({ initial: listed });
        const field = screen.getByLabelText("Launch instance types");
        expect(field).toHaveValue("g5.2xlarge, g6e.xlarge");
        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0].launchInstanceTypes).toEqual(["g5.2xlarge", "g6e.xlarge"]);

        await user.clear(field);
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(2));
        expect(onSubmit.mock.calls[1][0]).not.toHaveProperty("launchInstanceTypes");
    });

    it("flags a malformed launch list under its field and submits nothing", async () => {
        const onSubmit = renderDialog({ initial: RECIPE, onSubmit: jest.fn() });
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("Launch instance types"), "g5.2xlarge, g5.2xlarge");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));

        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.getByLabelText("Launch instance types")).toHaveAttribute(
            "aria-invalid",
            "true"
        );
        expect(
            screen.getByText(
                "Enter up to 64 different EC2 instance types separated by commas, e.g. g5.2xlarge, g6e.xlarge, or leave it empty."
            )
        ).toBeInTheDocument();
    });

    it("shows a config recipe's launch list read-only", () => {
        renderDialog({
            initial: { ...SHIPPED, launchInstanceTypes: ["g5.2xlarge", "g6e.xlarge"] },
        });
        expect(screen.getByLabelText("Launch instance types")).toHaveValue(
            "g5.2xlarge, g6e.xlarge"
        );
        expect(screen.getByLabelText("Launch instance types")).toBeDisabled();
    });

    it("copies the source's launch list into a duplicate and sends it", async () => {
        const source = {
            ...SHIPPED,
            launchInstanceTypes: ["g5.2xlarge", "g6e.xlarge"],
        } as EdhAmiRecipe;
        const onSubmit = renderDialog({ duplicateSource: source });
        expect(screen.getByLabelText("Launch instance types")).toHaveValue(
            "g5.2xlarge, g6e.xlarge"
        );
        expect(screen.getByLabelText("Launch instance types")).toBeEnabled();
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("Recipe id"), "isaacsim-lab");
        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        expect(onSubmit.mock.calls[0][0]).toMatchObject({
            recipeId: "isaacsim-lab",
            duplicateOf: "isaacsim",
            launchInstanceTypes: ["g5.2xlarge", "g6e.xlarge"],
        });
    });

    it("opens a config recipe with several build instance types read-only", () => {
        renderDialog({
            initial: {
                ...RECIPE,
                source: "config",
                buildInstanceType: ["m7i.xlarge", "m6i.xlarge"],
            },
        });
        expect(screen.getByRole("dialog")).toHaveTextContent("View recipe");
        expect(screen.getByLabelText("Build instance type")).toHaveValue("m7i.xlarge, m6i.xlarge");
        expect(screen.getByLabelText("Build instance type")).toBeDisabled();
    });

    it("opens a config recipe read-only, with no way to submit it", () => {
        renderDialog({ initial: { ...RECIPE, source: "config" } });
        const dialog = screen.getByRole("dialog");
        expect(dialog).toHaveTextContent("View recipe");
        expect(within(dialog).queryByRole("button", { name: /recipe$/ })).not.toBeInTheDocument();
        expect(
            within(dialog).queryByRole("button", { name: "Add component" })
        ).not.toBeInTheDocument();
        expect(screen.getByLabelText("Display name")).toBeDisabled();
        expect(screen.getByLabelText("Kind")).toBeDisabled();
        expect(screen.getByLabelText("Component 1 name")).toBeDisabled();
        expect(screen.getByLabelText("component-yaml")).toHaveAttribute("readonly");
        expect(within(dialog).getByRole("button", { name: "Close" })).toBeInTheDocument();
    });

    it("offers the base OS keys of the chosen OS family", async () => {
        renderDialog();
        const user = userEvent.setup();
        const options = () =>
            Array.from((screen.getByLabelText("Base OS") as HTMLSelectElement).options).map(
                (option) => option.value
            );
        expect(options()).toEqual(["", ...BASE_OS_BY_FAMILY.linux.map((o) => o.value)]);
        await user.selectOptions(screen.getByLabelText("OS family"), "windows");
        expect(options()).toEqual(["", ...BASE_OS_BY_FAMILY.windows.map((o) => o.value)]);
    });

    const architectures = () =>
        Array.from((screen.getByLabelText("Architecture") as HTMLSelectElement).options).map(
            (option) => option.value
        );

    it("offers a Windows recipe x86_64 only and selects it", async () => {
        renderDialog();
        const user = userEvent.setup();
        await user.selectOptions(screen.getByLabelText("Architecture"), "arm64");

        await user.selectOptions(screen.getByLabelText("OS family"), "windows");

        expect(architectures()).toEqual(["x86_64"]);
        expect(screen.getByLabelText("Architecture")).toHaveValue("x86_64");
        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        expect(screen.getByLabelText("Architecture")).not.toHaveAttribute("aria-invalid", "true");
        await user.selectOptions(screen.getByLabelText("OS family"), "linux");
        expect(architectures()).toEqual(["x86_64", "arm64"]);
    });

    it("flags a stored Windows recipe on arm64 instead of changing it", async () => {
        const onSubmit = renderDialog({
            initial: { ...RECIPE, osFamily: "windows" } as EdhAmiRecipe,
        });
        const user = userEvent.setup();
        expect(architectures()).toEqual(["x86_64", "arm64"]);
        expect(screen.getByLabelText("Architecture")).toHaveValue("arm64");

        await user.click(screen.getByRole("button", { name: "Save recipe" }));

        expect(screen.getByLabelText("Architecture")).toHaveAttribute("aria-invalid", "true");
        expect(onSubmit).not.toHaveBeenCalled();
    });

    it("offers a stored Linux recipe switched to Windows x86_64 only", async () => {
        renderDialog({ initial: RECIPE });
        const user = userEvent.setup();
        expect(screen.getByLabelText("Architecture")).toHaveValue("arm64");

        await user.selectOptions(screen.getByLabelText("OS family"), "windows");

        expect(architectures()).toEqual(["x86_64"]);
        expect(screen.getByLabelText("Architecture")).toHaveValue("x86_64");
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        expect(screen.getByLabelText("Architecture")).not.toHaveAttribute("aria-invalid", "true");
    });

    it("opens a duplicate as a new recipe prefilled from its source, with an empty recipe id and copy names", () => {
        renderDialog({ duplicateSource: SHIPPED });
        const dialog = screen.getByRole("dialog");
        expect(dialog).toHaveTextContent("Duplicate recipe");
        expect(screen.getByLabelText("Recipe id")).toHaveValue("");
        expect(screen.getByLabelText("Recipe id")).toBeEnabled();
        expect(screen.getByLabelText("Display name")).toHaveValue(
            "NVIDIA Isaac Sim desktop (copy)"
        );
        expect(screen.getByLabelText("Stack name")).toHaveValue("VAMS Isaac Sim Desktop Copy");
        expect(screen.getByLabelText("Kind")).toHaveValue("vdi");
        expect(screen.getByLabelText("OS family")).toHaveValue("linux");
        expect(screen.getByLabelText("Base OS")).toHaveValue("ubuntu2404");
        expect(screen.getByLabelText("Architecture")).toHaveValue("x86_64");
        expect(screen.getByLabelText("Parent image")).toHaveValue(SHIPPED.parentImage);
        expect(screen.getByLabelText("Build instance type")).toHaveValue("m7i.xlarge, m6i.xlarge");
        expect(screen.getByLabelText("Root disk size (GB)")).toHaveValue(150);
        expect(screen.getByLabelText("Profile id")).toHaveValue(1);
        expect(screen.getByLabelText("Keep versions")).toHaveValue(3);
        expect(screen.getByLabelText("Recipe revision")).toHaveValue(1);
        expect(screen.getByLabelText("Description")).toHaveValue(
            "Ubuntu 24.04 with NVIDIA Isaac Sim 5.1"
        );
        expect(screen.getByLabelText("Component 1 ARN")).toHaveValue(
            "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0"
        );
        expect(screen.getByLabelText("Component 2 name")).toHaveValue("isaacsim");
        // The bundled files come with the duplicate: named, not editable.
        expect(
            within(dialog).getByText("Bundled files from isaacsim: vamscli, connector")
        ).toBeInTheDocument();
        expect(within(dialog).queryByRole("textbox", { name: /bundled/i })).not.toBeInTheDocument();
        expect(within(dialog).getByRole("button", { name: "Create recipe" })).toBeEnabled();
    });

    it("needs a recipe id for a duplicate and submits it with duplicateOf, the source's parameters and no assets", async () => {
        const onSubmit = renderDialog({ duplicateSource: SHIPPED });
        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.getByLabelText("Recipe id")).toHaveAttribute("aria-invalid", "true");

        await user.type(screen.getByLabelText("Recipe id"), "isaacsim-lab");
        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        const body = onSubmit.mock.calls[0][0];
        expect(body).toEqual({
            recipeId: "isaacsim-lab",
            displayName: "NVIDIA Isaac Sim desktop (copy)",
            kind: "vdi",
            osFamily: "linux",
            baseOs: "ubuntu2404",
            architecture: "x86_64",
            parentImage: SHIPPED.parentImage,
            buildInstanceType: ["m7i.xlarge", "m6i.xlarge"],
            rootSizeGb: 150,
            stackName: "VAMS Isaac Sim Desktop Copy",
            profileId: 1,
            components: [
                { arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0" },
                { name: "isaacsim", data: "schemaVersion: 1.0" },
            ],
            keepVersions: 3,
            recipeRevision: 1,
            description: "Ubuntu 24.04 with NVIDIA Isaac Sim 5.1",
            parameters: { isaacSimVersion: "5.1.0" },
            duplicateOf: "isaacsim",
        });
        // A new recipe starts enabled, and the files are the service's to copy.
        expect(body).not.toHaveProperty("assets");
        expect(body).not.toHaveProperty("enabled");
    });

    it("flags copy names that outgrow the backend's limits under their fields", async () => {
        const onSubmit = renderDialog({
            duplicateSource: {
                ...SHIPPED,
                displayName: "d".repeat(250),
                stackName: "s".repeat(196),
            } as EdhAmiRecipe,
        });
        const user = userEvent.setup();
        await user.type(screen.getByLabelText("Recipe id"), "isaacsim-lab");
        await user.click(screen.getByRole("button", { name: "Create recipe" }));
        expect(onSubmit).not.toHaveBeenCalled();
        expect(screen.getByLabelText("Display name")).toHaveAttribute("aria-invalid", "true");
        expect(screen.getByLabelText("Stack name")).toHaveAttribute("aria-invalid", "true");
        expect(screen.getByText("256 characters at most.")).toBeInTheDocument();
        expect(screen.getByText("200 characters at most.")).toBeInTheDocument();
    });

    it("shows no bundled-files line for a recipe without bundled files", () => {
        renderDialog({ duplicateSource: RECIPE });
        expect(screen.getByLabelText("Display name")).toHaveValue("Mine (copy)");
        expect(screen.queryByText(/Bundled files/)).not.toBeInTheDocument();
    });

    it("names a shipped recipe's bundled files when it is viewed", () => {
        renderDialog({ initial: SHIPPED });
        const dialog = screen.getByRole("dialog");
        expect(dialog).toHaveTextContent("View recipe");
        expect(within(dialog).getByText("Bundled files: vamscli, connector")).toBeInTheDocument();
    });

    it("shows a duplicated recipe's inherited files read-only in Edit and sends neither them nor duplicateOf", async () => {
        const onSubmit = renderDialog({
            initial: {
                ...RECIPE,
                assets: [{ id: "vamscli", repoPath: "tools/VamsCLI" }],
                assetPrefix: "addon/edh/amiRecipes/abc/al2023-desktop/assets",
                duplicatedFrom: "al2023-desktop",
                parameters: { greeting: "hello" },
            } as unknown as EdhAmiRecipe,
        });
        const dialog = screen.getByRole("dialog");
        expect(dialog).toHaveTextContent("Edit recipe");
        expect(
            within(dialog).getByText("Bundled files from al2023-desktop: vamscli")
        ).toBeInTheDocument();

        const user = userEvent.setup();
        await user.click(screen.getByRole("button", { name: "Save recipe" }));
        await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
        const body = onSubmit.mock.calls[0][0];
        expect(body).not.toHaveProperty("assets");
        expect(body).not.toHaveProperty("duplicateOf");
        // The parameters have no field, so the body carries the recipe's own.
        expect(body).toMatchObject({
            recipeId: "mine",
            displayName: "Mine",
            stackName: "Mine",
            parameters: { greeting: "hello" },
            enabled: true,
        });
    });
});
