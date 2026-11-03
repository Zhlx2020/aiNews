# AI 视界（ai-radar）

把国外一手 AI 资讯翻成中文并归档的自建信息台。跑在一台一次性 Linux 小机器上，页面是静态 HTML，纯 Python 标准库，无第三方依赖。

## 能力

- **采集**：Miniflux 订阅库（48 小时内，AI 关键词命中）、GitHub 新库搜索（14 天内 ★>15）、HackerNews 热榜（含热评正文）
- **翻译**：三级降级链 —— Google `dict-chrome-ex`（免 key，需浏览器 UA）→ 自建 LibreTranslate → MyMemory → 留原文
- **摘要**：LLM 链 —— AMD Radeon 网关（DeepSeek-V4.1-Flash → GLM-5.3-Flash）→ pollinations（免 key）→ AMD Qwen；全挂时退回"翻译正文两句"
- **版式**：浅色编辑排版，红标=资讯 / 绿标=项目，仓库名保留原文
- **归档**：资讯 24 小时、项目 72 小时后自动沉入 `history.html`（可搜索，最多 4000 条）
- **推送**：自建 ntfy，话题 `/airadar`

## 运行

```sh
mkdir -p /data/home/airadar
# 放入 airadar.py、loop.sh（见仓库）
setsid nohup sh /data/home/airadar/loop.sh >/dev/null 2>&1 &
python3 -m http.server 8091 --bind 0.0.0.0   # 静态托管
```

`loop.sh` 每 20 分钟跑一轮。首次运行会自建 `archive.jsonl` / `seen.json`。

## 配置点（airadar.py 顶部）

| 常量 | 说明 |
|---|---|
| `MF_BASE` / `MF_AUTH` | Miniflux 地址与 Basic 认证 |
| `LLM_CHAIN` | 模型链，按顺序降级；key 为空表示免鉴权 |
| `LT` / `MYMEM` | 翻译兜底端点 |
| `NTFY` | 推送地址 |
| `FRESH_HOURS` / `REPO_FRESH_HOURS` | 资讯/项目的保鲜期 |

## 文件

- `airadar.py` 主程序（采集 + 翻译 + 渲染 + 归档 + 推送）
- `loop.sh` 20 分钟调度
- `boot.sh` 开机自启（静态服务 + 循环）
- `.gitignore` 排除运行时产物（归档、状态、日志、生成的 HTML）

生成物不纳入版本库：`index.html`、`history.html`、`archive.jsonl`、`seen.json`、`*.log`。
