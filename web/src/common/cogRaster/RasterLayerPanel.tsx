/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Toggle from "@cloudscape-design/components/toggle";
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
    > &
        Partial<Pick<UseRasterLayers, "setVisible">>;
    /**
     * Offers a Visible switch on each raster in place of Remove: a hidden raster stays in the list, dimmed, with
     * its opacity and place, and is off the map. The GeoTIFF Map Viewer uses this (a raster leaves it by
     * being deselected); the Search map keeps Remove.
     */
    visibilityToggle?: boolean;
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
    visibilityToggle: boolean;
}> = ({ entry, index, count, layers, visibilityToggle }) => (
    <div
        data-testid="raster-layer-row"
        data-hidden={entry.visible ? undefined : "true"}
        style={{ padding: "8px 0" }}
    >
        <SpaceBetween size="xs">
            <SpaceBetween direction="horizontal" size="xs">
                {/* A hidden row's label takes the secondary text colour, a theme token that keeps at
                    least 4.5:1 contrast in the light and dark themes (an opacity would not). */}
                <Box
                    variant="strong"
                    color={entry.visible ? "inherit" : "text-body-secondary"}
                    data-testid="raster-layer-label"
                >
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
            <div>
                <Slider
                    value={Math.round(entry.opacity * 100)}
                    min={0}
                    max={100}
                    step={5}
                    valueFormatter={(value) => `${value}%`}
                    ariaLabel={`Opacity of ${entry.label}`}
                    onChange={({ detail }) => layers.setOpacity(entry.id, detail.value / 100)}
                />
            </div>
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
                    // A hidden layer is not on the map, so there is nothing to zoom to: show it first.
                    disabled={!entry.visible}
                    onClick={() => layers.zoomTo(entry.id)}
                >
                    Zoom to layer
                </Button>
                {visibilityToggle && layers.setVisible && !entry.failed ? (
                    <span data-testid="raster-layer-visibility">
                        {/* A switch labelled "Visible": on means the raster is on the map. Its accessible
                            name is the same in both states and the checked state carries the state, so a
                            screen reader says "Show <label> on the map, switch, on / off". */}
                        <Toggle
                            checked={entry.visible}
                            ariaLabel={`Show "${entry.label}" on the map`}
                            onChange={({ detail }) => {
                                void layers.setVisible!(entry.id, detail.checked);
                            }}
                        >
                            Visible
                        </Toggle>
                    </span>
                ) : (
                    !visibilityToggle && (
                        <Button
                            iconName="close"
                            variant="inline-icon"
                            onClick={() => layers.remove(entry.id)}
                            ariaLabel={`Remove ${entry.label}`}
                        >
                            Remove
                        </Button>
                    )
                )}
            </SpaceBetween>
        </SpaceBetween>
    </div>
);

/**
 * The layer list for the rasters on a map: opacity, order (top of the list is the top layer), zoom
 * and remove, plus the reason any raster could not be shown. Shared by the file viewer and the
 * Search map.
 */
const RasterLayerPanel: React.FC<RasterLayerPanelProps> = ({
    layers,
    emptyText,
    visibilityToggle = false,
}) => {
    const { entries, notices, busy } = layers;
    // Said to screen readers when a raster is hidden, shown again, or could not be shown: read from
    // each entry's own state once the manager has applied it, never at the press.
    const [announcement, setAnnouncement] = useState("");
    const previous = useRef<Map<string, { visible: boolean; failed: boolean }>>(new Map());
    useEffect(() => {
        const next = new Map<string, { visible: boolean; failed: boolean }>();
        for (const entry of entries) {
            const now = { visible: entry.visible, failed: !!entry.failed };
            const before = previous.current.get(entry.id);
            if (before) {
                if (!before.failed && now.failed && !before.visible) {
                    setAnnouncement(`${entry.label} could not be shown`);
                } else if (before.visible && !now.visible) {
                    setAnnouncement(`${entry.label} hidden`);
                } else if (!before.visible && now.visible) {
                    setAnnouncement(`${entry.label} shown`);
                }
            }
            next.set(entry.id, now);
        }
        previous.current = next;
    }, [entries]);
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
                    visibilityToggle={visibilityToggle}
                />
            ))}
            <div
                role="status"
                aria-live="polite"
                data-testid="raster-visibility-status"
                style={{
                    position: "absolute",
                    width: 1,
                    height: 1,
                    overflow: "hidden",
                    clip: "rect(0 0 0 0)",
                    whiteSpace: "nowrap",
                }}
            >
                {announcement}
            </div>
        </SpaceBetween>
    );
};

export default RasterLayerPanel;
