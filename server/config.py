import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(INSTANCE_DIR, "gracelab.sqlite3")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # Session code settings
    DEFAULT_SESSION_MINUTES = 60
    CODE_EXPIRATION_MINUTES = 1440  # 24 hours
    SESSION_WARNING_MINUTES = 5

    # Station monitoring
    STATION_OFFLINE_AFTER_SECONDS = 90

    # Organization
    ORGANIZATION_NAME = "Grace Marketplace"
    TICKET_FOOTER = "Ask staff if you need more time."


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    WTF_CSRF_ENABLED = True


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
