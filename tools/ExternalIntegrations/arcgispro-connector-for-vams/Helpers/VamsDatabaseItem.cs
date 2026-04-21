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
    public class VamsDatabaseItem : VamsItemBase
    {
        private readonly VamsCliService _vamsService;
        private readonly Database _database;

        public VamsDatabaseItem(VamsCliService vamsService, Database database)
        {
            _vamsService = vamsService;
            _database = database;
            Name = database.DatabaseName;
            DatabaseId = database.DatabaseId;
        }

        public string DatabaseId { get; private set; }

        public VamsDatabaseExplorer.Models.Database GetDatabase()
        {
            return _database;
        }

        public override void LoadChildren()
        {
            System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: LoadChildren called for database '{_database.DatabaseName}' (ID: {_database.DatabaseId}, AssetCount: {_database.AssetCount})");

            // Use Task.Run to avoid async void
            _ = Task.Run(async () =>
            {
                try
                {
                    if (_database.AssetCount > 0)
                    {
                        System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Loading assets for database {_database.DatabaseName}");

                        var assets = await _vamsService.GetAssetsForDatabaseAsync(_database.DatabaseId);

                        System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Retrieved {assets.Count} assets, updating UI");

                        // Update UI on main thread
                        await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                        {
                            System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Adding {assets.Count} assets to Children collection");

                            // Clear any existing children first (should just be the lazy load child)
                            Children.Clear();

                            foreach (var asset in assets)
                            {
                                var assetItem = new VamsAssetItem(_vamsService, asset);
                                Children.Add(assetItem);
                                System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Added asset '{asset.AssetName}' to children");
                            }

                            System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Children collection now has {Children.Count} items");
                        });
                    }
                    else
                    {
                        System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Database {_database.DatabaseName} has no assets, clearing children");

                        // Clear children if no assets
                        await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                        {
                            Children.Clear();
                        });
                    }
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"VamsDatabaseItem: Error loading assets for database {_database.DatabaseName}: {ex}");

                    await System.Windows.Application.Current.Dispatcher.InvokeAsync(() =>
                    {
                        MessageBox.Show($"Error loading assets for database {_database.DatabaseName}: {ex.Message}");
                    });
                }
            });
        }
    }
}