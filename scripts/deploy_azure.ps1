[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$SubscriptionId,

    [ValidateSet('uaenorth')]
    [string]$Location = 'uaenorth',

    [ValidatePattern('^[a-zA-Z0-9._-]*$')]
    [string]$ImageTag = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The validated deployment profile is intentionally narrow. UAE North has the
# required PostgreSQL 16 Standard_B1ms offer for this subscription.
$ResourceGroup = 'rg-hallucination-trap-poc-uaen'
$ContainerEnvironment = 'cae-hallucination-trap-uaen'
$RegistryName = 'acrhallitrapuaen260812'
$PostgresServer = 'pg-hallitrap-uaen-260812'
$ContainerApp = 'ca-hallucination-trap'
$InitializationJob = 'job-hallucination-trap-init'
$DatabaseName = 'hallucination_trap'
$DatabaseAdmin = 'trapadmin'
$ImageRepository = 'hallucination-trap'
$DatabaseSecretName = 'database-url'
$RequiredOwnershipTags = [ordered]@{
    project = 'hallucination-trap'
    'managed-by' = 'deploy-azure-ps1'
    owner = 'minhal-abdul-sami'
    purpose = 'cold-outreach-demo'
    'deployment-profile' = 'uaenorth'
    'resource-scope' = 'dedicated'
}
$ProjectTags = @($RequiredOwnershipTags.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })

function Invoke-Az {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CommandArgs,
        [switch]$Sensitive
    )

    if ($Sensitive) {
        & az @CommandArgs 1>$null 2>$null
    }
    else {
        & az @CommandArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed with exit code $LASTEXITCODE."
    }
}

function Invoke-AzText {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CommandArgs,
        [switch]$Sensitive
    )

    $result = if ($Sensitive) {
        & az @CommandArgs 2>$null
    }
    else {
        & az @CommandArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI command failed with exit code $LASTEXITCODE."
    }
    return (($result | Out-String).Trim())
}

function Test-AzCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$CommandArgs
    )

    # Windows PowerShell can promote native stderr to a terminating error when
    # the script-wide policy is Stop. Resource-existence probes must use the
    # Azure CLI exit code instead. Always restore the strict policy afterwards.
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & az @CommandArgs 1>$null 2>$null
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return $exitCode -eq 0
}

function Assert-DedicatedResource {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Resource,
        [Parameter(Mandatory = $true)]
        [string]$ResourceType,
        [Parameter(Mandatory = $true)]
        [string]$ResourceName
    )

    $actualLocation = [Regex]::Replace(([string]$Resource.location).ToLowerInvariant(), '[^a-z0-9]', '')
    $expectedLocation = [Regex]::Replace($Location.ToLowerInvariant(), '[^a-z0-9]', '')
    if ($actualLocation -ne $expectedLocation) {
        throw "$ResourceType $ResourceName is in '$($Resource.location)', not '$Location'. Deployment stopped before reuse or update."
    }

    if (-not $Resource.tags) {
        throw "$ResourceType $ResourceName has no ownership tags. Deployment stopped before reuse or update."
    }
    foreach ($entry in $RequiredOwnershipTags.GetEnumerator()) {
        $property = $Resource.tags.PSObject.Properties[$entry.Key]
        if (-not $property -or [string]$property.Value -cne [string]$entry.Value) {
            throw "$ResourceType $ResourceName does not have the required ownership tag '$($entry.Key)=$($entry.Value)'. Deployment stopped before reuse or update."
        }
    }
}

function New-StrongPassword {
    $bytes = New-Object byte[] 48
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $randomText = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', 'A').Replace('/', 'b')
    # The fixed prefix guarantees all four PostgreSQL password character classes.
    return "Ht!7-$randomText"
}

function Test-StrongPassword {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value.Length -lt 8 -or $Value.Length -gt 128) {
        return $false
    }
    $classes = 0
    if ($Value -cmatch '[A-Z]') { $classes++ }
    if ($Value -cmatch '[a-z]') { $classes++ }
    if ($Value -match '[0-9]') { $classes++ }
    if ($Value -match '[^a-zA-Z0-9]') { $classes++ }
    return $classes -ge 3
}

