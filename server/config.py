import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

_DEV_SECRET = "dev-secret-change-in-production"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_SECRET)
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(INSTANCE_DIR, "gracelab.sqlite3")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True

    # Session code settings
    DEFAULT_SESSION_MINUTES = 60
    CODE_EXPIRATION_MINUTES = 1440  # 24 hours — codes are inventory, valid all day
    SESSION_WARNING_MINUTES = 5

    # Station monitoring
    STATION_OFFLINE_AFTER_SECONDS = 90

    # Organization
    ORGANIZATION_NAME = "Grace Marketplace"
    TICKET_FOOTER = "Ask staff if you need more time."

    # Client update packages directory (on the server)
    UPDATES_DIR = "/var/lib/gracelab/updates"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    WTF_CSRF_ENABLED = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    @classmethod
    def validate(cls):
        key = os.environ.get("SECRET_KEY", _DEV_SECRET)
        if not key or key == _DEV_SECRET:
            raise RuntimeError(
                "SECRET_KEY must be set to a strong random value in production. "
                "Set the SECRET_KEY environment variable before starting the server."
            )


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": ProductionConfig,  # safe default — dev must be explicit
}
