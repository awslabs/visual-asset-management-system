/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The rig is asserted against a stubbed Three.js rather than the real one: `PMREMGenerator` needs a
 * live WebGL context, which jsdom does not provide, so a test using the real library could only ever
 * assert that setup threw. The stub records what was constructed and what was disposed, which is what
 * the defect was about — a scene with no `environment` renders every metallic glTF material black.
 */

import {
    applySceneEnvironment,
    applySceneLighting,
    DEFAULT_AMBIENT_INTENSITY,
    DEFAULT_DIRECTIONAL_INTENSITY,
    DEFAULT_ENVIRONMENT_INTENSITY,
} from "./sceneLighting";

class StubScene {
    children: any[] = [];
    environment: any = undefined;
    /** A sentinel, not a plausible value. The default environment intensity is 0, so a stub that
     *  started at 0 would satisfy the assertion without the module ever assigning it. */
    environmentIntensity = -1;
    add(obj: any) {
        this.children.push(obj);
    }
}

/** Tracks every dispose() the module calls, so a leak shows up as a missing entry. */
interface Recorder {
    geometriesDisposed: number;
    materialsDisposed: number;
    generatorsDisposed: number;
    targetsDisposed: number;
}

function makeThree(recorder: Recorder, opts: { withPmrem?: boolean } = {}) {
    const withPmrem = opts.withPmrem !== false;

    class Light {
        type: string;
        /** The rig aims the key light; the tests assert type and intensity, not placement. */
        position = { set: () => undefined };
        constructor(type: string, public color: number, public intensity: number) {
            this.type = type;
        }
    }

    const THREE: any = {
        BackSide: "BackSide",
        Scene: StubScene,
        AmbientLight: class extends Light {
            constructor(color: number, intensity: number) {
                super("AmbientLight", color, intensity);
            }
        },
        DirectionalLight: class extends Light {
            constructor(color: number, intensity: number) {
                super("DirectionalLight", color, intensity);
            }
        },
        HemisphereLight: class extends Light {
            constructor(color: number, _ground: number, intensity: number) {
                super("HemisphereLight", color, intensity);
            }
        },
        BoxGeometry: class {
            dispose() {
                recorder.geometriesDisposed += 1;
            }
        },
        MeshBasicMaterial: class {
            constructor(public params: any) {}
            dispose() {
                recorder.materialsDisposed += 1;
            }
        },
        Mesh: class {
            position = { set: () => undefined };
            constructor(public geometry: any, public material: any) {}
        },
    };

    if (withPmrem) {
        THREE.PMREMGenerator = class {
            constructor(public renderer: any) {}
            fromScene(_scene: any) {
                return {
                    texture: { isPmremTexture: true },
                    dispose: () => {
                        recorder.targetsDisposed += 1;
                    },
                };
            }
            dispose() {
                recorder.generatorsDisposed += 1;
            }
        };
    }

    return THREE;
}

function newRecorder(): Recorder {
    return {
        geometriesDisposed: 0,
        materialsDisposed: 0,
        generatorsDisposed: 0,
        targetsDisposed: 0,
    };
}

describe("applySceneLighting", () => {
    it("adds an ambient light, a hemisphere fill, and a directional key light", () => {
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        const key = applySceneLighting(THREE, scene);

        const types = scene.children.map((c: any) => c.type).sort();
        expect(types).toEqual(["AmbientLight", "DirectionalLight", "HemisphereLight"]);
        expect(key.type).toBe("DirectionalLight");
        expect(key.intensity).toBe(DEFAULT_DIRECTIONAL_INTENSITY);
    });

    it("uses the exported defaults, which the Controls sliders open on", () => {
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        applySceneLighting(THREE, scene);

        const ambient = scene.children.find((c: any) => c.type === "AmbientLight");
        expect(ambient.intensity).toBe(DEFAULT_AMBIENT_INTENSITY);
    });

    it("adds exactly one DirectionalLight, so the directional slider cannot flatten the fill", () => {
        // The fill is a HemisphereLight on purpose. Controls.tsx finds its targets by `obj.type` and
        // drives every match to one value, so a second DirectionalLight would be raised to the key
        // light's intensity and cancel the shading it was added to provide.
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        applySceneLighting(THREE, scene);

        const directionals = scene.children.filter((c: any) => c.type === "DirectionalLight");
        expect(directionals).toHaveLength(1);
    });
});

describe("applySceneEnvironment", () => {
    it("assigns a generated environment to the scene", () => {
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        const env = applySceneEnvironment(THREE, scene, { isRenderer: true });

        expect(env.texture).toEqual({ isPmremTexture: true });
        expect(scene.environment).toBe(env.texture);
        expect(scene.environmentIntensity).toBe(DEFAULT_ENVIRONMENT_INTENSITY);
    });

    it("leaves scene.background alone, so the backdrop is unchanged", () => {
        const THREE = makeThree(newRecorder());
        const scene: any = new StubScene();
        scene.background = "existing-backdrop";

        applySceneEnvironment(THREE, scene, { isRenderer: true });

        expect(scene.background).toBe("existing-backdrop");
    });

    it("releases the source room once the environment has been baked", () => {
        // The room exists only to be sampled. Retaining its geometry and materials would leak a
        // handful of GPU buffers on every viewer mount, which a remount repeats.
        const recorder = newRecorder();
        const THREE = makeThree(recorder);

        applySceneEnvironment(THREE, new StubScene(), { isRenderer: true });

        // Five panels: the enclosing shell, ceiling, two side fills, and the floor.
        expect(recorder.geometriesDisposed).toBe(5);
        expect(recorder.materialsDisposed).toBe(5);
        expect(recorder.generatorsDisposed).toBe(1);
    });

    it("releases the render target and clears the scene on dispose", () => {
        const recorder = newRecorder();
        const THREE = makeThree(recorder);
        const scene = new StubScene();

        const env = applySceneEnvironment(THREE, scene, { isRenderer: true });
        env.dispose();

        expect(recorder.targetsDisposed).toBe(1);
        expect(scene.environment).toBeNull();
    });

    it("does not clear an environment that something else replaced", () => {
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        const env = applySceneEnvironment(THREE, scene, { isRenderer: true });
        scene.environment = { somebodyElses: true };
        env.dispose();

        expect(scene.environment).toEqual({ somebodyElses: true });
    });

    it("degrades to a no-op when the bundled Three.js has no PMREMGenerator", () => {
        const recorder = newRecorder();
        const THREE = makeThree(recorder, { withPmrem: false });
        const scene = new StubScene();

        const env = applySceneEnvironment(THREE, scene, { isRenderer: true });

        expect(env.texture).toBeNull();
        expect(scene.environment).toBeUndefined();
        expect(() => env.dispose()).not.toThrow();
    });

    it("degrades to a no-op when there is no renderer to bake with", () => {
        const THREE = makeThree(newRecorder());
        const scene = new StubScene();

        const env = applySceneEnvironment(THREE, scene, null);

        expect(env.texture).toBeNull();
        expect(scene.environment).toBeUndefined();
    });

    it("skips environmentIntensity on a Three.js build that predates it", () => {
        // Three.js added Scene.environmentIntensity in r163. On an older bundled build the slider is
        // inert, which is a lost control rather than a scene that fails to initialize.
        const THREE = makeThree(newRecorder());
        const scene: any = { add: () => {}, environment: undefined };

        applySceneEnvironment(THREE, scene, { isRenderer: true });

        expect(scene.environment).toEqual({ isPmremTexture: true });
        expect("environmentIntensity" in scene).toBe(false);
    });
});
