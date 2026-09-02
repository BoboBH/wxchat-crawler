# 尖峰B辅助:开启/关闭用户级系统代理。用法:
#   开:powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1
#   关:powershell -ExecutionPolicy Bypass -File tools/spike_set_proxy.ps1 -Off
param([switch]$Off, [int]$Port = 8888)
$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
if ($Off) {
    Set-ItemProperty -Path $k -Name ProxyEnable -Value 0
} else {
    Set-ItemProperty -Path $k -Name ProxyEnable -Value 1
    Set-ItemProperty -Path $k -Name ProxyServer -Value "127.0.0.1:$Port"
}
Add-Type -TypeDefinition @"
using System; using System.Runtime.InteropServices;
public class WinInet {
    [DllImport("wininet.dll")] public static extern bool InternetSetOption(IntPtr h, int opt, IntPtr buf, int len);
}
"@
[WinInet]::InternetSetOption([IntPtr]::Zero, 39, [IntPtr]::Zero, 0) | Out-Null
[WinInet]::InternetSetOption([IntPtr]::Zero, 37, [IntPtr]::Zero, 0) | Out-Null
Write-Host "系统代理已 $(if ($Off) { '关闭' } else { "指向 127.0.0.1:$Port" })"
