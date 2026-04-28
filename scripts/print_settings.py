from app.core.settings import app_settings
import os


def redact(value: str | None) -> str:
    return "(set)" if value else "(not set)"


print('app_settings.REDIS_URL=', redact(app_settings.REDIS_URL))
print('app_settings.PRESENCE_BACKEND=', app_settings.PRESENCE_BACKEND)
print('ENV REDIS_URL=', redact(os.environ.get('REDIS_URL')))
print('ENV PRESENCE_BACKEND=', os.environ.get('PRESENCE_BACKEND'))
