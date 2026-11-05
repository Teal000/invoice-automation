# Create (or update) the application role this project connects as.
#
#   powershell -NoProfile -File tools\setup_db_role.ps1
#
# It reads the application user and password from .env, prompts once for the
# PostgreSQL superuser password you chose during installation, and creates the
# role with CREATEDB so the application can create its own database and the
# separate test database. Nothing else in this project needs superuser rights.
#
# The superuser password is only held for the duration of this script and is
# never written anywhere. The application password stays in .env, which is
# git-ignored.

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path $envFile)) {
    throw "No .env found at $envFile. Copy .env.example to .env first."
}

$settings = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*#') { continue }
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') {
        $settings[$Matches[1]] = $Matches[2].Trim()
    }
}

$appUser = $settings['INVAUT_DB_USER']
$appPassword = $settings['INVAUT_DB_PASSWORD']
$port = $settings['INVAUT_DB_PORT']
if (-not $port) { $port = '5432' }
if (-not $appUser -or -not $appPassword -or $appPassword -eq 'change_me') {
    throw 'Set INVAUT_DB_USER and INVAUT_DB_PASSWORD in .env first.'
}

# Windows PowerShell 5.1 compatible: no ternary, no null-coalescing.
$psqlPath = $null
$installed = Get-ChildItem 'C:\Program Files\PostgreSQL\*\bin\psql.exe' -ErrorAction SilentlyContinue |
             Sort-Object FullName -Descending | Select-Object -First 1
if ($installed) {
    $psqlPath = $installed.FullName
} else {
    $onPath = Get-Command psql -ErrorAction SilentlyContinue
    if ($onPath) { $psqlPath = $onPath.Source }
}
if (-not $psqlPath) { throw 'psql.exe not found. Is PostgreSQL installed?' }
Write-Host "using $psqlPath"

$secure = Read-Host -Prompt 'PostgreSQL superuser (postgres) password' -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

try {
    $env:PGPASSWORD = $plain

    $exists = & $psqlPath -U postgres -p $port -h localhost -tAc `
        "SELECT 1 FROM pg_roles WHERE rolname = '$appUser'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not connect as postgres. Wrong password, or the server is not running yet.' }

    # Escape single quotes in the password for the SQL literal.
    $sqlPassword = $appPassword.Replace("'", "''")

    if (("$exists").Trim() -eq '1') {
        Write-Host "role $appUser exists; updating its password and CREATEDB"
        $statement = "ALTER ROLE `"$appUser`" WITH LOGIN CREATEDB PASSWORD '$sqlPassword'"
    } else {
        Write-Host "creating role $appUser"
        $statement = "CREATE ROLE `"$appUser`" WITH LOGIN CREATEDB PASSWORD '$sqlPassword'"
    }

    & $psqlPath -U postgres -p $port -h localhost -v ON_ERROR_STOP=1 -c $statement | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Role creation failed.' }

    Write-Host "done. Now run: .\.venv\Scripts\python.exe -m invoice_automation.cli init-db"
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    $plain = $null
}
