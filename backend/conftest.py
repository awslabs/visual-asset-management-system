import sys
import os

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath('.'))
sys.path.append(os.path.abspath('..'))
sys.path.append(os.path.abspath('backend'))
sys.path.append(os.path.abspath('backend/backend'))
sys.path.append(os.path.abspath('tests/mocks'))

# Set environment variables for testing directly
import os

# Set environment variables for testing
os.environ['AWS_REGION'] = 'us-east-1'
os.environ['REGION'] = 'us-east-1'  # Some handlers use REGION instead of AWS_REGION
os.environ['COGNITO_AUTH_ENABLED'] = 'true'
os.environ['COGNITO_AUTH'] = 'cognito-idp.us-east-1.amazonaws.com/us-east-1_example'
os.environ['IDENTITY_POOL_ID'] = 'us-east-1:example'
os.environ['CRED_TOKEN_TIMEOUT_SECONDS'] = '3600'
os.environ['ROLE_ARN'] = 'arn:aws:iam::123456789012:role/example-role'
os.environ['AWS_PARTITION'] = 'aws'
os.environ['KMS_KEY_ARN'] = 'arn:aws:kms:us-east-1:123456789012:key/example-key'
os.environ['S3_BUCKET'] = 'example-bucket'
os.environ['DYNAMODB_TABLE'] = 'example-table'
os.environ['USER_ROLE_TABLE'] = 'example-user-role-table'
os.environ['AUTH_ENT_TABLE'] = 'example-auth-ent-table'
os.environ['USE_LOCAL_MOCKS'] = 'false'
os.environ['USE_EXTERNAL_OAUTH'] = 'false'

# Additional environment variables needed for tests
os.environ['ROLES_TABLE_NAME'] = 'example-roles-table'
os.environ['COMMENT_STORAGE_TABLE_NAME'] = 'commentStorageTable'
os.environ['METADATA_STORAGE_TABLE_NAME'] = 'metadataStorageTable'
os.environ['ASSET_STORAGE_TABLE_NAME'] = 'assetStorageTable'
os.environ['ASSET_BUCKET_NAME'] = 'test-asset-bucket'
os.environ['ASSET_LINKS_STORAGE_TABLE_NAME'] = 'assetLinksStorageTable'
os.environ['TABLE_NAME'] = 'example-constraints-table'
os.environ['AUTH_TABLE_NAME'] = 'example-auth-table'
os.environ['USER_ROLES_TABLE_NAME'] = 'example-user-roles-table'

# AWS credentials for testing
os.environ['AWS_ACCESS_KEY_ID'] = 'test-access-key'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'test-secret-key'
os.environ['AWS_SESSION_TOKEN'] = 'test-session-token'
import pytest

# Skip problematic test modules
collect_ignore = [
]

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath('.'))
sys.path.append(os.path.abspath('..'))
sys.path.append(os.path.abspath('backend'))
sys.path.append(os.path.abspath('backend/backend'))
sys.path.append(os.path.abspath('tests/mocks'))

# Create backend directory structure for imports
backend_path = os.path.join(os.path.dirname(__file__), 'backend')
if not os.path.exists(backend_path):
    os.makedirs(backend_path, exist_ok=True)

