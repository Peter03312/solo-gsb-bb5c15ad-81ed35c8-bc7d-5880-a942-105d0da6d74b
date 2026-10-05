FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/drill.db \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 数据卷：SQLite 文件及其 WAL 文件所在目录
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

# 容器启动即幂等建库，随后拉起 API；compose 中另有独立 init 服务
CMD ["sh", "-c", "python -m app.init_db && uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT}"]
