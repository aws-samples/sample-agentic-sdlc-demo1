import json
import boto3
import cfnresponse


def handler(event, context):
    try:
        request_type = event["RequestType"]
        props = event.get("ResourceProperties", {})

        if request_type == "Delete":
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return

        region = props.get("Region", "us-west-2")
        account_id = props.get("AccountId")
        sampling_pct = int(props.get("SamplingPercentage", 100))

        logs_client = boto3.client("logs", region_name=region)
        xray_client = boto3.client("xray", region_name=region)

        # 1. Resource policy (putResourcePolicy is idempotent)
        policy_doc = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "TransactionSearchXRayAccess",
                "Effect": "Allow",
                "Principal": {"Service": "xray.amazonaws.com"},
                "Action": "logs:PutLogEvents",
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:aws/spans:*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/application-signals/data:*",
                ],
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{region}:{account_id}:*"},
                    "StringEquals": {"aws:SourceAccount": account_id},
                },
            }],
        })
        logs_client.put_resource_policy(
            policyName="AgentCoreTransactionSearchXRayAccess",
            policyDocument=policy_doc,
        )

        # 2. Trace segment destination (check before mutating)
        dest_resp = xray_client.get_trace_segment_destination()
        current_dest = dest_resp.get("Destination", "")
        # Destination can be a string like "CloudWatchLogs" or a dict with a Status key
        if isinstance(current_dest, dict):
            current_dest = current_dest.get("Status", "")
        if current_dest != "CloudWatchLogs":
            xray_client.update_trace_segment_destination(Destination="CloudWatchLogs")

        # 3. Indexing rule (check before mutating)
        rules_resp = xray_client.get_indexing_rules()
        rules = rules_resp.get("IndexingRules", [])
        needs_update = True
        for rule in rules:
            prob = rule.get("Rule", {}).get("Probabilistic", {})
            if prob.get("ActualSamplingPercentage") == sampling_pct:
                needs_update = False
                break
        if needs_update:
            xray_client.update_indexing_rule(
                Name="Default",
                Rule={"Probabilistic": {"DesiredSamplingPercentage": sampling_pct}},
            )

        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            "Destination": "CloudWatchLogs",
            "SamplingPercentage": str(sampling_pct),
        })
    except Exception as e:
        print(f"Error: {e}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {"Error": str(e)})
