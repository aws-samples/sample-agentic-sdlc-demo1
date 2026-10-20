import boto3
import time
import cfnresponse

codebuild = boto3.client('codebuild')

def wait_for_build(build_id, build_name):
    """Wait for a CodeBuild project to complete"""
    print(f'Waiting for {build_name} build: {build_id}')
    
    while True:
        response = codebuild.batch_get_builds(ids=[build_id])
        build = response['builds'][0]
        status = build['buildStatus']
        
        print(f'{build_name}: {status}')
        
        if status == 'SUCCEEDED':
            print(f'{build_name} completed successfully!')
            return True
        elif status in ['FAILED', 'FAULT', 'TIMED_OUT', 'STOPPED']:
            if 'logs' in build and 'deepLink' in build['logs']:
                print(f'{build_name} failed. Logs: {build["logs"]["deepLink"]}')
            return False
        elif status in ['IN_PROGRESS']:
            time.sleep(15)  # nosemgrep: arbitrary-sleep — polling for CodeBuild completion
        else:
            time.sleep(5)  # nosemgrep: arbitrary-sleep — polling for CodeBuild completion

def handler(event, context):
    """Custom resource handler to trigger Agent build"""
    try:
        request_type = event['RequestType']
        print(f'Request type: {request_type}')
        
        if request_type == 'Delete':
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return
        
        project_name = event['ResourceProperties']['ProjectName']
        
        print(f'Starting Docker build: {project_name}')
        response = codebuild.start_build(projectName=project_name)
        build_id = response['build']['id']
        print(f'Build ID: {build_id}')
        
        if not wait_for_build(build_id, 'Agent'):
            error_msg = f'Agent build failed: {build_id}'
            print(error_msg)
            cfnresponse.send(event, context, cfnresponse.FAILED, {
                'Error': error_msg,
                'BuildId': build_id
            })
            return
        
        print('Docker image built successfully!')
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            'Message': 'Docker image built successfully',
            'BuildId': build_id
        })
        
    except Exception as e:
        error_msg = f'Build trigger failed: {str(e)}'
        print(error_msg)
        import traceback
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': error_msg
        })