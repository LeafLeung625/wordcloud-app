# config.py
import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-change-this-in-production'
    HOST = '0.0.0.0'
    PORT = 5000
    DEBUG = False
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload

class DevelopmentConfig(Config):
    DEBUG = True

class ProductionConfig(Config):
    DEBUG = False

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig
}
