FROM python

WORKDIR /dockApp

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY algo.py .
COPY client.py .
COPY api_client.py .
COPY graph.py .

COPY cache ./cache
COPY graph_imgs ./graph_imgs

CMD ["python", "-u", "client.py"]

#docker run -v "C:\DockerData\entsoe\graph_imgs:/dockApp/graph_imgs" entsoe