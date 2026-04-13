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
using System.Threading.Tasks;
using System.Windows.Input;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using VamsConnector.Helpers;
using VamsDatabaseExplorer.Services;

namespace VamsConnector
{
    internal class DatabaseExplorerViewModel : DockPane
    {
        private const string _dockPaneID = "VamsConnector_DatabaseExplorer";
        private readonly VamsCliService _vamsService;
        private bool _isLoggedIn = false;

        protected DatabaseExplorerViewModel()
        {
            _vamsService = new VamsCliService();
            
            // Check authentication status when pane is created
            // Use fire-and-forget pattern for async operation
            _ = CheckAuthenticationOnShowAsync();
        }

        private async Task CheckAuthenticationOnShowAsync()
        {
            try
            {
                var isAuthenticated = await _vamsService.CheckAuthenticationAsync();
                
                if (isAuthenticated)
                {
                    LoginStatus = "Authenticated";
                    _isLoggedIn = true;
                    
                    // Auto-load databases if already authenticated
                    await LoadVamsDatabasesAsync();
                }
                else
                {
                    LoginStatus = "Not authenticated - Click button to login";
                    _isLoggedIn = false;
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Constructor: Error checking authentication: {ex.Message}");
                LoginStatus = "Authentication check failed";
                _isLoggedIn = false;
            }
        }

        #region Properties

        private List<VamsItemBase> _vamsItems;
        public List<VamsItemBase> VamsItems
        {
            get { return _vamsItems; }
            set
            {
                SetProperty(ref _vamsItems, value, () => VamsItems);
            }
        }

        private string _loginStatus = "Not logged in";
        public string LoginStatus
        {
            get { return _loginStatus; }
            set
            {
                SetProperty(ref _loginStatus, value, () => LoginStatus);
            }
        }

        private VamsItemBase _selectedItem;
        public VamsItemBase SelectedItem
        {
            get { return _selectedItem; }
            set
            {
                SetProperty(ref _selectedItem, value, () => SelectedItem);
                UpdateSelectedItemDetails();
            }
        }

        private string _selectedItemDetails = "Select an item to view details";
        public string SelectedItemDetails
        {
            get { return _selectedItemDetails; }
            set
            {
                SetProperty(ref _selectedItemDetails, value, () => SelectedItemDetails);
            }
        }


        #endregion Properties

        #region Commands



        public ICommand CmdLoginAndLoadVams
        {
            get
            {
                return new RelayCommand(async () => {
                    try
                    {
                        // Get auth type first
                        LoginStatus = "Checking authentication type...";
                        string authType;
                        try
                        {
                            authType = await _vamsService.GetAuthTypeAsync();
                        }
                        catch (Exception ex)
                        {
                            LoginStatus = "Profile setup error";
                            MessageBox.Show(ex.Message, "Profile Setup Required");
                            return;
                        }
                        
                        // Show login dialog with appropriate auth type
                        var loginDialog = new LoginDialog(authType);
                        if (loginDialog.ShowDialog() == true)
                        {
                            LoginStatus = "Logging in...";
                            var username = loginDialog.Username;
                            var passwordOrToken = loginDialog.Password;
                            
                            var profileName = await _vamsService.LoginAsync(username, passwordOrToken);
                            LoginStatus = $"Logged in: {profileName}";
                            _isLoggedIn = true;

                            // Load VAMS databases
                            await LoadVamsDatabasesAsync();
                        }
                        else
                        {
                            LoginStatus = "Login cancelled";
                        }
                    }
                    catch (Exception ex)
                    {
                        LoginStatus = $"Error: {ex.Message}";
                        MessageBox.Show($"Failed to login or load databases: {ex.Message}", "VAMS Error");
                        _isLoggedIn = false;
                    }
                }, true);
            }
        }

        public ICommand CmdRefreshVams
        {
            get
            {
                return new RelayCommand(async () => {
                    try
                    {
                        System.Diagnostics.Debug.WriteLine("=== REFRESH: Starting refresh process ===");
                        
                        // Check if already authenticated
                        var isAuthenticated = await _vamsService.CheckAuthenticationAsync();
                        
                        if (!isAuthenticated)
                        {
                            // Get auth type for login dialog
                            LoginStatus = "Checking authentication type...";
                            string authType;
                            try
                            {
                                authType = await _vamsService.GetAuthTypeAsync();
                            }
                            catch (Exception ex)
                            {
                                LoginStatus = "Profile setup error";
                                MessageBox.Show(ex.Message, "Profile Setup Required");
                                return;
                            }
                            
                            // Show login dialog for re-authentication
                            LoginStatus = "Re-authentication required...";
                            var loginDialog = new LoginDialog(authType);
                            if (loginDialog.ShowDialog() == true)
                            {
                                var username = loginDialog.Username;
                                var passwordOrToken = loginDialog.Password;
                                
                                var profileName = await _vamsService.LoginAsync(username, passwordOrToken);
                                System.Diagnostics.Debug.WriteLine($"REFRESH: LoginAsync completed. Profile: {profileName}");
                                LoginStatus = $"Re-authenticated: {profileName}";
                                _isLoggedIn = true;
                            }
                            else
                            {
                                LoginStatus = "Re-authentication cancelled";
                                return;
                            }
                        }
                        else
                        {
                            LoginStatus = "Refreshing...";
                        }

                        // Load VAMS databases
                        System.Diagnostics.Debug.WriteLine("REFRESH: Calling LoadVamsDatabasesAsync...");
                        await LoadVamsDatabasesAsync();
                        
                        System.Diagnostics.Debug.WriteLine("=== REFRESH: Refresh completed successfully ===");
                    }
                    catch (Exception ex)
                    {
                        System.Diagnostics.Debug.WriteLine($"REFRESH ERROR: {ex.Message}");
                        System.Diagnostics.Debug.WriteLine($"REFRESH ERROR Stack: {ex.StackTrace}");
                        LoginStatus = $"Error: {ex.Message}";
                        MessageBox.Show($"Failed to refresh databases: {ex.Message}", "VAMS Error");
                        _isLoggedIn = false;
                    }
                }, true);
            }
        }





        #endregion Commands

        #region Helper Methods

        private async Task LoadVamsDatabasesAsync()
        {
            LoginStatus = "Loading databases...";

            try
            {
                var databases = await _vamsService.GetAllDatabasesAsync();

                var vamsItems = new List<VamsItemBase>();
                foreach (var database in databases)
                {
                    vamsItems.Add(new VamsDatabaseItem(_vamsService, database));
                }

                // Clear existing items first
                VamsItems = null;
                VamsItems = vamsItems;

                LoginStatus = $"Loaded {databases.Count} databases";


            }
            catch (Exception ex)
            {
                LoginStatus = $"Error loading databases: {ex.Message}";
                System.Diagnostics.Debug.WriteLine($"LoadVamsDatabasesAsync Error: {ex}");
                throw;
            }
        }

        private void UpdateSelectedItemDetails()
        {
            if (_selectedItem == null)
            {
                SelectedItemDetails = "Select an item to view detailed information";
                return;
            }

            var details = new System.Text.StringBuilder();

            if (_selectedItem is VamsDatabaseItem databaseItem)
            {
                var database = GetDatabaseFromItem(databaseItem);
                if (database != null)
                {
                    details.AppendLine("DATABASE INFORMATION");
                    details.AppendLine("═══════════════════════════════════════");
                    details.AppendLine($"Name:           {database.DatabaseName}");
                    details.AppendLine($"ID:             {database.DatabaseId}");
                    details.AppendLine($"Description:    {database.Description}");
                    details.AppendLine();
                    details.AppendLine("STATISTICS");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Assets:         {database.AssetCount:N0}");
                    details.AppendLine();
                    details.AppendLine("TIMELINE");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Created:        {database.CreatedAt:yyyy-MM-dd HH:mm:ss}");
                }
            }
            else if (_selectedItem is VamsAssetItem assetItem)
            {
                var asset = GetAssetFromItem(assetItem);
                if (asset != null)
                {
                    // Use calculated values from VamsAssetItem if available (after files are loaded)
                    int fileCount = assetItem.CalculatedFileCount > 0 ? assetItem.CalculatedFileCount : asset.FileCount;
                    long totalSize = assetItem.CalculatedTotalSize > 0 ? assetItem.CalculatedTotalSize : asset.TotalSize;
                    
                    details.AppendLine("ASSET INFORMATION");
                    details.AppendLine("═══════════════════════════════════════");
                    details.AppendLine($"Name:           {asset.AssetName}");
                    details.AppendLine($"ID:             {asset.AssetId}");
                    details.AppendLine($"Database:        {asset.DatabaseName}");
                    details.AppendLine($"Description:    {asset.Description}");
                    details.AppendLine();
                    details.AppendLine("STATISTICS");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Files:          {fileCount:N0}");
                    details.AppendLine($"Total Size:     {FormatBytes(totalSize)}");
                    details.AppendLine();
                    details.AppendLine("TIMELINE");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Created:        {asset.CreatedAt:yyyy-MM-dd HH:mm:ss}");
                    details.AppendLine($"Created By:     {asset.CreatedBy}");
                }
            }
            else if (_selectedItem is VamsFileItem fileItem)
            {
                var file = GetFileFromItem(fileItem);
                if (file != null)
                {
                    details.AppendLine("FILE INFORMATION");
                    details.AppendLine("═══════════════════════════════════════");
                    details.AppendLine($"Path:           {file.Path}");
                    details.AppendLine($"Size:           {FormatBytes(file.Size)}");
                    details.AppendLine($"Type:           {file.Type.ToUpper()}");
                    details.AppendLine($"State:          {file.State}");
                    details.AppendLine();
                    details.AppendLine("PREVIEW");
                    details.AppendLine("─────────────────────────────────────");
                    if (!string.IsNullOrEmpty(file.PreviewFile))
                    {
                        details.AppendLine($"Has Preview:    Yes");
                        details.AppendLine($"Preview Path:   {file.PreviewFile}");
                    }
                    else
                    {
                        details.AppendLine($"Has Preview:    No");
                    }
                    details.AppendLine();
                    details.AppendLine();
                    details.AppendLine("TECHNICAL");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Key:            {file.Key}");
                    details.AppendLine();
                    details.AppendLine("TIMELINE");
                    details.AppendLine("─────────────────────────────────────");
                    details.AppendLine($"Added:          {file.AddedAt:yyyy-MM-dd HH:mm:ss}");
                    details.AppendLine($"Modified:       {file.LastModifiedDateTime:yyyy-MM-dd HH:mm:ss}");
                }
            }

            SelectedItemDetails = details.ToString();
        }

        private VamsDatabaseExplorer.Models.Database GetDatabaseFromItem(VamsDatabaseItem item)
        {
            // We need to store the original database data in the item
            return item.GetDatabase();
        }

        private VamsDatabaseExplorer.Models.Asset GetAssetFromItem(VamsAssetItem item)
        {
            // We need to store the original asset data in the item
            return item.GetAsset();
        }

        private VamsDatabaseExplorer.Models.AssetFile GetFileFromItem(VamsFileItem item)
        {
            // We need to store the original file data in the item
            return item.GetFile();
        }

        private string FormatBytes(long? bytes)
        {
            if (!bytes.HasValue || bytes.Value == 0)
            {
                return "0 B";
            }

            string[] suffixes = { "B", "KB", "MB", "GB", "TB" };
            int counter = 0;
            decimal number = bytes.Value;

            while (Math.Round(number / 1024) >= 1)
            {
                number /= 1024;
                counter++;
            }

            return $"{number:n1} {suffixes[counter]}";
        }

        #endregion Helper Methods

        /// <summary>
        /// Show the DockPane.
        /// </summary>
        internal static void Show()
        {
            DockPane pane = FrameworkApplication.DockPaneManager.Find(_dockPaneID);
            if (pane == null)
                return;

            pane.Activate();
        }

        /// <summary>
        /// Text shown near the top of the DockPane.
        /// </summary>
        private string _heading = "Database Explorer";
        public string Heading
        {
            get { return _heading; }
            set
            {
                SetProperty(ref _heading, value, () => Heading);
            }
        }
    }

    /// <summary>
    /// Button implementation to show the DockPane.
    /// </summary>
    internal class DatabaseExplorer_ShowButton : Button
    {
        protected override void OnClick()
        {
            // Just show the pane - OnShow will handle authentication check
            DatabaseExplorerViewModel.Show();
        }
    }
}
