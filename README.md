# 抖音视频数据趋势分析

提供命令行采集脚本和 Windows 桌面应用。桌面应用包含实时时钟、内置浏览器、个人主页地址栏、视频数据表格和趋势图；定时保存公开主页可见的播放量与点赞量快照。

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

在应用内输入抖音个人主页 URL（格式为 `https://www.douyin.com/user/用户ID`），点击“打开主页”，再点击“开始采集”。应用会等待主页动态内容加载并尝试滚动加载视频，然后逐条打开识别到的视频详情页读取公开可见的点赞数。每条读取后都会先返回指定主页，待主页加载完成后再进入下一条，避免从详情页跳到推荐视频；视频之间间隔约 1.5 秒。视频较多时一次采集可能需要几分钟。点击“停止采集”可中止后续采集。应用内置浏览器也可用于导航和手动登录。数据保存在 `data/douyin_metrics.sqlite`，趋势图保存到 `data/metrics_trend.png`。

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

可通过 `--interval` 设置间隔秒数，`--database` 指定 SQLite 数据库，`--chart` 指定 PNG 图表输出路径，`--scrolls` 设置主页滚动次数。数据库和图表不会提交到 Git。

## 数据说明与限制

- 趋势来自本地重复采集的历史快照；首次运行只有一个数据点，持续运行后才能观察变化。
- 命令行脚本读取主页中明确标注的可见指标；桌面应用读取作品卡片上的播放数，并在视频详情页获取点赞数，不猜测未展示的数据。抖音页面结构或可见字段变化时，可能无法识别指标。
- 桌面应用逐个打开主页列出的视频详情页读取点赞数；受访问限制、页面变化或详情未加载影响时，该视频点赞量会留空。应用不会点击点赞按钮，也不会尝试绕过登录或访问限制。
- 仅适用于无需登录即可访问的公开主页；不会登录、绕过验证码、访问限制或平台反爬措施。若页面不公开播放量或点赞量，脚本会提示无法绘制相应趋势。
- 请遵守抖音服务条款、适用法律及数据主体隐私要求，并使用合理采集频率。

## 测试

```powershell
python -m unittest discover -s tests
```
