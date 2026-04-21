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
using VamsConnector.Helpers;
using VamsConnector.Services;
using System;
using System.Collections.Generic;
using System.Linq;

namespace VamsConnector.Handlers
{
    public static class VamsUrlHandler
    {
        /// <summary>
        /// Handles VAMS web URLs in the format: <base_url>#/databases/<dbId>/assets/<assetId>/file/<fileKey>
        /// Example: http://localhost:3000/#/databases/test/assets/x3930a310-8262-4934-b471-c5aa35afb6ab/file/Avocado.bin
        /// </summary>
        public static void HandleVamsUrl(string url)
        {
            try
            {
                // Check if it's the new web format
                if (url.Contains("#/databases/"))
                {
                    var parsedUrl = ParseWebVamsUrl(url);
                    if (parsedUrl == null)
                    {
                        MessageBox.Show("Could not parse VAMS URL.", "Error",
                            System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                        return;
                    }

                    // Handle based on file type (determined by extension)
                    var fileExtension = System.IO.Path.GetExtension(parsedUrl.FileKey).ToLowerInvariant();
                    var imageExtensions = new[] { ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp" };
                    
                    if (imageExtensions.Contains(fileExtension))
                    {
                        OpenImagePreview(parsedUrl);
                    }
                    else
                    {
                        ShowFileInfo(parsedUrl);
                    }
                    return;
                }

                // If not a web URL, show error
                MessageBox.Show("Invalid VAMS URL format. Expected web URL with #/databases/ path.", "Error", 
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Error handling VAMS URL: {ex.Message}", "Error",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }

        private static VamsUrlInfo ParseWebVamsUrl(string url)
        {
            try
            {
                // Example: http://localhost:3000/#/databases/test/assets/x3930a310-8262-4934-b471-c5aa35afb6ab/file/Avocado.bin
                
                // Find the hash fragment
                var hashIndex = url.IndexOf("#/databases/");
                if (hashIndex == -1) return null;

                // Extract the path after the hash
                var path = url.Substring(hashIndex + 1); // Remove the #
                
                // Split by / and parse: /databases/<dbId>/assets/<assetId>/file/<fileKey>
                var segments = path.Split(new[] { '/' }, StringSplitOptions.RemoveEmptyEntries);
                
                // Expected: ["databases", "<dbId>", "assets", "<assetId>", "file", "<fileKey>", ...]
                if (segments.Length < 6)
                {
                    System.Diagnostics.Debug.WriteLine($"VamsUrlHandler: Invalid segment count: {segments.Length}");
                    return null;
                }

                if (segments[0] != "databases" || segments[2] != "assets" || segments[4] != "file")
                {
                    System.Diagnostics.Debug.WriteLine($"VamsUrlHandler: Invalid path structure");
                    return null;
                }

                var databaseId = Uri.UnescapeDataString(segments[1]);
                var assetId = Uri.UnescapeDataString(segments[3]);
                
                // File key may contain slashes, so join remaining segments
                var fileKeySegments = segments.Skip(5).ToArray();
                var fileKey = "/" + string.Join("/", fileKeySegments.Select(Uri.UnescapeDataString));

                System.Diagnostics.Debug.WriteLine($"VamsUrlHandler: Parsed - DB: {databaseId}, Asset: {assetId}, File: {fileKey}");

                return new VamsUrlInfo
                {
                    DatabaseId = databaseId,
                    AssetId = assetId,
                    FileKey = fileKey
                };
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsUrlHandler: Parse error: {ex.Message}");
                return null;
            }
        }

        private static async void OpenImagePreview(VamsUrlInfo urlInfo)
        {
            try
            {
                // Create a mock AssetFile from the URL info
                var assetFile = new VamsDatabaseExplorer.Models.AssetFile
                {
                    RelativePath = urlInfo.FileKey,
                    FileName = System.IO.Path.GetFileName(urlInfo.FileKey),
                    PrimaryType = "image"
                };

                // Create a mock VamsFileItem
                var fileItem = new VamsFileItem(
                    assetFile, 
                    urlInfo.DatabaseId, 
                    urlInfo.AssetId, 
                    urlInfo.DatabaseId, 
                    urlInfo.AssetId);
                
                // Create and show the image preview window using existing components
                var imagePreviewWindow = new ImagePreviewWindow(assetFile.FileName);
                var viewModel = new ImagePreviewViewModel(fileItem, assetFile);
                
                imagePreviewWindow.DataContext = viewModel;
                imagePreviewWindow.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Error opening image preview: {ex.Message}", "Error",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }

        private static void ShowFileInfo(VamsUrlInfo urlInfo)
        {
            var message = $"VAMS File Information:\n\n" +
                         $"Database: {urlInfo.DatabaseId}\n" +
                         $"Asset: {urlInfo.AssetId}\n" +
                         $"File: {urlInfo.FileKey}\n\n" +
                         $"This file type does not support preview.\n" +
                         $"Use the VAMS web interface to view or download this file.";

            MessageBox.Show(message, "VAMS File Info", 
                System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
        }

        private class VamsUrlInfo
        {
            public string DatabaseId { get; set; }
            public string AssetId { get; set; }
            public string FileKey { get; set; }
        }
    }
}
