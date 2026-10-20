"""In-memory fake of the subset of the boto3 S3 client used by lambda-tools.

The Gateway tools talk to S3 through ``get_object``, ``put_object``,
``list_objects_v2`` (via paginator), ``delete_object``, and
``generate_presigned_url``. The ``extract_document_text`` tool only uses
``get_object`` + ``put_object``, but this fake supports the broader set so it
can be reused by future tool tests.

It raises the real ``botocore.exceptions.ClientError`` so the tools' real
``except ClientError`` branches (e.g. ``NoSuchKey`` -> ``error(...)``) are
exercised end-to-end.
"""

import io

from botocore.exceptions import ClientError


class FakeS3Client:
    """In-memory fake S3 client for lambda-tools unit tests."""

    def __init__(self):
        # (bucket, key) -> bytes
        self.store = {}
        self.put_calls = []
        self.delete_calls = 0
        self.presigned_calls = 0

    # -- test helpers -------------------------------------------------------

    def seed(self, bucket, key, body):
        """Place an object directly in the store (for arrange-phase setup)."""
        self.store[(bucket, key)] = body if isinstance(body, bytes) else bytes(body)

    def raw_body(self, bucket, key):
        return self.store.get((bucket, key))

    # -- emulated S3 operations --------------------------------------------

    def get_object(self, Bucket, Key, **_kwargs):  # noqa: N803
        body = self.store.get((Bucket, Key))
        if body is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
                "GetObject",
            )
        return {"Body": io.BytesIO(body)}

    def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803
        body_bytes = Body if isinstance(Body, bytes) else bytes(Body)
        self.store[(Bucket, Key)] = body_bytes
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Body": body_bytes, **kwargs})
        return {}

    def delete_object(self, Bucket, Key, **_kwargs):  # noqa: N803
        self.delete_calls += 1
        self.store.pop((Bucket, Key), None)
        return {}

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2", operation_name
        store = self.store

        class _Paginator:
            def paginate(self, Bucket, Prefix="", **_kwargs):  # noqa: N803
                contents = [
                    {"Key": key, "Size": len(body)}
                    for (bucket, key), body in sorted(store.items())
                    if bucket == Bucket and key.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return _Paginator()

    def generate_presigned_url(self, ClientMethod, Params=None, ExpiresIn=None, **_kwargs):  # noqa: N803
        self.presigned_calls += 1
        params = Params or {}
        return (
            f"https://fake-s3.local/{params.get('Bucket')}/{params.get('Key')}"
            f"?op={ClientMethod}&expires={ExpiresIn}"
        )
