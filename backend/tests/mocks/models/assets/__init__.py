# Mock module for models.assets
from unittest.mock import MagicMock
from typing import Dict, List, Optional, Any

# Mock common functions and classes
common = MagicMock()

# Model classes needed for tests
class AssetPreviewLocationModel:
    def __init__(self, Key=None):
        self.Key = Key
    
    def dict(self):
        return {"Key": self.Key}

class ExecuteWorkflowModel:
    def __init__(self, workflowIds=None):
        self.workflowIds = workflowIds or []
    
    def dict(self):
        return {"workflowIds": self.workflowIds}

class UpdateMetadataModel:
    def __init__(self, version=None, metadata=None):
        self.version = version
        self.metadata = metadata or {}
    
    def dict(self):
        return {
            "version": self.version,
            "metadata": self.metadata
        }
    
    def update(self, data):
        for key, value in data.items():
            setattr(self, key, value)

class UploadAssetModel:
    def __init__(self, databaseId=None, assetId=None, assetName=None, key=None, 
                 assetType=None, description=None, isDistributable=None, 
                 specifiedPipelines=None, tags=None, Comment=None, previewLocation=None):
        self.databaseId = databaseId
        self.assetId = assetId
        self.assetName = assetName
        self.key = key
        self.assetType = assetType
        self.description = description
        self.isDistributable = isDistributable
        self.specifiedPipelines = specifiedPipelines or []
        self.tags = tags or []
        self.Comment = Comment
        self.previewLocation = previewLocation
    
    def dict(self):
        result = {
            "databaseId": self.databaseId,
            "assetId": self.assetId,
            "assetName": self.assetName,
            "key": self.key,
            "assetType": self.assetType,
            "description": self.description,
            "isDistributable": self.isDistributable,
            "specifiedPipelines": self.specifiedPipelines,
            "tags": self.tags,
            "Comment": self.Comment,
        }
        if self.previewLocation:
            result["previewLocation"] = self.previewLocation.dict() if hasattr(self.previewLocation, 'dict') else self.previewLocation
        return result
    
    def update(self, data):
        for key, value in data.items():
            setattr(self, key, value)

class UploadAssetWorkflowRequestModel:
    def __init__(self, uploadAssetBody=None, copyFrom=None, updateMetadataBody=None, executeWorkflowBody=None):
        self.uploadAssetBody = uploadAssetBody
        self.copyFrom = copyFrom
        self.updateMetadataBody = updateMetadataBody
        self.executeWorkflowBody = executeWorkflowBody
    
    def dict(self):
        result = {}
        if self.uploadAssetBody:
            result["uploadAssetBody"] = self.uploadAssetBody.dict() if hasattr(self.uploadAssetBody, 'dict') else self.uploadAssetBody
        if self.copyFrom:
            result["copyFrom"] = self.copyFrom.dict() if hasattr(self.copyFrom, 'dict') else self.copyFrom
        if self.updateMetadataBody:
            result["updateMetadataBody"] = self.updateMetadataBody.dict() if hasattr(self.updateMetadataBody, 'dict') else self.updateMetadataBody
        if self.executeWorkflowBody:
            result["executeWorkflowBody"] = self.executeWorkflowBody.dict() if hasattr(self.executeWorkflowBody, 'dict') else self.executeWorkflowBody
        return result

class UploadAssetWorkflowResponseModel:
    def __init__(self, message=None):
        self.message = message
    
    def dict(self):
        return {"message": self.message}

