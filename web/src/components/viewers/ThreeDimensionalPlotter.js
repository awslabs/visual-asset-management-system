import { Engine, Scene } from "@babylonjs/core";
import React, { useEffect, useRef, useState } from "react";
import { Storage } from "aws-amplify";
import * as BABYLON from "babylonjs";
import "babylonjs-loaders";
import { readRemoteFile } from "react-papaparse";
import FCS from "fcs";
import arrayBufferToBuffer from "arraybuffer-to-buffer";

let scatterPlot = {};
let points = [];
let PCS = null;
let groupingSize = 20;
let columnName = "srcFile";
let displayUpdated = true;
let hiddenGroups = {};
let selectBox;
let unique = {};
let sourceData = {};
const colors = [
  "#FF0000",
  "#00FF00",
  "#0000FF",
  "#FFFF00",
  "#00FFFF",
  "#FF00FF",
  "#C0C0C0",
  "#808080",
  "#800000",
  "#808000",
  "#008000",
  "#800080",
  "#008080",
  "#000080",
  "#800000",
  "#8B0000",
  "#A52A2A",
  "#B22222",
  "#DC143C",
  "#FF0000",
  "#FF6347",
  "#FF7F50",
  "#CD5C5C",
  "#F08080",
  "#E9967A",
  "#FA8072",
  "#FFA07A",
  "#FF4500",
  "#FF8C00",
  "#FFA500",
  "#FFD700",
  "#B8860B",
  "#DAA520",
  "#EEE8AA",
  "#BDB76B",
  "#F0E68C",
  "#808000",
  "#FFFF00",
  "#9ACD32",
  "#556B2F",
  "#6B8E23",
  "#7CFC00",
  "#7FFF00",
  "#ADFF2F",
  "#006400",
  "#008000",
  "#228B22",
  "#00FF00",
  "#32CD32",
  "#90EE90",
  "#98FB98",
  "#8FBC8F",
  "#00FA9A",
  "#00FF7F",
  "#2E8B57",
  "#66CDAA",
  "#3CB371",
  "#20B2AA",
  "#2F4F4F",
  "#008080",
  "#008B8B",
  "#00FFFF",
  "#00FFFF",
  "#E0FFFF",
  "#00CED1",
  "#40E0D0",
  "#48D1CC",
  "#AFEEEE",
  "#7FFFD4",
  "#B0E0E6",
  "#5F9EA0",
  "#4682B4",
  "#6495ED",
  "#00BFFF",
  "#1E90FF",
  "#ADD8E6",
  "#87CEEB",
  "#87CEFA",
  "#191970",
  "#000080",
  "#00008B",
  "#0000CD",
  "#0000FF",
  "#4169E1",
  "#8A2BE2",
  "#4B0082",
  "#483D8B",
  "#6A5ACD",
  "#7B68EE",
  "#9370DB",
  "#8B008B",
  "#9400D3",
  "#9932CC",
  "#BA55D3",
  "#800080",
  "#D8BFD8",
  "#DDA0DD",
  "#EE82EE",
  "#FF00FF",
  "#DA70D6",
  "#C71585",
  "#DB7093",
  "#FF1493",
  "#FF69B4",
  "#FFB6C1",
  "#FFC0CB",
  "#FAEBD7",
  "#F5F5DC",
  "#FFE4C4",
  "#FFEBCD",
  "#F5DEB3",
  "#FFF8DC",
  "#FFFACD",
  "#FAFAD2",
  "#FFFFE0",
  "#8B4513",
  "#A0522D",
  "#D2691E",
  "#CD853F",
  "#F4A460",
  "#DEB887",
  "#D2B48C",
  "#BC8F8F",
  "#FFE4B5",
  "#FFDEAD",
  "#FFDAB9",
  "#FFE4E1",
  "#FFF0F5",
  "#FAF0E6",
  "#FDF5E6",
  "#FFEFD5",
  "#FFF5EE",
  "#F5FFFA",
  "#708090",
  "#778899",
  "#B0C4DE",
  "#E6E6FA",
  "#FFFAF0",
  "#F0F8FF",
  "#F8F8FF",
  "#F0FFF0",
  "#FFFFF0",
  "#F0FFFF",
  "#FFFAFA",
  "#000000",
  "#696969",
  "#808080",
  "#A9A9A9",
  "#C0C0C0",
  "#D3D3D3",
  "#DCDCDC",
  "#F5F5F5",
];
let isCompare = false;
const babylonColors = colors.map(convertColor);

