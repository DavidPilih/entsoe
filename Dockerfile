FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Ljubljana

WORKDIR /dockApp

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY client_params.py algo.py api_client.py graph.py ./

RUN mkdir -p cache sun_data graph_imgs

CMD ["python", "-u", "client_params.py"]

# docker run --env-file .env -v "C:\DockerData\entsoe\graph_imgs:/dockApp/graph_imgs" entsoe
