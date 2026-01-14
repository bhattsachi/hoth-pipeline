# Repository structure 
Cloudformation
|       pipeline.yaml
|
\---FHIR
    |   buildspec.yaml
    |   dev.json
    |   hoth_migration_components.yaml
    |   prod.json
    |   test.json
    |
    +---api-gateway
    |       fhir_register_member_api.yaml
    |
    +---glue-infra
    |       template.yaml
    |
    +---lambda
    |       app.py
    |       authorizer.py
    |
    +---secrets-manager
    |       fhir_migration_secrets.yaml
    |
    \---step-function-eventbridge
            template.yaml

# Instruction
Objective
Design and implement a CI/CD pipeline using AWS CodePipeline and CloudFormation (with AWS SAM) that provisions and deploys a simple HOTH application.
You will build a CodePipeline with three stages:
• Source
• Build
• Deploy

The pipeline must deploy a serverless REST API composed of:
1. AWS Lambda
2. AWS Secrets Manager
3. Amazon API Gateway (REST API)
4. AWS Step function
5. AWS Glue jobs
6. AWS Authorizer for api endpoint


All infrastructure must be defined using CloudFormation and AWS SAM.
Manual resource creation via the AWS Console is not allowed.

  Deliverables 
  1. Cloudformation template (pipeline.yaml)
  2. Master SAM Template (hoth_migration_components.yaml) - This file have reference to all other individual template(.yaml) files
  3. API Gateway (fhir_register_member_api.yaml file should have the template for api resources )
  4. lambda (it should have app.py and authorizer.py )
  5. glue jobs (glue jobs template.yaml under glue-infra folder)
  6. step function (template.yaml under step-function-eventbridge folder)
  7. AWS Lambda
        •	Runtime: Python 3.12
        •	A simple handler that:
        –	Reads a secret from AWS Secrets Manager
        –	Returns a JSON response
        •	Proper IAM permissions must be defined using least privilege
  8. AWS Secrets Manager
        •	A secret must be created via CloudFormation
        •	The Lambda function must reference the secret using:
        –	Environment variables
        –	IAM permissions (no hard-coded values)
  9. API Gateway (REST API)
        •	Must be created using AWS SAM
        •	Must expose at least one endpoint:
        –	Method: POST
        –	Path: /health or /test
        •	API Gateway must integrate with the Lambda function
        •	Lambda proxy integration is acceptable 
  10. 
      . API gateway receives request. 
      .  In first place API will send the request to Authorizer lambda with headers (token, clientid and more)
      .  Authorizer lambda will do initial validation on input request/event and extract token.
      .  To verify token Authorizer will call SM to get client_id, Audience and some others which are require to call okta endpoint for token verification.
      .  Once Authorizer got all the details from SM. It will call okta API for verify token is valid or not 
      .  If valid return allow else deny back to API Gateway
      .  Returns allow/deny.