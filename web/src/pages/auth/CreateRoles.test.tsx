/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Editing a role the deployment seeds (admin, basicReadOnly) shows a notice that the next
 * deployment rewrites it. Creating a role or editing a custom one shows no notice.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import CreateRole from "./CreateRoles";

jest.mock("../../services/APIService", () => ({
    createRole: jest.fn(),
    updateRole: jest.fn(),
}));

function renderEditor(initState: any) {
    render(
        <CreateRole open={true} setOpen={jest.fn()} setReload={jest.fn()} initState={initState} />
    );
}

describe("CreateRoles default-role notice", () => {
    it.each(["admin", "basicReadOnly"])("shows the notice when editing %s", (roleName) => {
        renderEditor({ roleName, description: "d", mfaRequired: false });

        expect(screen.getByText("Default role")).toBeInTheDocument();
        expect(screen.getByText(/next deployment rewrites it/)).toBeInTheDocument();
    });

    it("shows no notice when editing a custom role", () => {
        renderEditor({ roleName: "analysts", description: "d", mfaRequired: false });

        expect(screen.queryByText("Default role")).not.toBeInTheDocument();
    });

    it("shows no notice when creating a role", () => {
        renderEditor(undefined);

        expect(screen.queryByText("Default role")).not.toBeInTheDocument();
    });
});
