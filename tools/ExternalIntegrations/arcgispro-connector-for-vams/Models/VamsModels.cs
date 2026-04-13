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
using System.Text.Json.Serialization;

namespace VamsDatabaseExplorer.Models
{
    // Response wrapper for database list
    public class DatabaseListResponse
    {
        [JsonPropertyName("Items")]
        public List<Database> Items { get; set; } = new List<Database>();

        [JsonPropertyName("NextToken")]
        public string NextToken { get; set; } = string.Empty;
    }

    public class Database
    {
        [JsonPropertyName("databaseId")]
        public string DatabaseId { get; set; } = string.Empty;

        [JsonPropertyName("description")]
        public string Description { get; set; } = string.Empty;

        [JsonPropertyName("dateCreated")]
        public string DateCreated { get; set; } = string.Empty;

        [JsonPropertyName("assetCount")]
        public int AssetCount { get; set; }

        [JsonPropertyName("defaultBucketId")]
        public string DefaultBucketId { get; set; } = string.Empty;

        [JsonPropertyName("bucketName")]
        public string BucketName { get; set; } = string.Empty;

        [JsonPropertyName("baseAssetsPrefix")]
        public string BaseAssetsPrefix { get; set; } = string.Empty;

        // Compatibility properties for UI
        public string DatabaseName => DatabaseId; // Fallback to ID

        public DateTime CreatedAt
        {
            get
            {
                if (DateTime.TryParse(DateCreated, out var date))
                    return date;
                return DateTime.MinValue;
            }
        }
    }

    // Response wrapper for asset list
    public class AssetListResponse
    {
        [JsonPropertyName("Items")]
        public List<Asset> Items { get; set; } = new List<Asset>();

        [JsonPropertyName("NextToken")]
        public string NextToken { get; set; } = string.Empty;
    }

    public class Asset
    {
        [JsonPropertyName("assetId")]
        public string AssetId { get; set; } = string.Empty;

        [JsonPropertyName("databaseId")]
        public string DatabaseId { get; set; } = string.Empty;

        [JsonPropertyName("assetName")]
        public string AssetName { get; set; } = string.Empty;

        [JsonPropertyName("description")]
        public string Description { get; set; } = string.Empty;

        [JsonPropertyName("isDistributable")]
        public bool IsDistributable { get; set; }

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("tags")]
        public List<string> Tags { get; set; } = new List<string>();

        [JsonPropertyName("currentVersion")]
        public CurrentVersion CurrentVersion { get; set; } = new CurrentVersion();

        [JsonPropertyName("assetLocation")]
        public AssetLocation AssetLocation { get; set; } = new AssetLocation();

        [JsonPropertyName("bucketName")]
        public string BucketName { get; set; } = string.Empty;

        [JsonPropertyName("assetType")]
        public string AssetType { get; set; } = string.Empty;

