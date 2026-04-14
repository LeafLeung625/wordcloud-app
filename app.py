from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for
import jieba
import jieba.analyse
from wordcloud import WordCloud, ImageColorGenerator
from collections import Counter
import io
import base64
from datetime import datetime
import os
from PIL import Image
import numpy as np
import json
import re
import sqlite3
from threading import Lock
import uuid

# 加载配置
try:
    from config import config
    env = os.environ.get('FLASK_ENV', 'development')
    app_config = config.get(env, config['default'])
except ImportError:
    # 如果没有配置文件，使用默认配置
    class DefaultConfig:
        SECRET_KEY = 'your-secret-key-change-in-production'
        HOST = '0.0.0.0'
        PORT = 5000
        DEBUG = True
        MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    app_config = DefaultConfig()

# 初始化Flask应用
app = Flask(__name__)
app.config.from_object(app_config)


# 数据库初始化
def init_db():
    conn = sqlite3.connect('wordcloud.db')
    c = conn.cursor()
    c.execute('''
              CREATE TABLE IF NOT EXISTS submissions
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  text
                  TEXT
                  NOT
                  NULL,
                  timestamp
                  DATETIME
                  DEFAULT
                  CURRENT_TIMESTAMP,
                  ip_address
                  TEXT
              )
              ''')
    c.execute('''
              CREATE TABLE IF NOT EXISTS word_frequencies
              (
                  id
                  INTEGER
                  PRIMARY
                  KEY
                  AUTOINCREMENT,
                  word
                  TEXT
                  NOT
                  NULL,
                  frequency
                  INTEGER
                  DEFAULT
                  1,
                  last_updated
                  DATETIME
                  DEFAULT
                  CURRENT_TIMESTAMP
              )
              ''')
    conn.commit()
    conn.close()


# 初始化数据库
init_db()

# 线程锁用于并发控制
db_lock = Lock()

# 配置jieba分词
jieba.setLogLevel(20)  # 减少日志输出
# 加载用户词典（可选）
# jieba.load_userdict("user_dict.txt")

# 停用词列表（可扩展）
STOPWORDS = set([
    '的', '了', '在', '是', '我', '有', '和', '就',
    '不', '人', '都', '一', '一个', '上', '也', '很',
    '到', '说', '要', '去', '你', '会', '着', '没有',
    '看', '好', '自己', '这', '那', '他', '她', '它'
])


def process_text(text):
    """处理文本，提取关键词并统计词频"""
    # 去除标点符号和特殊字符
    text = re.sub(r'[^\w\s\u4e00-\u9fff]', '', text)

    # 使用jieba进行分词
    words = jieba.lcut(text)

    # 过滤停用词和单字
    filtered_words = [
        word for word in words
        if word not in STOPWORDS
           and len(word) > 1
           and not word.isdigit()
    ]

    return filtered_words


def get_word_frequencies():
    """从数据库获取所有词语的频率"""
    with db_lock:
        conn = sqlite3.connect('wordcloud.db')
        c = conn.cursor()

        # 获取所有提交的文本
        c.execute("SELECT text FROM submissions")
        all_texts = c.fetchall()

        # 处理所有文本
        all_words = []
        for (text,) in all_texts:
            all_words.extend(process_text(text))

        # 统计词频
        word_counter = Counter(all_words)

        # 更新数据库中的词频
        for word, freq in word_counter.items():
            c.execute(
                "INSERT OR REPLACE INTO word_frequencies (word, frequency, last_updated) "
                "VALUES (?, COALESCE((SELECT frequency FROM word_frequencies WHERE word = ?), 0) + ?, CURRENT_TIMESTAMP)",
                (word, word, freq)
            )

        conn.commit()

        # 获取最新的词频数据
        c.execute("SELECT word, frequency FROM word_frequencies ORDER BY frequency DESC LIMIT 100")
        word_freq = dict(c.fetchall())

        conn.close()

    return word_freq


def generate_wordcloud_image(word_freq, width=800, height=600):
    """生成词云图片并返回base64编码"""
    if not word_freq:
        # 如果没有数据，返回默认图片
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (width, height), color='white')
        d = ImageDraw.Draw(img)
        # 这里可以添加默认文字
        return img_to_base64(img)

    # 创建词云对象
    wordcloud = WordCloud(
        font_path='SimHei.ttf' if os.path.exists('SimHei.ttf') else None,
        width=width,
        height=height,
        background_color='white',
        max_words=100,
        max_font_size=150,
        min_font_size=10,
        random_state=42,
        colormap='viridis',  # 可以使用'plasma', 'inferno', 'magma', 'cividis'等
        prefer_horizontal=0.9,  # 水平文字的比例
        scale=2  # 提高分辨率
    )

    # 生成词云
    wordcloud.generate_from_frequencies(word_freq)

    # 转换为PIL图片
    img = wordcloud.to_image()

    return img_to_base64(img)


