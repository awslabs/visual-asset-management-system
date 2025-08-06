/**
 * File Handle Compatibility Layer
 * 
 * This utility provides a compatibility layer for the File System Access API
 * to ensure consistent behavior across different browsers and operating systems.
 * It handles cases where the API is not fully supported or has implementation differences.
 */

/**
 * Check if the File System Access API is fully supported
 */
export const isFileSystemAccessAPISupported = (): boolean => {
  return (
    typeof window !== 'undefined' &&
    'showOpenFilePicker' in window &&
    'showDirectoryPicker' in window &&
    'showSaveFilePicker' in window
  );
};

/**
 * Check if the browser supports directory selection
 */
export const isDirectoryPickerSupported = (): boolean => {
  return typeof window !== 'undefined' && 'showDirectoryPicker' in window;
};

/**
 * Interface for a normalized file handle that works across browsers
 */
export interface NormalizedFileHandle {
  name: string;
  path: string;
  getFile: () => Promise<File>;
  isDirectory: boolean;
}

/**
 * Create a normalized file handle from a File System Access API handle
 * or from a traditional File object
 */
export const createNormalizedFileHandle = (
  handle: any,
  path: string = ''
): NormalizedFileHandle => {
  // If it's a standard File object (fallback for browsers without File System Access API)
  if (handle instanceof File) {
    return {
      name: handle.name,
      path: path || handle.name,
      getFile: async () => handle,
      isDirectory: false,
    };
  }

  // If it's a FileSystemFileHandle from the File System Access API
  if (handle && typeof handle.getFile === 'function') {
    return {
      name: handle.name,
      path: path || handle.name,
      getFile: async () => {
        try {
          return await handle.getFile();
        } catch (error) {
          console.error('Error getting file from handle:', error);
          throw new Error('Failed to get file from handle. Browser may not fully support the File System Access API.');
        }
      },
      isDirectory: false,
    };
  }

  // If it's a custom handle format (like from our FolderUpload component)
  if (handle && handle.handle) {
    return {
      name: handle.name || (handle.handle.name ? handle.handle.name : 'unknown'),
      path: path || handle.path || handle.name || 'unknown',
      getFile: async () => {
        try {
          // Try the standard getFile method first
          if (typeof handle.handle.getFile === 'function') {
            return await handle.handle.getFile();
          }
          
          // If handle.handle is itself a File object
          if (handle.handle instanceof File) {
            return handle.handle;
          }
          
          // Last resort fallback
          throw new Error('Unsupported file handle format');
        } catch (error) {
          console.error('Error getting file from custom handle:', error);
          throw new Error('Failed to get file from handle. The file may not be accessible.');
        }
      },
      isDirectory: false,
    };
  }

  // Fallback for unknown handle types
  console.warn('Unknown handle type:', handle);
  return {
    name: handle?.name || 'unknown',
    path: path || handle?.name || 'unknown',
    getFile: async () => {
      throw new Error('Unsupported file handle format');
    },
    isDirectory: false,
  };
};

/**
 * Safely get a File from a handle, with error handling
 * @param handle Any type of file handle
 * @returns Promise<File>
 */
export const safeGetFile = async (handle: any): Promise<File> => {
  try {
    // If it's already a File
    if (handle instanceof File) {
      return handle;
    }
    
    // If it's a normalized handle
    if (handle && typeof handle.getFile === 'function') {
      return await handle.getFile();
    }
    
    // If it's a FileSystemFileHandle
    if (handle && handle.kind === 'file' && typeof handle.getFile === 'function') {
      return await handle.getFile();
    }
    
    // If it's our custom handle format
    if (handle && handle.handle) {
      if (typeof handle.handle.getFile === 'function') {
        return await handle.handle.getFile();
      }
      
      if (handle.handle instanceof File) {
        return handle.handle;
      }
    }
    
    throw new Error('Unsupported file handle format');
  } catch (error) {
    console.error('Error in safeGetFile:', error);
    throw new Error('Failed to get file from handle. The browser may not support this operation.');
  }
};
