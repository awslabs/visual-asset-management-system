/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    Scene,
    Mesh,
    Color3,
    Vector3,
    MeshBuilder,
    Color4,
    DynamicTexture,
    StandardMaterial,
    AbstractMesh,
    PointsCloudSystem,
} from "babylonjs";
import { readRemoteFile } from "react-papaparse";
import FCS from "fcs";
import arrayBufferToBuffer from "arraybuffer-to-buffer";

export const buildScatterPlot = ({
    dimensions,
    labels,
    scene,
    points,
}: {
    dimensions: number[];
    labels: any;
    scene: Scene;
    points: Vector3[];
}) => {
    const scatterPlot: any = {};
    scatterPlot.scene = scene;
    scatterPlot.dimensions = { width: 100, height: 100, depth: 100 };

    if (dimensions.length > 0) {
        if (dimensions[0] !== undefined)
            scatterPlot.dimensions.width = parseFloat(dimensions[0].toString());
        if (dimensions[1] !== undefined)
            scatterPlot.dimensions.height = parseFloat(dimensions[1].toString());
        if (dimensions[2] !== undefined)
            scatterPlot.dimensions.depth = parseFloat(dimensions[2].toString());
    }

    scatterPlot.labelsInfo = {
        x: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
        y: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
        z: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
    };

    if (Object.keys(labels).length > 0) {
        if (labels.x !== undefined && Array.isArray(labels.x)) scatterPlot.labelsInfo.x = labels.x;
        if (labels.y !== undefined && Array.isArray(labels.y)) scatterPlot.labelsInfo.y = labels.y;
        if (labels.z !== undefined && Array.isArray(labels.z)) scatterPlot.labelsInfo.z = labels.z;
    }

    scatterPlot.axis = [];
    scatterPlot._materials = [];
    scatterPlot._meshes = [];
    scatterPlot._textures = [];
    scatterPlot.shape = null;
    scatterPlot.mesh = new Mesh("scatterPlot", scatterPlot.scene);

    // Internals
    scatterPlot._depth = scatterPlot.dimensions.depth / 2;
    scatterPlot._width = scatterPlot.dimensions.width / 2;
    scatterPlot._height = scatterPlot.dimensions.height / 2;
    scatterPlot._a = scatterPlot.labelsInfo.y.length;
    scatterPlot._b = scatterPlot.labelsInfo.x.length;
    scatterPlot._c = scatterPlot.labelsInfo.z.length;
    scatterPlot._color = new Color3(0.6, 0.6, 0.6);
    scatterPlot._defPos = scatterPlot.mesh.position.clone();

    // Add grid function
    scatterPlot.addGrid = function (
        width: number,
        height: number,
        linesHeight: number,
        linesWidth: number,
        position: Vector3,
        rotation: Vector3,
        highlight?: boolean
    ) {
        const stepw = (2 * width) / linesWidth;
        const steph = (2 * height) / linesHeight;
        const verts = [];

        for (let i = -width; i < width - stepw; i += stepw) {
            verts.push([
                new Vector3(-height, i + stepw / 2, 0),
                new Vector3(height, i + stepw / 2, 0),
            ]);
        }
        for (let i = -height; i < height - steph; i += steph) {
            verts.push([
                new Vector3(i + steph / 2, -width, 0),
                new Vector3(i + steph / 2, width, 0),
            ]);
        }

        scatterPlot.BBJSaddGrid(verts, position, rotation, highlight);
    };

    scatterPlot.BBJSaddGrid = function (
        verts: Vector3[][],
        position: Vector3,
        rotation: Vector3,
        highlight?: boolean
    ) {
        const line = MeshBuilder.CreateLineSystem(
            "linesystem",
            { lines: verts, updatable: false },
            scatterPlot.scene
        );
        if (highlight) {
            line.color = new Color3(0.2, 0.2, 0.2);
        } else {
            line.color = new Color3(0.1, 0.1, 0.1);
        }

        line.position = position;
        line.rotation = rotation;
        line.parent = scatterPlot.mesh;
        scatterPlot.axis.push(line);
        scatterPlot._meshes.push(line);
    };

    // Add label function
    scatterPlot.addLabel = function (length: number, data: any[], axis: string) {
        let parent = new Mesh("label_" + axis, scatterPlot.scene);

        const step = (length * 2) / data.length;

        let counter = 0;
        for (let i = -length; i < length - step / 2; i += step) {
            let label;
            if (data[counter] === 0) {
                label = scatterPlot.BBJSaddLabel(data[counter]);
            } else {
                label = scatterPlot.BBJSaddLabel(axis + ": " + data[counter]);
            }
            if (axis === "x") {
                label.position = new Vector3(i + step / 2, 0, 0);
            } else if (axis === "y") {
                label.position = new Vector3(0, i + step / 2, 0);
            } else if (axis === "z") {
                label.position = new Vector3(0, 0, i + step / 2);
            }
            label.parent = parent;
            scatterPlot._meshes.push(label);
            counter++;
        }
    };

    scatterPlot.BBJSaddLabel = function (text: string) {
        const planeTexture = new DynamicTexture(
            "dynamic texture",
            { width: 4000, height: 4000 },
            scatterPlot.scene,
            true,
            DynamicTexture.TRILINEAR_SAMPLINGMODE
        );
        planeTexture.drawText(
            text,
            null,
            null,
            "bold 1024px monospace",
            "white",
            "transparent",
            true
        );

        const material = new StandardMaterial("outputplane", scatterPlot.scene);
        material.emissiveTexture = planeTexture;
        material.opacityTexture = planeTexture;
        material.backFaceCulling = true;
        material.disableLighting = true;
        material.freeze();

        const outputplane = Mesh.CreatePlane("outputplane", 50, scatterPlot.scene, false);
        outputplane.billboardMode = AbstractMesh.BILLBOARDMODE_ALL;
        outputplane.material = material;

        scatterPlot._meshes.push(outputplane);
        scatterPlot._materials.push(material);
        scatterPlot._textures.push(planeTexture);

        return outputplane;
    };

    // Draw function
    scatterPlot.draw = function () {
        const convertedPoints: Vector3[] = [];
        if (points.length > 0) {
            for (let i = 0; i < points.length; i++) {
                convertedPoints.push(
                    new Vector3(
                        points[i].x * (this.dimensions.width / this._b),
                        points[i].y * (this.dimensions.height / this._a),
                        points[i].z * (this.dimensions.depth / this._c)
                    )
                );
            }
        }

        if (convertedPoints.length > 0) {
            this._defPos = this.mesh.position.clone();
            this.mesh.position = new Vector3(this._width, this._height, this._depth);

            const PCS = new PointsCloudSystem("pcs", 1, scene);

            const pointGenerator = function (particle: any, i: number) {
                particle.position = new Vector3(
                    convertedPoints[i].x / 5 + 450,
                    convertedPoints[i].y / 5 + 450,
                    convertedPoints[i].z / 5 + 450
                );
                particle.color = new Color4(0, 1, 0, 1);
            };

            PCS.addPoints(points.length, pointGenerator);

            (async () => {
                await PCS.buildMeshAsync();
                scene.registerBeforeRender(function () {
                    PCS.setParticles(0, 999999, true);
                });
                PCS.mesh.position.subtractInPlace(this.mesh.position);
                PCS.mesh.parent = this.mesh;
                this._meshes.push(PCS);
                this.shape = PCS;
                this.mesh.position = this._defPos;
            })();
        }
    };

    // Create items
    scatterPlot.addGrid(
        scatterPlot._height,
        scatterPlot._width,
        scatterPlot._b,
        scatterPlot._a,
        new Vector3(0, 0, 0),
        Vector3.Zero()
    );
    scatterPlot.addGrid(
        scatterPlot._depth,
        scatterPlot._width,
        scatterPlot._b,
        scatterPlot._c,
        new Vector3(0, 0, 0),
        new Vector3(Math.PI / 2, 0, 0),
        true
    );
    scatterPlot.addGrid(
        scatterPlot._height,
        scatterPlot._depth,
        scatterPlot._c,
        scatterPlot._a,
        new Vector3(0, 0, 0),
        new Vector3(0, Math.PI / 2, 0)
    );

    scatterPlot.addLabel(scatterPlot._width, scatterPlot.labelsInfo.x, "x");
    scatterPlot.addLabel(scatterPlot._height, scatterPlot.labelsInfo.y, "y");
    scatterPlot.addLabel(scatterPlot._depth, scatterPlot.labelsInfo.z, "z");

    return scatterPlot;
};