def img_to_base64(img):
    """将PIL图片转换为base64字符串"""
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str


@app.route('/')
def index():
    """首页 - 文本录入界面"""
    return render_template_string(INDEX_TEMPLATE)


@app.route('/cloud')
def cloud():
    """词云展示页面"""
    word_freq = get_word_frequencies()
    wordcloud_img = generate_wordcloud_image(word_freq)

    # 将词频数据转换为前端需要的格式
    word_data = [{'text': word, 'value': freq} for word, freq in word_freq.items()]

    return render_template_string(
        CLOUD_TEMPLATE,
        wordcloud_img=wordcloud_img,
        word_data=json.dumps(word_data, ensure_ascii=False)
    )


@app.route('/api/submit', methods=['POST'])
def submit_text():
    """API接口：提交文本"""
    data = request.get_json()
    text = data.get('text', '').strip()

    if not text or len(text) < 2:
        return jsonify({'success': False, 'message': '文本太短'})

    if len(text) > 1000:
        return jsonify({'success': False, 'message': '文本过长（最多1000字）'})

    # 保存到数据库
    with db_lock:
        conn = sqlite3.connect('wordcloud.db')
        c = conn.cursor()
        c.execute(
            "INSERT INTO submissions (text, ip_address) VALUES (?, ?)",
            (text, request.remote_addr)
        )
        conn.commit()
        conn.close()

    return jsonify({
        'success': True,
        'message': '提交成功！词云已更新',
        'word_count': len(text)
    })


@app.route('/api/wordcloud/data')
def get_wordcloud_data():
    """API接口：获取词云数据"""
    word_freq = get_word_frequencies()
    wordcloud_img = generate_wordcloud_image(word_freq)

    word_data = [{'text': word, 'value': freq} for word, freq in word_freq.items()]

    return jsonify({
        'success': True,
        'wordcloud_img': wordcloud_img,
        'word_data': word_data
    })


@app.route('/api/stats')
def get_stats():
    """API接口：获取统计信息"""
    with db_lock:
        conn = sqlite3.connect('wordcloud.db')
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM submissions")
        total_submissions = c.fetchone()[0]

        c.execute("SELECT COUNT(DISTINCT word) FROM word_frequencies")
        unique_words = c.fetchone()[0]

        c.execute("SELECT SUM(frequency) FROM word_frequencies")
        total_words = c.fetchone()[0] or 0

        c.execute("""
                  SELECT word, frequency
                  FROM word_frequencies
                  ORDER BY frequency DESC LIMIT 10
                  """)
        top_words = [{'word': row[0], 'frequency': row[1]} for row in c.fetchall()]

        conn.close()

    return jsonify({
        'success': True,
        'total_submissions': total_submissions,
        'unique_words': unique_words,
        'total_words': total_words,
        'top_words': top_words
    })


