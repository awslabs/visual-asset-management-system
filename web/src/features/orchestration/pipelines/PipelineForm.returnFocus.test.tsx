/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pipeline form's dialog returns focus to the control that opened it when it closes, by Cancel,
 * Escape or the close button, rather than to the page body.
 */

import React, { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";

jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
}));
jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

/** A page button that mounts the dialog, which unmounts itself through onDone. */
const Page: React.FC = () => {
    const [open, setOpen] = useState(false);
    return (
        <QueryClientProvider client={new QueryClient()}>
            <button type="button" onClick={() => setOpen(true)}>
                New pipeline
            </button>
            {open && <PipelineForm mode="create" databaseId="db1" onDone={() => setOpen(false)} />}
        </QueryClientProvider>
    );
};

describe("PipelineForm dialog focus return", () => {
    it("returns focus to its opener after Cancel, Escape and the close button", async () => {
        const user = userEvent.setup({ delay: null });
        render(<Page />);
        const opener = screen.getByRole("button", { name: "New pipeline" });
        for (const close of ["Cancel", "Escape", "Close dialog"]) {
            opener.focus();
            await user.keyboard("{Enter}");
            const dialog = await screen.findByRole("dialog");
            if (close === "Escape") await user.keyboard("{Escape}");
            else await user.click(within(dialog).getByRole("button", { name: close }));
            await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
            await waitFor(() => expect(opener).toHaveFocus());
        }
    });
});
