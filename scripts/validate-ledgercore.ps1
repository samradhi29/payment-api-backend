$ErrorActionPreference = 'Stop'

Write-Host "LedgerCore validation" -ForegroundColor Cyan
Write-Host "This script uses the dedicated test database. It does NOT migrate your main database." -ForegroundColor Yellow

$confirmation = Read-Host "Have you rotated the credentials exposed earlier and updated .env plus frontend/.env.local? Type ROTATED to continue"
if ($confirmation -ne 'ROTATED') {
    throw "Stop here. Rotate exposed credentials before building or starting the project."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI was not found. Install/start Docker Desktop first."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found. Install Node.js 22 or later first."
}

$python = Join-Path (Get-Location) "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Write-Host "[1/8] Validating Compose configuration..." -ForegroundColor Cyan
docker compose config -q
if ($LASTEXITCODE -ne 0) { throw "docker compose config failed." }

Write-Host "[2/8] Starting isolated test database..." -ForegroundColor Cyan
docker compose up -d test-db
if ($LASTEXITCODE -ne 0) { throw "Could not start test-db." }

Write-Host "[3/8] Applying migrations to the isolated test database only..." -ForegroundColor Cyan
docker compose --profile test run --rm migrate-test
if ($LASTEXITCODE -ne 0) { throw "Test database migration failed." }

# Read the test DB password from .env without printing it.
$testPassword = "ledgercore_test_password"
if (Test-Path ".env") {
    $passwordLine = Get-Content ".env" | Where-Object { $_ -match '^TEST_POSTGRES_PASSWORD=' } | Select-Object -First 1
    if ($passwordLine) {
        $testPassword = $passwordLine.Substring("TEST_POSTGRES_PASSWORD=".Length).Trim().Trim('"').Trim("'")
    }
}
$escapedPassword = [uri]::EscapeDataString($testPassword)
$env:TEST_DATABASE_URL = "postgresql+psycopg://ledgercore_test:$escapedPassword@localhost:5433/ledgercore_test"

Write-Host "[4/8] Installing backend test dependencies..." -ForegroundColor Cyan
& $python -m pip install -r requirements.txt -r aggregator/requirements.txt -r metrics_service/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

Write-Host "[5/8] Running main backend tests..." -ForegroundColor Cyan
& $python -m pytest -q tests
if ($LASTEXITCODE -ne 0) { throw "Main backend tests failed." }

Write-Host "[6/8] Running aggregator and metrics tests..." -ForegroundColor Cyan
Push-Location aggregator
try {
    & $python -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw "Aggregator tests failed." }
} finally { Pop-Location }
Push-Location metrics_service
try {
    & $python -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw "Metrics service tests failed." }
} finally { Pop-Location }

Write-Host "[7/8] Running frontend lint and production build..." -ForegroundColor Cyan
Push-Location frontend
try {
    if (-not (Test-Path "node_modules")) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    }
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
} finally { Pop-Location }

Write-Host "[8/8] Building all Docker images..." -ForegroundColor Cyan
docker compose build
if ($LASTEXITCODE -ne 0) { throw "Docker image build failed." }

Write-Host "Validation passed. Main database migration was intentionally not run." -ForegroundColor Green
Write-Host "Next: back up the main database, inspect its Alembic revision, then migrate it deliberately." -ForegroundColor Yellow
