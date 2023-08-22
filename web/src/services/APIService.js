/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { API } from "aws-amplify";

/**
 * Returns array of boolean and response/error message for the element that the current user is downloading, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const downloadAsset = async ({ databaseId, assetId, config }, api = API) => {
    try {
        const response = await api.post(
            "api",
            `/database/${databaseId}/assets/${assetId}/download`,
            config || config.body
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
        const response = await api.del(
            "api",
            deleteRoute
                .replace("{databaseId}", item?.databaseId)
                .replace(`{${elementId}}`, item[elementId]),
            {}
        );
        if (response.message) {
            console.log(response.message);
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
 * Returns array of boolean and response/error message for the workflow that the current user is running, or false if error.
 * @returns {Promise<boolean|{message}|any>}
 */
export const runWorkflow = async ({ databaseId, assetId, workflowId }, api = API) => {
    try {
        const response = await api.post(
            "api",
            `database/${databaseId}/assets/${assetId}/workflows/${workflowId}`,
            {}
        );
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
 * Returns array of all constraints from the auth/constraints api
 * @returns {Promise<boolean|{constraints}|any>}
 */
export const fetchConstraints = async (api = API) => {
    try {
        const response = await api.get("api", "auth/constraints", {});
        if (response.constraints) {
            return response.constraints;
        } else {
            return false;
        }
    } catch (error) {
        console.log(error);
        return error?.message;
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

export const fetchAssetFiles = async ({ databaseId, assetId }, api = API) => {
    try {
        let response;
        if (databaseId && assetId) {
            response = await api.get(
                "api",
                `database/${databaseId}/assets/${assetId}/listFiles`,
                {}
            );
            if (response) return response;
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
        console.log(error);
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
        if (databaseId) {
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
        } else {
            console.log("not fetching pipelines");
            return false;
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
        if (databaseId) {
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
        } else {
            return false;
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
export const fetchWorkflowExecutions = async ({ databaseId, assetId, workflowId }, api = API) => {
    try {
        let response;
        if (databaseId && assetId && workflowId) {
            response = await api.get(
                "api",
                `database/${databaseId}/assets/${assetId}/workflows/${workflowId}/executions`,
                {}
            );
            if (response.message) {
                if (response.message.Items) {
                    return response.message.Items;
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
