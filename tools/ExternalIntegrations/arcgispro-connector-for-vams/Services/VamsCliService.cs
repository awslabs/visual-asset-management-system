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
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using VamsDatabaseExplorer.Models;

namespace VamsDatabaseExplorer.Services
{
    /// <summary>Thrown when the vamscli executable cannot be launched: not installed, or not on PATH.</summary>
    public class VamsCliNotFoundException : InvalidOperationException
    {
        public VamsCliNotFoundException(string message, Exception inner = null)
            : base(message, inner) { }
    }

    /// <summary>Thrown when vamscli reports that the named profile does not exist.</summary>
    public class VamsProfileNotFoundException : InvalidOperationException
    {
        public VamsProfileNotFoundException(string profileName, string message, Exception inner = null)
            : base(message, inner)
        {
            ProfileName = profileName;
        }

        public string ProfileName { get; }
    }

    /// <summary>
    /// Thrown when a file-transfer command reports failed files. A transfer command writes its full
    /// report to stdout and THEN exits non-zero when <c>overall_success</c> is false, so a partial
    /// transfer stays distinguishable from a total one. <see cref="Report"/> carries that report.
    /// </summary>
    public class VamsTransferException : InvalidOperationException
    {
        public VamsTransferException(string message, AssetDownloadResponse report)
            : base(message)
        {
            Report = report;
        }

        public AssetDownloadResponse Report { get; }
    }

    public class VamsCliService : IDisposable
    {
        /// <summary>The vamscli profile used when none is configured.</summary>
        public const string DefaultProfileName = "default";

        /// <summary>Environment variable naming the vamscli profile this connector runs against.</summary>
        public const string ProfileEnvironmentVariable = "VAMS_CLI_PROFILE";

        /// <summary>The <c>error_type</c> vamscli reports for a profile that does not exist. In JSON
        /// mode the field carries the CLI exception's class name.</summary>
        private const string ProfileNotFoundErrorType = "ProfileNotFoundError";

        /// <summary>How many failed file keys a transfer-failure message names before summarizing.</summary>
        private const int MaxNamedTransferFailures = 5;

        /// <summary>
        /// Bound on a metadata, auth or listing command. Every wait needs one: a child that fills its
        /// redirected stderr buffer never exits, and without a bound the ArcGIS Pro operation hangs
        /// until the add-in is unloaded.
        /// </summary>
        private static readonly TimeSpan CommandTimeout = TimeSpan.FromMinutes(10);

        /// <summary>
        /// Bound on a download. Far longer than <see cref="CommandTimeout"/>, because a recursive
        /// download of a real asset moves thousands of files and a bound that cut it short would
        /// break working transfers.
        /// </summary>
        private static readonly TimeSpan TransferCommandTimeout = TimeSpan.FromHours(4);

        private readonly JsonSerializerOptions _jsonOptions;
        private readonly string _profileName;
        private bool _disposed = false;

        // Cached credentials for automatic re-authentication
        private static string _cachedUsername;
        private static string _cachedPassword;

        // Cached web deployed URL from auth status
        private static string _webDeployedUrl;

        // Cached auth type (once per session)
        private static string _cachedAuthType;
        private static bool _profileInfoFetched = false;

        public VamsCliService() : this(null)
        {
        }

        /// <param name="profileName">
        /// The vamscli profile every command runs against. When null or blank it is read from
        /// <see cref="ProfileEnvironmentVariable"/>, and falls back to <see cref="DefaultProfileName"/>.
        /// </param>
        public VamsCliService(string profileName)
        {
            _profileName = ResolveProfileName(profileName);

            _jsonOptions = new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true,
                DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
            };
        }

