/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";

// --- Schema body (vams-rules-v1) ---

export const VAMS_RULES_V1_FORMAT = "vams-rules-v1";

export type ComplianceEnforcementLevel = "quarantine" | "warn" | "inform";

export type ComplianceRuleType = "pipeline" | "metadata" | "relationship";

/**
 * Where a pipeline rule's workflow lives and which pipeline inside it produces the checked
 * output. The workflow and pipeline database ids may each be a database id or GLOBAL.
 */
export interface CompliancePipelineRef {
    databaseId: string;
    workflowId: string;
    pipelineDatabaseId: string;
    pipelineId: string;
    templateId?: string;
}

export interface ComplianceTolerance {
    operator: "lte" | "gte" | "eq" | "between";
    value?: number;
    min?: number;
    max?: number;
    epsilon?: number;
}

export interface CompliancePipelineCheck {
    name: string;
    description?: string;
    outputField: string;
    tolerance: ComplianceTolerance;
}

export interface CompliancePipelineRule {
    ruleType: "pipeline";
    enforcement: ComplianceEnforcementLevel;
    pipelineRef: CompliancePipelineRef;
    inputParameters?: Record<string, any>;
    checks: CompliancePipelineCheck[];
}

export interface ComplianceMetadataRule {
    ruleType: "metadata";
    enforcement: ComplianceEnforcementLevel;
    metadataSchemaRef: { databaseId: string; schemaName: string };
    checks: {
        name: string;
        description?: string;
        validateRequired?: boolean;
        validateTypes?: boolean;
        additionalRequiredFields?: string[];
    }[];
}

export interface ComplianceRelationshipRule {
    ruleType: "relationship";
    enforcement: ComplianceEnforcementLevel;
    checks: {
        name: string;
        description?: string;
        direction: "parents" | "children" | "related";
        relationshipType: "parentChild" | "related";
        minCount?: number;
        maxCount?: number;
    }[];
}

export type ComplianceRule =
    | CompliancePipelineRule
    | ComplianceMetadataRule
    | ComplianceRelationshipRule;

export interface VamsRulesV1SchemaBody {
    schemaFormat: typeof VAMS_RULES_V1_FORMAT;
    extends?: string;
    rules: Record<string, ComplianceRule>;
}

/** A schema body is either a vams-rules-v1 rule set or a JSON Schema (draft-07 subset). */
export type ComplianceSchemaBody = VamsRulesV1SchemaBody | Record<string, any>;

export interface ComplianceSchema {
    schemaName: string;
    description?: string;
    schemaBody: ComplianceSchemaBody;
    version?: number;
    createdAt?: string;
    updatedAt?: string;
}

export interface ComplianceState {
    databaseId: string;
    assetId: string;
    complianceState:
        | "unknown"
        | "pending_evaluation"
        | "compliant"
        | "non_compliant"
        | "quarantined"
        | "pending_parent_resolution";
    schemaName?: string;
    lastEvaluationAt?: string;
    lastEvaluationId?: string;
    schemaSource?: string;
    updatedAt?: string;
}

export type ComplianceEvaluationVerdict =
    | "compliant"
    | "non_compliant"
    | "quarantined"
    | "pending_pipeline"
    | "error";

export interface EvaluationRecord {
    evaluationId: string;
    databaseId: string;
    assetId: string;
    schemaName: string;
    result: ComplianceEvaluationVerdict;
    verdict?: ComplianceEvaluationVerdict;
    status?: "pending_pipeline" | "completed" | "error";
    violations?: string[];
    evaluatedAt: string;
    /** Workflow execution a pipeline rule launched; set once the execution has started. */
    executionId?: string;
    pipelineRuleName?: string;
}

export interface CascadeRecord {
    cascadeId: string;
    status: "pending_approval" | "approved" | "rejected" | "executing" | "completed";
    triggerAssetId: string;
    triggerDatabaseId: string;
    requireApproval: boolean;
    createdAt: string;
}

export interface AuditEntry {
    entryId: string;
    eventType: string;
    databaseId?: string;
    assetId?: string;
    schemaName?: string;
    userId: string;
    timestamp: string;
    details?: Record<string, any>;
}

// --- Schema Management ---

export const fetchComplianceSchemas = async (): Promise<[boolean, ComplianceSchema[] | string]> => {
    try {
        const response = await apiClient.get("compliance/schemas", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.schemas || response.Items || []];
    } catch (error: any) {
        console.error("fetchComplianceSchemas error:", error);
        return [false, error?.message || "Failed to fetch compliance schemas"];
    }
};

export const fetchComplianceSchema = async (
    schemaName: string
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.get(`compliance/schemas/${schemaName}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("fetchComplianceSchema error:", error);
        return [false, error?.message || "Failed to fetch compliance schema"];
    }
};

export const createComplianceSchema = async (
    schema: Omit<ComplianceSchema, "version" | "createdAt" | "updatedAt">
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.post("compliance/schemas", {
            body: schema,
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("createComplianceSchema error:", error);
        return [false, error?.message || "Failed to create compliance schema"];
    }
};

export const updateComplianceSchema = async (
    schemaName: string,
    schema: Partial<ComplianceSchema>
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.put(`compliance/schemas/${schemaName}`, {
            body: schema,
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("updateComplianceSchema error:", error);
        return [false, error?.message || "Failed to update compliance schema"];
    }
};

// --- Evaluation ---

export const evaluateAssetCompliance = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, EvaluationRecord | string]> => {
    try {
        const response = await apiClient.post(`compliance/evaluate/${databaseId}/${assetId}`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("evaluateAssetCompliance error:", error);
        return [false, error?.message || "Failed to evaluate asset compliance"];
    }
};

export const sweepSchema = async (schemaName: string): Promise<[boolean, any]> => {
    try {
        const response = await apiClient.post(`compliance/sweep/${schemaName}`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("sweepSchema error:", error);
        return [false, error?.message || "Failed to sweep schema"];
    }
};

export const fetchEvaluationHistory = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, EvaluationRecord[] | string]> => {
    try {
        const response = await apiClient.get(`compliance/evaluations/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.evaluations || response.Items || []];
    } catch (error: any) {
        console.error("fetchEvaluationHistory error:", error);
        return [false, error?.message || "Failed to fetch evaluation history"];
    }
};

