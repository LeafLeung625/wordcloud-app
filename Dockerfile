FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖（中文支持）
RUN apt-get update && apt-get install -y \
    gcc \
    fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app.py .
COPY config.py .
COPY gunicorn_config.py .

# 创建必要目录
RUN mkdir -p static logs data

# 暴露端口
EXPOSE 8000

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 使用Gunicorn启动（简单命令）
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
