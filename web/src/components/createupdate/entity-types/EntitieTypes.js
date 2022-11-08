import AssetEntity from "./AssetEntity";
import PipelineEntity from "./PipelineEntity";
import DatabaseEntity from "./DatabaseEntity";

export const ENTITY_TYPE_= {
    ASSET: AssetEntity,
    DATABASE: DatabaseEntity,
    PIPELINE: PipelineEntity
};

export const ENTITY_TYPES_NAMES = {
    ASSET: "ASSET",
    DATABASE: "DATABASE",
    PIPELINE: "PIPELINE",
    WORKFLOW: "WORKFLOW",
    WORKFLOW_EXECUTION: "WORKFLOW_EXECUTION"
};