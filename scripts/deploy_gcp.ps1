<#
.SYNOPSIS
  Deploy Versa's server to Google Cloud Run, backed by Cloud SQL (Postgres 16 +
  pgvector) and Secret Manager. Safe to run again: anything that exists is kept.

.DESCRIPTION
  1. Turns on the APIs it needs and makes an Artifact Registry repository.
  2. Cloud SQL: an instance, a `versa` database and user (first run only).
  3. Secret Manager: the database URL, a session secret and a tester code are
     generated; GEMINI_API_KEY and the RevenueCat keys are read from .env.
  4. Builds the image with Cloud Build (the API + the web app).
  5. Runs the migrations as a Cloud Run job (`versa migrate`), then deploys the
     service: sign-in ON, invite-only, one always-on instance (study rooms and
     live chats keep state in memory, so exactly one), 1-hour request timeout
     for the chat WebSockets.

  Nothing is sent anywhere until you run this yourself. It prints every step.
  Read docs/DEPLOY.md first -- Firebase has to be set up by hand.

.EXAMPLE
  .\scripts\deploy_gcp.ps1 -Project versa-demo-123 -FirebaseProject versa-demo-123
  .\scripts\deploy_gcp.ps1 -Project versa-demo-123 -FirebaseProject versa-demo-123 -SkipBuild
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$FirebaseProject,
    [string]$Region = 'asia-south1',          # Mumbai
    [string]$Instance = 'versa-db',
    [string]$Service = 'versa',
    [string]$SqlTier = 'db-g1-small',
    [string]$AndroidUrl = '',                 # where the invite page sends people for the APK
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path $PSScriptRoot -Parent) -ErrorAction Stop
function Step($msg) { Write-Host "versa: $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "versa: $msg" -ForegroundColor Red; exit 1 }
function Run([string[]]$argv) {
    Write-Host "  > gcloud $($argv -join ' ')" -ForegroundColor DarkGray
    & gcloud @argv
    if ($LASTEXITCODE -ne 0) { Fail "gcloud $($argv[0]) $($argv[1]) failed" }
}
function Exists([string[]]$argv) {
    & gcloud @argv 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function RandomSecret([int]$bytes = 48) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return [Convert]::ToBase64String($b).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function FromDotEnv($name) {
    if (-not (Test-Path .env)) { return $null }
    $line = Get-Content .env | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if (-not $line) { return $null }
    $value = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
    if ($value -eq '' -or $value -like 'your-*' -or $value -like 'sk_your*' -or $value -eq 'change-me') { return $null }
    return $value
}
function EnsureSecret($name, $value) {
    if (Exists @('secrets', 'describe', $name, "--project=$Project")) {
        Write-Host "  secret $name exists (kept)"
        return
    }
    if (-not $value) { Fail "secret $name has no value -- set it in .env first" }
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $value)
    try { Run @('secrets', 'create', $name, "--data-file=$tmp", '--replication-policy=automatic', "--project=$Project") }
    finally { Remove-Item $tmp -Force }
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { Fail 'gcloud is not installed' }
$account = (& gcloud config get-value account 2>$null)
if (-not $account) { Fail 'not signed in: run  gcloud auth login' }
Step "project $Project as $account, region $Region"

Step 'turning on the APIs'
Run @('services', 'enable', 'run.googleapis.com', 'sqladmin.googleapis.com', 'secretmanager.googleapis.com',
      'cloudbuild.googleapis.com', 'artifactregistry.googleapis.com', "--project=$Project")

$repo = "$Region-docker.pkg.dev/$Project/versa"
if (-not (Exists @('artifacts', 'repositories', 'describe', 'versa', "--location=$Region", "--project=$Project"))) {
    Step 'making the image repository'
    Run @('artifacts', 'repositories', 'create', 'versa', '--repository-format=docker', "--location=$Region", "--project=$Project")
}

# ------------------------------------------------------------------ Cloud SQL
$connection = "${Project}:${Region}:${Instance}"
if (-not (Exists @('sql', 'instances', 'describe', $Instance, "--project=$Project"))) {
    Step "creating Cloud SQL instance $Instance (Postgres 16, $SqlTier) -- this takes several minutes"
    # --edition=ENTERPRISE: gcloud now defaults Postgres 16 to Enterprise Plus,
    # which has no shared-core tiers like db-g1-small.
    Run @('sql', 'instances', 'create', $Instance, '--database-version=POSTGRES_16', '--edition=ENTERPRISE', "--tier=$SqlTier",
          "--region=$Region", '--storage-auto-increase', '--backup', "--project=$Project")
}
if (-not (Exists @('sql', 'databases', 'describe', 'versa', "--instance=$Instance", "--project=$Project"))) {
    Run @('sql', 'databases', 'create', 'versa', "--instance=$Instance", "--project=$Project")
}
if (-not (Exists @('secrets', 'describe', 'versa-database-url', "--project=$Project"))) {
    Step 'creating the database user and its connection secret'
    $dbPassword = RandomSecret 24
    Run @('sql', 'users', 'create', 'versa', "--instance=$Instance", "--password=$dbPassword", "--project=$Project")
    EnsureSecret 'versa-database-url' "postgresql://versa:$dbPassword@/versa?host=/cloudsql/$connection"
}

# ------------------------------------------------------------------ secrets
Step 'secrets'
EnsureSecret 'versa-session-secret' (RandomSecret 48)
EnsureSecret 'versa-gemini-key' (FromDotEnv 'GEMINI_API_KEY')
$devCode = RandomSecret 9
if (-not (Exists @('secrets', 'describe', 'versa-dev-login-code', "--project=$Project"))) {
    EnsureSecret 'versa-dev-login-code' $devCode
    Write-Host "  tester code for Sooraj / Adithya: $devCode   (also in Secret Manager: versa-dev-login-code)" -ForegroundColor Yellow
}
# Judges: ANY name + this one code signs in (each name its own account). Share
# it with the judges; delete the secret's versions to close judge sign-in.
$judgeCode = RandomSecret 9
if (-not (Exists @('secrets', 'describe', 'versa-judge-code', "--project=$Project"))) {
    EnsureSecret 'versa-judge-code' $judgeCode
    Write-Host "  judge code (any name + this code): $judgeCode   (also in Secret Manager: versa-judge-code)" -ForegroundColor Yellow
}
$rcKey = FromDotEnv 'REVENUECAT_SECRET_KEY'
$rcHook = FromDotEnv 'REVENUECAT_WEBHOOK_AUTH'
if (-not $rcHook) { $rcHook = RandomSecret 32 }
$rcProject = FromDotEnv 'REVENUECAT_PROJECT_ID'
$billing = [bool]$rcKey
if ($billing) {
    EnsureSecret 'versa-revenuecat-key' $rcKey
    EnsureSecret 'versa-revenuecat-webhook' $rcHook
} else {
    Write-Host '  no REVENUECAT_SECRET_KEY in .env: billing stays off (everyone is Free)' -ForegroundColor Yellow
}

# The service's identity may read those secrets and reach Cloud SQL.
$number = (& gcloud projects describe $Project --format='value(projectNumber)')
$sa = "$number-compute@developer.gserviceaccount.com"
foreach ($role in @('roles/secretmanager.secretAccessor', 'roles/cloudsql.client')) {
    Run @('projects', 'add-iam-policy-binding', $Project, "--member=serviceAccount:$sa", "--role=$role", '--condition=None', '--quiet')
}

# ------------------------------------------------------------------ build
$tag = (Get-Date -Format 'yyyyMMdd-HHmmss')
$image = "$repo/versa:$tag"
if ($SkipBuild) {
    $image = "$repo/versa:latest"
    Step "skipping the build, deploying $image"
} else {
    if (-not (Test-Path app/config/firebase.json)) {
        Write-Host '  no app/config/firebase.json: the web build will have no Google/email sign-in' -ForegroundColor Yellow
    }
    Step "building $image with Cloud Build (the Flutter web build makes this take a while)"
    # BuildKit, not Cloud Build's default legacy builder (`--tag`): the legacy
    # one once kept the base image's older Flutter and failed at pub get.
    $cbConfig = Join-Path ([System.IO.Path]::GetTempPath()) 'versa-cloudbuild.yaml'
    @"
steps:
- name: gcr.io/cloud-builders/docker
  env: ['DOCKER_BUILDKIT=1']
  args: ['build', '--progress=plain', '-t', '$image', '.']
images: ['$image']
timeout: 3600s
options:
  machineType: E2_HIGHCPU_8
"@ | Set-Content -Encoding ascii $cbConfig
    Run @('builds', 'submit', '.', "--config=$cbConfig", "--project=$Project")
    Run @('artifacts', 'docker', 'tags', 'add', $image, "$repo/versa:latest", "--project=$Project")
}

$secrets = "DATABASE_URL=versa-database-url:latest,GEMINI_API_KEY=versa-gemini-key:latest," +
           "VERSA_SESSION_SECRET=versa-session-secret:latest,VERSA_DEV_LOGIN_CODE=versa-dev-login-code:latest," +
           "VERSA_JUDGE_CODE=versa-judge-code:latest"
if ($billing) {
    $secrets += ",REVENUECAT_SECRET_KEY=versa-revenuecat-key:latest,REVENUECAT_WEBHOOK_AUTH=versa-revenuecat-webhook:latest"
}

# ------------------------------------------------------------------ migrate
Step 'applying migrations (Cloud Run job versa-migrate)'
Run @('run', 'jobs', 'deploy', 'versa-migrate', "--image=$image", "--region=$Region", "--project=$Project",
      "--set-cloudsql-instances=$connection", "--set-secrets=DATABASE_URL=versa-database-url:latest",
      '--command=uv', '--args=run,--no-sync,versa,migrate', '--max-retries=0', '--task-timeout=900s')
Run @('run', 'jobs', 'execute', 'versa-migrate', "--region=$Region", "--project=$Project", '--wait')

# ------------------------------------------------------------------ deploy
Step "deploying the service $Service"
$pairs = @("FIREBASE_PROJECT_ID=$FirebaseProject", 'VERSA_DEV_LOGINS=sooraj,adithya', 'VERSA_INVITES=on')
if ($rcProject) { $pairs += "REVENUECAT_PROJECT_ID=$rcProject" }
if ($AndroidUrl) { $pairs += "VERSA_ANDROID_URL=$AndroidUrl" }
# `^|^` makes "|" gcloud's separator, so the comma in the tester list survives.
$envVars = '^|^' + ($pairs -join '|')
Run @('run', 'deploy', $Service, "--image=$image", "--region=$Region", "--project=$Project",
      '--allow-unauthenticated', "--add-cloudsql-instances=$connection",
      "--set-secrets=$secrets", "--set-env-vars=$envVars",
      '--min-instances=1', '--max-instances=1', '--no-cpu-throttling', '--session-affinity',
      '--timeout=3600', '--memory=1Gi', '--cpu=1', '--concurrency=80')

$url = (& gcloud run services describe $Service --region=$Region --project=$Project --format='value(status.url)')
Run @('run', 'services', 'update', $Service, "--region=$Region", "--project=$Project",
      "--update-env-vars=VERSA_PUBLIC_URL=$url")

Step "live at $url"
Write-Host "  health:      $url/api/health"
Write-Host "  app build:   cd app; flutter build apk --release --dart-define-from-file=config/firebase.json --dart-define=VERSA_API=$url"
Write-Host "  invites:     run the Cloud SQL proxy, then  uv run versa invite create --count 10   (docs/DEPLOY.md)"
if ($billing) {
    Write-Host "  RevenueCat:  webhook URL  $url/api/billing/revenuecat/webhook"
    Write-Host "               Authorization header = the secret versa-revenuecat-webhook"
}
