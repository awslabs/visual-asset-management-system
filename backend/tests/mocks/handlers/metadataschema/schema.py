# Mock module for metadataschema.schema
import json
from unittest.mock import MagicMock
from boto3.dynamodb.conditions import Key

class MetadataSchema:
    """Mock MetadataSchema class"""
    def __init__(self, table_name=None, dynamodb=None):
        self.table_name = table_name
        self.dynamodb = dynamodb
        
    def get_schema(self, database_id, schema_id):
        """Mock get_schema method"""
        if not self.dynamodb:
            return None
        return self.dynamodb.get_item(Key={'databaseId': database_id, 'field': schema_id}).get('Item', {}).get('schema')
        
    def get_all_schemas(self, database_id):
        """Mock get_all_schemas method"""
        if not self.dynamodb:
            return []
        return self.dynamodb.query(KeyConditionExpression=Key("databaseId").eq(database_id)).get('Items', [])
        
    def update_schema(self, database_id, schema_id, schema_data):
        """Mock update_schema method"""
        if not self.dynamodb:
            return
        
        # Create update expression
        update_expr = 'SET '
        expr_attr_names = {}
        expr_attr_values = {}
        
        # Skip databaseId and field as they are part of the key
        skip_fields = ['databaseId', 'field']
        
        i = 0
        for key, value in schema_data.items():
            if key not in skip_fields:
                field_name = f"#f{i}"
                value_name = f":v{i}"
                update_expr += f"{field_name} = {value_name}, "
                expr_attr_names[field_name] = key
                expr_attr_values[value_name] = value
                i += 1
        
        # Remove trailing comma and space
        update_expr = update_expr[:-2]
        
        self.dynamodb.update_item(
            Key={'databaseId': database_id, 'field': schema_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values
        )
        
    def delete_schema(self, database_id, schema_id):
        """Mock delete_schema method"""
        if not self.dynamodb:
            return
        self.dynamodb.delete_item(Key={'databaseId': database_id, 'field': schema_id})

class APIGatewayProxyEvent:
    """Mock APIGatewayProxyEvent class"""
    def __init__(self, event_dict):
        self.event = event_dict
        
def lambda_handler(event, context, **kwargs):
    """Mock lambda_handler function"""
    claims_fn = kwargs.get('claims_fn', lambda: {'roles': []})
    metadata_schema_fn = kwargs.get('metadata_schema_fn', lambda: MagicMock())
    
    # Extract request details
    request_id = event.event.get('requestContext', {}).get('requestId', 'unknown')
    http_method = event.event.get('requestContext', {}).get('http', {}).get('method', event.event.get('httpMethod', 'GET'))
    path_params = event.event.get('pathParameters', {})
    
    # Check for required parameters
    database_id = path_params.get('databaseId')
    if not database_id:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing databaseId in path',
                'requestid': request_id
            })
        }
    
    # Initialize metadata schema
    metadata_schema = metadata_schema_fn()
    
    # Handle different HTTP methods
    if http_method == 'GET':
        schemas = metadata_schema.get_all_schemas(database_id)
        return {
            'statusCode': 200,
            'body': json.dumps({
                'schemas': schemas,
                'requestid': request_id
            }, default=str)
        }
    elif http_method in ['POST', 'PUT']:
        # Parse request body
        body = json.loads(event.event.get('body', '{}'))
        field = body.get('field')
        
        # Update schema
        metadata_schema.update_schema(database_id, field, body)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Schema updated successfully',
                'requestid': request_id
            })
        }
    elif http_method == 'DELETE':
        field = path_params.get('field')
        
        # Delete schema
        metadata_schema.delete_schema(database_id, field)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Schema deleted successfully',
                'requestid': request_id
            })
        }
    else:
        return {
            'statusCode': 405,
            'body': json.dumps({
                'error': 'Method not allowed',
                'requestid': request_id
            })
        }
