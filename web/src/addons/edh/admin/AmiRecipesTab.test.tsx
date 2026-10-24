/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AmiRecipesTab from "./AmiRecipesTab";
import * as amiRecipeQueries from "../api/amiRecipeQueries";
import type { EdhAmiRecipe } from "../types";

jest.mock("../api/amiRecipeQueries", () => ({
    useEdhAmiRecipes: jest.fn(),
    useEdhAmiBuilds: jest.fn(),
    useEdhAmiRecipeMutations: jest.fn(),
}));

// The recipe form edits an inline component document in Monaco, which does not render under jsdom.
jest.mock("../../../features/orchestration/components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value, onChange }: any) => (
        <textarea
            aria-label="component-yaml"
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
        />
    ),
}));

// The tab reports every mutation outcome through a toast alone, so the toast is observed here.
const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const query = (data: any) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const RECIPES = [
    {
        recipeId: "al2023-desktop",
        displayName: "AL2023 desktop",
        kind: "vdi",
        osFamily: "linux",
        baseOs: "amazonlinux2023",
        architecture: "x86_64",
        parentImage: "ssm:/aws/service/x",
        buildInstanceType: "m7i.large",
        rootSizeGb: 60,
        stackName: "VAMS AL2023 Desktop",
        profileId: 1,
        components: [{ arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/x.x.x" }],
        keepVersions: 3,
        source: "config",
        enabled: true,
        contentHash: "abc",
        latestVersion: 2,
        latestBuild: {
            recipeId: "al2023-desktop",
            version: 2,
            status: "BUILDING",
            contentHash: "abc",
        },
        createdAt: "",
        updatedAt: "",
    },
    {
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
        components: [{ name: "x", data: "schemaVersion: 1.0" }],
        keepVersions: 3,
        source: "admin",
        enabled: true,
        contentHash: "def",
        latestVersion: 1,
        latestBuild: {
            recipeId: "mine",
            version: 1,
            status: "AVAILABLE",
            contentHash: "def",
            edhStackName: "Mine v1",
            amiId: "ami-0fedcba9876543210",
        },
        createdAt: "",
        updatedAt: "",
    },
] as unknown as EdhAmiRecipe[];

const mutation = (result: any = {}) => ({
    mutateAsync: jest.fn().mockResolvedValue(result),
    isPending: false,
});

let mutations: Record<string, ReturnType<typeof mutation>>;

const recipes = (data: any) =>
    (amiRecipeQueries.useEdhAmiRecipes as jest.Mock).mockReturnValue(query(data));

const openActionsFor = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
    screen.getByLabelText(`Actions for ${name}`).focus();
    await user.keyboard("{Enter}");
    await screen.findByRole("menu");
};

const menuLabels = () => screen.getAllByRole("menuitem").map((item) => item.textContent);

beforeEach(() => {
    jest.clearAllMocks();
    mutations = {
        create: mutation(),
        update: mutation(),
        remove: mutation(),
        startBuild: mutation({ version: 3, outcome: "started" }),
        registerBuild: mutation(),
        retireBuild: mutation(),
    };
    (amiRecipeQueries.useEdhAmiRecipeMutations as jest.Mock).mockReturnValue(mutations);
    (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(query([]));
    recipes(RECIPES);
});

it("renders one row per recipe with source, status and stack columns", () => {
    render(<AmiRecipesTab can={() => true} />);

    expect(screen.getByText("AL2023 desktop")).toBeInTheDocument();
    expect(screen.getByText("al2023-desktop")).toBeInTheDocument();
    expect(screen.getByText("Config")).toBeInTheDocument();
    expect(screen.getByText("Admin")).toBeInTheDocument();
    expect(screen.getByText("Building")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
    expect(screen.getByText("VAMS AL2023 Desktop")).toBeInTheDocument();
    expect(screen.getByText("Mine v1")).toBeInTheDocument();
    expect(screen.getByText("2 recipes")).toBeInTheDocument();
});

it("offers Build now, Builds, Duplicate and, for an admin recipe, Edit and Delete", async () => {
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    expect(menuLabels()).toEqual(["Build now", "Builds", "View", "Duplicate"]);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

    await openActionsFor(user, "Mine");
    expect(menuLabels()).toEqual(["Build now", "Builds", "Edit", "Duplicate", "Delete"]);
});

it("hides Duplicate without the create route, for a shipped and an admin recipe alike", async () => {
    const user = userEvent.setup();
    render(
        <AmiRecipesTab
            can={(method, path) => !(method === "POST" && path === "/addon/edh/admin/ami-recipes")}
        />
    );

    await openActionsFor(user, "AL2023 desktop");
    expect(menuLabels()).toEqual(["Build now", "Builds", "View"]);

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

    await openActionsFor(user, "Mine");
    expect(menuLabels()).toEqual(["Build now", "Builds", "Edit", "Delete"]);
});

it("Build now calls the mutation and toasts", async () => {
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Build now" }));

    await waitFor(() =>
        expect(mutations.startBuild.mutateAsync).toHaveBeenCalledWith("al2023-desktop")
    );
    expect(mockToast.success).toHaveBeenCalledWith("Build started", {
        description: "AL2023 desktop v3",
    });
});

it("Build now behind a build of other content says the build is queued", async () => {
    mutations.startBuild.mutateAsync.mockResolvedValue({ version: 3, outcome: "queued" });
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Build now" }));

    await waitFor(() =>
        expect(mockToast.success).toHaveBeenCalledWith("Build queued", {
            description: "AL2023 desktop v3",
        })
    );
});

it("Build now on content already building names the build in progress", async () => {
    mutations.startBuild.mutateAsync.mockResolvedValue({ version: 2, outcome: "in-progress" });
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Build now" }));

    await waitFor(() =>
        expect(mockToast.info).toHaveBeenCalledWith("Build already in progress", {
            description: "AL2023 desktop v2",
        })
    );
    expect(mockToast.success).not.toHaveBeenCalled();
});

it("disables Build now on a recipe while its request is in flight, so a second one sends nothing", async () => {
    const user = userEvent.setup();
    let answer: (build: unknown) => void = () => undefined;
    mutations.startBuild.mutateAsync = jest.fn(
        () =>
            new Promise((resolve) => {
                answer = resolve;
            })
    );
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Build now" }));
    await openActionsFor(user, "AL2023 desktop");
    const buildNow = screen.getByRole("menuitem", { name: "Build now" });
    expect(buildNow).toHaveAttribute("aria-disabled", "true");
    expect(buildNow).toHaveAccessibleDescription("A build request for this recipe is in progress.");
    await user.click(buildNow);
    expect(mutations.startBuild.mutateAsync).toHaveBeenCalledTimes(1);
    await user.keyboard("{Escape}");

    // The guard holds for that recipe only.
    await openActionsFor(user, "Mine");
    expect(screen.getByRole("menuitem", { name: "Build now" })).not.toHaveAttribute(
        "aria-disabled",
        "true"
    );
    await user.keyboard("{Escape}");

    await act(async () => answer({ version: 3, outcome: "started" }));
    await openActionsFor(user, "AL2023 desktop");
    expect(screen.getByRole("menuitem", { name: "Build now" })).not.toHaveAttribute(
        "aria-disabled",
        "true"
    );
});

it("hides New recipe when POST is not allowed", () => {
    const { unmount } = render(<AmiRecipesTab can={() => true} />);
    expect(screen.getByRole("button", { name: "New recipe" })).toBeInTheDocument();
    unmount();

    render(
        <AmiRecipesTab
            can={(method, path) => !(method === "POST" && path === "/addon/edh/admin/ami-recipes")}
        />
    );
    expect(screen.queryByRole("button", { name: "New recipe" })).not.toBeInTheDocument();
});

it("shows the newer-than-latest-build marker when the hash differs", () => {
    recipes([
        {
            ...RECIPES[1],
            contentHash: "new",
            latestBuild: { ...RECIPES[1].latestBuild, contentHash: "old" },
        },
        RECIPES[0],
    ]);
    render(<AmiRecipesTab can={() => true} />);

    // Only the recipe whose stored hash moved on carries the marker.
    expect(screen.getAllByText(/newer than latest build/i)).toHaveLength(1);
    const row = screen.getByText("Mine").closest("tr") as HTMLElement;
    expect(within(row).getByText(/newer than latest build/i)).toBeInTheDocument();
});

it("deletes an admin recipe through the confirmation", async () => {
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    // The confirmation says what a delete leaves behind.
    expect(dialog).toHaveTextContent(/AMIs/);
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(mutations.remove.mutateAsync).toHaveBeenCalledWith("mine"));
    expect(mockToast.success).toHaveBeenCalledWith("Recipe deleted", { description: "Mine" });
});

it("lists the builds of a recipe and retries a failed registration", async () => {
    const user = userEvent.setup();
    (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(
        query([
            {
                recipeId: "mine",
                version: 1,
                status: "AVAILABLE",
                amiId: "ami-0fedcba9876543210",
                edhStackName: "Mine v1",
            },
            {
                recipeId: "mine",
                version: 2,
                status: "REGISTER_FAILED",
                amiId: "ami-0aaaaaaaaaaaaaaaa",
                error: "EDH refused the stack",
            },
        ])
    );
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Builds" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Builds of Mine");
    expect(within(dialog).getByText("EDH refused the stack")).toBeInTheDocument();
    // Newest first: the failed v2 leads the table.
    const versions = within(dialog)
        .getAllByRole("row")
        .slice(1)
        .map((row) => row.querySelectorAll("td")[0].textContent);
    expect(versions).toEqual(["2", "1"]);

    await user.click(within(dialog).getByRole("button", { name: "Retry registration" }));
    await waitFor(() =>
        expect(mutations.registerBuild.mutateAsync).toHaveBeenCalledWith({
            recipeId: "mine",
            version: 2,
        })
    );
    expect(mockToast.success).toHaveBeenCalledWith("Registration retried", {
        description: "Mine v2",
    });
});

it("shows the retirement and pruning warnings of an available build", async () => {
    const user = userEvent.setup();
    (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(
        query([
            {
                recipeId: "mine",
                version: 3,
                status: "AVAILABLE",
                amiId: "ami-0bbbbbbbbbbbbbbbb",
                edhStackName: "Mine v3",
                error: "registered; retirement or pruning of earlier versions failed (EdhError)",
                retireWarning: "v2 not retired (EdhError)",
                pruneWarning: "v1 snapshot not deleted (ClientError)",
            },
        ])
    );
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Builds" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("v2 not retired (EdhError)")).toBeInTheDocument();
    expect(within(dialog).getByText("v1 snapshot not deleted (ClientError)")).toBeInTheDocument();
});

// A request that stays in flight for the rest of the test.
const neverAnswers = () => new Promise<never>(() => undefined);

// Both clicks land before a re-render, as a double-click's do.
const doubleClick = (element: HTMLElement) => {
    fireEvent.click(element);
    fireEvent.click(element);
};

it("sends one delete for a double-click on the confirmation", async () => {
    mutations.remove.mutateAsync = jest.fn(neverAnswers);
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    doubleClick(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(mutations.remove.mutateAsync).toHaveBeenCalledWith("mine"));
    expect(mutations.remove.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
});

it("sends one update for a double-click on Save recipe", async () => {
    mutations.update.mutateAsync = jest.fn(neverAnswers);
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    doubleClick(within(dialog).getByRole("button", { name: "Save recipe" }));
    await waitFor(() => expect(mutations.update.mutateAsync).toHaveBeenCalled());
    expect(mutations.update.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
});

it("sends one retry and one retire per build for a double-click", async () => {
    mutations.registerBuild.mutateAsync = jest.fn(neverAnswers);
    mutations.retireBuild.mutateAsync = jest.fn(neverAnswers);
    (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(
        query([
            { recipeId: "mine", version: 1, status: "AVAILABLE", edhStackName: "Mine v1" },
            { recipeId: "mine", version: 2, status: "REGISTER_FAILED" },
        ])
    );
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Builds" }));
    const panel = await screen.findByRole("dialog");
    doubleClick(within(panel).getByRole("button", { name: "Retry registration" }));
    await waitFor(() =>
        expect(mutations.registerBuild.mutateAsync).toHaveBeenCalledWith({
            recipeId: "mine",
            version: 2,
        })
    );
    expect(mutations.registerBuild.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);

    await user.click(within(panel).getByRole("button", { name: "Retire" }));
    const confirm = await screen.findByRole("dialog", { name: "Retire Mine v1" });
    doubleClick(within(confirm).getByRole("button", { name: "Retire" }));
    await waitFor(() =>
        expect(mutations.retireBuild.mutateAsync).toHaveBeenCalledWith({
            recipeId: "mine",
            version: 1,
        })
    );
    expect(mutations.retireBuild.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
});

const VAMSCLI_ASSETS = [{ id: "vamscli", repoPath: "tools/VamsCLI" }];

it("duplicates a shipped recipe through the prefilled form, posting duplicateOf and no assets", async () => {
    recipes([
        {
            ...RECIPES[0],
            assets: VAMSCLI_ASSETS,
            assetPrefix: "addon/edh/amiRecipes/abc/al2023-desktop/assets",
            parameters: { greeting: "hello" },
        },
        RECIPES[1],
    ]);
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Duplicate recipe");
    expect(within(dialog).getByLabelText("Recipe id")).toHaveValue("");
    expect(within(dialog).getByLabelText("Display name")).toHaveValue("AL2023 desktop (copy)");
    expect(within(dialog).getByLabelText("Stack name")).toHaveValue("VAMS AL2023 Desktop Copy");
    expect(
        within(dialog).getByText("Bundled files from al2023-desktop: vamscli")
    ).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Recipe id"), "al2023-copy");
    await user.click(within(dialog).getByRole("button", { name: "Create recipe" }));

    await waitFor(() => expect(mutations.create.mutateAsync).toHaveBeenCalledTimes(1));
    const body = mutations.create.mutateAsync.mock.calls[0][0];
    expect(body).toEqual({
        recipeId: "al2023-copy",
        displayName: "AL2023 desktop (copy)",
        kind: "vdi",
        osFamily: "linux",
        baseOs: "amazonlinux2023",
        architecture: "x86_64",
        parentImage: "ssm:/aws/service/x",
        buildInstanceType: "m7i.large",
        rootSizeGb: 60,
        stackName: "VAMS AL2023 Desktop Copy",
        profileId: 1,
        components: [{ arn: "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/x.x.x" }],
        keepVersions: 3,
        recipeRevision: 1,
        parameters: { greeting: "hello" },
        duplicateOf: "al2023-desktop",
    });
    expect(body).not.toHaveProperty("assets");
    expect(mutations.update.mutateAsync).not.toHaveBeenCalled();
    expect(mockToast.success).toHaveBeenCalledWith("Recipe duplicated", {
        description: "AL2023 desktop (copy)",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("keeps the duplicate form open and toasts the service's refusal", async () => {
    mutations.create.mutateAsync.mockRejectedValue(
        new Error("stackName is already used by recipe 'other'")
    );
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Recipe id"), "mine-copy");
    await user.click(within(dialog).getByRole("button", { name: "Create recipe" }));

    await waitFor(() =>
        expect(mockToast.error).toHaveBeenCalledWith("Duplicate failed", {
            description: "Mine (copy): stackName is already used by recipe 'other'",
        })
    );
    expect(mutations.create.mutateAsync.mock.calls[0][0]).toMatchObject({
        recipeId: "mine-copy",
        stackName: "Mine Copy",
        duplicateOf: "mine",
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(mockToast.success).not.toHaveBeenCalled();
});

it("edits a duplicated recipe without sending the files it inherited", async () => {
    recipes([
        RECIPES[0],
        {
            ...RECIPES[1],
            assets: VAMSCLI_ASSETS,
            assetPrefix: "addon/edh/amiRecipes/abc/al2023-desktop/assets",
            duplicatedFrom: "al2023-desktop",
        },
    ]);
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    expect(
        within(dialog).getByText("Bundled files from al2023-desktop: vamscli")
    ).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save recipe" }));

    await waitFor(() => expect(mutations.update.mutateAsync).toHaveBeenCalledTimes(1));
    const { recipeId, body } = mutations.update.mutateAsync.mock.calls[0][0];
    expect(recipeId).toBe("mine");
    expect(body).not.toHaveProperty("assets");
    expect(body).not.toHaveProperty("duplicateOf");
    expect(mockToast.success).toHaveBeenCalledWith("Recipe updated", { description: "Mine" });
});
