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
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading.Tasks;
using VamsDatabaseExplorer.Models;

namespace VamsDatabaseExplorer.Services
{
    public class VamsCliService : IDisposable
    {
        private readonly JsonSerializerOptions _jsonOptions;
        private bool _disposed = false;
        
        // Cached credentials for automatic re-authentication
        private static string _cachedUsername;
        private static string _cachedPassword;
        
        // Cached web deployed URL from auth status
        private static string _webDeployedUrl;
        
        // Cached auth type (once per session)
        private static string _cachedAuthType;
        private static bool _profileInfoFetched = false;

        public VamsCliService()
        {
            _jsonOptions = new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
            };
        }

        public async Task<string> LoginAsync(string username, string passwordOrToken)
        {
            System.Diagnostics.Debug.WriteLine("=== LoginAsync: Starting authentication ===");
            
            // Get auth type to determine which command to use
            var authType = await GetAuthTypeAsync();
            System.Diagnostics.Debug.WriteLine($"LoginAsync: Auth type: {authType}");
            
            string output;
            
            if (authType == "Cognito")
            {
                // Use regular Cognito login
                var arguments = $"auth login --json-output -u {username}";
                if (!string.IsNullOrEmpty(passwordOrToken))
                {
                    arguments += $" -p {passwordOrToken}";
                }
                
                output = await ExecuteCommandAsync("vamscli", arguments);
                System.Diagnostics.Debug.WriteLine($"LoginAsync: Cognito output: {output}");
                
                // Parse success message
                if (output.Contains("successful"))
                {
                    // Cache credentials for automatic re-authentication
                    _cachedUsername = username;
                    _cachedPassword = passwordOrToken;
                    
                    // Fetch auth status to get web URL
                    await CheckAuthenticationAsync();
                    
                    return username;
                }
            }
            else
            {
                // Use auth login with token override for external auth (JWT token or VAMS API key)
                var arguments = $"auth login --user-id {username} --token-override \"{passwordOrToken}\" --json-output";
                
                output = await ExecuteCommandAsync("vamscli", arguments);
                System.Diagnostics.Debug.WriteLine($"LoginAsync: External auth output: {output}");
                
                // Parse JSON response to check success
                var response = JsonSerializer.Deserialize<AuthStatusResponse>(output, _jsonOptions);
                if (response?.Success == true || response?.Authenticated == true)
                {
                    // Cache credentials for automatic re-authentication
                    _cachedUsername = username;
                    _cachedPassword = passwordOrToken;
                    
                    // Cache web URL if provided
                    if (!string.IsNullOrEmpty(response.WebDeployedUrl))
                    {
                        _webDeployedUrl = response.WebDeployedUrl;
                    }
                    
                    return username;
                }
            }
            
            throw new InvalidOperationException("Authentication failed");
        }

        public async Task<bool> CheckAuthenticationAsync()
        {
            try
            {
                var output = await ExecuteCommandAsync("vamscli", "auth status --json-output");
                
                // Parse JSON response to get auth status and web URL
                var authStatus = JsonSerializer.Deserialize<AuthStatusResponse>(output, _jsonOptions);
                
                if (authStatus != null)
                {
                    // Cache the web deployed URL if provided
                    if (!string.IsNullOrEmpty(authStatus.WebDeployedUrl))
                    {
                        _webDeployedUrl = authStatus.WebDeployedUrl;
                        System.Diagnostics.Debug.WriteLine($"VamsCliService: Cached web deployed URL: {_webDeployedUrl}");
                    }
                    else
                    {
                        _webDeployedUrl = null;
                        System.Diagnostics.Debug.WriteLine("VamsCliService: No web deployed URL available");
                    }
                    
                    return authStatus.Authenticated && !authStatus.IsExpired;
                }
                
                return false;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: CheckAuthenticationAsync error: {ex.Message}");
                return false;
            }
        }

        public async Task<string> GetAuthTypeAsync()
        {
            // Return cached auth type if already fetched (once per session)
            if (_profileInfoFetched && !string.IsNullOrEmpty(_cachedAuthType))
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Using cached auth type: {_cachedAuthType}");
                return _cachedAuthType;
            }

