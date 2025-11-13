# Online 3D Viewer - React Component Architecture

This directory contains a complete rewrite of the Online3DViewer website components into modern TypeScript React components. The implementation uses the `online-3d-viewer` NPM package for the core 3D engine while providing a native React UI.

## Architecture Overview

### Core Components

-   **`Online3DViewerContainer.tsx`** - Main container component with React Context provider
-   **`ViewerCanvas.tsx`** - 3D viewer canvas component that interfaces with the OV.Viewer engine
-   **`LoadingOverlay.tsx`** - Loading state overlay component

### Layout Components

-   **`Header.tsx`** - Top header with title and file name display
-   **`Toolbar.tsx`** - Main toolbar with all viewer controls
-   **`LeftPanel.tsx`** - Navigator panel with Files, Materials, and Meshes tabs
-   **`RightPanel.tsx`** - Sidebar panel with Details and Settings tabs

### State Management

-   **`ViewerContext.tsx`** - React Context for global state management
-   **`viewer.types.ts`** - TypeScript interfaces and type definitions

### Styling

-   **`Online3DViewerContainer.css`** - Complete CSS with theming support

## Key Features

### Modern React Patterns

-   Functional components with hooks
-   React Context for state management
-   TypeScript for type safety
-   CSS custom properties for theming

### 3D Viewer Integration

-   Uses `online-3d-viewer` NPM package
-   Direct integration with OV.Viewer engine
-   Support for multiple file formats
-   Model loading from URLs and files

### UI Features

-   **Responsive Design** - Works on desktop and mobile
-   **Dark/Light Themes** - Automatic theme switching
-   **Panel Management** - Collapsible left and right panels
-   **Toolbar Controls** - All essential 3D viewer controls
-   **Loading States** - Progressive loading indicators
-   **Error Handling** - Comprehensive error states

### Supported Operations

-   File loading (single and multiple files)
-   Model navigation and camera controls
-   Material and mesh inspection
-   Settings management
-   Theme switching
-   Model export (framework ready)
-   Measurement tools (framework ready)

## Usage

The component is a drop-in replacement for the original Online3dViewerComponent:

```tsx
import Online3dViewerComponent from "./Online3dViewerComponent";

<Online3dViewerComponent
    assetId="asset-id"
    databaseId="database-id"
    assetKey="model.obj"
    multiFileKeys={["model.obj", "texture.jpg"]}
    versionId="version-id"
/>;
```

## Component Structure

```
Online3dViewerPlugin/
├── Online3dViewerComponent.tsx          # Main export component
├── README.md                            # This documentation
├── types/
│   └── viewer.types.ts                  # TypeScript definitions
├── context/
│   └── ViewerContext.tsx                # React Context provider
└── components/
    ├── core/
    │   ├── Online3DViewerContainer.tsx  # Main container
    │   ├── Online3DViewerContainer.css  # Styling
    │   ├── ViewerCanvas.tsx             # 3D viewer canvas
    │   └── LoadingOverlay.tsx           # Loading states
    └── layout/
        ├── Header.tsx                   # Top header
        ├── Toolbar.tsx                  # Main toolbar
        ├── LeftPanel.tsx                # Navigator panel
        └── RightPanel.tsx               # Settings panel
```

## State Management

The application uses React Context for global state management:

-   **ViewerState** - Viewer instance, model, loading states
-   **ViewerSettings** - Theme, colors, display settings
-   **CameraSettings** - Navigation and projection modes
-   **Selection** - Currently selected mesh or material

## Theming

The component supports light and dark themes using CSS custom properties:

```css
:root {
    --ov-background-color: #f5f5f5;
    --ov-foreground-color: #333333;
    /* ... other theme variables */
}

[data-theme="dark"] {
    --ov-background-color: #2a2a2a;
    --ov-foreground-color: #ffffff;
    /* ... dark theme overrides */
}
```

## Future Enhancements

The architecture is designed to easily support additional features:

-   **Dialog Components** - Export, Share, Snapshot dialogs
-   **Measurement Tools** - Distance and angle measurement
-   **Plugin System** - Extensible plugin architecture
-   **Advanced Settings** - More viewer configuration options
-   **Accessibility** - Enhanced a11y support

## Dependencies

-   `online-3d-viewer` - Core 3D engine
-   `react` - UI framework
-   `typescript` - Type safety

## Browser Support

-   Modern browsers with WebGL support
-   Mobile browsers (responsive design)
-   Progressive enhancement for older browsers

## Performance

-   Lazy loading of 3D engine
-   Efficient React rendering with memo and callbacks
-   CSS-based animations and transitions
-   Optimized for large 3D models
