/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AmiBuildsPanel from "./AmiBuildsPanel";
import * as amiRecipeQueries from "../api/amiRecipeQueries";
import type { EdhAmiBuild, EdhAmiRecipe } from "../types";

jest.mock("../api/amiRecipeQueries", () => ({
    useEdhAmiBuilds: jest.fn(),
    useEdhAmiRecipeMutations: jest.fn(),
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const RECIPE = { recipeId: "mine", displayName: "Mine" } as unknown as EdhAmiRecipe;

const query = (data: any) => ({
    data,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const mutation = () => ({ mutateAsync: jest.fn().mockResolvedValue({}), isPending: false });

const builds = (rows: Partial<EdhAmiBuild>[]) =>
    (amiRecipeQueries.useEdhAmiBuilds as jest.Mock).mockReturnValue(
        query(rows.map((row) => ({ recipeId: "mine", ...row })))
    );

const renderPanel = () =>
    render(<AmiBuildsPanel recipe={RECIPE} open onOpenChange={jest.fn()} can={() => true} />);

// The status cell of one version's row.
const statusCell = (version: number) => {
    const row = screen
        .getAllByRole("row")
        .find((r) => r.querySelectorAll("td")[0]?.textContent === String(version));
    if (!row) throw new Error(`no row for v${version}`);
    return row.querySelectorAll("td")[1] as HTMLElement;
};

const KEPT_LINE = /AMI kept while in use|AMI deregistered once no desktop uses it/;

beforeEach(() => {
    jest.clearAllMocks();
    (amiRecipeQueries.useEdhAmiRecipeMutations as jest.Mock).mockReturnValue({
        registerBuild: mutation(),
        retireBuild: mutation(),
    });
});

it("lists the instances that keep a superseded build's AMI", () => {
    builds([
        { version: 2, status: "AVAILABLE", amiId: "ami-0bbbbbbbbbbbbbbbb" },
        {
            version: 1,
            status: "RETIRED",
            amiId: "ami-0aaaaaaaaaaaaaaaa",
            amiInUseBy: ["i-0aaaaaaaaaaaaaaaa", "i-0bbbbbbbbbbbbbbbb"],
        },
    ]);
    renderPanel();

    expect(statusCell(1)).toHaveTextContent(
        "AMI kept while in use: i-0aaaaaaaaaaaaaaaa, i-0bbbbbbbbbbbbbbbb"
    );
    // Information, not an error: the AMI is kept on purpose.
    expect(screen.getByText(/AMI kept while in use/)).not.toHaveClass("text-vams-error");
    expect(statusCell(2)).not.toHaveTextContent(KEPT_LINE);
});

it("says a retired build's AMI is deregistered once no desktop uses it, before the instances", () => {
    builds([
        {
            version: 3,
            status: "RETIRED",
            amiId: "ami-0cccccccccccccccc",
            amiRemovalPending: true,
            amiInUseBy: ["i-0cccccccccccccccc"],
        },
    ]);
    renderPanel();

    expect(statusCell(3)).toHaveTextContent(
        "AMI deregistered once no desktop uses it: i-0cccccccccccccccc"
    );
    expect(statusCell(3)).not.toHaveTextContent("AMI kept while in use");
});

it("says a pending removal without instances, next to the warning of a failed check", () => {
    builds([
        {
            version: 4,
            status: "RETIRED",
            amiId: "ami-0dddddddddddddddd",
            amiRemovalPending: true,
            pruneWarning: "in-use check failed (UnauthorizedOperation)",
        },
    ]);
    renderPanel();

    const status = statusCell(4);
    expect(status).toHaveTextContent("AMI deregistered once no desktop uses it");
    expect(status).not.toHaveTextContent("AMI deregistered once no desktop uses it:");
    expect(status).toHaveTextContent("in-use check failed (UnauthorizedOperation)");
});

it("shows no kept line for a deregistered build or an available one", () => {
    builds([
        {
            version: 6,
            status: "AVAILABLE",
            amiId: "ami-0ffffffffffffffff",
            amiInUseBy: ["i-0ffffffffffffffff"],
        },
        {
            version: 5,
            status: "RETIRED",
            amiId: "ami-0eeeeeeeeeeeeeeee",
            amiDeregisteredAt: "2026-10-06T10:00:00+00:00",
            amiRemovalPending: true,
            amiInUseBy: ["i-0eeeeeeeeeeeeeeee"],
        },
    ]);
    renderPanel();

    expect(statusCell(6)).not.toHaveTextContent(KEPT_LINE);
    expect(statusCell(5)).not.toHaveTextContent(KEPT_LINE);
    // An instance id, not the AMI id that ends the same way.
    expect(screen.queryByText(/\bi-0/)).not.toBeInTheDocument();
});

it("the retire confirmation says the AMI is deregistered once no desktop uses it", async () => {
    builds([
        {
            version: 7,
            status: "AVAILABLE",
            amiId: "ami-0123456789abcdef0",
            edhStackName: "Mine v7",
        },
    ]);
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Retire" }));
    const confirm = await screen.findByRole("dialog", { name: "Retire Mine v7" });
    expect(confirm).toHaveTextContent(
        "The software stack is removed from the EDH catalog, so no new session can be launched from it."
    );
    expect(confirm).toHaveTextContent(
        "VAMS deregisters the AMI once no desktop uses it, so existing desktops can still be started."
    );
    expect(confirm).not.toHaveTextContent("Running sessions are not affected");
});

describe("focus when the Retire confirmation closes", () => {
    // Hidden included: the panel is hidden from the accessibility tree while the confirmation is open.
    const retireButton = () => screen.getByRole("button", { name: "Retire", hidden: true });
    const openRetire = async (user: ReturnType<typeof userEvent.setup>) => {
        retireButton().focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog", { name: "Retire Mine v7" });
    };
    const confirmClosed = () =>
        waitFor(() =>
            expect(screen.queryByRole("dialog", { name: "Retire Mine v7" })).not.toBeInTheDocument()
        );

    beforeEach(() => builds([{ version: 7, status: "AVAILABLE", amiId: "ami-0bbbbbbbbbbbbbbbb" }]));

    it.each(["Escape", "Cancel"])("returns to the build's Retire button after %s", async (how) => {
        const user = userEvent.setup();
        renderPanel();
        const confirm = await openRetire(user);
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
        }
        await confirmClosed();
        await waitFor(() => expect(retireButton()).toHaveFocus());
        // The builds panel stays open beneath it.
        expect(screen.getByRole("dialog", { name: "Builds of Mine" })).toBeInTheDocument();
    });

    it("goes to the refresh button once the build is retired, its Retire button leaving the list", async () => {
        const user = userEvent.setup();
        renderPanel();
        const confirm = await openRetire(user);
        await user.click(within(confirm).getByRole("button", { name: "Retire" }));
        await confirmClosed();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Refresh builds" })).toHaveFocus()
        );
    });
});
