FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY prism ./prism
COPY config ./config
COPY docs ./docs
COPY fixtures ./fixtures
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["prism", "serve", "--host", "0.0.0.0", "--port", "8000"]
