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

using ArcGIS.Core.Data;
using ArcGIS.Core.Data.DDL;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using VamsConnector.Helpers;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using DDLFieldDescription = ArcGIS.Core.Data.DDL.FieldDescription;

namespace VamsConnector.Services
{
    public class VamsReferenceService
    {
        private const string FIELD_PREFIX = "Vams_";
        
        public static readonly List<string> VamsFieldNames = new List<string>
        {
            $"{FIELD_PREFIX}FileName",
            $"{FIELD_PREFIX}DatabaseId",
            $"{FIELD_PREFIX}DatabaseName", 
            $"{FIELD_PREFIX}AssetId",
            $"{FIELD_PREFIX}AssetName",
            $"{FIELD_PREFIX}FileLink",
            $"{FIELD_PREFIX}FileExtension",
            $"{FIELD_PREFIX}AddedDate"
        };

        /// <summary>
        /// Checks if the specified table/feature class has VAMS reference fields
        /// </summary>
        public static async Task<bool> HasVamsFields(MapMember mapMember)
        {
            return await QueuedTask.Run(() =>
            {
                try
                {
                    var table = GetTableFromMapMember(mapMember);
                    if (table == null) return false;

                    var tableDefinition = table.GetDefinition();
                    var existingFields = tableDefinition.GetFields().Select(f => f.Name).ToList();
                    
                    // Check if all VAMS fields exist
                    return VamsFieldNames.All(fieldName => existingFields.Contains(fieldName));
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Error checking VAMS fields: {ex.Message}");
                    return false;
                }
            });
        }

