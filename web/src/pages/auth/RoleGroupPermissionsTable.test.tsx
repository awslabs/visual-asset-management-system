/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import RoleGroupPermissionsTable, { RoleGroupPermission } from "./RoleGroupPermissionsTable";
import { useState } from "react";
import { fetchRoles } from "../../services/APIService";

// The component loads role groups through the APIService, not props
jest.mock("../../services/APIService", () => ({
    fetchRoles: jest.fn(),
}));

const mockFetchRoles = fetchRoles as jest.MockedFunction<any>;

const crypto = require("crypto");

Object.defineProperty(global.self, "crypto", {
    value: {
        getRandomValues: (arr: any[]) => crypto.randomBytes(arr.length),
    },
});

function Harness({ startPerm = [] }: any) {
    const [permissions, setPermissions] = useState<RoleGroupPermission[]>(startPerm);

    return <RoleGroupPermissionsTable permissions={permissions} setPermissions={setPermissions} />;
}

describe("Group Permissions Table", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockFetchRoles.mockResolvedValue([{ roleName: "one" }, { roleName: "two" }]);
    });

    it("renders with an empty list", async () => {
        await act(async () => {
            render(<Harness />);
        });
        const wrapper = createWrapper();
        expect(wrapper.findTable()).toBeTruthy();
        expect(mockFetchRoles).toHaveBeenCalled();
    });

    it("can add a row to the list of permissions", async () => {
        await act(async () => {
            render(<Harness />);
        });
        const wrapper = createWrapper();
        act(() => {
            wrapper.findButton("[data-testid=add-permission-button]")?.click();
        });
        expect(wrapper.findTable()?.findRows()).toHaveLength(1);
    });

    it("has an editable form", async () => {
        mockFetchRoles.mockResolvedValue([{ roleName: "test" }, { roleName: "other" }]);
        await act(async () => {
            render(
                <Harness
                    startPerm={[
                        {
                            id: "test",
                            groupId: "test",
                            permission: "GET",
                        },
                    ]}
                />
            );
        });
        const wrapper = createWrapper();
        expect(wrapper.findTable()?.findRows()).toHaveLength(1);

        expect(wrapper.findTable()?.findBodyCell(1, 2)?.getElement().textContent).toContain("test");
        await act(async () => {
            wrapper.findTable()?.findBodyCell(1, 2)?.click();
        });
        expect(wrapper.findTable()?.findEditingCell()).toBeTruthy();
    });
});
