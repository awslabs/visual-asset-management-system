/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Simple orbit controls for Three.js camera
 * Provides mouse and touch controls for rotating, zooming, and panning
 */
export class MouseControls {
    private camera: any;
    private domElement: HTMLElement;
    private target: any; // THREE.Vector3
    private enabled: boolean = true;

    // State
    private isRotating: boolean = false;
    private isPanning: boolean = false;
    private rotateStart: { x: number; y: number } = { x: 0, y: 0 };
    private panStart: { x: number; y: number } = { x: 0, y: 0 };

    // Settings
    private rotateSpeed: number = 1.0;
    private zoomSpeed: number = 1.0;
    private panSpeed: number = 1.0;
    private minDistance: number = 0.01;
    private maxDistance: number = 10000;

    constructor(camera: any, domElement: HTMLElement, THREE: any) {
        this.camera = camera;
        this.domElement = domElement;
        this.target = new THREE.Vector3();

        this.setupEventListeners();
    }

    private setupEventListeners(): void {
        // Mouse events
        this.domElement.addEventListener("mousedown", this.onMouseDown.bind(this));
        this.domElement.addEventListener("mousemove", this.onMouseMove.bind(this));
        this.domElement.addEventListener("mouseup", this.onMouseUp.bind(this));
        this.domElement.addEventListener("wheel", this.onMouseWheel.bind(this), {
            passive: false,
        });

        // Touch events
        this.domElement.addEventListener("touchstart", this.onTouchStart.bind(this), {
            passive: false,
        });
        this.domElement.addEventListener("touchmove", this.onTouchMove.bind(this), {
            passive: false,
        });
        this.domElement.addEventListener("touchend", this.onTouchEnd.bind(this));

        // Context menu
        this.domElement.addEventListener("contextmenu", (e) => e.preventDefault());
    }

    public hasMoved: boolean = false;

    private onMouseDown(event: MouseEvent): void {
        if (!this.enabled) return;

        this.hasMoved = false;

        if (event.button === 0) {
            // Left click - rotate
            this.isRotating = true;
            this.rotateStart = { x: event.clientX, y: event.clientY };
        } else if (event.button === 2) {
            // Right click - pan
            this.isPanning = true;
            this.panStart = { x: event.clientX, y: event.clientY };
        }
    }

    private onMouseMove(event: MouseEvent): void {
        if (!this.enabled) return;

        if (this.isRotating) {
            const deltaX = event.clientX - this.rotateStart.x;
            const deltaY = event.clientY - this.rotateStart.y;

            // Mark as moved if there's significant movement
            if (Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2) {
                this.hasMoved = true;
            }

            this.rotateCamera(deltaX, deltaY);

            this.rotateStart = { x: event.clientX, y: event.clientY };
        } else if (this.isPanning) {
            const deltaX = event.clientX - this.panStart.x;
            const deltaY = event.clientY - this.panStart.y;

            this.hasMoved = true;

            this.panCamera(deltaX, deltaY);

            this.panStart = { x: event.clientX, y: event.clientY };
        }
    }

    private onMouseUp(event: MouseEvent): void {
        // If we didn't move, it's a click - don't consume the event
        if (!this.hasMoved && event.button === 0) {
            // Let the click event propagate for selection
            console.log("MouseControls: Click detected (no drag)");
        }

        this.isRotating = false;
        this.isPanning = false;
        this.hasMoved = false;
    }

    private onMouseWheel(event: WheelEvent): void {
        if (!this.enabled) return;
        event.preventDefault();

        const delta = event.deltaY;
        this.zoomCamera(delta > 0 ? 1.1 : 0.9);
    }

    private onTouchStart(event: TouchEvent): void {
        if (!this.enabled) return;
        event.preventDefault();

        if (event.touches.length === 1) {
            this.isRotating = true;
            this.rotateStart = { x: event.touches[0].clientX, y: event.touches[0].clientY };
        }
    }

