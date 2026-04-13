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
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Navigation;
using System.Windows.Shapes;
using ArcGIS.Desktop.Framework.Dialogs;
using Microsoft.Win32;
using VamsConnector.Helpers;
using VamsDatabaseExplorer.Models;
using VamsDatabaseExplorer.Services;


namespace VamsConnector
{
    /// <summary>
    /// Interaction logic for DatabaseExplorerView.xaml
    /// </summary>
    public partial class DatabaseExplorerView : UserControl
    {
        public DatabaseExplorerView()
        {
            InitializeComponent();
        }

        private void TreeView_SelectedItemChanged(object sender, System.Windows.RoutedPropertyChangedEventArgs<object> e)
        {
            if (DataContext is DatabaseExplorerViewModel viewModel && e.NewValue is VamsConnector.Helpers.VamsItemBase selectedItem)
            {
                viewModel.SelectedItem = selectedItem;
            }
        }

        private void PreviewImage_Click(object sender, RoutedEventArgs e)
        {
            if (sender is MenuItem menuItem && menuItem.DataContext is VamsFileItem fileItem)
            {
                var file = fileItem.GetFile();
                if (IsImageFile(file.Path))
                {
                    var fileName = System.IO.Path.GetFileName(file.Path);
                    var previewWindow = new ImagePreviewWindow(fileName);
                    var viewModel = new ImagePreviewViewModel(fileItem, file);
                    previewWindow.DataContext = viewModel;
                    // Don't set Owner to prevent binding inheritance issues
                    // previewWindow.Owner = Window.GetWindow(this);
                    previewWindow.Show();
                }
                else
                {
                    ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show("Selected file is not an image and does not have a preview image specified.", "Preview Error");
                }
            }
        }

