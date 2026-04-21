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

using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using VamsConnector.Handlers;
using VamsConnector.Services;
using System;
using System.Linq;
using System.Threading.Tasks;

namespace VamsConnector.Commands
{
    internal class OpenVamsLinkCommand : Button
    {
        protected override void OnUpdate()
        {
            // Enable the button only when we're in a table context with VAMS fields and have selected rows
            try
            {
                Enabled = IsVamsTableContextActive();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error in OnUpdate: {ex.Message}");
                Enabled = false;
            }
        }

        protected override async void OnClick()
        {
            await OpenSelectedVamsLinks();
        }

        private bool IsVamsTableContextActive()
        {
            try
            {
                var mapView = MapView.Active;
                if (mapView == null) return false;

                // Check all layers and tables in the active map for VAMS fields
                var allLayers = mapView.Map.GetLayersAsFlattenedList().OfType<FeatureLayer>().ToList();
                var allTables = mapView.Map.GetStandaloneTablesAsFlattenedList().ToList();

                // Check if any layer or table has VAMS fields and has selected features
                foreach (var layer in allLayers)
                {
                    if (HasVamsFieldsAndSelection(layer))
                        return true;
                }

                foreach (var table in allTables)
                {
                    if (HasVamsFieldsAndSelection(table))
                        return true;
                }

                return false;
            }
            catch
            {
                return false;
            }
        }

        private bool HasVamsFieldsAndSelection(MapMember mapMember)
        {
            // Don't use QueuedTask.Run in OnUpdate as it causes threading issues
            // Just return true for now - validation will happen in OnClick
            return true;
        }

        private async Task<bool> HasVamsFieldsAndSelectionAsync(MapMember mapMember)
        {
            try
            {
                return await QueuedTask.Run(() =>
                {
                    try
                    {
                        var table = mapMember is FeatureLayer featureLayer ?
                            featureLayer.GetTable() :
                            ((StandaloneTable)mapMember).GetTable();

                        // Check if it has VAMS fields
                        var tableDefinition = table.GetDefinition();
                        var fieldNames = tableDefinition.GetFields().Select(f => f.Name).ToList();
                        var hasVamsFields = fieldNames.Any(name => name.StartsWith("Vams_"));

                        if (!hasVamsFields) return false;

                        // Check if it has selected rows
                        var selection = mapMember is FeatureLayer fl ?
                            fl.GetSelection() :
                            ((StandaloneTable)mapMember).GetSelection();

                        return selection.GetCount() > 0;
                    }
                    catch
                    {
                        return false;
                    }
                });
            }
            catch
            {
                return false;
            }
        }

        private async Task OpenSelectedVamsLinks()
        {
            try
            {
                var mapView = MapView.Active;
                if (mapView == null)
                {
                    MessageBox.Show("No active map found.", "No Active Map",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Find the layer/table with VAMS fields that has selected rows
                MapMember targetMapMember = null;

                // Check all layers first
                var allLayers = mapView.Map.GetLayersAsFlattenedList().OfType<FeatureLayer>().ToList();
                foreach (var layer in allLayers)
                {
                    if (await HasVamsFieldsAndSelectionAsync(layer))
                    {
                        targetMapMember = layer;
                        break;
                    }
                }

                // If no layer found, check standalone tables
                if (targetMapMember == null)
                {
                    var allTables = mapView.Map.GetStandaloneTablesAsFlattenedList().ToList();
                    foreach (var table in allTables)
                    {
                        if (await HasVamsFieldsAndSelectionAsync(table))
                        {
                            targetMapMember = table;
                            break;
                        }
                    }
                }

                if (targetMapMember == null)
                {
                    MessageBox.Show("No layer or table with VAMS references and selected rows found.", "No Valid Selection",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Check if it has VAMS fields
                var hasVamsFields = await VamsReferenceService.HasVamsFields(targetMapMember);
                if (!hasVamsFields)
                {
                    MessageBox.Show($"The selected layer '{targetMapMember.Name}' does not have VAMS reference fields.", "No VAMS Fields",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Get selected features and their VAMS links
                var vamsLinks = await GetVamsLinksFromSelection(targetMapMember);
                
                if (!vamsLinks.Any())
                {
                    MessageBox.Show("No features selected or no VAMS links found in selected features.", "No Links Found",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Open each unique link
                var uniqueLinks = vamsLinks.Distinct().ToList();
                
                if (uniqueLinks.Count > 5)
                {
                    var result = MessageBox.Show($"This will open {uniqueLinks.Count} VAMS links. Continue?", "Multiple Links",
                        System.Windows.MessageBoxButton.YesNo, System.Windows.MessageBoxImage.Question);
                    
                    if (result != System.Windows.MessageBoxResult.Yes)
                        return;
                }

                foreach (var link in uniqueLinks.Take(10)) // Limit to 10 to prevent overwhelming
                {
                    if (!string.IsNullOrEmpty(link))
                    {
                        VamsUrlHandler.HandleVamsUrl(link);
                    }
                }

                MessageBox.Show($"Opened {uniqueLinks.Count} VAMS link(s).", "Links Opened",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
            }
            catch (System.Exception ex)
            {
                MessageBox.Show($"Error opening VAMS links: {ex.Message}", "Error",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }

        private async Task<System.Collections.Generic.List<string>> GetVamsLinksFromSelection(MapMember mapMember)
        {
            return await QueuedTask.Run(() =>
            {
                var links = new System.Collections.Generic.List<string>();
                
                try
                {
                    var table = mapMember is FeatureLayer featureLayer ? 
                        featureLayer.GetTable() : 
                        ((StandaloneTable)mapMember).GetTable();

                    // Get selected object IDs
                    var selectedOIDs = mapMember is FeatureLayer fl ? 
                        fl.GetSelection().GetObjectIDs().ToList() :
                        ((StandaloneTable)mapMember).GetSelection().GetObjectIDs().ToList();

                    if (!selectedOIDs.Any()) return links;

                    // Query for Vams_FileLink field
                    var queryFilter = new ArcGIS.Core.Data.QueryFilter 
                    { 
                        ObjectIDs = selectedOIDs,
                        SubFields = "Vams_FileLink"
                    };

                    using (var cursor = table.Search(queryFilter))
                    {
                        while (cursor.MoveNext())
                        {
                            using (var row = cursor.Current)
                            {
                                var linkValue = row["Vams_FileLink"];
                                if (linkValue != null && !string.IsNullOrEmpty(linkValue.ToString()))
                                {
                                    links.Add(linkValue.ToString());
                                }
                            }
                        }
                    }
                }
                catch (System.Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Error getting VAMS links: {ex.Message}");
                }

                return links;
            });
        }
    }
}