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

// Both Build now paths ask first; the build starts from the confirmation's Start build.
const confirmBuild = async (user: ReturnType<typeof userEvent.setup>) => {
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Start build" }));
};

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
    await confirmBuild(user);

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
    await confirmBuild(user);

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
    await confirmBuild(user);

    await waitFor(() =>
        expect(mockToast.info).toHaveBeenCalledWith("Build already in progress", {
            description: "AL2023 desktop v2",
        })
    );
    expect(mockToast.success).not.toHaveBeenCalled();
});

/**
 * Holds `mutation`'s next request in flight, reading as pending, until the returned function
 * answers it with `value`.
 */
const holdRequest = (held: ReturnType<typeof mutation>, value: unknown = {}) => {
    let answer = () => undefined as void;
    held.mutateAsync = jest.fn(() => {
        held.isPending = true;
        return new Promise((resolve) => {
            answer = () => {
                held.isPending = false;
                resolve(value);
            };
        });
    });
    return () => act(async () => answer());
};

/**
 * Presses Enter on the dialog's `label` button and checks the busy window: the button reads
 * `busyLabel` and keeps focus (aria-disabled, not disabled), and a second Enter, Escape and Cancel
 * leave the request and the dialog as they are.
 */
const pressAndHold = async (
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement,
    label: string,
    busyLabel: string
) => {
    within(dialog).getByRole("button", { name: label }).focus();
    await user.keyboard("{Enter}");
    const busy = within(dialog).getByRole("button", { name: busyLabel });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toBe(dialog);
};

it("keeps the Build now confirmation open while its request is in flight, so a second build sends nothing", async () => {
    const user = userEvent.setup({ delay: null });
    const answer = holdRequest(mutations.startBuild, { version: 3, outcome: "started" });
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "AL2023 desktop");
    await user.click(screen.getByRole("menuitem", { name: "Build now" }));
    const dialog = await screen.findByRole("dialog");
    await pressAndHold(user, dialog, "Start build", "Starting build...");
    expect(mutations.startBuild.mutateAsync).toHaveBeenCalledTimes(1);

    await answer();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // The menu's item closed with the menu, so focus goes to the row's actions.
    await waitFor(() =>
        expect(screen.getByRole("button", { name: "Actions for AL2023 desktop" })).toHaveFocus()
    );
    await openActionsFor(user, "AL2023 desktop");
    expect(screen.getByRole("menuitem", { name: "Build now" })).not.toHaveAttribute(
        "aria-disabled",
        "true"
    );
});

it("keeps the Delete confirmation open while the delete is in flight, then focus goes to the search box", async () => {
    const user = userEvent.setup({ delay: null });
    const answer = holdRequest(mutations.remove);
    render(<AmiRecipesTab can={() => true} />);

    await openActionsFor(user, "Mine");
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    await pressAndHold(user, await screen.findByRole("dialog"), "Delete", "Deleting...");
    expect(mutations.remove.mutateAsync).toHaveBeenCalledTimes(1);

    await answer();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
        expect(screen.getByRole("textbox", { name: "Search AMI recipes" })).toHaveFocus()
    );
});

it.each<[string, string, string, string]>([
    ["Edit", "update", "Save recipe", "Saving..."],
    ["Duplicate", "create", "Create recipe", "Creating..."],
])(
    "keeps the recipe form open after the menu's %s while its save is in flight",
    async (item, mutationName, label, busyLabel) => {
        const user = userEvent.setup({ delay: null });
        const answer = holdRequest(mutations[mutationName]);
        render(<AmiRecipesTab can={() => true} />);

        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: item }));
        const dialog = await screen.findByRole("dialog");
        if (item === "Duplicate") {
            await user.click(within(dialog).getByLabelText("Recipe id"));
            await user.paste("mine-copy");
        }
        await pressAndHold(user, dialog, label, busyLabel);
        // Enter in a field submits nothing more either.
        await user.click(within(dialog).getByLabelText("Display name"));
        await user.keyboard("{Enter}");
        fireEvent.submit(dialog.querySelector("form") as HTMLFormElement);
        expect(mutations[mutationName].mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() =>
            expect(
                item === "Edit"
                    ? screen.getByRole("button", { name: "Actions for Mine" })
                    : screen.getByRole("textbox", { name: "Search AMI recipes" })
            ).toHaveFocus()
        );
    }
);

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

