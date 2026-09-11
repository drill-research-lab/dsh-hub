FROM python:3.12-slim
COPY dispatcher/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 1000 dispatcher
COPY common/dispatch_queue.py common/api_keys.py /opt/common/
COPY dispatcher/app.py /opt/app/app.py
WORKDIR /opt/app
USER dispatcher
EXPOSE 8080
CMD ["python3", "app.py"]
