"""Simple test runner to trigger the app email service.

Usage (from repo root, with venv active):
.venv\Scripts\python.exe scripts/test_send_email.py
"""
from pathlib import Path
import time
import os

# Try to load .env early for reliable env pickup
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()
except Exception:
    pass

from app.services.email import EmailService

print('NOC list:', os.environ.get('NOC_EMAIL_ADDRESSES'))
print('SMTP host:', os.environ.get('SMTP_HOST'))

print('Triggering EmailService.send_task_completed()')
EmailService.send_task_completed(
    ref_no='TEST-1234',
    site_name='Test Site',
    technician_name='Unit Tester',
    task_type='routine_check',
    completed_at='2026-02-21 12:00',
)

# Give background thread a few seconds to run the async send
print('Waiting 6 seconds for send to complete...')
for i in range(6, 0, -1):
    print(i, '...')
    time.sleep(1)

print('Done')
