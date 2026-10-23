/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReasonModal, { REASON_REQUIRED_MESSAGE } from "./ReasonModal";

const renderModal = (overrides: Partial<React.ComponentProps<typeof ReasonModal>> = {}) => {
    const onConfirm = jest.fn();
    const onDismiss = jest.fn();
    render(
        <ReasonModal
            visible
            header="Reject cascade"
            label="Reason for rejection"
            confirmLabel="Reject"
            onConfirm={onConfirm}
            onDismiss={onDismiss}
            {...overrides}
        />
    );
    return { onConfirm, onDismiss };
};

describe("ReasonModal", () => {
    it("requires a reason before confirming", async () => {
        const { onConfirm } = renderModal();

        await userEvent.click(screen.getByRole("button", { name: "Reject" }));

        expect(onConfirm).not.toHaveBeenCalled();
        expect(screen.getByText(REASON_REQUIRED_MESSAGE)).toBeInTheDocument();
    });

    it("treats a whitespace-only reason as missing", async () => {
        const { onConfirm } = renderModal();

        await userEvent.type(screen.getByLabelText("Reason for rejection"), "   ");
        await userEvent.click(screen.getByRole("button", { name: "Reject" }));

        expect(onConfirm).not.toHaveBeenCalled();
        expect(screen.getByText(REASON_REQUIRED_MESSAGE)).toBeInTheDocument();
    });

    it("passes the trimmed reason to onConfirm", async () => {
        const { onConfirm } = renderModal();

        await userEvent.type(
            screen.getByLabelText("Reason for rejection"),
            "  Trigger asset is being re-uploaded  "
        );
        await userEvent.click(screen.getByRole("button", { name: "Reject" }));

        expect(onConfirm).toHaveBeenCalledTimes(1);
        expect(onConfirm).toHaveBeenCalledWith("Trigger asset is being re-uploaded");
        expect(screen.queryByText(REASON_REQUIRED_MESSAGE)).not.toBeInTheDocument();
    });

    it("cancel dismisses without confirming", async () => {
        const { onConfirm, onDismiss } = renderModal();

        await userEvent.type(screen.getByLabelText("Reason for rejection"), "some reason");
        await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

        expect(onDismiss).toHaveBeenCalledTimes(1);
        expect(onConfirm).not.toHaveBeenCalled();
    });

    it("clears the validation message once a reason is typed", async () => {
        renderModal();

        await userEvent.click(screen.getByRole("button", { name: "Reject" }));
        expect(screen.getByText(REASON_REQUIRED_MESSAGE)).toBeInTheDocument();

        await userEvent.type(screen.getByLabelText("Reason for rejection"), "x");
        expect(screen.queryByText(REASON_REQUIRED_MESSAGE)).not.toBeInTheDocument();
    });
});
