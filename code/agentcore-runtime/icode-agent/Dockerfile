FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

EXPOSE 8080

CMD ["opentelemetry-instrument", "python", "-m", "main_ws"]
