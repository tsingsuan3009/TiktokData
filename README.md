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

在应用内输入公开的抖音主页 URL，点击“打开主页”，再点击“开始采集”。应用首次采集后会按设定间隔继续保存数据；点击“停止采集”结束。应用内置浏览器也可用于导航到公开主页。数据保存在 `data/douyin_metrics.sqlite`，趋势图保存到 `data/metrics_trend.png`。

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
- 脚本只读取主页 DOM 中明确标注的播放量、点赞量，不猜测未展示的数值。抖音页面结构或可见字段变化时，可能无法识别指标。
- 仅适用于无需登录即可访问的公开主页；不会登录、绕过验证码、访问限制或平台反爬措施。若页面不公开播放量或点赞量，脚本会提示无法绘制相应趋势。
- 请遵守抖音服务条款、适用法律及数据主体隐私要求，并使用合理采集频率。

## 测试

```powershell
python -m unittest discover -s tests
```
