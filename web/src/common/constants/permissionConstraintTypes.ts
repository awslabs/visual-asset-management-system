export const criteriaOperators = [
    { label: "Equals", value: "equals" },
    { label: "Contains", value: "contains" },
    { label: "Does Not Contain", value: "does_not_contain" },
    { label: "Starts With", value: "starts_with" },
    { label: "Ends With", value: "ends_with" },
    { label: "Is One Of", value: "is_one_of" },
    { label: "Is Not One Of", value: "is_not_one_of" },
];

//Note: "object__type" is a core field in itself behind the scenes
export const objectTypes = [
    { label: "Database", value: "database" },
    { label: "Asset", value: "asset" },
    { label: "API", value: "api" },
    { label: "Web", value: "web" },
    { label: "Tag", value: "tag" },
    { label: "Tag Type", value: "tagType" },
    { label: "Role", value: "role" },
    { label: "User Role", value: "userRole" },
    { label: "Pipeline", value: "pipeline" },
    { label: "Workflow", value: "workflow" },
    { label: "Metadata Schema", value: "metadataSchema" },
];

export const fieldNamesToObjectTypeMapping: { [key: string]: Record<string, string>[] } = {
    database: [{ label: "Database ID", value: "databaseId" }],
    asset: [
        { label: "Database ID", value: "databaseId" },
        { label: "Asset Name", value: "assetName" },
        { label: "Asset Type", value: "assetType" },
        { label: "Tags", value: "tags" },
    ],
    api: [{ label: "Route Path", value: "route__path" }],
    web: [{ label: "Route Path", value: "route__path" }],
    tag: [{ label: "Tag Name", value: "tagName" }],
    tagType: [{ label: "Tag Type Name", value: "tagTypeName" }],
    role: [{ label: "Role Name", value: "roleName" }],
    userRole: [
        { label: "Role Name", value: "roleName" },
        { label: "User ID", value: "userId" },
    ],
    pipeline: [
        { label: "Database ID", value: "databaseId" },
        { label: "Pipeline ID", value: "pipelineId" },
        { label: "Pipeline Type", value: "pipelineType" },
    ],
    workflow: [
        { label: "Database ID", value: "databaseId" },
        { label: "Workflow ID", value: "workflowId" },
    ],
    metadataSchema: [
        { label: "Database ID", value: "databaseId" },
        { label: "Metadata Field", value: "field" },
    ],
};
