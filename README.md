# GKM-Knowledge-Governance

KM 知识有效性智能治理助手 · PRD V4.0 开发候选版 0.1.0。

原生中文 Windows 桌面应用：Excel 增量导入、SQLite 文章版本管理、可配置 Chat Completions 模型、全量分段 AI、证据校验、暂停恢复队列、人工复核、审核案例检索与提示词回归。**不自动上下架，不含遥测，不默认向任何在线模型发送数据。**

Windows 用户：完整解压 `GKM-Windows-x64.zip` 后双击 `GKM.exe`。内置运行时，无需安装 Python，无本地监听端口。数据独立存放在 `%LOCALAPPDATA%\GKM`，密钥使用当前用户 DPAPI。此为未代码签名的候选包，干净 Windows 启动与真实模型验收待完成。

- [使用手册](docs/USER_GUIDE.md)
- [架构与开发计划](docs/ARCHITECTURE.md)
- 原始 PRD 保留在本地，不随 GitHub 源码或运行包发布。
- [需求验收追踪](docs/ACCEPTANCE.md)
- [自动化测试报告](docs/TEST_REPORT.md)
- [20 万篇模拟性能报告](docs/PERFORMANCE.json)
- [JSON 结果契约](docs/RESULT_SCHEMA.json)
- [数据库结构](docs/SCHEMA.sql)

## 开发与测试

开发机需 Python 3.12；这不是最终 Windows 用户的依赖。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests -q
.venv/bin/python -m gkm
```

交互窗口需要桌面显示；无显示的云环境使用 `QT_QPA_PLATFORM=offscreen` 做 Qt 测试。Linux 不允许保存生产 API Key，没有明文回退；测试注入内存密钥库。不要把测试内存密钥库用于生产。

## Windows 构建

在 Windows 开发机 / GitHub Actions 执行 `scripts/build_windows.ps1`：安装锁定运行依赖、运行含 DPAPI 的测试、使用 PyInstaller 生成独立 GUI 运行包。CI 配置为手动执行或版本 tag，不会自行推送或发布。

当前 Linux 已提供跨构建便携包路线：官方 CPython Windows NuGet 包（固定 SHA512）+ Windows wheels（逐文件 SHA256）+ Zig 0.13.0 编译的原生 GUI 启动器。构建工具不属于用户运行依赖。

```bash
python -m pip install ziglang==0.13.0
python -m pip download --dest /workspace/gkm-windows-wheels --platform win_amd64 --python-version 312 --implementation cp --abi cp312 --only-binary=:all: --require-hashes -r requirements-windows.lock
python scripts/build_portable.py --wheels /workspace/gkm-windows-wheels --python-package /workspace/python.3.12.10.nupkg
```

构建成功和 PE 静态检查不等于干净 Windows 实际运行验收。源码、文档和依赖校验资料均保留；公司知识正文、真实密钥和本地数据库不提交 Git。

## 验证真实样本 / 大规模模拟

```bash
python scripts/inspect_sample.py /private/知识总表20261012AI投喂.xlsx --output /private/sample-report.json
python scripts/stress.py --count 200000 --engine-items 200000 --output docs/PERFORMANCE.json
```

样本诊断报告只包含字段与统计；真实文件暂未收到，文档中的历史 151 篇等统计未硬编码为测试结果。压测使用生成的中英文文章和本地模拟 API，不产生真实模型费用。业务 precision/recall/F1 必须由真实人工金标准集计算，不能用模拟测试替代。

## 空白数据发布

GitHub 预发布运行包不包含文章、案例、任务、复核记录、模型配置、密钥或原始 PRD。测试代码保留，测试样本只在临时目录生成。用户已有本地数据不会自动删除。版本标签触发 Windows 测试和打包，全部检查通过后生成 Releases 下载附件。
