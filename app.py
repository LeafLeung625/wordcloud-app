from flask import Flask, render_template, render_template_string, request, jsonify, send_file, redirect, url_for
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

        # 直接从 word_frequencies 表获取已有词频
        c.execute("SELECT word, frequency FROM word_frequencies ORDER BY frequency DESC LIMIT 100")
        word_freq = dict(c.fetchall())

        conn.close()

    return word_freq


def add_word_frequencies(text):
    """将新提交的文本分词后添加到词频表"""
    words = process_text(text)
    if not words:
        return

    # 统计词频
    word_counter = Counter(words)

    with db_lock:
        conn = sqlite3.connect('wordcloud.db')
        c = conn.cursor()

        # 只更新本次提交的词语（累加频率）
        for word, freq in word_counter.items():
            c.execute(
                "INSERT OR REPLACE INTO word_frequencies (word, frequency, last_updated) "
                "VALUES (?, COALESCE((SELECT frequency FROM word_frequencies WHERE word = ?), 0) + ?, CURRENT_TIMESTAMP)",
                (word, word, freq)
            )

        conn.commit()
        conn.close()


def find_chinese_font():
    """查找可用的中文字体"""
    import subprocess
    import glob
    
    # 常见的中文字体路径
    font_paths = [
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        '/usr/share/fonts/truetype/arphic/uming.ttc',
    ]
    
    # 搜索系统字体
    try:
        # 搜索包含CJK/Chinese的字体
        for pattern in ['**/wqy*.ttc', '**/NotoSansCJK*.ttc', '**/NotoSans*.ttc', '**/uming.ttc', '**/ukai.ttc']:
            fonts = glob.glob(f'/usr/share/fonts/**/{pattern}', recursive=True)
            if fonts:
                return fonts[0]
    except:
        pass
    
    # 尝试用fc-list查找
    try:
        result = subprocess.run(['fc-list', ':lang=zh', '-f', '%{file}\n'], 
                              capture_output=True, text=True, timeout=5)
        if result.stdout.strip():
            return result.stdout.strip().split('\n')[0]
    except:
        pass
    
    # 如果都找不到，尝试下载思源黑体
    try:
        font_dir = '/tmp/fonts'
        os.makedirs(font_dir, exist_ok=True)
        font_file = os.path.join(font_dir, 'SourceHanSansSC.ttf')
        if not os.path.exists(font_file):
            import urllib.request
            # 使用 GitHub 上的思源黑体 CDN
            url = 'https://github.com/adobe-fonts/source-han-sans/releases/download/2.004R/SourceHanSansSC.zip'
            # 改用更小的字体包
            url = 'https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf'
            urllib.request.urlretrieve(url, font_file)
            print(f"Downloaded font to {font_file}")
        if os.path.exists(font_file):
            return font_file
    except Exception as e:
        print(f"Font download failed: {e}")
    
    return None