class GetUploadAssetWorkflowStepFunctionInput:
    def __init__(self, request):
        self.uploadAssetBody = None
        self.copyObjectBody = None
        self.updateMetadataBody = None
        self.executeWorkflowBody = None
        
        # Create a class for uploadAssetBody with nested body attribute and update method
        class UploadAssetBody(dict):
            def __init__(self, body):
                self.body = body
                self.pathParameters = {
                    "databaseId": body.databaseId,
                    "assetId": body.assetId
                }
                # Initialize as a dictionary to support dict operations
                super().__init__()
            
            def update(self, data):
                for key, value in data.items():
                    setattr(self, key, value)
                # Also update as a dictionary
                super().update(data)
        
        # Create a class for copyObjectBody with copySource attribute and update method
        class CopyObjectBody:
            def __init__(self, copy_source):
                self.copySource = copy_source
            
            def update(self, data):
                for key, value in data.items():
                    setattr(self, key, value)
        
        # Create a class for updateMetadataBody with nested body attribute and update method
        class UpdateMetadataBody:
            def __init__(self, body, database_id, asset_id):
                self.body = body
                
                # Create a class for pathParameters
                class PathParameters:
                    def __init__(self, database_id, asset_id):
                        self.databaseId = database_id
                        self.assetId = asset_id
                
                self.pathParameters = PathParameters(database_id, asset_id)
            
            def update(self, data):
                for key, value in data.items():
                    setattr(self, key, value)
        
        if hasattr(request, 'uploadAssetBody') and request.uploadAssetBody:
            self.uploadAssetBody = UploadAssetBody(request.uploadAssetBody)
        
        if hasattr(request, 'copyFrom') and request.copyFrom:
            self.copyObjectBody = CopyObjectBody(request.copyFrom)
        
        if hasattr(request, 'updateMetadataBody') and request.updateMetadataBody:
            database_id = request.uploadAssetBody.databaseId if hasattr(request, 'uploadAssetBody') and request.uploadAssetBody else None
            asset_id = request.uploadAssetBody.assetId if hasattr(request, 'uploadAssetBody') and request.uploadAssetBody else None
            self.updateMetadataBody = UpdateMetadataBody(request.updateMetadataBody, database_id, asset_id)
        
        if hasattr(request, 'executeWorkflowBody') and request.executeWorkflowBody and hasattr(request.executeWorkflowBody, 'workflowIds'):
            # Create a class for workflow items
            class WorkflowItem:
                def __init__(self, workflow_id):
                    # Create a class for pathParameters
                    class PathParameters:
                        def __init__(self, workflow_id):
                            self.workflowId = workflow_id
                    
                    self.pathParameters = PathParameters(workflow_id)
                
                def dict(self):
                    return {"pathParameters": {"workflowId": self.pathParameters.workflowId}}
            
            self.executeWorkflowBody = []
            for workflow_id in request.executeWorkflowBody.workflowIds:
                self.executeWorkflowBody.append(WorkflowItem(workflow_id))
    
    def dict(self):
        result = {}
        if self.uploadAssetBody:
            result["uploadAssetBody"] = self.uploadAssetBody
        if self.copyObjectBody:
            result["copyObjectBody"] = self.copyObjectBody
        if self.updateMetadataBody:
            result["updateMetadataBody"] = self.updateMetadataBody
        if self.executeWorkflowBody:
            result["executeWorkflowBody"] = self.executeWorkflowBody
        return result

# Mock asset model functions
def get_asset(database_id, asset_id):
    """Mock get_asset function"""
    return {
        "assetId": asset_id,
        "databaseId": database_id,
        "name": "Test Asset",
        "type": "test",
        "createdAt": "2023-01-01T00:00:00Z",
        "updatedAt": "2023-01-01T00:00:00Z",
        "metadata": {}
    }

def create_asset(database_id, asset_data):
    """Mock create_asset function"""
    return {
        "assetId": "test-asset-id",
        "databaseId": database_id,
        "name": asset_data.get("name", "Test Asset"),
        "type": asset_data.get("type", "test"),
        "createdAt": "2023-01-01T00:00:00Z",
        "updatedAt": "2023-01-01T00:00:00Z",
        "metadata": asset_data.get("metadata", {})
    }

def update_asset(database_id, asset_id, asset_data):
    """Mock update_asset function"""
    return {
        "assetId": asset_id,
        "databaseId": database_id,
        "name": asset_data.get("name", "Test Asset"),
        "type": asset_data.get("type", "test"),
        "createdAt": "2023-01-01T00:00:00Z",
        "updatedAt": "2023-01-01T00:00:00Z",
        "metadata": asset_data.get("metadata", {})
    }

def delete_asset(database_id, asset_id):
    """Mock delete_asset function"""
    return True
