/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useEffect, useRef, useState } from "react";
import { Storage } from "aws-amplify";
import * as THREE from 'three';
import initOpenCascade from "opencascade.js";

import {
    Group,
    AmbientLight,
    DirectionalLight,
    PerspectiveCamera,
    Scene,
    WebGLRenderer,
    Color,
    Mesh,
    MeshStandardMaterial,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls';

// write normal buffer
function createNormals(openCascade, triangulation, myFace, myT, aLocation) {
    const pc = new openCascade.Poly_Connect_2(myT);
    const myNormal = new openCascade.TColgp_Array1OfDir_2(1, triangulation.NbNodes());
    openCascade.StdPrs_ToolTriangulatedShape.Normal(myFace, pc, myNormal);

    let normals = new Float32Array(myNormal.Length() * 3);
    for (let i = myNormal.Lower(); i <= myNormal.Upper(); i++) {
        const t1 = aLocation.Transformation();
        const d1 = myNormal.Value(i);
        const d = d1.Transformed(t1);

        normals[3 * (i - 1)] = d.X();
        normals[3 * (i - 1) + 1] = d.Y();
        normals[3 * (i - 1) + 2] = d.Z();

        t1.delete();
        d1.delete();
        d.delete();
    }
    pc.delete();
    myNormal.delete();
    return normals;
}

function createVertexBuffer(triangulation, aLocation) {
    let vertices = new Float32Array(triangulation.NbNodes() * 3);
    // write vertex buffer
    for (let i = 1; i <= triangulation.NbNodes(); i++) {
        const t1 = aLocation.Transformation();
        const p = triangulation.Node(i);
        const p1 = p.Transformed(t1);
        vertices[3 * (i - 1)] = p1.X();
        vertices[3 * (i - 1) + 1] = p1.Y();
        vertices[3 * (i - 1) + 2] = p1.Z();
        p.delete();
        t1.delete();
        p1.delete();
    }
    return vertices;
}

function createIndices(openCascade, myFace, myT) {
    // write triangle buffer
    const orient = myFace.Orientation_1();
    const triangles = myT.get().Triangles();
    let indices;
    let triLength = triangles.Length() * 3;
    if (triLength > 65535)
        indices = new Uint32Array(triLength);
    else
        indices = new Uint16Array(triLength);

    for (let nt = 1; nt <= myT.get().NbTriangles(); nt++) {
        const t = triangles.Value(nt);
        let n1 = t.Value(1);
        let n2 = t.Value(2);
        let n3 = t.Value(3);
        if (orient !== openCascade.TopAbs_Orientation.TopAbs_FORWARD) {
            let tmp = n1;
            n1 = n2;
            n2 = tmp;
        }

        indices[3 * (nt - 1)] = n1 - 1;
        indices[3 * (nt - 1) + 1] = n2 - 1;
        indices[3 * (nt - 1) + 2] = n3 - 1;
        t.delete();
    }
    triangles.delete();
    return indices;
}

function createGeometry(vertices, normals, indices) {
    let geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
    geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));
    return geometry;
}

function visualize(openCascade, shape) {
    let geometries = []
    const ExpFace = new openCascade.TopExp_Explorer_1();
    for (ExpFace.Init(shape, openCascade.TopAbs_ShapeEnum.TopAbs_FACE, openCascade.TopAbs_ShapeEnum.TopAbs_SHAPE); ExpFace.More(); ExpFace.Next()) {
        const myShape = ExpFace.Current();
        const myFace = openCascade.TopoDS.Face_1(myShape);
        let inc;
        try {
            //in case some of the faces can not been visualized
            inc = new openCascade.BRepMesh_IncrementalMesh_2(myFace, 0.1, false, 0.5, false);
        } catch (e) {
            console.error('face visualizi<ng failed');
            continue;
        }

        const aLocation = new openCascade.TopLoc_Location_1();
        const myT = openCascade.BRep_Tool.Triangulation(myFace, aLocation, 0 /* == Poly_MeshPurpose_NONE */);
        if (myT.IsNull()) {
            continue;
        }

        const triangulation = myT.get();

        const vertices = createVertexBuffer(triangulation, aLocation);
        const normals = createNormals(openCascade, triangulation, myFace, myT, aLocation);
        const indices = createIndices(openCascade, myFace, myT);

        geometries.push(createGeometry(vertices, normals, indices));

        aLocation.delete();
        myT.delete();
        inc.delete();
        myFace.delete();
        myShape.delete();
    }
    ExpFace.delete();
    return geometries;
}

