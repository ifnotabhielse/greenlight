FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY greenlight/ ./greenlight/
ENV GREENLIGHT_SIMULATE=true
EXPOSE 9100
CMD ["kopf", "run", "-m", "greenlight.controller", "--all-namespaces"]