        /// <summary>
        /// Adds VAMS reference fields to the specified table/feature class
        /// </summary>
        public static async Task<bool> AddVamsFields(MapMember mapMember)
        {
            return await QueuedTask.Run(() =>
            {
                try
                {
                    var table = GetTableFromMapMember(mapMember);
                    if (table == null) return false;

                    using (var geodatabase = table.GetDatastore() as Geodatabase)
                    {
                        if (geodatabase == null) return false;

                        var tableName = table.GetName();
                        var originalDefinition = geodatabase.GetDefinition<TableDefinition>(tableName);
                        var originalDescription = new TableDescription(originalDefinition);

                        // Create field descriptions
                        var fieldDescriptions = new List<DDLFieldDescription>
                        {
                            new DDLFieldDescription($"{FIELD_PREFIX}FileName", FieldType.String) { Length = 255 },
                            new DDLFieldDescription($"{FIELD_PREFIX}DatabaseId", FieldType.String) { Length = 255 },
                            new DDLFieldDescription($"{FIELD_PREFIX}DatabaseName", FieldType.String) { Length = 255 },
                            new DDLFieldDescription($"{FIELD_PREFIX}AssetId", FieldType.String) { Length = 255 },
                            new DDLFieldDescription($"{FIELD_PREFIX}AssetName", FieldType.String) { Length = 255 },
                            new DDLFieldDescription($"{FIELD_PREFIX}FileLink", FieldType.String) { Length = 1000 },
                            new DDLFieldDescription($"{FIELD_PREFIX}FileExtension", FieldType.String) { Length = 10 },
                            new DDLFieldDescription($"{FIELD_PREFIX}AddedDate", FieldType.Date)
                        };

                        // Check if it's a feature class or table and create appropriate description
                        if (mapMember is FeatureLayer)
                        {
                            var fcDefinition = geodatabase.GetDefinition<FeatureClassDefinition>(tableName);
                            var fcDescription = new FeatureClassDescription(fcDefinition);
                            
                            // Add fields to existing feature class
                            var allFields = fcDescription.FieldDescriptions.ToList();
                            allFields.AddRange(fieldDescriptions);
                            var newFcDescription = new FeatureClassDescription(tableName, allFields, fcDescription.ShapeDescription);
                            
                            var schemaBuilder = new SchemaBuilder(geodatabase);
                            schemaBuilder.Modify(newFcDescription);
                            return schemaBuilder.Build();
                        }
                        else
                        {
                            var tableDefinition = geodatabase.GetDefinition<TableDefinition>(tableName);
                            var tableDescription = new TableDescription(tableDefinition);
                            
                            // Add fields to existing table
                            var allFields = tableDescription.FieldDescriptions.ToList();
                            allFields.AddRange(fieldDescriptions);
                            var newTableDescription = new TableDescription(tableName, allFields);
                            
                            var schemaBuilder = new SchemaBuilder(geodatabase);
                            schemaBuilder.Modify(newTableDescription);
                            return schemaBuilder.Build();
                        }
                    }
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Error adding VAMS fields: {ex.Message}");
                    MessageBox.Show($"Error adding VAMS fields: {ex.Message}", "Error", System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                    return false;
                }
            });
        }

        /// <summary>
        /// Main method to add reference to selected features - handles the entire workflow
        /// </summary>
        public static async Task AddReferenceToSelectedFeaturesAsync(VamsFileItem fileItem)
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
                var hasFields = await HasVamsFields(targetMapMember);
                
                if (!hasFields)
                {
                    var result = MessageBox.Show(
                        $"The layer '{targetMapMember.Name}' does not have VAMS reference fields.\n\nWould you like to add them now?",
                        "Add VAMS Fields",
                        System.Windows.MessageBoxButton.YesNo,
                        System.Windows.MessageBoxImage.Question);

                    if (result == System.Windows.MessageBoxResult.Yes)
                    {
                        var fieldsAdded = await AddVamsFields(targetMapMember);
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
                    $"Add VAMS file reference '{fileItem.Name}' to {selectionCount} selected feature(s) in '{targetMapMember.Name}'?",
                    "Confirm Reference Addition",
                    System.Windows.MessageBoxButton.YesNo,
                    System.Windows.MessageBoxImage.Question);

                if (confirmResult == System.Windows.MessageBoxResult.Yes)
                {
                    var success = await PopulateVamsReference(targetMapMember, fileItem);
                    
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
            catch (Exception ex)
            {
                MessageBox.Show($"An error occurred: {ex.Message}", "Error",
                    System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
            }
        }

        /// <summary>
        /// Populates VAMS reference data for selected features/rows
        /// </summary>
        public static async Task<bool> PopulateVamsReference(MapMember mapMember, VamsFileItem fileItem)
        {
            return await QueuedTask.Run(() =>
            {
                try
                {
                    var table = GetTableFromMapMember(mapMember);
                    if (table == null) return false;

                    // Get selected object IDs
                    var selectedOIDs = GetSelectedObjectIDs(mapMember);
                    if (selectedOIDs == null || !selectedOIDs.Any())
                    {
                        MessageBox.Show("No features/rows are selected. Please select features first.", "No Selection", 
                            System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Information);
                        return false;
                    }

                    // Create the custom URL for the file
                    var fileLink = CreateVamsFileUrl(fileItem);
                    var file = fileItem.GetFile();
                    
                    
                    // Prepare the attribute values
                    var attributes = new Dictionary<string, object>
                    {
                        [$"{FIELD_PREFIX}FileName"] = fileItem.Name,
                        [$"{FIELD_PREFIX}DatabaseId"] = fileItem.DatabaseId,
                        [$"{FIELD_PREFIX}DatabaseName"] = fileItem.DatabaseName,
                        [$"{FIELD_PREFIX}AssetId"] = fileItem.AssetId,
                        [$"{FIELD_PREFIX}AssetName"] = fileItem.AssetName,
                        [$"{FIELD_PREFIX}FileLink"] = fileLink,
                        [$"{FIELD_PREFIX}FileExtension"] = GetFileExtension(fileItem.FilePath),
                        [$"{FIELD_PREFIX}AddedDate"] = DateTime.Now
                    };

                    // Update each selected feature/row
                    var queryFilter = new QueryFilter { ObjectIDs = selectedOIDs.ToList() };
                    using (var rowCursor = table.Search(queryFilter))
                    {
                        var editOperation = new ArcGIS.Desktop.Editing.EditOperation();
                        editOperation.Name = "Add VAMS File Reference";

                        while (rowCursor.MoveNext())
                        {
                            using (var row = rowCursor.Current)
                            {
                                editOperation.Modify(row, attributes);
                            }
                        }

                        return editOperation.Execute();
                    }
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Error populating VAMS reference: {ex.Message}");
                    MessageBox.Show($"Error populating VAMS reference: {ex.Message}", "Error", 
                        System.Windows.MessageBoxButton.OK, System.Windows.MessageBoxImage.Error);
                    return false;
                }
            });
        }

        private static Table GetTableFromMapMember(MapMember mapMember)
        {
            if (mapMember is FeatureLayer featureLayer)
                return featureLayer.GetTable();
            else if (mapMember is StandaloneTable standaloneTable)
                return standaloneTable.GetTable();
            
            return null;
        }

        private static IList<long> GetSelectedObjectIDs(MapMember mapMember)
        {
            if (mapMember is FeatureLayer featureLayer)
            {
                var selection = featureLayer.GetSelection();
                return selection.GetObjectIDs().ToList();
            }
            else if (mapMember is StandaloneTable standaloneTable)
            {
                var selection = standaloneTable.GetSelection();
                return selection.GetObjectIDs().ToList();
            }
            
            return new List<long>();
        }

        private static string CreateVamsFileUrl(VamsFileItem fileItem)
        {
            // Get web deployed URL from VamsCliService
            var webBaseUrl = VamsDatabaseExplorer.Services.VamsCliService.GetWebDeployedUrl();
            
            if (string.IsNullOrEmpty(webBaseUrl))
            {
                // No web URL available - return empty or placeholder
                return "Web URL not available - No VAMS web deployment configured";
            }
            
            var file = fileItem.GetFile();
            var fileKey = Uri.EscapeDataString(file.RelativePath.TrimStart('/'));
            
            // New format: <base_url>#/databases/<databaseId>/assets/<assetId>/file/<fileKey>
            return $"{webBaseUrl}#/databases/{Uri.EscapeDataString(fileItem.DatabaseId)}/assets/{Uri.EscapeDataString(fileItem.AssetId)}/file/{fileKey}";
        }

        private static async Task<int> GetSelectionCount(MapMember mapMember)
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



        private static string GetFileExtension(string filePath)
        {
            var extension = System.IO.Path.GetExtension(filePath);
            return string.IsNullOrEmpty(extension) ? "" : extension.ToLowerInvariant();
        }

        private static string GetFileType(string filePath)
        {
            var extension = System.IO.Path.GetExtension(filePath)?.ToLowerInvariant();
            
            return extension switch
            {
                ".jpg" or ".jpeg" or ".png" or ".gif" or ".bmp" or ".tiff" or ".tif" => "image",
                ".pdf" => "pdf",
                ".txt" or ".csv" => "text",
                ".zip" or ".rar" or ".7z" => "archive",
                _ => "unknown"
            };
        }
    }
}
