/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import CreateConstraint from "./CreateConstraint";

const _PERMISSION_OBJECTS = {
    objectTypes: [
        {
            label: "Asset",
            value: "asset",
            fields: [{ label: "Asset Name", value: "assetName" }],
        },
    ],
    operators: [{ label: "Equals", value: "equals" }],
    permissions: [{ label: "View/GET", value: "GET" }],
    permissionTypes: [{ label: "Allow", value: "allow" }],
};

jest.mock("../../services/APIService", () => ({
    createConstraint: jest.fn(),
    fetchRoles: jest.fn().mockResolvedValue([]),
    fetchApiRoutes: jest.fn().mockResolvedValue([true, { routes: [] }]),
    fetchConstraintPermissionObjects: jest.fn(),
}));

describe("CreateConstraint", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        const { fetchConstraintPermissionObjects } = require("../../services/APIService");
        fetchConstraintPermissionObjects.mockResolvedValue([true, _PERMISSION_OBJECTS]);
    });

    it("fetches constraint permission objects when opened", async () => {
        const { fetchConstraintPermissionObjects } = require("../../services/APIService");
        render(
            <MemoryRouter>
                <CreateConstraint
                    open={true}
                    setOpen={jest.fn()}
                    setReload={jest.fn()}
                    initState={undefined}
                />
            </MemoryRouter>
        );
        await waitFor(() => {
            expect(fetchConstraintPermissionObjects).toHaveBeenCalled();
        });
    });

    it("shows a retry control on fetch failure and refetches when clicked", async () => {
        const { fetchConstraintPermissionObjects } = require("../../services/APIService");
        // First load fails, retry succeeds.
        fetchConstraintPermissionObjects
            .mockReset()
            .mockResolvedValueOnce([false, "network error"])
            .mockResolvedValueOnce([true, _PERMISSION_OBJECTS]);

        render(
            <MemoryRouter>
                <CreateConstraint
                    open={true}
                    setOpen={jest.fn()}
                    setReload={jest.fn()}
                    initState={undefined}
                />
            </MemoryRouter>
        );

        const retry = await screen.findByText("Retry loading object types");
        expect(fetchConstraintPermissionObjects).toHaveBeenCalledTimes(1);

        await userEvent.click(retry);

        await waitFor(() => {
            expect(fetchConstraintPermissionObjects).toHaveBeenCalledTimes(2);
        });
        await waitFor(() => {
            expect(screen.queryByText("Retry loading object types")).not.toBeInTheDocument();
        });
    });

    describe("deprecated-field filtering on the first open", () => {
        // The object-type matrix is fetched only after the modal opens, so on the very first
        // open the form state is built with no valid-field list. A criterion whose field is no
        // longer in the matrix used to survive that transition, render with a blank Field cell,
        // and be re-submitted; a later open of the same record silently dropped it.
        const baseState = {
            constraintId: "test-constraint",
            name: "test-constraint",
            description: "a valid description",
            objectType: "asset",
            criteriaOr: [],
            groupPermissions: [
                { id: "g1", groupId: "admin", permission: "GET", permissionType: "allow" },
            ],
            userPermissions: [],
        };

        /** Mount closed, then open — the transition on which the matrix is still unfetched. */
        async function openWithCriterion(field: string) {
            const initState = {
                ...baseState,
                criteriaAnd: [{ id: "c1", field, operator: "equals", value: "x" }],
            };
            const view = (open: boolean) => (
                <MemoryRouter>
                    <CreateConstraint
                        open={open}
                        setOpen={jest.fn()}
                        setReload={jest.fn()}
                        initState={initState}
                    />
                </MemoryRouter>
            );
            const { rerender } = render(view(false));
            rerender(view(true));
            const { fetchConstraintPermissionObjects } = require("../../services/APIService");
            await waitFor(() => expect(fetchConstraintPermissionObjects).toHaveBeenCalled());
            return screen.findByTestId("Update-authcriteria-button");
        }

        it("drops a criterion whose field is not in the matrix", async () => {
            // 'deprecatedField' is absent from _PERMISSION_OBJECTS' asset fields. Once it is
            // filtered out no criteria remain, so the form is no longer submittable and the
            // stale criterion cannot be re-saved.
            const submit = await openWithCriterion("deprecatedField");
            await waitFor(() => expect(submit).toBeDisabled());
        });

        it("positive control: keeps a criterion whose field IS in the matrix", async () => {
            // Same transition, valid field: proves the assertion above is caused by the
            // filtering and not by a modal that never became submittable at all.
            const submit = await openWithCriterion("assetName");
            await waitFor(() => expect(submit).not.toBeDisabled());
        });
    });

    it("surfaces the backend error message when create fails", async () => {
        const { createConstraint } = require("../../services/APIService");
        // apiClient throws an ApiError carrying the backend message + status.
        const apiError: any = new Error(
            "Invalid field 'route__path' for objectType 'tagType'. Allowed fields: tagTypeName"
        );
        apiError.name = "ApiError";
        apiError.status = 400;
        createConstraint.mockRejectedValue(apiError);

        // A fully valid form so the submit button is enabled.
        const initState = {
            constraintId: "test-constraint",
            name: "test-constraint",
            description: "a valid description",
            objectType: "asset",
            criteriaAnd: [{ field: "assetName", operator: "equals", value: "x" }],
            criteriaOr: [],
            groupPermissions: [
                { id: "g1", groupId: "admin", permission: "GET", permissionType: "allow" },
            ],
            userPermissions: [],
        };

        render(
            <MemoryRouter>
                <CreateConstraint
                    open={true}
                    setOpen={jest.fn()}
                    setReload={jest.fn()}
                    initState={initState}
                />
            </MemoryRouter>
        );

        const submit = await screen.findByTestId("Update-authcriteria-button");
        await waitFor(() => expect(submit).not.toBeDisabled());
        await userEvent.click(submit);

        await waitFor(() => {
            expect(screen.getAllByText(/Allowed fields: tagTypeName/).length).toBeGreaterThan(0);
        });
    });
});
