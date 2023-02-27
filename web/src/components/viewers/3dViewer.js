/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Engine, Scene } from "@babylonjs/core";
import React, { useEffect, useRef, useState } from "react";
import { Storage } from "aws-amplify";
import * as BABYLON from "babylonjs";
import "babylonjs-loaders";

const { Vector3, ArcRotateCamera, Color4 } = BABYLON;

const encompassBounds = (meshes, filter = 0) => {
  let boundingInfo = meshes[0].getBoundingInfo();
  let min = boundingInfo.minimum.add(meshes[0].position);
  let max = boundingInfo.maximum.add(meshes[0].position);
  for (let i = 1; i < meshes.length; i++) {
    if (meshes[i].getTotalVertices() <= filter) {
      continue;
    }
    boundingInfo = meshes[i].getBoundingInfo();
    min = BABYLON.Vector3.Minimize(
      min,
      boundingInfo.minimum.add(meshes[i].position)
    );
    max = BABYLON.Vector3.Maximize(
      max,
      boundingInfo.maximum.add(meshes[i].position)
    );
  }
  return new BABYLON.BoundingInfo(min, max);
};

const totalBoundingInfo = (meshes) => {
  var boundingInfo = meshes[0].getBoundingInfo();
  var min = boundingInfo.minimum.add(meshes[0].position);
  var max = boundingInfo.maximum.add(meshes[0].position);
  for (var i = 1; i < meshes.length; i++) {
    boundingInfo = meshes[i].getBoundingInfo();
    min = BABYLON.Vector3.Minimize(
      min,
      boundingInfo.minimum.add(meshes[i].position)
    );
    max = BABYLON.Vector3.Maximize(
      max,
      boundingInfo.maximum.add(meshes[i].position)
    );
  }
  return new BABYLON.BoundingInfo(min, max);
};

