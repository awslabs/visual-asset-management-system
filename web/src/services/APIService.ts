/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";
import { default as vamsConfig } from "../config";
import { ensureApiStage } from "../utils/apiEndpoint";

export const getAmplifyConfig = async () => {
    console.log("getAmplifyConfig");
    // amplify-config's ROUTE path is "/api/amplify-config"; the API also serves it under the
    // fixed "/api" stage. Two addressing modes:
    //  - Same-origin (production): the CloudFront/ALB front maps "/api/*" to the stage, so
    //    "{origin}/api/amplify-config" reaches the route correctly.
    //  - Direct DEV_API_ENDPOINT (local dev, no front): the request hits execute-api directly,
    //    so the stage must be included explicitly -> "{base}/api" + "api/amplify-config".
    //    ensureApiStage guarantees the base ends with the stage; the route is appended relative.
    let amplifyConfigUrl: URL;
    try {
        if (vamsConfig.DEV_API_ENDPOINT === "") {
            amplifyConfigUrl = new URL("/api/amplify-config", window.location.origin);
        } else {
            const stagedBase = ensureApiStage(vamsConfig.DEV_API_ENDPOINT);
            amplifyConfigUrl = new URL("api/amplify-config", stagedBase);
        }
    } catch (error: any) {
        const baseUrl = vamsConfig.DEV_API_ENDPOINT || window.location.origin;
        console.error("getAmplifyConfig: Invalid base URL", baseUrl);
        return {
            _configError: true,
            _errorMessage: `Invalid API endpoint URL: ${baseUrl}`,
            _attemptedUrl: baseUrl,
        };
    }

    console.log(amplifyConfigUrl.href);
    try {
        const response = await fetch(amplifyConfigUrl);

        if (!response.ok) {
            console.error("getAmplifyConfig: HTTP error", response.status, response.statusText);
            return {
                _configError: true,
                _errorMessage: `Unable to reach the API configuration endpoint. The server returned HTTP ${response.status} (${response.statusText}).`,
                _attemptedUrl: amplifyConfigUrl.href,
            };
        }

        const config = await response.json();

        // Validate that we got a proper config object (not an error response)
        if (!config || typeof config !== "object" || Array.isArray(config)) {
            console.error("getAmplifyConfig: Invalid config response", config);
            return {
                _configError: true,
                _errorMessage: "The API returned an invalid configuration response.",
                _attemptedUrl: amplifyConfigUrl.href,
            };
        }

        return config;
    } catch (error: any) {
        console.error("getAmplifyConfig: Fetch error", error);
        return {
            _configError: true,
            _errorMessage: `Unable to connect to the API at ${amplifyConfigUrl.href}. ${
                error?.message || "Network error or server unreachable."
            }`,
            _attemptedUrl: amplifyConfigUrl.href,
        };
    }
};

export const getSecureConfig = async () => {
    console.log("getSecureConfig");
    return apiClient.get(`secure-config`, {});
};

/**
 * Fetch the backend VAMS version from the anonymous "/api/version" endpoint.
 * This route requires no authorization, so it is fetched directly (bypassing
 * apiClient's auth header injection), mirroring getAmplifyConfig's addressing.
 * @returns {Promise<string | null>} The backend version string, or null on failure.
 */
export const getVamsVersion = async (): Promise<string | null> => {
    let versionUrl: URL;
    try {
        if (vamsConfig.DEV_API_ENDPOINT === "") {
            versionUrl = new URL("/api/version", window.location.origin);
        } else {
            const stagedBase = ensureApiStage(vamsConfig.DEV_API_ENDPOINT);
            versionUrl = new URL("api/version", stagedBase);
        }
    } catch (error: any) {
        console.log("getVamsVersion: Invalid base URL", error?.message);
        return null;
    }

    try {
        const response = await fetch(versionUrl);
        if (!response.ok) {
            console.log("getVamsVersion: HTTP error", response.status, response.statusText);
            return null;
        }
        const data = await response.json();
        return data?.version ?? null;
    } catch (error: any) {
        console.log("getVamsVersion: Fetch error", error?.message);
        return null;
    }
};

