# -*- coding: utf-8 -*-
"""
Created on Wed Dec 10 08:41:01 2022

@author: Subhamay Bhattacharyya
"""

import json
import logging
import boto3
import csv
import os

# Load the exceptions for error handling
from botocore.exceptions import ClientError, ParamValidationError
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

s3_client = boto3.client('s3', region_name=os.getenv('AWS_REGION'))
s3_resource = boto3.resource('s3', region_name=os.getenv('AWS_REGION'))
dynamodb_client = boto3.client('dynamodb', region_name=os.environ.get("AWS_REGION"))
sns = boto3.client('sns', region_name=os.getenv('AWS_REGION'))
topic_arn = os.getenv("SNS_TOPIC_ARN")
dynamodb_table = os.getenv("DYNAMODB_TABLE_NAME")


logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_event_source(event):
    
    if "Records" in event.keys():
        if "s3" in event.get("Records")[0].keys():
            return "s3"
    else:
        return "unknown"
        
def python_obj_to_dynamo_obj(python_obj: dict) -> dict:
    serializer = TypeSerializer()
    return {
        k: serializer.serialize(v)
        for k, v in python_obj.items()
    }

def dynamodb_obj_to_python_obj(dynamodb_obj: dict) -> dict:
    deserializer = TypeDeserializer()
    return {
        k: deserializer.deserialize(v)
        for k, v in dynamodb_obj.items()
    }
    
def send_sns_message(topic_arn, s3_bucket_name, s3_key, s3_key_invalid_records, load_stat):
    
        
    logger.info(f"load stats :: {json.dumps(load_stat)}")
    if load_stat.get("TOTAL_RECORDS") < load_stat.get("RECORDS_LOADED"):
        message = f"Data loaded successfully to DynamoDB table {dynamodb_table} from s3://{s3_bucket_name}/{s3_key}, There are some invalid items are saved in the s3 bucket s3://{s3_bucket_name}/{s3_key_invalid_records}"
    elif load_stat.get("TOTAL_RECORDS") == load_stat.get("FAILED_RECORDS"):
        message = f"Failed to load all the items and are saved in the s3 bucket s3://{s3_bucket_name}/{s3_key_invalid_records}"
    else:
        message = f"Data loaded successfully to DynamoDB table {dynamodb_table} from s3://{s3_bucket_name}/{s3_key}"
        
    logger.info(f"message = {message}")
        
    try:
        response = sns.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject='DynamoDB table load status',
        )
        logger.info(f'Message published to the SNS Topic {topic_arn}')
        return "success"
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")

def dynamodb_item_exists(partitionKey):
    try:
        response = dynamodb_client.get_item(
                            TableName=dynamodb_table,
                            Key={
                                'ID': {'S': partitionKey}
                            }
                    )

        if not response.get('Item'):
            logger.info(f"The item {dict(ID=partitionKey)} does not exist.")
            return False
        else:
            dynamodb_item = dynamodb_obj_to_python_obj(response['Item'])
            logger.info(f"The item {dict(ID=partitionKey)} exists.")
            return True
 
        
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}") 
    except ClientError as e:
        logger.error(f"Client error: {e}")
        
def dynamo_db_put_item(item):

    try:
        response = dynamodb_client.put_item(
            TableName = dynamodb_table,
            Item=item
            )

        return response
    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")
        
def upload_to_s3(bucket,key,invalid_items):

    try:
        object = s3_resource.Object(bucket_name=bucket,key=key)
        object.put(Body=invalid_items)

    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")

def download_s3_data(bucket, key):
    try:
        data_object = s3_client.get_object(
            Bucket=bucket,
            Key=key
        )
        data_string = data_object['Body'].read().decode(
            'utf-8').splitlines(True)
        logger.info(f"Downloaded file file s3://{bucket}/{key}")
        reader = csv.DictReader(data_string) 
    
        list_items = []
        for row in reader:
            Item=python_obj_to_dynamo_obj(row)
            list_items.append(Item)

        return list_items


    # An error occurred
    except ParamValidationError as e:
        logger.error(f"Parameter validation error: {e}")
        raise Exception(f"Parameter validation error: {e}")
    except ClientError as e:
        logger.error(f"Client error: {e}")
        raise Exception(f"Client error: {e}")


def lambda_handler(event, context):
    
    event_source = get_event_source(event)
    
    if event_source == "s3":
        records_processed = 0
        invalid_items = []
        load_stat=dict(TOTAL_RECORDS=0,RECORDS_LOADED=0,FAILED_RECORDS=0)
        
    
        s3_bucket_name = event["Records"][0]["s3"]["bucket"]["name"]
        s3_key = event["Records"][0]["s3"]["object"]["key"]
    
        logger.info(f"S3 bucket name : {s3_bucket_name}")
        logger.info(f"S3 Key : {s3_key}")
    
        product_data = download_s3_data(s3_bucket_name, s3_key)
        
        load_stat['TOTAL_RECORDS'] = len(product_data)
        for item in product_data:
            python_obj = dynamodb_obj_to_python_obj(item)
            partition_key = python_obj.get("ID")
            
            
            if partition_key:
                item_exists = dynamodb_item_exists(partitionKey=partition_key)
                response = dynamo_db_put_item(item) 
                if response["ResponseMetadata"]["HTTPStatusCode"] == 200:
                    records_processed += 1
                if item_exists:
                    logger.info(f"Updated the existing item {dict(ID=partition_key)} successfully")
                else:
                    logger.info(f"Inserted the new item {dict(ID=partition_key)} successfully")
            else:
                invalid_items.append(item)
    
        load_stat['RECORDS_LOADED'] = records_processed
        load_stat['FAILED_RECORDS'] =  len(invalid_items) 
                
        # If there are any failed records then upload the invalid records to the S3 bucket under the 
        # invalid-records/ folder
        if len(invalid_items) > 0:
            s3_key_invalid_records = f"invalid-records/{context.aws_request_id}/invalid_items.json"
            response = upload_to_s3(s3_bucket_name,s3_key_invalid_records, json.dumps(invalid_items))
        sns_success = send_sns_message(topic_arn,s3_bucket_name,s3_key,s3_key_invalid_records,load_stat)
        if sns_success == "success":
            logger.info("SNS Message sent successfully")
    else:
        logger.error(f"Event source : {event_source}")
        raise Exception("Invalid event source")
    
    return {
        "statsCode": 200,
        "Message": f"File s3://{s3_bucket_name}/{s3_key} processed successfully !"
    }
    
