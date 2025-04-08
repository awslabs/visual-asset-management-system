# Mock module for streams
from unittest.mock import MagicMock

class AOSSIndexAssetMetadata:
    @staticmethod
    def _determine_field_type(field):
        if isinstance(field, str):
            if field.lower() == "true" or field.lower() == "false":
                return "bool"
            if field.isdigit():
                return "num"
            try:
                import json
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
