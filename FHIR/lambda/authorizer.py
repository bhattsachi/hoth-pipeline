"""
Lambda Authorizer for API Gateway
Validates JWT tokens with Okta for API authentication
"""

import json
import logging
import os
import re
import urllib.request
import urllib.error
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
log_level = os.environ.get('LOG_LEVEL', 'INFO')
logger.setLevel(getattr(logging, log_level))

# Environment variables
OKTA_SECRET_ARN = os.environ.get('OKTA_SECRET_ARN')
OKTA_DOMAIN = os.environ.get('OKTA_DOMAIN', '')

# AWS clients
secrets_client = boto3.client('secretsmanager')

# Cache for Okta configuration
_okta_config_cache = None


def get_okta_config() -> dict:
    """
    Retrieve Okta configuration from Secrets Manager.
    Uses caching to minimize API calls.
    
    Returns:
        dict: Okta configuration including client_id, audience, issuer
    """
    global _okta_config_cache
    
    if _okta_config_cache:
        return _okta_config_cache
    
    if not OKTA_SECRET_ARN:
        raise ValueError("OKTA_SECRET_ARN environment variable not set")
    
    try:
        response = secrets_client.get_secret_value(SecretId=OKTA_SECRET_ARN)
        
        if 'SecretString' in response:
            _okta_config_cache = json.loads(response['SecretString'])
        else:
            import base64
            _okta_config_cache = json.loads(base64.b64decode(response['SecretBinary']))
        
        logger.info("Successfully retrieved Okta configuration from Secrets Manager")
        return _okta_config_cache
        
    except ClientError as e:
        logger.error(f"Error retrieving Okta config from Secrets Manager: {e}")
        raise


def extract_token(event: dict) -> Optional[str]:
    """
    Extract JWT token from the Authorization header.
    
    Args:
        event: API Gateway authorizer event
        
    Returns:
        str: JWT token or None if not found
    """
    auth_header = event.get('authorizationToken', '')
    
    if not auth_header:
        logger.warning("No Authorization header provided")
        return None
    
    # Handle Bearer token format
    if auth_header.lower().startswith('bearer '):
        token = auth_header[7:].strip()
    else:
        token = auth_header.strip()
    
    if not token:
        logger.warning("Empty token after extraction")
        return None
    
    # Basic JWT format validation (three parts separated by dots)
    if not re.match(r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$', token):
        logger.warning("Token does not match JWT format")
        return None
    
    return token


def extract_client_id(event: dict) -> Optional[str]:
    """
    Extract client ID from headers or query parameters.
    
    Args:
        event: API Gateway authorizer event
        
    Returns:
        str: Client ID or None if not found
    """
    # Try to get from headers (passed through methodArn or additional context)
    headers = event.get('headers', {}) or {}
    
    # Check various header formats
    client_id = headers.get('x-client-id') or headers.get('X-Client-Id') or headers.get('X-CLIENT-ID')
    
    if client_id:
        return client_id
    
    # Try query string parameters
    query_params = event.get('queryStringParameters', {}) or {}
    return query_params.get('client_id')


def validate_token_with_okta(token: str, okta_config: dict) -> dict:
    """
    Validate JWT token with Okta introspection endpoint.
    
    Args:
        token: JWT token to validate
        okta_config: Okta configuration from Secrets Manager
        
    Returns:
        dict: Token introspection response from Okta
        
    Raises:
        Exception: If token validation fails
    """
    issuer = okta_config.get('issuer', f"https://{OKTA_DOMAIN}/oauth2/default")
    client_id = okta_config.get('client_id')
    client_secret = okta_config.get('client_secret')
    
    # Okta introspect endpoint
    introspect_url = f"{issuer}/v1/introspect"
    
    # Prepare request data
    data = {
        'token': token,
        'token_type_hint': 'access_token'
    }
    
    encoded_data = urllib.parse.urlencode(data).encode('utf-8')
    
    # Create request with Basic Auth
    import base64
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'Authorization': f'Basic {credentials}'
    }
    
    try:
        request = urllib.request.Request(
            introspect_url,
            data=encoded_data,
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode())
            logger.info(f"Okta introspection response: active={result.get('active')}")
            return result
            
    except urllib.error.HTTPError as e:
        logger.error(f"Okta introspection HTTP error: {e.code} - {e.reason}")
        raise Exception(f"Token validation failed: {e.reason}")
    except urllib.error.URLError as e:
        logger.error(f"Okta introspection URL error: {e.reason}")
        raise Exception(f"Unable to reach Okta: {e.reason}")
    except Exception as e:
        logger.error(f"Okta introspection error: {e}")
        raise


