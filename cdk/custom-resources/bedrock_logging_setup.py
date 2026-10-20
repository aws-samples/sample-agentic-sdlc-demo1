import boto3
import cfnresponse


def handler(event, context):
    try:
        request_type = event["RequestType"]
        props = event.get("ResourceProperties", {})

        if request_type == "Delete":
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return

        region = props["Region"]
        log_group_name = props["LogGroupName"]
        s3_bucket_name = props["S3BucketName"]
        role_arn = props["RoleArn"]
        retention_days = int(props.get("RetentionDays", 30))

        logs_client = boto3.client("logs", region_name=region)
        bedrock = boto3.client("bedrock", region_name=region)

        # 1. Create log group if it doesn't exist
        try:
            logs_client.create_log_group(logGroupName=log_group_name)
            print(f"Created log group: {log_group_name}")
        except logs_client.exceptions.ResourceAlreadyExistsException:
            print(f"Log group already exists: {log_group_name}")

        # Ensure retention is set
        try:
            logs_client.put_retention_policy(
                logGroupName=log_group_name,
                retentionInDays=retention_days,
            )
        except Exception as e:
            print(f"put_retention_policy: {e}")

        # 2. Configure Bedrock model invocation logging
        desired = {
            "cloudWatchConfig": {
                "logGroupName": log_group_name,
                "roleArn": role_arn,
                "largeDataDeliveryS3Config": {
                    "bucketName": s3_bucket_name,
                },
            },
            "s3Config": {
                "bucketName": s3_bucket_name,
            },
            "textDataDeliveryEnabled": True,
            "imageDataDeliveryEnabled": True,
            "embeddingDataDeliveryEnabled": True,
            "videoDataDeliveryEnabled": True,
        }

        try:
            resp = bedrock.get_model_invocation_logging_configuration()
            current = resp.get("loggingConfig")
        except Exception:
            current = None

        needs_update = current is None
        if not needs_update:
            cw = current.get("cloudWatchConfig") or {}
            s3c = current.get("s3Config") or {}
            needs_update = (
                cw.get("logGroupName") != log_group_name
                or cw.get("roleArn") != role_arn
                or s3c.get("bucketName") != s3_bucket_name
                or not current.get("textDataDeliveryEnabled")
                or not current.get("imageDataDeliveryEnabled")
                or not current.get("embeddingDataDeliveryEnabled")
            )

        if needs_update:
            bedrock.put_model_invocation_logging_configuration(loggingConfig=desired)
            status = "CONFIGURED"
        else:
            status = "ALREADY_CONFIGURED"

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            "Status": status,
        })
    except Exception as e:
        print(f"Error: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})
