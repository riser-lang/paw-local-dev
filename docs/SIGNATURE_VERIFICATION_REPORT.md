# PAW Personal Work OS v2.3.2 - sunao13 防伪数字签名核验报告

**版本**：v2.3.2  
**状态**：提请外部评审  
**签名证书**：`sunao13_防伪公钥证书.cer`  

---

## 1. 签名证书属性
- **主题 (Subject)**：`CN=sunao13, O=sunao13 Personal Work OS, OU=Anti-counterfeit`
- **证书指纹 (SHA-1)**：`B2D6EB9F292A758A7C45F993B838680BDCC26068`
- **公钥文件哈希 (SHA256)**：`sunao13_防伪公钥证书.cer` 对应哈希值为 `544B841BE6F6AD262F98E2BFD5219F89443C323791C2BA0FD6BEDED6DA44AFC8`

---

## 2. 签名目标与核验结果
已使用该证书对构建产物进行 PE 代码签名：
- **安装包**：`PAW_Personal_Work_OS_Setup_v2.3.2.exe`，SHA256 为 `45D2CFEF237EDF3C9F03A82561F872937DDC25980CC3ADB09B8EC351546F5B10`
- **可执行文件**：`PAW_Personal_Work_OS.exe`

使用 PowerShell `Get-AuthenticodeSignature` 执行核验的原始输出片段：

```log ref=02_Runtime_Verification_Logs/signatures_verification.log
File:        PAW_Personal_Work_OS_Setup_v2.3.2.exe
Path:        C:\Users\sunao\Desktop\PAW_Personal_Work_OS_Setup_v2.3.2.exe
Signer:      CN=sunao13, O=sunao13 Personal Work OS, OU=Anti-counterfeit
Thumbprint:  B2D6EB9F292A758A7C45F993B838680BDCC26068
Status:      UnknownError
==================================================
```

---

## 3. 防伪机制与信任说明
本签名属于开发者自制防伪证书，未向公共商业 CA 申请背书，亦无联网时间戳服务。因此在未手动导入该证书公钥的第三方 Windows 操作系统中，系统属性或命令行核验将提示 `Status: UnknownError`（证书链终止于不受信任的根证书）。

本防伪体系的设计目的并非建立商业公网信任链，而是提供**确定性的所有权与防篡改验证**：任何接收方均可通过比对可执行文件签名证书的指纹是否严格等于 `B2D6EB9F292A758A7C45F993B838680BDCC26068`，确认为 sunao13 亲自构建发布的原始程序。
