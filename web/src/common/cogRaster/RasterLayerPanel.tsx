/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Slider from "@cloudscape-design/components/slider";
import { RasterLayerEntry } from "./RasterLayerManager";
import { RasterNotice, UseRasterLayers } from "./useRasterLayers";
import { APPROXIMATE_PLACEMENT_LABEL } from "./rasterEligibility";

export interface RasterLayerPanelProps {
    layers: Pick<
        UseRasterLayers,
        | "entries"
        | "notices"
        | "busy"
        | "remove"
        | "setOpacity"
        | "move"
        | "zoomTo"
        | "dismissNotice"
        | "retry"
    >;
    /** Shown when nothing is on the map yet. */
    emptyText?: string;
}

const NoticeAlert: React.FC<{ notice: RasterNotice; onDismiss: (id: string) => void }> = ({
    notice,
    onDismiss,
}) => (
    <Alert
        type={notice.kind === "error" ? "error" : "warning"}
        dismissible
        onDismiss={() => onDismiss(notice.id)}
        header={notice.label}
        data-testid="raster-notice"
    >
        {notice.text}
    </Alert>
);

const LayerRow: React.FC<{
    entry: RasterLayerEntry;
    index: number;
    count: number;
    layers: RasterLayerPanelProps["layers"];
}> = ({ entry, index, count, layers }) => (
    <div data-testid="raster-layer-row" style={{ padding: "8px 0" }}>
        <SpaceBetween size="xs">
            <SpaceBetween direction="horizontal" size="xs">
                <Box variant="strong" data-testid="raster-layer-label">
                    {entry.label}
                </Box>
                {entry.mode === "tile" && <Badge color="green">COG tiles</Badge>}
                {entry.approximate && (
                    <Badge color="red" data-testid="raster-approximate-badge">
                        {APPROXIMATE_PLACEMENT_LABEL}
                    </Badge>
                )}
                {entry.usedCog && <Badge>Converted COG</Badge>}
            </SpaceBetween>
            {entry.failed && (
                <Alert
                    type="error"
                    data-testid="raster-layer-failed"
                    action={
                        entry.mode === "tile" && entry.retryable ? (
                            <Button
                                data-testid="raster-layer-retry"
                                onClick={() => void layers.retry(entry.id)}
                            >
                                Retry
                            </Button>
                        ) : undefined
                    }
                >
                    {entry.failed}
                </Alert>
            )}
            {entry.approximateNote && (
                <Box variant="small" color="text-status-warning">
                    {entry.approximateNote}
                </Box>
            )}
            {entry.usedCog && (
                <Box variant="small" color="text-body-secondary">
                    Showing the COG made from {entry.sourceKey.split("/").pop()}.
                </Box>
            )}
            <Slider
                value={Math.round(entry.opacity * 100)}
                min={0}
                max={100}
                step={5}
                valueFormatter={(value) => `${value}%`}
                ariaLabel={`Opacity of ${entry.label}`}
                onChange={({ detail }) => layers.setOpacity(entry.id, detail.value / 100)}
            />
            <SpaceBetween direction="horizontal" size="xs">
                <Button
                    iconName="angle-up"
                    variant="icon"
                    ariaLabel={`Move ${entry.label} up`}
                    disabled={index === count - 1}
                    onClick={() => layers.move(entry.id, "up")}
                />
                <Button
                    iconName="angle-down"
                    variant="icon"
                    ariaLabel={`Move ${entry.label} down`}
                    disabled={index === 0}
                    onClick={() => layers.move(entry.id, "down")}
                />
                <Button
                    iconName="search"
                    variant="inline-icon"
                    ariaLabel={`Zoom to ${entry.label}`}
                    onClick={() => layers.zoomTo(entry.id)}
                >
                    Zoom to layer
                </Button>
                <Button
                    iconName="close"
                    variant="inline-icon"
                    onClick={() => layers.remove(entry.id)}
                    ariaLabel={`Remove ${entry.label}`}
                >
                    Remove
                </Button>
            </SpaceBetween>
        </SpaceBetween>
    </div>
);

/**
 * The layer list for the rasters on a map: opacity, order (top of the list is the top layer), zoom
 * and remove, plus the reason any raster could not be shown. Shared by the file viewer and the
 * Search map.
 */
const RasterLayerPanel: React.FC<RasterLayerPanelProps> = ({ layers, emptyText }) => {
    const { entries, notices, busy } = layers;
    // Listed top layer first, as map layer lists usually are.
    const topFirst = [...entries].reverse();
    return (
        <SpaceBetween size="s" data-testid="raster-layer-panel">
            {notices.map((notice) => (
                <NoticeAlert key={notice.id} notice={notice} onDismiss={layers.dismissNotice} />
            ))}
            {busy > 0 && (
                <Box color="text-status-info" data-testid="raster-loading">
                    Loading raster...
                </Box>
            )}
            {entries.length === 0 && busy === 0 && emptyText && (
                <Box color="text-body-secondary">{emptyText}</Box>
            )}
            {topFirst.map((entry) => (
                <LayerRow
                    key={entry.id}
                    entry={entry}
                    index={entries.indexOf(entry)}
                    count={entries.length}
                    layers={layers}
                />
            ))}
        </SpaceBetween>
    );
};

export default RasterLayerPanel;