describe("Build now beside the newer-than-latest-build marker", () => {
    const BUILD_ROUTE = "/addon/edh/admin/ami-recipes/{recipeId}/builds";
    const NEWER = {
        ...RECIPES[1],
        contentHash: "new",
        latestBuild: { ...RECIPES[1].latestBuild, contentHash: "old" },
    };
    // Hidden ones included: an open dialog hides the table from the accessibility tree, not the DOM.
    const button = () => screen.queryByRole("button", { name: "Build now: Mine", hidden: true });

    it("starts a build of the recipe through the row's own build action", async () => {
        recipes([NEWER, RECIPES[0]]);
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);
        const marker = screen.getByText("Recipe is newer than latest build");
        expect(marker.parentElement).toContainElement(button());
        // Only the recipe that moved on offers it.
        expect(screen.getAllByRole("button", { name: /^Build now/ })).toHaveLength(1);

        await user.click(button() as HTMLElement);
        await confirmBuild(user);
        await waitFor(() => expect(mutations.startBuild.mutateAsync).toHaveBeenCalledWith("mine"));
        expect(mockToast.success).toHaveBeenCalledWith("Build started", {
            description: "Mine v3",
        });
    });

    it("leaves the button out for a role that may not start a build", () => {
        recipes([NEWER]);
        render(
            <AmiRecipesTab can={(method, path) => !(method === "POST" && path === BUILD_ROUTE)} />
        );
        expect(screen.getByText("Recipe is newer than latest build")).toBeInTheDocument();
        expect(button()).toBeNull();
    });

    it.each(["QUEUED", "BUILDING", "REGISTERING"])(
        "leaves the button out while the latest build is %s",
        (status) => {
            recipes([{ ...NEWER, latestBuild: { ...NEWER.latestBuild, status } }]);
            render(<AmiRecipesTab can={() => true} />);
            expect(screen.getByText("Recipe is newer than latest build")).toBeInTheDocument();
            expect(button()).toBeNull();
        }
    );

    it("leaves the button out of a disabled recipe, which cannot be built", () => {
        recipes([{ ...NEWER, enabled: false }]);
        render(<AmiRecipesTab can={() => true} />);
        expect(button()).toBeNull();
    });

    it("leaves the button out while its request is in flight, so a second click sends nothing", async () => {
        recipes([NEWER]);
        let answer: (build: unknown) => void = () => undefined;
        mutations.startBuild.mutateAsync = jest.fn(
            () =>
                new Promise((resolve) => {
                    answer = resolve;
                })
        );
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);
        await user.click(button() as HTMLElement);
        await confirmBuild(user);
        expect(button()).toBeNull();
        expect(mutations.startBuild.mutateAsync).toHaveBeenCalledTimes(1);
        await act(async () => answer({ version: 3, outcome: "started" }));
        expect(button()).not.toBeNull();
    });
});