const load = async (openCascade, addFunction, scene, reader, fileName) => {
    const readResult = reader.ReadFile(fileName);
    if (readResult === openCascade.IFSelect_ReturnStatus.IFSelect_RetDone) {
        console.log("file loaded successfully! Converting to OCC now...");

        // Translate all transferable roots to OpenCascade
        const numRootsTransferred = reader.TransferRoots(new openCascade.Message_ProgressRange_1());
        console.log("run roots transferred", numRootsTransferred);
        // Obtain the results of translation in one OCCT shape
        const stepShape = reader.OneShape();
        console.log("converted successfully!  Triangulating now...");

        // Out with the old, in with the new!
        scene.remove(scene.getObjectByName("shape"));
        await addFunction(openCascade, stepShape, scene);
        console.log("triangulated and added to the scene!");

        // Remove the file when we're done (otherwise we run into errors on reupload)
        openCascade.FS.unlink(`/${fileName}`);
    } else {
        console.error("Something in OCCT went wrong trying to read ");
    }
};

const setupThreeJSViewport = (viewport) => {
    var scene = new Scene();
    // const aspect = window.innerWidth / window.innerHeight;
    var camera = new PerspectiveCamera(75, viewport.getBoundingClientRect().width / viewport.getBoundingClientRect().height, 0.1, 1000);

    var renderer = new WebGLRenderer({ antialias: true });
    viewport.appendChild(renderer.domElement);

    function viewportSize() {
        const viewportRect = viewport.getBoundingClientRect();
        const width = viewportRect.width;
        const height = viewportRect.height;
        renderer.setSize(viewportRect.width, viewportRect.height);
        renderer.domElement.width = viewportRect.width;
        renderer.domElement.height = viewportRect.height;
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    }

    viewportSize();
    const obs = new ResizeObserver(viewportSize);
    obs.observe(viewport);

    const light = new AmbientLight(0x404040);
    scene.add(light);
    const directionalLight = new DirectionalLight(0xffffff, 0.5);
    directionalLight.position.set(0.5, 0.5, 0.5);
    scene.add(directionalLight);

    camera.position.set(0, 50, 100);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.screenSpacePanning = true;
    controls.target.set(0, 0, 0);
    controls.update();

    function animate() {
        requestAnimationFrame(animate);
        renderer.render(scene, camera);
    }
    animate();
    return scene;
}


const addShapeToScene = async (openCascade, shape, scene) => {
    const objectMat = new MeshStandardMaterial({
        color: new Color(0.9, 0.9, 0.9)
    });

    let geometries = visualize(openCascade, shape);

    let group = new Group();
    geometries.forEach(geometry => {
        group.add(new Mesh(geometry, objectMat));
    });

    group.name = "shape";
    group.rotation.x = -Math.PI / 2;
    scene.add(group);
}

export default function OpenCascadeViewer({ assetKey }) {
    const viewport = useRef(null);
    const [loaded, setLoaded] = useState(false);
    const [openCascade, setOpenCascade] = useState(null);
    const [scene, setScene] = useState(null);

    const fileType = "step";
    const fileName = `file.${fileType}`

    useEffect(() => {
        if (!openCascade) {
            console.log("loading opencascade");
            initOpenCascade().then(setOpenCascade, (err) => console.log("err?", err));
        }
    }, [openCascade]);

    useEffect(() => {

        if (loaded) {
            return;
        }
        if (!openCascade) {
            return;
        }

        console.log("load file");
        const fetch = async () => {
            const file = await Storage.get(assetKey, { download: true });
            file.Body.text().then(async fileText => {
                // Writes the uploaded file to Emscripten's Virtual Filesystem
                openCascade.FS.createDataFile("/", fileName, fileText, true, true);
                console.log("file loaded");
            }).then(() => setLoaded(true));
        };
        fetch();

    }, [loaded, openCascade, assetKey, fileName]);

    useEffect(() => {
        if (scene) {
            return;
        }
        const s = setupThreeJSViewport(viewport.current);
        setScene(s);
    }, [scene]);

    useEffect(() => {
        if (!loaded || !openCascade || !scene) {
            return;
        }

        const reader = new openCascade.STEPControl_Reader_1();
        load(openCascade, addShapeToScene, scene, reader, fileName);

    }, [loaded, openCascade, scene, fileName])

    return (
        <div ref={viewport} style={{ width: "100%", height: "100%" }} />
    );
}