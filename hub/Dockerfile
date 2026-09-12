FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g configurable-http-proxy@4 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
COPY hub/requirements.txt /tmp/ldap-requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/ldap-requirements.txt
COPY common/api_keys.py common/resource_limits.py common/dispatch_queue.py /srv/jupyterhub/
COPY hub /srv/jupyterhub
WORKDIR /data
EXPOSE 8000
CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
