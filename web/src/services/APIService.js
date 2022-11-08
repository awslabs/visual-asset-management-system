import {API} from "aws-amplify";

/**
 * Returns array of all databases the current user can access, or false if error.
 * @todo add error handling
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchDatabases = async () => {
    const response = await API.get("api", "databases", {});
    if (response.message) {
        if (response.message.Items) return response.message.Items;
        else return response.message;
    } else return false;
};

export const fetchAsset = async (databaseId, assetId) => {
    let response;
    if (databaseId && assetId) {
        response = await API.get("api", `database/${databaseId}/assets/${assetId}`, {});
        if (response.message) return response.message;
    } else {
        return false;
    }
};
/**
 * Returns array of all assets the current user can access for a given database, or false if error.
 * @todo add error handling
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchAssets = async (database) => {
    let response;
    if (database) {
        response = await API.get("api", `database/${database}/assets`, {});
    } else {
        response = await API.get("api", `assets`, {});
    }
    if (response.message) {
        if (response.message.Items) return response.message.Items;
        else return response.message;
    } else return false;
};

/**
 * Returns array of all pipelines the current user can access for a given database, or false if error.
 * @todo add error handling
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchPipelines = async (database) => {
    let response;
    if (database) {
        response = await API.get("api", `database/${database}/pipelines`, {});
    } else {
        response = await API.get("api", `pipelines`, {});
    }
    if (response.message) {
        if (response.message.Items) return response.message.Items;
        else return response.message;
    } else return false;
};

/**
 * Returns array of all workflows the current user can access for a given database, or false if error.
 * @todo add error handling
 * @returns {Promise<boolean|{message}|any>}
 */
export const fetchWorkflows = async (database) => {
    let response;
    if (database) {
        response = await API.get("api", `database/${database}/workflows`, {});
    } else {
        response = await API.get("api", `workflows`, {});
    }
    if (response.message) {
        if (response.message.Items) return response.message.Items;
        else return response.message;
    } else return false;
};

export const fetchWorkflowExecutions = async ({databaseId, assetId, workflowId}) => {
    let response;
    if (databaseId && assetId && workflowId) {
        response = await API.get("api", `database/${databaseId}/assets/${assetId}/workflows/${workflowId}/executions`, {});
    } else {
        response = await API.get("api", `workflows`, {});
    }
    if (response.message) {
        if (response.message.Items) return response.message.Items;
        else return response.message;
    } else return false;
};

/** add in the columnar data loaders **/

export const ACTIONS = {
    CREATE: {
    },
    UPDATE: {},
    READ: {
        ASSET: fetchAsset
    },
    LIST: {},
    DELETE: {},
    EXECUTE: {},
    REVERT: {}
}
