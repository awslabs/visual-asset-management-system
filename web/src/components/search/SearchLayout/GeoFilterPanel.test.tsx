/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The geospatial filter's Apply gate.
 *
 * `apply()` used to check only NaN-ness, so an out-of-range coordinate was accepted, stored in the
 * panel, and rejected by the backend — which fails the WHOLE search request (the filter travels as
 * `geoSearch` on it) with a Pydantic message that names no field. The panel kept the bad value, so
 * every subsequent search failed until the user guessed which box was wrong.
 *
 * The map selector is stubbed because it imports `react-map-gl`, which is ESM-only under Jest.
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import GeoFilterPanel from "./GeoFilterPanel";

jest.mock("./GeoFilterMapSelector", () => ({ __esModule: true, default: () => null }));

/**
 * Seeding an existing point filter serves two purposes: the ExpandableSection starts expanded, and
 * the inputs start populated, so each test only has to change the one field it is about.
 */
const existingFilters = {
    geo_filter: { relation: "intersects", point: { lat: 47.6062, lon: -122.3321 } },
} as any;

const setUp = () => {
    const onFilterChange = jest.fn();
    render(<GeoFilterPanel filters={existingFilters} onFilterChange={onFilterChange} />);
    return {
        onFilterChange,
        latitude: screen.getByLabelText("Latitude"),
        longitude: screen.getByLabelText("Longitude"),
        apply: screen.getByRole("button", { name: "Apply" }),
    };
};

describe("GeoFilterPanel Apply", () => {
    it("applies an in-range point", () => {
        // Positive control: the "does not apply" assertions below are only meaningful if this path
        // reaches onFilterChange at all.
        const { onFilterChange, longitude, apply } = setUp();
        fireEvent.change(longitude, { target: { value: "-122.4" } });
        fireEvent.click(apply);

        expect(onFilterChange).toHaveBeenCalledTimes(1);
        expect(onFilterChange).toHaveBeenCalledWith(
            "geo_filter",
            expect.objectContaining({ point: { lat: 47.6062, lon: -122.4 } })
        );
    });

    it("refuses a longitude outside [-180, 180] and says which field is wrong", () => {
        // The recorded scenario: a slipped decimal point.
        const { onFilterChange, longitude, apply } = setUp();
        fireEvent.change(longitude, { target: { value: "-1223321" } });
        fireEvent.click(apply);

        expect(onFilterChange).not.toHaveBeenCalled();
        expect(screen.getByText("Longitude must be between -180 and 180.")).toBeInTheDocument();
    });

    it("refuses a latitude outside [-90, 90]", () => {
        const { onFilterChange, latitude, apply } = setUp();
        fireEvent.change(latitude, { target: { value: "476062" } });
        fireEvent.click(apply);

        expect(onFilterChange).not.toHaveBeenCalled();
        expect(screen.getByText("Latitude must be between -90 and 90.")).toBeInTheDocument();
    });

    it("refuses a coordinate with trailing garbage rather than truncating it", () => {
        // parseFloat("47.6abc") is 47.6, which would have been applied silently.
        const { onFilterChange, latitude, apply } = setUp();
        fireEvent.change(latitude, { target: { value: "47.6abc" } });
        fireEvent.click(apply);

        expect(onFilterChange).not.toHaveBeenCalled();
        expect(screen.getByText("Latitude must be a number.")).toBeInTheDocument();
    });
});
