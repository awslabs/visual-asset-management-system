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
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using System.Windows.Controls;

namespace VamsConnector
{
    public partial class ImagePreviewWindow : Window
    {
        private bool _isClosing = false;

        public ImagePreviewWindow()
        {
            InitializeComponent();
            
            // Suppress binding errors for this window
            System.Diagnostics.PresentationTraceSources.DataBindingSource.Switch.Level = System.Diagnostics.SourceLevels.Critical;
        }

        public ImagePreviewWindow(string fileName) : this()
        {
            Title = $"Image Preview - {fileName}";
        }

        protected override void OnContentRendered(EventArgs e)
        {
            base.OnContentRendered(e);
            
            // Subscribe to image loaded event
            if (DataContext is ImagePreviewViewModel viewModel)
            {
                viewModel.OnImageLoaded += OnImageLoadedHandler;
            }

            // Subscribe to size changed events
            ImageScrollViewer.SizeChanged += ImageScrollViewer_SizeChanged;
        }

        private async void OnImageLoadedHandler()
        {
            // Wait for the window to be fully rendered
            await Task.Delay(100);
            
            // Ensure we're on the UI thread
            await Dispatcher.InvokeAsync(() =>
            {
                FitImageToWindow();
            }, System.Windows.Threading.DispatcherPriority.Loaded);
        }

        private void ImageScrollViewer_SizeChanged(object sender, SizeChangedEventArgs e)
        {
            if (PreviewImage?.Source != null)
            {
                UpdateImageLayout();
            }
        }

        private async void CloseButton_Click(object sender, RoutedEventArgs e)
        {
            await CloseWindowSafelyAsync();
        }

        private async Task CloseWindowSafelyAsync()
        {
            if (_isClosing) return;
            _isClosing = true;

            try
            {
                // Cleanup the ViewModel first
                if (DataContext is ImagePreviewViewModel viewModel)
                {
                    viewModel.Cleanup();
                }

                // Clear the DataContext immediately
                DataContext = null;

                // Clear the Owner to prevent parent window binding issues
                Owner = null;

                // Clear all bindings on this window
                BindingOperations.ClearAllBindings(this);

                // Give a longer delay to allow any animations to complete
                await Task.Delay(200);

                // Hide the window first to prevent visual glitches
                Hide();

                // Small additional delay
                await Task.Delay(50);

                // Close the window
                Close();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error during window close: {ex.Message}");
                // Force close even if there's an error
                try 
                { 
                    Hide();
                    await Task.Delay(50);
                    Close(); 
                } 
                catch { }
            }
        }

        protected override void OnClosing(System.ComponentModel.CancelEventArgs e)
        {
            if (!_isClosing)
            {
                e.Cancel = true;
                _ = Task.Run(async () =>
                {
                    await Dispatcher.InvokeAsync(async () => await CloseWindowSafelyAsync());
                });
                return;
            }
            base.OnClosing(e);
        }

        protected override void OnClosed(System.EventArgs e)
        {
            try
            {
                // Unsubscribe from events to prevent memory leaks
                if (DataContext is ImagePreviewViewModel viewModel)
                {
                    viewModel.OnImageLoaded -= OnImageLoadedHandler;
                    viewModel.Dispose(); // Use Dispose instead of Cleanup
                }

                // Unsubscribe from size changed event
                if (ImageScrollViewer != null)
                {
                    ImageScrollViewer.SizeChanged -= ImageScrollViewer_SizeChanged;
                }

                // Clear references
                DataContext = null;
                Owner = null;

                // Clear any remaining bindings on all child elements
                ClearBindingsRecursively(this);

                // Clear named elements to break circular references
                PreviewImage = null;
                ImageScrollViewer = null;
                ImageScaleTransform = null;
                ImageTranslateTransform = null;
                ImageCanvas = null;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error during final cleanup: {ex.Message}");
            }
            finally
            {
                base.OnClosed(e);
            }
        }

        private double _currentZoom = 1.0;
        private const double ZoomFactor = 1.2;
        private const double MinZoom = 0.1;
        private const double MaxZoom = 5.0;
        
        // Panning variables
        private bool _isPanning = false;
        private Point _lastPanPoint;
        private Point _zoomCenter;
        private double _panOffsetX = 0;
        private double _panOffsetY = 0;

        private void ZoomIn_Click(object sender, RoutedEventArgs e)
        {
            if (_currentZoom < MaxZoom)
            {
                _currentZoom *= ZoomFactor;
                ApplyZoom();
                UpdateZoomDisplay();
            }
        }