describe("Build now asks for confirmation", () => {
    const NEWER = {
        ...RECIPES[1],
        contentHash: "new",
        latestBuild: { ...RECIPES[1].latestBuild, contentHash: "old" },
    };

    it("opens one dialog from the row menu that names the recipe and what a build does, and starts nothing yet", async () => {
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);

        await openActionsFor(user, "AL2023 desktop");
        await user.click(screen.getByRole("menuitem", { name: "Build now" }));

        const dialog = await screen.findByRole("dialog", { name: "Build AL2023 desktop" });
        expect(screen.getAllByRole("dialog")).toHaveLength(1);
        expect(dialog).toHaveTextContent("Start a build of the recipe AL2023 desktop?");
        expect(dialog).toHaveTextContent("Amazon EC2 build instance (m7i.large)");
        expect(dialog).toHaveTextContent("billed while the build runs");
        expect(dialog).toHaveTextContent(
            "registered in the EDH catalog as the recipe's next version and retires the recipe's other available builds"
        );
        expect(within(dialog).getByRole("button", { name: "Start build" })).toBeEnabled();
        expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeEnabled();
        expect(mutations.startBuild.mutateAsync).not.toHaveBeenCalled();
    });

    it("opens the same dialog from the button beside the newer-than-latest-build marker", async () => {
        recipes([NEWER]);
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);

        await user.click(screen.getByRole("button", { name: "Build now: Mine" }));

        const dialog = await screen.findByRole("dialog", { name: "Build Mine" });
        expect(dialog).toHaveTextContent("Amazon EC2 build instance (c7g.large)");
        expect(within(dialog).getByRole("button", { name: "Start build" })).toBeInTheDocument();
        expect(mutations.startBuild.mutateAsync).not.toHaveBeenCalled();
    });

    it("starts no build when the dialog is cancelled, from either path", async () => {
        recipes([NEWER, RECIPES[0]]);
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);

        await openActionsFor(user, "AL2023 desktop");
        await user.click(screen.getByRole("menuitem", { name: "Build now" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        await user.click(screen.getByRole("button", { name: "Build now: Mine" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        expect(mutations.startBuild.mutateAsync).not.toHaveBeenCalled();
    });

    it("starts exactly one build from Start build, a double click included, and closes once answered", async () => {
        let answer: (build: unknown) => void = () => undefined;
        mutations.startBuild.mutateAsync = jest.fn(
            () =>
                new Promise((resolve) => {
                    answer = resolve;
                })
        );
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);

        await openActionsFor(user, "AL2023 desktop");
        await user.click(screen.getByRole("menuitem", { name: "Build now" }));
        const start = within(await screen.findByRole("dialog")).getByRole("button", {
            name: "Start build",
        });
        // Both clicks land before a re-render, as a double-click's do.
        fireEvent.click(start);
        fireEvent.click(start);

        expect(mutations.startBuild.mutateAsync).toHaveBeenCalledTimes(1);
        expect(mutations.startBuild.mutateAsync).toHaveBeenCalledWith("al2023-desktop");
        // While the request is in flight the dialog stays open, Start build busy but focusable.
        expect(start).toHaveAccessibleName("Starting build...");
        expect(start).toHaveAttribute("aria-disabled", "true");
        expect(start).not.toBeDisabled();
        expect(screen.getByRole("dialog")).toBeInTheDocument();

        await act(async () => answer({ version: 3, outcome: "started" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(mutations.startBuild.mutateAsync).toHaveBeenCalledTimes(1);
        expect(mockToast.success).toHaveBeenCalledWith("Build started", {
            description: "AL2023 desktop v3",
        });
    });
});

it("draws the row actions button as an icon and says in the search box what it searches", () => {
    render(<AmiRecipesTab can={() => true} />);

    const button = screen.getByRole("button", { name: "Actions for Mine" });
    // An SVG icon, not a text glyph that renders blank without a font carrying it.
    expect(button.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    expect(button.textContent).toBe("");
    expect(screen.getByRole("textbox", { name: "Search AMI recipes" })).toHaveAttribute(
        "placeholder",
        "Search recipes"
    );
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

describe("the Build now confirmation's words", () => {
    it("names the one build instance type and the default build timeout", async () => {
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "AL2023 desktop");
        await user.click(screen.getByRole("menuitem", { name: "Build now" }));

        const dialog = await screen.findByRole("dialog", { name: "Build AL2023 desktop" });
        expect(dialog).toHaveTextContent(
            "runs it on an Amazon EC2 build instance (m7i.large), which is billed while the build runs"
        );
        expect(dialog).toHaveTextContent(
            "A build still running after the deployment's build timeout (3 hours by default) fails."
        );
    });

    it("says a build runs on one of a recipe's several build instance types", async () => {
        recipes([{ ...RECIPES[0], buildInstanceType: ["m7i.large", "c7i.large"] }]);
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "AL2023 desktop");
        await user.click(screen.getByRole("menuitem", { name: "Build now" }));

        expect(await screen.findByRole("dialog")).toHaveTextContent(
            "runs it on an Amazon EC2 build instance of one of these types (m7i.large, c7i.large), which is billed"
        );
    });
});

describe("focus when a recipe confirmation closes", () => {
    const NEWER = {
        ...RECIPES[1],
        contentHash: "new",
        latestBuild: { ...RECIPES[1].latestBuild, contentHash: "old" },
    };
    // Hidden ones included: the table is hidden from the accessibility tree while a dialog is open.
    const control = (name: string) => screen.getByRole("button", { name, hidden: true });

    const dismiss = async (user: ReturnType<typeof userEvent.setup>, how: string) => {
        const dialog = await screen.findByRole("dialog");
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        }
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    };

    it.each(["Escape", "Cancel"])(
        "returns to the Build now button beside the marker after %s",
        async (how) => {
            recipes([NEWER]);
            const user = userEvent.setup();
            render(<AmiRecipesTab can={() => true} />);
            control("Build now: Mine").focus();
            await user.keyboard("{Enter}");
            await dismiss(user, how);
            await waitFor(() => expect(control("Build now: Mine")).toHaveFocus());
        }
    );

    it.each(["Escape", "Cancel"])(
        "returns to the row's actions after the menu's Build now and %s",
        async (how) => {
            const user = userEvent.setup();
            render(<AmiRecipesTab can={() => true} />);
            await openActionsFor(user, "AL2023 desktop");
            await user.click(screen.getByRole("menuitem", { name: "Build now" }));
            await dismiss(user, how);
            await waitFor(() => expect(control("Actions for AL2023 desktop")).toHaveFocus());
        }
    );

    it.each(["Escape", "Cancel"])(
        "returns to the row's actions after the menu's Delete and %s",
        async (how) => {
            const user = userEvent.setup();
            render(<AmiRecipesTab can={() => true} />);
            await openActionsFor(user, "Mine");
            await user.click(screen.getByRole("menuitem", { name: "Delete" }));
            await dismiss(user, how);
            await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
        }
    );

    it("goes to the row's actions once a build from the marker's button is answered", async () => {
        recipes([NEWER]);
        let answer: (build: unknown) => void = () => undefined;
        mutations.startBuild.mutateAsync = jest.fn(
            () =>
                new Promise((resolve) => {
                    answer = resolve;
                })
        );
        const user = userEvent.setup();
        render(<AmiRecipesTab can={() => true} />);
        control("Build now: Mine").focus();
        await user.keyboard("{Enter}");
        await confirmBuild(user);
        // The button leaves the row while the request is in flight.
        expect(screen.queryByRole("button", { name: "Build now: Mine", hidden: true })).toBeNull();

        await act(async () => answer({ version: 2, outcome: "started" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
    });

    it("goes to the search box once a delete removes the recipe, and to the row's actions when it fails", async () => {
        const user = userEvent.setup();
        const { unmount } = render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() =>
            expect(screen.getByRole("textbox", { name: "Search AMI recipes" })).toHaveFocus()
        );
        unmount();

        mutations.remove.mutateAsync = jest.fn().mockRejectedValue(new Error("refused"));
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
    });

    it("returns to the row's actions when Delete opens again on a recipe the list still shows", async () => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        // The list still reads the recipe once the delete is answered.
        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await dismiss(user, "Cancel");
        await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
    });
});

it("says when the search matches no recipe, and shows the rows again once it is cleared", async () => {
    const user = userEvent.setup();
    render(<AmiRecipesTab can={() => true} />);
    const search = screen.getByRole("textbox", { name: "Search AMI recipes" });

    await user.type(search, "  zz-no-such  ");
    expect(screen.getByRole("status")).toHaveTextContent('No recipes match "zz-no-such".');
    // The table keeps its header row, so a sort survives the search.
    expect(screen.getByRole("table", { name: "EDH AMI recipes" })).toBeInTheDocument();
    expect(screen.queryByText("Mine")).toBeNull();
    expect(screen.getByText("0 of 2 recipes")).toBeInTheDocument();

    await user.clear(search);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("Mine")).toBeInTheDocument();
    expect(screen.getByText("AL2023 desktop")).toBeInTheDocument();
    expect(screen.getByText("2 recipes")).toBeInTheDocument();
});

it("counts the recipes a search shows of every recipe", async () => {
    const user = userEvent.setup({ delay: null });
    render(<AmiRecipesTab can={() => true} />);
    expect(screen.getByText("2 recipes")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "Search AMI recipes" }), "AL2023");
    expect(screen.getByText("1 of 2 recipes")).toBeInTheDocument();
    expect(screen.queryByText("2 recipes")).toBeNull();
});

describe("focus when the recipe form or the builds panel closes", () => {
    // Hidden ones included: the table is hidden from the accessibility tree while a dialog is open.
    const control = (name: string) => screen.getByRole("button", { name, hidden: true });
    const searchBox = () =>
        screen.getByRole("textbox", { name: "Search AMI recipes", hidden: true });

    const dismiss = async (user: ReturnType<typeof userEvent.setup>, how: string) => {
        const dialog = await screen.findByRole("dialog");
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: how }));
        }
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    };

    it.each(["Escape", "Cancel"])("returns to New recipe after %s", async (how) => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        control("New recipe").focus();
        await user.keyboard("{Enter}");
        await dismiss(user, how);
        await waitFor(() => expect(control("New recipe")).toHaveFocus());
    });

    it("goes to the search box once New recipe creates a recipe", async () => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        control("New recipe").focus();
        await user.keyboard("{Enter}");
        const dialog = await screen.findByRole("dialog", { name: "New recipe" });
        // The form reads each field's final value, so the values are pasted.
        const enter = async (label: string, value: string) => {
            await user.click(within(dialog).getByLabelText(label));
            await user.paste(value);
        };
        await enter("Recipe id", "mine-2");
        await enter("Display name", "Mine 2");
        await user.selectOptions(within(dialog).getByLabelText("Kind"), "simulation");
        await user.selectOptions(within(dialog).getByLabelText("Architecture"), "arm64");
        await enter("Parent image", "ami-0123456789abcdef0");
        await enter("Build instance type", "c7g.large");
        await enter("Stack name", "Mine 2");
        await enter("Profile id", "2");
        await enter(
            "Component 1 ARN",
            "arn:aws:imagebuilder:us-east-1:aws:component/update-linux/1.0.0"
        );
        await user.click(within(dialog).getByRole("button", { name: "Create recipe" }));

        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(mutations.create.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it.each([
        ["Edit", "Mine", "Escape"],
        ["Edit", "Mine", "Cancel"],
        ["View", "AL2023 desktop", "Escape"],
        ["View", "AL2023 desktop", "Close"],
        ["Duplicate", "Mine", "Escape"],
        ["Duplicate", "Mine", "Cancel"],
    ])("returns to the row's actions after the menu's %s on %s and %s", async (item, name, how) => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, name);
        await user.click(screen.getByRole("menuitem", { name: item }));
        await dismiss(user, how);
        await waitFor(() => expect(control(`Actions for ${name}`)).toHaveFocus());
    });

    it("returns to the row's actions once an edit is saved, the recipe keeping its row", async () => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Edit" }));
        await user.click(
            within(await screen.findByRole("dialog")).getByRole("button", { name: "Save recipe" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(mutations.update.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
    });

    it("goes to the search box once a duplicate is created", async () => {
        const user = userEvent.setup({ delay: null });
        render(<AmiRecipesTab can={() => true} />);
        await openActionsFor(user, "Mine");
        await user.click(screen.getByRole("menuitem", { name: "Duplicate" }));
        const dialog = await screen.findByRole("dialog");
        await user.click(within(dialog).getByLabelText("Recipe id"));
        await user.paste("mine-copy");
        await user.click(within(dialog).getByRole("button", { name: "Create recipe" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(mutations.create.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(searchBox()).toHaveFocus());
    });

    it.each(["Escape", "Close"])(
        "returns to the row's actions after the builds panel's %s",
        async (how) => {
            const user = userEvent.setup({ delay: null });
            render(<AmiRecipesTab can={() => true} />);
            await openActionsFor(user, "Mine");
            await user.click(screen.getByRole("menuitem", { name: "Builds" }));
            expect(await screen.findByRole("dialog")).toHaveAccessibleName("Builds of Mine");
            await dismiss(user, how);
            await waitFor(() => expect(control("Actions for Mine")).toHaveFocus());
        }
    );
});
