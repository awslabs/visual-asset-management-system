/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import CapacityTab, {
    profileInstanceTypes,
    profileInstanceTypesByArchitecture,
} from "./CapacityTab";
import * as adminQueries from "../api/adminQueries";
import type { EdhDesktopProfile } from "../api/admin";

jest.mock("../api/adminQueries", () => ({
    useEdhCapacity: jest.fn(),
}));

// A profile's instance types as EDH lists them: a JSON string keyed by architecture, with an empty group.
const GROUPED = '{"x86_64":["m7i.large","g5.xlarge"],"arm64":["m7g.large"],"i386":[]}';

const profile = (fields: Partial<EdhDesktopProfile> = {}): EdhDesktopProfile => ({
    id: 1,
    ...fields,
});

describe("profileInstanceTypesByArchitecture", () => {
    it("names each architecture that lists a type, in the value's key order, and skips empty groups", () => {
        expect(
            profileInstanceTypesByArchitecture(profile({ allowed_instance_types: GROUPED }))
        ).toEqual([
            { architecture: "x86_64", instanceTypes: ["m7i.large", "g5.xlarge"] },
            { architecture: "arm64", instanceTypes: ["m7g.large"] },
        ]);
        // Not sorted: i386 follows x86_64, as the value lists them.
        expect(
            profileInstanceTypesByArchitecture(
                profile({ allowed_instance_types: '{"x86_64":["t3.large"],"i386":["t2.micro"]}' })
            )
        ).toEqual([
            { architecture: "x86_64", instanceTypes: ["t3.large"] },
            { architecture: "i386", instanceTypes: ["t2.micro"] },
        ]);
    });

    it("reads a mapping as it reads the JSON string", () => {
        expect(
            profileInstanceTypesByArchitecture(
                profile({ allowed_instance_types: { arm64: ["m7g.large"], x86_64: [] } })
            )
        ).toEqual([{ architecture: "arm64", instanceTypes: ["m7g.large"] }]);
    });

    it.each<[string, EdhDesktopProfile["allowed_instance_types"]]>([
        ["a comma-separated string", "m5.large, m5.xlarge"],
        ["a list", ["m5.large", "m5.xlarge"]],
        ["a JSON list", '["m5.large","m5.xlarge"]'],
    ])("gives one group of no architecture for %s", (_case, allowed) => {
        expect(
            profileInstanceTypesByArchitecture(profile({ allowed_instance_types: allowed }))
        ).toEqual([{ architecture: null, instanceTypes: ["m5.large", "m5.xlarge"] }]);
    });

    it("gives the pattern EDH derived the types from when no group lists one", () => {
        expect(
            profileInstanceTypesByArchitecture(
                profile({
                    allowed_instance_types: '{"x86_64":[],"arm64":[]}',
                    pattern_allowed_instance_types: "m5.large,m5.xlarge",
                })
            )
        ).toEqual([{ architecture: null, instanceTypes: ["m5.large", "m5.xlarge"] }]);
    });
});

describe("profileInstanceTypes", () => {
    it("lists the types of every group", () => {
        expect(profileInstanceTypes(profile({ allowed_instance_types: GROUPED }))).toEqual([
            "m7i.large",
            "g5.xlarge",
            "m7g.large",
        ]);
    });
});

describe("CapacityTab", () => {
    beforeEach(() => {
        (adminQueries.useEdhCapacity as jest.Mock).mockReturnValue({
            data: {
                profiles: [
                    profile({ id: 1, profile_name: "grouped", allowed_instance_types: GROUPED }),
                    profile({
                        id: 2,
                        profile_name: "listed",
                        allowed_instance_types: "m5.large,m5.xlarge",
                    }),
                    profile({ id: 3, profile_name: "unset" }),
                ],
                totalSessions: 0,
                byState: {},
                distinctOwners: 0,
            },
            isLoading: false,
            error: null,
            refetch: jest.fn(),
            isFetching: false,
        });
    });

    /** The "Allowed instance types" cell of the profile named `name`. */
    const allowedCell = (name: string) => {
        const table = screen.getByRole("table", { name: "Virtual desktop profiles" });
        const headers = within(table)
            .getAllByRole("columnheader")
            .map((header) => header.textContent);
        const row = within(table).getByText(name).closest("tr") as HTMLElement;
        return within(row).getAllByRole("cell")[headers.indexOf("Allowed instance types")];
    };

    it("shows a profile's allowed instance types by architecture, and an ungrouped list as it is", () => {
        render(<CapacityTab />);
        expect(allowedCell("grouped").textContent).toBe(
            "x86_64: m7i.large, g5.xlarge · arm64: m7g.large"
        );
        expect(allowedCell("listed").textContent).toBe("m5.large, m5.xlarge");
        expect(allowedCell("unset").textContent).toBe("—");
    });
});