    private onTouchMove(event: TouchEvent): void {
        if (!this.enabled) return;
        event.preventDefault();

        if (event.touches.length === 1 && this.isRotating) {
            const deltaX = event.touches[0].clientX - this.rotateStart.x;
            const deltaY = event.touches[0].clientY - this.rotateStart.y;

            this.rotateCamera(deltaX, deltaY);

            this.rotateStart = { x: event.touches[0].clientX, y: event.touches[0].clientY };
        }
    }

    private onTouchEnd(): void {
        this.isRotating = false;
        this.isPanning = false;
    }

    private rotateCamera(deltaX: number, deltaY: number): void {
        const offset = this.camera.position.clone().sub(this.target);
        const spherical = {
            radius: offset.length(),
            theta: Math.atan2(offset.x, offset.z),
            phi: Math.acos(Math.max(-1, Math.min(1, offset.y / offset.length()))),
        };

        spherical.theta -= (deltaX * this.rotateSpeed * Math.PI) / this.domElement.clientHeight;
        spherical.phi -= (deltaY * this.rotateSpeed * Math.PI) / this.domElement.clientHeight;

        // Clamp phi to prevent flipping
        spherical.phi = Math.max(0.01, Math.min(Math.PI - 0.01, spherical.phi));

        offset.x = spherical.radius * Math.sin(spherical.phi) * Math.sin(spherical.theta);
        offset.y = spherical.radius * Math.cos(spherical.phi);
        offset.z = spherical.radius * Math.sin(spherical.phi) * Math.cos(spherical.theta);

        this.camera.position.copy(this.target).add(offset);
        this.camera.lookAt(this.target);
    }

    private panCamera(deltaX: number, deltaY: number): void {
        const THREE = (window as any).THREE;
        if (!THREE) return;

        const offset = this.camera.position.clone().sub(this.target);
        const targetDistance = offset.length();

        // Calculate pan distance based on camera FOV
        const fov = (this.camera.fov * Math.PI) / 180;
        const panDistance = 2 * Math.tan(fov / 2) * targetDistance;

        const panX = (deltaX * panDistance * this.panSpeed) / this.domElement.clientHeight;
        const panY = (deltaY * panDistance * this.panSpeed) / this.domElement.clientHeight;

        const right = new THREE.Vector3();
        right.setFromMatrixColumn(this.camera.matrix, 0);

        const up = new THREE.Vector3();
        up.setFromMatrixColumn(this.camera.matrix, 1);

        const pan = right.multiplyScalar(-panX).add(up.multiplyScalar(panY));

        this.camera.position.add(pan);
        this.target.add(pan);
    }

    private zoomCamera(scale: number): void {
        const offset = this.camera.position.clone().sub(this.target);
        const distance = offset.length() * scale;

        // Clamp distance
        const clampedDistance = Math.max(this.minDistance, Math.min(this.maxDistance, distance));

        offset.normalize().multiplyScalar(clampedDistance);
        this.camera.position.copy(this.target).add(offset);
    }

    // Public methods
    public setTarget(x: number, y: number, z: number): void {
        this.target.set(x, y, z);
    }

    public getTarget(): any {
        return this.target.clone();
    }

    public setEnabled(enabled: boolean): void {
        this.enabled = enabled;
    }

    public dispose(): void {
        // Remove event listeners
        this.domElement.removeEventListener("mousedown", this.onMouseDown.bind(this));
        this.domElement.removeEventListener("mousemove", this.onMouseMove.bind(this));
        this.domElement.removeEventListener("mouseup", this.onMouseUp.bind(this));
        this.domElement.removeEventListener("wheel", this.onMouseWheel.bind(this));
        this.domElement.removeEventListener("touchstart", this.onTouchStart.bind(this));
        this.domElement.removeEventListener("touchmove", this.onTouchMove.bind(this));
        this.domElement.removeEventListener("touchend", this.onTouchEnd.bind(this));
    }
}
