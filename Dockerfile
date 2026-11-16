FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --system app && mkdir /data && chown app /data
USER app
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "freezerhost.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
