/* eslint-disable testing-library/no-unnecessary-act */
/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { render } from "@testing-library/react";
import { act } from "react-dom/test-utils";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import RoleGroupPermissionsTable, { RoleGroupPermission } from "./RoleGroupPermissionsTable";
import { useState } from "react";

const crypto = require("crypto");

Object.defineProperty(global.self, "crypto", {
    value: {
        getRandomValues: (arr: any[]) => crypto.randomBytes(arr.length),
    },
});

function Harness({ fetchGroups, startPerm = [] }: any) {
    const [permissions, setPermissions] = useState<RoleGroupPermission[]>(startPerm);

    return (
        <RoleGroupPermissionsTable
            permissions={permissions}
            setPermissions={setPermissions}
            fetchGroups={fetchGroups}
        />
    );
}

describe("Group Permissions Table", () => {
    it("renders with an empty list", async () => {
        const promise = Promise.resolve(["one", "two"]);
        const fetchGroups = jest.fn(() => promise);

        // eslint-disable-next-line testing-library/no-unnecessary-act
        await act(async () => {
            render(<Harness fetchGroups={fetchGroups} />);
            await promise;
        });
        const wrapper = createWrapper();
        expect(wrapper.findTable()).toBeTruthy();
    });

    it("can add a row to the list of permissions", async () => {
        const promise = Promise.resolve(["one", "two"]);
        const fetchGroups = jest.fn(() => promise);
        await act(async () => {
            render(<Harness fetchGroups={fetchGroups} />);
            await promise;
        });
        const wrapper = createWrapper();
        act(() => {
            wrapper.findButton("[data-testid=add-permission-button]")?.click();
        });
        expect(wrapper.findTable()?.findRows()).toHaveLength(1);
    });

    it("has an editable form", async () => {
        const promise = Promise.resolve(["one", "two"]);
        const fetchGroups = jest.fn(() => promise);
        await act(async () => {
            render(
                <Harness
                    fetchGroups={fetchGroups}
                    startPerm={[
                        {
                            id: "test",
                            groupId: "test",
                            permission: "test",
                        },
                    ]}
                />
            );
            await promise;
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
