/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EdhAdminPage from "./EdhAdminPage";
import * as adminQueries from "../api/adminQueries";
import * as amiRecipeQueries from "../api/amiRecipeQueries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import { describeLastError } from "../components/WorkstationStatusBadge";
import { appCache } from "../../../services/appCache";
import { EdhRequestError, OUTCOME_UNKNOWN_MESSAGE } from "../api/outcome";

jest.mock("../api/adminQueries", () => ({
    useEdhAdminImages: jest.fn(),
    useEdhAdminInstances: jest.fn(),
    useEdhStaleInstances: jest.fn(),
    useEdhCapacity: jest.fn(),
    useEdhHealth: jest.fn(),
    useEdhAdminMutations: jest.fn(),
    useEdhAdminSettings: jest.fn(),
    useUpdateEdhAdminSettings: jest.fn(),
}));

jest.mock("../api/amiRecipeQueries", () => ({
    useEdhAmiRecipes: jest.fn(),
    useEdhAmiBuilds: jest.fn(),
    useEdhAmiRecipeMutations: jest.fn(),
}));

// The recipe form edits an inline component document in Monaco, which does not render under jsdom.
jest.mock("../../../features/orchestration/components/ConfigEditor", () => ({
    __esModule: true,
    default: ({ value }: any) => <textarea aria-label="component-yaml" value={value} readOnly />,
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: {
        getItem: jest.fn(() => ({ featuresEnabled: ["EDH_WORKSTATIONS"] })),
    },
}));

// The admin tabs report a refused mutation through a toast alone, so the toast is observed here.
const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

// The bodies the add-on's handlers answer with, as the service layer rejects with them.
const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const UNAVAILABLE = "EDH is unavailable. Try again later.";
const unknown502 = () =>
    new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true });
const gateway504 = () =>
    new EdhRequestError("Endpoint request timed out", 504, {
        message: "Endpoint request timed out",
    });

const query = (data: any) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const IMAGES = [
    {
        kind: "vdi",
        imageId: "7",
        stackName: "linux-cad",
        amiId: "ami-0123456789abcdef0",
        baseOs: "amazonlinux2023",
        osFamily: "linux",
        architecture: "x86_64",
        rootSize: 100,
        profileId: 1,
        description: "Linux CAD",
    },
    {
        kind: "simulation",
        imageId: "3",
        stackName: "sim-base",
        amiId: "ami-0aaaaaaaaaaaaaaaa",
        baseOs: "",
        osFamily: "linux",
        architecture: null,
        rootSize: 50,
        profileId: 4,
        description: "",
    },
];

const INSTANCES = [
    {
        sessionId: "s-1",
        edhState: "running",
        owner: "vowner1",
        name: "cad-1",
        createdAt: "2026-09-08T10:00:00Z",
        updatedAt: "2026-09-08T10:05:00Z",
        lastError: null,
        userId: "alice",
        bootstrapId: "b-1",
        state: "ACTIVE",
        imageId: "7",
        osFamily: "linux",
        instanceType: "g4dn.xlarge",
    },
];

// One profile as EDH 26.8 reports it: the instance types are a JSON string per architecture.
const CAPACITY = {
    profiles: [
        {
            id: 1,
            profile_name: "default",
            is_active: true,
            pattern_allowed_instance_types: "m5.large,m5.xlarge,m5.2xlarge",
            allowed_instance_types: '{"x86_64": ["m5.large", "m5.xlarge"], "arm64": []}',
            allowed_subnet_ids: "subnet-00000abcdefabcdef,subnet-00000bcdefabcdefa",
            max_root_size: 500,
            software_stack_ids: [1, 2],
        },
    ],
    totalSessions: 3,
    byState: { running: 2, stopped: 1 },
    distinctOwners: 2,
};

// The stale view's two arms: a stopped desktop EDH still lists, and a row EDH no longer lists.
const STALE = [
    {
        ...INSTANCES[0],
        sessionId: "s-2",
        name: "cad-2",
        edhState: "stopped",
        state: "STOPPED",
        stale: true,
        staleReason: "stopped",
    },
    {
        ...INSTANCES[0],
        sessionId: "s-3",
        name: "cad-3",
        edhState: null,
        state: "ORPHANED",
        lastError: "QUOTA_EXCEEDED",
        stale: true,
        staleReason: "missingInEdh",
    },
];

const AMI_RECIPES = [
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
        source: "config",
        enabled: true,
        contentHash: "abc",
        latestVersion: 1,
        latestBuild: {
            recipeId: "al2023-desktop",
            version: 1,
            status: "AVAILABLE",
            contentHash: "abc",
        },
        createdAt: "",
        updatedAt: "",
    },
];

let mutations: Record<string, { mutateAsync: jest.Mock; isPending: boolean }>;

// A request that stays in flight for the rest of the test.
const neverAnswers = () => new Promise<never>(() => undefined);

// Both clicks land before a re-render, as a double-click's do.
const doubleClick = (element: HTMLElement) => {
    fireEvent.click(element);
    fireEvent.click(element);
};

