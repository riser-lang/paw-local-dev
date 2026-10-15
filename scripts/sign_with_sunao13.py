# -*- coding: utf-8 -*-
r"""Sign PAW executables with custom anti-counterfeiting digital signature: sunao13.

Strict requirements:
- Strictly select certificate by thumbprint B2D6EB9F292A758A7C45F993B838680BDCC26068 from Cert:\CurrentUser\My.
- If not found or lacking private key, exit with error immediately.
- Do NOT create any new certificate.
- Do NOT modify certificate stores (no Root/TrustedPublisher import).
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
EXPECTED_THUMBPRINT = "B2D6EB9F292A758A7C45F993B838680BDCC26068"

PS_SCRIPT = r"""
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
"""

def run_sign(target_file: Path = None):
    ps_file = ROOT_DIR / "scripts" / "_run_sign.ps1"
    with open(ps_file, "w", encoding="utf-8") as f:
        f.write(PS_SCRIPT)
    
    ps_exe = "pwsh.exe" if shutil.which("pwsh.exe") else "powershell.exe"
    cmd = [
        ps_exe,
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps_file),
        "-ExpectedThumbprint", EXPECTED_THUMBPRINT,
    ]
    if target_file:
        cmd.extend(["-TargetFile", str(target_file)])
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(res.stdout)
    if res.stderr:
        print("[STDERR]", res.stderr)
    return res.returncode

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    target_path = Path(target) if target else None
    code = run_sign(target_path)
    sys.exit(code)
