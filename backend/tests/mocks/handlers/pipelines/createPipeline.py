# Mock module for pipelines.createPipeline
from unittest.mock import MagicMock

class CreatePipeline:
    """Mock CreatePipeline class"""
    def __init__(self, dynamodb=None, cloudformation=None, lambda_client=None, env=None):
        self.dynamodb = dynamodb
        self.cloudformation = cloudformation
        self.lambda_client = lambda_client
        self.env = env or {}
        self._now = lambda: "2023-06-14 19:53:45"
        
    def createLambdaPipeline(self, body):
        """Mock createLambdaPipeline method"""
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
        """Mock upload_Pipeline method"""
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