# HTML模板
INDEX_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>互动词云 - 录入界面</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 30px;
            margin-top: 30px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            color: #333;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .header p {
            color: #666;
        }
        .form-control {
            border-radius: 10px;
            border: 2px solid #e0e0e0;
            padding: 15px;
            font-size: 16px;
            resize: none;
        }
        .form-control:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 0.25rem rgba(102, 126, 234, 0.25);
        }
        .btn-submit {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 12px 30px;
            border-radius: 10px;
            font-size: 18px;
            font-weight: bold;
            width: 100%;
            margin-top: 20px;
            transition: transform 0.3s;
        }
        .btn-submit:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 20px rgba(102, 126, 234, 0.3);
        }
        .btn-view-cloud {
            background: #28a745;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 10px;
            font-size: 16px;
            margin-top: 10px;
            display: block;
            width: 100%;
            text-align: center;
            text-decoration: none;
        }
        .btn-view-cloud:hover {
            background: #218838;
            color: white;
        }
        .stats {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 20px;
            margin-top: 30px;
        }
        .counter {
            font-size: 24px;
            font-weight: bold;
            color: #667eea;
        }
        .alert {
            border-radius: 10px;
            display: none;
        }
        .word-count {
            text-align: right;
            color: #666;
            font-size: 14px;
            margin-top: 5px;
        }
        .recent-submissions {
            margin-top: 30px;
        }
        .submission-item {
            background: #f8f9fa;
            border-left: 4px solid #667eea;
            padding: 10px 15px;
            margin-bottom: 10px;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>💬 互动词云</h1>
            <p>分享您的话语，共创美丽词云</p>
        </div>

        <div class="alert alert-success" id="successAlert"></div>
        <div class="alert alert-danger" id="errorAlert"></div>

        <form id="submitForm">
            <div class="mb-3">
                <label for="userText" class="form-label">输入您想说的话：</label>
                <textarea 
                    class="form-control" 
                    id="userText" 
                    rows="6" 
                    placeholder="请输入您想分享的话语（支持中文、英文，最少2个字，最多1000字）"
                    maxlength="1000"
                    required></textarea>
                <div class="word-count">
                    <span id="charCount">0</span>/1000
                </div>
            </div>

            <button type="submit" class="btn-submit" id="submitBtn">
                提交并生成词云
            </button>
        </form>

        <a href="/cloud" class="btn-view-cloud">
            📊 查看词云图
        </a>

        <div class="stats">
            <h5>📈 实时统计</h5>
            <div class="row text-center">
                <div class="col-md-4">
                    <div class="counter" id="totalSubmissions">0</div>
                    <small>总提交数</small>
                </div>
                <div class="col-md-4">
                    <div class="counter" id="uniqueWords">0</div>
                    <small>独特词语</small>
                </div>
                <div class="col-md-4">
                    <div class="counter" id="totalWords">0</div>
                    <small>词语总数</small>
                </div>
            </div>
        </div>

        <div class="recent-submissions">
            <h5>最近提交：</h5>
            <div id="recentSubmissions">
                <p class="text-muted">暂无提交，成为第一个分享者吧！</p>
            </div>
        </div>
    </div>

    <script>
        // 字符计数
        document.getElementById('userText').addEventListener('input', function() {
            document.getElementById('charCount').textContent = this.value.length;
        });

        // 表单提交
        document.getElementById('submitForm').addEventListener('submit', async function(e) {
            e.preventDefault();

            const text = document.getElementById('userText').value.trim();
            const submitBtn = document.getElementById('submitBtn');
            const originalText = submitBtn.textContent;

            if (text.length < 2) {
                showAlert('文本太短，请至少输入2个字', 'error');
                return;
            }

            // 禁用按钮
            submitBtn.disabled = true;
            submitBtn.textContent = '提交中...';

            try {
                const response = await fetch('/api/submit', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ text: text })
                });

                const data = await response.json();

                if (data.success) {
                    showAlert(data.message, 'success');
                    document.getElementById('userText').value = '';
                    document.getElementById('charCount').textContent = '0';

                    // 更新统计
                    updateStats();
                    // 更新最近提交
                    updateRecentSubmissions(text);
                } else {
                    showAlert(data.message, 'error');
                }
            } catch (error) {
                showAlert('网络错误，请重试', 'error');
            } finally {
                // 恢复按钮
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        });

        function showAlert(message, type) {
            const successAlert = document.getElementById('successAlert');
            const errorAlert = document.getElementById('errorAlert');

            if (type === 'success') {
                successAlert.textContent = message;
                successAlert.style.display = 'block';
                errorAlert.style.display = 'none';
            } else {
                errorAlert.textContent = message;
                errorAlert.style.display = 'block';
                successAlert.style.display = 'none';
            }

            // 3秒后自动隐藏
            setTimeout(() => {
                successAlert.style.display = 'none';
                errorAlert.style.display = 'none';
            }, 3000);
        }

        async function updateStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                if (data.success) {
                    document.getElementById('totalSubmissions').textContent = data.total_submissions;
                    document.getElementById('uniqueWords').textContent = data.unique_words;
                    document.getElementById('totalWords').textContent = data.total_words;
                }
            } catch (error) {
                console.error('获取统计信息失败:', error);
            }
        }

        function updateRecentSubmissions(newText) {
            const container = document.getElementById('recentSubmissions');
            const submissionDiv = document.createElement('div');
            submissionDiv.className = 'submission-item';

            // 截断过长的文本
            const displayText = newText.length > 100 ? newText.substring(0, 100) + '...' : newText;
            submissionDiv.textContent = displayText;

            // 插入到最前面
            if (container.firstChild && container.firstChild.className === 'text-muted') {
                container.removeChild(container.firstChild);
            }

            container.insertBefore(submissionDiv, container.firstChild);

            // 只保留最近的5条
            const items = container.getElementsByClassName('submission-item');
            if (items.length > 5) {
                container.removeChild(items[items.length - 1]);
            }
        }

        // 页面加载时获取统计信息
        window.addEventListener('DOMContentLoaded', updateStats);
    </script>
