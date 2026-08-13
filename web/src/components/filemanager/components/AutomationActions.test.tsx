/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AutomationActions from "./AutomationActions";

// The execute modal is lazy-loaded from the orchestration module; stub it so this suite stays on the
// Cloudscape side of the boundary and does not pull that module's query layer in.
jest.mock("../../../features/orchestration/executions/ExecuteWorkflowModal", () => ({
    __esModule: true,
    default: ({ presetInputFiles }: any) => (
        <div data-testid="execute-modal">{JSON.stringify(presetInputFiles)}</div>
    ),
}));

const FILE = [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/pump.glb" }];

describe("AutomationActions", () => {
    it("offers Execute Workflow under an Automation group", async () => {
        render(<AutomationActions databaseId="db1" assetId="a1" inputFiles={FILE} />);
        // The group is a dropdown, matching the neighbouring Export group rather than a bare button.
        await userEvent.click(screen.getByRole("button", { name: /automation/i }));
        expect(await screen.findByText("Execute Workflow")).toBeInTheDocument();
    });

    it("opens the execute modal with the selection already filled in", async () => {
        render(<AutomationActions databaseId="db1" assetId="a1" inputFiles={FILE} />);
        await userEvent.click(screen.getByRole("button", { name: /automation/i }));
        await userEvent.click(await screen.findByText("Execute Workflow"));
        const modal = await screen.findByTestId("execute-modal");
        expect(JSON.parse(modal.textContent || "[]")).toEqual(FILE);
    });

    it("does not mount the modal until the action is used", () => {
        // Keeps the orchestration chunk off the file-manager's critical path.
        render(<AutomationActions databaseId="db1" assetId="a1" inputFiles={FILE} />);
        expect(screen.queryByTestId("execute-modal")).not.toBeInTheDocument();
    });

    it("disables the action with the supplied reason", async () => {
        render(
            <AutomationActions
                databaseId="db1"
                assetId="a1"
                inputFiles={[]}
                disabledReason="Select a file, folder, or asset first."
            />
        );
        await userEvent.click(screen.getByRole("button", { name: /automation/i }));
        const item = await screen.findByText("Execute Workflow");
        // Cloudscape marks a disabled dropdown item on its menu-item ancestor.
        expect(item.closest('[role="menuitem"]')).toHaveAttribute("aria-disabled", "true");
    });

    it("does not open the modal while disabled", async () => {
        render(
            <AutomationActions
                databaseId="db1"
                assetId="a1"
                inputFiles={[]}
                disabledReason="Nope"
            />
        );
        await userEvent.click(screen.getByRole("button", { name: /automation/i }));
        await userEvent.click(await screen.findByText("Execute Workflow"));
        expect(screen.queryByTestId("execute-modal")).not.toBeInTheDocument();
    });

    it.each([
        ["whole asset", [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/" }]],
        ["folder", [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/models/" }]],
        ["single file", [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" }]],
        [
            "multi file",
            [
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/a.glb" },
                { databaseId: "db1", assetId: "a1", relativeFileKey: "/b.glb" },
            ],
        ],
    ])("passes a %s selection through unchanged", async (_label, files) => {
        // The four shapes the group must support; the trailing '/' on a folder and the bare '/' for a
        // whole asset are what the backend's assetScope gates read, so they must survive verbatim.
        render(<AutomationActions databaseId="db1" assetId="a1" inputFiles={files as any} />);
        await userEvent.click(screen.getByRole("button", { name: /automation/i }));
        await userEvent.click(await screen.findByText("Execute Workflow"));
        const modal = await screen.findByTestId("execute-modal");
        expect(JSON.parse(modal.textContent || "[]")).toEqual(files);
    });
});
