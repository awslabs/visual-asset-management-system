/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Lighting rig and image-based environment for the Three.js viewer scene.
 *
 * A physically-based material takes its ambient diffuse AND its specular reflection from
 * `scene.environment`, not from an `AmbientLight`. With no environment the specular term is zero, and
 * because the glTF specification defaults `metallicFactor` to 1, a mesh that declares no
 * metallic-roughness values is a mirror with nothing to reflect. That is what makes a model look dark
 * and dull no matter how far the light sliders are raised: a metal surface has almost no diffuse
 * albedo for a light to illuminate, so the only response is a small highlight.
 *
 * So the rig is three things rather than two lights:
 *
 *   - an environment, generated here rather than loaded, so it costs no network request and works in
 *     an air-gapped deployment. It ships at zero intensity and is raised per model: for a textured
 *     model the two lights are enough and the environment only washes it out, but for a mesh whose
 *     material the lights cannot reach it is the only thing that renders it at all;
 *   - a key light and a hemisphere fill, which shape the geometry that the environment lights flatly;
 *   - `scene.environmentIntensity`, which is the control that actually brightens a PBR material.
 *
 * The hemisphere light is deliberately NOT a second `DirectionalLight`: the viewer's light sliders
 * find their targets by `obj.type`, so a second directional would be driven to the key light's
 * intensity and flatten the shading it exists to provide.
 */

/** Light intensities in the renderer's physical units. The sliders in `components/Controls.tsx`
 *  start here, and its reset restores these values.
 *
 *  The environment starts at ZERO. It is generated and assigned regardless, so raising the slider
 *  takes effect immediately, but it is off by default because it washes out most models: the two
 *  lights are enough for a textured model, while the environment adds a flat lift on top of them.
 *  The models that need it are the ones the lights cannot reach — a metallic surface with no
 *  metallic-roughness texture, which has no diffuse albedo for a light to illuminate at all. That is
 *  a per-model judgement, so it is a control rather than a default. */
export const DEFAULT_AMBIENT_INTENSITY = 1.0;
export const DEFAULT_DIRECTIONAL_INTENSITY = 2.0;
export const DEFAULT_ENVIRONMENT_INTENSITY = 0;
/** Fixed: the fill is part of the rig rather than something the operator tunes. */
const HEMISPHERE_INTENSITY = 0.6;

export interface SceneEnvironment {
    /** The generated PMREM texture, or null when this Three.js build cannot produce one. */
    texture: any | null;
    /** Releases the texture and the generator. Safe to call when `texture` is null. */
    dispose: () => void;
}

/**
 * Build a neutral studio environment and assign it to `scene.environment`.
 *
 * The source is a box turned inside out, with a bright ceiling panel and two dimmer side panels — a
 * softbox, in effect. `MeshBasicMaterial` is used because the source scene is only ever read as
 * radiance by the PMREM generator; it is never lit or rendered itself.
 *
 * `scene.background` is left alone. The environment is what the materials sample; making it the
 * backdrop as well would replace the viewer's flat backdrop with a visible grey room.
 */
export function applySceneEnvironment(THREE: any, scene: any, renderer: any): SceneEnvironment {
    // A Three.js build without PMREMGenerator still renders; it just gets no image-based lighting.
    // Returning a no-op keeps the caller's cleanup unconditional.
    if (!THREE?.PMREMGenerator || !renderer) {
        return { texture: null, dispose: () => undefined };
    }

    const room = new THREE.Scene();
    const panels: any[] = [];

    const addPanel = (
        color: number,
        width: number,
        height: number,
        depth: number,
        x: number,
        y: number,
        z: number
    ) => {
        const panel = new THREE.Mesh(
            new THREE.BoxGeometry(width, height, depth),
            new THREE.MeshBasicMaterial({ color })
        );
        panel.position.set(x, y, z);
        room.add(panel);
        panels.push(panel);
    };

    // The enclosure. Scale is arbitrary — only the relative proportions and the radiance matter.
    const shell = new THREE.Mesh(
        new THREE.BoxGeometry(10, 10, 10),
        new THREE.MeshBasicMaterial({ color: 0x808080, side: THREE.BackSide })
    );
    room.add(shell);
    panels.push(shell);

    addPanel(0xffffff, 8, 0.1, 8, 0, 4.5, 0); // ceiling — the dominant source
    addPanel(0xbbbbbb, 0.1, 6, 6, -4.5, 0, 0); // left fill
    addPanel(0xbbbbbb, 0.1, 6, 6, 4.5, 0, 0); // right fill
    addPanel(0x4a4a4a, 8, 0.1, 8, 0, -4.5, 0); // floor — darker, so shading reads bottom-lit-less

    const generator = new THREE.PMREMGenerator(renderer);
    const target = generator.fromScene(room);
    const texture = target?.texture ?? null;

    scene.environment = texture;
    // Added in Three.js r163. Guarded rather than assumed so an older bundled build still works —
    // it simply loses the Environment slider's effect rather than throwing during scene setup.
    if ("environmentIntensity" in scene) {
        scene.environmentIntensity = DEFAULT_ENVIRONMENT_INTENSITY;
    }

    // The source scene is not retained: the PMREM texture is the only thing sampled from here on.
    panels.forEach((mesh) => {
        mesh.geometry?.dispose?.();
        mesh.material?.dispose?.();
    });
    generator.dispose?.();

    return {
        texture,
        dispose: () => {
            if (scene.environment === texture) {
                scene.environment = null;
            }
            target?.dispose?.();
        },
    };
}

/**
 * Add the light rig to a scene. Returns the key light so the caller can aim or attach it.
 */
export function applySceneLighting(THREE: any, scene: any): any {
    scene.add(new THREE.AmbientLight(0xffffff, DEFAULT_AMBIENT_INTENSITY));

    // Sky/ground fill. Keeps a face turned away from the key light readable instead of black, which
    // an AmbientLight cannot do on its own because it lights every face identically.
    scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, HEMISPHERE_INTENSITY));

    const keyLight = new THREE.DirectionalLight(0xffffff, DEFAULT_DIRECTIONAL_INTENSITY);
    keyLight.position.set(5, 10, 7.5);
    scene.add(keyLight);

    return keyLight;
}
