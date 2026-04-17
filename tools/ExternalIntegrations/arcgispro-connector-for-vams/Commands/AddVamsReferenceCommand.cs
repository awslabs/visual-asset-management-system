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
using VamsConnector.Helpers;
using VamsConnector.Services;
using System.Linq;
using System.Threading.Tasks;

namespace VamsConnector.Commands
{
    internal class AddVamsReferenceCommand : Button
    {
        private VamsFileItem _fileItem;

        public AddVamsReferenceCommand()
        {
        }

        public void SetFileItem(VamsFileItem fileItem)
        {
            _fileItem = fileItem;
        }

        protected override async void OnClick()
        {
            if (_fileItem == null)
            {
                MessageBox.Show("No VAMS file selected.", "Error", 
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                return;
            }

            await AddReferenceToSelectedFeatures();
        }

        private async Task AddReferenceToSelectedFeatures()
        {
            try
            {
                // Get the active map
                var mapView = MapView.Active;
                if (mapView == null)
                {
                    MessageBox.Show("No active map found. Please open a map first.", "No Active Map",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Get selected layers and tables
                var selectedLayers = mapView.GetSelectedLayers().ToList();
                var selectedTables = mapView.GetSelectedStandaloneTables().ToList();

                if (!selectedLayers.Any() && !selectedTables.Any())
                {
                    MessageBox.Show("Please select a feature layer or standalone table first.", "No Layer Selected",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // For now, work with the first selected layer/table
                MapMember targetMapMember = selectedLayers.FirstOrDefault() ?? (MapMember)selectedTables.FirstOrDefault();

                if (targetMapMember == null)
                {
                    MessageBox.Show("Selected item is not a supported layer or table type.", "Unsupported Type",
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Warning);
                    return;
                }

                // Check if VAMS fields exist
                var hasFields = await VamsReferenceService.HasVamsFields(targetMapMember);
                
                if (!hasFields)
                {
                    var result = MessageBox.Show(
                        $"The layer '{targetMapMember.Name}' does not have VAMS reference fields.\n\nWould you like to add them now?",
                        "Add VAMS Fields",
                        System.Windows.MessageBoxButton.YesNo,
                        System.Windows.MessageBoxImage.Question);

                    if (result == System.Windows.MessageBoxResult.Yes)
                    {
                        var fieldsAdded = await VamsReferenceService.AddVamsFields(targetMapMember);
                        if (!fieldsAdded)
                        {
                            MessageBox.Show("Failed to add VAMS fields to the layer.", "Error",
                                System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                            return;
                        }
                        
                        MessageBox.Show("VAMS fields have been added successfully!", "Success",
                            System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    }
                    else
                    {
                        return; // User chose not to add fields
                    }
                }

                // Get selection count for confirmation
                var selectionCount = await GetSelectionCount(targetMapMember);
                
                if (selectionCount == 0)
                {
                    MessageBox.Show($"No features/rows are selected in '{targetMapMember.Name}'.\n\nPlease select the features you want to associate with this VAMS file.", 
                        "No Selection", System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    return;
                }

                // Confirm the operation
                var confirmResult = MessageBox.Show(
                    $"Add VAMS file reference '{_fileItem.Name}' to {selectionCount} selected feature(s) in '{targetMapMember.Name}'?",
                    "Confirm Reference Addition",
                    System.Windows.MessageBoxButton.YesNo,
                    System.Windows.MessageBoxImage.Question);

                if (confirmResult == System.Windows.MessageBoxResult.Yes)
                {
                    var success = await VamsReferenceService.PopulateVamsReference(targetMapMember, _fileItem);
                    
                    if (success)
                    {
                        MessageBox.Show($"Successfully added VAMS file reference to {selectionCount} feature(s)!", "Success",
                            System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                    }
                    else
                    {
                        MessageBox.Show("Failed to add VAMS file reference.", "Error",
                            System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                    }
                }
            }
            catch (System.Exception ex)
            {
                MessageBox.Show($"An error occurred: {ex.Message}", "Error",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }

        private async Task<int> GetSelectionCount(MapMember mapMember)
        {
            return await QueuedTask.Run(() =>
            {
                try
                {
                    if (mapMember is FeatureLayer featureLayer)
                    {
                        var selection = featureLayer.GetSelection();
                        return (int)selection.GetCount();
                    }
                    else if (mapMember is StandaloneTable standaloneTable)
                    {
                        var selection = standaloneTable.GetSelection();
                        return (int)selection.GetCount();
                    }
                    
                    return 0;
                }
                catch
                {
                    return 0;
                }
            });
        }
    }
}