        private void ZoomOut_Click(object sender, RoutedEventArgs e)
        {
            if (_currentZoom > MinZoom)
            {
                _currentZoom /= ZoomFactor;
                ApplyZoom();
                UpdateZoomDisplay();
            }
        }

        private void ZoomReset_Click(object sender, RoutedEventArgs e)
        {
            FitImageToWindow();
            UpdateZoomDisplay();
        }



        private void FitImageToWindow()
        {
            if (PreviewImage?.Source == null) return;

            try
            {
                // Get the natural size of the image
                var imageSource = PreviewImage.Source;
                var naturalWidth = imageSource.Width;
                var naturalHeight = imageSource.Height;

                // Get container size
                var containerWidth = ImageScrollViewer.ActualWidth;
                var containerHeight = ImageScrollViewer.ActualHeight;

                if (containerWidth <= 0 || containerHeight <= 0)
                {
                    // Container not ready yet, will be called again when size changes
                    _currentZoom = 1.0;
                    _panOffsetX = 0;
                    _panOffsetY = 0;
                    ApplyImageTransform();
                    return;
                }

                // Calculate the scale factor to fit the image in the window
                var scaleX = containerWidth / naturalWidth;
                var scaleY = containerHeight / naturalHeight;
                var fitScale = Math.Min(scaleX, scaleY) * 0.95; // 95% to add some padding

                // Set zoom to fit scale
                _currentZoom = fitScale;
                _panOffsetX = 0;
                _panOffsetY = 0;
                
                ApplyImageTransform();
                
                // Center the image in the scroll viewer
                CenterImageInScrollViewer();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error fitting image: {ex.Message}");
                _currentZoom = 1.0;
                _panOffsetX = 0;
                _panOffsetY = 0;
                ApplyImageTransform();
            }
        }

        private async void CenterImageInScrollViewer()
        {
            if (ImageScrollViewer == null || ImageCanvas == null) return;

            try
            {
                // Force layout update
                ImageCanvas.UpdateLayout();
                ImageScrollViewer.UpdateLayout();

                // Small delay to ensure layout is complete
                await Task.Delay(50);

                // Force another layout pass
                await Dispatcher.InvokeAsync(() =>
                {
                    ImageCanvas.UpdateLayout();
                    ImageScrollViewer.UpdateLayout();
                }, System.Windows.Threading.DispatcherPriority.Render);

                // Calculate the center position
                var scrollableWidth = ImageScrollViewer.ScrollableWidth;
                var scrollableHeight = ImageScrollViewer.ScrollableHeight;

                System.Diagnostics.Debug.WriteLine($"Centering image - ScrollableWidth: {scrollableWidth}, ScrollableHeight: {scrollableHeight}");

                // Scroll to center
                ImageScrollViewer.ScrollToHorizontalOffset(scrollableWidth / 2);
                ImageScrollViewer.ScrollToVerticalOffset(scrollableHeight / 2);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error centering image: {ex.Message}");
            }
        }



        private void UpdateZoomDisplay()
        {
            if (ZoomLevelText != null)
            {
                ZoomLevelText.Text = $"{Math.Round(_currentZoom * 100)}%";
            }
        }

        private void ImageScrollViewer_PreviewMouseWheel(object sender, System.Windows.Input.MouseWheelEventArgs e)
        {
            if (System.Windows.Input.Keyboard.Modifiers == System.Windows.Input.ModifierKeys.Control)
            {
                e.Handled = true;
                
                // Get mouse position relative to the image for zoom centering
                var mousePos = e.GetPosition(PreviewImage);
                _zoomCenter = mousePos;
                
                double oldZoom = _currentZoom;
                
                if (e.Delta > 0 && _currentZoom < MaxZoom)
                {
                    _currentZoom *= ZoomFactor;
                }
                else if (e.Delta < 0 && _currentZoom > MinZoom)
                {
                    _currentZoom /= ZoomFactor;
                }
                
                if (oldZoom != _currentZoom)
                {
                    ApplyZoomWithCenter();
                    UpdateZoomDisplay();
                }
            }
        }

        private void ImageScrollViewer_PreviewMouseLeftButtonDown(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            // Allow panning at any zoom level
            _isPanning = true;
            _lastPanPoint = e.GetPosition(ImageScrollViewer);
            ImageScrollViewer.CaptureMouse();
            ImageScrollViewer.Tag = System.Windows.Input.Cursors.Hand;
            e.Handled = true;
        }