export const fetchComplianceState = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, ComplianceState | string]> => {
    try {
        const response = await apiClient.get(`compliance/state/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("fetchComplianceState error:", error);
        return [false, error?.message || "Failed to fetch compliance state"];
    }
};

// --- Database Compliance Overview ---

export interface DatabaseComplianceOverview {
    databaseId: string;
    totalAssets: number;
    summary: {
        compliant: number;
        non_compliant: number;
        pending_evaluation: number;
        quarantined: number;
        unknown: number;
    };
    assets: ComplianceState[];
}

export const fetchDatabaseComplianceOverview = async (
    databaseId: string
): Promise<[boolean, DatabaseComplianceOverview | string]> => {
    try {
        const response = await apiClient.get(`compliance/state/${databaseId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("fetchDatabaseComplianceOverview error:", error);
        return [false, error?.message || "Failed to fetch database compliance overview"];
    }
};

// --- Quarantine ---

export const fetchQuarantinedAssets = async (): Promise<[boolean, any[] | string]> => {
    try {
        const response = await apiClient.get("compliance/quarantine", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.quarantinedAssets || response.assets || response.Items || []];
    } catch (error: any) {
        console.error("fetchQuarantinedAssets error:", error);
        return [false, error?.message || "Failed to fetch quarantined assets"];
    }
};

export const releaseFromQuarantine = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(
            `compliance/quarantine/${databaseId}/${assetId}/release`,
            { body: {} }
        );
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Released from quarantine"];
    } catch (error: any) {
        console.error("releaseFromQuarantine error:", error);
        return [false, error?.message || "Failed to release from quarantine"];
    }
};

export const grantException = async (
    databaseId: string,
    assetId: string,
    reason: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(
            `compliance/quarantine/${databaseId}/${assetId}/exception`,
            { body: { reason } }
        );
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Exception granted"];
    } catch (error: any) {
        console.error("grantException error:", error);
        return [false, error?.message || "Failed to grant exception"];
    }
};

// --- Cascades ---

export const fetchCascades = async (): Promise<[boolean, CascadeRecord[] | string]> => {
    try {
        const response = await apiClient.get("compliance/cascades", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.cascades || response.Items || []];
    } catch (error: any) {
        console.error("fetchCascades error:", error);
        return [false, error?.message || "Failed to fetch cascades"];
    }
};

export const approveCascade = async (cascadeId: string): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(`compliance/cascades/${cascadeId}/approve`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Cascade approved"];
    } catch (error: any) {
        console.error("approveCascade error:", error);
        return [false, error?.message || "Failed to approve cascade"];
    }
};

export const rejectCascade = async (cascadeId: string): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(`compliance/cascades/${cascadeId}/reject`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Cascade rejected"];
    } catch (error: any) {
        console.error("rejectCascade error:", error);
        return [false, error?.message || "Failed to reject cascade"];
    }
};

// --- Schema Binding ---

export const bindSchemaToDatabase = async (
    databaseId: string,
    schemaName: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.put(`compliance/bind/${databaseId}`, {
            body: { schemaName },
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema bound to database"];
    } catch (error: any) {
        console.error("bindSchemaToDatabase error:", error);
        return [false, error?.message || "Failed to bind schema to database"];
    }
};

export const unbindSchemaFromDatabase = async (databaseId: string): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.del(`compliance/bind/${databaseId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema unbound from database"];
    } catch (error: any) {
        console.error("unbindSchemaFromDatabase error:", error);
        return [false, error?.message || "Failed to unbind schema from database"];
    }
};

export const bindSchemaToAsset = async (
    databaseId: string,
    assetId: string,
    schemaName: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.put(`compliance/bind/${databaseId}/${assetId}`, {
            body: { schemaName },
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema bound to asset"];
    } catch (error: any) {
        console.error("bindSchemaToAsset error:", error);
        return [false, error?.message || "Failed to bind schema to asset"];
    }
};

export const unbindSchemaFromAsset = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.del(`compliance/bind/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema unbound from asset"];
    } catch (error: any) {
        console.error("unbindSchemaFromAsset error:", error);
        return [false, error?.message || "Failed to unbind schema from asset"];
    }
};

export const getDatabaseBindings = async (databaseId: string): Promise<[boolean, any]> => {
    try {
        const response = await apiClient.get(`compliance/bind/${databaseId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.error("getDatabaseBindings error:", error);
        return [false, error?.message || "Failed to get database bindings"];
    }
};

// --- Audit ---

export const fetchAssetAuditHistory = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, AuditEntry[] | string]> => {
    try {
        const response = await apiClient.get(`compliance/audit/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.entries || response.Items || []];
    } catch (error: any) {
        console.error("fetchAssetAuditHistory error:", error);
        return [false, error?.message || "Failed to fetch audit history"];
    }
};

export const fetchAuditLog = async (
    queryParams?: Record<string, string>
): Promise<[boolean, AuditEntry[] | string]> => {
    try {
        const response = await apiClient.get("compliance/audit", {
            queryStringParameters: queryParams || {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.entries || response.Items || []];
    } catch (error: any) {
        console.error("fetchAuditLog error:", error);
        return [false, error?.message || "Failed to fetch audit log"];
    }
};