function New-DatabaseUrl {
    param([Parameter(Mandatory = $true)][string]$Password)

    $encodedPassword = [Uri]::EscapeDataString($Password)
    $hostName = "$PostgresServer.postgres.database.azure.com"
    return "postgresql+psycopg://${DatabaseAdmin}:${encodedPassword}@${hostName}:5432/${DatabaseName}?sslmode=require"
}

function Test-DedicatedDatabaseUrl {
    param([Parameter(Mandatory = $true)][string]$Value)

    $expectedTarget = "@$PostgresServer.postgres.database.azure.com`:5432/$DatabaseName"
    $expectedPrefix = "postgresql+psycopg://${DatabaseAdmin}:"
    return $Value.StartsWith($expectedPrefix) -and $Value.Contains($expectedTarget)
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI is required. Install Azure CLI, then run az login.'
}
if (-not (Test-Path (Join-Path $PSScriptRoot '..\Dockerfile'))) {
    throw 'Run this script from the repository. The production Dockerfile is missing.'
}

Write-Host 'Validating the Azure account and subscription.'
Invoke-Az -CommandArgs @('account', 'set', '--subscription', $SubscriptionId, '--only-show-errors')
$account = (Invoke-AzText -CommandArgs @('account', 'show', '--output', 'json', '--only-show-errors')) | ConvertFrom-Json
if ($account.id -ne $SubscriptionId -or $account.state -ne 'Enabled') {
    throw 'The requested Azure subscription is not active.'
}
Write-Host "Subscription: $($account.name) ($($account.id))"

