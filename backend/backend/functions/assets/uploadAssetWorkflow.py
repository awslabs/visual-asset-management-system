
from typing import Any, Dict
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()


def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> None:
    logger.info("Starting uploadAssetWorkflow")


def add(a: int, b: int) -> int:
    return a+b
