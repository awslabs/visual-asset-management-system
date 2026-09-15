/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import ScopeBadge from "./ScopeBadge";

describe("ScopeBadge", () => {
    it("renders GLOBAL for missing databaseId", () => {
        render(<ScopeBadge databaseId={undefined} />);
        expect(screen.getByText(/GLOBAL/)).toBeInTheDocument();
    });

    it("renders GLOBAL for the GLOBAL sentinel", () => {
        render(<ScopeBadge databaseId="GLOBAL" />);
        expect(screen.getByText(/GLOBAL/)).toBeInTheDocument();
    });

    it("renders the database id for a scoped tag", () => {
        render(<ScopeBadge databaseId="factory-db" />);
        expect(screen.getByText("factory-db")).toBeInTheDocument();
    });
});
