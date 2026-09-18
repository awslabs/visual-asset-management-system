/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecuteWorkflowButton from "./ExecuteWorkflowButton";

jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(() => ({ loading: false, can: () => true })),
}));

/** The props of every modal the button rendered, newest last. */
const modalProps: any[] = [];
jest.mock("./ExecuteWorkflowModal", () => ({
    __esModule: true,
    default: (props: any) => {
        modalProps.push(props);
        return <div data-testid="execute-modal" />;
    },
}));

beforeEach(() => {
    modalProps.length = 0;
});

describe("ExecuteWorkflowButton", () => {
    it("presets the workflow on a workflow-scoped board", async () => {
        // The board is already filtered to one workflow, so the dialog must not ask which one.
        render(
            <ExecuteWorkflowButton
                scope={{ kind: "workflow", databaseId: "db1", workflowId: "wf1" }}
            />
        );
        await userEvent.click(screen.getByRole("button", { name: "Execute workflow" }));
        expect(modalProps[modalProps.length - 1]).toMatchObject({
            presetWorkflow: { databaseId: "db1", workflowId: "wf1" },
            databaseId: undefined,
            assetId: undefined,
        });
    });

    it("presets the asset, not a workflow, on an asset-scoped board", async () => {
        render(
            <ExecuteWorkflowButton scope={{ kind: "asset", databaseId: "db1", assetId: "a1" }} />
        );
        await userEvent.click(screen.getByRole("button", { name: "Execute workflow" }));
        expect(modalProps[modalProps.length - 1]).toMatchObject({
            databaseId: "db1",
            assetId: "a1",
            presetWorkflow: undefined,
        });
    });

    it("presets nothing on the global board", async () => {
        render(<ExecuteWorkflowButton scope={{ kind: "global" }} />);
        await userEvent.click(screen.getByRole("button", { name: "Execute workflow" }));
        expect(modalProps[modalProps.length - 1].presetWorkflow).toBeUndefined();
        expect(modalProps[modalProps.length - 1].databaseId).toBeUndefined();
    });
});
