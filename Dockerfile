FROM docker.litellm.ai/berriai/litellm:1.104.0
WORKDIR /app
COPY config.yaml /app/config.yaml
EXPOSE 8080/tcp
CMD ["--port", "8080", "--config", "/app/config.yaml"]
