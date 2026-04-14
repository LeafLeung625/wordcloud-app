FROM python:3.9-slim

WORKDIR /app

# 复制并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# Railway 使用 10000 端口
EXPOSE 10000

# 直接用 python 运行，避免 gunicorn 日志问题
CMD ["python", "app.py"]
