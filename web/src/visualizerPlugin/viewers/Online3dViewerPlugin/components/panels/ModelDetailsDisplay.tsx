/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect } from "react";
import { Selection } from "../../types/viewer.types";
import { useViewerContext } from "../../context/ViewerContext";

interface ModelDetailsDisplayProps {
    model: any;
    selection: Selection;
}

export const ModelDetailsDisplay: React.FC<ModelDetailsDisplayProps> = ({ model, selection }) => {
    const { state } = useViewerContext();
    const [objectDetails, setObjectDetails] = useState<any>(null);
    const [materialDetails, setMaterialDetails] = useState<any>(null);
    const [volume, setVolume] = useState<number | null>(null);
    const [surface, setSurface] = useState<number | null>(null);

    // Get the actual OV model for calculations
    const getOVModel = () => {
        return model?.ovModel || state.model?.ovModel;
    };

    const getOVLibrary = () => {
        if (state.viewer && state.viewer.OV) {
            return state.viewer.OV;
        }
        if (window.OV) {
            return window.OV;
        }
        return null;
    };

    useEffect(() => {
        const ovModel = getOVModel();
        const OV = getOVLibrary();

        if (ovModel && OV) {
            try {
                // Get bounding box and size information
                const boundingBox = OV.GetBoundingBox(ovModel);
                if (boundingBox) {
                    const size = {
                        x: boundingBox.max.x - boundingBox.min.x,
                        y: boundingBox.max.y - boundingBox.min.y,
                        z: boundingBox.max.z - boundingBox.min.z,
                    };

                    // Get model statistics
                    const stats = {
                        vertices: ovModel.VertexCount ? ovModel.VertexCount() : 0,
                        triangles: ovModel.TriangleCount ? ovModel.TriangleCount() : 0,
                        lineSegments: ovModel.LineSegmentCount ? ovModel.LineSegmentCount() : 0,
                        materials: ovModel.MaterialCount ? ovModel.MaterialCount() : 0,
                        meshes: ovModel.MeshCount ? ovModel.MeshCount() : 0,
                    };

                    // Get unit information
                    let unit = "Unknown";
                    try {
                        const modelUnit = ovModel.GetUnit ? ovModel.GetUnit() : null;
                        if (modelUnit !== null && modelUnit !== undefined) {
                            // Map unit enum to string
                            const unitMap: { [key: number]: string } = {
                                0: "Unknown",
                                1: "Millimeter",
                                2: "Centimeter",
                                3: "Meter",
                                4: "Inch",
                                5: "Foot",
                            };
                            unit = unitMap[modelUnit] || "Unknown";
                        }
                    } catch (error) {
                        console.warn("Could not get model unit:", error);
                    }

                    setObjectDetails({
                        ...stats,
                        size,
                        unit,
                        boundingBox,
                    });
                }
            } catch (error) {
                console.error("Error extracting model details:", error);
            }
        }
    }, [model, state.model]);

    // Handle material selection
    useEffect(() => {
        if (selection.type === "Material" && selection.materialIndex !== undefined) {
            const ovModel = getOVModel();
            if (ovModel && ovModel.GetMaterial) {
                try {
                    const material = ovModel.GetMaterial(selection.materialIndex);
                    if (material) {
                        setMaterialDetails({
                            index: selection.materialIndex,
                            name: material.name || `Material ${selection.materialIndex}`,
                            type: material.type === 1 ? "Phong" : "Physical",
                            source: material.source === 0 ? "Model" : "Default",
                            color: material.color,
                            ambient: material.ambient,
                            specular: material.specular,
                            opacity: material.opacity,
                            metalness: material.metalness,
                            roughness: material.roughness,
                            vertexColors: material.vertexColors,
                            // Texture maps
                            diffuseMap: material.diffuseMap,
                            bumpMap: material.bumpMap,
                            normalMap: material.normalMap,
                            emissiveMap: material.emissiveMap,
                            specularMap: material.specularMap,
                            metalnessMap: material.metalnessMap,
                        });
                    }
                } catch (error) {
                    console.warn("Error getting material details:", error);
                    setMaterialDetails(null);
                }
            }
        } else {
            setMaterialDetails(null);
        }
    }, [selection, model, state.model]);

    const calculateVolume = async () => {
        const ovModel = getOVModel();
        const OV = getOVLibrary();
        if (ovModel && OV) {
            try {
                // Check if model is two-manifold first
                const isTwoManifold = OV.IsTwoManifold ? OV.IsTwoManifold(ovModel) : true;
                if (!isTwoManifold) {
                    setVolume(-1); // Indicate non-manifold
                    return;
                }
                const calculatedVolume = OV.CalculateVolume(ovModel);
                setVolume(calculatedVolume);
            } catch (error) {
                console.error("Error calculating volume:", error);
                setVolume(-1);
            }
        }
    };

    const calculateSurface = async () => {
        const ovModel = getOVModel();
        const OV = getOVLibrary();
        if (ovModel && OV) {
            try {
                const calculatedSurface = OV.CalculateSurfaceArea(ovModel);
                setSurface(calculatedSurface);
            } catch (error) {
                console.error("Error calculating surface area:", error);
                setSurface(-1);
            }
        }
    };

    const getTextureMapName = (textureMap: any) => {
        if (!textureMap || !textureMap.name) return null;
        // Extract filename from path
        const parts = textureMap.name.split("/");
        return parts[parts.length - 1];
    };

    const renderAssetProperties = () => {
        const ovModel = getOVModel();
        if (!ovModel || !ovModel.PropertyGroupCount || ovModel.PropertyGroupCount() === 0) {
            return null;
        }

        const propertyGroups = [];
        for (let i = 0; i < ovModel.PropertyGroupCount(); i++) {
            const propertyGroup = ovModel.GetPropertyGroup(i);
            if (propertyGroup) {
                const properties = [];
                for (let j = 0; j < propertyGroup.PropertyCount(); j++) {
                    const property = propertyGroup.GetProperty(j);
                    if (property) {
                        properties.push(property);
                    }
                }
                propertyGroups.push({
                    name: propertyGroup.name,
                    properties,
                });
            }
        }

        return propertyGroups.map((group, groupIndex) => (
            <React.Fragment key={groupIndex}>
                <div className="ov_property_table_row group" title={group.name}>
                    {group.name}
                </div>
                {group.properties.map((property: any, propIndex: number) => (
                    <div key={propIndex} className="ov_property_table_row ingroup">
                        <div
                            className="ov_property_table_cell ov_property_table_name"
                            title={property.name}
                        >
                            {property.name}:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {property.value?.toString() || "N/A"}
                        </div>
                    </div>
                ))}
            </React.Fragment>
        ));
    };

    if (!model) {
        return (
            <div className="ov-empty-state">
                <p>No model loaded</p>
            </div>
        );
    }

    // If material is selected, show material properties (like original)
    if (materialDetails) {
        return (
            <div className="ov_property_table">
                <div className="ov_property_table_row">
                    <div className="ov_property_table_cell ov_property_table_name">Source:</div>
                    <div className="ov_property_table_cell ov_property_table_value">
                        {materialDetails.source}
                    </div>
                </div>
                <div className="ov_property_table_row">
                    <div className="ov_property_table_cell ov_property_table_name">Type:</div>
                    <div className="ov_property_table_cell ov_property_table_value">
                        {materialDetails.type}
                    </div>
                </div>

                {materialDetails.vertexColors ? (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">Color:</div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            Vertex colors
                        </div>
                    </div>
                ) : (
                    materialDetails.color && (
                        <div className="ov_property_table_row">
                            <div className="ov_property_table_cell ov_property_table_name">
                                Color:
                            </div>
                            <div className="ov_property_table_cell ov_property_table_value">
                                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                    <div
                                        className="ov_color_circle"
                                        style={{
                                            backgroundColor: `rgb(${Math.round(
                                                materialDetails.color.r || 200
                                            )}, ${Math.round(
                                                materialDetails.color.g || 200
                                            )}, ${Math.round(materialDetails.color.b || 200)})`,
                                        }}
                                    ></div>
                                    #
                                    {(
                                        (1 << 24) +
                                        (Math.round(materialDetails.color.r || 200) << 16) +
                                        (Math.round(materialDetails.color.g || 200) << 8) +
                                        Math.round(materialDetails.color.b || 200)
                                    )
                                        .toString(16)
                                        .slice(1)
                                        .toUpperCase()}
                                </div>
                            </div>
                        </div>
                    )
                )}

                {materialDetails.type === "Phong" && materialDetails.ambient && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Ambient:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                <div
                                    className="ov_color_circle"
                                    style={{
                                        backgroundColor: `rgb(${Math.round(
                                            materialDetails.ambient.r || 200
                                        )}, ${Math.round(
                                            materialDetails.ambient.g || 200
                                        )}, ${Math.round(materialDetails.ambient.b || 200)})`,
                                    }}
                                ></div>
                                #
                                {(
                                    (1 << 24) +
                                    (Math.round(materialDetails.ambient.r || 200) << 16) +
                                    (Math.round(materialDetails.ambient.g || 200) << 8) +
                                    Math.round(materialDetails.ambient.b || 200)
                                )
                                    .toString(16)
                                    .slice(1)
                                    .toUpperCase()}
                            </div>
                        </div>
                    </div>
                )}

                {materialDetails.type === "Phong" && materialDetails.specular && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Specular:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                <div
                                    className="ov_color_circle"
                                    style={{
                                        backgroundColor: `rgb(${Math.round(
                                            materialDetails.specular.r || 200
                                        )}, ${Math.round(
                                            materialDetails.specular.g || 200
                                        )}, ${Math.round(materialDetails.specular.b || 200)})`,
                                    }}
                                ></div>
                                #
                                {(
                                    (1 << 24) +
                                    (Math.round(materialDetails.specular.r || 200) << 16) +
                                    (Math.round(materialDetails.specular.g || 200) << 8) +
                                    Math.round(materialDetails.specular.b || 200)
                                )
                                    .toString(16)
                                    .slice(1)
                                    .toUpperCase()}
                            </div>
                        </div>
                    </div>
                )}

                {materialDetails.type === "Physical" && materialDetails.metalness !== undefined && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Metalness:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {(materialDetails.metalness * 100).toFixed(1)}%
                        </div>
                    </div>
                )}

                {materialDetails.type === "Physical" && materialDetails.roughness !== undefined && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Roughness:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {(materialDetails.roughness * 100).toFixed(1)}%
                        </div>
                    </div>
                )}

                {materialDetails.opacity !== undefined && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Opacity:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {(materialDetails.opacity * 100).toFixed(1)}%
                        </div>
                    </div>
                )}

                {/* Texture Maps */}
                {getTextureMapName(materialDetails.diffuseMap) && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Diffuse Map:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {getTextureMapName(materialDetails.diffuseMap)}
                        </div>
                    </div>
                )}
                {getTextureMapName(materialDetails.bumpMap) && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Bump Map:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {getTextureMapName(materialDetails.bumpMap)}
                        </div>
                    </div>
                )}
                {getTextureMapName(materialDetails.normalMap) && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Normal Map:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {getTextureMapName(materialDetails.normalMap)}
                        </div>
                    </div>
                )}
                {getTextureMapName(materialDetails.emissiveMap) && (
                    <div className="ov_property_table_row">
                        <div className="ov_property_table_cell ov_property_table_name">
                            Emissive Map:
                        </div>
                        <div className="ov_property_table_cell ov_property_table_value">
                            {getTextureMapName(materialDetails.emissiveMap)}
                        </div>
                    </div>
                )}
                {materialDetails.type === "Phong" &&
                    getTextureMapName(materialDetails.specularMap) && (
                        <div className="ov_property_table_row">
                            <div className="ov_property_table_cell ov_property_table_name">
                                Specular Map:
                            </div>
                            <div className="ov_property_table_cell ov_property_table_value">
                                {getTextureMapName(materialDetails.specularMap)}
                            </div>
                        </div>
                    )}
                {materialDetails.type === "Physical" &&
                    getTextureMapName(materialDetails.metalnessMap) && (
                        <div className="ov_property_table_row">
                            <div className="ov_property_table_cell ov_property_table_name">
                                Metallic Map:
                            </div>
                            <div className="ov_property_table_cell ov_property_table_value">
                                {getTextureMapName(materialDetails.metalnessMap)}
                            </div>
                        </div>
                    )}
            </div>
        );
    }

    // Default: Show object/model properties (like original)
    if (!objectDetails) {
        return (
            <div className="ov-empty-state">
                <p>Loading model details...</p>
            </div>
        );
    }

    return (
        <div className="ov_property_table">
            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Vertices:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {objectDetails.vertices.toLocaleString()}
                </div>
            </div>

            {objectDetails.lineSegments > 0 && (
                <div className="ov_property_table_row">
                    <div className="ov_property_table_cell ov_property_table_name">Lines:</div>
                    <div className="ov_property_table_cell ov_property_table_value">
                        {objectDetails.lineSegments.toLocaleString()}
                    </div>
                </div>
            )}

            {objectDetails.triangles > 0 && (
                <div className="ov_property_table_row">
                    <div className="ov_property_table_cell ov_property_table_name">Triangles:</div>
                    <div className="ov_property_table_cell ov_property_table_value">
                        {objectDetails.triangles.toLocaleString()}
                    </div>
                </div>
            )}

            {objectDetails.unit !== "Unknown" && (
                <div className="ov_property_table_row">
                    <div className="ov_property_table_cell ov_property_table_name">Unit:</div>
                    <div className="ov_property_table_cell ov_property_table_value">
                        {objectDetails.unit}
                    </div>
                </div>
            )}

            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Size X:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {objectDetails.size.x.toFixed(2)}
                </div>
            </div>
            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Size Y:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {objectDetails.size.y.toFixed(2)}
                </div>
            </div>
            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Size Z:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {objectDetails.size.z.toFixed(2)}
                </div>
            </div>

            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Volume:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {volume === null ? (
                        <div className="ov_property_table_button" onClick={calculateVolume}>
                            Calculate...
                        </div>
                    ) : volume === -1 ? (
                        "-"
                    ) : (
                        volume.toFixed(3)
                    )}
                </div>
            </div>

            <div className="ov_property_table_row">
                <div className="ov_property_table_cell ov_property_table_name">Surface:</div>
                <div className="ov_property_table_cell ov_property_table_value">
                    {surface === null ? (
                        <div className="ov_property_table_button" onClick={calculateSurface}>
                            Calculate...
                        </div>
                    ) : surface === -1 ? (
                        "-"
                    ) : (
                        surface.toFixed(3)
                    )}
                </div>
            </div>

            {/* Asset Properties */}
            {renderAssetProperties()}
        </div>
    );
};
