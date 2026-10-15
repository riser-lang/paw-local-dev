# PAW Personal Work OS v2.3.2 - Inno Setup 安装包逆向与保真度核验报告

**版本**：v2.3.2  
**状态**：提请外部评审  
**分析对象**：`PAW_Personal_Work_OS_Setup_v2.3.2.exe`  
**提取产物**：`CompiledCode.bin`  

---

## 1. 提取与哈希事实
使用 `innounp.exe` 从桌面正式安装包 `PAW_Personal_Work_OS_Setup_v2.3.2.exe` 中提取得到内嵌 Pascal 编译字节码：
- **文件名称**：`CompiledCode.bin`
- **文件大小**：8,931 字节
- **CompiledCode.bin SHA256 哈希值**：`5E116656B840A4BDF090B2EE095DD50CA25B65C8E337FC9DE6264D9AB4A7986F`

```log ref=02_Runtime_Verification_Logs/bytecode_strings.log
=== CompiledCode.bin String Extraction Log ===
Source file: C:\Users\sunao\.gemini\antigravity\brain\af0f00d3-464e-40fc-bd2f-fd235c3a6395\scratch\test_innounp\embedded\CompiledCode.bin
File size: 8931 bytes

```

---

## 2. 源码函数列表核验 (`installer/paw_setup.iss`)
经核对开源安装脚本 `installer/paw_setup.iss:76-285`，其 [Code] 段实际定义且编译进入字节码的函数共 10 个：
1. `procedure RequestGracefulShutdown();` (`installer/paw_setup.iss:76`)
2. `function GetInstalledUninstallString(): string;` (`installer/paw_setup.iss:86`)
3. `function GetInstalledDisplayVersion(): string;` (`installer/paw_setup.iss:97`)
4. `function InitializeSetup(): Boolean;` (`installer/paw_setup.iss:109`)
5. `function InitializeUninstall(): Boolean;` (`installer/paw_setup.iss:170`)
6. `procedure BackupDatabaseBeforeInstall();` (`installer/paw_setup.iss:177`)
7. `procedure InitializeUserDataDirectory();` (`installer/paw_setup.iss:191`)
8. `procedure CurStepChanged(CurStep: TSetupStep);` (`installer/paw_setup.iss:229`)
9. `function ShouldPurgeData(): Boolean;` (`installer/paw_setup.iss:250`)
10. `procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);` (`installer/paw_setup.iss:268`)

**端口配置**：`installer/paw_setup.iss:9` 中定义 `#define MyAppURL "http://127.0.0.1:8800/app"`，默认端口为 8800。

---

## 3. 字节码字符串分析
运行字符串提取工具对 `CompiledCode.bin` 进行分析，其提取出的 ASCII 符号表完全覆盖上述 10 个函数名称（如 `REQUESTGRACEFULSHUTDOWN`、`GETINSTALLEDUNINSTALLSTRING`、`INITIALIZESETUP`、`CURSTEPCHANGED` 等）。完整提取清单见 `02_Runtime_Verification_Logs/bytecode_strings.log`。经核验，安装脚本未包含任何自定义向导页、端口探测或外部网络库引用，仅包含真实的 10 个安装生命周期过程与函数。
