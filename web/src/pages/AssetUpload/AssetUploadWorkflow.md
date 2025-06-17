## Complete Workflow Scenarios Matrix:

### __Asset Creation Modes:__

1. __New Asset Creation__ (`isExistingAsset = false`)
   - Needs: Asset Creation → Metadata → File Uploads
2. __Existing Asset Modification__ (`isExistingAsset = true`)
   - Skips: Asset Creation & Metadata, goes directly to File Uploads

### __File Upload Combinations:__

1. __Asset Files Only__ (fileItems.length > 0, no Preview)
2. __Preview File Only__ (fileItems.length = 0, has Preview)
3. __Both Asset Files + Preview__ (fileItems.length > 0, has Preview)
4. __No Files__ (fileItems.length = 0, no Preview) - Empty asset creation

## Comprehensive Race Condition Solution:

### __1. Enhanced Upload State Tracking__

```typescript
interface UploadState {
  // Existing states...
  finalCompletionTriggered: boolean;
  activeUploadTypes: ('assetFiles' | 'preview')[];
  completedUploadTypes: ('assetFiles' | 'preview')[];
}
```

### __2. Dynamic Upload Type Detection__

```typescript
const getActiveUploadTypes = useCallback(() => {
  const types: ('assetFiles' | 'preview')[] = [];
  if (fileItems.length > 0) types.push('assetFiles');
  if (assetDetail.Preview) types.push('preview');
  return types;
}, [fileItems.length, assetDetail.Preview]);
```

### __3. Centralized Completion Logic__

```typescript
const checkAndTriggerFinalCompletion = useCallback(() => {
  // Don't trigger if already triggered
  if (uploadState.finalCompletionTriggered) return;
  
  const activeTypes = getActiveUploadTypes();
  
  // If no files at all, complete immediately after asset/metadata steps
  if (activeTypes.length === 0) {
    const prerequisitesComplete = isExistingAsset || 
      (uploadState.assetCreationStatus === 'completed' && uploadState.metadataStatus === 'completed');
    
    if (prerequisitesComplete) {
      triggerFinalCompletion('Asset created successfully without files');
      return;
    }
  }
  
  // Check if all active upload types are complete
  const assetFilesComplete = !activeTypes.includes('assetFiles') || 
    uploadState.completionStatus === 'completed';
  const previewComplete = !activeTypes.includes('preview') || 
    uploadState.previewCompletionStatus === 'completed';
  
  if (assetFilesComplete && previewComplete) {
    // Determine completion message based on what was uploaded
    let message = 'Upload completed successfully';
    if (activeTypes.includes('assetFiles') && activeTypes.includes('preview')) {
      message = 'Asset files and preview uploaded successfully';
    } else if (activeTypes.includes('assetFiles')) {
      message = 'Asset files uploaded successfully';
    } else if (activeTypes.includes('preview')) {
      message = 'Preview file uploaded successfully';
    }
    
    triggerFinalCompletion(message);
  }
}, [uploadState, isExistingAsset, getActiveUploadTypes]);
```

### __4. Safe Completion Trigger__

```typescript
const triggerFinalCompletion = useCallback((message: string) => {
  setUploadState(prev => ({ ...prev, finalCompletionTriggered: true }));
  
  const finalResponse: CompleteUploadResponse = {
    assetId: uploadState.createdAssetId || assetDetail.assetId || '',
    message,
    uploadId: uploadState.uploadId || uploadState.previewUploadId || 'no-upload-required',
    fileResults: [],
    overallSuccess: true
  };
  
  onUploadComplete(finalResponse);
}, [uploadState, assetDetail, onUploadComplete]);
```

### __5. Remove All Individual Completion Calls__

- Remove `onUploadComplete` call from `completePreviewUpload`
- Remove multiple `completeUpload` calls from asset file upload logic
- Replace with single monitoring useEffect

### __6. Single Monitoring Effect__

```typescript
useEffect(() => {
  checkAndTriggerFinalCompletion();
}, [
  uploadState.assetCreationStatus,
  uploadState.metadataStatus, 
  uploadState.completionStatus,
  uploadState.previewCompletionStatus,
  checkAndTriggerFinalCompletion
]);
```

## __Workflow State Machine:__

```javascript
New Asset:     Create Asset → Add Metadata → Upload Files → Complete
Existing Asset:                              Upload Files → Complete

Upload Files can be:
- Asset Files Only: Initialize → Upload → Complete Asset Files → Final Complete
- Preview Only: Initialize → Upload → Complete Preview → Final Complete  
- Both: Initialize Both → Upload Both → Complete Both → Final Complete
- Neither: Skip All Uploads → Final Complete (after prerequisites)
```