def validate_audience_and_scope(introspection_result: dict, okta_config: dict) -> bool:
    """
    Validate the token audience and scopes.
    
    Args:
        introspection_result: Okta introspection response
        okta_config: Okta configuration
        
    Returns:
        bool: True if validation passes
    """
    expected_audience = okta_config.get('audience')
    required_scopes = okta_config.get('required_scopes', [])
    
    # Validate audience if configured
    if expected_audience:
        token_audience = introspection_result.get('aud')
        if isinstance(token_audience, list):
            if expected_audience not in token_audience:
                logger.warning(f"Invalid audience: expected {expected_audience}, got {token_audience}")
                return False
        elif token_audience != expected_audience:
            logger.warning(f"Invalid audience: expected {expected_audience}, got {token_audience}")
            return False
    
    # Validate required scopes if configured
    if required_scopes:
        token_scopes = introspection_result.get('scope', '').split()
        missing_scopes = [s for s in required_scopes if s not in token_scopes]
        if missing_scopes:
            logger.warning(f"Missing required scopes: {missing_scopes}")
            return False
    
    return True


def generate_policy(principal_id: str, effect: str, resource: str, context: dict = None) -> dict:
    """
    Generate IAM policy document for API Gateway.
    
    Args:
        principal_id: User/client identifier
        effect: 'Allow' or 'Deny'
        resource: API Gateway method ARN
        context: Additional context to pass to the API
        
    Returns:
        dict: API Gateway authorizer policy document
    """
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        }
    }
    
    if context:
        policy['context'] = context
    
    return policy


def generate_allow_policy(principal_id: str, resource: str, context: dict = None) -> dict:
    """Generate Allow policy."""
    return generate_policy(principal_id, 'Allow', resource, context)


def generate_deny_policy(principal_id: str, resource: str, context: dict = None) -> dict:
    """Generate Deny policy."""
    return generate_policy(principal_id, 'Deny', resource, context)


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Lambda authorizer handler function.
    
    Flow:
    1. Extract token from Authorization header
    2. Extract client_id from headers
    3. Validate input request/event
    4. Retrieve Okta config from Secrets Manager (client_id, audience, etc.)
    5. Call Okta introspection endpoint to verify token
    6. Return Allow or Deny policy
    
    Args:
        event: API Gateway authorizer event
        context: Lambda context
        
    Returns:
        dict: API Gateway authorizer policy
    """
    logger.info(f"Authorizer invoked with event type: {event.get('type')}")
    logger.debug(f"Full event: {json.dumps(event, default=str)}")
    
    method_arn = event.get('methodArn', '*')
    
    try:
        # Step 1: Extract token from Authorization header
        token = extract_token(event)
        
        if not token:
            logger.warning("No valid token found in request")
            return generate_deny_policy('anonymous', method_arn, {
                'error': 'Missing or invalid authorization token'
            })
        
        # Step 2: Extract client_id from headers (optional but logged)
        client_id_header = extract_client_id(event)
        logger.info(f"Client ID from header: {client_id_header or 'not provided'}")
        
        # Step 3: Validate basic input requirements
        if not OKTA_SECRET_ARN:
            logger.error("OKTA_SECRET_ARN not configured")
            return generate_deny_policy('system', method_arn, {
                'error': 'Authorization service not configured'
            })
        
        # Step 4: Retrieve Okta configuration from Secrets Manager
        okta_config = get_okta_config()
        
        if not okta_config.get('client_id') or not okta_config.get('client_secret'):
            logger.error("Incomplete Okta configuration in Secrets Manager")
            return generate_deny_policy('system', method_arn, {
                'error': 'Authorization service misconfigured'
            })
        
        # Step 5: Validate token with Okta
        introspection_result = validate_token_with_okta(token, okta_config)
        
        # Check if token is active
        if not introspection_result.get('active', False):
            logger.warning("Token is not active (expired or revoked)")
            return generate_deny_policy('invalid_token', method_arn, {
                'error': 'Token is not active'
            })
        
        # Validate audience and scopes
        if not validate_audience_and_scope(introspection_result, okta_config):
            logger.warning("Token failed audience or scope validation")
            return generate_deny_policy('invalid_token', method_arn, {
                'error': 'Token failed validation'
            })
        
        # Step 6: Token is valid - return Allow policy
        principal_id = introspection_result.get('sub', introspection_result.get('uid', 'user'))
        
        context_data = {
            'principalId': principal_id,
            'clientId': introspection_result.get('client_id', client_id_header or 'unknown'),
            'scope': introspection_result.get('scope', ''),
            'tokenType': introspection_result.get('token_type', 'Bearer')
        }
        
        logger.info(f"Authorization successful for principal: {principal_id}")
        
        # Generate wildcard resource for caching
        # This allows the policy to be cached for all methods
        parts = method_arn.split(':')
        api_gateway_arn = ':'.join(parts[:5])
        api_id_stage_parts = parts[5].split('/')
        wildcard_resource = f"{api_gateway_arn}:{api_id_stage_parts[0]}/{api_id_stage_parts[1]}/*"
        
        return generate_allow_policy(principal_id, wildcard_resource, context_data)
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return generate_deny_policy('system', method_arn, {
            'error': 'Authorization service configuration error'
        })
    except Exception as e:
        logger.error(f"Authorization error: {e}", exc_info=True)
        return generate_deny_policy('error', method_arn, {
            'error': 'Authorization failed due to internal error'
        })
