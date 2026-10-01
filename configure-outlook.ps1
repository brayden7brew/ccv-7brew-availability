$ErrorActionPreference = 'Stop'
$ccvAppId = '298269c1-e359-42ba-aea2-01f0d711b9b6'
$ccvObjectId = 'd4c01e78-3086-493e-ad45-fb5ab22244fa'
$ccvMailbox = 'alerts@rva7brew.com'
$ccvScopeName = 'CCV Availability Alerts Only'
$ccvAssignment = 'CCV Availability Send Alerts'
$ccvFilter = "PrimarySmtpAddress -eq 'alerts@rva7brew.com'"

if (-not (Get-Module -ListAvailable ExchangeOnlineManagement)) {
    Install-Module ExchangeOnlineManagement -Scope CurrentUser -Repository PSGallery
}
Import-Module ExchangeOnlineManagement
Connect-ExchangeOnline -Device -ShowBanner:$false
try {
    $ccvMailboxRecord = Get-EXOMailbox -Identity $ccvMailbox
    if ([string]$ccvMailboxRecord.PrimarySmtpAddress -ne $ccvMailbox) { throw 'Mailbox address did not match.' }
    $ccvPrincipal = Get-ServicePrincipal | Where-Object { $_.AppId -eq $ccvAppId }
    if (-not $ccvPrincipal) {
        New-ServicePrincipal -AppId $ccvAppId -ObjectId $ccvObjectId -DisplayName 'CCV 7 Brew Availability' | Out-Null
    } elseif ([string]$ccvPrincipal.ObjectId -ne $ccvObjectId) {
        throw 'Existing Exchange application identity does not match. No permissions assigned.'
    }
    $ccvScope = Get-ManagementScope | Where-Object { $_.Name -eq $ccvScopeName }
    if (-not $ccvScope) {
        $ccvScope = New-ManagementScope -Name $ccvScopeName -RecipientRestrictionFilter $ccvFilter
    }
    $ccvMembers = @(Get-Recipient -RecipientPreviewFilter $ccvScope.RecipientFilter)
    if ($ccvMembers.Count -ne 1 -or [string]$ccvMembers[0].PrimarySmtpAddress -ne $ccvMailbox) {
        throw 'Scope must contain only alerts@rva7brew.com. No new permission assigned.'
    }
    $ccvExisting = Get-ManagementRoleAssignment | Where-Object { $_.Name -eq $ccvAssignment }
    if (-not $ccvExisting) {
        New-ManagementRoleAssignment -Name $ccvAssignment -Role 'Application Mail.Send' -App $ccvObjectId -CustomResourceScope $ccvScopeName | Out-Null
    } else {
        Write-Host 'Existing named assignment found; review the test results below.'
    }
    Write-Host 'Alerts mailbox: Application Mail.Send should show InScope True.'
    Test-ServicePrincipalAuthorization -Identity $ccvObjectId -Resource $ccvMailbox | Format-Table RoleName,GrantedPermissions,InScope -AutoSize
    Write-Host 'Brayden mailbox: Application Mail.Send should show InScope False.'
    Test-ServicePrincipalAuthorization -Identity $ccvObjectId -Resource 'brayden@rva7brew.com' | Format-Table RoleName,GrantedPermissions,InScope -AutoSize
    Write-Host 'No email sent. These tests cover Exchange RBAC, not separate Entra permissions.'
} finally {
    Disconnect-ExchangeOnline -Confirm:$false
}