# Never adopt a resource group that does not match this deployment profile.
$resourceGroupExists = Test-AzCommand -CommandArgs @('group', 'show', '--name', $ResourceGroup)
if ($resourceGroupExists) {
    $resourceGroupRecord = (Invoke-AzText -CommandArgs @(
        'group', 'show', '--name', $ResourceGroup, '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $resourceGroupRecord -ResourceType 'Resource group' -ResourceName $ResourceGroup
}

# Check the paid database offer before this script creates a new server. Azure
# can restrict a subscription from PostgreSQL provisioning in one region even
# when the CLI syntax and resource provider are valid. An existing dedicated
# server can still be updated, so the SKU gate does not block a normal rerun.
$preflightServerExists = Test-AzCommand -CommandArgs @(
    'postgres', 'flexible-server', 'show', '--name', $PostgresServer,
    '--resource-group', $ResourceGroup
)
if ($preflightServerExists) {
    $preflightServer = (Invoke-AzText -CommandArgs @(
        'postgres', 'flexible-server', 'show', '--name', $PostgresServer,
        '--resource-group', $ResourceGroup, '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $preflightServer -ResourceType 'PostgreSQL server' -ResourceName $PostgresServer
}
else {
    $postgresCapabilities = (
        Invoke-AzText -CommandArgs @(
            'postgres', 'flexible-server', 'list-skus', '--location', $Location,
            '--output', 'json', '--only-show-errors'
        )
    ) | ConvertFrom-Json
    $postgresCapability = $postgresCapabilities | Select-Object -First 1
    if (-not $postgresCapability) {
        throw "PostgreSQL preflight returned no capability record for $Location. No resource was created by this run."
    }
    $b1msAvailable = @(
        $postgresCapability.supportedServerEditions |
            ForEach-Object { $_.supportedServerSkus } |
            Where-Object { $_.name -eq 'Standard_B1ms' }
    ).Count -gt 0
    if (-not $b1msAvailable) {
        $restriction = if ($postgresCapability.reason) {
            $postgresCapability.reason
        }
        else {
            'Standard_B1ms is not listed for this subscription and region.'
        }
        throw "PostgreSQL preflight failed for $Location. $restriction No resource was created by this run."
    }
}

Write-Host 'Installing or upgrading the required Azure Container Apps CLI extension.'
Invoke-Az -CommandArgs @(
    'extension', 'add', '--name', 'containerapp', '--upgrade',
    '--only-show-errors', '--output', 'none'
)
if (-not (Test-AzCommand -CommandArgs @('containerapp', 'job', 'logs', 'show', '--help'))) {
    throw 'The Azure Container Apps CLI extension is missing the required job logs show command.'
}

foreach ($provider in @('Microsoft.App', 'Microsoft.ContainerRegistry', 'Microsoft.DBforPostgreSQL')) {
    $state = Invoke-AzText -CommandArgs @('provider', 'show', '--namespace', $provider, '--query', 'registrationState', '--output', 'tsv', '--only-show-errors')
    if ($state -ne 'Registered') {
        Write-Host "Registering $provider."
        Invoke-Az -CommandArgs @('provider', 'register', '--namespace', $provider, '--wait', '--only-show-errors', '--output', 'none')
    }
}

if (-not $resourceGroupExists) {
    Write-Host "Creating the dedicated resource group $ResourceGroup."
    Invoke-Az -CommandArgs (@(
        'group', 'create', '--name', $ResourceGroup, '--location', $Location,
        '--tags') + $ProjectTags + @('--only-show-errors', '--output', 'none'))
}
else {
    Write-Host "Using the guarded dedicated resource group $ResourceGroup."
}

$registryExists = Test-AzCommand -CommandArgs @('acr', 'show', '--name', $RegistryName, '--resource-group', $ResourceGroup)
if (-not $registryExists) {
    Write-Host "Creating the dedicated Basic registry $RegistryName."
    Invoke-Az -CommandArgs (@(
        'acr', 'create', '--name', $RegistryName, '--resource-group', $ResourceGroup,
        '--location', $Location, '--sku', 'Basic', '--admin-enabled', 'true',
        '--tags') + $ProjectTags + @('--only-show-errors', '--output', 'none'))
}
else {
    $registry = (Invoke-AzText -CommandArgs @(
        'acr', 'show', '--name', $RegistryName, '--resource-group', $ResourceGroup,
        '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $registry -ResourceType 'Container registry' -ResourceName $RegistryName
    if (-not $registry.adminUserEnabled) {
        Write-Host "Enabling the administrator account on the guarded registry $RegistryName."
        Invoke-Az -CommandArgs @(
            'acr', 'update', '--name', $RegistryName, '--resource-group', $ResourceGroup,
            '--admin-enabled', 'true', '--only-show-errors', '--output', 'none'
        )
    }
}

$containerEnvironmentExists = Test-AzCommand -CommandArgs @('containerapp', 'env', 'show', '--name', $ContainerEnvironment, '--resource-group', $ResourceGroup)
if (-not $containerEnvironmentExists) {
    Write-Host "Creating the dedicated Container Apps environment $ContainerEnvironment."
    Invoke-Az -CommandArgs (@(
        'containerapp', 'env', 'create', '--name', $ContainerEnvironment,
        '--resource-group', $ResourceGroup, '--location', $Location,
        '--environment-mode', 'ConsumptionOnly', '--logs-destination', 'none',
        '--tags') + $ProjectTags + @('--only-show-errors', '--output', 'none'))
}
else {
    $containerEnvironmentRecord = (Invoke-AzText -CommandArgs @(
        'containerapp', 'env', 'show', '--name', $ContainerEnvironment,
        '--resource-group', $ResourceGroup, '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $containerEnvironmentRecord -ResourceType 'Container Apps environment' -ResourceName $ContainerEnvironment
}

$appExists = Test-AzCommand -CommandArgs @('containerapp', 'show', '--name', $ContainerApp, '--resource-group', $ResourceGroup)
$jobExists = Test-AzCommand -CommandArgs @('containerapp', 'job', 'show', '--name', $InitializationJob, '--resource-group', $ResourceGroup)
if ($appExists) {
    $existingApp = (Invoke-AzText -CommandArgs @(
        'containerapp', 'show', '--name', $ContainerApp, '--resource-group', $ResourceGroup,
        '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $existingApp -ResourceType 'Container App' -ResourceName $ContainerApp
}
if ($jobExists) {
    $existingJob = (Invoke-AzText -CommandArgs @(
        'containerapp', 'job', 'show', '--name', $InitializationJob,
        '--resource-group', $ResourceGroup, '--output', 'json', '--only-show-errors'
    )) | ConvertFrom-Json
    Assert-DedicatedResource -Resource $existingJob -ResourceType 'Container Apps job' -ResourceName $InitializationJob
}

$registryServer = Invoke-AzText -CommandArgs @(
    'acr', 'show', '--name', $RegistryName, '--resource-group', $ResourceGroup,
    '--query', 'loginServer', '--output', 'tsv', '--only-show-errors'
)
$registryCredentials = (Invoke-AzText -Sensitive -CommandArgs @(
    'acr', 'credential', 'show', '--name', $RegistryName, '--resource-group', $ResourceGroup,
    '--output', 'json', '--only-show-errors'
)) | ConvertFrom-Json
$registryUsername = [string]$registryCredentials.username
$registryPassword = [string]($registryCredentials.passwords | Where-Object { $_.value } | Select-Object -First 1).value
if (-not $registryServer -or -not $registryUsername -or -not $registryPassword) {
    throw 'Azure did not return complete credentials for the dedicated container registry.'
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$repositoryRootForGit = $repositoryRoot.Replace('\', '/')
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required to create a clean, commit-exact Azure build context.'
}
$gitTag = ((& git -c "safe.directory=$repositoryRootForGit" -C $repositoryRoot rev-parse --short=12 HEAD 2>$null) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $gitTag) {
    throw 'The deployment source must be a Git worktree with a committed HEAD.'
}
$gitChanges = ((& git -c "safe.directory=$repositoryRootForGit" -C $repositoryRoot status --porcelain --untracked-files=normal 2>$null) | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'Git could not verify the deployment worktree.'
}
if ($gitChanges) {
    throw 'Commit or remove all non-ignored worktree changes before deployment. Azure builds the exact committed HEAD.'
}

if (-not $ImageTag) {
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
    $ImageTag = "$gitTag-$stamp"
}
$image = "$registryServer/${ImageRepository}:$ImageTag"

Write-Host "Building $image from the committed root production Dockerfile."
$deploymentRoot = Join-Path $repositoryRoot '.deploy'
$buildContextRoot = Join-Path $deploymentRoot "acr-context-$ImageTag"
$archivePath = Join-Path $buildContextRoot 'source.tar'
New-Item -ItemType Directory -Path $buildContextRoot -Force | Out-Null
try {
    & git -c "safe.directory=$repositoryRootForGit" -C $repositoryRoot archive `
        '--format=tar' "--output=$archivePath" HEAD
    if ($LASTEXITCODE -ne 0) {
        throw 'Git failed to create the commit-exact Azure build archive.'
    }
    & tar -xf $archivePath -C $buildContextRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'The commit-exact Azure build archive could not be extracted.'
    }
    Remove-Item -LiteralPath $archivePath -Force
    Invoke-Az -CommandArgs @(
        'acr', 'build', '--registry', $RegistryName, '--resource-group', $ResourceGroup,
        '--image', "${ImageRepository}:$ImageTag", '--file', 'Dockerfile', $buildContextRoot,
        '--platform', 'linux/amd64', '--timeout', '3600', '--no-logs', '--only-show-errors')
}
finally {
    if (Test-Path -LiteralPath $buildContextRoot) {
        $resolvedBuildContext = (Resolve-Path -LiteralPath $buildContextRoot).Path
        $resolvedDeploymentRoot = (Resolve-Path -LiteralPath $deploymentRoot).Path
        if (-not $resolvedBuildContext.StartsWith($resolvedDeploymentRoot + '\')) {
            throw 'Build-context cleanup target escaped the repository deployment directory.'
        }
        Remove-Item -LiteralPath $resolvedBuildContext -Recurse -Force
    }
}

$serverExists = $preflightServerExists
$databasePassword = $null
$databaseUrl = $null

if ($env:HALLITRAP_DB_PASSWORD) {
    if (-not (Test-StrongPassword -Value $env:HALLITRAP_DB_PASSWORD)) {
        throw 'HALLITRAP_DB_PASSWORD does not meet the Azure PostgreSQL password policy.'
    }
    $databasePassword = $env:HALLITRAP_DB_PASSWORD
    $databaseUrl = New-DatabaseUrl -Password $databasePassword
}
elseif ($serverExists) {
    if ($appExists -and (Test-AzCommand -CommandArgs @('containerapp', 'secret', 'show', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--secret-name', $DatabaseSecretName))) {
        $candidate = Invoke-AzText -Sensitive -CommandArgs @('containerapp', 'secret', 'show', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--secret-name', $DatabaseSecretName, '--query', 'value', '--output', 'tsv', '--only-show-errors')
        if ($candidate -and (Test-DedicatedDatabaseUrl -Value $candidate)) {
            $databaseUrl = $candidate
        }
    }
    if (-not $databaseUrl -and $jobExists -and (Test-AzCommand -CommandArgs @('containerapp', 'job', 'secret', 'show', '--name', $InitializationJob, '--resource-group', $ResourceGroup, '--secret-name', $DatabaseSecretName))) {
        $candidate = Invoke-AzText -Sensitive -CommandArgs @('containerapp', 'job', 'secret', 'show', '--name', $InitializationJob, '--resource-group', $ResourceGroup, '--secret-name', $DatabaseSecretName, '--query', 'value', '--output', 'tsv', '--only-show-errors')
        if ($candidate -and (Test-DedicatedDatabaseUrl -Value $candidate)) {
            $databaseUrl = $candidate
        }
    }
    if (-not $databaseUrl) {
        Write-Host 'No stored deployment secret was available. Rotating the password on the dedicated server.'
        $databasePassword = New-StrongPassword
        $databaseUrl = New-DatabaseUrl -Password $databasePassword
    }
}
else {
    $databasePassword = New-StrongPassword
    $databaseUrl = New-DatabaseUrl -Password $databasePassword
}

if (-not (Test-DedicatedDatabaseUrl -Value $databaseUrl)) {
    throw 'The database secret does not point to the dedicated PostgreSQL server. Deployment stopped.'
}

$initCommand = 'cd /workspace && alembic -c api/alembic.ini upgrade head && python scripts/ingest.py && python scripts/verify_corpus.py'
$jobEnvironment = @(
    'DATABASE_URL=secretref:database-url',
    'CACHE_MODE=cached',
    'MODEL_CACHE_DIR=/models'
)

if (-not $jobExists) {
    Write-Host "Creating the one-time initialization job $InitializationJob."
    $jobArgs = @(
        'containerapp', 'job', 'create', '--name', $InitializationJob,
        '--resource-group', $ResourceGroup, '--environment', $ContainerEnvironment,
        '--trigger-type', 'Manual', '--replica-timeout', '3600',
        '--replica-retry-limit', '1', '--replica-completion-count', '1',
        '--parallelism', '1', '--image', $image, '--container-name', 'init',
        '--cpu', '2.0', '--memory', '4.0Gi',
        '--registry-server', $registryServer, '--registry-username', $registryUsername,
        '--registry-password', $registryPassword,
        '--secrets', "${DatabaseSecretName}=$databaseUrl", '--env-vars'
    ) + $jobEnvironment + @(
        '--command', '/bin/sh', '--args', '-c', $initCommand,
        '--tags') + $ProjectTags + @('--only-show-errors', '--output', 'none')
    Invoke-Az -Sensitive -CommandArgs $jobArgs
    $jobExists = $true
}
else {
    Write-Host "Updating the one-time initialization job $InitializationJob."
    Invoke-Az -Sensitive -CommandArgs @('containerapp', 'job', 'secret', 'set', '--name', $InitializationJob, '--resource-group', $ResourceGroup, '--secrets', "${DatabaseSecretName}=$databaseUrl", '--only-show-errors', '--output', 'none')
    Invoke-Az -Sensitive -CommandArgs @(
        'containerapp', 'job', 'registry', 'set', '--name', $InitializationJob,
        '--resource-group', $ResourceGroup, '--server', $registryServer,
        '--username', $registryUsername, '--password', $registryPassword,
        '--only-show-errors', '--output', 'none'
    )
    $updateJobArgs = @(
        'containerapp', 'job', 'update', '--name', $InitializationJob,
        '--resource-group', $ResourceGroup, '--image', $image, '--container-name', 'init',
        '--cpu', '2.0', '--memory', '4.0Gi', '--replica-timeout', '3600',
        '--replica-retry-limit', '1', '--set-env-vars'
    ) + $jobEnvironment + @('--command', '/bin/sh', '--args', '-c', $initCommand, '--only-show-errors', '--output', 'none')
    Invoke-Az -CommandArgs $updateJobArgs
}

if (-not $serverExists) {
    Write-Host "Creating the dedicated PostgreSQL 16 Burstable B1ms server $PostgresServer."
    Invoke-Az -Sensitive -CommandArgs (@(
        'postgres', 'flexible-server', 'create', '--name', $PostgresServer,
        '--resource-group', $ResourceGroup, '--location', $Location,
        '--admin-user', $DatabaseAdmin, '--admin-password', $databasePassword,
        '--version', '16', '--sku-name', 'Standard_B1ms', '--tier', 'Burstable',
        '--storage-size', '32', '--storage-auto-grow', 'Enabled',
        '--public-access', 'None', '--backup-retention', '7',
        '--geo-redundant-backup', 'Disabled', '--tags') + $ProjectTags + @(
        '--yes', '--only-show-errors', '--output', 'none'))
}
elseif ($databasePassword) {
    Write-Host 'Applying the supplied or rotated password to the dedicated PostgreSQL server.'
    Invoke-Az -Sensitive -CommandArgs @(
        'postgres', 'flexible-server', 'update', '--name', $PostgresServer,
        '--resource-group', $ResourceGroup, '--admin-password', $databasePassword,
        '--yes', '--only-show-errors', '--output', 'none')
}

Write-Host 'Allowing the pgvector extension and Azure-service connections.'
Invoke-Az -CommandArgs @(
    'postgres', 'flexible-server', 'update', '--name', $PostgresServer,
    '--resource-group', $ResourceGroup, '--public-access', 'Enabled',
    '--yes', '--only-show-errors', '--output', 'none')
Invoke-Az -CommandArgs @(
    'postgres', 'flexible-server', 'parameter', 'set', '--name', 'azure.extensions',
    '--server-name', $PostgresServer, '--resource-group', $ResourceGroup,
    '--value', 'vector', '--only-show-errors', '--output', 'none')

$firewallArgs = @(
    '--name', $PostgresServer, '--resource-group', $ResourceGroup,
    '--rule-name', 'AllowAzureServicesOnly', '--start-ip-address', '0.0.0.0',
    '--end-ip-address', '0.0.0.0', '--only-show-errors', '--output', 'none'
)
if (Test-AzCommand -CommandArgs @('postgres', 'flexible-server', 'firewall-rule', 'show', '--name', $PostgresServer, '--resource-group', $ResourceGroup, '--rule-name', 'AllowAzureServicesOnly')) {
    Invoke-Az -CommandArgs (@('postgres', 'flexible-server', 'firewall-rule', 'update') + $firewallArgs)
}
else {
    Invoke-Az -CommandArgs (@('postgres', 'flexible-server', 'firewall-rule', 'create') + $firewallArgs)
}

if (-not (Test-AzCommand -CommandArgs @('postgres', 'flexible-server', 'db', 'show', '--server-name', $PostgresServer, '--resource-group', $ResourceGroup, '--database-name', $DatabaseName))) {
    Write-Host "Creating database $DatabaseName."
    Invoke-Az -CommandArgs @(
        'postgres', 'flexible-server', 'db', 'create', '--server-name', $PostgresServer,
        '--resource-group', $ResourceGroup, '--database-name', $DatabaseName,
        '--charset', 'UTF8', '--only-show-errors', '--output', 'none')
}

Write-Host 'Starting migrations, corpus ingestion, and corpus verification.'
$executionName = Invoke-AzText -CommandArgs @(
    'containerapp', 'job', 'start', '--name', $InitializationJob,
    '--resource-group', $ResourceGroup, '--query', 'name', '--output', 'tsv',
    '--only-show-errors')
if (-not $executionName) {
    throw 'Azure did not return an initialization job execution name.'
}

$deadline = (Get-Date).AddMinutes(65)
$lastStatus = ''
while ((Get-Date) -lt $deadline) {
    $status = Invoke-AzText -CommandArgs @(
        'containerapp', 'job', 'execution', 'show', '--name', $InitializationJob,
        '--resource-group', $ResourceGroup, '--job-execution-name', $executionName,
        '--query', 'properties.status', '--output', 'tsv', '--only-show-errors')
    if ($status -ne $lastStatus) {
        Write-Host "Initialization job: $status"
        $lastStatus = $status
    }
    if ($status -eq 'Succeeded') { break }
    if ($status -in @('Failed', 'Stopped')) {
        Invoke-Az -CommandArgs @(
            'containerapp', 'job', 'logs', 'show', '--name', $InitializationJob,
            '--resource-group', $ResourceGroup, '--execution', $executionName,
            '--container', 'init', '--tail', '100', '--format', 'text', '--only-show-errors')
        throw "Initialization job ended with status $status."
    }
    Start-Sleep -Seconds 10
}
if ($lastStatus -ne 'Succeeded') {
    throw 'Initialization job did not finish in 65 minutes.'
}

$appEnvironment = @(
    'DATABASE_URL=secretref:database-url',
    'CACHE_MODE=cached',
    'MODEL_CACHE_DIR=/models'
)
if (-not $appExists) {
    Write-Host "Creating the public Container App $ContainerApp."
    $createAppArgs = @(
        'containerapp', 'create', '--name', $ContainerApp,
        '--resource-group', $ResourceGroup, '--environment', $ContainerEnvironment,
        '--image', $image, '--ingress', 'external', '--target-port', '8000',
        '--transport', 'auto', '--min-replicas', '1', '--max-replicas', '3',
        '--cpu', '2.0', '--memory', '4.0Gi', '--revisions-mode', 'single',
        '--registry-server', $registryServer, '--registry-username', $registryUsername,
        '--registry-password', $registryPassword,
        '--secrets', "${DatabaseSecretName}=$databaseUrl",
        '--env-vars') + $appEnvironment + @('--tags') + $ProjectTags + @('--only-show-errors', '--output', 'none')
    Invoke-Az -Sensitive -CommandArgs $createAppArgs
}
else {
    Write-Host "Updating the public Container App $ContainerApp."
    Invoke-Az -Sensitive -CommandArgs @('containerapp', 'secret', 'set', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--secrets', "${DatabaseSecretName}=$databaseUrl", '--only-show-errors', '--output', 'none')
    Invoke-Az -Sensitive -CommandArgs @(
        'containerapp', 'registry', 'set', '--name', $ContainerApp,
        '--resource-group', $ResourceGroup, '--server', $registryServer,
        '--username', $registryUsername, '--password', $registryPassword,
        '--only-show-errors', '--output', 'none'
    )
    Invoke-Az -CommandArgs (@(
        'containerapp', 'update', '--name', $ContainerApp, '--resource-group', $ResourceGroup,
        '--image', $image, '--cpu', '2.0', '--memory', '4.0Gi',
        '--min-replicas', '1', '--max-replicas', '3', '--set-env-vars') + $appEnvironment + @('--only-show-errors', '--output', 'none'))
    Invoke-Az -CommandArgs @('containerapp', 'ingress', 'enable', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--type', 'external', '--target-port', '8000', '--transport', 'auto', '--only-show-errors', '--output', 'none')
}

# Remove live secret references as soon as all secret-bearing commands finish.
$registryCredentials = $null
$registryUsername = $null
$registryPassword = $null
$databasePassword = $null
$databaseUrl = $null
$candidate = $null

$fqdn = Invoke-AzText -CommandArgs @('containerapp', 'show', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--query', 'properties.configuration.ingress.fqdn', '--output', 'tsv', '--only-show-errors')
if (-not $fqdn) { throw 'The Container App has no public FQDN.' }
$publicUrl = "https://$fqdn"
Invoke-Az -CommandArgs @('containerapp', 'update', '--name', $ContainerApp, '--resource-group', $ResourceGroup, '--set-env-vars', "CORS_ORIGINS=$publicUrl", '--only-show-errors', '--output', 'none')

Write-Host 'Waiting for the public health check.'
$healthy = $false
for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$publicUrl/health" -Method Get -TimeoutSec 20
        if ($health.status -eq 'ok' -and [int]$health.articles -eq 1422 -and $health.corpus_ready) {
            $healthy = $true
            break
        }
    }
    catch {
        # A new revision can take a short time before it accepts traffic.
    }
    Start-Sleep -Seconds 10
}
if (-not $healthy) {
    throw 'The public health check did not report the complete 1,422-article corpus.'
}

Write-Host ''
Write-Host "Public URL: $publicUrl"
Write-Host "Image: $image"
Write-Host ''
Write-Host 'Reproducible verification commands:'
Write-Host "Invoke-RestMethod '$publicUrl/health' | ConvertTo-Json"
Write-Host "node scripts/measure_fcp.mjs '$publicUrl'"
Write-Host "az containerapp job execution list --name $InitializationJob --resource-group $ResourceGroup --output table"
Write-Host "az containerapp show --name $ContainerApp --resource-group $ResourceGroup --query properties.configuration.ingress.fqdn --output tsv"
