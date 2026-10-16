# GKM Windows 开发候选版 · 空白业务数据

完整解压 `GKM-Windows-x64.zip`，进入 GKM 文件夹，双击 `GKM.exe`。内置运行时，不需要另装 Python；应用不监听本地端口。

本次发布不预装文章、案例、任务、复核记录、模型配置或密钥。测试文章截图、原始 PRD 和公司 Excel 不包含在发布文件中。源代码保留自动化测试；测试临时生成的样本不进入运行包。应用不会删除用户以前导入的本地数据。

配置模型并导入自己的 Excel 后使用。API Key 只在程序内录入，由 Windows DPAPI 保存，勿发到聊天、Issue 或 Git 仓库。

这是开发候选版，不是全部 P0 已正式签收的生产版本。真实 Excel 核对、批准的模型联调、真实人工金标准质量评估、干净 Windows 的人工启动和恢复验收仍待完成。GitHub Actions 的 Windows 测试结果随附 `windows-tests.xml`；构建不通过不会发布包。

下载：`GKM-Windows-x64.zip` 为普通用户运行包；`GKM-Source.zip` 为源码；`SHA256SUMS.txt` 为校验值。