export const webRoutes = async (body: any) => {
    try {
        const response = await apiClient.post("auth/routes", {
            body: {
                routes: body.routes,
            },
        });
        // The response body carries the caller's `email` alongside their permitted routes, so it is
        // not logged. Route gating runs on every authenticated session, which put the signed-in
        // user's address and their full permission profile in the console of every session.
        return response;
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Fetch the full list of VAMS API routes (paths, methods, categories).
 * Not cached -- used by the constraints editor to offer valid route values.
 * @returns {Promise<[boolean, any]>}
 */
export const fetchApiRoutes = async () => {
    try {
        const response: any = await apiClient.get(`auth/routes/api`, {});
        if (response.message) {
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Fetch the constraint object types (with their valid fields), criteria operators,
 * permissions, and permission types. Used by the constraints editor to drive
 * object-type, field, operator, permission, and permission-type options.
 * @returns {Promise<[boolean, any]>}
 */
export const fetchConstraintPermissionObjects = async () => {
    try {
        const response: any = await apiClient.get(`auth/constraints/permissionObjects`, {});
        if (response.message) {
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Fetch the API routes (and methods) the current user is authorized to call.
 * Cached by the auth flow (see FedAuth/Auth.tsx) and periodically renewed.
 * On failure a third element carries the HTTP status when the request reached the
 * backend, so a caller can tell an absent endpoint (404) from a call that failed and
 * gate accordingly instead of treating both as "unknown".
 * @returns {Promise<[boolean, any, number|undefined]>}
 */
export const fetchAllowedApiRoutes = async () => {
    try {
        const response: any = await apiClient.get(`auth/routes/api/allowed`, {});
        if (response.message) {
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message, error?.status];
    }
};

/**
 * Returns array of boolean and response/error message for the element that the current user is downloading, or false if error.
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} [params.key] - Optional key path for the file
 * @param {string} [params.versionId] - Optional version ID
 * @param {string} [params.assetVersionId] - Optional asset version ID
 * @param {string} [params.downloadType="assetFile"] - Download type: "assetFile" (default) or "assetPreview"
 * @returns {Promise<boolean|{message}|any>}
 */
export const downloadAsset = async ({
    databaseId,
    assetId,
    key,
    versionId,
    assetVersionId = undefined,
    downloadType = "assetFile",
}: any) => {
    try {
        // Build request body with new model structure
        // Only include one version parameter — assetVersionId takes priority over versionId
        const body: any = {
            downloadType: downloadType,
            key: key,
        };
        if (assetVersionId) {
            body.assetVersionId = assetVersionId;
        } else if (versionId) {
            body.versionId = versionId;
        }

        const response = await apiClient.post(`database/${databaseId}/assets/${assetId}/download`, {
            body: body,
        });

        // Handle new response structure
        if (response.downloadUrl) {
            // New API response format
            return [true, response.downloadUrl];
        } else if (response.message) {
            // Legacy or error response format
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Check for 410 Gone status (archived file)
        if (error.status === 410) {
            return [false, "This file version has been archived and cannot be downloaded"];
        }
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the elements that the current user is deleting, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteElement = async ({ deleteRoute, elementId, item }: any) => {
    try {
        let route = deleteRoute;
        route = route.replace("{databaseId}", item?.databaseId);

        const response = await apiClient.del(route.replace(`{${elementId}}`, item[elementId]), {});
        if (response.message) {
            console.log(response.message);
            return [true, response.message, ""];
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return [false, error?.message, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the workflow that the current user is running, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const runWorkflow = async ({
    databaseId,
    assetId,
    workflowId,
    fileKey,
    isGlobalWorkflow = false,
}: any) => {
    try {
        let endpoint;
        let eventBody: any = {};
        endpoint = `database/${databaseId}/assets/${assetId}/workflows/${workflowId}`;

        if (isGlobalWorkflow) {
            eventBody = { workflowDatabaseId: "GLOBAL", fileKey: fileKey };
        } else {
            eventBody = { workflowDatabaseId: databaseId, fileKey: fileKey };
        }

        const response = await apiClient.post(endpoint, {
            body: eventBody,
        });

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, `/databases/${databaseId}/assets/${assetId}`];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the workflow that the current user is saving/updating, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const saveWorkflow = async ({ config }: any) => {
    try {
        const response = await apiClient.put("workflows", config || config.body);
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the element that the current user is creating/updating, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const createUpdateElements = async ({ pluralName, config }: any) => {
    try {
        const response = await apiClient.put(pluralName, config || config.body);
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of all databases the current user can access, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllDatabases = async () => {
    try {
        let response = await apiClient.get("database", {});
        console.log("Raw databases response:", response);

        // If response is directly an array, return it
        if (Array.isArray(response)) {
            return response;
        }

        // If response has Items property, process it
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };

        if (response && response.Items) {
            items = items.concat(response.Items);
            while (response.NextToken) {
                init["queryStringParameters"]["startingToken"] = response.NextToken;
                response = await apiClient.get("database", init);
                if (response && response.Items) {
                    items = items.concat(response.Items);
                }
            }
            return items;
        } else if (response && response.message && response.message.Items) {
            // Legacy format with message wrapper
            items = items.concat(response.message.Items);
            while (response.message.NextToken) {
                init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                response = await apiClient.get("database", init);
                if (response && response.message && response.message.Items) {
                    items = items.concat(response.message.Items);
                }
            }
            return items;
        }

        // No recognizable Items payload — surface the API message so the page can
        // show it, otherwise fall back to an empty list (genuinely zero databases).
        if (response && typeof response.message === "string" && response.message.trim() !== "") {
            console.log(response.message);
            return response.message;
        }
        return [];
    } catch (error: any) {
        console.log("Error fetching databases:", error);
        // Return the error message string so list/selector consumers can surface it
        // (ListPage renders a non-empty string via setError); an array would be
        // mistaken for data and the failure would be silent.
        return error?.message || "Failed to load databases.";
    }
};

/**
 * Returns the asset that the current user can access for the given databaseId & assetId, or false if error.
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.showArchived - Whether to include archived assets (optional)
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAsset = async ({ databaseId, assetId, showArchived = false }: any) => {
    try {
        let response;
        if (databaseId && assetId) {
            response = await apiClient.get(`database/${databaseId}/assets/${assetId}`, {
                queryStringParameters: {
                    showArchived: showArchived.toString(),
                },
            });

            // Handle the new API response structure
            // If response has a message field and it contains "error" or "Error", it's an error message
            if (
                response.message &&
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Error fetching asset:", response.message);
                return response.message;
            }

            // If response has a message field, return it (for backward compatibility)
            if (response.message) {
                return response.message;
            }

            // Otherwise, return the response directly (new API structure)
            return response;
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns the database that the current user can access for the given databaseId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabase = async ({ databaseId }: any) => {
    try {
        let response;
        if (databaseId) {
            response = await apiClient.get(`database/${databaseId}`, {});
            // Return response.message if it exists (legacy format), otherwise return response directly (new format)
            if (response.message) {
                return response.message;
            }
            return response;
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{tags}|any>}
 */
export const fetchTags = async (params?: { databaseId?: string; scope?: "global" | "all" }) => {
    try {
        const scopeParams: Record<string, any> = {};
        if (params?.databaseId) scopeParams.databaseId = params.databaseId;
        if (params?.scope) scopeParams.scope = params.scope;

        let response = await apiClient.get("tags", {
            queryStringParameters: { ...scopeParams },
        });
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null, ...scopeParams },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("tags", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns the tags selectable for an asset in a given database: the GLOBAL tags
 * plus that database's own tags, and nothing from other databases. The backend
 * serves each scope from a separate partition (a databaseId query does not
 * include GLOBAL), so this merges a global-scoped fetch with a database-scoped
 * fetch. With no databaseId it falls back to the full tag list.
 * @param {Object} params
 * @param {string} params.databaseId - The asset's database.
 * @returns {Promise<any[]|any>} Merged tag array, or a non-array error value.
 */
export const fetchTagsForAsset = async (params?: { databaseId?: string }) => {
    if (!params?.databaseId) {
        return fetchTags();
    }
    const [globalTags, databaseTags] = await Promise.all([
        fetchTags({ scope: "global" }),
        fetchTags({ databaseId: params.databaseId }),
    ]);
    // Surface a load failure (fetchTags returns a non-array on error) instead of
    // silently dropping either scope.
    if (!Array.isArray(globalTags)) return globalTags;
    if (!Array.isArray(databaseTags)) return databaseTags;
    // A tag name cannot exist as both GLOBAL and database-specific, but de-dupe by
    // name defensively so the picker never shows a duplicate label.
    const seen = new Set<string>();
    return [...globalTags, ...databaseTags].filter((tag: any) => {
        if (seen.has(tag.tagName)) return false;
        seen.add(tag.tagName);
        return true;
    });
};
/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{tagtypes}|any>}
 */
export const fetchtagTypes = async (params?: { databaseId?: string; scope?: "global" | "all" }) => {
    try {
        const scopeParams: Record<string, any> = {};
        if (params?.databaseId) scopeParams.databaseId = params.databaseId;
        if (params?.scope) scopeParams.scope = params.scope;

        let response = await apiClient.get("tag-types", {
            queryStringParameters: { ...scopeParams },
        });
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null, ...scopeParams },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("tag-types", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns the tag types that apply to an asset in a given database: the GLOBAL tag types
 * plus that database's own, and nothing from other databases.
 *
 * The asset forms use this to decide which tag types are REQUIRED. Using the unscoped list
 * demanded a selection for a tag type belonging to another database, which the scoped tag
 * picker can never satisfy — the form could not be completed. The backend applies the same
 * scope when it validates required tags on create/update.
 * @param {Object} params
 * @param {string} params.databaseId - The asset's database.
 * @returns {Promise<any[]|any>} Merged tag-type array, or a non-array error value.
 */
export const fetchTagTypesForAsset = async (params?: { databaseId?: string }) => {
    if (!params?.databaseId) {
        return fetchtagTypes();
    }
    const [globalTagTypes, databaseTagTypes] = await Promise.all([
        fetchtagTypes({ scope: "global" }),
        fetchtagTypes({ databaseId: params.databaseId }),
    ]);
    // Surface a load failure instead of silently dropping either scope.
    if (!Array.isArray(globalTagTypes)) return globalTagTypes;
    if (!Array.isArray(databaseTagTypes)) return databaseTagTypes;
    // A tag-type name cannot exist as both GLOBAL and database-specific, but de-dupe by name
    // defensively so a required type is never listed twice.
    const seen = new Set<string>();
    return [...globalTagTypes, ...databaseTagTypes].filter((tagType: any) => {
        if (seen.has(tagType.tagTypeName)) return false;
        seen.add(tagType.tagTypeName);
        return true;
    });
};

export const fetchAssetLinks = async ({ assetId, databaseId, childTreeView = false }: any) => {
    try {
        let response;
        if (assetId) {
            const queryParams: any = {};
            if (childTreeView) {
                queryParams.childTreeView = "true";
            }

            console.log("Fetching asset links with params:", queryParams);

            response = await apiClient.get(`database/${databaseId}/assets/${assetId}/asset-links`, {
                queryStringParameters: queryParams,
            });

            console.log("Raw asset links response:", response);

            // Handle response structure
            // If the response itself has the expected structure (related, parents, children)
            if (
                response &&
                typeof response === "object" &&
                response.related !== undefined &&
                response.parents !== undefined &&
                response.children !== undefined
            ) {
                return response;
            }
            // If the response has a message property that contains the data
            else if (
                response &&
                typeof response === "object" &&
                response.message &&
                typeof response.message === "object" &&
                response.message.related !== undefined &&
                response.message.parents !== undefined &&
                response.message.children !== undefined
            ) {
                return response.message;
            }
            // If the response is just a string message, treat it as an error
            // (success responses always carry the related/parents/children structure)
            else if (response && typeof response === "string") {
                console.error("Received string response:", response);
                return [false, response];
            }
            // Return the response as is, let the component handle validation
            return response;
        } else {
            return false;
        }
    } catch (error: any) {
        // Return the standard [false, message] error tuple so callers can
        // surface the failure instead of rendering an empty (success-shaped)
        // relationship tree.
        console.log("Error fetching asset links:", error);
        return [false, error?.message || "An error occurred"];
    }
};

export const deleteAssetLink = async ({ assetLinkId }: any) => {
    try {
        if (!assetLinkId) {
            return [false, "assetLinkId is required"];
        }
        const response = await apiClient.del(`asset-links/${assetLinkId}`, {});
        return [true, response?.message ?? response];
    } catch (error: any) {
        // [false, message, status] so callers can distinguish authorization
        // failures (403) from other errors.
        console.log(error);
        return [false, error?.message || "Failed to delete asset link", error?.status];
    }
};

/**
 * Returns array of all subscription constraints from the auth/constraints api
 * @returns {Promise<boolean|{rules}|any>}
 */
export const fetchSubscriptionRules = async () => {
    try {
        let response = await apiClient.get("subscriptions", {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("subscriptions", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all roles
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchRoles = async () => {
    try {
        let response = await apiClient.get("roles", {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("roles", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all users assigned to roles
 * @returns {Promise<boolean|{userroles}|any>}
 */
export const fetchUserRoles = async () => {
    try {
        let response = await apiClient.get("user-roles", {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("user-roles", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{constraints}|any>}
 */
export const fetchConstraints = async () => {
    try {
        let response = await apiClient.get("auth/constraints", {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("auth/constraints", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all Cognito users
 * @returns {Promise<Array|boolean>}
 */
export const fetchCognitoUsers = async () => {
    try {
        let response = await apiClient.get("user/cognito");
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };

        // Handle direct response with users array
        if (response.users && Array.isArray(response.users)) {
            items = items.concat(response.users);
            while (response.nextToken) {
                init["queryStringParameters"]["startingToken"] = response.nextToken;
                response = await apiClient.get("user/cognito", init);
                if (response.users) {
                    items = items.concat(response.users);
                }
            }
            return items;
        }
        // Handle legacy response format with message wrapper
        else if (response.message) {
            if (response.message.users && Array.isArray(response.message.users)) {
                items = items.concat(response.message.users);
                while (response.message.nextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.nextToken;
                    response = await apiClient.get("user/cognito", init);
                    if (response.message && response.message.users) {
                        items = items.concat(response.message.users);
                    }
                }
                return items;
            } else if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("user/cognito", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Extract the actual error message from the API response
        const errorMessage =
            error?.response?.data?.message || error?.message || "An error occurred";
        return errorMessage;
    }
};

/**
 * Creates a new Cognito user
 * @param {Object} params - Parameters object
 * @param {string} params.userId - User ID
 * @param {string} params.email - Email address
 * @param {string} params.phone - Phone number (optional, E.164 format)
 * @returns {Promise<[boolean, string]>}
 */
export const createCognitoUser = async ({ userId, email, phone }: any) => {
    try {
        const body: any = { userId, email };
        if (phone) {
            body.phone = phone;
        }

        const response = await apiClient.post("user/cognito", { body });

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Extract the actual error message from the API response
        const errorMessage =
            error?.response?.data?.message || error?.message || "An error occurred";
        return [false, errorMessage];
    }
};

/**
 * Updates an existing Cognito user
 * @param {Object} params - Parameters object
 * @param {string} params.userId - User ID
 * @param {string} params.email - Email address (optional)
 * @param {string} params.phone - Phone number (optional, E.164 format)
 * @returns {Promise<[boolean, string]>}
 */
export const updateCognitoUser = async ({ userId, email, phone }: any) => {
    try {
        const body: any = {};
        if (email) body.email = email;
        if (phone) body.phone = phone;

        const response = await apiClient.put(`user/cognito/${userId}`, { body });

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Extract the actual error message from the API response
        const errorMessage =
            error?.response?.data?.message || error?.message || "An error occurred";
        return [false, errorMessage];
    }
};

/**
 * Deletes a Cognito user
 * @param {Object} params - Parameters object
 * @param {string} params.userId - User ID
 * @returns {Promise<[boolean, string]>}
 */
export const deleteCognitoUser = async ({ userId }: any) => {
    try {
        const response = await apiClient.del(`user/cognito/${userId}`);

        if (response.message) {
            console.log(response.message);
            return [true, response.message];
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Extract the actual error message from the API response
        const errorMessage =
            error?.response?.data?.message || error?.message || "An error occurred";
        return [false, errorMessage];
    }
};

/**
 * Resets a Cognito user's password
 * @param {Object} params - Parameters object
 * @param {string} params.userId - User ID
 * @param {boolean} params.confirmReset - Confirmation of the reset; the endpoint rejects the
 *     request unless this is true
 * @returns {Promise<[boolean, string]>}
 */
export const resetCognitoUserPassword = async ({ userId, confirmReset }: any) => {
    try {
        const response = await apiClient.post(`user/cognito/${userId}/resetPassword`, {
            body: { userId, confirmReset: confirmReset === true },
        });

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        // Extract the actual error message from the API response
        const errorMessage =
            error?.response?.data?.message || error?.message || "An error occurred";
        return [false, errorMessage];
    }
};

/**
 * Returns array of all the comments that are attached to a given assetId
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllComments = async ({ assetId }: any) => {
    try {
        let response = await apiClient.get(`comments/assets/${assetId}`, {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get(`comments/assets/${assetId}`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Deletes the given comment from the database
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteComment = async ({ assetId, assetVersionIdAndCommentId }: any) => {
    try {
        const response = await apiClient.del(
            `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
            {}
        );
        if (response.message) {
            console.log(response.message);
            return [true, response.message];
        } else {
            console.log(response);
            return false;
        }
    } catch (error: any) {
        if (error.status === 403) return error.status;
        return [false, error?.message];
    }
};

/**
 * Returns array of all assets the current user can access for a given database, or empty array if error.
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {boolean} params.showArchived - Whether to include archived assets (optional)
 * @param {number} params.maxItems - Maximum items to retrieve per request (optional, default 1000, max 1000)
 * @param {number} params.pageSize - Page size for pagination (optional, default 1000)
 * @param {string} params.startingToken - Pagination token (optional)
 * @returns {Promise<Array>} Array of assets or empty array on error
 */
export const fetchDatabaseAssets = async ({
    databaseId,
    showArchived = false,
    maxItems = 1000,
    pageSize = 1000,
    startingToken = null,
}: any) => {
    try {
        if (!databaseId) {
            return [];
        }

        const queryParams: Record<string, any> = {
            showArchived: showArchived.toString(),
            maxItems: maxItems.toString(),
            pageSize: pageSize.toString(),
        };

        if (startingToken) {
            queryParams.startingToken = startingToken;
        }

        let response = await apiClient.get(`database/${databaseId}/assets`, {
            queryStringParameters: queryParams,
        });

        let items: any[] = [];

        // Handle legacy response format with message wrapper
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    queryParams.startingToken = response.message.NextToken;
                    response = await apiClient.get(`database/${databaseId}/assets`, {
                        queryStringParameters: queryParams,
                    });
                    if (response.message && response.message.Items) {
                        items = items.concat(response.message.Items);
                    }
                }
                return items;
            }
            // If message exists but no Items, return empty array
            return [];
        }
        // Handle new API format with direct Items property
        else if (response.Items) {
            items = items.concat(response.Items);
            while (response.NextToken) {
                queryParams.startingToken = response.NextToken;
                response = await apiClient.get(`database/${databaseId}/assets`, {
                    queryStringParameters: queryParams,
                });
                if (response.Items) {
                    items = items.concat(response.Items);
                }
            }
            return items;
        }

        return [];
    } catch (error: any) {
        console.log("Error fetching database assets:", error);
        return [];
    }
};

/**
 * Returns array of all assets the current user can access for all databases, or empty array if error.
 * @param {Object} params - Parameters object (optional)
 * @param {boolean} params.showArchived - Whether to include archived assets (optional)
 * @param {number} params.maxItems - Maximum items to retrieve per request (optional, default 1000, max 1000)
 * @param {number} params.pageSize - Page size for pagination (optional, default 1000)
 * @param {string} params.startingToken - Pagination token (optional)
 * @returns {Promise<Array>} Array of assets or empty array on error
 */
export const fetchAllAssets = async ({
    showArchived = false,
    maxItems = 1000,
    pageSize = 1000,
    startingToken = null,
}: any = {}) => {
    try {
        const queryParams: Record<string, any> = {
            showArchived: showArchived.toString(),
            maxItems: maxItems.toString(),
            pageSize: pageSize.toString(),
        };

        if (startingToken) {
            queryParams.startingToken = startingToken;
        }

        let response = await apiClient.get(`assets`, {
            queryStringParameters: queryParams,
        });

        let items: any[] = [];

        // Handle legacy response format with message wrapper
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    queryParams.startingToken = response.message.NextToken;
                    response = await apiClient.get(`assets`, {
                        queryStringParameters: queryParams,
                    });
                    if (response.message && response.message.Items) {
                        items = items.concat(response.message.Items);
                    }
                }
                return items;
            }
            // If message exists but no Items, return empty array
            return [];
        }
        // Handle new API format with direct Items property
        else if (response.Items) {
            items = items.concat(response.Items);
            while (response.NextToken) {
                queryParams.startingToken = response.NextToken;
                response = await apiClient.get(`assets`, {
                    queryStringParameters: queryParams,
                });
                if (response.Items) {
                    items = items.concat(response.Items);
                }
            }
            return items;
        }

        return [];
    } catch (error: any) {
        console.log("Error fetching all assets:", error);
        return [];
    }
};

/**
 * Returns array of all pipelines the current user can access for all databases, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllPipelines = async () => {
    try {
        let response = await apiClient.get(`pipelines`, {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get(`pipelines`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all pipelines the current user can access for a given database, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabasePipelines = async ({ databaseId }: any) => {
    try {
        let response;
        // If databaseId is undefined, return false
        if (databaseId === undefined) {
            console.log("not fetching pipelines");
            return false;
        }

        response = await apiClient.get(`database/${databaseId}/pipelines`, {});

        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get(`database/${databaseId}/pipelines`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflows the current user can access for a given database, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabaseWorkflows = async ({ databaseId }: any) => {
    try {
        let response;
        // If databaseId is undefined, return false
        if (databaseId === undefined) {
            console.log("not fetching workflows");
            return false;
        }

        response = await apiClient.get(`database/${databaseId}/workflows`, {});

        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get(`database/${databaseId}/workflows`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflows the current user can access for all databases, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllWorkflows = async () => {
    try {
        let response = await apiClient.get(`workflows`, {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get(`workflows`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflow executions the current user can access for the given databaseId & assetId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchWorkflowExecutions = async ({ databaseId, assetId, workflowId = "" }: any) => {
    try {
        let response;
        let endpoint;

        if (assetId) {
            // Determine the endpoint based on whether it's a global workflow
            if (workflowId == "") {
                endpoint = `database/${databaseId}/assets/${assetId}/workflows/executions`;
            } else {
                endpoint = `database/${databaseId}/assets/${assetId}/workflows/executions/${workflowId}`;
            }

            response = await apiClient.get(endpoint, {});
            let items: any[] = [];
            const init: { queryStringParameters: Record<string, any> } = {
                queryStringParameters: { startingToken: null },
            };
            if (response.message) {
                if (response.message.Items) {
                    items = items.concat(response.message.Items);
                    while (response.message.NextToken) {
                        init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                        response = await apiClient.get(endpoint, init);
                        items = items.concat(response.message.Items);
                    }
                    return items;
                } else {
                    return response.message;
                }
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all metadata fields from the backend
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchAllMetadataSchema = async () => {
    try {
        let response = await apiClient.get("metadataschema/", {});
        let items: any[] = [];
        const init: { queryStringParameters: Record<string, any> } = {
            queryStringParameters: { startingToken: null },
        };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await apiClient.get("metadataschema/", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of  metadata fields from the backend for a particular databaseId
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchDatabaseMetadataSchema = async ({ databaseId }: any) => {
    try {
        let response;
        if (databaseId) {
            response = await apiClient.get(`metadataschema/${databaseId}`, {});
            let items: any[] = [];
            const init: { queryStringParameters: Record<string, any> } = {
                queryStringParameters: { startingToken: null },
            };
            if (response.message) {
                if (response.message.Items) {
                    items = items.concat(response.message.Items);
                    while (response.message.NextToken) {
                        init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                        response = await apiClient.get(`metadataschema/${databaseId}`, init);
                        items = items.concat(response.message.Items);
                    }
                    return items;
                } else {
                    return response.message;
                }
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return error?.message;
    }
};

/** add in the columnar data loaders **/
/**
 * Creates a new folder in the specified asset
 * @returns {Promise<boolean|{message}|any>}
 */
export const createFolder = async ({ databaseId, assetId, relativeKey }: any) => {
    try {
        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/createFolder`,
            {
                body: { relativeKey },
            }
        );

        if (response.message) {
            return [true, response.message];
        } else {
            return false;
        }
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Reverts a file to a specific version by creating a new current version with the contents of the specified version
 * @returns {Promise<boolean|{message}|any>}
 */
export const revertFileVersion = async ({ databaseId, assetId, filePath, versionId }: any) => {
    try {
        if (!databaseId || !assetId || !filePath || !versionId) {
            return [false, "Missing required parameters"];
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/revertFileVersion/${versionId}`,
            {
                body: { filePath },
            }
        );

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log("Revert error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error reverting file version:", error);
        return [false, error?.message || "Failed to revert file version"];
    }
};

/**
 * Updates an asset with new properties
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {Object} params.updateData - Data to update (assetName, description, isDistributable, tags)
 * @returns {Promise<boolean|{message}|any>}
 */
export const updateAsset = async ({ databaseId, assetId, updateData }: any) => {
    try {
        if (!databaseId || !assetId || !updateData) {
            return [false, "Missing required parameters"];
        }

        const response = await apiClient.put(`database/${databaseId}/assets/${assetId}`, {
            body: updateData,
        });

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Update asset error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error updating asset:", error);
        return [false, error?.message || "Failed to update asset"];
    }
};

/**
 * Archives an asset (soft delete)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.confirmArchive - Confirmation flag (required)
 * @param {string} params.reason - Optional reason for archiving
 * @returns {Promise<boolean|{message}|any>}
 */
export const archiveAsset = async ({
    databaseId,
    assetId,
    confirmArchive = true,
    reason = "",
}: any) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        if (!confirmArchive) {
            return [false, "Archive operation must be confirmed"];
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/archiveAsset`,
            {
                body: {
                    confirmArchive,
                    reason,
                },
            }
        );

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Archive asset error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error archiving asset:", error);
        return [false, error?.message || "Failed to archive asset"];
    }
};

/**
 * Permanently deletes an asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.confirmPermanentDelete - Confirmation flag (required)
 * @param {string} params.reason - Optional reason for deletion
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteAssetPermanent = async ({
    databaseId,
    assetId,
    confirmPermanentDelete = false,
    reason = "",
}: any) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        if (!confirmPermanentDelete) {
            return [false, "Permanent deletion requires explicit confirmation"];
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/deleteAsset`,
            {
                body: {
                    confirmPermanentDelete,
                    reason,
                },
            }
        );

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Delete asset error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error deleting asset:", error);
        return [false, error?.message || "Failed to delete asset"];
    }
};

/**
 * Returns array of all buckets the current user can access, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchBuckets = async () => {
    try {
        const response = await apiClient.get("buckets", {});
        console.log("Raw buckets response:", response);

        // Direct return of the response which should contain Items array
        return response;
    } catch (error: any) {
        console.log("Error fetching buckets:", error);
        return { Items: [], error: error?.message };
    }
};

/**
 * Creates a new database
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.description - Database description
 * @param {string} params.defaultBucketId - Default bucket ID
 * @param {boolean} params.restrictMetadataOutsideSchemas - Restrict metadata to schemas only
 * @param {string} params.restrictFileUploadsToExtensions - Comma-delimited file extensions
 * @returns {Promise<boolean|{message}|any>}
 */
export const createDatabase = async ({
    databaseId,
    description,
    defaultBucketId,
    restrictMetadataOutsideSchemas = false,
    restrictFileUploadsToExtensions = "",
}: any) => {
    try {
        const response = await apiClient.post("database", {
            body: {
                databaseId,
                description,
                defaultBucketId,
                restrictMetadataOutsideSchemas,
                restrictFileUploadsToExtensions,
            },
        });

        if (response.message) {
            console.log("create database", response);
            return [true, response.message];
        } else {
            return false;
        }
    } catch (error: any) {
        console.log("create database error", error);
        return [false, error?.message];
    }
};

/**
 * Updates an existing database
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.description - Database description
 * @param {string} params.defaultBucketId - Default bucket ID
 * @param {boolean} params.restrictMetadataOutsideSchemas - Restrict metadata to schemas only
 * @param {string} params.restrictFileUploadsToExtensions - Comma-delimited file extensions
 * @returns {Promise<boolean|{message}|any>}
 */
export const updateDatabase = async ({
    databaseId,
    description,
    defaultBucketId,
    restrictMetadataOutsideSchemas,
    restrictFileUploadsToExtensions,
}: any) => {
    try {
        const response = await apiClient.put(`database/${databaseId}`, {
            body: {
                description,
                defaultBucketId,
                restrictMetadataOutsideSchemas,
                restrictFileUploadsToExtensions,
            },
        });

        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log("update database error:", response.message);
                return [false, response.message];
            } else {
                console.log("update database", response);
                return [true, response.message];
            }
        } else {
            return false;
        }
    } catch (error: any) {
        console.log("update database error", error);
        return [false, error?.message];
    }
};

/**
 * Fetches every page of a paged metadata GET and aggregates the records.
 *
 * The metadata GET APIs return one page of records plus an optional NextToken.
 * This helper follows NextToken to completion so callers receive the full
 * metadata set, then normalizes the various response shapes the metadata
 * endpoints can return into a single `{ metadata: [...] }` object.
 *
 * @param {string} endpoint - API path (relative to the apiClient base).
 * @param {Object} baseQuery - Query string parameters to send on every request.
 * @returns {Promise<{ metadata: any[], message?: string }>}
 */
const fetchAllMetadataPages = async (endpoint: string, baseQuery: Record<string, any> = {}) => {
    let allMetadata: any[] = [];
    let nextToken: string | null = null;
    let lastMessage: any;

    do {
        const queryStringParameters: Record<string, any> = {
            ...baseQuery,
            ...(nextToken ? { startingToken: nextToken } : {}),
        };
        const response = await apiClient.get(endpoint, { queryStringParameters });

        // Normalize the response into a payload object that holds `metadata`.
        let payload: any = null;
        if (response && typeof response === "object") {
            if (Array.isArray(response.metadata)) {
                payload = response;
            } else if (
                response.message &&
                typeof response.message === "object" &&
                Array.isArray(response.message.metadata)
            ) {
                payload = response.message;
            } else if (response.message && typeof response.message === "string") {
                return { metadata: [], message: response.message };
            }
        } else if (typeof response === "string") {
            return { metadata: [], message: response };
        }

        if (!payload) {
            return { metadata: [], message: "Unknown response format" };
        }

        allMetadata = allMetadata.concat(payload.metadata || []);
        // Preserve any non-metadata fields (e.g. restrictMetadataOutsideSchemas) from the last page.
        lastMessage = payload;
        nextToken = payload.NextToken || null;
    } while (nextToken);

    return { ...lastMessage, metadata: allMetadata, NextToken: undefined };
};

/**
 * Fetches metadata for an asset link
 * @param {Object} params - Parameters object
 * @param {string} params.assetLinkId - Asset link ID
 * @returns {Promise<any>}
 */
export const fetchAssetLinkMetadata = async ({ assetLinkId }: any) => {
    try {
        if (!assetLinkId) {
            return false;
        }

        // Follow NextToken to aggregate all pages of metadata.
        return await fetchAllMetadataPages(`asset-links/${assetLinkId}/metadata`);
    } catch (error: any) {
        console.log("Error fetching asset link metadata:", error);
        return { metadata: [], message: error?.message || "Error fetching metadata" };
    }
};

/**
 * Creates metadata for an asset link
 * @param {Object} params - Parameters object
 * @param {string} params.assetLinkId - Asset link ID
 * @param {string} params.metadataKey - Metadata key
 * @param {string} params.metadataValue - Metadata value
 * @param {string} params.metadataValueType - Metadata value type ('XYZ' or 'String')
 * @returns {Promise<any>}
 */
export const createAssetLinkMetadata = async ({
    assetLinkId,
    metadataKey,
    metadataValue,
    metadataValueType,
}: any) => {
    try {
        if (!assetLinkId || !metadataKey || !metadataValue || !metadataValueType) {
            return [false, "Missing required parameters"];
        }

        // The collection route takes a bulk body; this wraps the single item in it.
        const response = await apiClient.post(`asset-links/${assetLinkId}/metadata`, {
            body: {
                metadata: [{ metadataKey, metadataValue, metadataValueType }],
            },
        });

        if (response.message) {
            if (
                response.success === false ||
                (response.message.indexOf &&
                    (response.message.indexOf("error") !== -1 ||
                        response.message.indexOf("Error") !== -1))
            ) {
                console.log("Create asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error creating asset link metadata:", error);
        return [false, error?.message || "Failed to create metadata"];
    }
};

/**
 * Updates metadata for an asset link
 * @param {Object} params - Parameters object
 * @param {string} params.assetLinkId - Asset link ID
 * @param {string} params.metadataKey - Metadata key
 * @param {string} params.metadataValue - Metadata value
 * @param {string} params.metadataValueType - Metadata value type ('XYZ' or 'String')
 * @returns {Promise<any>}
 */
export const updateAssetLinkMetadata = async ({
    assetLinkId,
    metadataKey,
    metadataValue,
    metadataValueType,
}: any) => {
    try {
        if (!assetLinkId || !metadataKey || !metadataValue || !metadataValueType) {
            return [false, "Missing required parameters"];
        }

        // The metadata key travels in the bulk body, not the path: the collection route
        // carries all four verbs and there is no per-key sub-path.
        const response = await apiClient.put(`asset-links/${assetLinkId}/metadata`, {
            body: {
                metadata: [{ metadataKey, metadataValue, metadataValueType }],
            },
        });

        if (response.message) {
            if (
                response.success === false ||
                (response.message.indexOf &&
                    (response.message.indexOf("error") !== -1 ||
                        response.message.indexOf("Error") !== -1))
            ) {
                console.log("Update asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error updating asset link metadata:", error);
        return [false, error?.message || "Failed to update metadata"];
    }
};

/**
 * Deletes metadata for an asset link
 * @param {Object} params - Parameters object
 * @param {string} params.assetLinkId - Asset link ID
 * @param {string} params.metadataKey - Metadata key
 * @returns {Promise<any>}
 */
export const deleteAssetLinkMetadata = async ({ assetLinkId, metadataKey }: any) => {
    try {
        if (!assetLinkId || !metadataKey) {
            return [false, "Missing required parameters"];
        }

        // Keys to delete travel in the body, not the path, on the same collection route.
        const response = await apiClient.del(`asset-links/${assetLinkId}/metadata`, {
            body: {
                metadataKeys: [metadataKey],
            },
        });

        if (response.message) {
            if (
                response.success === false ||
                (response.message.indexOf &&
                    (response.message.indexOf("error") !== -1 ||
                        response.message.indexOf("Error") !== -1))
            ) {
                console.log("Delete asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error deleting asset link metadata:", error);
        return [false, error?.message || "Failed to delete metadata"];
    }
};

/**
 * Sets or removes primary type metadata for a file
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @param {string} params.primaryType - Primary type value (empty string to remove)
 * @param {string} params.primaryTypeOther - Custom primary type when primaryType is 'other'
 * @returns {Promise<any>}
 */
export const setPrimaryType = async ({
    databaseId,
    assetId,
    filePath,
    primaryType,
    primaryTypeOther,
}: any) => {
    try {
        if (!databaseId || !assetId || !filePath) {
            return [false, "Missing required parameters"];
        }

        const response = await apiClient.put(
            `database/${databaseId}/assets/${assetId}/setPrimaryFile`,
            {
                body: {
                    filePath,
                    primaryType: primaryType || "",
                    primaryTypeOther: primaryTypeOther || null,
                },
            }
        );

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Set primary type error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else if (response.success) {
            return [true, response.message || "Primary type updated successfully"];
        } else {
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.log("Error setting primary type:", error);
        return [false, error?.message || "Failed to set primary type"];
    }
};

/**
 * Fetches all files in S3 for an asset (for version creation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.includeArchived - Whether to include archived files
 * @param {boolean} params.basic - Whether to just basic file information or detailed information (basic is much faster)
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssetS3Files = async ({
    databaseId,
    assetId,
    includeArchived = false,
    basic = false,
}: any) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        const queryParams: Record<string, any> = {
            includeArchived: includeArchived.toString(),
        };

        if (basic) {
            queryParams.basic = basic.toString();
        }

        const response = await apiClient.get(`database/${databaseId}/assets/${assetId}/listFiles`, {
            queryStringParameters: queryParams,
        });

        console.log("fetchAssetS3Files raw response:", JSON.stringify(response, null, 2));

        // Handle direct response format (new API format)
        if (response && response.items) {
            let items = response.items;

            // Handle pagination if needed
            let nextToken = response.NextToken;
            while (nextToken) {
                const nextResponse = await apiClient.get(
                    `database/${databaseId}/assets/${assetId}/listFiles`,
                    {
                        queryStringParameters: {
                            includeArchived: includeArchived.toString(),
                            startingToken: nextToken,
                        },
                    }
                );

                if (nextResponse && nextResponse.items) {
                    items = items.concat(nextResponse.items);
                    nextToken = nextResponse.NextToken;
                } else {
                    break;
                }
            }
            return [true, items];
        }
        // Handle legacy response format with message wrapper
        else if (response.message) {
            let items: any[] = [];
            if (response.message.Items) {
                items = response.message.Items;

                // Handle pagination if needed
                let nextToken = response.message.NextToken;
                while (nextToken) {
                    const nextResponse = await apiClient.get(
                        `database/${databaseId}/assets/${assetId}/listFiles`,
                        {
                            queryStringParameters: {
                                includeArchived: includeArchived.toString(),
                                startingToken: nextToken,
                            },
                        }
                    );

                    if (nextResponse.message && nextResponse.message.Items) {
                        items = items.concat(nextResponse.message.Items);
                        nextToken = nextResponse.message.NextToken;
                    } else {
                        break;
                    }
                }
            }
            return [true, items];
        } else {
            console.error("Unexpected response format:", response);
            return [false, "No response received"];
        }
    } catch (error: any) {
        console.error("Error fetching asset S3 files:", error);
        return [false, error?.message || "Failed to fetch asset files"];
    }
};

/**
 * Fetches a single page of files from S3 for an asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.includeArchived - Whether to include archived files
 * @param {boolean} params.basic - Whether to use basic mode (faster, less data)
 * @param {string|null} params.startingToken - Pagination token
 * @param {number} params.pageSize - Page size (default: 1500 for basic, 100 for detailed)
 * @param {string|null} [params.assetVersionId] - Asset version ID to filter files (optional)
 * @returns {Promise<{success: boolean, items: Array, nextToken: string|null, error: string|null}>}
 */
export const fetchAssetS3FilesPage = async ({
    databaseId,
    assetId,
    includeArchived = false,
    basic = false,
    startingToken = null,
    pageSize = null,
    assetVersionId = null,
}: any) => {
    try {
        if (!databaseId || !assetId) {
            return {
                success: false,
                items: [],
                nextToken: null,
                error: "Database ID and Asset ID are required",
            };
        }

        // Set default page size based on mode
        const defaultPageSize = basic ? 1500 : 100;
        const actualPageSize = pageSize || defaultPageSize;

        const queryParams: Record<string, any> = {
            includeArchived: includeArchived.toString(),
            basic: basic.toString(),
            pageSize: actualPageSize.toString(),
        };

        if (startingToken) {
            queryParams.startingToken = startingToken;
        }

        if (assetVersionId) {
            queryParams.assetVersionId = assetVersionId;
        }

        const response = await apiClient.get(`database/${databaseId}/assets/${assetId}/listFiles`, {
            queryStringParameters: queryParams,
        });

        console.log(
            `fetchAssetS3FilesPage (basic=${basic}, page=${startingToken ? "next" : "first"}):`,
            response?.items?.length || 0,
            "items"
        );

        // Handle direct response format (new API format)
        if (response && response.items) {
            return {
                success: true,
                items: response.items,
                nextToken: response.NextToken || null,
                error: null,
            };
        }
        // Handle legacy response format with message wrapper
        else if (response.message) {
            if (response.message.Items) {
                return {
                    success: true,
                    items: response.message.Items,
                    nextToken: response.message.NextToken || null,
                    error: null,
                };
            }
        }

        return {
            success: false,
            items: [],
            nextToken: null,
            error: "Unexpected response format",
        };
    } catch (error: any) {
        console.error("Error fetching asset S3 files page:", error);
        return {
            success: false,
            items: [],
            nextToken: null,
            error: error?.message || "Failed to fetch asset files page",
        };
    }
};

/**
 * Async generator that yields pages of files as they're fetched
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {boolean} params.includeArchived - Whether to include archived files
 * @param {boolean} params.basic - Whether to use basic mode
 * @param {number} [params.pageSize] - Page size (optional)
 * @param {string|null} [params.assetVersionId] - Asset version ID to filter files (optional)
 * @yields {Object} Page result with items and metadata
 */
export async function* fetchAssetS3FilesStreaming({
    databaseId,
    assetId,
    includeArchived = false,
    basic = false,
    pageSize,
    assetVersionId = null,
}: {
    databaseId: string;
    assetId: string;
    includeArchived?: boolean;
    basic?: boolean;
    pageSize?: any;
    assetVersionId?: string | null | undefined;
}) {
    let nextToken = null;
    let pageNumber = 0;

    do {
        pageNumber++;
        const result = await fetchAssetS3FilesPage({
            databaseId,
            assetId,
            includeArchived,
            basic,
            startingToken: nextToken,
            pageSize,
            assetVersionId,
        });

        if (!result.success) {
            yield {
                success: false,
                items: [],
                nextToken: null,
                error: result.error,
                pageNumber,
                isLastPage: true,
            };
            break;
        }

        nextToken = result.nextToken;
        const isLastPage = !nextToken;

        yield {
            success: true,
            items: result.items,
            nextToken,
            error: null,
            pageNumber,
            isLastPage,
        };
    } while (nextToken);
}

/**
 * Fetches file information for a specific file in an asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.fileKey - File key/path
 * @param {boolean} params.includeVersions - If to include file version data on the response
 * @returns {Promise<any>}
 */
export const fetchFileInfo = async ({
    databaseId,
    assetId,
    fileKey,
    includeVersions = false,
}: any) => {
    try {
        if (!databaseId || !assetId || !fileKey) {
            return [false, "Missing required parameters"];
        }

        const response = await apiClient.get(`database/${databaseId}/assets/${assetId}/fileInfo`, {
            queryStringParameters: {
                filePath: fileKey,
                includeVersions: includeVersions ? "true" : "false",
            },
        });

        // Handle different response formats
        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Fetch file info error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            // Direct response format
            return [true, response];
        }
    } catch (error: any) {
        console.log("Error fetching file info:", error);
        return [false, error?.message || "Failed to fetch file information"];
    }
};

/**
 * Fetches the lifecycle history records for an asset (paged)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {number} params.pageSize - Records per page
 * @param {string} params.startingToken - Continuation token from a prior page
 * @returns {Promise<any>}
 */
export const fetchAssetHistory = async ({ databaseId, assetId, pageSize, startingToken }: any) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Missing required parameters"];
        }

        const queryStringParameters: any = {};
        if (pageSize) {
            queryStringParameters.pageSize = `${pageSize}`;
        }
        if (startingToken) {
            queryStringParameters.startingToken = startingToken;
        }

        const response = await apiClient.get(
            `database/${databaseId}/assets/${assetId}/assetHistory`,
            { queryStringParameters }
        );

        if (response.Items) {
            return [true, response];
        } else if (response.message) {
            console.log("Fetch asset history error:", response.message);
            return [false, response.message];
        } else {
            return [false, "Unknown error fetching asset history"];
        }
    } catch (error: any) {
        console.log("Error fetching asset history:", error);
        return [false, error?.message || "Failed to fetch asset history"];
    }
};

//=============================================================================
// METADATA V2 API FUNCTIONS - Bulk Operations for All Entity Types
//=============================================================================

/**
 * Fetches metadata for an asset (bulk operation with schema enrichment)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @returns {Promise<any>}
 */
export const fetchAssetMetadata = async ({ databaseId, assetId }: any) => {
    try {
        if (!databaseId || !assetId) {
            return { metadata: [], message: "Missing required parameters" };
        }

        // Follow NextToken to aggregate all pages of metadata.
        return await fetchAllMetadataPages(`database/${databaseId}/assets/${assetId}/metadata`);
    } catch (error: any) {
        console.log("Error fetching asset metadata:", error);
        return { metadata: [], message: error?.message || "Error fetching metadata" };
    }
};

/**
 * Creates metadata for an asset (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {Array} params.metadata - Array of metadata items {metadataKey, metadataValue, metadataValueType}
 * @returns {Promise<any>}
 */
export const createAssetMetadata = async ({ databaseId, assetId, metadata }: any) => {
    try {
        if (!databaseId || !assetId || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.post(`database/${databaseId}/assets/${assetId}/metadata`, {
            body: { metadata },
        });

        console.log("createAssetMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error creating asset metadata:", error);
        throw error;
    }
};

/**
 * Updates metadata for an asset (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {Array} params.metadata - Array of metadata items
 * @param {string} params.updateType - 'update' or 'replace_all'
 * @returns {Promise<any>}
 */
export const updateAssetMetadata = async ({
    databaseId,
    assetId,
    metadata,
    updateType = "update",
}: any) => {
    try {
        if (!databaseId || !assetId || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.put(`database/${databaseId}/assets/${assetId}/metadata`, {
            body: { metadata, updateType },
        });

        console.log("updateAssetMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error updating asset metadata:", error);
        throw error;
    }
};

/**
 * Deletes metadata for an asset (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {Array} params.metadataKeys - Array of metadata keys to delete
 * @returns {Promise<any>}
 */
export const deleteAssetMetadata = async ({ databaseId, assetId, metadataKeys }: any) => {
    try {
        if (!databaseId || !assetId || !metadataKeys) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.del(`database/${databaseId}/assets/${assetId}/metadata`, {
            body: { metadataKeys },
        });

        console.log("deleteAssetMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error deleting asset metadata:", error);
        throw error;
    }
};

/**
 * Fetches metadata for a file (bulk operation with schema enrichment)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @param {string} params.type - 'metadata' or 'attribute'
 * @returns {Promise<any>}
 */
export const fetchFileMetadata = async ({ databaseId, assetId, filePath, type }: any) => {
    try {
        if (!databaseId || !assetId || !filePath || !type) {
            return { metadata: [], message: "Missing required parameters" };
        }

        // Follow NextToken to aggregate all pages of metadata.
        return await fetchAllMetadataPages(
            `database/${databaseId}/assets/${assetId}/metadata/file`,
            { filePath, type }
        );
    } catch (error: any) {
        console.log("Error fetching file metadata:", error);
        return { metadata: [], message: error?.message || "Error fetching metadata" };
    }
};

/**
 * Creates metadata for a file (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @param {string} params.type - 'metadata' or 'attribute'
 * @param {Array} params.metadata - Array of metadata items
 * @returns {Promise<any>}
 */
export const createFileMetadata = async ({
    databaseId,
    assetId,
    filePath,
    type,
    metadata,
}: any) => {
    try {
        if (!databaseId || !assetId || !filePath || !type || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.post(
            `database/${databaseId}/assets/${assetId}/metadata/file`,
            {
                body: { filePath, type, metadata },
            }
        );

        console.log("createFileMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error creating file metadata:", error);
        throw error;
    }
};

/**
 * Updates metadata for a file (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @param {string} params.type - 'metadata' or 'attribute'
 * @param {Array} params.metadata - Array of metadata items
 * @param {string} params.updateType - 'update' or 'replace_all'
 * @returns {Promise<any>}
 */
export const updateFileMetadata = async ({
    databaseId,
    assetId,
    filePath,
    type,
    metadata,
    updateType = "update",
}: any) => {
    try {
        if (!databaseId || !assetId || !filePath || !type || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.put(
            `database/${databaseId}/assets/${assetId}/metadata/file`,
            {
                body: { filePath, type, metadata, updateType },
            }
        );

        console.log("updateFileMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error updating file metadata:", error);
        throw error;
    }
};

/**
 * Deletes metadata for a file (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.filePath - File path
 * @param {string} params.type - 'metadata' or 'attribute'
 * @param {Array} params.metadataKeys - Array of metadata keys to delete
 * @returns {Promise<any>}
 */
export const deleteFileMetadata = async ({
    databaseId,
    assetId,
    filePath,
    type,
    metadataKeys,
}: any) => {
    try {
        if (!databaseId || !assetId || !filePath || !type || !metadataKeys) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/metadata/file`,
            {
                body: { filePath, type, metadataKeys },
            }
        );

        console.log("deleteFileMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error deleting file metadata:", error);
        throw error;
    }
};

/**
 * Fetches metadata for a database (bulk operation with schema enrichment)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @returns {Promise<any>}
 */
export const fetchDatabaseMetadata = async ({ databaseId }: any) => {
    try {
        if (!databaseId) {
            return { metadata: [], message: "Missing required parameters" };
        }

        // Follow NextToken to aggregate all pages of metadata.
        return await fetchAllMetadataPages(`database/${databaseId}/metadata`);
    } catch (error: any) {
        console.log("Error fetching database metadata:", error);
        return { metadata: [], message: error?.message || "Error fetching metadata" };
    }
};

/**
 * Creates metadata for a database (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {Array} params.metadata - Array of metadata items
 * @returns {Promise<any>}
 */
export const createDatabaseMetadata = async ({ databaseId, metadata }: any) => {
    try {
        if (!databaseId || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.post(`database/${databaseId}/metadata`, {
            body: { metadata },
        });

        console.log("createDatabaseMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error creating database metadata:", error);
        throw error;
    }
};

/**
 * Updates metadata for a database (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {Array} params.metadata - Array of metadata items
 * @param {string} params.updateType - 'update' or 'replace_all'
 * @returns {Promise<any>}
 */
export const updateDatabaseMetadata = async ({
    databaseId,
    metadata,
    updateType = "update",
}: any) => {
    try {
        if (!databaseId || !metadata) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.put(`database/${databaseId}/metadata`, {
            body: { metadata, updateType },
        });

        console.log("updateDatabaseMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error updating database metadata:", error);
        throw error;
    }
};

/**
 * Deletes metadata for a database (bulk operation)
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {Array} params.metadataKeys - Array of metadata keys to delete
 * @returns {Promise<any>}
 */
export const deleteDatabaseMetadata = async ({ databaseId, metadataKeys }: any) => {
    try {
        if (!databaseId || !metadataKeys) {
            return { success: false, message: "Missing required parameters" };
        }

        const response = await apiClient.del(`database/${databaseId}/metadata`, {
            body: { metadataKeys },
        });

        console.log("deleteDatabaseMetadata response:", response);
        return response;
    } catch (error: any) {
        console.log("Error deleting database metadata:", error);
        throw error;
    }
};

/**
 * Fetches API keys across all users (paged)
 * @param {Object} params - Parameters object
 * @param {number} params.pageSize - Keys per page
 * @param {string} params.startingToken - Continuation token from a prior page
 * @returns {Promise<any>}
 */
export const fetchApiKeys = async ({ pageSize, startingToken }: any = {}) => {
    try {
        const queryStringParameters: any = {};
        if (pageSize) {
            queryStringParameters.pageSize = `${pageSize}`;
        }
        if (startingToken) {
            queryStringParameters.startingToken = startingToken;
        }

        const response = await apiClient.get("auth/api-keys", { queryStringParameters });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return response;
        }
        return [false, "Failed to fetch API keys"];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createApiKey = async (body: any) => {
    try {
        const response = await apiClient.post("auth/api-keys", { body });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to create API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to create API key";
        return [false, errorMsg];
    }
};

export const updateApiKey = async ({ apiKeyId, ...body }: any) => {
    try {
        const response = await apiClient.put(`auth/api-keys/${apiKeyId}`, { body });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to update API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to update API key";
        return [false, errorMsg];
    }
};

export const deleteApiKey = async ({ apiKeyId }: any) => {
    try {
        const response = await apiClient.del(`auth/api-keys/${apiKeyId}`);
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to delete API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to delete API key";
        return [false, errorMsg];
    }
};

// ===== Auth: User (self-service) API Keys =====
// These call the /auth/user/api-keys routes: scoped server-side to the
// requesting user's own keys, with mandatory expiration.

/**
 * Fetches the calling user's own API keys (paged)
 * @param {Object} params - Parameters object
 * @param {number} params.pageSize - Keys per page
 * @param {string} params.startingToken - Continuation token from a prior page
 * @returns {Promise<any>}
 */
export const fetchUserApiKeys = async ({ pageSize, startingToken }: any = {}) => {
    try {
        const queryStringParameters: any = {};
        if (pageSize) {
            queryStringParameters.pageSize = `${pageSize}`;
        }
        if (startingToken) {
            queryStringParameters.startingToken = startingToken;
        }

        const response = await apiClient.get("auth/user/api-keys", { queryStringParameters });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return response;
        }
        return [false, "Failed to fetch API keys"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to fetch API keys";
        return [false, errorMsg];
    }
};

export const createUserApiKey = async (body: any) => {
    try {
        const response = await apiClient.post("auth/user/api-keys", { body });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to create API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to create API key";
        return [false, errorMsg];
    }
};

export const updateUserApiKey = async ({ apiKeyId, ...body }: any) => {
    try {
        const response = await apiClient.put(`auth/user/api-keys/${apiKeyId}`, { body });
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to update API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to update API key";
        return [false, errorMsg];
    }
};

export const deleteUserApiKey = async ({ apiKeyId }: any) => {
    try {
        const response = await apiClient.del(`auth/user/api-keys/${apiKeyId}`);
        if (response !== false && response !== undefined) {
            if (
                response.message &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                return [false, response.message];
            }
            return [true, response];
        }
        return [false, "Failed to delete API key"];
    } catch (error: any) {
        console.log(error);
        const errorMsg =
            error?.response?.data?.message || error?.message || "Failed to delete API key";
        return [false, errorMsg];
    }
};

// ===== Auth: Constraints =====

export const deleteConstraint = async ({ constraintId }: any) => {
    try {
        const response = await apiClient.del(`auth/constraints/${constraintId}`, {});
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createConstraint = async ({ constraintId, ...body }: any) => {
    return apiClient.post(`auth/constraints/${constraintId}`, { body: { constraintId, ...body } });
};

// ===== Auth: Roles =====

export const deleteRole = async ({ roleName }: any) => {
    try {
        const response = await apiClient.del(`roles/${roleName}`, {});
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createRole = async (body: any) => {
    return apiClient.post("roles", { body });
};

export const updateRole = async (body: any) => {
    return apiClient.put("roles", { body });
};

// ===== Auth: User Roles =====

export const deleteUserRole = async (body: any) => {
    try {
        const response = await apiClient.del("user-roles", { body });
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createUserRole = async (body: any) => {
    return apiClient.post("user-roles", { body });
};

export const updateUserRole = async (body: any) => {
    return apiClient.put("user-roles", { body });
};

// ===== Tags =====

/**
 * Deletes a tag from a specific scope.
 *
 * A tag is identified by scope AND name — the scope is the storage partition key — so `databaseId`
 * must be sent for a database-scoped tag. Omitting it targets the GLOBAL partition, which reports
 * "Tag not found" for a scoped tag rather than deleting it.
 */
export const deleteTag = async ({ tagName, databaseId }: any) => {
    try {
        const response = await apiClient.del(`tags/${tagName}`, {
            queryStringParameters: databaseId ? { databaseId } : {},
        });
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/** Deletes a tag type from a specific scope; see deleteTag on why databaseId is required. */
export const deleteTagType = async ({ tagTypeName, databaseId }: any) => {
    try {
        const response = await apiClient.del(`tag-types/${tagTypeName}`, {
            queryStringParameters: databaseId ? { databaseId } : {},
        });
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createTag = async (body: any) => {
    return apiClient.post("tags", { body });
};

export const updateTag = async (body: any) => {
    return apiClient.put("tags", { body });
};

export const createTagType = async (body: any) => {
    return apiClient.post("tag-types", { body });
};

export const updateTagType = async (body: any) => {
    return apiClient.put("tag-types", { body });
};

// ===== Subscriptions =====

export const deleteSubscription = async (body: any) => {
    try {
        const response = await apiClient.del("subscriptions", { body });
        if (
            response.message?.indexOf("error") !== -1 ||
            response.message?.indexOf("Error") !== -1
        ) {
            return [false, response.message];
        }
        return [true, response.message];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createSubscription = async (body: any) => {
    return apiClient.post("subscriptions", { body });
};

export const updateSubscription = async (body: any) => {
    return apiClient.put("subscriptions", { body });
};

export const checkSubscription = async (body: any) => {
    try {
        const response = await apiClient.post("check-subscription", { body });
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const unsubscribeFromAsset = async (body: any) => {
    try {
        const response = await apiClient.del("unsubscribe", { body });
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createComment = async ({ assetId, assetVersionIdAndCommentId, body }: any) => {
    try {
        const response = await apiClient.post(
            `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const updateComment = async ({ assetId, assetVersionIdAndCommentId, body }: any) => {
    try {
        const response = await apiClient.put(
            `comments/assets/${assetId}/assetVersionId:commentId/${assetVersionIdAndCommentId}`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const createAssetLink = async (body: any) => {
    try {
        const response = await apiClient.post("asset-links", { body });
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const savePipeline = async (body: any) => {
    try {
        const response = await apiClient.put("pipelines", { body });
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const unarchiveAsset = async ({ databaseId, assetId, body }: any) => {
    try {
        const response = await apiClient.put(
            `database/${databaseId}/assets/${assetId}/unarchiveAsset`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const archiveAssetDelete = async ({ databaseId, assetId, body }: any) => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/archiveAsset`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const deleteAssetPermanentDelete = async ({ databaseId, assetId, body }: any) => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/deleteAsset`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const archiveFile = async ({ databaseId, assetId, body }: any) => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/archiveFile`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const deleteFilePermanent = async ({ databaseId, assetId, body }: any) => {
    try {
        const response = await apiClient.del(
            `database/${databaseId}/assets/${assetId}/deleteFile`,
            { body }
        );
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const searchAssets = async (body: any) => {
    try {
        const response = await apiClient.post("search", {
            "Content-type": "application/json",
            body,
        } as any);
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const searchAssetsSimple = async (body: any) => {
    try {
        const response = await apiClient.post("search/simple", {
            "Content-type": "application/json",
            body,
        } as any);
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const fetchSearchMappings = async () => {
    try {
        const response = await apiClient.get("search", {});
        return response;
    } catch (error: any) {
        console.log(error);
        return false;
    }
};

export const ingestAsset = async (body: any) => {
    try {
        const response = await apiClient.post("ingest-asset", { body });
        if (response.message) {
            if (
                response.message.indexOf("error") !== -1 ||
                response.message.indexOf("Error") !== -1
            ) {
                console.log(response.message);
                return [false, response.message];
            }
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

export const fetchLoginProfile = async ({ username }: any) => {
    try {
        const response = await apiClient.post(`auth/loginProfile/${username}`);
        if (response.message) {
            return [true, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Response envelope returned by `GET /addon/physna/viewer`.
 *
 * The backend now returns JSON describing what the frontend should show
 * instead of pre-rendering an iframe HTML payload. The frontend switches
 * on `status` and, for `"ready"`, uses the viewer-token bundle to build
 * a direct Physna iframe src.
 */
export interface PhysnaViewerMetadataResponse {
    status:
        | "ready"
        | "indexing"
        | "failed"
        | "not_synced"
        | "not_found"
        | "unsupported"
        | "forbidden"
        | "upstream_unavailable"
        | "invalid_request"
        | "method_not_allowed"
        | "request_failed"
        | "internal_error";
    message: string;
    /** Populated only when `status === "ready"`. */
    tenantId?: string;
    physnaAssetId?: string;
    viewerToken?: string;
    physnaApiBase?: string;
    /** Populated on `indexing` and `failed` — raw upstream state for display. */
    physnaState?: string;
}

/**
 * Fetch Physna viewer metadata (authz + lookup + viewer-token mint) from the
 * VAMS backend. The frontend uses the returned envelope to decide whether to
 * render a direct-to-Physna iframe, show a "still indexing" placeholder, or
 * surface an error.
 *
 * Uses `apiClient` because the endpoint now returns JSON (previously it
 * returned HTML for an iframe, which required a direct `fetch`).
 */
export const fetchPhysnaViewerMetadata = async ({
    databaseId,
    assetId,
    relativePath,
}: {
    databaseId: string;
    assetId: string;
    relativePath: string;
}): Promise<[boolean, PhysnaViewerMetadataResponse | string]> => {
    try {
        const response = await apiClient.get("addon/physna/viewer", {
            queryStringParameters: {
                databaseId,
                assetId,
                relativePath,
            },
        });
        if (response && typeof response === "object" && "status" in response) {
            return [true, response as PhysnaViewerMetadataResponse];
        }
        if (response?.message) {
            console.log(response.message);
            return [false, response.message];
        }
        return [false, "Unexpected response shape from viewer endpoint"];
    } catch (error: any) {
        console.log(error);
        return [false, error?.message || "Failed to load Physna viewer"];
    }
};

export const ACTIONS = {
    CREATE: {
        DATABASE: createDatabase,
    },
    UPDATE: {
        ASSET: updateAsset,
    },
    READ: {
        ASSET: fetchAsset,
    },
    LIST: {},
    DELETE: {
        ASSET_ARCHIVE: archiveAsset,
        ASSET_PERMANENT: deleteAssetPermanent,
    },
    EXECUTE: {},
    REVERT: {},
};
