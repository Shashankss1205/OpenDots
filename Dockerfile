FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 opendots
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY opendots ./opendots
COPY spots ./spots
RUN python -m pip install --no-cache-dir . \
    && mkdir /data && chown opendots:opendots /data
COPY --chmod=755 deploy/docker-entrypoint.sh /usr/local/bin/opendots-entrypoint
USER opendots
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["opendots-entrypoint"]
CMD ["serve", "--host", "0.0.0.0"]
