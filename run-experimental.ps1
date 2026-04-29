# Seacom Backend - Experimental Mode Launcher
# This script runs the backend against the LOCAL experimental database
# NOT the production Supabase database

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  EXPERIMENTAL MODE - LOCAL DATABASE   " -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Database: seacom_experimental_db" -ForegroundColor Green
Write-Host "Host:     localhost:5432" -ForegroundColor Green
Write-Host "PostGIS:  Enabled" -ForegroundColor Green
Write-Host ""

# Override environment variables for experimental database
$env:DB_HOST = "localhost"
$env:DB_PORT = "5432"
$env:DB_NAME = "seacom_experimental_db"
$env:DB_USER = "postgres"

if (-not $env:EXPERIMENTAL_DB_PASSWORD) {
    throw "Set EXPERIMENTAL_DB_PASSWORD before running this script."
}

$env:DB_PASSWORD = $env:EXPERIMENTAL_DB_PASSWORD

# Run the application
uv run uvicorn app.main:app --reload --port 8000
