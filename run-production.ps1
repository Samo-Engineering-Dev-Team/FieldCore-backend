# Seacom Backend - Production Mode Launcher
# This script runs the backend against PRODUCTION Supabase database
# USE WITH CAUTION

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "     PRODUCTION MODE - SUPABASE DB     " -ForegroundColor Red
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "WARNING: Connected to PRODUCTION database!" -ForegroundColor Red
Write-Host ""

# Uses credentials from .env file (production)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
