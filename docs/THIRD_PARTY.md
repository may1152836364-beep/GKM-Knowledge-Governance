# 第三方组件

Windows 候选便携包包含 CPython 3.12.10（Python Software Foundation，官方 NuGet python 包）、PySide6/Qt 6.8.3（Qt Company，LGPLv3/GPLv3/商业授权）、openpyxl、httpx、jsonschema 及其依赖。原始 Python LICENSE、各 wheel 的 `.dist-info` 许可证和 Qt 随包许可文件保留在 runtime 目录。

Qt 动态链接，程序不修改 Qt 库，用户可在符合相同 ABI 的前提下替换 runtime/Lib/site-packages/PySide6 中的动态库。Qt/PySide 对应源码与许可： https://code.qt.io/pyside/pyside-setup.git/ ， https://code.qt.io/qt/ ， https://www.qt.io/licensing/open-source-lgpl-obligations 。正式对外发行前应由发行方复核许可证义务。

项目源码中不包含公司文章、真实密钥或第三方私有模型。候选 EXE 未代码签名。SHA256 清单用于校验完整性，不代替发行方身份签名。
