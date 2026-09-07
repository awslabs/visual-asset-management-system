/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The footer is mounted on the unauthenticated login/config-error screens as well as inside the
 * signed-in shell. The backend version is a deployment fingerprint, so the login screens must
 * neither display it nor ask for it.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { PageFooter } from "./Footer";

const mockGetVamsVersion = jest.fn();
jest.mock("../services/APIService", () => ({
    getVamsVersion: (...args: any[]) => mockGetVamsVersion(...args),
}));

describe("PageFooter", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockGetVamsVersion.mockResolvedValue("2.6.0");
    });

    it("does not show or request the backend version by default (login screens)", async () => {
        render(<PageFooter />);

        // The copyright still renders, so the footer is present — the version simply is not.
        await waitFor(() => expect(screen.getByText(/All rights reserved/)).toBeInTheDocument());
        expect(mockGetVamsVersion).not.toHaveBeenCalled();
        expect(screen.queryByText(/Version \d+\.\d+\.\d+/)).not.toBeInTheDocument();
    });

    it("positive control: shows the version when the signed-in shell asks for it", async () => {
        // Proves the assertion above is about the gate, not about a lookup that never resolves.
        render(<PageFooter showVersion={true} />);

        await waitFor(() => expect(screen.getByText(/Version 2\.6\.0/)).toBeInTheDocument());
        expect(mockGetVamsVersion).toHaveBeenCalledTimes(1);
    });
});
