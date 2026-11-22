# Family Dashboard server. The TV's browser and launcher stay on the Pi itself.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server/requirements.txt server/
RUN pip install --no-cache-dir -r server/requirements.txt
COPY server/ server/
COPY web/ web/
RUN useradd --uid 1000 --create-home dashboard && mkdir /data && chown dashboard /data
USER dashboard
VOLUME /data
EXPOSE 8080
CMD ["python", "server/server.py", "--config", "/data/config.json", "--state-dir", "/data/state"]