            try
            {
                System.Diagnostics.Debug.WriteLine("VamsCliService: Fetching profile info for auth type");
                var output = await ExecuteCommandAsync("vamscli", "profile info default --json-output");
                
                var profileInfo = JsonSerializer.Deserialize<ProfileInfoResponse>(output, _jsonOptions);
                
                if (profileInfo?.ProfileInfo != null)
                {
                    _cachedAuthType = profileInfo.ProfileInfo.AuthType ?? "Cognito"; // Default to Cognito
                    _profileInfoFetched = true;
                    
                    System.Diagnostics.Debug.WriteLine($"VamsCliService: Auth type: {_cachedAuthType}");
                    
                    // Also cache web URL if available
                    if (!string.IsNullOrEmpty(profileInfo.ProfileInfo.WebDeployedUrl))
                    {
                        _webDeployedUrl = profileInfo.ProfileInfo.WebDeployedUrl;
                        System.Diagnostics.Debug.WriteLine($"VamsCliService: Cached web URL from profile: {_webDeployedUrl}");
                    }
                    
                    return _cachedAuthType;
                }
                
                throw new InvalidOperationException(
                    "Profile may not be set up.\n\n" +
                    "Please run 'vamscli setup' in a terminal to configure your VAMS profile before using this tool.");
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Error fetching profile info: {ex.Message}");
                throw new InvalidOperationException(
                    "Failed to fetch profile information.\n\n" +
                    "Please ensure 'vamscli setup' has been run to configure your VAMS profile.", ex);
            }
        }

        public static string GetWebDeployedUrl()
        {
            return _webDeployedUrl;
        }

        public static bool HasWebDeployedUrl()
        {
            return !string.IsNullOrEmpty(_webDeployedUrl);
        }

        public bool HasCachedCredentials()
        {
            return !string.IsNullOrEmpty(_cachedUsername) && !string.IsNullOrEmpty(_cachedPassword);
        }

        public async Task<bool> TryAutoReauthenticateAsync()
        {
            if (!HasCachedCredentials())
            {
                return false;
            }

            try
            {
                System.Diagnostics.Debug.WriteLine("VamsCliService: Attempting automatic re-authentication with cached credentials");
                await LoginAsync(_cachedUsername, _cachedPassword);
                return true;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Auto re-authentication failed: {ex.Message}");
                return false;
            }
        }

        private async Task EnsureAuthenticatedAsync()
        {
            var isAuthenticated = await CheckAuthenticationAsync();
            if (!isAuthenticated)
            {
                // Try to auto-reauth with cached credentials
                if (HasCachedCredentials())
                {
                    var reauthSuccess = await TryAutoReauthenticateAsync();
                    if (!reauthSuccess)
                    {
                        throw new InvalidOperationException("Authentication token has expired and automatic re-authentication failed. Please login again.");
                    }
                }
                else
                {
                    throw new InvalidOperationException("Not authenticated. Please login first.");
                }
            }
        }

        public async Task LogoutAsync()
        {
            await ExecuteCommandAsync("vamscli", "auth logout");
        }

        public async Task<List<Database>> GetAllDatabasesAsync()
        {
            System.Diagnostics.Debug.WriteLine("VamsCliService: Starting GetAllDatabasesAsync");

            try
            {
                // Ensure authenticated before executing command
                await EnsureAuthenticatedAsync();
                
                // Add --json-output flag for structured JSON response
                var output = await ExecuteCommandAsync("vamscli", "database list --auto-paginate --json-output");

                System.Diagnostics.Debug.WriteLine($"VamsCliService: CLI output length: {output?.Length ?? 0}");

                // Parse JSON response with wrapper
                var response = JsonSerializer.Deserialize<DatabaseListResponse>(output, _jsonOptions);

                if (response?.Items == null)
                {
                    return new List<Database>();
                }

                System.Diagnostics.Debug.WriteLine($"VamsCliService: Parsed {response.Items.Count} databases");

                // Debug the parsed database details
                foreach (var database in response.Items)
                {
                    System.Diagnostics.Debug.WriteLine($"VamsCliService: Database - ID: '{database.DatabaseId}', AssetCount: {database.AssetCount}");
                }

                return response.Items;
            }
            catch (InvalidOperationException ex) when (ex.Message.Contains("401") || ex.Message.Contains("token has expired"))
            {
                System.Diagnostics.Debug.WriteLine("VamsCliService: Token expired, attempting re-authentication");
                throw new InvalidOperationException("Authentication token has expired. Please click Refresh to re-authenticate.", ex);
            }
        }

        public async Task<List<Asset>> GetAssetsForDatabaseAsync(string databaseId)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Starting GetAssetsForDatabaseAsync for database: {databaseId}");

            // Ensure authenticated before executing command
            await EnsureAuthenticatedAsync();
            
            // Note: Command changed from 'asset' to 'assets' (plural)
            var output = await ExecuteCommandAsync("vamscli", 
                $"assets list --database-id {databaseId} --auto-paginate --json-output");

            System.Diagnostics.Debug.WriteLine($"VamsCliService: Asset CLI output length: {output?.Length ?? 0}");

            // Parse JSON response with wrapper
            var response = JsonSerializer.Deserialize<AssetListResponse>(output, _jsonOptions);

            if (response?.Items == null)
            {
                return new List<Asset>();
            }

            System.Diagnostics.Debug.WriteLine($"VamsCliService: Parsed {response.Items.Count} assets for database {databaseId}");

            // Debug the parsed asset details
            foreach (var asset in response.Items)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Asset - ID: '{asset.AssetId}', Name: '{asset.AssetName}'");
            }

            return response.Items;
        }

        public async Task<List<AssetFile>> GetFilesForAssetAsync(string assetId, string databaseId)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Starting GetFilesForAssetAsync for asset: {assetId}, database: {databaseId}");

            // Ensure authenticated before executing command
            await EnsureAuthenticatedAsync();
            
            // Command changed significantly: file list -d <dbId> -a <assetId>
            var output = await ExecuteCommandAsync("vamscli", 
                $"file list -d {databaseId} -a {assetId} --basic --auto-paginate --json-output");

            System.Diagnostics.Debug.WriteLine($"VamsCliService: File CLI output length: {output?.Length ?? 0}");

            // Parse JSON response with wrapper
            var response = JsonSerializer.Deserialize<FileListResponse>(output, _jsonOptions);

            if (response?.Items == null)
            {
                return new List<AssetFile>();
            }

            System.Diagnostics.Debug.WriteLine($"VamsCliService: Parsed {response.Items.Count} files for asset {assetId}");

            // Debug the parsed file details
            foreach (var file in response.Items)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: File - Path: '{file.RelativePath}', Size: {file.Size}, Type: '{file.Type}', PreviewFile: '{file.PreviewFile}'");
            }

            return response.Items;
        }

        public async Task<AssetFile> GetFileInfoAsync(string databaseId, string assetId, string filePath)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Starting GetFileInfoAsync for file: {filePath}");

            // Ensure authenticated before executing command
            await EnsureAuthenticatedAsync();
            
            // Command: file info -d <dbId> -a <assetId> -p <filePath>
            var output = await ExecuteCommandAsync("vamscli", 
                $"file info -d {databaseId} -a {assetId} -p \"{filePath}\" --json-output");

            System.Diagnostics.Debug.WriteLine($"VamsCliService: File info output length: {output?.Length ?? 0}");

            // Parse JSON response (single file object, not wrapped)
            var fileInfo = JsonSerializer.Deserialize<AssetFile>(output, _jsonOptions);

            if (fileInfo == null)
            {
                throw new InvalidOperationException($"Failed to get file info for {filePath}");
            }

            System.Diagnostics.Debug.WriteLine($"VamsCliService: File info - Path: '{fileInfo.RelativePath}', PreviewFile: '{fileInfo.PreviewFile}'");

            return fileInfo;
        }

        public async Task<AssetDownloadResponse> DownloadAssetRecursivelyAsync(string downloadPath, string databaseId, string assetId)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Downloading asset recursively: {assetId} to {downloadPath}");

            // Ensure authenticated before executing command
            await EnsureAuthenticatedAsync();
            
            // Ensure directory exists
            if (!Directory.Exists(downloadPath))
            {
                Directory.CreateDirectory(downloadPath);
            }

            // Use the vamscli assets download command with --recursive flag
            // local_path is a positional argument and must come before options
            var arguments = $"assets download \"{downloadPath}\" -d {databaseId} -a {assetId} --file-key / --recursive --json-output";

            var output = await ExecuteCommandAsync("vamscli", arguments);
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Recursive download output: {output}");

            // Parse the JSON response
            var downloadResponse = JsonSerializer.Deserialize<AssetDownloadResponse>(output, _jsonOptions);
            
            if (downloadResponse == null)
            {
                throw new InvalidOperationException("Failed to parse download response");
            }

            System.Diagnostics.Debug.WriteLine($"VamsCliService: Download completed - Success: {downloadResponse.OverallSuccess}, " +
                $"Total: {downloadResponse.TotalFiles}, Successful: {downloadResponse.SuccessfulFiles}, Failed: {downloadResponse.FailedFiles}");

            return downloadResponse;
        }

        public async Task<bool> DownloadFileAsync(string localPath, string databaseId, string assetId, string fileKey)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Downloading file: {fileKey} to {localPath}");

            // Ensure authenticated before executing command
            await EnsureAuthenticatedAsync();
            
            // Determine if localPath is a directory or a full file path
            bool isDirectory = Directory.Exists(localPath) || 
                              (!File.Exists(localPath) && !Path.HasExtension(localPath));
            
            string targetDirectory;
            string expectedFileName;
            
            if (isDirectory)
            {
                // localPath is a directory - CLI will download file with original name
                targetDirectory = localPath;
                expectedFileName = Path.GetFileName(fileKey);
                
                // Ensure directory exists
                if (!Directory.Exists(targetDirectory))
                {
                    Directory.CreateDirectory(targetDirectory);
                }
            }
            else
            {
                // localPath is a full file path (used for preview downloads)
                targetDirectory = Path.GetDirectoryName(localPath);
                expectedFileName = Path.GetFileName(localPath);
                
                // Ensure directory exists
                if (!string.IsNullOrEmpty(targetDirectory) && !Directory.Exists(targetDirectory))
                {
                    Directory.CreateDirectory(targetDirectory);
                }
            }

            // Use the download command - CLI handles everything internally
            // Note: local_path is the FIRST positional argument
            var arguments = $"assets download \"{localPath}\" -d {databaseId} -a {assetId} --file-key \"{fileKey}\" --json-output";

            var output = await ExecuteCommandAsync("vamscli", arguments);
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Download output: {output}");

            // The CLI behavior:
            // - If localPath is a directory: downloads file with original name into that directory
            // - If localPath is a file path: creates a folder with that name and downloads file inside
            
            string actualFilePath;
            
            if (isDirectory)
            {
                // File should be directly in the directory with its original name
                actualFilePath = Path.Combine(targetDirectory, expectedFileName);
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Expected file at: {actualFilePath}");
            }
            else
            {
                // Check if localPath became a directory (happens when passing full file path)
                if (Directory.Exists(localPath))
                {
                    System.Diagnostics.Debug.WriteLine($"VamsCliService: Download created a directory at {localPath}, looking for file inside");
                    
                    // Get the filename from the fileKey
                    var fileName = Path.GetFileName(fileKey);
                    actualFilePath = Path.Combine(localPath, fileName);
                    
                    System.Diagnostics.Debug.WriteLine($"VamsCliService: Expected file path: {actualFilePath}");
                    
                    if (File.Exists(actualFilePath))
                    {
                        // Move the file from the subdirectory to the intended location
                        var targetPath = localPath + "_temp";
                        File.Move(actualFilePath, targetPath);
                        
                        // Delete the directory
                        Directory.Delete(localPath, true);
                        
                        // Rename the temp file to the original intended name
                        File.Move(targetPath, localPath);
                        
                        System.Diagnostics.Debug.WriteLine($"VamsCliService: File moved from {actualFilePath} to {localPath}");
                        actualFilePath = localPath;
                    }
                }
                else
                {
                    actualFilePath = localPath;
                }
            }
            
            // Check if file was created successfully
            if (File.Exists(actualFilePath))
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: File downloaded successfully to {actualFilePath}");
                return true;
            }

            throw new InvalidOperationException($"Download failed: File not created at expected location: {actualFilePath}");
        }

        private bool TryParseError(string output, out string errorMessage, out string errorType)
        {
            errorMessage = null;
            errorType = null;

            if (string.IsNullOrWhiteSpace(output)) return false;

            try
            {
                var errorResponse = JsonSerializer.Deserialize<VamsErrorResponse>(output, _jsonOptions);
                if (errorResponse != null && !string.IsNullOrEmpty(errorResponse.Error))
                {
                    errorMessage = errorResponse.Error;
                    errorType = errorResponse.ErrorType ?? "Error";
                    return true;
                }
            }
            catch
            {
                // Not JSON or doesn't match error structure
            }

            return false;
        }

        private async Task<string> ExecuteCommandAsync(string command, string arguments)
        {
            System.Diagnostics.Debug.WriteLine($"VamsCliService: Executing command: {command} {arguments}");

            try
            {
                var processStartInfo = new ProcessStartInfo
                {
                    FileName = command,
                    Arguments = arguments,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };

                using var process = new Process { StartInfo = processStartInfo };
                process.Start();

                var output = await process.StandardOutput.ReadToEndAsync();
                var error = await process.StandardError.ReadToEndAsync();

                await process.WaitForExitAsync();

                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command exit code: {process.ExitCode}");
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command output: {output}");
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command error: {error}");

                if (process.ExitCode != 0)
                {
                    // Try to parse as error JSON first
                    if (TryParseError(output, out var errorMsg, out var errorType))
                    {
                        throw new InvalidOperationException($"{errorType}: {errorMsg}");
                    }
                    
                    // Fall back to stderr
                    throw new InvalidOperationException($"Command failed with exit code {process.ExitCode}: {error}");
                }

                // Also check successful responses for error fields
                if (TryParseError(output, out var successErrorMsg, out var successErrorType))
                {
                    throw new InvalidOperationException($"{successErrorType}: {successErrorMsg}");
                }

                return output;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Exception executing command: {ex}");
                throw new InvalidOperationException($"Error executing command '{command} {arguments}': {ex.Message}", ex);
            }
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
                    // No specific resources to dispose for this service
                }
                _disposed = true;
            }
        }

        ~VamsCliService()
        {
            Dispose(false);
        }
    }
}
