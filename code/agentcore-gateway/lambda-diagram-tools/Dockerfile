FROM public.ecr.aws/lambda/python:3.10

# Install system dependencies
RUN yum install -y \
    graphviz \
    graphviz-devel \
    && yum clean all

# Install Python dependencies
RUN pip install --no-cache-dir \
    boto3 \
    diagrams \
    graphviz

# Copy Lambda handler
COPY handler.py ${LAMBDA_TASK_ROOT}/

# Set the Lambda handler
CMD ["handler.lambda_handler"]
