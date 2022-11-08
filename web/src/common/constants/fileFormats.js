export const cadFileFormats = [".step", ".dwg"];
export const modelFileFormats = [".gltf", ".glb", ".obj", ".stl"];
export const columnarFileFormats = [".rds", ".fcs", ".csv"];
export const previewFileFormats = [".png", ".jpg", ".svg", ".gif"];
export const archiveFileFormats = [".zip"];
//html files are view-only, should not be made available for upload or as sources in pipeline
//there may be a need in some cases to output notes on the pipeline execution
//future state could add txt and rename from "presentation"
export const presentationFileFormats = [".html"];

export const FILE_FORMATS = {
  CAD: cadFileFormats,
  PRINT: [],
  VR: [],
  MODEL: modelFileFormats,
  COLUMNAR: columnarFileFormats,
  PREVIEW: previewFileFormats,
  ARCHIVE: archiveFileFormats,
  PRESENTATION: presentationFileFormats
};