export default function ThreeDViewer(props) {
  const reactCanvas = useRef(null);
  const { assetKey, engineOptions, adaptToDeviceRatio, sceneOptions, ...rest } =
    props;
  const [loaded, setLoaded] = useState(false);
  const antialias = true;

  //defaults and scene state, @todo move
  let W = 900,
    H = 900,
    D = 900;
  let arcCamera;
  let freeCamera;
  let camera2;
  let engine;
  let VR_mode = false;
  let cursorDown = false;
  let yScale = 2;
  let xScale = 2;
  let oldCamState = { x: 0, y: 0 };
  let cursorState = { x: 0, y: 0 };
  let cacheScene;
  let render = true;

  setTimeout(() => {
    const canvasDivs = document.getElementsByTagName("canvas");
    for (let i = 0; i < canvasDivs.length; i++) {
      canvasDivs[i].addEventListener("wheel", (e) => e.preventDefault(), {
        passive: false,
      });

      canvasDivs[i].addEventListener("focus", (event) => {
        render = true;
      });

      canvasDivs[i].addEventListener("mouseover", (event) => {
        render = true;
      });

      canvasDivs[i].addEventListener("mouseout", (event) => {
        render = false;
      });

      canvasDivs[i].addEventListener("blur", (event) => {
        render = false;
      });
    }
  }, 0);

  const onSceneReady = async (scene) => {
    const canvas = scene.getEngine().getRenderingCanvas();

    let oVec = new Vector3(0, 0, 0);
    let oPos = new Vector3(0, 15, 25);
    arcCamera = new ArcRotateCamera("Camera", 0, 0, 10, oVec, scene);
    let light1 = new BABYLON.HemisphericLight(
      "light1",
      new BABYLON.Vector3(1, 1, 0),
      scene
    );

    arcCamera.attachControl(canvas, true);
    arcCamera.setPosition(oPos);
    scene._useRightHandedSystem = true;
    scene.clearColor = new Color4(211 / 255, 211 / 255, 211 / 255, 1);

    freeCamera = new BABYLON.UniversalCamera(
      "camera1",
      new BABYLON.Vector3(350, 350, 1350),
      scene
    );
    freeCamera.setTarget(BABYLON.Vector3.Zero());
    freeCamera.inputs.addMouseWheel();
    freeCamera.speed = 50;

    scene.onPointerDown = function (evt, pic) {
      cursorDown = true;
      oldCamState.x = freeCamera.position.x;
      oldCamState.y = freeCamera.position.y;

      cursorState.x = evt.clientX;
      cursorState.y = evt.clientY;
    };
    scene.onPointerUp = function (evt, pic) {
      cursorDown = false;
    };

    scene.onPointerMove = function (evt, pic) {
      if (cursorDown) {
        freeCamera.position.y =
          oldCamState.y + yScale * 1 * (evt.clientY - cursorState.y);
        freeCamera.position.x =
          oldCamState.x + xScale * -1 * (evt.clientX - cursorState.x);
      }
    };

    if (navigator.getVRDisplays)
      camera2 = new BABYLON.WebVRFreeCamera(
        "camera1",
        new BABYLON.Vector3(0, 1, 0),
        scene,
        false,
        { trackPosition: true }
      );
    else
      camera2 = new BABYLON.VRDeviceOrientationArcRotateCamera(
        "vrCam",
        0.76,
        1.41,
        950,
        BABYLON.Vector3.Zero(),
        scene
      );
    camera2.attachControl(canvas, true);
  };

  const onRender = (scene) => {
    const engine = scene.getEngine();
    engine.resize();
  };

  useEffect(() => {
    if (reactCanvas.current) {
      engine = new Engine(
        reactCanvas.current,
        antialias,
        engineOptions,
        adaptToDeviceRatio
      );
      const scene = new Scene(engine, sceneOptions);
      cacheScene = scene;
      if (scene.isReady()) {
        onSceneReady(scene);
      } else {
        scene.onReadyObservable.addOnce((scene) => onSceneReady(scene));
      }
      setTimeout(() => (render = false), 100);
      engine.runRenderLoop(() => {
        if (render) {
          if (typeof onRender === "function") {
            onRender(scene);
          }
          scene.render();
        }
      });

      const resize = () => {
        scene.getEngine().resize();
      };

      if (window) {
        window.addEventListener("resize", resize);
      }

      return () => {
        scene.getEngine().dispose();

        if (window) {
          window.removeEventListener("resize", resize);
        }
      };
    }
  }, [reactCanvas]);

  useEffect(() => {
    let config = {
      download: false,
      expires: 10,
    };
    const loadAsset = async () => {
      let parent = new BABYLON.Mesh("parent", cacheScene);
      await Storage.get(assetKey, config).then((ps_url) => {
        BABYLON.SceneLoader.Append(ps_url, "", cacheScene, (sceneObj) => {
          try {
            const meshes = cacheScene.meshes;
            for (let i = 0; i < meshes.length; i++) {
              const mesh = meshes[i];
              // console.log(mesh);
              mesh.enableEdgesRendering(0.9999999999);
              mesh.edgesWidth = 25.0;
              mesh.edgesColor = new BABYLON.Color4(0, 0, 1, 1);
              mesh.position.y = -12;
              const oldPos = mesh.position;
              const pos = new Vector3(0, 0, 0) - oldPos;
              mesh.setAbsolutePosition(pos);
            }
            parent.setBoundingInfo(encompassBounds(meshes, 500));
            arcCamera.setTarget(
              totalBoundingInfo(meshes).boundingBox.centerWorld
            );
            let radius = parent.getBoundingInfo().boundingSphere.radiusWorld;
            if (radius > 10) {
              let divider = new Vector3(radius, radius, radius);
              for (let i = 0; i < meshes.length; i++) {
                const mesh = meshes[i];
                mesh.scaling.divide(divider);
              }
              parent.setBoundingInfo(encompassBounds(meshes, 500));
            }
            let aspectRatio = sceneObj.getEngine().getAspectRatio(arcCamera);
            let halfMinFov = arcCamera.fov / 2;
            if (aspectRatio < 1) {
              halfMinFov = Math.atan(aspectRatio * Math.tan(arcCamera.fov / 2));
            }
            let viewRadius = Math.abs(radius / Math.sin(halfMinFov));
            arcCamera.radius = viewRadius;
            console.log("Loaded");
            render = true;
            setTimeout(() => (render = false), 100);
          } catch (e) {
            console.log(e);
          }
        });
      });
    };
    if (!loaded && assetKey !== "") {
      loadAsset();
      setLoaded(true);
    }
  }, [loaded, assetKey]);

  return <canvas ref={reactCanvas} height={600} {...rest} />;
}
