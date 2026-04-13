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

using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using Microsoft.Win32;
using VamsConnector.Helpers;
using VamsDatabaseExplorer.Models;
using VamsDatabaseExplorer.Services;

namespace VamsConnector
{
    public class ImagePreviewViewModel : PropertyChangedBase, IDisposable
    {
        private readonly VamsCliService _vamsService;
        private readonly VamsFileItem _fileItem;
        private readonly AssetFile _file;
        private string _tempFilePath;
        private bool _disposed = false;

        public ImagePreviewViewModel(VamsFileItem fileItem, AssetFile file)
        {
            _vamsService = new VamsCliService();
            _fileItem = fileItem;
            _file = file;
            
            FilePath = file.Path;
            
            // Start loading the image
            _ = Task.Run(LoadImageAsync);
        }

        #region Properties

        private string _filePath;
        public string FilePath
        {
            get { return _filePath; }
            set { SetProperty(ref _filePath, value, () => FilePath); }
        }

        private bool _isLoading = true;
        public bool IsLoading
        {
            get { return _isLoading; }
            set { SetProperty(ref _isLoading, value, () => IsLoading); }
        }

        private bool _isDownloading = false;
        public bool IsDownloading
        {
            get { return _isDownloading; }
            set 
            { 
                SetProperty(ref _isDownloading, value, () => IsDownloading);
            }
        }

        private int _downloadProgress = 0;
        public int DownloadProgress
        {
            get { return _downloadProgress; }
            set { SetProperty(ref _downloadProgress, value, () => DownloadProgress); }
        }

        private BitmapImage _previewImage;
        public BitmapImage PreviewImage
        {
            get { return _previewImage; }
            set { SetProperty(ref _previewImage, value, () => PreviewImage); }
        }

        private string _errorMessage;
        public string ErrorMessage
        {
            get { return _errorMessage; }
            set 
            { 
                SetProperty(ref _errorMessage, value, () => ErrorMessage);
                // Update visibility based on error message
                ErrorMessageVisible = !string.IsNullOrEmpty(value);
            }
        }

        private bool _errorMessageVisible = false;
        public bool ErrorMessageVisible
        {
            get { return _errorMessageVisible; }
            set { SetProperty(ref _errorMessageVisible, value, () => ErrorMessageVisible); }
        }

        #endregion

        #region Events

        public event Action OnImageLoaded;

        #endregion

        #region Commands

        public ICommand DownloadCommand
        {
            get
            {
                return new RelayCommand(async () => await DownloadFileAsync(), true);
            }
        }

        #endregion

        #region Methods

