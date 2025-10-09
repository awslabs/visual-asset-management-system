/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";
import { default as vamsConfig } from "../config";

export const getAmplifyConfig = async () => {
    console.log("getAmplifyConfig");
    try {
        const amplifyConfigUrl = new URL(
            "/api/amplify-config",
            vamsConfig.DEV_API_ENDPOINT === ""
                ? window.location.origin
                : vamsConfig.DEV_API_ENDPOINT
        );
        console.log(amplifyConfigUrl.href);
        const response = await fetch(amplifyConfigUrl);
        return response.json();
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

export const getSecureConfig = async () => {
    console.log("getSecureConfig");
    return API.get("api", `secure-config`, {});
};

export const webRoutes = async (body) => {
    console.log("webRoutes");
    try {
        const response = await API.post("api", "auth/routes", {
            body: {
                routes: body.routes,
            },
        });
        console.log("response", response);
        return response;
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the element that the current user is downloading, or false if error.
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.key - Optional key path for the file
 * @param {string} params.versionId - Optional version ID
 * @param {string} params.downloadType - Download type: "assetFile" (default) or "assetPreview"
 * @returns {Promise<boolean|{message}|any>}
 */
export const downloadAsset = async (
    { databaseId, assetId, key, versionId, downloadType = "assetFile" },
    api = API
) => {
    try {
        // Build request body with new model structure
        const body = {
            downloadType: downloadType,
            key: key,
            versionId: versionId,
        };

        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/download`,
            {
                body: body,
            }
        );

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
    } catch (error) {
        console.log(error);
        // Check for 410 Gone status (archived file)
        if (error.response && error.response.status === 410) {
            return [false, "This file version has been archived and cannot be downloaded"];
        }
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the elements that the current user is deleting, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteElement = async ({ deleteRoute, elementId, item }, api = API) => {
    try {
        let route = deleteRoute;
        route = route.replace("{databaseId}", item?.databaseId);

        const response = await api.del("api", route.replace(`{${elementId}}`, item[elementId]), {});
        if (response.message) {
            console.log(response.message);
            return [true, response.message, ""];
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return [false, error?.message, error?.response.data.message];
    }
};

/**
 * Returns array of boolean and response/error message for the workflow that the current user is running, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const runWorkflow = async (
    { databaseId, assetId, workflowId, fileKey, isGlobalWorkflow = false },
    api = API
) => {
    try {
        let endpoint;
        let eventBody = {};
        endpoint = `database/${databaseId}/assets/${assetId}/workflows/${workflowId}`;

        if (isGlobalWorkflow) {
            eventBody = { workflowDatabaseId: "GLOBAL", fileKey: fileKey };
        } else {
            eventBody = { workflowDatabaseId: databaseId, fileKey: fileKey };
        }

        const response = await api.post("api", endpoint, {
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
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the workflow that the current user is saving/updating, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const saveWorkflow = async ({ config }, api = API) => {
    try {
        const response = await api.put("api", "workflows", config || config.body);
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
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of boolean and response/error message for the element that the current user is creating/updating, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const createUpdateElements = async ({ pluralName, config }, api = API) => {
    try {
        const response = await api.put("api", pluralName, config || config.body);
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
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Returns array of all databases the current user can access, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllDatabases = async (api = API) => {
    try {
        let response = await api.get("api", "database", {});
        console.log("Raw databases response:", response);

        // If response is directly an array, return it
        if (Array.isArray(response)) {
            return response;
        }

        // If response has Items property, process it
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };

        if (response && response.Items) {
            items = items.concat(response.Items);
            while (response.NextToken) {
                init["queryStringParameters"]["startingToken"] = response.NextToken;
                response = await api.get("api", "database", init);
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
                response = await api.get("api", "database", init);
                if (response && response.message && response.message.Items) {
                    items = items.concat(response.message.Items);
                }
            }
            return items;
        }

        // If no items found, return empty array instead of false
        return [];
    } catch (error) {
        console.log("Error fetching databases:", error);
        return [];
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
export const fetchAsset = async ({ databaseId, assetId, showArchived = false }, api = API) => {
    try {
        let response;
        if (databaseId && assetId) {
            response = await api.get("api", `database/${databaseId}/assets/${assetId}`, {
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
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns the asset that the current user can access for the given databaseId & assetId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabase = async ({ databaseId }, api = API) => {
    try {
        let response;
        if (databaseId) {
            response = await api.get("api", `database/${databaseId}`, {});
            if (response.message) return response.message;
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{tags}|any>}
 */
export const fetchTags = async (api = API) => {
    try {
        let response = await api.get("api", "tags", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "tags", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};
/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{tagtypes}|any>}
 */
export const fetchtagTypes = async (api = API) => {
    try {
        let response = await api.get("api", "tag-types", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "tag-types", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

export const fetchAssetLinks = async (
    { assetId, databaseId, childTreeView = false },
    api = API
) => {
    try {
        let response;
        if (assetId) {
            const queryParams = {};
            if (childTreeView) {
                queryParams.childTreeView = "true";
            }

            console.log("Fetching asset links with params:", queryParams);

            response = await api.get(
                "api",
                `database/${databaseId}/assets/${assetId}/asset-links`,
                {
                    queryStringParameters: queryParams,
                }
            );

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
            // If the response is just a string message
            else if (response && typeof response === "string") {
                console.error("Received string response:", response);
                return {
                    related: [],
                    parents: [],
                    children: [],
                    unauthorizedCounts: { related: 0, parents: 0, children: 0 },
                    message: response,
                };
            }
            // Return the response as is, let the component handle validation
            return response;
        } else {
            return false;
        }
    } catch (error) {
        console.log("Error fetching asset links:", error);
        return {
            related: [],
            parents: [],
            children: [],
            unauthorizedCounts: { related: 0, parents: 0, children: 0 },
            message: error?.message || "An error occurred",
        };
    }
};

export const deleteAssetLink = async ({ relationId }, api = API) => {
    try {
        let response;
        if (relationId) {
            response = await api.del("api", `asset-links/${relationId}`, {});
            if (response.message) return response.message;
        } else {
            return response.message.status;
        }
    } catch (error) {
        console.log(error);
        return error;
    }
};

/**
 * Returns array of all subscription constraints from the auth/constraints api
 * @returns {Promise<boolean|{rules}|any>}
 */
export const fetchSubscriptionRules = async (api = API) => {
    try {
        let response = await api.get("api", "subscriptions", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "subscriptions", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all roles
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchRoles = async (api = API) => {
    try {
        let response = await api.get("api", "roles", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "roles", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all users assigned to roles
 * @returns {Promise<boolean|{userroles}|any>}
 */
export const fetchUserRoles = async (api = API) => {
    try {
        let response = await api.get("api", "user-roles", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "user-roles", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{constraints}|any>}
 */
export const fetchConstraints = async (api = API) => {
    try {
        let response = await api.get("api", "auth/constraints", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "auth/constraints", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all the comments that are attached to a given assetId
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllComments = async ({ assetId }, api = API) => {
    try {
        let response = await api.get("api", `comments/assets/${assetId}`, {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `comments/assets/${assetId}`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Deletes the given comment from the database
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteComment = async ({ assetId, assetVersionIdAndCommentId }, api = API) => {
    try {
        let response = await api.del(
            "api",
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
    } catch (error) {
        if (error.response.status === 403) return error.response.status;
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
export const fetchDatabaseAssets = async (
    { databaseId, showArchived = false, maxItems = 1000, pageSize = 1000, startingToken = null },
    api = API
) => {
    try {
        if (!databaseId) {
            return [];
        }

        const queryParams = {
            showArchived: showArchived.toString(),
            maxItems: maxItems.toString(),
            pageSize: pageSize.toString(),
        };

        if (startingToken) {
            queryParams.startingToken = startingToken;
        }

        let response = await api.get("api", `database/${databaseId}/assets`, {
            queryStringParameters: queryParams,
        });

        let items = [];
        
        // Handle legacy response format with message wrapper
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    queryParams.startingToken = response.message.NextToken;
                    response = await api.get("api", `database/${databaseId}/assets`, {
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
                response = await api.get("api", `database/${databaseId}/assets`, {
                    queryStringParameters: queryParams,
                });
                if (response.Items) {
                    items = items.concat(response.Items);
                }
            }
            return items;
        }
        
        return [];
    } catch (error) {
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
export const fetchAllAssets = async (
    { showArchived = false, maxItems = 1000, pageSize = 1000, startingToken = null } = {},
    api = API
) => {
    try {
        const queryParams = {
            showArchived: showArchived.toString(),
            maxItems: maxItems.toString(),
            pageSize: pageSize.toString(),
        };

        if (startingToken) {
            queryParams.startingToken = startingToken;
        }

        let response = await api.get("api", `assets`, {
            queryStringParameters: queryParams,
        });

        let items = [];
        
        // Handle legacy response format with message wrapper
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    queryParams.startingToken = response.message.NextToken;
                    response = await api.get("api", `assets`, {
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
                response = await api.get("api", `assets`, {
                    queryStringParameters: queryParams,
                });
                if (response.Items) {
                    items = items.concat(response.Items);
                }
            }
            return items;
        }
        
        return [];
    } catch (error) {
        console.log("Error fetching all assets:", error);
        return [];
    }
};

/**
 * Returns array of all pipelines the current user can access for all databases, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllPipelines = async (api = API) => {
    try {
        let response = await api.get("api", `pipelines`, {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `pipelines`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all pipelines the current user can access for a given database, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabasePipelines = async ({ databaseId }, api = API) => {
    try {
        let response;
        // If databaseId is undefined, return false
        if (databaseId === undefined) {
            console.log("not fetching pipelines");
            return false;
        }

        response = await api.get("api", `database/${databaseId}/pipelines`, {});

        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `database/${databaseId}/pipelines`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflows the current user can access for a given database, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabaseWorkflows = async ({ databaseId }, api = API) => {
    try {
        let response;
        // If databaseId is undefined, return false
        if (databaseId === undefined) {
            console.log("not fetching workflows");
            return false;
        }

        response = await api.get("api", `database/${databaseId}/workflows`, {});

        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `database/${databaseId}/workflows`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflows the current user can access for all databases, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllWorkflows = async (api = API) => {
    try {
        let response = await api.get("api", `workflows`, {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `workflows`, init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all workflow executions the current user can access for the given databaseId & assetId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchWorkflowExecutions = async (
    { databaseId, assetId, workflowId = "" },
    api = API
) => {
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

            response = await api.get("api", endpoint, {});
            let items = [];
            const init = { queryStringParameters: { startingToken: null } };
            if (response.message) {
                if (response.message.Items) {
                    items = items.concat(response.message.Items);
                    while (response.message.NextToken) {
                        init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                        response = await api.get("api", endpoint, init);
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
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of all metadata fields from the backend
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchAllMetadataSchema = async (api = API) => {
    try {
        let response = await api.get("api", "metadataschema/", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "metadataschema/", init);
                    items = items.concat(response.message.Items);
                }
                return items;
            } else {
                return response.message;
            }
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/**
 * Returns array of  metadata fields from the backend for a particular databaseId
 * @returns {Promise<boolean|{roles}|any>}
 */
export const fetchDatabaseMetadataSchema = async ({ databaseId }, api = API) => {
    try {
        let response;
        if (databaseId) {
            response = await api.get("api", `metadataschema/${databaseId}`, {});
            let items = [];
            const init = { queryStringParameters: { startingToken: null } };
            if (response.message) {
                if (response.message.Items) {
                    items = items.concat(response.message.Items);
                    while (response.message.NextToken) {
                        init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                        response = await api.get("api", `metadataschema/${databaseId}`, init);
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
    } catch (error) {
        console.log(error);
        return error?.message;
    }
};

/** add in the columnar data loaders **/
/**
 * Creates a new folder in the specified asset
 * @returns {Promise<boolean|{message}|any>}
 */
export const createFolder = async ({ databaseId, assetId, relativeKey }, api = API) => {
    try {
        const response = await api.post(
            "api",
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
    } catch (error) {
        console.log(error);
        return [false, error?.message];
    }
};

/**
 * Reverts a file to a specific version by creating a new current version with the contents of the specified version
 * @returns {Promise<boolean|{message}|any>}
 */
export const revertFileVersion = async (
    { databaseId, assetId, filePath, versionId },
    api = API
) => {
    try {
        if (!databaseId || !assetId || !filePath || !versionId) {
            return [false, "Missing required parameters"];
        }

        const response = await api.post(
            "api",
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
    } catch (error) {
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
export const updateAsset = async ({ databaseId, assetId, updateData }, api = API) => {
    try {
        if (!databaseId || !assetId || !updateData) {
            return [false, "Missing required parameters"];
        }

        const response = await api.put("api", `database/${databaseId}/assets/${assetId}`, {
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
    } catch (error) {
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
export const archiveAsset = async (
    { databaseId, assetId, confirmArchive = true, reason = "" },
    api = API
) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        if (!confirmArchive) {
            return [false, "Archive operation must be confirmed"];
        }

        const response = await api.post(
            "api",
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
    } catch (error) {
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
export const deleteAssetPermanent = async (
    { databaseId, assetId, confirmPermanentDelete = false, reason = "" },
    api = API
) => {
    try {
        if (!databaseId || !assetId) {
            return [false, "Database ID and Asset ID are required"];
        }

        if (!confirmPermanentDelete) {
            return [false, "Permanent deletion requires explicit confirmation"];
        }

        const response = await api.post(
            "api",
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
    } catch (error) {
        console.log("Error deleting asset:", error);
        return [false, error?.message || "Failed to delete asset"];
    }
};

/**
 * Returns array of all buckets the current user can access, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchBuckets = async (api = API) => {
    try {
        let response = await api.get("api", "buckets", {});
        console.log("Raw buckets response:", response);

        // Direct return of the response which should contain Items array
        return response;
    } catch (error) {
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
 * @returns {Promise<boolean|{message}|any>}
 */
export const createDatabase = async ({ databaseId, description, defaultBucketId }, api = API) => {
    try {
        const response = await api.post("api", "database", {
            body: {
                databaseId,
                description,
                defaultBucketId,
            },
        });

        if (response.message) {
            console.log("create database", response);
            return [true, response.message];
        } else {
            return false;
        }
    } catch (error) {
        console.log("create database error", error);
        return [false, error?.message];
    }
};

/**
 * Fetches metadata for an asset link
 * @param {Object} params - Parameters object
 * @param {string} params.assetLinkId - Asset link ID
 * @returns {Promise<any>}
 */
export const fetchAssetLinkMetadata = async ({ assetLinkId }, api = API) => {
    try {
        if (!assetLinkId) {
            return false;
        }

        const response = await api.get("api", `asset-links/${assetLinkId}/metadata`, {});
        console.log("fetchAssetLinkMetadata raw response:", response);

        // Handle different response formats
        if (response && typeof response === "object") {
            // If response has metadata array directly
            if (Array.isArray(response.metadata)) {
                return response;
            }
            // If response.message contains the data
            else if (
                response.message &&
                typeof response.message === "object" &&
                Array.isArray(response.message.metadata)
            ) {
                return response.message;
            }
            // If response.message is just a string (like "Success"), return empty metadata structure
            else if (response.message && typeof response.message === "string") {
                return { metadata: [], message: response.message };
            }
            // Return response as-is for other object formats
            else {
                return response;
            }
        }
        // If response is a string (like "Success"), return empty metadata structure
        else if (typeof response === "string") {
            return { metadata: [], message: response };
        }

        return response;
    } catch (error) {
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
export const createAssetLinkMetadata = async (
    { assetLinkId, metadataKey, metadataValue, metadataValueType },
    api = API
) => {
    try {
        if (!assetLinkId || !metadataKey || !metadataValue || !metadataValueType) {
            return [false, "Missing required parameters"];
        }

        const response = await api.post("api", `asset-links/${assetLinkId}/metadata`, {
            body: {
                metadataKey,
                metadataValue,
                metadataValueType,
            },
        });

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Create asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
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
export const updateAssetLinkMetadata = async (
    { assetLinkId, metadataKey, metadataValue, metadataValueType },
    api = API
) => {
    try {
        if (!assetLinkId || !metadataKey || !metadataValue || !metadataValueType) {
            return [false, "Missing required parameters"];
        }

        const response = await api.put(
            "api",
            `asset-links/${assetLinkId}/metadata/${metadataKey}`,
            {
                body: {
                    metadataValue,
                    metadataValueType,
                },
            }
        );

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Update asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
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
export const deleteAssetLinkMetadata = async ({ assetLinkId, metadataKey }, api = API) => {
    try {
        if (!assetLinkId || !metadataKey) {
            return [false, "Missing required parameters"];
        }

        const response = await api.del(
            "api",
            `asset-links/${assetLinkId}/metadata/${metadataKey}`,
            {}
        );

        if (response.message) {
            if (
                response.message.indexOf &&
                (response.message.indexOf("error") !== -1 ||
                    response.message.indexOf("Error") !== -1)
            ) {
                console.log("Delete asset link metadata error:", response.message);
                return [false, response.message];
            } else {
                return [true, response.message];
            }
        } else {
            return [false, "No response received"];
        }
    } catch (error) {
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
export const setPrimaryType = async (
    { databaseId, assetId, filePath, primaryType, primaryTypeOther },
    api = API
) => {
    try {
        if (!databaseId || !assetId || !filePath) {
            return [false, "Missing required parameters"];
        }

        const response = await api.put(
            "api",
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
    } catch (error) {
        console.log("Error setting primary type:", error);
        return [false, error?.message || "Failed to set primary type"];
    }
};

/**
 * Fetches file information for a specific file in an asset
 * @param {Object} params - Parameters object
 * @param {string} params.databaseId - Database ID
 * @param {string} params.assetId - Asset ID
 * @param {string} params.fileKey - File key/path
 * @returns {Promise<any>}
 */
export const fetchFileInfo = async ({ databaseId, assetId, fileKey }, api = API) => {
    try {
        if (!databaseId || !assetId || !fileKey) {
            return [false, "Missing required parameters"];
        }

        const response = await api.get("api", `database/${databaseId}/assets/${assetId}/fileInfo`, {
            queryStringParameters: { filePath: fileKey },
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
    } catch (error) {
        console.log("Error fetching file info:", error);
        return [false, error?.message || "Failed to fetch file information"];
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