## __Error Handling Scenarios:__

- If asset files fail but preview succeeds → Show partial success
- If preview fails but asset files succeed → Show partial success
- If both fail → Show failure with retry options
- If prerequisites fail → Don't attempt file uploads

This approach ensures: ✅ __No race conditions__ - Single completion trigger point ✅ __All scenarios handled__ - New/existing assets, all file combinations ✅ __Proper sequencing__ - Prerequisites before uploads ✅ __Error resilience__ - Partial success handling ✅ __State consistency__ - No duplicate completion calls

## __Enhanced Failure Handling & Recovery Options:__

### __1. Granular Failure Tracking__

```typescript
interface FilePart {
  // Existing properties...
  status: 'pending' | 'in-progress' | 'completed' | 'failed' | 'skipped';
  retryCount: number;
  maxRetries: number; // e.g., 3
  skipRequested: boolean;
}

interface FileUploadState {
  // Existing properties...
  hasFailedParts: boolean;
  hasSkippedParts: boolean;
  partialUploadAllowed: boolean;
}
```

### __2. Enhanced Completion Logic with Partial Success__

```typescript
const checkAndTriggerFinalCompletion = useCallback(() => {
  if (uploadState.finalCompletionTriggered) return;
  
  const activeTypes = getActiveUploadTypes();
  
  // Handle no files scenario
  if (activeTypes.length === 0) {
    const prerequisitesComplete = isExistingAsset || 
      (uploadState.assetCreationStatus === 'completed' && uploadState.metadataStatus === 'completed');
    
    if (prerequisitesComplete) {
      triggerFinalCompletion('Asset created successfully without files', true);
      return;
    }
  }
  
  // Check completion status for each upload type
  const assetFilesStatus = getAssetFilesCompletionStatus();
  const previewStatus = getPreviewCompletionStatus();
  
  const canComplete = 
    (!activeTypes.includes('assetFiles') || assetFilesStatus.canComplete) &&
    (!activeTypes.includes('preview') || previewStatus.canComplete);
  
  if (canComplete) {
    const message = buildCompletionMessage(assetFilesStatus, previewStatus);
    const isFullSuccess = assetFilesStatus.isFullSuccess && previewStatus.isFullSuccess;
    triggerFinalCompletion(message, isFullSuccess);
  }
}, [uploadState, isExistingAsset, getActiveUploadTypes]);

const getAssetFilesCompletionStatus = () => {
  if (uploadState.uploadStatus === 'completed') {
    return { canComplete: true, isFullSuccess: true, hasFailures: false };
  }
  if (uploadState.uploadStatus === 'skipped') {
    return { canComplete: true, isFullSuccess: true, hasFailures: false };
  }
  if (uploadState.uploadStatus === 'failed') {
    // Check if user chose to skip failed parts
    const hasSkippedParts = fileParts.some(part => part.skipRequested);
    const hasCompletedParts = fileParts.some(part => part.status === 'completed');
    
    return { 
      canComplete: hasSkippedParts || hasCompletedParts, 
      isFullSuccess: false, 
      hasFailures: true 
    };
  }
  return { canComplete: false, isFullSuccess: false, hasFailures: false };
};
```

### __3. Retry Logic Enhancement__

```typescript
const retryFailedParts = useCallback(async (fileIndex?: number) => {
  // Reset specific file parts or all failed parts
  setFileParts(prev => 
    prev.map(part => {
      if (part.status === 'failed' && 
          (fileIndex === undefined || part.fileIndex === fileIndex) &&
          part.retryCount < part.maxRetries) {
        return { 
          ...part, 
          status: 'pending', 
          retryCount: part.retryCount + 1,
          skipRequested: false 
        };
      }
      return part;
    })
  );
  
  // Reset upload started flag to allow retry
  setUploadStarted(false);
  
  // Restart upload process
  await uploadFileParts();
}, [uploadFileParts]);

const skipFailedParts = useCallback((fileIndex?: number) => {
  setFileParts(prev => 
    prev.map(part => {
      if (part.status === 'failed' && 
          (fileIndex === undefined || part.fileIndex === fileIndex)) {
        return { ...part, status: 'skipped', skipRequested: true };
      }
      return part;
    })
  );
  
  // Update file status to reflect skipped parts
  setFileUploadItems(prev => 
    prev.map((item, idx) => {
      if (fileIndex === undefined || idx === fileIndex) {
        const itemParts = fileParts.filter(p => p.fileIndex === idx);
        const failedParts = itemParts.filter(p => p.status === 'failed');
        const completedParts = itemParts.filter(p => p.status === 'completed');
        
        if (failedParts.length > 0 && completedParts.length > 0) {
          return { ...item, status: "Partially Completed" };
        } else if (failedParts.length === itemParts.length) {
          return { ...item, status: "Skipped" };
        }
      }
      return item;
    })
  );
  
  // Trigger completion check
  checkAndTriggerFinalCompletion();
}, [fileParts, checkAndTriggerFinalCompletion]);
```