        private async void DownloadFile_Click(object sender, RoutedEventArgs e)
        {
            if (sender is MenuItem menuItem && menuItem.DataContext is VamsFileItem fileItem)
            {
                try
                {
                    var file = fileItem.GetFile();

                    // Show save file dialog
                    var saveDialog = new SaveFileDialog
                    {
                        FileName = System.IO.Path.GetFileName(file.Path),
                        Filter = GetFileFilter(file.Path),
                        Title = "Save File As"
                    };

                    if (saveDialog.ShowDialog() == true)
                    {
                        // Show progress window
                        var progressWindow = new Window
                        {
                            Title = "Downloading...",
                            Width = 400,
                            Height = 150,
                            WindowStartupLocation = WindowStartupLocation.CenterOwner,
                            Owner = Window.GetWindow(this),
                            ResizeMode = ResizeMode.NoResize
                        };

                        var progressContent = new StackPanel
                        {
                            Margin = new Thickness(20),
                            Children =
                            {
                                new TextBlock { Text = "Downloading file...", FontSize = 14, Margin = new Thickness(0, 0, 0, 10) },
                                new ProgressBar { IsIndeterminate = true, Height = 8, Margin = new Thickness(0, 0, 0, 10) },
                                new TextBlock { Text = System.IO.Path.GetFileName(file.Path), FontSize = 12, Foreground = new SolidColorBrush(Colors.Gray) }
                            }
                        };

                        progressWindow.Content = progressContent;
                        progressWindow.Show();

                        try
                        {
                            var vamsService = new VamsCliService();

                            // Download file directly using CLI - it handles everything
                            await vamsService.DownloadFileAsync(
                                saveDialog.FileName, fileItem.DatabaseId, fileItem.AssetId, file.RelativePath);

                            await CloseProgressWindowSafelyAsync(progressWindow);
                            ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"File downloaded successfully to:\n{saveDialog.FileName}",
                                "Download Complete");
                        }
                        finally
                        {
                            await CloseProgressWindowSafelyAsync(progressWindow);
                        }
                    }
                }
                catch (Exception ex)
                {
                    ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"Failed to download file: {ex.Message}",
                        "Download Error");
                }
            }
        }

        private bool IsImageFile(string path)
        {
            var extension = System.IO.Path.GetExtension(path).ToLowerInvariant();
            var imageExtensions = new[] { ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp" };
            return imageExtensions.Contains(extension);
        }

        private async void DownloadAsset_Click(object sender, RoutedEventArgs e)
        {
            if (sender is MenuItem menuItem && menuItem.DataContext is VamsAssetItem assetItem)
            {
                try
                {
                    var asset = assetItem.GetAsset();

                    // Use FolderBrowserDialog for proper folder selection
                    using (var folderDialog = new System.Windows.Forms.FolderBrowserDialog())
                    {
                        folderDialog.Description = "Select folder to download asset files";
                        folderDialog.ShowNewFolderButton = true;
                        
                        if (folderDialog.ShowDialog() == System.Windows.Forms.DialogResult.OK)
                        {
                            var selectedFolder = folderDialog.SelectedPath;
                            if (!string.IsNullOrEmpty(selectedFolder))
                            {
                                await DownloadAllAssetFilesAsync(assetItem, selectedFolder);
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"Failed to download asset: {ex.Message}",
                        "Download Error");
                }
            }
        }

        private async Task DownloadAllAssetFilesAsync(VamsAssetItem assetItem, string downloadFolder)
        {
            var asset = assetItem.GetAsset();
            var vamsService = new VamsCliService();

            // Create progress window
            var progressWindow = new Window
            {
                Title = "Downloading Asset Files...",
                Width = 500,
                Height = 180,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                Owner = Window.GetWindow(this),
                ResizeMode = ResizeMode.NoResize
            };

            var progressContent = new StackPanel
            {
                Margin = new Thickness(20)
            };

            var titleText = new TextBlock
            {
                Text = $"Downloading files from: {asset.AssetName}",
                FontSize = 14,
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 0, 0, 10)
            };

            var statusText = new TextBlock
            {
                Text = "Downloading all files recursively...",
                FontSize = 12,
                Margin = new Thickness(0, 0, 0, 10)
            };

            var progressBar = new ProgressBar
            {
                IsIndeterminate = true,
                Height = 8,
                Margin = new Thickness(0, 0, 0, 10)
            };

            var detailText = new TextBlock
            {
                Text = "Please wait while vamscli downloads all asset files...",
                FontSize = 10,
                Foreground = new SolidColorBrush(Colors.Gray)
            };

            progressContent.Children.Add(titleText);
            progressContent.Children.Add(statusText);
            progressContent.Children.Add(progressBar);
            progressContent.Children.Add(detailText);

            progressWindow.Content = progressContent;
            progressWindow.Show();

            var stopwatch = System.Diagnostics.Stopwatch.StartNew();

            try
            {
                // Create asset folder
                var assetFolder = System.IO.Path.Combine(downloadFolder, SanitizeFileName(asset.AssetName));
                System.IO.Directory.CreateDirectory(assetFolder);

                // Use vamscli to download all files recursively with a single command
                var downloadResult = await vamsService.DownloadAssetRecursivelyAsync(assetFolder, asset.DatabaseId, asset.AssetId);

                // Clean window closure to prevent binding errors
                await CloseProgressWindowSafelyAsync(progressWindow);
                stopwatch.Stop();

                // Build detailed completion message
                var messageBuilder = new System.Text.StringBuilder();
                
                if (downloadResult.OverallSuccess)
                {
                    messageBuilder.AppendLine("✓ Asset download completed successfully!");
                }
                else
                {
                    messageBuilder.AppendLine("⚠ Asset download completed with some failures");
                }
                
                messageBuilder.AppendLine();
                messageBuilder.AppendLine("SUMMARY");
                messageBuilder.AppendLine("═══════════════════════════════════════");
                messageBuilder.AppendLine($"Total Files:        {downloadResult.TotalFiles}");
                messageBuilder.AppendLine($"Successful:         {downloadResult.SuccessfulFiles}");
                
                if (downloadResult.FailedFiles > 0)
                {
                    messageBuilder.AppendLine($"Failed:             {downloadResult.FailedFiles}");
                }
                
                messageBuilder.AppendLine($"Total Size:         {downloadResult.TotalSizeFormatted}");
                messageBuilder.AppendLine($"Duration:           {downloadResult.DownloadDuration:F1}s");
                messageBuilder.AppendLine($"Average Speed:      {downloadResult.AverageSpeedFormatted}");
                messageBuilder.AppendLine();
                messageBuilder.AppendLine($"Location:           {assetFolder}");

                // Add failed downloads details if any
                if (downloadResult.FailedDownloads.Count > 0)
                {
                    messageBuilder.AppendLine();
                    messageBuilder.AppendLine("FAILED DOWNLOADS");
                    messageBuilder.AppendLine("─────────────────────────────────────");
                    foreach (var failed in downloadResult.FailedDownloads)
                    {
                        messageBuilder.AppendLine($"• {failed.RelativeKey}");
                        messageBuilder.AppendLine($"  Error: {failed.Error}");
                    }
                }

                var title = downloadResult.OverallSuccess ? "Download Complete" : "Download Completed with Warnings";
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(messageBuilder.ToString(), title);

                // Open the folder in Windows Explorer if any files were downloaded
                if (downloadResult.SuccessfulFiles > 0)
                {
                    System.Diagnostics.Process.Start("explorer.exe", assetFolder);
                }
            }
            catch (Exception ex)
            {
                await CloseProgressWindowSafelyAsync(progressWindow);
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show($"Failed to download asset files: {ex.Message}",
                    "Download Error");
            }
        }

        private async Task CloseProgressWindowSafelyAsync(Window progressWindow)
        {
            try
            {
                if (progressWindow == null) return;

                // Clear DataContext and bindings to prevent ArcGIS Pro binding errors
                progressWindow.DataContext = null;
                progressWindow.Owner = null;

                // Clear bindings on all child elements
                ClearBindingsRecursively(progressWindow);

                // Clear content to break references
                if (progressWindow.Content is StackPanel stackPanel)
                {
                    stackPanel.Children.Clear();
                    progressWindow.Content = null;
                }

                // Give time for animations to complete
                await Task.Delay(100);

                // Hide first, then close
                progressWindow.Hide();
                await Task.Delay(50);

                progressWindow.Close();

                // Explicitly null the reference
                progressWindow = null;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error closing progress window: {ex.Message}");
                try { progressWindow?.Close(); } catch { }
            }
        }

        private void ClearBindingsRecursively(DependencyObject obj)
        {
            try
            {
                if (obj == null) return;

                // Clear bindings on this object
                BindingOperations.ClearAllBindings(obj);

                // Recursively clear bindings on children
                int childCount = VisualTreeHelper.GetChildrenCount(obj);
                for (int i = 0; i < childCount; i++)
                {
                    var child = VisualTreeHelper.GetChild(obj, i);
                    ClearBindingsRecursively(child);
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error clearing bindings: {ex.Message}");
            }
        }

        private string SanitizeFileName(string fileName)
        {
            var invalidChars = System.IO.Path.GetInvalidFileNameChars();
            var sanitized = new string(fileName.Where(c => !invalidChars.Contains(c)).ToArray());
            return string.IsNullOrWhiteSpace(sanitized) ? "Asset" : sanitized;
        }

        private string GetFileFilter(string filePath)
        {
            var extension = System.IO.Path.GetExtension(filePath).ToLowerInvariant();
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
    }
}