</body>
</html>
'''

CLOUD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>互动词云 - 词云展示</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 30px;
            margin-top: 20px;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
        }
        .header h1 {
            color: #333;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .wordcloud-container {
            text-align: center;
            margin: 30px 0;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 10px;
            min-height: 500px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .wordcloud-img {
            max-width: 100%;
            max-height: 500px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            border-radius: 10px;
        }
        .controls {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 20px;
            margin-top: 20px;
        }
        .btn-control {
            margin: 5px;
        }
        .word-list {
            max-height: 400px;
            overflow-y: auto;
        }
        .word-item {
            padding: 8px 15px;
            margin: 5px 0;
            background: white;
            border-radius: 5px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-left: 4px solid #667eea;
        }
        .word-text {
            font-weight: bold;
        }
        .word-freq {
            background: #667eea;
            color: white;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 12px;
        }
        .btn-back {
            background: #6c757d;
            color: white;
            text-decoration: none;
            padding: 10px 20px;
            border-radius: 10px;
            display: inline-block;
            margin-bottom: 20px;
        }
        .btn-back:hover {
            background: #5a6268;
            color: white;
        }
        .loading {
            text-align: center;
            padding: 50px;
            font-size: 18px;
            color: #666;
        }
        .color-option {
            width: 30px;
            height: 30px;
            border-radius: 50%;
            display: inline-block;
            margin: 5px;
            cursor: pointer;
            border: 2px solid #ddd;
        }
        .color-option.active {
            border-color: #333;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="btn-back">← 返回录入界面</a>

        <div class="header">
            <h1>🌈 互动词云展示</h1>
            <p>基于所有用户提交生成的可视化词云</p>
        </div>

        <div class="row">
            <div class="col-md-9">
                <div class="wordcloud-container" id="wordcloudContainer">
                    <div class="loading" id="loading">
                        正在生成词云...
                    </div>
                    <img id="wordcloudImage" class="wordcloud-img" style="display: none;">
                </div>
            </div>

            <div class="col-md-3">
                <div class="controls">
                    <h5>🎨 控制面板</h5>

                    <div class="mb-3">
                        <label class="form-label">配色方案：</label>
                        <div>
                            <div class="color-option active" style="background: linear-gradient(135deg, #667eea, #764ba2);" data-colormap="viridis"></div>
                            <div class="color-option" style="background: linear-gradient(135deg, #ff6b6b, #ee5a52);" data-colormap="plasma"></div>
                            <div class="color-option" style="background: linear-gradient(135deg, #4cd964, #5ac8fa);" data-colormap="summer"></div>
                            <div class="color-option" style="background: linear-gradient(135deg, #ff9500, #ff5e3a);" data-colormap="autumn"></div>
                            <div class="color-option" style="background: linear-gradient(135deg, #8e44ad, #3498db);" data-colormap="cool"></div>
                        </div>
                    </div>

                    <div class="mb-3">
                        <label class="form-label">背景颜色：</label>
                        <div>
                            <button class="btn btn-sm btn-light btn-control" data-bg="white">白色</button>
                            <button class="btn btn-sm btn-dark btn-control" data-bg="#f8f9fa">浅灰</button>
                            <button class="btn btn-sm btn-dark btn-control" data-bg="#1a1a2e">深蓝</button>
                        </div>
                    </div>

                    <div class="mb-3">
                        <button class="btn btn-primary w-100" id="refreshBtn">
                            🔄 刷新词云
                        </button>
                        <button class="btn btn-success w-100 mt-2" id="downloadBtn">
                            💾 下载图片
                        </button>
                    </div>

                    <div class="mt-4">
                        <h5>📊 词频排行</h5>
                        <div class="word-list" id="wordList">
                            <!-- 词频列表会动态加载 -->
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="mt-4 text-center text-muted">
            <small>词云会定期自动更新，点击刷新按钮立即更新</small>
        </div>
    </div>

    <script>
        let wordData = {{ word_data|safe }};
        let currentColormap = 'viridis';
        let currentBgColor = 'white';

        // 页面加载时显示词云
        window.addEventListener('DOMContentLoaded', function() {
            const wordcloudImg = document.getElementById('wordcloudImage');
            const loading = document.getElementById('loading');

            if ('{{ wordcloud_img }}') {
                wordcloudImg.src = 'data:image/png;base64,{{ wordcloud_img }}';
                wordcloudImg.style.display = 'block';
                loading.style.display = 'none';

                // 更新词频列表
                updateWordList();

                // 更新统计信息
                updateCloudStats();
            } else {
                loadWordcloudData();
            }
        });

        // 更新词频列表
        function updateWordList() {
            const wordList = document.getElementById('wordList');
            wordList.innerHTML = '';

            if (wordData.length === 0) {
                wordList.innerHTML = '<p class="text-muted">暂无数据</p>';
                return;
            }

            // 按频率排序
            wordData.sort((a, b) => b.value - a.value);

            // 只显示前50个
            const topWords = wordData.slice(0, 50);

            topWords.forEach(item => {
                const wordItem = document.createElement('div');
                wordItem.className = 'word-item';

                // 根据频率设置字体大小
                const fontSize = Math.min(20, 12 + item.value * 0.5);

                wordItem.innerHTML = `
                    <span class="word-text" style="font-size: ${fontSize}px">${item.text}</span>
                    <span class="word-freq">${item.value}</span>
                `;

                wordList.appendChild(wordItem);
            });
        }

        // 加载词云数据
        async function loadWordcloudData() {
            try {
                const response = await fetch('/api/wordcloud/data');
                const data = await response.json();

                if (data.success) {
                    wordData = data.word_data;

                    const wordcloudImg = document.getElementById('wordcloudImage');
                    const loading = document.getElementById('loading');

                    wordcloudImg.src = 'data:image/png;base64,' + data.wordcloud_img;
                    wordcloudImg.style.display = 'block';
                    loading.style.display = 'none';

                    updateWordList();
                    updateCloudStats();
                }
            } catch (error) {
                console.error('加载词云数据失败:', error);
                document.getElementById('loading').textContent = '加载失败，请刷新页面重试';
            }
        }

        // 更新统计信息
        async function updateCloudStats() {
            try {
                const response = await fetch('/api/stats');
                const data = await response.json();

                if (data.success) {
                    // 可以在这里显示更多统计信息
                    console.log('统计信息:', data);
                }
            } catch (error) {
                console.error('获取统计信息失败:', error);
            }
        }

        // 刷新按钮事件
        document.getElementById('refreshBtn').addEventListener('click', function() {
            const btn = this;
            const originalText = btn.textContent;

            btn.disabled = true;
            btn.textContent = '刷新中...';

            loadWordcloudData();

            setTimeout(() => {
                btn.disabled = false;
                btn.textContent = originalText;
            }, 1000);
        });

        // 下载按钮事件
        document.getElementById('downloadBtn').addEventListener('click', function() {
            const img = document.getElementById('wordcloudImage');
            const link = document.createElement('a');
            link.href = img.src;
            link.download = 'wordcloud_' + new Date().toISOString().slice(0, 10) + '.png';
            link.click();
        });

        // 颜色选择事件
        document.querySelectorAll('.color-option').forEach(option => {
            option.addEventListener('click', function() {
                document.querySelectorAll('.color-option').forEach(opt => {
                    opt.classList.remove('active');
                });
                this.classList.add('active');
                currentColormap = this.dataset.colormap;
                // 这里可以添加重新生成词云的逻辑
                // 需要后端支持不同的配色方案
            });
        });

        // 背景颜色选择事件
        document.querySelectorAll('button[data-bg]').forEach(btn => {
            btn.addEventListener('click', function() {
                currentBgColor = this.dataset.bg;
                document.getElementById('wordcloudContainer').style.background = currentBgColor;
            });
        });

        // 自动刷新（每5秒）
        setInterval(loadWordcloudData, 5 * 1000);
    </script>
</body>
</html>
'''

# 运行应用
if __name__ == '__main__':
    # 创建必要的目录
    if not os.path.exists('static'):
        os.makedirs('static')
    if not os.path.exists('data'):
        os.makedirs('data')

    # 获取配置（Railway 会设置 PORT 环境变量）
    host = app.config.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', app.config.get('PORT', 5000)))
    debug = app.config.get('DEBUG', False)

    # 运行Flask应用
    print("=" * 50)
    print("Interactive Word Cloud Application Starting...")
    print(f"Visit: http://127.0.0.1:{port}")
    print(f"Input Page: http://127.0.0.1:{port}/")
    print(f"Word Cloud: http://127.0.0.1:{port}/cloud")
    print("=" * 50)

    app.run(debug=debug, host=host, port=port)
