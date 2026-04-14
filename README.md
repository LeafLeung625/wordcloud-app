# 互动词云应用

一个基于 Flask 的实时词云生成应用，支持文本提交、自动分词和词云可视化展示。

## 功能特点

- ✅ 文本提交与实时分词
- ✅ 自动生成词云图
- ✅ 每5秒自动刷新词云
- ✅ 实时统计显示
- ✅ 多种配色方案
- ✅ 词云图片下载

## 技术栈

- Python 3.8+
- Flask 2.3.3
- jieba 分词
- wordcloud 词云
- Gunicorn (生产环境)

## 快速开始

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 启动应用
python app.py
```

访问 http://127.0.0.1:5000

### Docker 部署

```bash
docker-compose up -d
```

## 部署到 Railway（免费）

1. 将此仓库上传到 GitHub
2. 访问 https://railway.app
3. 使用 GitHub 登录
4. 创建新项目 → Deploy from GitHub
5. 选择此仓库，等待部署完成

## 项目结构

```
├── app.py                 # 主应用
├── config.py             # 配置文件
├── requirements.txt       # Python 依赖
├── gunicorn_config.py    # Gunicorn 配置
├── Dockerfile            # Docker 配置
├── docker-compose.yml    # Docker Compose 配置
└── Procfile             # Heroku 配置
```

## API 接口

- `POST /api/submit` - 提交文本
- `GET /api/stats` - 获取统计
- `GET /api/wordcloud/data` - 获取词云数据

## 许可证

MIT License
