FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖和中文语言包
RUN apt-get update && apt-get install -y \
    gcc \
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app.py .
COPY config.py .
COPY gunicorn_config.py .

# 创建空目录
RUN mkdir -p static logs data

# 暴露端口
EXPOSE 5000

# 设置环境变量
ENV FLASK_APP=app.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# 使用Gunicorn启动
CMD ["gunicorn", "-c", "gunicorn_config.py", "app:app"]
