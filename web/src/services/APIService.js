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
 * @returns {Promise<boolean|{message}|any>}
 */
export const downloadAsset = async ({ databaseId, assetId, key, version }, api = API) => {
    try {
        //Version and key are optional fields
        const body = {
            version: version,
            key: key,
        };

        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/download`,
            {
                body: body,
            }
        );
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
 * Returns array of boolean and response/error message for the elements that the current user is deleting, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const deleteElement = async ({ deleteRoute, elementId, item }, api = API) => {
    try {
        let route = deleteRoute;
        
        // Handle global pipelines/workflows (no database ID)
        if (item?.databaseId === "" && deleteRoute.includes("{databaseId}")) {
            // For global items, use the global database route
            route = deleteRoute.replace("{databaseId}", "global");
        } else {
            route = route.replace("{databaseId}", item?.databaseId);
        }
        
        const response = await api.del(
            "api",
            route.replace(`{${elementId}}`, item[elementId]),
            {}
        );
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
export const runWorkflow = async ({ databaseId, assetId, workflowId, isGlobalWorkflow = false }, api = API) => {
    try {
        let endpoint;
        
        // If it's a global workflow, use the global database route
        if (isGlobalWorkflow) {
            endpoint = `database/global/assets/${assetId}/workflows/${workflowId}`;
        } else {
            endpoint = `database/${databaseId}/assets/${assetId}/workflows/${workflowId}`;
        }
        
        const response = await api.post("api", endpoint, {});
        
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
        // Check if this is a global workflow (databaseId is empty string)
        const isGlobalWorkflow = config?.body?.databaseId === "";
        
        // Use different endpoint for global workflows
        const endpoint = isGlobalWorkflow ? "database/global/workflows" : "workflows";
        
        const response = await api.put("api", endpoint, config || config.body);
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
        let response = await api.get("api", "databases", {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", "databases", init);
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
 * Returns the asset that the current user can access for the given databaseId & assetId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAsset = async ({ databaseId, assetId }, api = API) => {
    try {
        let response;
        if (databaseId && assetId) {
            response = await api.get("api", `database/${databaseId}/assets/${assetId}`, {});
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
 * Returns the asset that the current user can access for the given databaseId & assetId, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabase = async ({ databaseId }, api = API) => {
    try {
        let response;
        if (databaseId) {
            response = await api.get("api", `databases/${databaseId}`, {});
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

export const fetchAssetLinks = async ({ assetId }, api = API) => {
    try {
        let response;
        if (assetId) {
            response = await api.get("api", `asset-links/${assetId}`, {});
            if (response.message) return response.message;
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
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

// /**
//  * Returns array of all constraints from the auth/constraints api
//  * @returns {Promise<boolean|{rules}|any>}
//  */
// export const fetchRulesMetadata = async (api = API) => {
//     try {
//         const response = await api.get("api", "notification-config/metadata", {});
//         if (response.message) {
//             return response.message;
//         } else {
//             return false;
//         }
//     } catch (error) {
//         console.log(error);
//         return error?.message;
//     }
// };

export const fetchAssetFiles = async ({ databaseId, assetId }, api = API) => {
    try {
        let response;

        if (databaseId && assetId) {
            response = await api.get(
                "api",
                `database/${databaseId}/assets/${assetId}/listFiles`,
                {}
            );
            //console.log("fetchAssetFiles response", response)
            let items = [];
            const init = { queryStringParameters: { startingToken: null } };
            if (response.message) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get(
                        "api",
                        `database/${databaseId}/assets/${assetId}/listFiles`,
                        init
                    );
                    items = items.concat(response.message.Items);
                }

                return items;
            } else return false;
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
 * Returns array of all assets the current user can access for all databases, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAllAssets = async (api = API) => {
    try {
        let response = await api.get("api", `assets`, {});
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    response = await api.get("api", `assets`, init);
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
 * Returns array of all assets the current user can access for a given database, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabaseAssets = async ({ databaseId }, api = API) => {
    try {
        let response;
        if (databaseId) {
            response = await api.get("api", `database/${databaseId}/assets`, {});
            let items = [];
            const init = { queryStringParameters: { startingToken: null } };
            if (response.message) {
                if (response.message.Items) {
                    items = items.concat(response.message.Items);
                    while (response.message.NextToken) {
                        init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                        response = await api.get("api", `database/${databaseId}/assets`, init);
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
        
        // If databaseId is an empty string, fetch global pipelines using the global database route
        if (databaseId === "") {
            response = await api.get("api", `database/global/pipelines`, {});
        } else {
            // Otherwise fetch database-specific pipelines
            response = await api.get("api", `database/${databaseId}/pipelines`, {});
        }
        
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    if (databaseId === "") {
                        response = await api.get("api", `database/global/pipelines`, init);
                    } else {
                        response = await api.get("api", `database/${databaseId}/pipelines`, init);
                    }
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
        
        // If databaseId is an empty string, fetch global workflows using the global database route
        if (databaseId === "") {
            response = await api.get("api", `database/global/workflows`, {});
        } else {
            // Otherwise fetch database-specific workflows
            response = await api.get("api", `database/${databaseId}/workflows`, {});
        }
        
        let items = [];
        const init = { queryStringParameters: { startingToken: null } };
        if (response.message) {
            if (response.message.Items) {
                items = items.concat(response.message.Items);
                while (response.message.NextToken) {
                    init["queryStringParameters"]["startingToken"] = response.message.NextToken;
                    if (databaseId === "") {
                        response = await api.get("api", `database/global/workflows`, init);
                    } else {
                        response = await api.get("api", `database/${databaseId}/workflows`, init);
                    }
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
export const fetchWorkflowExecutions = async ({ databaseId, assetId, workflowId, isGlobalWorkflow = false }, api = API) => {
    try {
        let response;
        let endpoint;
        
        if (assetId && workflowId) {
            // Determine the endpoint based on whether it's a global workflow
            if (isGlobalWorkflow) {
                endpoint = `database/global/assets/${assetId}/workflows/${workflowId}/executions`;
            } else {
                endpoint = `database/${databaseId}/assets/${assetId}/workflows/${workflowId}/executions`;
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
export const ACTIONS = {
    CREATE: {},
    UPDATE: {},
    READ: {
        ASSET: fetchAsset,
    },
    LIST: {},
    DELETE: {},
    EXECUTE: {},
    REVERT: {},
};
