/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useEffect, useState, useCallback } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Alert from "@cloudscape-design/components/alert";
import Select from "@cloudscape-design/components/select";
import FormField from "@cloudscape-design/components/form-field";
import Link from "@cloudscape-design/components/link";
import { SelectProps } from "@cloudscape-design/components";
import Synonyms from "../synonyms";
import { appCache } from "../services/appCache";
import { featuresEnabled } from "../common/constants/featuresEnabled";
import { fetchAuditLog } from "../services/ComplianceService";

const eventTypeOptions: SelectProps.Option[] = [
    { label: "All Events", value: "" },
    { label: "Compliance Check", value: "compliance_check" },
    { label: "Schema Bound to Database", value: "schema_bound_to_database" },
    { label: "Schema Bound to Asset", value: "schema_bound_to_asset" },
    { label: "Schema Unbound from Database", value: "schema_unbound_from_database" },
    { label: "Schema Unbound from Asset", value: "schema_unbound_from_asset" },
    { label: "Quarantine Released", value: "quarantine_released" },
    { label: "Exception Granted", value: "exception_granted" },
    { label: "Cascade Triggered", value: "cascade_triggered" },
];

const ComplianceAuditLog: React.FC = () => {
    const config = appCache.getItem("config");
    const isComplianceEnabled = config?.featuresEnabled?.includes(featuresEnabled.COMPLIANCE);

    const [items, setItems] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedEventType, setSelectedEventType] = useState<SelectProps.Option>(
        eventTypeOptions[0]
    );

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        const params: Record<string, string> = {};
        if (selectedEventType.value) {
            params.eventType = selectedEventType.value;
        }
        const [success, result] = await fetchAuditLog(params);
        if (success && Array.isArray(result)) {
            setItems(result);
        } else {
            setError(typeof result === "string" ? result : "Failed to load audit log");
        }
        setLoading(false);
    }, [selectedEventType]);

    useEffect(() => {
        if (isComplianceEnabled) {
            loadData();
        }
    }, [isComplianceEnabled, loadData]);

    if (!isComplianceEnabled) {
        return (
            <Box padding="l">
                <Alert type="info">
                    Compliance is not enabled for this deployment.
                </Alert>
            </Box>
        );
    }

    return (
        <SpaceBetween size="l">
            {error && (
                <Alert type="error" dismissible onDismiss={() => setError(null)}>
                    {error}
                </Alert>
            )}

            <Table
                header={
                    <Header
                        variant="h1"
                        counter={`(${items.length})`}
                        actions={
                            <SpaceBetween direction="horizontal" size="xs">
                                <FormField label="">
                                    <Select
                                        selectedOption={selectedEventType}
                                        onChange={({ detail }) =>
                                            setSelectedEventType(detail.selectedOption)
                                        }
                                        options={eventTypeOptions}
                                        filteringType="auto"
                                    />
                                </FormField>
                                <Button iconName="refresh" onClick={loadData} />
                            </SpaceBetween>
                        }
                    >
                        Compliance Audit Log
                    </Header>
                }
                loading={loading}
                items={items}
                empty={
                    <Box textAlign="center" padding="l">
                        <b>No audit entries</b>
                        <Box variant="p" color="inherit">
                            No compliance audit events have been recorded.
                        </Box>
                    </Box>
                }
                columnDefinitions={[
                    {
                        id: "timestamp",
                        header: "Timestamp",
                        cell: (item: any) =>
                            item.timestamp ? new Date(item.timestamp).toLocaleString() : "-",
                        sortingField: "timestamp",
                    },
                    {
                        id: "eventType",
                        header: "Event Type",
                        cell: (item: any) => item.eventType || "-",
                        sortingField: "eventType",
                    },
                    {
                        id: "databaseId",
                        header: Synonyms.Database,
                        cell: (item: any) => item.databaseId || "-",
                        sortingField: "databaseId",
                    },
                    {
                        id: "assetId",
                        header: Synonyms.Asset,
                        cell: (item: any) =>
                            item.assetId && item.assetId !== "*" ? (
                                <Link
                                    href={`#/databases/${item.databaseId}/assets/${item.assetId}`}
                                >
                                    {item.assetId}
                                </Link>
                            ) : (
                                item.assetId || "-"
                            ),
                        sortingField: "assetId",
                    },
                    {
                        id: "schemaName",
                        header: "Schema",
                        cell: (item: any) => item.schemaName || "-",
                    },
                    {
                        id: "actor",
                        header: "Actor",
                        cell: (item: any) => item.actor || "-",
                        sortingField: "actor",
                    },
                    {
                        id: "details",
                        header: "Details",
                        cell: (item: any) => {
                            if (!item.details) return "-";
                            try {
                                const parsed =
                                    typeof item.details === "string"
                                        ? JSON.parse(item.details)
                                        : item.details;
                                return JSON.stringify(parsed);
                            } catch {
                                return item.details;
                            }
                        },
                    },
                ]}
            />
        </SpaceBetween>
    );
};

export default ComplianceAuditLog;