def generate_wordcloud_image(word_freq, width=1920, height=1080):
    """生成词云图片并返回base64编码"""
    if not word_freq:
        # 如果没有数据，返回默认图片
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new('RGB', (width, height), color='white')
        d = ImageDraw.Draw(img)
        # 这里可以添加默认文字
        return img_to_base64(img)

    # 查找中文字体
    font_path = find_chinese_font()
    
    # 创建词云对象 - 使用更大尺寸和边距确保内容完整
    wordcloud = WordCloud(
        font_path=font_path,
        width=width,
        height=height,
        background_color='white',
        max_words=150,
        max_font_size=200,
        min_font_size=12,
        random_state=42,
        colormap='viridis',
        prefer_horizontal=0.9,
        scale=1,
        margin=60,  # 大边距确保文字不被截断
        relative_scaling=0.5,
        collocations=False  # 避免重复词组
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
    """首页 - 语言选择"""
    return render_template_string(LANGUAGE_TEMPLATE)


@app.route('/<lang>/input')
def input_page(lang):
    """录入界面 - 根据语言"""
    if lang == 'en':
        return render_template_string(INDEX_TEMPLATE_EN)
    return render_template_string(INDEX_TEMPLATE_ZH)


@app.route('/<lang>/cloud')
def cloud_page(lang):
    """词云展示页面"""
    word_freq = get_word_frequencies()
    wordcloud_img = generate_wordcloud_image(word_freq)

    # 将词频数据转换为前端需要的格式
    word_data = [{'text': word, 'value': freq} for word, freq in word_freq.items()]

    if lang == 'en':
        return render_template_string(
            CLOUD_TEMPLATE_EN,
            wordcloud_img=wordcloud_img,
            word_data=json.dumps(word_data, ensure_ascii=False),
            lang='en'
        )
    return render_template_string(
        CLOUD_TEMPLATE_ZH,
        wordcloud_img=wordcloud_img,
        word_data=json.dumps(word_data, ensure_ascii=False),
        lang='zh'
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

    # 只为新提交的文本计算词频并添加
    add_word_frequencies(text)

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
LANGUAGE_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>互动词云</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .lang-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 50px;
            text-align: center;
        }
        .lang-container h1 {
            color: #333;
            margin-bottom: 40px;
            font-weight: bold;
        }
        .lang-btn {
            display: block;
            width: 100%;
            padding: 20px;
            margin: 15px 0;
            font-size: 20px;
            border-radius: 15px;
            text-decoration: none;
            transition: all 0.3s;
        }
        .lang-btn-zh {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .lang-btn-zh:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 30px rgba(102, 126, 234, 0.4);
            color: white;
        }
        .lang-btn-en {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
            color: white;
        }
        .lang-btn-en:hover {
            transform: translateY(-3px);
            box-shadow: 0 10px 30px rgba(56, 239, 125, 0.4);
            color: white;
        }
        .subtitle {
            color: #666;
            margin-top: 30px;
            font-size: 16px;
        }
    </style>
</head>
<body>
    <div class="lang-container">
        <h1>🌈 互动词云</h1>
        <p style="color: #666; margin-bottom: 30px;">Welcome to Interactive Word Cloud</p>
        <a href="/zh/input" class="lang-btn lang-btn-zh">
            🇨🇳 中文
        </a>
        <a href="/en/input" class="lang-btn lang-btn-en">
            🇺🇸 English
        </a>
        <p class="subtitle">Choose your language to continue</p>
    </div>
</body>
</html>
'''

# 中文录入模板
INDEX_TEMPLATE_ZH = '''
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
        .btn-lang {
            background: #6c757d;
            color: white;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 8px;
            display: inline-block;
            margin-bottom: 15px;
        }
        .btn-lang:hover {
            background: #5a6268;
            color: white;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="btn-lang">🌐 语言 / Language</a>
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

        <a href="/zh/cloud" class="btn-view-cloud">
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

# 英文录入模板
INDEX_TEMPLATE_EN = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Interactive Word Cloud</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
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
            border-color: #11998e;
            box-shadow: 0 0 0 0.25rem rgba(17, 153, 142, 0.25);
        }
        .btn-submit {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
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
            box-shadow: 0 10px 20px rgba(17, 153, 142, 0.3);
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
            color: #11998e;
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
            border-left: 4px solid #11998e;
            padding: 10px 15px;
            margin-bottom: 10px;
            border-radius: 5px;
        }
        .btn-lang {
            background: #6c757d;
            color: white;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 8px;
            display: inline-block;
            margin-bottom: 15px;
        }
        .btn-lang:hover {
            background: #5a6268;
            color: white;
        }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="btn-lang">🌐 Language / 语言</a>
        <div class="header">
            <h1>💬 Interactive Word Cloud</h1>
            <p>Share your thoughts, create beautiful clouds</p>
        </div>

        <div class="alert alert-success" id="successAlert"></div>
        <div class="alert alert-danger" id="errorAlert"></div>

        <form id="submitForm">
            <div class="mb-3">
                <label for="userText" class="form-label">Enter your message:</label>
                <textarea 
                    class="form-control" 
                    id="userText" 
                    rows="6" 
                    placeholder="Share your thoughts here (supports English, min 2 chars, max 1000 chars)"
                    maxlength="1000"
                    required></textarea>
                <div class="word-count">
                    <span id="charCount">0</span>/1000
                </div>
            </div>

            <button type="submit" class="btn-submit" id="submitBtn">
                Submit & Generate Word Cloud
            </button>
        </form>

        <a href="/en/cloud" class="btn-view-cloud">
            📊 View Word Cloud
        </a>

        <div class="stats">
            <h5>📈 Statistics</h5>
            <div class="row text-center">
                <div class="col-md-4">
                    <div class="counter" id="totalSubmissions">0</div>
                    <small>Total Submissions</small>
                </div>
                <div class="col-md-4">
                    <div class="counter" id="uniqueWords">0</div>
                    <small>Unique Words</small>
                </div>
                <div class="col-md-4">
                    <div class="counter" id="totalWords">0</div>
                    <small>Total Words</small>
                </div>
            </div>
        </div>

        <div class="recent-submissions">
            <h5>Recent Submissions:</h5>
            <div id="recentSubmissions">
                <p class="text-muted">No submissions yet. Be the first to share!</p>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('userText').addEventListener('input', function() {
            document.getElementById('charCount').textContent = this.value.length;
        });

        document.getElementById('submitForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const text = document.getElementById('userText').value.trim();
            const submitBtn = document.getElementById('submitBtn');
            const originalText = submitBtn.textContent;

            if (text.length < 2) {
                showAlert('Text too short, please enter at least 2 characters', 'error');
                return;
            }

            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';

            try {
                const response = await fetch('/api/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: text })
                });
                const data = await response.json();

                if (data.success) {
                    showAlert('Submitted! Word cloud updated.', 'success');
                    document.getElementById('userText').value = '';
                    document.getElementById('charCount').textContent = '0';
                    updateStats();
                    updateRecentSubmissions(text);
                } else {
                    // 翻译错误消息
                    let errorMsg = data.message;
                    if (errorMsg.includes('太短')) errorMsg = 'Text too short';
                    else if (errorMsg.includes('过长')) errorMsg = 'Text too long (max 1000 chars)';
                    showAlert(errorMsg, 'error');
                }
            } catch (error) {
                showAlert('Network error, please try again', 'error');
            } finally {
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
                console.error('Failed to get stats:', error);
            }
        }

        function updateRecentSubmissions(newText) {
            const container = document.getElementById('recentSubmissions');
            const submissionDiv = document.createElement('div');
            submissionDiv.className = 'submission-item';
            const displayText = newText.length > 100 ? newText.substring(0, 100) + '...' : newText;
            submissionDiv.textContent = displayText;

            if (container.firstChild && container.firstChild.className === 'text-muted') {
                container.removeChild(container.firstChild);
            }
            container.insertBefore(submissionDiv, container.firstChild);

            const items = container.getElementsByClassName('submission-item');
            if (items.length > 5) {
                container.removeChild(items[items.length - 1]);
            }
        }

        window.addEventListener('DOMContentLoaded', updateStats);
    </script>
