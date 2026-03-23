param(
    [Parameter(Mandatory = $true)]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$ServerName,

    [string]$TargetServerName,
    [string]$RestoreTimeUtc,
    [string]$BackupName,
    [string]$DatabaseName,
    [bool]$UseManagedIdentity = $true
)

$ErrorActionPreference = "Stop"

function Ensure-AzCli {
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        throw "Azure CLI is not installed in the container."
    }
}

function Connect-Azure {
    $account = az account show --output json --only-show-errors 2>$null
    if (-not $account) {
        if (-not $UseManagedIdentity) {
            throw "Azure CLI is not logged in and managed identity login is disabled."
        }

        az login --identity --output none --only-show-errors | Out-Null
    }

    az account set --subscription $SubscriptionId --only-show-errors
}

function Invoke-AzJson {
    param([string[]]$Arguments)

    $output = az @Arguments --output json --only-show-errors 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output | Out-String).Trim()
    }

    if (-not $output) {
        return @{}
    }

    return ($output | Out-String | ConvertFrom-Json -Depth 12)
}

function Invoke-AzNone {
    param([string[]]$Arguments)

    $output = az @Arguments --output json --only-show-errors 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output | Out-String).Trim()
    }

    if (-not $output) {
        return @{ status = "ok" }
    }

    try {
        return ($output | Out-String | ConvertFrom-Json -Depth 12)
    } catch {
        return @{ status = "ok"; raw = ($output | Out-String).Trim() }
    }
}

Ensure-AzCli
Connect-Azure

$result = switch ($Action) {
    "show-server" {
        Invoke-AzJson @(
            "postgres", "flexible-server", "show",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName
        )
        break
    }
    "list-backups" {
        Invoke-AzJson @(
            "postgres", "flexible-server", "backup", "list",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName
        )
        break
    }
    "create-backup" {
        if (-not $BackupName) {
            throw "BackupName is required for create-backup."
        }

        Invoke-AzNone @(
            "postgres", "flexible-server", "backup", "create",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName,
            "--backup-name", $BackupName
        )
        break
    }
    "restore-server" {
        if (-not $TargetServerName) {
            throw "TargetServerName is required for restore-server."
        }
        if (-not $RestoreTimeUtc) {
            throw "RestoreTimeUtc is required for restore-server."
        }

        $normalizedRestoreTime = [DateTimeOffset]::Parse($RestoreTimeUtc).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

        Invoke-AzNone @(
            "postgres", "flexible-server", "restore",
            "--resource-group", $ResourceGroup,
            "--source-server", $ServerName,
            "--name", $TargetServerName,
            "--restore-time", $normalizedRestoreTime
        )
        break
    }
    "start-server" {
        Invoke-AzNone @(
            "postgres", "flexible-server", "start",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName
        )
        break
    }
    "stop-server" {
        Invoke-AzNone @(
            "postgres", "flexible-server", "stop",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName
        )
        break
    }
    "restart-server" {
        Invoke-AzNone @(
            "postgres", "flexible-server", "restart",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName
        )
        break
    }
    "delete-server" {
        Invoke-AzNone @(
            "postgres", "flexible-server", "delete",
            "--resource-group", $ResourceGroup,
            "--name", $ServerName,
            "--yes"
        )
        break
    }
    "delete-database" {
        if (-not $DatabaseName) {
            throw "DatabaseName is required for delete-database."
        }

        Invoke-AzNone @(
            "postgres", "flexible-server", "db", "delete",
            "--resource-group", $ResourceGroup,
            "--server-name", $ServerName,
            "--database-name", $DatabaseName,
            "--yes"
        )
        break
    }
    default {
        throw "Unsupported action: $Action"
    }
}

$result | ConvertTo-Json -Depth 12 -Compress