### __4. Enhanced UI Controls__

```typescript
// In the render section, add comprehensive retry/skip options:

{/* File-level controls */}
<FileUploadTable
  allItems={fileUploadItems}
  resume={false}
  showCount={true}
  onRetry={handleRetry}
  onRetryItem={retryFailedParts}
  onSkipItem={skipFailedParts}
  showPartialControls={true}
/>

{/* Global controls for failed uploads */}
{(uploadState.uploadStatus === 'failed' || uploadState.previewCompletionStatus === 'failed') && (
  <SpaceBetween direction="horizontal" size="xs">
    <Button onClick={() => retryFailedParts()} variant="primary">
      Retry All Failed Parts
    </Button>
    <Button onClick={() => skipFailedParts()}>
      Skip All Failed Parts
    </Button>
    <Button onClick={handleManualCompletion}>
      Complete with Successful Parts Only
    </Button>
  </SpaceBetween>
)}

{/* Individual file controls */}
{fileUploadItems.map((item, index) => {
  const hasFailedParts = fileParts.some(p => p.fileIndex === index && p.status === 'failed');
  const hasCompletedParts = fileParts.some(p => p.fileIndex === index && p.status === 'completed');
  
  return hasFailedParts && (
    <SpaceBetween direction="horizontal" size="xs" key={index}>
      <Button size="small" onClick={() => retryFailedParts(index)}>
        Retry {item.name}
      </Button>
      <Button size="small" onClick={() => skipFailedParts(index)}>
        Skip Failed Parts of {item.name}
      </Button>
    </SpaceBetween>
  );
})}
```

### __5. Partial Upload Completion__

```typescript
const completePartialUpload = useCallback(async () => {
  // Only complete files that have all parts successfully uploaded
  const validFiles = getValidCompletionFiles();
  
  if (validFiles.length === 0) {
    // No files completed successfully
    const response: CompleteUploadResponse = {
      assetId: uploadState.createdAssetId || assetDetail.assetId || '',
      message: 'Asset created but no files were successfully uploaded',
      uploadId: 'partial-failure',
      fileResults: [],
      overallSuccess: false
    };
    onUploadComplete(response);
    return;
  }
  
  // Complete upload with only successful files
  const completionRequest = {
    assetId: uploadState.createdAssetId || assetDetail.assetId || '',
    databaseId: assetDetail.databaseId || '',
    uploadType: "assetFile" as const,
    files: validFiles,
  };
  
  try {
    const response = await AssetUploadService.completeUpload(
      uploadState.uploadId!,
      completionRequest
    );
    
    // Modify response to indicate partial success
    const modifiedResponse = {
      ...response,
      message: `${response.message} (${validFiles.length} of ${fileUploadItems.length} files uploaded)`,
      overallSuccess: false // Indicate partial success
    };
    
    onUploadComplete(modifiedResponse);
  } catch (error) {
    onError(error as Error);
  }
}, [uploadState, assetDetail, fileUploadItems, onUploadComplete, onError]);
```

### __6. Enhanced Status Messages__

```typescript
const buildCompletionMessage = (assetStatus: any, previewStatus: any) => {
  const messages = [];
  
  if (assetStatus.isFullSuccess && previewStatus.isFullSuccess) {
    return 'All files uploaded successfully';
  }
  
  if (assetStatus.hasFailures || previewStatus.hasFailures) {
    if (assetStatus.canComplete) messages.push('Some asset files uploaded');
    if (previewStatus.canComplete) messages.push('Preview file uploaded');
    
    return messages.join(', ') + ' (some files had issues)';
  }
  
  return 'Upload completed with mixed results';
};
```

## __Complete Failure Handling Flow:__

1. __During Upload__: Track retry counts and allow up to maxRetries per part

2. __On Failure__: Present options:

   - Retry individual file parts
   - Retry all failed parts
   - Skip failed parts and continue
   - Complete with only successful parts

3. __Partial Success__: Allow completion with subset of files

4. __User Choice__: Respect user decision to skip vs retry

5. __Final Status__: Clear messaging about what succeeded/failed

This comprehensive approach ensures users never get stuck with failed uploads and always have options to proceed, whether with full success, partial success, or informed failure handling.