        private async Task LoadImageAsync()
        {
            try
            {
                IsLoading = true;
                ErrorMessage = null;

                // Determine which file to download (preview or original)
                string fileToDownload = null;
                bool hasPreview = false;
                
                // Check if preview file is available from the file list
                if (!string.IsNullOrEmpty(_file.PreviewFile))
                {
                    System.Diagnostics.Debug.WriteLine($"ImagePreview: Using preview file from list: {_file.PreviewFile}");
                    fileToDownload = _file.PreviewFile;
                    hasPreview = true;
                }
                else
                {
                    // Preview not in list, fetch file info to check for preview
                    System.Diagnostics.Debug.WriteLine($"ImagePreview: Fetching file info to check for preview");
                    try
                    {
                        var fileInfo = await _vamsService.GetFileInfoAsync(
                            _fileItem.DatabaseId, _fileItem.AssetId, _file.RelativePath);
                        
                        if (!string.IsNullOrEmpty(fileInfo.PreviewFile))
                        {
                            System.Diagnostics.Debug.WriteLine($"ImagePreview: Found preview file in info: {fileInfo.PreviewFile}");
                            fileToDownload = fileInfo.PreviewFile;
                            hasPreview = true;
                        }
                        else
                        {
                            System.Diagnostics.Debug.WriteLine($"ImagePreview: No preview file available");
                        }
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"ImagePreview: Error fetching file info: {ex.Message}");
                    }
                }

                // If no preview available, check if original file is an image format
                if (!hasPreview)
                {
                    if (IsImageFile(_file.RelativePath))
                    {
                        System.Diagnostics.Debug.WriteLine($"ImagePreview: No preview, but original file is an image format");
                        fileToDownload = _file.RelativePath;
                    }
                    else
                    {
                        // Not an image format and no preview available
                        throw new InvalidOperationException(
                            $"No preview file is available for this file.\n\n" +
                            $"File: {Path.GetFileName(_file.RelativePath)}\n" +
                            $"Type: {_file.Type}\n\n" +
                            $"VAMS does not currently have a preview image for this file.");
                    }
                }

                // Create a temporary file path for the image
                var extension = Path.GetExtension(fileToDownload);
                if (string.IsNullOrEmpty(extension))
                {
                    extension = Path.GetExtension(_file.RelativePath);
                }
                _tempFilePath = Path.Combine(Path.GetTempPath(), $"vams_preview_{Guid.NewGuid()}{extension}");

                // Download file to temp location using CLI
                await _vamsService.DownloadFileAsync(
                    _tempFilePath, _fileItem.DatabaseId, _fileItem.AssetId, fileToDownload);

                await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    try
                    {
                        // Load image from temp file
                        var bitmap = new BitmapImage();
                        bitmap.BeginInit();
                        bitmap.CacheOption = BitmapCacheOption.OnLoad;
                        bitmap.UriSource = new Uri(_tempFilePath);
                        bitmap.EndInit();
                        bitmap.Freeze();

                        PreviewImage = bitmap;
                        
                        // Notify that image is loaded so window can fit it
                        OnImageLoaded?.Invoke();
                    }
                    catch (Exception ex)
                    {
                        ErrorMessage = $"Failed to load image: {ex.Message}";
                        System.Diagnostics.Debug.WriteLine($"Failed to create bitmap: {ex.Message}");
                    }
                });
            }
            catch (Exception ex)
            {
                await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    ErrorMessage = $"Failed to load image: {ex.Message}\n\nVAMS may not have a preview file available for this file.";
                });
                System.Diagnostics.Debug.WriteLine($"Failed to load image: {ex.Message}");
            }
            finally
            {
                IsLoading = false;
            }
        }

        private async Task DownloadFileAsync()
        {
            try
            {
                // Show save file dialog
                var saveDialog = new SaveFileDialog
                {
                    FileName = Path.GetFileName(_file.Path),
                    Filter = GetFileFilter(_file.Path),
                    Title = "Save Image As"
                };

                if (saveDialog.ShowDialog() == true)
                {
                    IsDownloading = true;
                    DownloadProgress = 0;
                    
                    // Get the directory where user wants to save the file
                    var targetDirectory = Path.GetDirectoryName(saveDialog.FileName);
                    var targetFileName = Path.GetFileName(saveDialog.FileName);
                    
                    // Download using CLI (pass only directory, CLI will create file inside)
                    // The CLI creates the file with the original filename from fileKey
                    await _vamsService.DownloadFileAsync(
                        targetDirectory, _fileItem.DatabaseId, _fileItem.AssetId, _file.RelativePath);
                    
                    // The CLI downloads the file with its original name, so we need to rename it
                    var downloadedFilePath = Path.Combine(targetDirectory, Path.GetFileName(_file.RelativePath));
                    var finalFilePath = saveDialog.FileName;
                    
                    // If the user chose a different filename, rename the downloaded file
                    if (downloadedFilePath != finalFilePath && File.Exists(downloadedFilePath))
                    {
                        // Delete target file if it exists
                        if (File.Exists(finalFilePath))
                        {
                            File.Delete(finalFilePath);
                        }
                        File.Move(downloadedFilePath, finalFilePath);
                    }
                    
                    ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"File downloaded successfully to:\n{finalFilePath}", 
                        "Download Complete");
                }
            }
            catch (Exception ex)
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"Failed to download file: {ex.Message}", 
                    "Download Error");
            }
            finally
            {
                IsDownloading = false;
                DownloadProgress = 0;
            }
        }

        private bool IsImageFile(string filePath)
        {
            var extension = Path.GetExtension(filePath).ToLowerInvariant();
            var imageExtensions = new[] { ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp" };
            return imageExtensions.Contains(extension);
        }

        private string GetFileFilter(string filePath)
        {
            var extension = Path.GetExtension(filePath).ToLowerInvariant();
            return extension switch
            {
                ".png" => "PNG Images (*.png)|*.png|All Files (*.*)|*.*",
                ".jpg" => "JPEG Images (*.jpg;*.jpeg)|*.jpg;*.jpeg|All Files (*.*)|*.*",
                ".jpeg" => "JPEG Images (*.jpg;*.jpeg)|*.jpg;*.jpeg|All Files (*.*)|*.*",
                ".gif" => "GIF Images (*.gif)|*.gif|All Files (*.*)|*.*",
                ".bmp" => "Bitmap Images (*.bmp)|*.bmp|All Files (*.*)|*.*",
                ".tiff" => "TIFF Images (*.tiff;*.tif)|*.tiff;*.tif|All Files (*.*)|*.*",
                ".tif" => "TIFF Images (*.tiff;*.tif)|*.tiff;*.tif|All Files (*.*)|*.*",
                _ => "All Files (*.*)|*.*"
            };
        }

        public void Cleanup()
        {
            Dispose();
        }

        public void Dispose()
        {
            Dispose(true);
            GC.SuppressFinalize(this);
        }

        protected virtual void Dispose(bool disposing)
        {
            if (!_disposed)
            {
                if (disposing)
                {
                    try
                    {
                        // Clear event handlers to prevent memory leaks
                        OnImageLoaded = null;

                        // Dispose of the image properly
                        if (PreviewImage != null)
                        {
                            // BitmapImage doesn't implement IDisposable, but we can clear the reference
                            PreviewImage = null;
                        }

                        // Clean up temp file
                        if (!string.IsNullOrEmpty(_tempFilePath) && File.Exists(_tempFilePath))
                        {
                            try
                            {
                                File.Delete(_tempFilePath);
                            }
                            catch (Exception ex)
                            {
                                System.Diagnostics.Debug.WriteLine($"Failed to delete temp file: {ex.Message}");
                            }
                        }

                        // Clear all properties
                        ErrorMessage = null;
                        ErrorMessageVisible = false;
                        IsLoading = false;
                        IsDownloading = false;
                        DownloadProgress = 0;
                        FilePath = null;
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"Error during ViewModel disposal: {ex.Message}");
                    }
                }
                _disposed = true;
            }
        }

        ~ImagePreviewViewModel()
        {
            Dispose(false);
        }

        #endregion
    }
}