        private void ImageScrollViewer_PreviewMouseMove(object sender, System.Windows.Input.MouseEventArgs e)
        {
            if (_isPanning && e.LeftButton == System.Windows.Input.MouseButtonState.Pressed)
            {
                var currentPoint = e.GetPosition(ImageScrollViewer);
                var deltaX = currentPoint.X - _lastPanPoint.X;
                var deltaY = currentPoint.Y - _lastPanPoint.Y;

                // Update pan offsets - apply delta directly to transform
                _panOffsetX += deltaX;
                _panOffsetY += deltaY;

                // Apply only the translate transform without recalculating layout
                if (ImageTranslateTransform != null)
                {
                    ImageTranslateTransform.X = _panOffsetX;
                    ImageTranslateTransform.Y = _panOffsetY;
                }

                _lastPanPoint = currentPoint;
                e.Handled = true;
            }
            else
            {
                // Always show hand cursor to indicate panning is available
                ImageScrollViewer.Tag = System.Windows.Input.Cursors.Hand;
            }
        }

        private void ImageScrollViewer_PreviewMouseLeftButtonUp(object sender, System.Windows.Input.MouseButtonEventArgs e)
        {
            if (_isPanning)
            {
                _isPanning = false;
                ImageScrollViewer.ReleaseMouseCapture();
                ImageScrollViewer.Tag = System.Windows.Input.Cursors.Hand;
                e.Handled = true;
            }
        }

        private void ApplyZoom()
        {
            ApplyImageTransform();
            
            // Always show hand cursor since panning is available at any scale
            ImageScrollViewer.Tag = System.Windows.Input.Cursors.Hand;
        }

        private void ApplyImageTransform()
        {
            if (ImageScaleTransform == null || ImageTranslateTransform == null || PreviewImage?.Source == null)
                return;

            try
            {
                // Apply zoom
                ImageScaleTransform.ScaleX = _currentZoom;
                ImageScaleTransform.ScaleY = _currentZoom;

                // Apply pan offset
                ImageTranslateTransform.X = _panOffsetX;
                ImageTranslateTransform.Y = _panOffsetY;

                // Update canvas and image size to ensure proper layout
                UpdateImageLayout();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error applying image transform: {ex.Message}");
            }
        }

        private void UpdateImageLayout()
        {
            if (PreviewImage?.Source == null || ImageCanvas == null) return;

            try
            {
                // Get the natural size of the image
                var imageSource = PreviewImage.Source;
                var naturalWidth = imageSource.Width;
                var naturalHeight = imageSource.Height;

                // Get container size
                var containerWidth = ImageScrollViewer.ActualWidth;
                var containerHeight = ImageScrollViewer.ActualHeight;

                if (containerWidth <= 0 || containerHeight <= 0) return;

                // Set image to its natural size (scaling is done via ScaleTransform)
                PreviewImage.Width = naturalWidth;
                PreviewImage.Height = naturalHeight;

                // Calculate zoomed size for canvas sizing
                var zoomedWidth = naturalWidth * _currentZoom;
                var zoomedHeight = naturalHeight * _currentZoom;

                // Make canvas large enough to accommodate the image at current zoom plus panning space
                var canvasWidth = Math.Max(containerWidth * 3, zoomedWidth * 2);
                var canvasHeight = Math.Max(containerHeight * 3, zoomedHeight * 2);

                ImageCanvas.Width = canvasWidth;
                ImageCanvas.Height = canvasHeight;

                // Center the image in the canvas (this is the base position, panning is done via TranslateTransform)
                var centerX = (canvasWidth - naturalWidth) / 2;
                var centerY = (canvasHeight - naturalHeight) / 2;

                Canvas.SetLeft(PreviewImage, centerX);
                Canvas.SetTop(PreviewImage, centerY);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error updating image layout: {ex.Message}");
            }
        }

        private void ApplyZoomWithCenter()
        {
            if (ImageScaleTransform == null || PreviewImage?.Source == null) return;

            try
            {
                // For now, just apply the zoom - we can enhance center-based zooming later
                ApplyImageTransform();

                // Always show hand cursor since panning is available at any scale
                ImageScrollViewer.Tag = System.Windows.Input.Cursors.Hand;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error applying zoom with center: {ex.Message}");
                // Fallback to simple zoom
                ApplyZoom();
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
    }
}