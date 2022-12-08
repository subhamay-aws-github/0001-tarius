import json
import logging
import boto3
import csv
import os

# Load the exceptions for error handling
from botocore.exceptions import ClientError, ParamValidationError

s3 = boto3.client('s3', region_name='us-east-1')
dynamodb = boto3.client('dynamodb', region_name='us-east-1')
sns = boto3.client('sns', region_name='us-east-1')
topic_arn = os.getenv("SNS_TOPIC_ARN")
dynamodb_table = os.getenv("DYNAMODB_TABLE_NAME")

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def send_sns_message(topic_arn,s3_bucket_name,s3_key):
    try:
        response = sns.publish(
            TopicArn = topic_arn,
            Message = f"Data loaded successfully to DynamoDB table {dynamodb_table} from s3://{s3_bucket_name}/{s3_key}",
            Subject = 'DynamoDB table load status',
        )
        logger.info(f'Message published to the SNS Topic {topic_arn}')
        logger.info(response)
        return "success"
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")
        
        
def write_dynamo_db(json_data):
    try:
        data = dynamodb.batch_write_item(
            RequestItems = json_data
        )
        logger.info('UnprocessedItems: ')
        logger.info(data['UnprocessedItems'])
        return data['UnprocessedItems']
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")
        
def download_s3_data(bucket, key):
    try:
        data_object = s3.get_object(
            Bucket=bucket,
            Key=key
            )
        data_string = data_object['Body'].read().decode('utf-8').splitlines(True)
        logger.info('Downloaded from S3:')
        reader = csv.DictReader(data_string)


        list_items = []
        for row in reader:
            items = dict()
            attributes = dict()
            put_request_dict = dict()
            attributes["product_id"] = dict(N = row["product_id"])
            attributes["product_title"] = dict(S = row["product_title"])
            attributes["sku"] =  dict(S = row["sku"])
            attributes["parent_sku"] =  dict(S = row["parent_sku"])
            attributes["price"] =  dict(S = row["price"])
            attributes["color"] =  dict(S = row["color"])
            attributes["description"] =  dict(S = row["description"])
            items = dict(Item = attributes)
            put_request_dict = dict(dict(PutRequest = items))
            list_items.append(put_request_dict)
        table_dict = dict()
        table_dict[dynamodb_table] = list_items

        return table_dict
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")
        
def lambda_handler(event, context):

    s3_bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
    s3_key = event["Records"][0]["s3"]["object"]["key"]
    
    logger.info(f"S3 bucket name : {s3_bucket_name}")
    logger.info(f"S3 Key : {s3_key}")
    
    product_data = download_s3_data(s3_bucket_name,s3_key)
    unprocessed_items = write_dynamo_db(product_data)
    logger.info(f"Unprocessed Items : {unprocessed_items}")
    if len(unprocessed_items) == 0:
         sns_success = send_sns_message(topic_arn,s3_bucket_name,s3_key)
         if sns_success == "success":
                 logger.info("SNS Message sent successfully")
    
    return "success"