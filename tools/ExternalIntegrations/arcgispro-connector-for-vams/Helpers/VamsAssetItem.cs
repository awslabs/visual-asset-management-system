/*
Copyright 2025 Esri

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied. See the License for the specific language governing
permissions and limitations under the License.
*/

using ArcGIS.Desktop.Framework.Dialogs;
using VamsDatabaseExplorer.Models;
using VamsDatabaseExplorer.Services;
using System;
using System.Collections.Generic;
using System.Threading.Tasks;

namespace VamsConnector.Helpers
{
    public class VamsAssetItem : VamsItemBase
    {
        private readonly VamsCliService _vamsService;
        private readonly Asset _asset;

        public VamsAssetItem(VamsCliService vamsService, Asset asset)
        {
            _vamsService = vamsService;
            _asset = asset;
            Name = asset.AssetName;
            AssetId = asset.AssetId;
            DatabaseId = asset.DatabaseId;

            System.Diagnostics.Debug.WriteLine($"VamsAssetItem: Created asset item - Name: '{Name}', AssetId: '{AssetId}', FileCount: {asset.FileCount}");
        }

        public string AssetId { get; private set; }
        public string DatabaseId { get; private set; }

        public VamsDatabaseExplorer.Models.Asset GetAsset()
        {
            return _asset;
        }

        public override async void LoadChildren()
        {
            try
            {
                if (_asset.AssetType != "none")
                {
                    var files = await _vamsService.GetFilesForAssetAsync(_asset.AssetId, _asset.DatabaseId);
                    BuildFolderHierarchy(files);
                    UpdateAssetStatistics(files);
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Error loading files for asset {_asset.AssetName}: {ex.Message}");
            }
        }

        private void UpdateAssetStatistics(List<VamsDatabaseExplorer.Models.AssetFile> files)
        {
            // Calculate total file count and size (excluding folder objects)
            int fileCount = 0;
            long totalSize = 0;

            foreach (var file in files)
            {
                // Only count actual files, not folder objects
                if (!file.IsFolder)
                {
                    fileCount++;
                    
                    // Add size if it has a value
                    if (file.Size.HasValue)
                    {
                        totalSize += file.Size.Value;
                    }
                }
            }

            // Update the asset's file count and total size
            // Note: These are compatibility properties that return 0 by default
            // We need to use reflection or add mutable properties to update them
            System.Diagnostics.Debug.WriteLine($"VamsAssetItem: Asset '{_asset.AssetName}' has {fileCount} files with total size {totalSize} bytes");
            
            // Since Asset properties are read-only, we'll need to store these values
            // in the VamsAssetItem itself for display purposes
            _calculatedFileCount = fileCount;
            _calculatedTotalSize = totalSize;
        }

        private int _calculatedFileCount = 0;
        private long _calculatedTotalSize = 0;

        public int CalculatedFileCount => _calculatedFileCount;
        public long CalculatedTotalSize => _calculatedTotalSize;

        private void BuildFolderHierarchy(List<VamsDatabaseExplorer.Models.AssetFile> files)
        {
            // Dictionary to track folder items by their path
            var folderMap = new Dictionary<string, VamsFolderItem>();

            foreach (var file in files)
            {
                // Skip folder objects - they're just metadata, not actual items to display
                if (file.IsFolder)
                {
                    continue;
                }

                // Get the relative path and split it
                var relativePath = file.Path.TrimStart('/'); // Remove leading slash
                
                if (string.IsNullOrEmpty(relativePath))
                {
                    // Skip empty paths
                    continue;
                }

                var pathParts = relativePath.Split('/');
                
                if (pathParts.Length == 1)
                {
                    // File at root level - add directly to asset
                    Children.Add(new VamsFileItem(file, _asset.DatabaseId, _asset.AssetId, _asset.DatabaseName, _asset.AssetName));
                }
                else
                {
                    // File is in a folder structure
                    VamsItemBase currentParent = this;
                    string currentPath = "";

                    // Process each folder in the path (excluding the filename)
                    for (int i = 0; i < pathParts.Length - 1; i++)
                    {
                        var folderName = pathParts[i];
                        
                        // Skip empty folder names
                        if (string.IsNullOrEmpty(folderName))
                        {
                            continue;
                        }

                        currentPath = string.IsNullOrEmpty(currentPath) 
                            ? folderName 
                            : $"{currentPath}/{folderName}";

                        if (!folderMap.ContainsKey(currentPath))
                        {
                            // Create new folder item
                            var folderItem = new VamsFolderItem(folderName);
                            folderMap[currentPath] = folderItem;
                            currentParent.Children.Add(folderItem);
                            currentParent = folderItem;
                        }
                        else
                        {
                            currentParent = folderMap[currentPath];
                        }
                    }

                    // Add the file to its parent folder
                    currentParent.Children.Add(new VamsFileItem(file, _asset.DatabaseId, _asset.AssetId, _asset.DatabaseName, _asset.AssetName));
                }
            }
        }
    }
}