export const readFcsFile = (
    remoteFileUrl: string,
    points: Vector3[],
    scatterPlot: any,
    onComplete: () => void
) => {
    const request = new XMLHttpRequest();
    request.open("GET", remoteFileUrl, true);
    request.responseType = "arraybuffer";
    request.onload = function () {
        const buffer = arrayBufferToBuffer(request.response);
        const fcs = new (FCS as any)({}, buffer);
        if (fcs.dataAsStrings && Array.isArray(fcs.dataAsStrings)) {
            const columnCount = fcs.dataAsStrings[0]
                ?.replace("[", "")
                .replace("]", "")
                .split(",").length;

            for (let i = 0; i < columnCount; i++) {
                const row = fcs.dataAsStrings[i]?.replace("[", "").replace("]", "").split(",");
                if (row.length > 2) {
                    const x = Number(row[row.length - 3]);
                    const y = Number(row[row.length - 2]);
                    const z = Number(row[row.length - 1]);
                    points.push(new Vector3(x, y, z));
                }
            }
            scatterPlot.draw();
            onComplete();
        }
    };
    request.send();
};

export const readCsvFile = (
    remoteFileUrl: string,
    points: Vector3[],
    scatterPlot: any,
    onComplete: () => void
) => {
    readRemoteFile(remoteFileUrl, {
        download: true,
        complete: (results: any) => {
            const { data } = results;
            for (let i = 0; i < data.length; i++) {
                if (data[i].length > 2) {
                    const x = data[i][data[i].length - 3];
                    const y = data[i][data[i].length - 2];
                    const z = data[i][data[i].length - 1];
                    points.push(new Vector3(x, y, z));
                }
            }
            scatterPlot.draw();
            onComplete();
        },
    });
};
