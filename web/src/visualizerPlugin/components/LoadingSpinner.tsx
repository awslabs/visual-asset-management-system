/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from 'react';

interface LoadingSpinnerProps {
    message?: string;
    progress?: number;
}

const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ 
    message = "Loading...", 
    progress 
}) => {
    // Create unique class name to avoid conflicts
    const spinnerClass = `loading-spinner-${Date.now()}`;
    
    React.useEffect(() => {
        // Safely inject CSS keyframes using CSSOM
        const style = document.createElement('style');
        style.textContent = `
            @keyframes ${spinnerClass}-spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            .${spinnerClass} {
                animation: ${spinnerClass}-spin 1s linear infinite;
            }
        `;
        document.head.appendChild(style);
        
        // Cleanup function to remove the style when component unmounts
        return () => {
            if (document.head.contains(style)) {
                document.head.removeChild(style);
            }
        };
    }, [spinnerClass]);

    return (
        <div style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            backgroundColor: 'rgba(0, 0, 0, 0.8)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            color: 'white',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
        }}>
            {/* Spinning loader */}
            <div 
                className={spinnerClass}
                style={{
                    width: '60px',
                    height: '60px',
                    border: '4px solid rgba(255, 255, 255, 0.1)',
                    borderTop: '4px solid #007ACC',
                    borderRadius: '50%',
                    marginBottom: '20px'
                }} 
            />
            
            {/* Loading message */}
            <div style={{
                fontSize: '16px',
                fontWeight: '500',
                marginBottom: progress !== undefined ? '16px' : '0',
                textAlign: 'center',
                maxWidth: '300px'
            }}>
                {message}
            </div>
            
            {/* Progress bar (if progress is provided) */}
            {progress !== undefined && (
                <div style={{
                    width: '200px',
                    height: '4px',
                    backgroundColor: 'rgba(255, 255, 255, 0.2)',
                    borderRadius: '2px',
                    overflow: 'hidden'
                }}>
                    <div style={{
                        width: `${Math.max(0, Math.min(100, progress))}%`,
                        height: '100%',
                        backgroundColor: '#007ACC',
                        borderRadius: '2px',
                        transition: 'width 0.3s ease'
                    }} />
                </div>
            )}
        </div>
    );
};

export default LoadingSpinner;