        private static string ResolveProfileName(string profileName)
        {
            if (!string.IsNullOrWhiteSpace(profileName))
            {
                return profileName.Trim();
            }

            var configured = Environment.GetEnvironmentVariable(ProfileEnvironmentVariable);
            return string.IsNullOrWhiteSpace(configured) ? DefaultProfileName : configured.Trim();
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
                // --password-stdin keeps the password out of the argument vector, which the OS
                // process table exposes to every other local account for the lifetime of the login.
                var arguments = new List<string> { "auth", "login", "--json-output", "-u", username };
                string stdinPayload = null;
                if (!string.IsNullOrEmpty(passwordOrToken))
                {
                    arguments.Add("--password-stdin");
                    stdinPayload = passwordOrToken;
                }

                output = await ExecuteCommandAsync("vamscli", arguments, stdinPayload);
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
                // Use auth login with token override for external auth (JWT token or VAMS API key).
                // The token goes to stdin for the same reason the password does.
                var arguments = new List<string>
                {
                    "auth", "login", "--user-id", username,
                    "--token-override-stdin", "--json-output"
                };

                output = await ExecuteCommandAsync(
                    "vamscli", arguments, passwordOrToken ?? string.Empty);
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
                var output = await ExecuteCommandAsync(
                    "vamscli", new List<string> { "auth", "status", "--json-output" });
                
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

            System.Diagnostics.Debug.WriteLine(
                $"VamsCliService: Fetching profile info for auth type (profile '{_profileName}')");

            ProfileInfoResponse profileInfo;
            try
            {
                var output = await ExecuteCommandAsync(
                    "vamscli", new List<string> { "profile", "info", _profileName, "--json-output" });
                profileInfo = JsonSerializer.Deserialize<ProfileInfoResponse>(output, _jsonOptions);
            }
            catch (VamsCliNotFoundException)
            {
                // Already names the install step. Restating it as a profile problem would send the
                // user to a command their machine does not have.
                throw;
            }
            catch (VamsProfileNotFoundException ex)
            {
                // A missing profile is reported as its own failure, so it is distinguishable from a
                // profile that exists but cannot be read.
                throw new InvalidOperationException(
                    $"The vamscli profile '{_profileName}' does not exist.\n\n" +
                    $"Create it with:\n  vamscli setup <api-gateway-url> --profile {_profileName}\n\n" +
                    $"Or set the {ProfileEnvironmentVariable} environment variable to a profile that " +
                    "does exist. Run 'vamscli profile list' to see which are configured.", ex);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Error fetching profile info: {ex.Message}");
                throw new InvalidOperationException(
                    $"Failed to read the vamscli profile '{_profileName}'.\n\n{ex.Message}", ex);
            }

            if (profileInfo?.ProfileInfo == null)
            {
                throw new InvalidOperationException(
                    $"The vamscli profile '{_profileName}' is not fully set up.\n\n" +
                    "Please run 'vamscli setup <api-gateway-url>' in a terminal to configure it " +
                    "before using this tool.");
            }

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
            await ExecuteCommandAsync("vamscli", new List<string> { "auth", "logout" });
        }

