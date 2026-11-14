/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import SystemTagHelp from "./SystemTagHelp";

describe("SystemTagHelp", () => {
    it("names the three stored trigger values the {{triggerType}} tag renders", () => {
        // templateRender.py copies the execution row's triggerType into the tag, and executions
        // store the hyphenated vocabulary — not the request-side "fileUpload".
        render(<SystemTagHelp defaultOpen />);
        expect(screen.getByText(/Manual, File-Upload, or System-Reindex/)).toBeInTheDocument();
        expect(screen.queryByText(/Manual \/ fileUpload/)).toBeNull();
    });
});
