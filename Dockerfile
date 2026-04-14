FROM python:3.9-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY app.py .
COPY config.py .

# Railway 会自动设置 PORT 环境变量，默认使用 8000
ENV PORT=8000

EXPOSE 8000

# 直接运行 python 应用
CMD ["python", "app.py"]
