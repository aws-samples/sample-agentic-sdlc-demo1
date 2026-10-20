"""Vendored copy of the AWS cfn-response module.

AWS auto-injects the ``cfnresponse`` module ONLY for *inline* (ZipFile)
custom-resource Lambdas. Several custom-resource handlers in this directory are
packaged from S3 assets (``Code.fromAsset``) instead of inline, to keep the
synthesized CloudFormation template under its hard 1,000,000-byte limit. When a
handler is packaged as an asset, ``import cfnresponse`` no longer resolves
automatically, so this module is bundled alongside them. The implementation
mirrors the public AWS cfn-response source.
"""

from __future__ import print_function

import json
import urllib.request

SUCCESS = "SUCCESS"
FAILED = "FAILED"


def send(
    event,
    context,
    responseStatus,
    responseData,
    physicalResourceId=None,
    noEcho=False,
    reason=None,
):
    """POST a custom-resource response to the pre-signed CloudFormation URL."""
    responseUrl = event["ResponseURL"]

    responseBody = {
        "Status": responseStatus,
        "Reason": reason
        or "See the details in CloudWatch Log Stream: {}".format(
            context.log_stream_name
        ),
        "PhysicalResourceId": physicalResourceId or context.log_stream_name,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "NoEcho": noEcho,
        "Data": responseData,
    }

    json_responseBody = json.dumps(responseBody)
    headers = {
        "content-type": "",
        "content-length": str(len(json_responseBody)),
    }

    try:
        req = urllib.request.Request(
            responseUrl,
            data=json_responseBody.encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        urllib.request.urlopen(req)  # nosemgrep: dynamic-urllib-use-detected  # nosec B310 — fixed AWS CloudFormation pre-signed HTTPS response URL
    except Exception as exc:  # noqa: BLE001
        print("cfnresponse.send failed:", exc)