        public async Task<List<Database>> GetAllDatabasesAsync()
        {
            System.Diagnostics.Debug.WriteLine("VamsCliService: Starting GetAllDatabasesAsync");

            try
            {
                // Ensure authenticated before executing command
                await EnsureAuthenticatedAsync();
                
                // Add --json-output flag for structured JSON response
                var output = await ExecuteCommandAsync(
                    "vamscli",
                    new List<string> { "database", "list", "--auto-paginate", "--json-output" });

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
            var output = await ExecuteCommandAsync("vamscli", new List<string>
            {
                "assets", "list", "--database-id", databaseId, "--auto-paginate", "--json-output"
            });

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
            var output = await ExecuteCommandAsync("vamscli", new List<string>
            {
                "file", "list", "-d", databaseId, "-a", assetId,
                "--basic", "--auto-paginate", "--json-output"
            });

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
            var output = await ExecuteCommandAsync("vamscli", new List<string>
            {
                "file", "info", "-d", databaseId, "-a", assetId, "-p", filePath, "--json-output"
            });

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
            var arguments = new List<string>
            {
                "assets", "download", downloadPath, "-d", databaseId, "-a", assetId,
                "--file-key", "/", "--recursive", "--json-output"
            };

            // allowTransferReport: a recursive download exits non-zero as soon as any one file
            // fails, but its report survives on stdout. Returning it lets the caller show "900 of
            // 1000 downloaded" and name the failures, which the summary window already does;
            // treating the exit code as fatal would report a mostly-successful download as a total
            // failure with an empty message.
            var output = await ExecuteCommandAsync(
                "vamscli", arguments, timeout: TransferCommandTimeout, allowTransferReport: true);
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
            var arguments = new List<string>
            {
                "assets", "download", localPath, "-d", databaseId, "-a", assetId,
                "--file-key", fileKey, "--json-output"
            };

            // A single-file download has no partial outcome to report, so a failure is left to throw
            // as VamsTransferException — whose message names the file and the reported reason.
            var output = await ExecuteCommandAsync(
                "vamscli", arguments, timeout: TransferCommandTimeout);
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

        /// <summary>
        /// Run a vamscli command. Arguments are supplied as individual tokens and passed through
        /// ProcessStartInfo.ArgumentList, which quotes each one, so a value containing a space, a
        /// double-quote or a leading dash reaches the CLI intact. Only the subcommand path (the
        /// leading tokens before the first option) is traced; the remaining tokens can carry a
        /// password or override token, so they are never written to the log or an exception message.
        /// <para>
        /// The configured profile is prepended here rather than at each call site: --profile is a
        /// group-level option, so it has to precede the subcommand, and an omitted flag resolves to
        /// whatever profile `vamscli profile switch` last recorded.
        /// </para>
        /// <para>
        /// <paramref name="stdinPayload"/> carries a credential to the CLI's stdin. It is never an
        /// argument, because the OS process table exposes arguments to every other local account.
        /// </para>
        /// <para>
        /// <paramref name="allowTransferReport"/> returns the output of a transfer command that
        /// exited non-zero but still reported which files succeeded, instead of throwing.
        /// </para>
        /// </summary>
        private async Task<string> ExecuteCommandAsync(
            string command, IReadOnlyList<string> arguments, string stdinPayload = null,
            TimeSpan? timeout = null, bool allowTransferReport = false)
        {
            var commandLabel = DescribeCommand(command, arguments);
            var commandTimeout = timeout ?? CommandTimeout;
            System.Diagnostics.Debug.WriteLine(
                $"VamsCliService: Executing command: {commandLabel} (profile '{_profileName}')");

            try
            {
                var processStartInfo = new ProcessStartInfo
                {
                    FileName = command,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    RedirectStandardInput = stdinPayload != null,
                    UseShellExecute = false,
                    CreateNoWindow = true
                };

                if (stdinPayload != null)
                {
                    // The CLI decodes a piped credential as UTF-8; the console code page the
                    // default writer would use is not it.
                    processStartInfo.StandardInputEncoding = new UTF8Encoding(false);
                }

                processStartInfo.ArgumentList.Add("--profile");
                processStartInfo.ArgumentList.Add(_profileName);

                foreach (var argument in arguments)
                {
                    processStartInfo.ArgumentList.Add(argument);
                }

                using var process = new Process { StartInfo = processStartInfo };

                try
                {
                    process.Start();
                }
                catch (System.ComponentModel.Win32Exception ex)
                {
                    // FileName is resolved through PATH, so a missing executable surfaces here and
                    // nowhere else. It is the most common first-run failure, and pointing the user
                    // at 'vamscli setup' would name a command they do not have.
                    throw new VamsCliNotFoundException(
                        $"The VAMS CLI ('{command}') was not found on PATH.\n\n" +
                        "Install it with:\n  pip install vamscli\n\n" +
                        "Then restart ArcGIS Pro so it picks up the updated PATH.", ex);
                }

                if (stdinPayload != null)
                {
                    // Written and closed before the output is read: the CLI blocks on stdin, and
                    // reading stdout first would deadlock.
                    await process.StandardInput.WriteLineAsync(stdinPayload);
                    process.StandardInput.Close();
                }

                // Both pipes are drained CONCURRENTLY. Reading stdout to completion first deadlocks
                // as soon as the child fills the redirected stderr buffer — a long traceback or an
                // SSL warning burst is enough: the child blocks writing stderr, so it never exits
                // and never closes stdout, and the awaited stdout read never returns.
                var outputTask = process.StandardOutput.ReadToEndAsync();
                var errorTask = process.StandardError.ReadToEndAsync();

                string output = null;
                string error = null;
                using (var expiry = new CancellationTokenSource(commandTimeout))
                {
                    try
                    {
                        await process.WaitForExitAsync(expiry.Token);
                        output = await outputTask;
                        error = await errorTask;
                    }
                    catch (OperationCanceledException)
                    {
                        // Killing the child closes both pipes, which completes the two reads.
                        KillQuietly(process);
                        ObserveQuietly(outputTask);
                        ObserveQuietly(errorTask);
                        throw new InvalidOperationException(
                            $"'{commandLabel}' did not finish within " +
                            $"{commandTimeout.TotalMinutes:F0} minute(s) and was cancelled.");
                    }
                }

                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command exit code: {process.ExitCode}");
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command output: {output}");
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Command error: {error}");

                if (process.ExitCode != 0)
                {
                    // Try to parse as error JSON first
                    if (TryParseError(output, out var errorMsg, out var errorType))
                    {
                        if (string.Equals(errorType, ProfileNotFoundErrorType, StringComparison.Ordinal))
                        {
                            throw new VamsProfileNotFoundException(_profileName, errorMsg);
                        }
                        throw new InvalidOperationException($"{errorType}: {errorMsg}");
                    }

                    // A transfer command writes its report to stdout and THEN exits non-zero when
                    // overall_success is false, so the report survives and still names which files
                    // failed. Under --json-output stderr is empty, so falling straight through to it
                    // would produce a message carrying nothing at all.
                    if (TryParseTransferReport(output, out var report))
                    {
                        if (allowTransferReport)
                        {
                            return output;
                        }
                        throw new VamsTransferException(DescribeTransferFailure(report), report);
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
            catch (VamsCliNotFoundException)
            {
                // These three already describe the failure and the action that fixes it. Wrapping
                // them in "Error executing command ..." would bury the guidance the caller keys off.
                throw;
            }
            catch (VamsProfileNotFoundException)
            {
                throw;
            }
            catch (VamsTransferException)
            {
                throw;
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"VamsCliService: Exception executing command: {ex}");
                throw new InvalidOperationException($"Error executing command '{commandLabel}': {ex.Message}", ex);
            }
        }

        /// <summary>Kill a process that overran its timeout, reporting rather than raising a failure to do so.</summary>
        private static void KillQuietly(Process process)
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine(
                    $"VamsCliService: could not kill the timed-out process: {ex.Message}");
            }
        }

        /// <summary>Attach a no-op continuation so an abandoned pipe read is never an unobserved exception.</summary>
        private static void ObserveQuietly(Task task)
        {
            _ = task.ContinueWith(
                completed => { _ = completed.Exception; },
                CancellationToken.None,
                TaskContinuationOptions.ExecuteSynchronously,
                TaskScheduler.Default);
        }

        /// <summary>
        /// The transfer report on the stdout of a command that exited non-zero, or false when stdout
        /// carries none. Keyed on <c>overall_success</c> being present and exactly false: presence
        /// keeps every other response shape out of this path, and testing for false rather than
        /// falsiness keeps a report with no such field from being read as a failure.
        /// </summary>
        private bool TryParseTransferReport(string output, out AssetDownloadResponse report)
        {
            report = null;

            if (string.IsNullOrWhiteSpace(output)) return false;

            try
            {
                using var document = JsonDocument.Parse(output);
                if (document.RootElement.ValueKind != JsonValueKind.Object) return false;
                if (!document.RootElement.TryGetProperty("overall_success", out var flag)) return false;
                if (flag.ValueKind != JsonValueKind.False) return false;

                report = JsonSerializer.Deserialize<AssetDownloadResponse>(output, _jsonOptions);
            }
            catch (JsonException)
            {
                return false;
            }

            return report != null;
        }

        /// <summary>A one-line summary of a transfer report, distinguishing a partial from a total failure.</summary>
        private static string DescribeTransferFailure(AssetDownloadResponse report)
        {
            var failedKeys = (report.FailedDownloads ?? new List<FailedDownload>())
                .Select(failed => string.IsNullOrEmpty(failed.RelativeKey)
                    ? failed.LocalPath
                    : failed.RelativeKey)
                .Where(key => !string.IsNullOrEmpty(key))
                .ToList();

            var summary = report.SuccessfulFiles > 0 && report.SuccessfulFiles < report.TotalFiles
                ? $"Transfer partially failed: {report.SuccessfulFiles} of {report.TotalFiles} " +
                  $"file(s) succeeded, {report.FailedFiles} failed"
                : $"Transfer failed: {report.FailedFiles} of {report.TotalFiles} file(s) failed";

            if (failedKeys.Count > 0)
            {
                var named = string.Join(", ", failedKeys.Take(MaxNamedTransferFailures));
                if (failedKeys.Count > MaxNamedTransferFailures)
                {
                    named += $", ... (+{failedKeys.Count - MaxNamedTransferFailures} more)";
                }
                summary = $"{summary} ({named})";
            }

            return summary;
        }

        /// <summary>
        /// The executable plus its leading non-option tokens (e.g. "vamscli auth login"), for tracing.
        /// Stops at the first token starting with '-' so no option value is included.
        /// </summary>
        private static string DescribeCommand(string command, IReadOnlyList<string> arguments)
        {
            var parts = new List<string> { command };
            foreach (var argument in arguments)
            {
                if (argument.StartsWith("-", StringComparison.Ordinal))
                {
                    break;
                }
                parts.Add(argument);
            }
            return string.Join(" ", parts);
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