</body>
</html>
'''

CLOUD_TEMPLATE_ZH = '''
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
            padding: 0;
            background: white;
            border-radius: 10px;
            width: 100%;
            min-height: 600px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            position: relative;
        }
        .wordcloud-img {
            width: 100%;
            height: auto;
            min-height: 600px;
            object-fit: contain;
            border-radius: 10px;
        }
        .fullscreen-btn {
            position: absolute;
            top: 20px;
            right: 20px;
            z-index: 999;
            background: #667eea;
            color: white;
            border: none;
            padding: 15px 25px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 18px;
            font-weight: bold;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        }
        .fullscreen-btn:hover {
            background: rgba(102, 126, 234, 1);
            transform: scale(1.1);
        }
        /* 全屏样式 */
        .fullscreen-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: #1a1a2e;
            z-index: 9999;
            display: none;
        }
        .fullscreen-overlay.active {
            display: block;
        }
        .fullscreen-overlay {
            background: url('') center center no-repeat;
            background-size: cover;
        }
        .fullscreen-overlay img {
            display: none;
        }
        .fullscreen-close {
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            font-size: 24px;
            cursor: pointer;
            transition: all 0.3s ease;
            z-index: 10000;
        }
        .fullscreen-close:hover {
            background: rgba(255,255,255,0.3);
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
    <button class="fullscreen-btn" id="fullscreenBtn" title="全屏查看">⛶</button>

    <!-- 全屏覆盖层 -->
    <div class="fullscreen-overlay" id="fullscreenOverlay">
        <button class="fullscreen-close" id="fullscreenClose">✕</button>
        <img id="fullscreenImage" src="" alt="词云全屏">
    </div>

    <div class="container">
        <a href="/zh/input" class="btn-back">← 返回录入界面</a>

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
                            <button class="btn btn-sm btn-secondary btn-control" data-bg="#f8f9fa">浅灰</button>
                            <button class="btn btn-sm btn-dark btn-control" data-bg="#1a1a2e">深蓝</button>
                            <button class="btn btn-sm btn-dark btn-control" data-bg="#000000">黑色</button>
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
                    const fullscreenImg = document.getElementById('fullscreenImage');
                    const loading = document.getElementById('loading');

                    const newSrc = 'data:image/png;base64,' + data.wordcloud_img;
                    wordcloudImg.src = newSrc;
                    wordcloudImg.style.display = 'block';
                    loading.style.display = 'none';

                    // 如果全屏打开着，也同步更新全屏背景
                    if (fullscreenOverlay.classList.contains('active')) {
                        fullscreenOverlay.style.backgroundImage = 'url(' + newSrc + ')';
                    }

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

        // 全屏功能
        const fullscreenBtn = document.getElementById('fullscreenBtn');
        const fullscreenOverlay = document.getElementById('fullscreenOverlay');
        const fullscreenClose = document.getElementById('fullscreenClose');
        const fullscreenImage = document.getElementById('fullscreenImage');
        const wordcloudImage = document.getElementById('wordcloudImage');

        // 打开全屏
        fullscreenBtn.addEventListener('click', function() {
            if (wordcloudImage.src) {
                fullscreenOverlay.style.backgroundImage = 'url(' + wordcloudImage.src + ')';
                fullscreenOverlay.classList.add('active');
                document.body.style.overflow = 'hidden';
            }
        });

        // 关闭全屏
        fullscreenClose.addEventListener('click', closeFullscreen);

        // 点击遮罩关闭
        fullscreenOverlay.addEventListener('click', function(e) {
            if (e.target === fullscreenOverlay) {
                closeFullscreen();
            }
        });

        // ESC键关闭全屏
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && fullscreenOverlay.classList.contains('active')) {
                closeFullscreen();
            }
        });

        function closeFullscreen() {
            fullscreenOverlay.classList.remove('active');
            document.body.style.overflow = 'auto';
        }
    </script>
