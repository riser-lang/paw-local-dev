# PAW Personal Work OS - 外部评审 (Claude Judge) 专项审计包

**版本**：v2.3.2 (Phase B 完整发布版)  
**状态**：提请外部评审  
**基线提交**：`27ab3ef` (baseline: v2.3.1 as judged)  

---

## 1. 目录结构与内容说明

- **`00_EXECUTIVE_AUDIT_REPORT_FOR_CLAUDE.md`**：综合执行审计报告，针对外部评审 F1–F10 逐条归正并提供确切代码与日志引用。
- **`CHANGES_SINCE_JUDGE.md`**：针对外部评审发现项整改对照总表与已知问题说明。
- **`01_Latest_Source_Code/`**：当前工程的完整源码镜像（已排除二进制与临时文件）。
- **`02_Runtime_Verification_Logs/`**：全部自动化测试与诊断的原始执行日志，以及 A25/A28 的观察与反向对照日志。
- **`03_Decompilation_And_Binary_Fidelity/`**：Inno Setup 安装包逆向字节码 `CompiledCode.bin` 与数字签名报告。
- **`04_Requirement_Traceability_Matrix/`**：核心需求追踪矩阵与提供给委托人的手动测试清单。
- **`05_Historical_Unverified/`**：历史旧版本报告与旧日志归档（本轮未复核，不作为验收依据）。
- **`MANIFEST.sha256`**：全量文件 SHA256 清单（含安装包与防伪证书）。
- **`gate_report.log`**：自我验收闸门 (`judge_gate.py`) 的最新执行报告。

---

## 2. 外部评审复核指引

在审计包根目录下执行自我验收闸门脚本即可快速复核全包合规性：

```bash
python scripts/judge_gate.py .
```

该闸门会自动核验：代码引用存在性、代码片段逐字一致性、日志片段连续一致性、哈希一致性、证书指纹一致性、禁用词与空断言检测、以及工程卫生。
