import boto3
import cfnresponse
import time


def handler(event, context):
    try:
        rt = event["RequestType"]
        props = event.get("ResourceProperties", {})
        source_name = props.get("SourceName", "")
        dest_name = props.get("DestName", "")
        log_type = props.get("LogType", "")
        resource_arn = props.get("ResourceArn", "")
        log_group_arn = props.get("LogGroupArn", "")
        region = props.get("Region", "us-west-2")

        logs = boto3.client("logs", region_name=region)

        if rt == "Delete":
            # Delete delivery first, then destination, then source
            try:
                deliveries = logs.describe_deliveries()
                for d in deliveries.get("deliveries", []):
                    if (
                        d.get("deliverySourceName") == source_name
                        and d.get("deliveryDestinationArn", "").endswith(dest_name)
                    ):
                        logs.delete_delivery(id=d["id"])
                        time.sleep(2)  # nosemgrep: arbitrary-sleep — wait for delivery deletion to propagate
            except Exception as e:
                print(f"delete_delivery: {e}")
            try:
                logs.delete_delivery_destination(name=dest_name)
            except Exception as e:
                print(f"delete_delivery_destination: {e}")
            try:
                logs.delete_delivery_source(name=source_name)
            except Exception as e:
                print(f"delete_delivery_source: {e}")
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return

        # Create or Update
        # 1. Delivery source (idempotent via put)
        logs.put_delivery_source(
            name=source_name,
            logType=log_type,
            resourceArn=resource_arn,
        )

        # 2. Delivery destination (idempotent via put)
        dest_resp = logs.put_delivery_destination(
            name=dest_name,
            deliveryDestinationType="CWL",
            deliveryDestinationConfiguration={
                "destinationResourceArn": log_group_arn,
            },
        )
        dest_arn = dest_resp["deliveryDestination"]["arn"]

        # 3. Check if delivery already exists
        delivery_id = None
        deliveries = logs.describe_deliveries()
        for d in deliveries.get("deliveries", []):
            if d.get("deliverySourceName") == source_name:
                delivery_id = d["id"]
                break

        if not delivery_id:
            create_resp = logs.create_delivery(
                deliverySourceName=source_name,
                deliveryDestinationArn=dest_arn,
            )
            delivery_id = create_resp["delivery"]["id"]

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            "DeliveryId": delivery_id,
        })
    except Exception as e:
        print(f"Error: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})