</body>
</html>
'''

# 英文词云模板
CLOUD_TEMPLATE_EN = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Interactive Word Cloud</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
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
            padding: 0;
            background: white;
            border-radius: 10px;
            width: 100%;
            min-height: 600px;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            position: relative;
        }
        .wordcloud-img {
            width: 100%;
            height: auto;
            min-height: 600px;
            object-fit: contain;
            border-radius: 10px;
        }
        .fullscreen-btn {
            position: absolute;
            top: 20px;
            right: 20px;
            z-index: 999;
            background: #11998e;
            color: white;
            border: none;
            padding: 15px 25px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 18px;
            font-weight: bold;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
        }
        .fullscreen-btn:hover {
            background: rgba(17, 153, 142, 1);
            transform: scale(1.1);
        }
        .fullscreen-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background-size: cover;
            background-position: center;
            background-repeat: no-repeat;
            z-index: 9999;
            display: none;
        }
        .fullscreen-overlay.active {
            display: block;
        }
        .fullscreen-close {
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 50px;
            height: 50px;
            border-radius: 50%;
            font-size: 24px;
            cursor: pointer;
            transition: all 0.3s ease;
            z-index: 10000;
        }
        .fullscreen-close:hover {
            background: rgba(255,255,255,0.3);
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
            border-left: 4px solid #11998e;
        }
        .word-text {
            font-weight: bold;
        }
        .word-freq {
            background: #11998e;
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
        .btn-lang {
            background: #6c757d;
            color: white;
            text-decoration: none;
            padding: 10px 20px;
            border-radius: 10px;
            display: inline-block;
            margin-bottom: 20px;
            margin-left: 10px;
        }
        .btn-lang:hover {
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
    <button class="fullscreen-btn" id="fullscreenBtn" title="Fullscreen">⛶</button>

    <div class="fullscreen-overlay" id="fullscreenOverlay">
        <button class="fullscreen-close" id="fullscreenClose">✕</button>
    </div>

    <div class="container">
        <div>
            <a href="/en/input" class="btn-back">← Back to Input</a>
            <a href="/" class="btn-lang">🌐 Language / 语言</a>
        </div>

        <div class="header">
            <h1>🌈 Interactive Word Cloud</h1>
            <p>Visual word cloud based on all user submissions</p>
        </div>

        <div class="row">
            <div class="col-md-9">
                <div class="wordcloud-container" id="wordcloudContainer">
                    <div class="loading" id="loading">
                        Generating word cloud...
                    </div>
                    <img id="wordcloudImage" class="wordcloud-img" style="display: none;">
                </div>
            </div>

            <div class="col-md-3">
                <div class="controls">
                    <h5>🎨 Control Panel</h5>

                    <div class="mb-3">
                        <label class="form-label">Refresh:</label>
                        <div>
                            <button class="btn btn-primary w-100" id="refreshBtn">
                                🔄 Refresh Word Cloud
                            </button>
                            <button class="btn btn-success w-100 mt-2" id="downloadBtn">
                                💾 Download Image
                            </button>
                        </div>
                    </div>

                    <div class="mt-4">
                        <h5>📊 Top Words</h5>
                        <div class="word-list" id="wordList"></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="mt-4 text-center text-muted">
            <small>Word cloud auto-refreshes every 5 seconds</small>
        </div>
    </div>

    <script>
        let wordData = {{ word_data|safe }};

        window.addEventListener('DOMContentLoaded', function() {
            const wordcloudImg = document.getElementById('wordcloudImage');
            const loading = document.getElementById('loading');

            if ('{{ wordcloud_img }}') {
                wordcloudImg.src = 'data:image/png;base64,{{ wordcloud_img }}';
                wordcloudImg.style.display = 'block';
                loading.style.display = 'none';
                updateWordList();
            } else {
                loadWordcloudData();
            }
        });

        function updateWordList() {
            const wordList = document.getElementById('wordList');
            wordList.innerHTML = '';

            if (wordData.length === 0) {
                wordList.innerHTML = '<p class="text-muted">No data yet</p>';
                return;
            }

            wordData.sort((a, b) => b.value - a.value);
            const topWords = wordData.slice(0, 50);

            topWords.forEach(item => {
                const wordItem = document.createElement('div');
                wordItem.className = 'word-item';
                const fontSize = Math.min(20, 12 + item.value * 0.5);
                wordItem.innerHTML = `
                    <span class="word-text" style="font-size: ${fontSize}px">${item.text}</span>
                    <span class="word-freq">${item.value}</span>
                `;
                wordList.appendChild(wordItem);
            });
        }

        async function loadWordcloudData() {
            try {
                const response = await fetch('/api/wordcloud/data');
                const data = await response.json();

                if (data.success) {
                    wordData = data.word_data;

                    const wordcloudImg = document.getElementById('wordcloudImage');
                    const fullscreenOverlay = document.getElementById('fullscreenOverlay');
                    const loading = document.getElementById('loading');

                    const newSrc = 'data:image/png;base64,' + data.wordcloud_img;
                    wordcloudImg.src = newSrc;
                    wordcloudImg.style.display = 'block';
                    loading.style.display = 'none';

                    if (fullscreenOverlay.classList.contains('active')) {
                        fullscreenOverlay.style.backgroundImage = 'url(' + newSrc + ')';
                    }

                    updateWordList();
                }
            } catch (error) {
                console.error('Failed to load word cloud:', error);
                document.getElementById('loading').textContent = 'Load failed, please refresh';
            }
        }

        document.getElementById('refreshBtn').addEventListener('click', function() {
            loadWordcloudData();
        });

        document.getElementById('downloadBtn').addEventListener('click', function() {
            const img = document.getElementById('wordcloudImage');
            const link = document.createElement('a');
            link.href = img.src;
            link.download = 'wordcloud_' + new Date().toISOString().slice(0, 10) + '.png';
            link.click();
        });

        setInterval(loadWordcloudData, 5 * 1000);

        const fullscreenBtn = document.getElementById('fullscreenBtn');
        const fullscreenOverlay = document.getElementById('fullscreenOverlay');
        const fullscreenClose = document.getElementById('fullscreenClose');
        const wordcloudImage = document.getElementById('wordcloudImage');

        fullscreenBtn.addEventListener('click', function() {
            if (wordcloudImage.src) {
                fullscreenOverlay.style.backgroundImage = 'url(' + wordcloudImage.src + ')';
                fullscreenOverlay.classList.add('active');
                document.body.style.overflow = 'hidden';
            }
        });

        fullscreenClose.addEventListener('click', closeFullscreen);

        fullscreenOverlay.addEventListener('click', function(e) {
            if (e.target === fullscreenOverlay) {
                closeFullscreen();
            }
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && fullscreenOverlay.classList.contains('active')) {
                closeFullscreen();
            }
        });

        function closeFullscreen() {
            fullscreenOverlay.classList.remove('active');
            document.body.style.overflow = 'auto';
        }
    </script>
</body>
</html>
'''

