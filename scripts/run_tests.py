import os
import sys
import subprocess
import importlib

# Configure environment for tests
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('PRESENCE_BACKEND', 'redis')
os.environ.setdefault('PYTHONPATH', '.')

print('ENV REDIS_URL=', '(set)' if os.environ.get('REDIS_URL') else '(not set)')
print('ENV PRESENCE_BACKEND=', os.environ.get('PRESENCE_BACKEND'))

# Run pytest full suite
print('\n=== Running pytest full suite ===')
ret = subprocess.run([sys.executable, '-m', 'pytest', '-q'], cwd=os.path.dirname(__file__) + '..')
print('pytest exit code:', ret.returncode)

# Run Redis-specific test (kept for presence backend verification)
print('\n=== Running Redis-specific test ===')
ret2 = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_presence_redis.py', '-q'], cwd=os.path.dirname(__file__) + '..')
print('redis test exit code:', ret2.returncode)

# Note: comprehensive_internal and other DB-seeding scripts removed for Supabase migration.

# Exit with pytest's exit code if non-zero
if ret.returncode != 0:
    sys.exit(ret.returncode)
sys.exit(0)
