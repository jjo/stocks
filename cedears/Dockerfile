FROM python:3.8-slim as compile-image
RUN apt-get update && apt-get install -y --no-install-recommends build-essential gcc && apt-get clean
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . /app
RUN patch -d /opt/venv -p0 -i /app/quart-asgi.diff

FROM python:3.8-slim as runtime-image
COPY --from=compile-image /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY . /app
WORKDIR /app
ENTRYPOINT ["/app/server.py"]