# 运行应用
if __name__ == '__main__':
    # 创建必要的目录
    if not os.path.exists('static'):
        os.makedirs('static')
    if not os.path.exists('logs'):
        os.makedirs('logs')

    # 获取配置
    host = app.config.get('HOST', '0.0.0.0')
    port = app.config.get('PORT', 5000)
    debug = app.config.get('DEBUG', False)

    # 尝试启动 ngrok
    public_url = None
    try:
        from pyngrok import ngrok
        # 创建 HTTP 隧道
        tunnel = ngrok.connect(port)
        public_url = tunnel.public_url
        print("\n" + "=" * 50)
        print("NGROK PUBLIC URL (Share this link):")
        print(public_url)
        print("=" * 50 + "\n")
    except Exception as e:
        print(f"ngrok not configured or failed: {e}")
        print("To get a public URL, sign up at https://ngrok.com")
        print("Then run: ngrok http 5000\n")

    # 运行Flask应用
    print("=" * 50)
    print("Interactive Word Cloud Application Starting...")
    print(f"Visit: http://127.0.0.1:{port}")
    print(f"Input Page: http://127.0.0.1:{port}/")
    print(f"Word Cloud: http://127.0.0.1:{port}/cloud")
    if public_url:
        print(f"PUBLIC URL: {public_url}")
    print("=" * 50)

    app.run(debug=debug, host=host, port=port)