const allow = (predicate: (method: string, path: string) => boolean = () => true) => {
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
        loading: false,
        can: predicate,
    });
};

// The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
const openActionsFor = async (user: ReturnType<typeof userEvent.setup>, name: string) => {
    screen.getByLabelText(`Actions for ${name}`).focus();
    await user.keyboard("{Enter}");
    await screen.findByRole("menu");
};

// The page opens on Sessions, its first tab; the image tests start by choosing Images.
const openImagesTab = (user: ReturnType<typeof userEvent.setup>) =>
    user.click(screen.getByRole("tab", { name: "Images" }));

describe("EdhAdminPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mutations = {
            registerVdi: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            registerSimulation: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            deleteImage: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            stopInstance: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            deactivateInstance: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        };
        (adminQueries.useEdhAdminMutations as jest.Mock).mockReturnValue(mutations);
        (adminQueries.useEdhAdminImages as jest.Mock).mockReturnValue(query(IMAGES));
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(query(INSTANCES));
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query([]));
        (adminQueries.useEdhCapacity as jest.Mock).mockReturnValue(query(CAPACITY));
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue(
            query({ status: "ok", edhClusterId: "edh-dev", latencyMs: 42 })
        );
        (adminQueries.useEdhAdminSettings as jest.Mock).mockReturnValue(
            query({
                enabled: true,
                leaseHours: 8,
                forced: false,
                graceHours: 24,
                updatedAt: "2026-09-17T10:00:00Z",
                updatedBy: "admin",
            })
        );
        (adminQueries.useUpdateEdhAdminSettings as jest.Mock).mockReturnValue({
            mutateAsync: jest.fn().mockResolvedValue({}),
            isPending: false,
        });
        (amiRecipeQueries.useEdhAmiRecipes as jest.Mock).mockReturnValue(query(AMI_RECIPES));
        (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(query([]));
        (amiRecipeQueries.useEdhAmiRecipeMutations as jest.Mock).mockReturnValue({
            create: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            update: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            remove: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            startBuild: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            registerBuild: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
            retireBuild: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
        });
        (appCache.getItem as jest.Mock).mockReturnValue({ featuresEnabled: ["EDH_WORKSTATIONS"] });
    });

    it("renders the not-enabled panel and no tabs when the flag is absent", () => {
        (appCache.getItem as jest.Mock).mockReturnValue({ featuresEnabled: [] });
        allow();
        render(<EdhAdminPage />);
        const panel = screen.getByRole("status");
        expect(panel).toHaveTextContent("EDH Workstations is not enabled in this deployment.");
        expect(panel).toHaveTextContent("Ask a VAMS administrator to enable the add-on.");
        expect(panel).toHaveClass("bg-surface-container");
        expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    });

    it("colours the Kind chip per kind, names the OS by its base OS, and searches the images from the toolbar", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        expect(screen.getByText("VDI")).toHaveClass("bg-blue-100");
        expect(screen.getByText("Simulation")).toHaveClass("bg-purple-100");
        expect(screen.getByText("Linux (Amazon Linux 2023)")).toBeInTheDocument();
        expect(screen.getByText("Linux")).toBeInTheDocument();
        expect(screen.queryByText("linux")).not.toBeInTheDocument();
        expect(screen.queryByText("linux (amazonlinux2023)")).not.toBeInTheDocument();

        // One search box, on the toolbar row next to Refresh — not the table's own.
        const search = screen.getByRole("textbox", { name: "Search images" });
        expect(screen.queryByRole("textbox", { name: "Search" })).not.toBeInTheDocument();
        expect(
            search.compareDocumentPosition(screen.getByRole("table")) &
                Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        await user.type(search, "sim");
        expect(screen.getByText("sim-base")).toBeInTheDocument();
        expect(screen.queryByText("linux-cad")).not.toBeInTheDocument();
        // A search that matches nothing keeps the table, not the empty-catalog copy.
        await user.type(search, "xyz");
        expect(screen.getByRole("table")).toBeInTheDocument();
        expect(screen.queryByText("No images are registered.")).not.toBeInTheDocument();
        // The OS column's word matches too: the simulation image has no base OS, only its family.
        await user.clear(search);
        await user.type(search, "linux");
        expect(screen.getByText("sim-base")).toBeInTheDocument();
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
    });

    it("shows each image's architecture after its OS, a dash for none, and searches and sorts by it", async () => {
        const graviton = {
            ...IMAGES[0],
            imageId: "9",
            stackName: "linux-graviton",
            amiId: "ami-0bbbbbbbbbbbbbbbb",
            description: "Graviton CAD",
            architecture: "arm64",
        };
        (adminQueries.useEdhAdminImages as jest.Mock).mockReturnValue(query([...IMAGES, graviton]));
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        const table = screen.getByRole("table", { name: "Registered EDH images" });
        const headers = within(table)
            .getAllByRole("columnheader")
            .map((header) => header.textContent);
        expect(headers).toEqual([
            "Kind",
            "Stack name",
            "AMI",
            "OS",
            "Architecture",
            "Root (GB)",
            "Profile",
            "Description",
            "Actions",
        ]);
        const column = headers.indexOf("Architecture");
        const architectureOf = (name: string) =>
            within(screen.getByText(name).closest("tr") as HTMLElement).getAllByRole("cell")[column]
                .textContent;
        expect(architectureOf("linux-cad")).toBe("x86_64");
        expect(architectureOf("linux-graviton")).toBe("arm64");
        expect(architectureOf("sim-base")).toBe("—");

        // Ascending text order puts an image without an architecture first.
        await user.click(within(table).getByRole("button", { name: "Architecture" }));
        const stackNames = () =>
            within(screen.getByRole("table", { name: "Registered EDH images" }))
                .getAllByRole("row")
                .slice(1)
                .map((row) => within(row).getAllByRole("cell")[1].textContent);
        expect(stackNames()).toEqual(["sim-base", "linux-graviton", "linux-cad"]);

        await user.type(screen.getByRole("textbox", { name: "Search images" }), "arm64");
        expect(screen.getByText("linux-graviton")).toBeInTheDocument();
        expect(screen.queryByText("linux-cad")).not.toBeInTheDocument();
        expect(screen.queryByText("sim-base")).not.toBeInTheDocument();
    });

    it("renders the tabs in order, opens on Sessions, and shows the health line and the image rows", async () => {
        allow();
        render(<EdhAdminPage />);
        expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
            "Sessions",
            "Capacity",
            "Images",
            "Settings",
        ]);
        expect(screen.getByRole("tab", { name: "Sessions" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByText("cad-1")).toBeInTheDocument();
        const healthLine = screen.getByText(/edh-dev: reachable \(42 ms\)/);
        expect(healthLine).toHaveAttribute("role", "status");
        expect(healthLine).toHaveClass("text-text-secondary");
        expect(healthLine).not.toHaveClass("text-vams-error");
        await openImagesTab(userEvent.setup());
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
        expect(screen.getByText("sim-base")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Register VDI image" })).toBeInTheDocument();
    });

    it("adds the AMI Recipes tab when the AMI recipes feature is enabled", async () => {
        (appCache.getItem as jest.Mock).mockReturnValue({
            featuresEnabled: ["EDH_WORKSTATIONS", "EDH_AMI_RECIPES"],
        });
        allow();
        render(<EdhAdminPage />);
        expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
            "Sessions",
            "Capacity",
            "Images",
            "AMI Recipes",
            "Settings",
        ]);

        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "AMI Recipes" }));
        expect(screen.getByText("AL2023 desktop")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "New recipe" })).toBeInTheDocument();
    });

    it("leaves the AMI Recipes tab out without the AMI recipes feature", () => {
        allow();
        render(<EdhAdminPage />);
        expect(screen.getAllByRole("tab").map((t) => t.textContent)).not.toContain("AMI Recipes");
        expect(screen.getAllByRole("tab")).toHaveLength(4);
    });

    it("opens on the first tab whose panel the caller may read", () => {
        allow((method, path) => !(method === "GET" && path === "/addon/edh/admin/instances"));
        render(<EdhAdminPage />);
        expect(screen.getByRole("tab", { name: "Sessions" })).toHaveAttribute(
            "aria-selected",
            "false"
        );
        expect(screen.getByRole("tab", { name: "Capacity" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByText("Total sessions")).toBeInTheDocument();
        expect(screen.queryByText(/You do not have access/)).not.toBeInTheDocument();
    });

    it("opens on Sessions with its no-access notice when the caller may read no panel", () => {
        allow((method) => method !== "GET");
        render(<EdhAdminPage />);
        expect(screen.getByRole("tab", { name: "Sessions" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByText(/You do not have access to the session views/)).toBeInTheDocument();
    });

    it("replaces the Images panel with a no-access notice when the list route is denied", async () => {
        allow((method, path) => !(method === "GET" && path === "/addon/edh/admin/images"));
        render(<EdhAdminPage />);
        await openImagesTab(userEvent.setup());
        expect(screen.getByRole("tab", { name: "Images" })).toBeInTheDocument();
        expect(screen.getByText(/You do not have access to the image catalog/)).toBeInTheDocument();
        expect(screen.queryByText("linux-cad")).not.toBeInTheDocument();
    });

    it("hides the register buttons without the POST routes", async () => {
        allow((method) => method !== "POST");
        render(<EdhAdminPage />);
        await openImagesTab(userEvent.setup());
        expect(
            screen.queryByRole("button", { name: "Register VDI image" })
        ).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Register simulation image" })
        ).not.toBeInTheDocument();
    });

    it("hides the health line without the health route", () => {
        allow((method, path) => path !== "/addon/edh/admin/health");
        render(<EdhAdminPage />);
        expect(screen.queryByText(/reachable/)).not.toBeInTheDocument();
        expect(adminQueries.useEdhHealth).toHaveBeenLastCalledWith(false);
    });

    it("names a rejected credential on the health line as an alert in the error tone", () => {
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue(
            query({ status: "error", reason: "credentialRejected", edhClusterId: "edh-dev" })
        );
        allow();
        render(<EdhAdminPage />);
        const healthLine = screen.getByText(/edh-dev: credential rejected/);
        expect(healthLine).toHaveAttribute("role", "alert");
        expect(healthLine).toHaveClass("text-vams-error");
        expect(healthLine).toHaveTextContent("re-sync the EDH root key secret");
    });

    it("renders a probe that got no answer as an alert too", () => {
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue({
            ...query(undefined),
            error: new Error("Network Error"),
        });
        allow();
        render(<EdhAdminPage />);
        const healthLine = screen.getByText(
            "EDH is unreachable or rejected the configured credential"
        );
        expect(healthLine).toHaveAttribute("role", "alert");
        expect(healthLine).toHaveClass("text-vams-error");
    });

    it("reports a probe that failed over a still-cached healthy answer as unreachable, not reachable", () => {
        // A refetch that fails keeps the previous data; the alert must describe the failed probe.
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue({
            ...query({ status: "ok", edhClusterId: "edh-dev", latencyMs: 42 }),
            error: new Error("Network Error"),
        });
        allow();
        render(<EdhAdminPage />);
        const healthLine = screen.getByText(
            "EDH is unreachable or rejected the configured credential"
        );
        expect(healthLine).toHaveAttribute("role", "alert");
        expect(healthLine).toHaveClass("text-vams-error");
        expect(screen.queryByText(/edh-dev: reachable/)).not.toBeInTheDocument();
    });

    it("moves focus with the selection when the tab strip is driven by the keyboard", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        screen.getByRole("tab", { name: "Sessions" }).focus();
        await user.keyboard("{ArrowRight}");
        expect(screen.getByRole("tab", { name: "Capacity" })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        expect(screen.getByRole("tab", { name: "Capacity" })).toHaveFocus();
        expect(screen.getByText("Total sessions")).toBeInTheDocument();
        await user.keyboard("{ArrowRight}");
        expect(screen.getByRole("tab", { name: "Images" })).toHaveFocus();
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
        await user.keyboard("{End}");
        expect(screen.getByRole("tab", { name: "Settings" })).toHaveFocus();
        expect(screen.getByText("Enabled")).toBeInTheDocument();
        await user.keyboard("{Home}");
        expect(screen.getByRole("tab", { name: "Sessions" })).toHaveFocus();
        expect(screen.getByText("cad-1")).toBeInTheDocument();
    });

    it("keeps the health line readable without a latency and for a reason it does not model", () => {
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue(
            query({ status: "ok", edhClusterId: "edh-dev" })
        );
        allow();
        const { rerender } = render(<EdhAdminPage />);
        expect(screen.getByText("EDH edh-dev: reachable")).toBeInTheDocument();
        (adminQueries.useEdhHealth as jest.Mock).mockReturnValue(
            query({ status: "error", reason: "timeout", edhClusterId: "edh-dev" })
        );
        rerender(<EdhAdminPage />);
        expect(screen.getByText("EDH edh-dev: unreachable")).toBeInTheDocument();
    });

    it("deletes an image through the row action and the confirm dialog", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await openActionsFor(user, "linux-cad");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(await screen.findByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(mutations.deleteImage.mutateAsync).toHaveBeenCalledWith({
                kind: "vdi",
                imageId: "7",
            })
        );
        await waitFor(() =>
            expect(mockToast.success).toHaveBeenCalledWith("Image deleted", {
                description: "linux-cad",
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("sends one image delete for a double-click on the confirmation", async () => {
        mutations.deleteImage.mutateAsync = jest.fn(neverAnswers);
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await openActionsFor(user, "linux-cad");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        doubleClick(await screen.findByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(mutations.deleteImage.mutateAsync).toHaveBeenCalledWith({
                kind: "vdi",
                imageId: "7",
            })
        );
        expect(mutations.deleteImage.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("sends one registration for a double-click on Register", async () => {
        mutations.registerVdi.mutateAsync = jest.fn(neverAnswers);
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await user.click(screen.getByRole("button", { name: "Register VDI image" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(within(dialog).getByLabelText("Stack name"), "linux-cad-2");
        await user.selectOptions(within(dialog).getByLabelText("Base OS"), "amazonlinux2023");
        await user.selectOptions(within(dialog).getByLabelText("Virtual desktop profile"), "1");
        doubleClick(within(dialog).getByRole("button", { name: "Register" }));
        await waitFor(() => expect(mutations.registerVdi.mutateAsync).toHaveBeenCalled());
        expect(mutations.registerVdi.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("a refused image delete closes the confirm dialog, toasts the message and keeps the row", async () => {
        mutations.deleteImage.mutateAsync = jest.fn().mockRejectedValue(new Error("EDH refused"));
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await openActionsFor(user, "linux-cad");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(await screen.findByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Delete failed", {
                description: "linux-cad: EDH refused",
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(screen.getByText("linux-cad")).toBeInTheDocument();
        expect(mockToast.success).not.toHaveBeenCalled();
    });

    it.each([
        ["Register VDI image", "registerVdi"],
        ["Register simulation image", "registerSimulation"],
    ] as const)(
        "a refused registration through %s keeps the form open and toasts the stack name",
        async (button, key) => {
            mutations[key].mutateAsync = jest.fn().mockRejectedValue(new Error("duplicate stack"));
            allow();
            render(<EdhAdminPage />);
            const user = userEvent.setup();
            await openImagesTab(user);
            await user.click(screen.getByRole("button", { name: button }));
            const dialog = screen.getByRole("dialog");
            await user.type(within(dialog).getByLabelText("AMI ID"), "ami-0123456789abcdef0");
            await user.type(within(dialog).getByLabelText("Stack name"), "linux-cad-2");
            if (key === "registerVdi") {
                await user.selectOptions(
                    within(dialog).getByLabelText("Base OS"),
                    "amazonlinux2023"
                );
                await user.selectOptions(
                    within(dialog).getByLabelText("Virtual desktop profile"),
                    "1"
                );
            } else {
                await user.type(within(dialog).getByLabelText("Target node profile ID"), "4");
            }
            await user.click(within(dialog).getByRole("button", { name: "Register" }));
            await waitFor(() =>
                expect(mockToast.error).toHaveBeenCalledWith("Registration failed", {
                    description: "linux-cad-2: duplicate stack",
                })
            );
            // The operator keeps their input to correct it.
            expect(screen.getByRole("dialog")).toBeInTheDocument();
            expect(within(dialog).getByLabelText("Stack name")).toHaveValue("linux-cad-2");
            expect(mockToast.success).not.toHaveBeenCalled();
        }
    );

    it.each([
        ["Stop", "stopInstance", "Stop failed"],
        ["Deactivate", "deactivateInstance", "Deactivate failed"],
    ])(
        "a refused %s closes the confirm dialog, toasts the session label and keeps the row",
        async (label, key, title) => {
            mutations[key].mutateAsync = jest.fn().mockRejectedValue(new Error("Forbidden"));
            allow();
            render(<EdhAdminPage />);
            const user = userEvent.setup();
            await user.click(screen.getByRole("tab", { name: "Sessions" }));
            await openActionsFor(user, "cad-1");
            await user.click(screen.getByRole("menuitem", { name: label }));
            await user.click(await screen.findByRole("button", { name: label }));
            await waitFor(() =>
                expect(mockToast.error).toHaveBeenCalledWith(title, {
                    description: "cad-1: Forbidden",
                })
            );
            expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
            expect(screen.getByText("cad-1")).toBeInTheDocument();
            expect(mockToast.success).not.toHaveBeenCalled();
        }
    );

    it.each([
        ["Stop", "stopInstance", "Stop not confirmed"],
        ["Deactivate", "deactivateInstance", "Deactivate not confirmed"],
    ])(
        "a %s EDH did not confirm closes the confirm dialog and warns instead of failing",
        async (label, key, title) => {
            mutations[key].mutateAsync = jest.fn().mockRejectedValue(unknown502());
            allow();
            render(<EdhAdminPage />);
            const user = userEvent.setup();
            await user.click(screen.getByRole("tab", { name: "Sessions" }));
            await openActionsFor(user, "cad-1");
            await user.click(screen.getByRole("menuitem", { name: label }));
            await user.click(await screen.findByRole("button", { name: label }));
            await waitFor(() =>
                expect(mockToast.warning).toHaveBeenCalledWith(title, {
                    description: `cad-1: ${UNCONFIRMED}`,
                })
            );
            expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
            expect(mockToast.error).not.toHaveBeenCalled();
            expect(mockToast.success).not.toHaveBeenCalled();
        }
    );

    it("a plain 502 on a stop is still a failure", async () => {
        mutations.stopInstance.mutateAsync = jest
            .fn()
            .mockRejectedValue(new EdhRequestError(UNAVAILABLE, 502, { message: UNAVAILABLE }));
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Stop" }));
        await user.click(await screen.findByRole("button", { name: "Stop" }));
        await waitFor(() =>
            expect(mockToast.error).toHaveBeenCalledWith("Stop failed", {
                description: `cad-1: ${UNAVAILABLE}`,
            })
        );
        expect(mockToast.warning).not.toHaveBeenCalled();
    });

    it("an image delete answered by the gateway's 504 warns 'Delete not confirmed'", async () => {
        mutations.deleteImage.mutateAsync = jest.fn().mockRejectedValue(gateway504());
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await openActionsFor(user, "linux-cad");
        await user.click(screen.getByRole("menuitem", { name: "Delete" }));
        await user.click(await screen.findByRole("button", { name: "Delete" }));
        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Delete not confirmed", {
                description: `linux-cad: ${OUTCOME_UNKNOWN_MESSAGE}`,
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(mockToast.error).not.toHaveBeenCalled();
    });

    it("a registration EDH did not confirm closes the form and warns, so it is not sent twice", async () => {
        mutations.registerVdi.mutateAsync = jest.fn().mockRejectedValue(unknown502());
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        await user.click(screen.getByRole("button", { name: "Register VDI image" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("AMI ID"), "ami-0123456789abcdef0");
        await user.type(within(dialog).getByLabelText("Stack name"), "linux-cad-2");
        await user.selectOptions(within(dialog).getByLabelText("Base OS"), "amazonlinux2023");
        await user.selectOptions(within(dialog).getByLabelText("Virtual desktop profile"), "1");
        await user.click(within(dialog).getByRole("button", { name: "Register" }));
        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Registration not confirmed", {
                description: `linux-cad-2: ${UNCONFIRMED}`,
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(mockToast.error).not.toHaveBeenCalled();
        expect(mockToast.success).not.toHaveBeenCalled();
    });

    it("the Sessions tab lists sessions and hides Stop without the stop route", async () => {
        allow((method, path) => !(method === "POST" && path.endsWith("/stop")));
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        expect(screen.getByText("cad-1")).toBeInTheDocument();
        // One noun for the view: the count, the view picker and the table all say session.
        expect(screen.getByText("1 sessions")).toBeInTheDocument();
        expect(screen.getByLabelText("Session view")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Refresh sessions" })).toBeInTheDocument();
        expect(screen.getByRole("table", { name: "EDH sessions" })).toBeInTheDocument();
        expect(screen.queryByText(/instances/i)).not.toBeInTheDocument();
        expect(screen.getByText("alice")).toBeInTheDocument();
        expect(screen.getByText("Active")).toBeInTheDocument();
        // EDH's own state word, capitalised.
        expect(screen.getByText("Running")).toBeInTheDocument();
        expect(screen.queryByText("running")).not.toBeInTheDocument();
        await openActionsFor(user, "cad-1");
        expect(screen.queryByRole("menuitem", { name: "Stop" })).not.toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: "Deactivate" })).toBeInTheDocument();
    });

    it("searches the sessions from the toolbar and keeps the count of the whole list", async () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                INSTANCES[0],
                { ...INSTANCES[0], sessionId: "s-2", name: "sim-2", userId: "bob" },
            ])
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        expect(screen.getByText("2 sessions")).toBeInTheDocument();
        const search = screen.getByRole("textbox", { name: "Search sessions" });
        expect(screen.queryByRole("textbox", { name: "Search" })).not.toBeInTheDocument();
        await user.type(search, "bob");
        expect(screen.getByText("sim-2")).toBeInTheDocument();
        expect(screen.queryByText("cad-1")).not.toBeInTheDocument();
        expect(screen.getByText("2 sessions")).toBeInTheDocument();
    });

    it("shows the service's message when the session list fails", async () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue({
            ...query(undefined),
            error: new Error("EDH refused the listing"),
        });
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        expect(
            screen.getByText("Error loading sessions: EDH refused the listing")
        ).toBeInTheDocument();
    });

    it("the Sessions tab keeps an open row menu open across a poll tick", async () => {
        allow();
        const { rerender } = render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        expect(screen.getByRole("menu")).toBeInTheDocument();

        // A tick hands back fresh row objects with the same identities while isFetching toggles.
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue({
            ...query([{ ...INSTANCES[0] }]),
            isFetching: true,
        });
        rerender(<EdhAdminPage />);
        expect(screen.getByRole("menu")).toBeInTheDocument();

        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([{ ...INSTANCES[0] }])
        );
        rerender(<EdhAdminPage />);
        expect(screen.getByRole("menu")).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: "Deactivate" })).toBeInTheDocument();
    });

    it("the deactivate dialog says session and names what EDH deletes", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
        const dialog = await screen.findByRole("dialog");
        expect(dialog).toHaveTextContent("Deactivate this session? EDH deletes the desktop");
        expect(dialog).toHaveTextContent("Its VAMS session key, if any, is revoked.");
        expect(dialog).not.toHaveTextContent(/reconciler/);
        expect(dialog).not.toHaveTextContent(/instance disk/);
    });

    it("confirms a stop with the affirmative button and a deactivate in the destructive red", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Stop" }));
        const stop = await screen.findByRole("button", { name: "Stop" });
        expect(stop).not.toHaveClass("bg-red-600");
        // The affirmative button, distinct from the outlined Cancel beside it.
        expect(stop).toHaveClass("bg-blue-600");
        expect(stop).not.toHaveClass("orch-outline");
        expect(screen.getByRole("button", { name: "Cancel" })).toHaveClass("orch-outline");
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
        expect(await screen.findByRole("button", { name: "Deactivate" })).toHaveClass("bg-red-600");
    });

    it("keeps a deactivate from being confirmed while a stop is still in flight", async () => {
        // The stop's dialog can be dismissed while its request is out; the next dialog waits for it.
        mutations.stopInstance.isPending = true;
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
        const confirm = await screen.findByRole("button", { name: "Deactivate" });
        expect(confirm).toBeDisabled();
        await user.click(confirm);
        expect(mutations.deactivateInstance.mutateAsync).not.toHaveBeenCalled();
    });

    it("sends one stop for a double-click on the confirmation", async () => {
        mutations.stopInstance.mutateAsync = jest.fn(neverAnswers);
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Stop" }));
        doubleClick(await screen.findByRole("button", { name: "Stop" }));
        await waitFor(() => expect(mutations.stopInstance.mutateAsync).toHaveBeenCalledWith("s-1"));
        expect(mutations.stopInstance.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("deactivate goes through the confirm dialog and the mutation", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-1");
        await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
        await user.click(await screen.findByRole("button", { name: "Deactivate" }));
        await waitFor(() =>
            expect(mutations.deactivateInstance.mutateAsync).toHaveBeenCalledWith("s-1")
        );
        await waitFor(() =>
            expect(mockToast.success).toHaveBeenCalledWith("Session deactivated", {
                description: "cad-1",
            })
        );
    });

    it("deactivates a launch that never received a session id by its bootstrap id", async () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                {
                    ...INSTANCES[0],
                    sessionId: null,
                    bootstrapId: "b-9",
                    name: "cad-9",
                    edhState: "provisioning",
                    state: "CREATING",
                },
            ])
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await openActionsFor(user, "cad-9");
        expect(screen.queryByRole("menuitem", { name: "Stop" })).not.toBeInTheDocument();
        await user.click(screen.getByRole("menuitem", { name: "Deactivate" }));
        await user.click(await screen.findByRole("button", { name: "Deactivate" }));
        await waitFor(() =>
            expect(mutations.deactivateInstance.mutateAsync).toHaveBeenCalledWith("b-9")
        );
    });

    it("shows a session being started again as Starting and a launch without one as Creating", async () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                {
                    ...INSTANCES[0],
                    sessionId: "s-7",
                    name: "cad-7",
                    edhState: "pending",
                    state: "CREATING",
                },
                {
                    ...INSTANCES[0],
                    sessionId: null,
                    bootstrapId: "b-9",
                    name: "cad-9",
                    edhState: "provisioning",
                    state: "CREATING",
                },
            ])
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        expect(screen.getByText("Starting")).toBeInTheDocument();
        expect(screen.getByText("Creating")).toBeInTheDocument();
    });

    it("shows the AWS error EDH reported beside a failed launch's reason, and nothing for a row without one", async () => {
        (adminQueries.useEdhAdminInstances as jest.Mock).mockReturnValue(
            query([
                INSTANCES[0],
                {
                    ...INSTANCES[0],
                    sessionId: null,
                    bootstrapId: "b-8",
                    name: "cad-8",
                    edhState: null,
                    state: "ORPHANED",
                    lastError: "QUOTA_EXCEEDED",
                    edhFailure: {
                        event: "placement_failed",
                        errorCode: "VcpuLimitExceeded",
                        operation: "RunInstances",
                        observedAt: "2026-10-22T10:00:00Z",
                    },
                },
                {
                    ...INSTANCES[0],
                    sessionId: null,
                    bootstrapId: "b-9",
                    name: "cad-9",
                    edhState: null,
                    state: "ORPHANED",
                    lastError: "LAUNCH_ERROR",
                    edhFailure: null,
                },
            ])
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        const reason = screen.getByText(describeLastError("QUOTA_EXCEEDED"));
        const detail = screen.getByText("AWS: VcpuLimitExceeded (RunInstances)");
        expect(reason.parentElement).toContainElement(detail);
        // The launch without a captured failure keeps its reason alone.
        expect(screen.getByText(describeLastError("LAUNCH_ERROR"))).toBeInTheDocument();
        expect(screen.getAllByText(/^AWS: /)).toHaveLength(1);
        expect(screen.queryByText(/^Desktop setup failed at /)).not.toBeInTheDocument();
    });

    it("the stale view shows the setup checkpoint of a failed launch", async () => {
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(
            query([
                STALE[0],
                {
                    ...STALE[1],
                    lastError: "LAUNCH_ERROR",
                    edhFailure: {
                        event: "session-failed",
                        checkpoint: "dcv_session_create",
                        observedAt: "2026-10-22T10:00:00Z",
                    },
                },
            ])
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        // The all-sessions rows carry no failure detail.
        expect(screen.queryByText(/^AWS: /)).not.toBeInTheDocument();
        await user.selectOptions(screen.getByLabelText("Session view"), "stale");
        expect(screen.getByText("Desktop setup failed at dcv_session_create")).toBeInTheDocument();
        expect(screen.queryByText(/^AWS: /)).not.toBeInTheDocument();
    });

    it("the stale view names why each session is stale and takes the stopped-minutes filter", async () => {
        (adminQueries.useEdhStaleInstances as jest.Mock).mockReturnValue(query(STALE));
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Sessions" }));
        await user.selectOptions(screen.getByLabelText("Session view"), "stale");
        const minutes = screen.getByLabelText("Stopped minutes");
        expect(minutes).toHaveValue(120);
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(120, true);
        // The threshold reaches the query once the field is left or Enter is pressed, not per keystroke.
        await user.clear(minutes);
        await user.type(minutes, "60");
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(120, true);
        await user.tab();
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(60, true);
        await user.clear(minutes);
        await user.type(minutes, "90{Enter}");
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(90, true);
        // An emptied field falls back to the default rather than snapping to 1.
        await user.clear(minutes);
        await user.tab();
        expect(minutes).toHaveValue(120);
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(120, true);
        // A number below the floor clamps to it rather than reading as empty.
        await user.clear(minutes);
        await user.type(minutes, "0");
        await user.tab();
        expect(minutes).toHaveValue(1);
        expect(adminQueries.useEdhStaleInstances).toHaveBeenLastCalledWith(1, true);
        expect(screen.getByText("Stopped in EDH")).toBeInTheDocument();
        expect(screen.getByText("Not listed by EDH")).toBeInTheDocument();
        expect(screen.getByText("Orphaned")).toBeInTheDocument();
        expect(screen.getByText(describeLastError("QUOTA_EXCEEDED"))).toBeInTheDocument();
    });

    it("the Capacity tab shows the aggregate", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Capacity" }));
        expect(screen.getByText("Total sessions").nextSibling).toHaveTextContent("3");
        expect(screen.getByText("Distinct owners").nextSibling).toHaveTextContent("2");
        expect(screen.getByText(/Running/)).toHaveTextContent("Running: 2");
    });

    it("the Capacity tab lists the virtual desktop profiles as a table, not raw records", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Capacity" }));
        const table = screen.getByRole("table", { name: "Virtual desktop profiles" });
        expect(
            within(table)
                .getAllByRole("columnheader")
                .map((header) => header.textContent)
        ).toEqual(["Id", "Name", "Allowed instance types", "Max root size (GB)", "Subnets"]);
        const row = within(table).getAllByRole("row")[1];
        expect(within(row).getByText("1")).toBeInTheDocument();
        expect(within(row).getByText("default")).toBeInTheDocument();
        // Grouped by architecture; the empty arm64 group is left out.
        expect(within(row).getByText("x86_64: m5.large, m5.xlarge")).toBeInTheDocument();
        expect(within(row).getByText("500")).toBeInTheDocument();
        expect(
            within(row).getByText("subnet-00000abcdefabcdef, subnet-00000bcdefabcdefa")
        ).toBeInTheDocument();
        expect(screen.queryByText(/profile_name/)).not.toBeInTheDocument();
    });

    it("the register VDI dialog offers the capacity profiles as a picker", async () => {
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await openImagesTab(user);
        expect(adminQueries.useEdhCapacity).toHaveBeenLastCalledWith(false);
        await user.click(screen.getByRole("button", { name: "Register VDI image" }));
        expect(adminQueries.useEdhCapacity).toHaveBeenLastCalledWith(true);
        const dialog = screen.getByRole("dialog");
        const picker = within(dialog).getByLabelText("Virtual desktop profile");
        expect(within(picker).getByRole("option", { name: "default (id 1)" })).toHaveValue("1");
        expect(within(dialog).queryByLabelText("Virtual desktop profile ID")).toBeNull();
    });

    it("the Capacity tab tolerates a body without the state or profile breakdowns", async () => {
        (adminQueries.useEdhCapacity as jest.Mock).mockReturnValue(
            query({ totalSessions: 0, distinctOwners: 0 })
        );
        allow();
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Capacity" }));
        expect(screen.getByText("Total sessions").nextSibling).toHaveTextContent("0");
        expect(screen.getByText("No sessions.")).toBeInTheDocument();
        expect(screen.getByText("No profiles returned.")).toBeInTheDocument();
        expect(screen.queryByText(/Error loading capacity/)).not.toBeInTheDocument();
    });

    it("renders the Settings tab with EDH_WORKSTATIONS alone", () => {
        allow();
        render(<EdhAdminPage />);
        expect(screen.getByRole("tab", { name: /settings/i })).toBeInTheDocument();
    });

    it("renders the Settings tab with both EDH_WORKSTATIONS and EDH_AMI_RECIPES", () => {
        (appCache.getItem as jest.Mock).mockReturnValue({
            featuresEnabled: ["EDH_WORKSTATIONS", "EDH_AMI_RECIPES"],
        });
        allow();
        render(<EdhAdminPage />);
        expect(screen.getByRole("tab", { name: /settings/i })).toBeInTheDocument();
    });

    it("replaces the Settings panel with a no-access notice when the settings route is denied", async () => {
        allow((method, path) => !(method === "GET" && path === "/addon/edh/admin/settings"));
        render(<EdhAdminPage />);
        const user = userEvent.setup();
        await user.click(screen.getByRole("tab", { name: "Settings" }));

        expect(screen.getByRole("tab", { name: "Settings" })).toBeInTheDocument();
        expect(screen.getByText(/You do not have access to the lease policy/)).toBeInTheDocument();
        expect(screen.queryByLabelText("Lease hours")).not.toBeInTheDocument();
    });
});
