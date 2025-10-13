/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { PlyType } from '../types/viewer.types';

/**
 * Detect if a PLY file is a Gaussian Splat or Point Cloud by reading the header
 * @param url - URL to the PLY file
 * @returns Promise that resolves to 'splat' for Gaussian Splat, 'pc' for Point Cloud
 */
export async function detectPlyType(url: string): Promise<PlyType> {
    try {
        console.log('Detecting PLY type for:', url);
        
        // Read only the first 8KB of the file to check the header
        const response = await fetch(url, {
            headers: {
                'Range': 'bytes=0-8191'
            }
        });
        
        if (!response.ok) {
            console.warn('Failed to fetch PLY header, defaulting to point cloud');
            return 'pc';
        }
        
        const headerText = await response.text();
        
        // Check for Gaussian Splat specific properties
        const splatProperties = [
            'property float f_dc_0',
            'property float opacity',
            'property float scale_0',
            'property float rot_0',
            'property float f_rest_0',
            'property float scale_1',
            'property float scale_2'
        ];
        
        const hasSplatProps = splatProperties.some(prop => 
            headerText.includes(prop)
        );
        
        const detectedType: PlyType = hasSplatProps ? 'splat' : 'pc';
        console.log(`PLY type detected: ${detectedType}`);
        
        return detectedType;
        
    } catch (error) {
        console.warn('Error detecting PLY type:', error);
        return 'pc'; // Default to point cloud on error
    }
}

/**
 * Validate PLY header structure
 * @param headerText - PLY header text
 * @returns boolean indicating if header is valid
 */
export function validatePlyHeader(headerText: string): boolean {
    const requiredElements = ['ply', 'format', 'element vertex', 'end_header'];
    return requiredElements.every(element => 
        headerText.toLowerCase().includes(element.toLowerCase())
    );
}

/**
 * Extract vertex count from PLY header
 * @param headerText - PLY header text
 * @returns number of vertices or 0 if not found
 */
export function extractVertexCount(headerText: string): number {
    const vertexMatch = headerText.match(/element vertex (\d+)/i);
    return vertexMatch ? parseInt(vertexMatch[1], 10) : 0;
}
