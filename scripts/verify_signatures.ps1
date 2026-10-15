Import-Module Microsoft.PowerShell.Security

$files = @(
    "D:\PAW\paw-beta\dist\PAW_Personal_Work_OS\PAW_Personal_Work_OS.exe",
    "D:\PAW\paw-beta\dist\PAW_Personal_Work_OS_Setup.exe",
    "C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.2.exe",
    "C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.1.exe",
    "C:\Users\sunao\AppData\Local\Programs\PAW Personal Work OS\PAW_Personal_Work_OS.exe"
)

foreach ($f in $files) {
    if (Test-Path $f) {
        $sig = Get-AuthenticodeSignature $f
        Write-Host "=================================================="
        Write-Host "File:       " (Split-Path $f -Leaf)
        Write-Host "Path:       " $f
        Write-Host "Signer:     " $sig.SignerCertificate.Subject
        Write-Host "Thumbprint: " $sig.SignerCertificate.Thumbprint
        Write-Host "Status:     " $sig.Status
    } else {
        Write-Host "File not found: $f"
    }
}
