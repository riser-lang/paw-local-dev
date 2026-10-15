param(
    [string]$FilePath = "dist\PAW_Personal_Work_OS_Setup.exe"
)

$target = (Resolve-Path $FilePath).Path
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -like "*PAW*" } | Select-Object -First 1

if (-not $cert) {
    Write-Host "[SIGN] Generating local Authenticode Code Signing Certificate for PAW Work OS..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=PAW Personal Work OS, O=PAW Work OS Team, C=CN" -CertStoreLocation "Cert:\CurrentUser\My" -NotAfter (Get-Date).AddYears(5)
}

Write-Host "[SIGN] Using cert:" $cert.Subject $cert.Thumbprint
$sig = Set-AuthenticodeSignature -FilePath $target -Certificate $cert
Write-Host "[SIGN] Result Status:" $sig.Status
Write-Host "[SIGN] Status Message:" $sig.StatusMessage
