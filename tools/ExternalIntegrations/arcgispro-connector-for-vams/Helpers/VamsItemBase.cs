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
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;

namespace VamsConnector.Helpers
{
    public class VamsItemBase : PropertyChangedBase, IDisposable
    {
        private static VamsItemBase LazyloadChild = new VamsItemBase();
        private bool _disposed = false;

        public VamsItemBase()
        {
            IsSelected = false;
            Children = new ObservableCollection<VamsItemBase>();
            Children.Add(LazyloadChild);
        }

        #region Common Properties

        private bool _isExpanded;
        public bool IsExpanded
        {
            get { return _isExpanded; }
            set
            {
                SetProperty(ref _isExpanded, value, () => IsExpanded);
                if (HasLazyloadChild)
                {
                    Children.Remove(LazyloadChild);
                    LoadChildren();
                }
            }
        }

        internal bool _isSelected;
        public virtual bool IsSelected
        {
            get { return _isSelected; }
            set
            {
                SetProperty(ref _isSelected, value, () => IsSelected);
            }
        }

        private string _name;
        public string Name
        {
            get { return _name; }
            set
            {
                SetProperty(ref _name, value, () => Name);
            }
        }

        private string _parentName;
        public string ParentName
        {
            get { return _parentName; }
            set
            {
                SetProperty(ref _parentName, value, () => ParentName);
            }
        }

        private ObservableCollection<VamsItemBase> _children;
        public ObservableCollection<VamsItemBase> Children
        {
            get { return _children; }
            set
            {
                SetProperty(ref _children, value, () => Children);
            }
        }

        #endregion Common Properties

        #region Child functions

        public virtual void LoadChildren() { }

        /// <summary>
        /// Returns true if this object's Children have not yet been populated.
        /// </summary>
        public bool HasLazyloadChild
        {
            get { return Children.Count == 1 && Children[0] == LazyloadChild; }
        }

        #endregion Child functions

        #region IDisposable

        public virtual void Dispose()
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
                    // Clear children collection
                    if (Children != null)
                    {
                        foreach (var child in Children)
                        {
                            if (child is IDisposable disposableChild && child != LazyloadChild)
                            {
                                disposableChild.Dispose();
                            }
                        }
                        Children.Clear();
                        Children = null;
                    }

                    // Clear properties
                    Name = null;
                }
                _disposed = true;
            }
        }

        ~VamsItemBase()
        {
            Dispose(false);
        }

        #endregion IDisposable
    }
}