function convertColor(hexadecimalColor) {
  var rgbColor = hexToRgb(hexadecimalColor);

  var red = Number((rgbColor.r / 255).toFixed(2));
  var green = Number((rgbColor.g / 255).toFixed(2));
  var blue = Number((rgbColor.b / 255).toFixed(2));

  return [red, green, blue];
}

function hexToRgb(hex) {
  var shorthandRegex = /^#?([a-f\d])([a-f\d])([a-f\d])$/i;
  hex = hex.replace(shorthandRegex, function (m, r, g, b) {
    return r + r + g + g + b + b;
  });

  var result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? {
        r: parseInt(result[1], 16),
        g: parseInt(result[2], 16),
        b: parseInt(result[3], 16),
      }
    : null;
}

const buildScatterPlot = ({ dimensions, labels, scene }) => {
  scatterPlot.scene = scene;
  scatterPlot.dimensions = { width: 100, height: 100, depth: 100 };

  if (dimensions.length > 0) {
    if (dimensions[0] != undefined)
      scatterPlot.dimensions.width = parseFloat(dimensions[0]);
    if (dimensions[1] != undefined)
      scatterPlot.dimensions.height = parseFloat(dimensions[1]);
    if (dimensions[2] != undefined)
      scatterPlot.dimensions.depth = parseFloat(dimensions[2]);
  }

  scatterPlot.labelsInfo = {
    x: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
    y: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
    z: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
  };

  if (Object.keys(labels).length > 0) {
    if (labels.x != undefined && Array.isArray(labels.x))
      scatterPlot.labelsInfo.x = labels.x;
    if (labels.y != undefined && Array.isArray(labels.y))
      scatterPlot.labelsInfo.y = labels.y;
    if (labels.z != undefined && Array.isArray(labels.z))
      scatterPlot.labelsInfo.z = labels.z;
  }

  scatterPlot.axis = [];

  //infos for dispose;
  scatterPlot._materials = [];
  scatterPlot._meshes = [];
  scatterPlot._textures = [];

  //the figure
  scatterPlot.shape = null;

  //the entire scatterPlot
  scatterPlot.mesh = new BABYLON.Mesh("scatterPlot", scatterPlot.scene);

  //internals
  scatterPlot._depth = scatterPlot.dimensions.depth / 2;
  scatterPlot._width = scatterPlot.dimensions.width / 2;
  scatterPlot._height = scatterPlot.dimensions.height / 2;
  scatterPlot._a = scatterPlot.labelsInfo.y.length;
  scatterPlot._b = scatterPlot.labelsInfo.x.length;
  scatterPlot._c = scatterPlot.labelsInfo.z.length;
  scatterPlot._color = new BABYLON.Color3(0.6, 0.6, 0.6);
  scatterPlot._defPos = scatterPlot.mesh.position.clone();

  scatterPlot.addGrid = function (
    width,
    height,
    linesHeight,
    linesWidth,
    position,
    rotation,
    highlight
  ) {
    const stepw = (2 * width) / linesWidth,
      steph = (2 * height) / linesHeight;
    const verts = [];

    for (let i = -width; i < width - stepw; i += stepw) {
      verts.push([
        new BABYLON.Vector3(-height, i + stepw / 2, 0),
        new BABYLON.Vector3(height, i + stepw / 2, 0),
      ]);
    }
    for (let i = -height; i < height - steph; i += steph) {
      verts.push([
        new BABYLON.Vector3(i + steph / 2, -width, 0),
        new BABYLON.Vector3(i + steph / 2, width, 0),
      ]);
    }

    scatterPlot.BBJSaddGrid(verts, position, rotation, highlight);
  };

  scatterPlot.BBJSaddGrid = function (verts, position, rotation, hightlight) {
    const line = BABYLON.MeshBuilder.CreateLineSystem(
      "linesystem",
      { lines: verts, updatable: false },
      scatterPlot.scene
    );
    if (hightlight) {
      line.color = new BABYLON.Color4(0.2, 0.2, 0.2, 0.5);
    } else {
      line.color = new BABYLON.Color4(0.2, 0.2, 0.2, 0.05);
    }

    line.position = position;
    line.rotation = rotation;
    line.parent = scatterPlot.mesh;
    scatterPlot.axis.push(line);
    scatterPlot._meshes.push(line);
  };

  scatterPlot.addLabel = function (length, data, axis, position) {
    let parent = new BABYLON.Mesh("label_" + axis, scatterPlot.scene);

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
        label.position = new BABYLON.Vector3(i + step / 2, 0, 0);
      } else if (axis === "y") {
        label.position = new BABYLON.Vector3(0, i + step / 2, 0);
      } else if (axis === "z") {
        label.position = new BABYLON.Vector3(0, 0, i + step / 2);
      }
      label.parent = parent;
      scatterPlot._meshes.push(label);
      counter++;
    }
  };

  scatterPlot.BBJSaddLabel = function (text) {
    const planeTexture = new BABYLON.DynamicTexture(
      "dynamic texture",
      { width: 4000, height: 4000 },
      scatterPlot.scene,
      true,
      BABYLON.DynamicTexture.TRILINEAR_SAMPLINGMODE
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

    const material = new BABYLON.StandardMaterial(
      "outputplane",
      scatterPlot.scene
    );
    material.emissiveTexture = planeTexture;
    material.opacityTexture = planeTexture;
    material.backFaceCulling = true;
    material.disableLighting = true;
    material.freeze();

    const outputplane = BABYLON.Mesh.CreatePlane(
      "outputplane",
      50,
      scatterPlot.scene,
      false
    );
    outputplane.billboardMode = BABYLON.AbstractMesh.BILLBOARDMODE_ALL;
    outputplane.material = material;

    scatterPlot._meshes.push(outputplane);
    scatterPlot._materials.push(material);
    scatterPlot._textures.push(planeTexture);

    return outputplane;
  };

  scatterPlot.setColor = function (color3) {
    if (scatterPlot.axis.length > 0) {
      for (let i = 0; i < scatterPlot.axis.length; i++) {
        scatterPlot.axis[i].color = color3;
      }
    }
  };

  scatterPlot.setPosition = function (vector3) {
    if (scatterPlot.mesh) {
      scatterPlot.mesh.position = vector3;
    }
  };

  scatterPlot.setScaling = function (vector3) {
    if (scatterPlot.mesh) {
      scatterPlot.mesh.scaling = vector3;
    }
  };

  scatterPlot.draw = function (hasSource) {
    var convertedPoints = [];
    if (points.length > 0) {
      for (var i = 0; i < points.length; i++) {
        convertedPoints.push(
          new BABYLON.Vector3(
            points[i].x * (this.dimensions.width / this._b),
            points[i].y * (this.dimensions.height / this._a),
            points[i].z * (this.dimensions.depth / this._c)
          )
        );
      }
    }

    if (convertedPoints.length > 0) {
      this._defPos = this.mesh.position.clone();
      this.mesh.position = new BABYLON.Vector3(
        this._width,
        this._height,
        this._depth
      );

      PCS = new BABYLON.PointsCloudSystem("pcs", 1, scene);

      var pointGenerator = function (particle, i) {
        particle.position = new BABYLON.Vector3(
          convertedPoints[i].x / 5 + 450,
          convertedPoints[i].y / 5 + 450,
          convertedPoints[i].z / 5 + 450
        );
        if (hasSource) {
          let color = babylonColors[unique[sourceData[columnName][i]]];
          particle.color = new BABYLON.Color4(
            color[0],
            color[1],
            color[2],
            0.1
          );
        } else {
          particle.color = new BABYLON.Color4(0, 1, 0, 1);
        }
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

  scatterPlot.dispose = function (allmeshes = false) {
    if (scatterPlot.shape != null) {
      if (scatterPlot.shape.material != undefined)
        scatterPlot.shape.material.dispose();
      scatterPlot.shape.dispose();
      scatterPlot.shape = null;
    }
    if (allmeshes) {
      if (scatterPlot._textures.length > 0) {
        for (var i = 0; i < scatterPlot._textures.length; i++) {
          scatterPlot._textures[i].dispose();
        }
      }
      if (scatterPlot._materials.length > 0) {
        for (var i = 0; i < scatterPlot._materials.length; i++) {
          scatterPlot._materials[i].dispose();
        }
      }
      if (scatterPlot._meshes.length > 0) {
        for (var i = 0; i < scatterPlot._meshes.length; i++) {
          scatterPlot._meshes[i].dispose();
        }
      }
      if (scatterPlot.mesh != null) {
        if (scatterPlot.mesh.material != null)
          scatterPlot.mesh.material.dispose();
        scatterPlot.mesh.dispose();
      }
      scatterPlot._meshes = [];
      scatterPlot._materials = [];
      scatterPlot._textures = [];
      scatterPlot.mesh = null;
    }
  };

  //create items
  scatterPlot.addGrid(
    scatterPlot._height,
    scatterPlot._width,
    scatterPlot._b,
    scatterPlot._a,
    new BABYLON.Vector3(0, 0, 0),
    BABYLON.Vector3.Zero()
  );
  scatterPlot.addGrid(
    scatterPlot._depth,
    scatterPlot._width,
    scatterPlot._b,
    scatterPlot._c,
    new BABYLON.Vector3(0, 0, 0),
    new BABYLON.Vector3(Math.PI / 2, 0, 0),
    true
  );
  scatterPlot.addGrid(
    scatterPlot._height,
    scatterPlot._depth,
    scatterPlot._c,
    scatterPlot._a,
    new BABYLON.Vector3(0, 0, 0),
    new BABYLON.Vector3(0, Math.PI / 2)
  );

  scatterPlot.addLabel(scatterPlot._width, scatterPlot.labelsInfo.x, "x");
  scatterPlot.addLabel(scatterPlot._height, scatterPlot.labelsInfo.y, "y");
  scatterPlot.addLabel(scatterPlot._depth, scatterPlot.labelsInfo.z, "z");

  return scatterPlot;
};

//@todo refactor without side effects, abstract common parts with other visualizers to higher level
const readFcsFile = (remoteFileUrl, render) => {
  const request = new XMLHttpRequest();
  request.open("GET", remoteFileUrl, true);
  request.responseType = "arraybuffer";
  request.onload = function () {
    const buffer = arrayBufferToBuffer(request.response);
    const fcs = new FCS({}, buffer);
    if (fcs.dataAsStrings && Array.isArray(fcs.dataAsStrings)) {
      const columnCount = fcs.dataAsStrings[0]
        ?.replace("[", "")
        .replace("]", "")
        .split(",").length;

      for (let i = 0; i < columnCount; i++) {
        const row = fcs.dataAsStrings[i]
          ?.replace("[", "")
          .replace("]", "")
          .split(",");
        if (row.length > 2) {
          const x = Number(row[row.length - 3]);
          const y = Number(row[row.length - 2]);
          const z = Number(row[row.length - 1]);
          points.push(new BABYLON.Vector3(x, y, z));
        }
      }
      scatterPlot.draw();
      render = true;
      setTimeout(() => (render = false), 100);
    }
  };
  request.send();
};

//@todo refactor without side effects, abstract common parts with other visualizers to higher level
const readCsvFile = (remoteFileUrl, render) => {
  readRemoteFile(remoteFileUrl, {
    complete: (results) => {
      const { data } = results;
      for (let i = 0; i < data.length; i++) {
        if (data[i].length > 2) {
          const x = data[i][data[i].length - 3];
          const y = data[i][data[i].length - 2];
          const z = data[i][data[i].length - 1];
          points.push(new BABYLON.Vector3(x, y, z));
        }
      }
      scatterPlot.draw();
      render = true;
      setTimeout(() => (render = false), 100);
    },
  });
};

export default function ThreeDimensionalPlotter(props) {
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
    scene.clearColor = new BABYLON.Color4(0.1, 0.1, 0.1);

    const light = new BABYLON.HemisphericLight(
      "light1",
      new BABYLON.Vector3(0, 0, 0),
      scene
    );
    light.intensity = 2;
    light.specular = new BABYLON.Color3(0.95, 0.95, 0.81);

    arcCamera = new BABYLON.ArcRotateCamera(
      "camera0",
      Math.PI / 3,
      Math.PI / 3,
      1350,
      BABYLON.Vector3.Zero(),
      scene
    );
    arcCamera.setTarget(new BABYLON.Vector3(0, 0, 0));
    arcCamera.speed = 0.4;
    arcCamera.attachControl(canvas, true);
    arcCamera.fov = 1;

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
          oldCamState.y + yScale * (evt.clientY - cursorState.y);
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

    const scatterPlot = buildScatterPlot({
      dimensions: [W, H, D],
      labels: {
        x: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
        y: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
        z: [-25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25],
      },
      scene,
    });
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
      points = [];
      await Storage.get(assetKey, config).then((remoteFileUrl) => {
        if (assetKey.indexOf(".fcs") !== -1) {
          try {
            readFcsFile(remoteFileUrl, render);
          } catch (error) {
            console.log(error);
          }
        } else {
          try {
            readCsvFile(remoteFileUrl, render);
          } catch (error) {
            console.log(error);
          }
        }
      });
    };
    if (!loaded && assetKey !== "") {
      loadAsset();
      setLoaded(true);
    }
  }, [loaded, assetKey]);

  return <canvas ref={reactCanvas} height={600} {...rest} />;
}
