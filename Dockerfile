FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git bubblewrap && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
CMD ["python", "-m", "opendots", "serve", "--host", "0.0.0.0"]
