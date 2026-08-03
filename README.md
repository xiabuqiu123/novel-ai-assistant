# 书镜辨章 · 长篇小说 AI 分析助手

面向百万字级长篇小说的 Windows 桌面 AI 分析工具：导入 TXT 小说后，自动建立结构化知识库，支持分层摘要、角色/关系/设定/事件抽取、时间线、设定冲突检测与基于证据的问答。

> 产品定位：AI 辅助的「小说编辑与知识库工具」，所有 AI 结论都要求带原文证据，冲突与编辑判断保留人工复核流程。

## 功能

- TXT 导入：编码自动检测、章节识别、稳定分块
- 分层摘要：分块 → 章节 → 卷（约 200 章）→ 全书大纲
- 批量抽取：角色档案、人物关系、世界规则、势力、地点、事件、设定事实（全部带章节级原文证据）
- 设定冲突检测：角色档案/世界规则/时间线/物品/剧情逻辑/关系六类冲突，证据引用 + 人工复核工作流
- 证据问答：两级检索（事实库 + 章节摘要索引），回答必须引用章节证据，区分事实/推断/建议
- 整书批量分析：进度、取消、断点续跑
- Markdown 报告导出；模型/API 设置与用量统计
- 全中文界面（Windows 桌面；Android APK 构建能力保留）

## 技术栈

- 前端：Flutter（Windows 桌面 / Android），23 个中文页面
- 后端：FastAPI（本地服务，默认 127.0.0.1:8000），SQLite 存储
- 模型：用户自配任意 OpenAI 兼容 API（仅此一项外部依赖，无账号、无云同步）

## 快速开始

1. 安装 [Flutter](https://flutter.dev)（Windows 桌面支持）与 Python 3.11+
2. 后端：`python -m pip install -r backend/requirements.txt`，然后运行 `run_backend.ps1`
3. 前端：`cd frontend && flutter pub get && flutter build windows`
4. 打开应用 → 「模型与 API 设置」填入你的模型服务地址与密钥
5. 导入 TXT 小说，运行「整书分析」

## 下载成品（Windows）

不想自己编译？直接到 [Releases](https://github.com/xiabuqiu123/novel-ai-assistant/releases) 下载 `书镜辨章-v1.0-windows.zip`，解压后双击「启动书镜辨章.bat」即可使用；首次使用需在「模型与 API 设置」里填入自己的模型服务地址与密钥。

## 数据与安全

- 所有数据保存在本机 SQLite，不上传、不云同步
- 模型调用遵循「分层摘要/分批抽取」，全书文本永不单次发送给模型
- 失败与未通过校验的模型结果不会写入缓存，重试会真实重新调用

## 许可证

MIT License，详见 [LICENSE](LICENSE)。
