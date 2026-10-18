# 抖音视频数据采集

提供命令行采集脚本和 Windows 桌面应用。桌面应用包含实时时钟、内置浏览器、个人主页地址栏、实时采集进度和视频数据列表，可将列表导出为 CSV。程序定时保存公开主页可见的播放量与点赞量快照，不再生成或显示趋势图。

## 环境准备

建议使用 Python 3.10 或更新版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 桌面应用

```powershell
python douyin_analyzer_gui.py
```

在应用内输入抖音个人主页 URL（格式为 `https://www.douyin.com/user/用户ID`），点击“打开主页”，再点击“开始采集”。应用会读取主页显示的作品总数，并在主页作品列表的滚动区域逐屏下拉以加载全部作品；只有去重后的视频数量与主页显示数量完全一致，才会开始采集点赞数。不完整或超出数量时会取消采集并提示，不会保存不匹配的结果。之后应用逐条打开视频详情页读取公开可见的点赞数，每条读取后先返回指定主页，待主页加载完成后再进入下一条，避免跳到推荐视频。视频之间间隔约 1.5 秒，视频较多时一次采集可能需要几分钟。点击“停止采集”可中止后续采集。完成后可点击“导出 CSV”保存当前作品列表。应用内置浏览器也可用于导航和手动登录。源码运行时数据库保存在 `data/douyin_metrics.sqlite`；封装版数据库和浏览器会话保存在当前 Windows 用户的 `%LOCALAPPDATA%\DouyinDataAnalyzer\DouyinDataAnalyzer` 下。

### 封装为 Windows 应用

安装打包依赖并构建目录版应用（需在 Windows 上构建）：

```powershell
python -m pip install -r requirements-build.txt
.\build_windows.ps1
```

构建产物位于 `dist\DouyinDataAnalyzer\DouyinDataAnalyzer.exe`。请整体分发 `dist\DouyinDataAnalyzer` 目录，不要只复制 EXE；Qt WebEngine 依赖旁边的运行时文件。`build/` 和 `dist/` 均不会提交到 Git。

### 记忆登录

- 可选输入登录用户名并勾选“记住用户名”，用户名保存在当前 Windows 用户的 Qt 应用设置中；取消勾选即可移除。
- 可在内置浏览器中手动登录。浏览器 Cookie、缓存和站点存储保存在 `%LOCALAPPDATA%\DouyinDataAnalyzer\DouyinDataAnalyzer\browser-profile`，以便下次启动时沿用浏览器会话。
- 应用不会读取或保存密码。登录会话相当于登录凭据，请勿在共用或不受信任的 Windows 账户中启用；点击“清除记忆登录”可以清除已记住的用户名、浏览器 Cookie、缓存和当前站点存储。站点存储只在当前页面允许访问时清除；若提示未能清除，请在抖音页面中重试。

## 命令行用法

仅采集一次：

```powershell
python douyin_analyzer.py "https://www.douyin.com/user/你的用户ID" --once
```

按默认 5 分钟间隔持续采集（按 `Ctrl+C` 停止）：

```powershell
python douyin_analyzer.py "https://www.douyin.com/user/你的用户ID"
```

可通过 `--interval` 设置间隔秒数，`--database` 指定 SQLite 数据库，`--scrolls` 设置主页滚动次数。CLI 和桌面应用均将采集结果保存为 SQLite 快照；桌面应用另可导出当前数据列表为 CSV。

## 数据说明与限制

- 命令行脚本读取主页中明确标注的可见指标；桌面应用读取作品卡片上的播放数，并在视频详情页获取点赞数，不猜测未展示的数据。抖音页面结构或可见字段变化时，可能无法识别指标。
- 桌面应用逐个打开主页列出的视频详情页读取点赞数；受访问限制、页面变化或详情未加载影响时，该视频点赞量会留空。应用不会点击点赞按钮，也不会尝试绕过登录或访问限制。
- 仅适用于公开主页可见的数据；不会绕过验证码、访问限制或平台反爬措施。若页面未公开播放量或点赞量，该字段会留空。
- 请遵守抖音服务条款、适用法律及数据主体隐私要求，并使用合理采集频率。

## 测试

```powershell
python -m unittest discover -s tests
```
