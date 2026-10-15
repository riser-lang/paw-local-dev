
param(
    [string]$TargetFile = "",
    [string]$ExpectedThumbprint = "B2D6EB9F292A758A7C45F993B838680BDCC26068"
)

# 1. Locate certificate strictly by thumbprint via .NET X509Store
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
    [System.Security.Cryptography.X509Certificates.StoreName]::My,
    [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
)
$store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)

$cert = $null
foreach ($c in $store.Certificates) {
    if ($c.Thumbprint -eq $ExpectedThumbprint) {
        $cert = $c
        break
    }
}
$store.Close()

if (-not $cert) {
    Write-Error "[CERT ERROR] Required code signing certificate with thumbprint $ExpectedThumbprint not found in CurrentUser\My!"
    exit 1
}

if (-not $cert.HasPrivateKey) {
    Write-Error "[CERT ERROR] Certificate $ExpectedThumbprint found but lacks private key!"
    exit 1
}

Write-Host "[CERT] Located existing certificate for sunao13:"
Write-Host "       Subject:    $($cert.Subject)"
Write-Host "       Thumbprint: $($cert.Thumbprint)"
Write-Host "       HasKey:     $($cert.HasPrivateKey)"

# 2. Sign the specified target file if provided
if ($TargetFile) {
    if (-not (Test-Path $TargetFile)) {
        Write-Error "[SIGN ERROR] Target file does not exist: $TargetFile"
        exit 1
    }

    Write-Host "[SIGN] Signing $TargetFile with certificate $ExpectedThumbprint..."
    $sig = Set-AuthenticodeSignature -FilePath $TargetFile -Certificate $cert -HashAlgorithm SHA256
    
    Write-Host "[SIGN] Status:      $($sig.Status)"
    Write-Host "[SIGN] Signer:      $($sig.SignerCertificate.Subject)"
    Write-Host "[SIGN] Thumbprint:  $($sig.SignerCertificate.Thumbprint)"

    if ($sig.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
        Write-Error "[SIGN ERROR] Signature thumbprint mismatch: $($sig.SignerCertificate.Thumbprint) != $ExpectedThumbprint"
        exit 1
    }
    Write-Host "[SIGN] Successfully signed with verified sunao13 thumbprint!"
}
