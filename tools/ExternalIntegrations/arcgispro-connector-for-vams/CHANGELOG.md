# Changelog

All notable changes to VamsConnector will be documented in this file.

## [1.0.0] - 2026-01-21

### Added

-   **Database Browser**: Hierarchical tree view for VAMS databases, assets, and files
    -   Folder hierarchy support for files with folder paths
    -   Dynamic icons that change when expanded/collapsed
    -   Resizable panes with GridSplitter between explorer and details
-   **Authentication**: Secure CLI-based login with VAMS
-   **File References**: Add VAMS file references to feature classes and tables
-   **Field Management**: Automatic creation of VAMS reference fields with metadata
-   **Image Preview**: Enhanced image viewer with pan and zoom capabilities
    -   Universal panning at any zoom level with smooth drag interaction
    -   Ctrl + Mouse Wheel zoom centered on cursor
    -   Automatic fit-to-window on initial load with proper centering
    -   Fit to window button resets view to centered, scaled image
    -   Download support with progress tracking
-   **Context Menu Integration**:
    -   Right-click on VAMS files to add references
    -   "Open VAMS Link" command for opening file references from attribute tables
-   **Smart Detection**: Automatic detection of tables with VAMS reference fields
-   **Batch Operations**: Open multiple file links from selected table rows
-   **Metadata Display**: Rich information display for databases, assets, and files
-   **Error Handling**: Comprehensive error handling with user-friendly messages
-   **Resource Management**: Proper disposal and memory management

### Technical Details

-   Built on .NET 8.0 targeting Windows
-   WPF-based UI with MVVM pattern
-   ArcGIS Pro SDK 3.5+ integration
-   Support for File Geodatabase and Enterprise Geodatabase
-   Uses VAMSCLI for data integration

### Supported File Types

-   **Images**: JPG, JPEG, PNG, GIF, BMP, TIFF, TIF (full preview)
-   **Documents**: PDF (info dialog)
-   **Text**: TXT, CSV (info dialog)
-   **Archives**: ZIP, RAR, 7Z (info dialog)
-   **Other**: All file types (generic info dialog)

### Known Limitations

-   Requires active map view for adding references
-   Image preview only (no PDF preview in viewer)
-   Windows only (ArcGIS Pro limitation)
