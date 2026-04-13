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

namespace VamsConnector.Helpers
{
    /// <summary>
    /// Represents a folder node in the VAMS tree structure
    /// </summary>
    public class VamsFolderItem : VamsItemBase
    {
        public VamsFolderItem(string folderName)
        {
            Name = folderName;
            // Remove the lazy load child since folders are populated immediately
            Children.Clear();
        }

        public override void LoadChildren()
        {
            // Folders are populated when created, no lazy loading needed
        }
    }
}