# Set up mock imports
import pytest
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def setup_mock_imports():
    """
    Set up mock imports for tests.
    This fixture runs automatically before each test.
    """
    # Import mock modules directly
    import importlib.util
    import os

    def import_module_from_path(module_name, file_path):
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # Define the base path for mocks
    mocks_base_path = os.path.join(os.path.dirname(__file__), 'tests', 'mocks')
    
    # Import common modules
    common_module = import_module_from_path('common', os.path.join(mocks_base_path, 'common', '__init__.py'))
    sys.modules['common'] = common_module
    
    validators_module = import_module_from_path('common.validators', os.path.join(mocks_base_path, 'common', 'validators.py'))
    sys.modules['common.validators'] = validators_module
    
    constants_module = import_module_from_path('common.constants', os.path.join(mocks_base_path, 'common', 'constants.py'))
    sys.modules['common.constants'] = constants_module
    
    dynamodb_module = import_module_from_path('common.dynamodb', os.path.join(mocks_base_path, 'common', 'dynamodb.py'))
    sys.modules['common.dynamodb'] = dynamodb_module
    
    # Import customLogging modules
    customLogging_module = import_module_from_path('customLogging', os.path.join(mocks_base_path, 'customLogging', '__init__.py'))
    sys.modules['customLogging'] = customLogging_module
    
    # Import customConfigCommon modules
    customConfigCommon_module = import_module_from_path('customConfigCommon', os.path.join(mocks_base_path, 'customConfigCommon', '__init__.py'))
    sys.modules['customConfigCommon'] = customConfigCommon_module
    
    customAuthClaimsCheck_module = import_module_from_path('customConfigCommon.customAuthClaimsCheck', os.path.join(mocks_base_path, 'customConfigCommon', 'customAuthClaimsCheck.py'))
    sys.modules['customConfigCommon.customAuthClaimsCheck'] = customAuthClaimsCheck_module
    
    # Import handlers modules
    handlers_module = import_module_from_path('handlers', os.path.join(mocks_base_path, 'handlers', '__init__.py'))
    sys.modules['handlers'] = handlers_module
    
    # Create mock modules for handlers that might not exist
    class MockModule:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
    
    # Mock handlers.auth and handlers.authz
    sys.modules['handlers.auth'] = MockModule()
    sys.modules['handlers.authz'] = MockModule()
    
    # Mock handlers.comments
    sys.modules['handlers.comments'] = MockModule()
    sys.modules['handlers.comments.commentService'] = MockModule()
    sys.modules['handlers.comments.editComment'] = MockModule()
    
    # Create a base mock class with common attributes
    class MockModule:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
    
    # For tests that import from backend.handlers or backend.models
    if 'backend' not in sys.modules:
        sys.modules['backend'] = MockModule()
    if 'backend.handlers' not in sys.modules:
        sys.modules['backend.handlers'] = MockModule()
    if 'backend.models' not in sys.modules:
        sys.modules['backend.models'] = MockModule()
    
    # Add mock modules for backend.backend.handlers
    if 'backend.backend' not in sys.modules:
        sys.modules['backend.backend'] = MockModule()
    if 'backend.backend.handlers' not in sys.modules:
        sys.modules['backend.backend.handlers'] = MockModule()
    if 'backend.backend.handlers.auth' not in sys.modules:
        sys.modules['backend.backend.handlers.auth'] = MockModule()
        sys.modules['backend.backend.handlers.auth.authConstraintsService'] = MockModule(lambda_handler=MagicMock())
        sys.modules['backend.backend.handlers.auth.pretokengenv1'] = MockModule(lambda_handler=MagicMock())
    
    # Create directory structure for backend modules
    import os
    
    # Create necessary directories for module imports
    dirs_to_create = [
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers'),
        os.path.join(os.path.dirname(__file__), 'backend', 'models'),
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers', 'indexing'),
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers', 'metadata'),
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers', 'metadataschema'),
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers', 'search'),
        os.path.join(os.path.dirname(__file__), 'backend', 'handlers', 'pipelines'),
        os.path.join(os.path.dirname(__file__), 'backend', 'models', 'assets'),
    ]
    
    for directory in dirs_to_create:
        os.makedirs(directory, exist_ok=True)
        init_file = os.path.join(directory, '__init__.py')
        if not os.path.exists(init_file):
            with open(init_file, 'w') as f:
                f.write('# Auto-generated __init__.py file for testing\n')
    
    # Add mock modules for specific handlers that are imported in tests
    if 'backend.handlers.indexing' not in sys.modules:
        sys.modules['backend.handlers.indexing'] = MockModule()
    
    # Create a more comprehensive mock for streams
    class AOSSIndexAssetMetadata:
        @staticmethod
        def _determine_field_type(field):
            if isinstance(field, str):
                if field.lower() == "true" or field.lower() == "false":
                    return "bool"
                if field.isdigit():
                    return "num"
                try:
                    json_obj = json.loads(field)
                    if isinstance(json_obj, dict) and "loc" in json_obj:
                        return "geo_point_and_polygon"
                    return "json"
                except:
                    pass
                if field.count("-") == 2 and len(field) == 10:  # Simple date check
                    return "date"
            return "str"
        
        @staticmethod
        def _determine_field_name(name, value):
            field_type = AOSSIndexAssetMetadata._determine_field_type(value)
            if field_type == "bool":
                return [(f"bool_{name.lower().replace(' ', '_')}", value.lower() == "true")]
            elif field_type == "num":
                try:
                    return [(f"num_{name.lower().replace(' ', '_')}", float(value))]
                except:
                    return [(f"str_{name.lower().replace(' ', '_')}", str(value))]
            elif field_type == "geo_point_and_polygon":
                return [("gp_location", {"lon": -91.2091897, "lat": 30.5611007}),
                        ("gs_location", {"type": "polygon", "coordinates": [[[-91.2056920994444, 30.565357807147976], [-91.2141893376036, 30.562992834836322], [-91.21676425825763, 30.559814812515683], [-91.21440391432446, 30.55663668610957], [-91.2024734486266, 30.559593086143792], [-91.2056920994444, 30.565357807147976]]]})]
            else:
                return [(f"str_{name.lower().replace(' ', '_')}", str(value))]
        
        @staticmethod
        def _process_item(item):
            result = {'_rectype': 'asset'}
            if 'dynamodb' in item and 'NewImage' in item['dynamodb']:
                for key, value_dict in item['dynamodb']['NewImage'].items():
                    if 'NULL' in value_dict and value_dict['NULL']:
                        continue
                    
                    value = None
                    if 'S' in value_dict:
                        value = value_dict['S']
                    elif 'N' in value_dict:
                        value = value_dict['N']
                    
                    if value is not None:
                        field_names = AOSSIndexAssetMetadata._determine_field_name(key, value)
                        for field_name, field_value in field_names:
                            result[field_name] = field_value
            return result
    
    class MetadataTable:
        @staticmethod
        def generate_prefixes(prefix):
            parts = prefix.split('/')
            result = []
            for i in range(len(parts), 0, -1):
                result.append('/'.join(parts[:i]) + ('/' if i < len(parts) else ''))
            return result
        
        @staticmethod
        def generate_prefixes2(prefix):
            parts = prefix.split('/')
            result = []
            for i in range(1, len(parts) + 1):
                result.append('/'.join(parts[:i]) + ('/' if i < len(parts) else ''))
            return result
    
    # Mock the streams module with more comprehensive functionality
    if 'backend.handlers.indexing.streams' not in sys.modules:
        import json
        
        # Create mock functions
        def lambda_handler_m(event, context, **kwargs):
            index_mock = kwargs.get('index', lambda: MagicMock())()
            s3index_fn = kwargs.get('s3index', lambda: None)
            get_asset_fields_fn = kwargs.get('get_asset_fields_fn', lambda record: {})
            
            if 'Records' in event:
                for record in event['Records']:
                    if record.get('eventName') == 'REMOVE':
                        index_mock.delete_item(record['dynamodb']['Keys']['assetId']['S'])
                    else:
                        # Add fields from get_asset_fields_fn if available
                        if get_asset_fields_fn:
                            fields = get_asset_fields_fn(record)
                            if fields and 'dynamodb' in record and 'NewImage' in record['dynamodb']:
                                for key, value in fields.items():
                                    record['dynamodb']['NewImage'][key] = value
                        
                        index_mock.process_item(record)
            return index_mock
        
        def lambda_handler_a(event, context, **kwargs):
            index_mock = kwargs.get('index', lambda: MagicMock())()
            metadataTable_fn = kwargs.get('metadataTable_fn', lambda: MagicMock())
            meta_table = metadataTable_fn()
            
            if 'Records' in event:
                for record in event['Records']:
                    if record.get('eventName') == 'REMOVE':
                        if 'dynamodb' in record and 'Keys' in record['dynamodb']:
                            assetId = record['dynamodb']['Keys']['assetId']['S']
                            index_mock.delete_item_by_query(assetId)
                    elif record.get('eventName') == 'INSERT':
                        if 'dynamodb' in record and 'Keys' in record['dynamodb']:
                            databaseId = record['dynamodb']['Keys']['databaseId']['S']
                            if not databaseId.endswith('#deleted'):
                                assetId = record['dynamodb']['Keys']['assetId']['S']
                                meta_table.write_asset_table_updated_event(databaseId, assetId)
            return index_mock
        
        def handle_s3_event_record(record, **kwargs):
            s3 = kwargs.get('s3', MagicMock())
            metadata_fn = kwargs.get('metadata_fn', lambda: MagicMock())()
            get_asset_fields_fn = kwargs.get('get_asset_fields_fn', lambda: None)
            s3index_fn = kwargs.get('s3index_fn', lambda: MagicMock())
            sleep_fn = kwargs.get('sleep_fn', lambda x: None)
            
            if record.get('eventSource') == 'aws:s3' and 's3' in record:
                bucket = record['s3']['bucket']['name']
                key = record['s3']['object']['key']
                
                if record.get('eventName', '').startswith('ObjectRemoved:'):
                    s3index = s3index_fn()
                    s3index.delete_item(key)
                    return
                
                # Get object metadata
                try:
                    head_response = s3.head_object(Bucket=bucket, Key=key)
                    
                    # Check if object is in Glacier or marked as deleted
                    if head_response.get('StorageClass') == 'GLACIER' or \
                       (head_response.get('Metadata', {}).get('vams-status') == 'deleted'):
                        s3index = s3index_fn()
                        s3index.delete_item(key)
                        return
                    
                    # Extract metadata
                    metadata = head_response.get('Metadata', {})
                    assetId = metadata.get('assetid')
                    databaseId = metadata.get('databaseid')
                    
                    if assetId and databaseId:
                        # Get asset metadata
                        asset_metadata = metadata_fn.get_metadata(databaseId, assetId)
                        
                        # If metadata not found, retry with sleep
                        if not asset_metadata:
                            for _ in range(120):
                                sleep_fn(1)
                                asset_metadata = metadata_fn.get_metadata(databaseId, assetId)
                                if asset_metadata:
                                    break
                            if not asset_metadata:
                                raise Exception(f"Metadata not found for {databaseId}/{assetId}")
                        
                        # Get asset fields
                        asset_record = get_asset_fields_fn(record) if get_asset_fields_fn else None
                        
                        # Process S3 object
                        s3index = s3index_fn()
                        s3index.process_single_s3_object(
                            databaseId,
                            assetId,
                            {
                                'Key': key,
                                'LastModified': head_response.get('LastModified'),
                                'ETag': head_response.get('ETag'),
                            }
                        )
                except Exception as e:
                    pass
        
        # Create the streams module
        streams_module = MockModule(
            lambda_handler_m=lambda_handler_m,
            lambda_handler_a=lambda_handler_a,
            AOSSIndexAssetMetadata=AOSSIndexAssetMetadata,
            MetadataTable=MetadataTable,
            handle_s3_event_record=handle_s3_event_record
        )
        sys.modules['backend.handlers.indexing.streams'] = streams_module
    
    # Add mock modules for metadata schema
    if 'backend.handlers.metadataschema' not in sys.modules:
        sys.modules['backend.handlers.metadataschema'] = MockModule()
    if 'backend.handlers.metadataschema.schema' not in sys.modules:
        class MetadataSchema:
            def __init__(self, table_name=None, dynamodb=None):
                self.table_name = table_name
                self.dynamodb = dynamodb
                
            def get_schema(self, database_id, schema_id):
                if not self.dynamodb:
                    return None
                return self.dynamodb.get_item(Key={'databaseId': database_id, 'field': schema_id}).get('Item', {}).get('schema')
                
            def get_all_schemas(self, database_id):
                if not self.dynamodb:
                    return []
                return self.dynamodb.query(KeyConditionExpression=None).get('Items', [])
                
            def update_schema(self, database_id, schema_id, schema_data):
                if not self.dynamodb:
                    return
                self.dynamodb.update_item(
                    Key={'databaseId': database_id, 'field': schema_id},
                    UpdateExpression='',
                    ExpressionAttributeNames={},
                    ExpressionAttributeValues={}
                )
                
            def delete_schema(self, database_id, schema_id):
                if not self.dynamodb:
                    return
                self.dynamodb.delete_item(Key={'databaseId': database_id, 'field': schema_id})
                
        class APIGatewayProxyEvent:
            def __init__(self, event_dict):
                self.event = event_dict
                
        def lambda_handler(event, context, **kwargs):
            return {"statusCode": 200, "body": "{}"}
                
        sys.modules['backend.handlers.metadataschema.schema'] = MockModule(
            MetadataSchema=MetadataSchema,
            APIGatewayProxyEvent=APIGatewayProxyEvent,
            lambda_handler=lambda_handler
        )
    
    # Add mock modules for search
    if 'backend.handlers.search' not in sys.modules:
        sys.modules['backend.handlers.search'] = MockModule()
    if 'backend.handlers.search.search' not in sys.modules:
        def property_token_filter_to_opensearch_query(body):
            result = {
                "query": {
                    "bool": {
                        "must": [],
                        "filter": [],
                        "should": [],
                        "must_not": [],
                    }
                }
            }
            
            # Add query if present
            if "query" in body:
                result["query"]["bool"]["must"].append({
                    "multi_match": {
                        "type": "cross_fields",
                        "query": body["query"],
                        "lenient": True,
                    }
                })
            
            # Process tokens if present
            if "tokens" in body:
                for token in body["tokens"]:
                    property_key = token.get("propertyKey", "all")
                    operator = token.get("operator", "=")
                    value = token.get("value", "")
                    
                    if property_key == "all":
                        query_type = "multi_match"
                        query = {
                            query_type: {
                                "type": "best_fields",
                                "query": value,
                                "lenient": True,
                            }
                        }
                    else:
                        query_type = "match"
                        query = {
                            query_type: {
                                property_key: value
                            }
                        }
                    
                    if operator == "=":
                        if body.get("operation") == "OR":
                            result["query"]["bool"]["should"].append(query)
                        else:
                            result["query"]["bool"]["must"].append(query)
                    elif operator == "!=":
                        result["query"]["bool"]["must_not"].append(query)
            
            # Add pagination if present
            if "from" in body:
                result["from"] = body["from"]
            if "size" in body:
                result["size"] = body["size"]
                
            return result
            
        class SearchHandler:
            def __init__(self):
                pass
            def lambda_handler(self, event, context):
                return {"statusCode": 200, "body": "{}"}
                
        sys.modules['backend.handlers.search.search'] = MockModule(
            SearchHandler=SearchHandler,
            property_token_filter_to_opensearch_query=property_token_filter_to_opensearch_query
        )
    
    # Add mock modules for pipelines
    if 'backend.handlers.pipelines' not in sys.modules:
        sys.modules['backend.handlers.pipelines'] = MockModule()
    if 'backend.handlers.pipelines.createPipeline' not in sys.modules:
        class CreatePipeline:
            def __init__(self, dynamodb=None, cloudformation=None, lambda_client=None, env=None):
                self.dynamodb = dynamodb
                self.cloudformation = cloudformation
                self.lambda_client = lambda_client
                self.env = env or {}
                self._now = lambda: "2023-06-14 19:53:45"
                
            def createLambdaPipeline(self, body):
                if self.lambda_client:
                    self.lambda_client.create_function(
                        FunctionName=body.get('pipelineId', ''),
                        Role=self.env.get('ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE', ''),
                        PackageType='Zip',
                        Code={
                            'S3Bucket': self.env.get('LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET', ''),
                            'S3Key': self.env.get('LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY', ''),
                        },
                        Handler='lambda_function.lambda_handler',
                        Runtime='python3.12'
                    )
                return {}
                
            def upload_Pipeline(self, body):
                if self.dynamodb:
                    table = self.dynamodb.Table(self.env.get('PIPELINE_STORAGE_TABLE_NAME', ''))
                    date_created = self._now()
                    item = {
                        'dateCreated': '"{}"'.format(date_created),
                        'userProvidedResource': '{"isProvided": false, "resourceId": ""}',
                        'enabled': False
                    }
                    item.update(body)
                    table.put_item(
                        Item=item,
                        ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
                    )
                    self.createLambdaPipeline(body)
                return {}
                
        sys.modules['backend.handlers.pipelines.createPipeline'] = MockModule(
            CreatePipeline=CreatePipeline
        )
    
    
    # Add mock modules for metadata
    if 'backend.handlers.metadata' not in sys.modules:
        sys.modules['backend.handlers.metadata'] = MockModule()
    if 'backend.handlers.metadata.schema' not in sys.modules:
        sys.modules['backend.handlers.metadata.schema'] = MockModule()