        // Compatibility properties for UI
        public string DatabaseName => DatabaseId; // Fallback to ID
        public int FileCount => 0; // Not provided by VAMS, this is a calculated field for the stats
        public long TotalSize => 0; // Not provided by VAMS, this is a calculated field for the stats
        public DateTime CreatedAt => CurrentVersion?.DateModifiedDateTime ?? DateTime.MinValue;
        public string CreatedBy => CurrentVersion?.CreatedBy ?? "Unknown";
    }

    public class CurrentVersion
    {
        [JsonPropertyName("Version")]
        public string Version { get; set; } = string.Empty;

        [JsonPropertyName("DateModified")]
        public string DateModified { get; set; } = string.Empty;

        [JsonPropertyName("Comment")]
        public string Comment { get; set; } = string.Empty;

        [JsonPropertyName("createdBy")]
        public string CreatedBy { get; set; } = string.Empty;

        public DateTime DateModifiedDateTime
        {
            get
            {
                if (DateTime.TryParse(DateModified, out var date))
                    return date;
                return DateTime.MinValue;
            }
        }
    }

    public class AssetLocation
    {
        [JsonPropertyName("Bucket")]
        public string Bucket { get; set; } = string.Empty;

        [JsonPropertyName("Key")]
        public string Key { get; set; } = string.Empty;
    }

    // Response wrapper for file list
    public class FileListResponse
    {
        [JsonPropertyName("items")]
        public List<AssetFile> Items { get; set; } = new List<AssetFile>();

        [JsonPropertyName("NextToken")]
        public string NextToken { get; set; } = string.Empty;
    }

    // Keep for backward compatibility with existing code
    public class AssetFileResponse
    {
        public List<AssetFile> Files { get; set; } = new List<AssetFile>();
    }

    public class AssetFile
    {
        [JsonPropertyName("fileName")]
        public string FileName { get; set; } = string.Empty;

        [JsonPropertyName("relativePath")]
        public string RelativePath { get; set; } = string.Empty;

        [JsonPropertyName("size")]
        public long? Size { get; set; }

        [JsonPropertyName("isFolder")]
        public bool IsFolder { get; set; }

        [JsonPropertyName("isArchived")]
        public bool IsArchived { get; set; }

        [JsonPropertyName("primaryType")]
        public string PrimaryType { get; set; } = string.Empty;

        [JsonPropertyName("contentType")]
        public string ContentType { get; set; } = string.Empty;

        [JsonPropertyName("lastModified")]
        public string LastModified { get; set; } = string.Empty;

        [JsonPropertyName("storageClass")]
        public string StorageClass { get; set; } = string.Empty;

        [JsonPropertyName("previewFile")]
        public string PreviewFile { get; set; } = string.Empty;

        // Compatibility properties for existing code
        public string Path => RelativePath;
        public string Key => RelativePath; // Simplified
        public string Type => PrimaryType ?? "unknown";
        public string State => IsArchived ? "archived" : "available";
        public DateTime AddedAt
        {
            get
            {
                if (DateTime.TryParse(LastModified, out var date))
                    return date;
                return DateTime.MinValue;
            }
        }
        public DateTime LastModifiedDateTime
        {
            get
            {
                if (DateTime.TryParse(LastModified, out var date))
                    return date;
                return DateTime.MinValue;
            }
        }
    }

    // Auth status response model
    public class AuthStatusResponse
    {
        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("authenticated")]
        public bool Authenticated { get; set; }

        [JsonPropertyName("authentication_type")]
        public string AuthenticationType { get; set; } = string.Empty;

        [JsonPropertyName("user_id")]
        public string UserId { get; set; } = string.Empty;

        [JsonPropertyName("web_deployed_url")]
        public string WebDeployedUrl { get; set; } = string.Empty;

        [JsonPropertyName("expires_at")]
        public string ExpiresAt { get; set; } = string.Empty;

        [JsonPropertyName("is_expired")]
        public bool IsExpired { get; set; }
        
        [JsonPropertyName("error")]
        public string Error { get; set; } = string.Empty;
        
        [JsonPropertyName("error_type")]
        public string ErrorType { get; set; } = string.Empty;
    }

    // Profile info response model
    public class ProfileInfoResponse
    {
        [JsonPropertyName("profile_name")]
        public string ProfileName { get; set; } = string.Empty;

        [JsonPropertyName("profile_info")]
        public ProfileInfo ProfileInfo { get; set; }
        
        [JsonPropertyName("error")]
        public string Error { get; set; } = string.Empty;
        
        [JsonPropertyName("error_type")]
        public string ErrorType { get; set; } = string.Empty;
    }

    public class ProfileInfo
    {
        [JsonPropertyName("auth_type")]
        public string AuthType { get; set; } = string.Empty;

        [JsonPropertyName("web_deployed_url")]
        public string WebDeployedUrl { get; set; } = string.Empty;

        [JsonPropertyName("api_gateway_url")]
        public string ApiGatewayUrl { get; set; } = string.Empty;
    }

    // Generic error response model
    public class VamsErrorResponse
    {
        [JsonPropertyName("error")]
        public string Error { get; set; } = string.Empty;

        [JsonPropertyName("error_type")]
        public string ErrorType { get; set; } = string.Empty;

        [JsonPropertyName("success")]
        public bool Success { get; set; }
    }

    // VamsFileCredentials class removed - no longer needed with new download mechanism

    // Download response models
    public class AssetDownloadResponse
    {
        [JsonPropertyName("overall_success")]
        public bool OverallSuccess { get; set; }

        [JsonPropertyName("total_files")]
        public int TotalFiles { get; set; }

        [JsonPropertyName("successful_files")]
        public int SuccessfulFiles { get; set; }

        [JsonPropertyName("failed_files")]
        public int FailedFiles { get; set; }

        [JsonPropertyName("total_size")]
        public long TotalSize { get; set; }

        [JsonPropertyName("total_size_formatted")]
        public string TotalSizeFormatted { get; set; } = string.Empty;

        [JsonPropertyName("download_duration")]
        public double DownloadDuration { get; set; }

        [JsonPropertyName("average_speed")]
        public double AverageSpeed { get; set; }

        [JsonPropertyName("average_speed_formatted")]
        public string AverageSpeedFormatted { get; set; } = string.Empty;

        [JsonPropertyName("successful_downloads")]
        public List<DownloadedFile> SuccessfulDownloads { get; set; } = new List<DownloadedFile>();

        [JsonPropertyName("failed_downloads")]
        public List<FailedDownload> FailedDownloads { get; set; } = new List<FailedDownload>();

        [JsonPropertyName("verified_files")]
        public int VerifiedFiles { get; set; }

        [JsonPropertyName("verification_failures")]
        public List<object> VerificationFailures { get; set; } = new List<object>();
    }

    public class DownloadedFile
    {
        [JsonPropertyName("relative_key")]
        public string RelativeKey { get; set; } = string.Empty;

        [JsonPropertyName("local_path")]
        public string LocalPath { get; set; } = string.Empty;

        [JsonPropertyName("size")]
        public long Size { get; set; }
    }

    public class FailedDownload
    {
        [JsonPropertyName("relative_key")]
        public string RelativeKey { get; set; } = string.Empty;

        [JsonPropertyName("local_path")]
        public string LocalPath { get; set; } = string.Empty;

        [JsonPropertyName("error")]
        public string Error { get; set; } = string.Empty;
    }
}
