# lambda-tools tests

Unit tests for the Gateway Lambda Tools handlers under
`code/agentcore-gateway/lambda-tools/`.

- **`extract_document_text`** (spec task 6.1) —
  `test_extract_document_text.py`. Drives the tool with the in-memory
  `FakeS3Client`, asserting the three branches of the design's Document_Extractor
  (Section 7b, requirements 5.2-5.3): `.md`/`.txt` passthrough, `.pdf`
  extraction (real `pypdf` against a real PDF byte stream), `.docx` extraction
  (real `python-docx` against a real DOCX byte stream). Also asserts the
  normalized text is written back to `.../uploaded/extracted/<filename>.txt`.

## Running

`boto3`, `botocore`, `pypdf`, `python-docx`, and `pytest` are required.

```bash
cd code/agentcore-gateway/lambda-tools
python3 -m venv .venv && source .venv/bin/activate
pip install boto3 pytest -r requirements.txt
pytest tests/ -v
```
