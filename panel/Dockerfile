FROM python:3.12-slim
COPY panel/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 1000 panel
COPY common/dispatch_queue.py common/api_keys.py common/resource_limits.py /opt/common/
COPY panel/app.py /opt/app/app.py
WORKDIR /opt/app
USER panel
EXPOSE 8090
CMD ["python3", "app.py"]
