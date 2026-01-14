"""
FHIR Migration Lambda Handler
Main application handler for processing FHIR migration requests
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logger.setLevel(getattr(logging, log_level))

# Environment variables
SECRET_ARN = os.environ.get('SECRET_ARN')
STATE_MACHINE_ARN = os.environ.get('STATE_MACHINE_ARN')
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')

# AWS clients
secrets_client = boto3.client('secretsmanager')
stepfunctions_client = boto3.client('stepfunctions')


def get_secret(secret_arn: str) -> dict:
    """
    Retrieve secret from AWS Secrets Manager.
    
    Args:
        secret_arn: ARN of the secret to retrieve
        
    Returns:
        dict: Parsed secret value
        
    Raises:
        ClientError: If secret retrieval fails
    """
    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        
        if 'SecretString' in response:
            return json.loads(response['SecretString'])
        else:
            # Handle binary secret if needed
            import base64
            return json.loads(base64.b64decode(response['SecretBinary']))
            
    except ClientError as e:
        logger.error(f"Error retrieving secret: {e}")
        raise


def start_step_function(input_data: dict) -> dict:
    """
    Start Step Functions state machine execution.
    
    Args:
        input_data: Input data for the state machine
        
    Returns:
        dict: Execution response
    """
    if not STATE_MACHINE_ARN:
        logger.warning("STATE_MACHINE_ARN not configured")
        return {"status": "skipped", "message": "State machine not configured"}
    
    try:
        import uuid
        execution_name = f"execution-{uuid.uuid4()}"
        
        response = stepfunctions_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps(input_data)
        )
        
        logger.info(f"Started Step Function execution: {response['executionArn']}")
        return {
            "executionArn": response['executionArn'],
            "startDate": str(response['startDate'])
        }
        
    except ClientError as e:
        logger.error(f"Error starting Step Function: {e}")
        raise


def create_response(status_code: int, body: Any, headers: dict = None) -> dict:
    """
    Create API Gateway response.
    
    Args:
        status_code: HTTP status code
        body: Response body
        headers: Optional additional headers
        
    Returns:
        dict: API Gateway response format
    """
    default_headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type,Authorization,x-client-id',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
    }
    
    if headers:
        default_headers.update(headers)
    
    return {
        'statusCode': status_code,
        'headers': default_headers,
        'body': json.dumps(body) if isinstance(body, dict) else body
    }


def handle_health_check(event: dict) -> dict:
    """
    Handle health check endpoint.
    
    Args:
        event: API Gateway event
        
    Returns:
        dict: Health check response
    """
    try:
        # Verify secrets manager connectivity
        if SECRET_ARN:
            secret = get_secret(SECRET_ARN)
            secrets_status = "connected"
        else:
            secrets_status = "not_configured"
            
    except Exception as e:
        secrets_status = f"error: {str(e)}"
    
    health_response = {
        "status": "healthy",
        "environment": ENVIRONMENT,
        "services": {
            "secrets_manager": secrets_status,
            "step_functions": "configured" if STATE_MACHINE_ARN else "not_configured"
        }
    }
    
    return create_response(200, health_response)


def handle_test_endpoint(event: dict) -> dict:
    """
    Handle test endpoint (requires authorization).
    
    Args:
        event: API Gateway event
        
    Returns:
        dict: Test response
    """
    # Extract user context from authorizer
    request_context = event.get('requestContext', {})
    authorizer_context = request_context.get('authorizer', {})
    
    response = {
        "message": "Test endpoint successful",
        "environment": ENVIRONMENT,
        "authorized": True,
        "principalId": authorizer_context.get('principalId', 'unknown'),
        "clientId": authorizer_context.get('clientId', 'unknown')
    }
    
    return create_response(200, response)


def handle_member_registration(event: dict) -> dict:
    """
    Handle member registration endpoint.
    
    Args:
        event: API Gateway event
        
    Returns:
        dict: Registration response
    """
    try:
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        
        # Validate required fields
        required_fields = ['memberId', 'firstName', 'lastName']
        missing_fields = [f for f in required_fields if f not in body]
        
        if missing_fields:
            return create_response(400, {
                "error": "Bad Request",
                "message": f"Missing required fields: {', '.join(missing_fields)}"
            })
        
        # Get configuration from Secrets Manager
        if SECRET_ARN:
            config = get_secret(SECRET_ARN)
            logger.info("Successfully retrieved configuration from Secrets Manager")
        else:
            config = {}
            logger.warning("SECRET_ARN not configured")
        
        # Prepare data for Step Function
        registration_data = {
            "memberId": body['memberId'],
            "firstName": body['firstName'],
            "lastName": body['lastName'],
            "dateOfBirth": body.get('dateOfBirth'),
            "email": body.get('email'),
            "metadata": body.get('metadata', {}),
            "timestamp": str(__import__('datetime').datetime.utcnow().isoformat()),
            "environment": ENVIRONMENT
        }
        
        # Start Step Function for async processing
        execution_result = start_step_function(registration_data)
        
        response = {
            "message": "Member registration initiated",
            "memberId": body['memberId'],
            "status": "processing",
            "execution": execution_result
        }
        
        return create_response(200, response)
        
    except json.JSONDecodeError:
        return create_response(400, {
            "error": "Bad Request",
            "message": "Invalid JSON in request body"
        })
    except Exception as e:
        logger.error(f"Error processing registration: {e}")
        return create_response(500, {
            "error": "Internal Server Error",
            "message": "An error occurred processing the request"
        })


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Main Lambda handler function.
    
    Routes requests based on path and HTTP method.
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        dict: API Gateway response
    """
    logger.info(f"Received event: {json.dumps(event, default=str)}")
    
    # Extract request details
    http_method = event.get('httpMethod', 'GET')
    path = event.get('path', '/')
    resource = event.get('resource', '/')
    
    logger.info(f"Processing {http_method} request to {path}")
    
    try:
        # Route based on path
        if path == '/health' or resource == '/health':
            return handle_health_check(event)
            
        elif path == '/test' or resource == '/test':
            return handle_test_endpoint(event)
            
        elif path == '/register/member' or resource == '/register/member':
            if http_method == 'POST':
                return handle_member_registration(event)
            else:
                return create_response(405, {
                    "error": "Method Not Allowed",
                    "message": f"Method {http_method} not allowed for this endpoint"
                })
        
        else:
            return create_response(404, {
                "error": "Not Found",
                "message": f"Endpoint {path} not found"
            })
            
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return create_response(500, {
            "error": "Internal Server Error",
            "message": "An unexpected error occurred"
        })
