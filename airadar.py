#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 视界 v4：浅色编辑排版，资讯/项目双轨，24 小时后自动归档到历史页。

翻译降级链: Google(dict-chrome-ex) -> 自建 LibreTranslate -> MyMemory -> 原文
"""
import base64
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

OUT_DIR = "/data/home/airadar"


def load_env(path=OUT_DIR + "/.env"):
    """读 .env（KEY=VALUE），已有环境变量优先。"""
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


load_env()
OUT = OUT_DIR + "/index.html"
HISTORY = OUT_DIR + "/history.html"
ARCHIVE = OUT_DIR + "/archive.jsonl"
STATE = OUT_DIR + "/seen.json"
LOG = OUT_DIR + "/run.log"
SNAP_DIR = OUT_DIR + "/snap"
SNAP_KEEP = 1500

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
GOOGLE = ("https://translate.googleapis.com/translate_a/single"
          "?client=dict-chrome-ex&sl=en&tl=zh-CN&dt=t&q=")
C5 = ("https://clients5.google.com/translate_a/t"
      "?client=dict-chrome-ex&sl=auto&tl=zh-CN&q=")
LT = os.environ.get("LT_URL", "https://5001-agq2az3swngxyr5doddjazpmde.instabox.tech/translate")
MYMEM = "https://api.mymemory.translated.net/get?langpair=en%7Czh-CN&q="
NTFY = os.environ.get("NTFY_URL", "https://8088-agq2az3swngxyr5doddjazpmde.instabox.tech/airadar")
BASE = os.environ.get("PANEL_URL", "https://8091-agq2autjlenu2z2aiclsdk4bq4.instabox.tech")

LLM_BASE = os.environ.get("LLM_URL", "https://developer.amd.com.cn/radeon/api/v1/chat/completions")
LLM_KEY = os.environ.get("LLM_KEY", "")
LLM_MODELS = [m for m in os.environ.get("LLM_MODELS", "DeepSeek-V4.1-Flash,GLM-5.3-Flash").split(",") if m]

LLM_CHAIN = []
if LLM_KEY:
    LLM_CHAIN.append((LLM_BASE, LLM_KEY, LLM_MODELS))
# 免 key 备胎
LLM_CHAIN.append(("https://text.pollinations.ai/openai", None, ["openai"]))
if LLM_KEY:
    LLM_CHAIN.append((LLM_BASE, LLM_KEY, ["Qwen3.8-Flash-Next"]))

MF_BASE = os.environ.get("MF_BASE", "https://8085-agq2az3swngxyr5doddjazpmde.instabox.tech")
MF_USER = os.environ.get("MF_USER", "radar")
MF_PASS = os.environ.get("MF_PASS", "")
MF_AUTH = base64.b64encode(("%s:%s" % (MF_USER, MF_PASS)).encode()).decode()

CATEGORIES = ["AI 资讯", "科技与开源"]
FRESH_HOURS = 24          # 资讯保鲜时长，超过即入历史
REPO_FRESH_HOURS = 72     # 项目从"首次发现"起保留时长
MAX_ITEMS = 100
MAX_PER_SECTION = 24
MAX_LLM = 15
_title_budget = [10]      # 每轮用 LLM 翻标题的额度
ARCHIVE_MAX = 4000
HOURS_BACK = 48

AI_RE = re.compile(
    r"(openai|anthropic|claude|gpt|llm|gemini|deepseek|qwen|llama|mistral|diffusion|"
    r"transformer|agent|rag|embedding|fine.?tun|inference|nvidia|amd|cuda|hugging|arxiv|"
    r"machine.?learning|neural|robot|chip|token|大模型|人工智能|智能体|算力)", re.I)

SECTIONS = [
    ("模型与产品", re.compile(r"(release|launch|introduc|announc|unveil|新模型|发布|上线|"
                          r"gpt-?\d|claude|gemini|llama|qwen|deepseek|kimi|grok)", re.I)),
    ("研究与论文", re.compile(r"(arxiv|paper|research|study|benchmark|sota|dataset|"
                         r"technical report|论文|研究|基准)", re.I)),
    ("开源与仓库", re.compile(r"(github|repo|open.?source|sdk|library|framework|"
                         r"开源|仓库|工具库)", re.I)),
    ("产业与资本", re.compile(r"(funding|raise|series [a-e]|valuation|acquis|ipo|layoff|"
                         r"revenue|pricing|融资|估值|收购|上市|裁员)", re.I)),
    ("算力与芯片", re.compile(r"(gpu|tpu|nvidia|amd|asic|cuda|datacenter|cluster|"
                         r"inference cost|芯片|算力|数据中心)", re.I)),
    ("治理与安全", re.compile(r"(regulat|policy|lawsuit|copyright|privacy|safety|"
                         r"security|jailbreak|监管|法案|安全|隐私)", re.I)),
]
FALLBACK_SECTION = "其他动态"


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http(url, payload=None, headers=None, timeout=25):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", UA)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(errors="replace")


# ---------- 翻译 ----------
def t_c5(text):
    """clients5：自动识别语种，单发快，实测最稳。返回结构随参数变化，需归一化。"""
    d = json.loads(http(C5 + urllib.parse.quote(text[:900]), timeout=12,
                        headers={"Accept": "application/json"}))
    head = d[0] if d and isinstance(d[0], list) else d
    if head and isinstance(head[0], list):
        parts = [seg[0] or "" for seg in head if isinstance(seg, list)]
    else:
        parts = [head[0] if head and isinstance(head[0], str) else ""]
    out = "".join(parts).strip()
    if not out:
        raise RuntimeError("empty c5")
    return out


def t_google(text):
    d = json.loads(http(GOOGLE + urllib.parse.quote(text[:900]), timeout=12,
                        headers={"Accept": "application/json"}))
    return "".join(s[0] for s in d[0] if s and s[0]).strip()


def t_libre(text):
    d = json.loads(http(LT, {"q": text[:900], "source": "en", "target": "zh"}, timeout=25))
    return (d.get("translatedText") or "").strip()


def t_mymemory(text):
    d = json.loads(http(MYMEM + urllib.parse.quote(text[:480]), timeout=20))
    return (d.get("responseData", {}).get("translatedText") or "").strip()


def translate(text):
    if not text or not text.strip():
        return ""
    for fn in (t_c5, t_google, t_libre, t_mymemory):
        try:
            out = fn(text)
            if out and not out.lower().startswith("my memory"):
                return polish(out)
        except Exception:  # noqa: BLE001
            time.sleep(0.7)
    return text


POLISH = [
    ("法学硕士", "大模型"),      # LLM 常被机翻误译
    ("法学硕士学位", "大模型"),
    ("大宗语言模型", "大模型"),
    ("代理工作负载", "智能体任务"),
    ("人工智能特工", "智能体"),
    ("特工", "智能体"),
    ("大型语言模型", "大模型"),
    ("法学硕士研究", "大模型研究"),
    ("代理", "智能体"),
    ("变压器", "Transformer"),
    ("拥抱脸", "Hugging Face"),
    ("开源权重的", "开放权重的"),
    ("推理成本", "推理成本"),
    ("显卡", "GPU"),
]


def polish(s):
    for a, b in POLISH:
        s = s.replace(a, b)
    s = s.replace("智能体智能", "智能体").replace("智能体化", "智能化")
    return s.replace("&#39;", "'").replace("&quot;", '"')


def t_llm(text):
    """用 LLM 翻译标题（质量高于机翻）。标题是可选项，快速失败：单次尝试、12 秒超时。"""
    prompt = ("把下面这条 AI/科技标题翻成简洁的中文，只输出译文，不要解释，"
              "保留产品名与公司名原文：\n" + text[:300])
    try:
        return llm_detail_text(prompt, 90, minlen=1, attempts=1, timeout=12)
    except Exception:  # noqa: BLE001
        return ""


# ---------- 采集 ----------
def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"seen": []}


def save_state(st):
    st["seen"] = st["seen"][-2000:]
    json.dump(st, open(STATE, "w", encoding="utf-8"))


def load_archive():
    rows = []
    if not os.path.exists(ARCHIVE):
        return rows
    with open(ARCHIVE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows


def save_archive(rows):
    rows = rows[-ARCHIVE_MAX:]
    tmp = ARCHIVE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, ARCHIVE)


def from_miniflux(seen):
    out = []
    try:
        auth = {"Authorization": "Basic " + MF_AUTH}
        cats = json.loads(http(MF_BASE + "/v1/categories", headers=auth))
        ids = [c["id"] for c in cats if c["title"] in CATEGORIES]
        since = (datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for cid in ids:
            url = "%s/v1/entries?category_id=%d&limit=120&published_after=%s" % (MF_BASE, cid, since)
            try:
                entries = json.loads(http(url, headers=auth))
            except Exception as ex:  # noqa: BLE001
                log("miniflux %d: %s" % (cid, ex))
                continue
            for e in entries.get("entries", []):
                u, title = e.get("url") or "", (e.get("title") or "").strip()
                if not u or not title or u in seen:
                    continue
                if not AI_RE.search(title):
                    continue
                body = re.sub(r"<[^>]+>", " ", e.get("content") or e.get("summary") or "")
                body = " ".join(body.split())
                if len(body) < 80:
                    body = " ".join([body, title])
                out.append({"title": title, "url": u, "summary": body[:1600],
                            "source": (e.get("feed") or {}).get("title", "RSS"),
                            "kind": "news", "time": e.get("published_at", "")})
    except Exception as ex:  # noqa: BLE001
        log("miniflux: %s" % ex)
    return out


def from_github(seen):
    out = []
    day = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    q = urllib.parse.quote("created:>%s stars:>15" % day)
    url = "https://api.github.com/search/repositories?q=%s&sort=stars&order=desc&per_page=40" % q
    try:
        data = json.loads(http(url, headers={"Accept": "application/vnd.github+json"}))
        for r in data.get("items", []):
            u = r.get("html_url", "")
            if not u or u in seen:
                continue
            desc = (r.get("description") or "").strip()
            topics = " ".join(r.get("topics") or [])
            out.append({"title": r.get("full_name", ""), "url": u,
                        "summary": " ".join([desc, topics])[:600] or "（仓库无描述）",
                        "meta": "\u2605%d %s" % (r.get("stargazers_count", 0),
                                                 r.get("language") or ""),
                        "source": "GitHub", "kind": "repo", "time": r.get("created_at", "")})
    except Exception as ex:  # noqa: BLE001
        log("github: %s" % ex)
    return out


def hn_comments(it, limit=3):
    texts = []
    for kid in (it.get("kids") or [])[:limit]:
        try:
            c = json.loads(http("https://hacker-news.firebaseio.com/v0/item/%d.json" % kid))
            if not c or c.get("deleted") or c.get("dead"):
                continue
            t = " ".join(re.sub(r"<[^>]+>", " ", c.get("text") or "").split())
            if len(t) > 60:
                texts.append(t[:500])
        except Exception:  # noqa: BLE001
            continue
        if len(texts) >= 2:
            break
    return texts


def from_hn(seen):
    out = []
    try:
        ids = json.loads(http("https://hacker-news.firebaseio.com/v0/topstories.json"))[:120]
        for i in ids:
            it = json.loads(http("https://hacker-news.firebaseio.com/v0/item/%d.json" % i))
            if not it or it.get("type") != "story":
                continue
            title = it.get("title", "")
            u = it.get("url") or "https://news.ycombinator.com/item?id=%d" % i
            if u in seen or not AI_RE.search(title):
                continue
            host = urllib.parse.urlparse(u).netloc.replace("www.", "")
            out.append({"title": title, "url": u,
                        "summary": " ".join(hn_comments(it)) or (it.get("text") or ""),
                        "meta": "\u25b2%d \u00b7 %d 评论 \u00b7 %s"
                                % (it.get("score", 0), it.get("descendants", 0) or 0, host),
                        "source": "HackerNews", "kind": "news",
                        "time": datetime.fromtimestamp(it.get("time", 0), timezone.utc).isoformat()})
            if len(out) >= 26:
                break
    except Exception as ex:  # noqa: BLE001
        log("hn: %s" % ex)
    return out


# ---------- 摘要 ----------
def llm_detail(item):
    prompt = ("用 3-5 句简体中文介绍这条科技资讯：先说它是什么，再说关键细节(数据/方法/产品名)，"
              "最后说影响或看点。不要复述标题，不要客套，像技术媒体编辑。\n标题：%s\n正文：%s"
              % (item["title"], item["summary"][:1200]))
    return llm_detail_text(prompt, 700, minlen=40)


def llm_detail_text(prompt, maxlen=700, minlen=1, attempts=2, timeout=25):
    last = None
    for base, key, models in LLM_CHAIN:
        for model in models:
            for attempt in range(attempts):
                try:
                    headers = {"Authorization": "Bearer " + key} if key else {}
                    raw = http(base, {"model": model,
                                      "messages": [{"role": "user", "content": prompt}],
                                      "temperature": 0.3, "max_tokens": 420},
                               headers=headers, timeout=timeout)
                    msg_obj = json.loads(raw)["choices"][0].get("message") or {}
                    txt = msg_obj.get("content") or msg_obj.get("reasoning") or ""
                    txt = re.sub(r"\s+", " ", txt).strip()[:maxlen]
                    if len(txt) > minlen:
                        return txt
                    last = RuntimeError("empty content")
                    time.sleep(4)
                    continue
                except Exception as ex:  # noqa: BLE001
                    last = ex
                    msg = str(ex)
                    retryable = ("429" in msg or "402" in msg or "timed out" in msg
                                 or "500" in msg or "503" in msg)
                    if not retryable:
                        break
                    time.sleep(4 + attempt * 4 if attempts > 1 else 0)
        log("llm fallback %s" % last)
    raise RuntimeError("all llm providers failed: %s" % last)


def detail(item, use_llm):
    src = item.get("summary") or ""
    if use_llm:
        try:
            got = llm_detail(item)
            if len(got) > 40:
                return got
        except Exception as ex:  # noqa: BLE001
            log("llm: %s" % ex)
    if item["kind"] == "repo":
        return translate(src)[:600] if src else "仓库暂无描述，点开看 README"
    sents = re.split(r"(?<=[.!?\u3002])\s+", src)
    limit = 2 if item.get("meta") and "评论" in item.get("meta", "") else 4
    chunk = " ".join(sents[:limit])[:700] or src[:400]
    if not chunk:
        return "讨论热度：" + item.get("meta", "") if item.get("meta") else "点开看原文与评论"
    out = translate(chunk)
    if "评论" in (item.get("meta") or "") and len(out) > 240:
        out = out[:240] + "\u2026"
    return out[:650]


def section_of(item):
    blob = item["title"] + " " + item.get("summary", "")[:400]
    for name, rx in SECTIONS:
        if rx.search(blob):
            return name
    return FALLBACK_SECTION


# ---------- 本地快照 ----------
def fetch_readme(full_name):
    """GitHub 仓库 README 正文。"""
    try:
        raw = http("https://api.github.com/repos/%s/readme" % full_name,
                   headers={"Accept": "application/vnd.github.raw"},
                   timeout=20)
        return raw
    except Exception:  # noqa: BLE001
        return ""


def extract_article(raw_html):
    """从 HTML 粗略抽正文：去脚本样式，取最长文本块。"""
    h = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", raw_html)
    h = re.sub(r"(?is)<!--.*?-->", " ", h)
    blocks = re.findall(r"(?is)<(article|main)[^>]*>(.*?)</\1>", h)
    body = blocks[0][1] if blocks else h
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", body)
    if len(" ".join(paras)) < 300:
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", h)
    text = "\n\n".join(
        " ".join(re.sub(r"<[^>]+>", " ", p).split()) for p in paras
        if len(" ".join(re.sub(r"<[^>]+>", " ", p).split())) > 40)
    return text[:9000]


SNAP_TMPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s · 快照</title><style>
*{box-sizing:border-box}
body{margin:0;background:#fbfaf7;color:#1b1d22;
 font:17px/1.8 "PingFang SC","Noto Serif SC",Georgia,"Microsoft YaHei",serif}
.wrap{max-width:760px;margin:0 auto;padding:0 22px 80px}
.top{position:sticky;top:0;background:#f4f1e9;border-bottom:1px solid #e0d9c9;
 padding:11px 22px;display:flex;gap:12px;align-items:center;font:13px/1.4 inherit;flex-wrap:wrap}
.top b{color:#8a4a1f}
.go{margin-left:auto;background:#1b1d22;color:#fff;text-decoration:none;
 padding:6px 14px;border-radius:6px;font-size:13px}
.go:hover{background:#33383f}
.kicker{font:11px/1 ui-monospace,Menlo,monospace;letter-spacing:1.6px;color:#8a8172;text-transform:uppercase}
h1{font-size:27px;line-height:1.35;margin:26px 0 6px;letter-spacing:-.3px}
.meta{font:11.5px/1.6 ui-monospace,Menlo,monospace;color:#9a9282;margin-bottom:18px}
.sum{background:#fff;border:1px solid #e6e0d4;border-left:3px solid #c0392b;border-radius:0 8px 8px 0;
 padding:14px 16px;margin:18px 0 24px;font-size:15.5px;color:#33302a}
.sum b{color:#a5342a}
.src{border-top:1px dashed #ded7c9;margin-top:26px;padding-top:18px;color:#413c33;font-size:15.5px}
.src p{margin:0 0 14px}
pre{white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #e6e0d4;
 border-radius:8px;padding:14px;font:13.5px/1.65 ui-monospace,Menlo,monospace;color:#3a3f46}
.none{color:#8a8172;font-size:14px}
a{color:#1b4f8a}
</style></head><body>
<div class="top"><span>这是 <b>本地快照</b>，原文在外网</span>
<a class="go" href="%(url)s" target="_blank" rel="noreferrer">去外网原文 &rarr;</a></div>
<div class="wrap">
<span class="kicker">%(kind)s · %(source)s</span>
<h1>%(title)s</h1>
<div class="meta">%(host)s · %(time)s</div>
<div class="sum">%(detail)s</div>
<div class="src">%(body)s</div>
</div></body></html>"""


def make_snapshot(it):
    """抓正文落盘成快照页，返回文件名。失败也生成占位页。"""
    try:
        os.makedirs(SNAP_DIR, exist_ok=True)
        key = re.sub(r"[^0-9a-zA-Z]+", "-", it["url"])[-120:]
        name = (hashlib.sha1(it["url"].encode()).hexdigest()[:16] + "-" + key + ".html")[:150]
        path = os.path.join(SNAP_DIR, name)
        if os.path.exists(path):
            return name
        body = ""
        if it["kind"] == "repo":
            body = fetch_readme(it["title"])
            inner = '<pre>%s</pre>' % html.escape(body) if body else \
                '<p class="none">README 抓取失败，请点上方按钮看原文。</p>'
        else:
            try:
                raw = http(it["url"], timeout=18,
                           headers={"Accept": "text/html,application/xhtml+xml"})
                txt = extract_article(raw)
            except Exception:  # noqa: BLE001
                txt = ""
            inner = ("".join("<p>%s</p>" % html.escape(p) for p in txt.split("\n\n"))
                     if txt else '<p class="none">正文抓取失败（可能反爬或需登录），请点上方按钮看原文。</p>')
        doc = SNAP_TMPL % {
            "title": html.escape(it.get("zh_title") or it["title"]),
            "url": html.escape(it["url"]),
            "source": html.escape(it["source"]),
            "kind": "\u9879\u76ee" if it["kind"] == "repo" else "\u8d44\u8baf",
            "host": html.escape(urllib.parse.urlparse(it["url"]).netloc.replace("www.", "")),
            "time": html.escape(it.get("time", "")[:16].replace("T", " ")),
            "detail": html.escape(it.get("detail", "")),
            "body": inner,
        }
        open(path, "w", encoding="utf-8").write(doc)
        return name
    except Exception as ex:  # noqa: BLE001
        log("snap: %s" % ex)
        return ""


def prune_snapshots():
    try:
        files = sorted((os.path.join(SNAP_DIR, f) for f in os.listdir(SNAP_DIR)),
                       key=os.path.getmtime, reverse=True)
        for p in files[SNAP_KEEP:]:
            os.remove(p)
    except OSError:
        pass


def prep(it):
    try:
        it["section"] = it.get("section") or section_of(it)
        if it["kind"] == "repo":
            it["zh_title"] = it["title"]
        else:
            zh = ""
            if _title_budget[0] > 0:
                _title_budget[0] -= 1
                zh = t_llm(it["title"])
            it["zh_title"] = zh or translate(it["title"])
            if zh:
                it["tr_llm"] = True
        it["detail"] = detail(it, use_llm=False)
        it["snap"] = make_snapshot(it)
    except Exception as ex:  # noqa: BLE001
        log("prep: %s" % ex)
        it.setdefault("zh_title", it["title"])
        it.setdefault("detail", "")
    return it


# ---------- 时间 ----------
def parse_t(t):
    try:
        dt = datetime.fromisoformat((t or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc)


def fmt_time(t):
    dt = parse_t(t).astimezone(timezone(timedelta(hours=8)))
    delta = (datetime.now(timezone.utc) - parse_t(t)).total_seconds()
    if delta < 3600:
        rel = "%d 分钟前" % max(1, delta // 60)
    elif delta < 86400:
        rel = "%d 小时前" % (delta // 3600)
    else:
        rel = "%d 天前" % (delta // 86400)
    return '%s<span class="rel">%s</span>' % (dt.strftime("%m-%d %H:%M"), rel)


STYLE = """
*{box-sizing:border-box}
body{margin:0;background:#fbfaf7;color:#1b1d22;
 font:16px/1.72 "PingFang SC","Noto Serif SC","Source Han Serif SC",Georgia,"Microsoft YaHei",serif;
 -webkit-font-smoothing:antialiased}
a{color:inherit}
header{border-bottom:3px double #1b1d22;padding:26px 28px 16px;background:#fbfaf7}
.brand{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{margin:0;font-size:30px;letter-spacing:-.4px}
.kicker{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:2px;color:#8a8172;text-transform:uppercase}
.desc{margin:8px 0 0;color:#5d574c;font-size:13.5px;max-width:70ch}
.bar{margin-top:14px;display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#4a453c;align-items:center}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid #ded7c9;background:#fff;border-radius:999px;padding:3px 12px;font-size:12px}
.pill b{font-variant-numeric:tabular-nums}
.dot{width:8px;height:8px;border-radius:50%}
.dot.n{background:#c0392b}.dot.p{background:#1f7a4d}
.bar a.hist{margin-left:auto;color:#8a8377;text-decoration:none;border-bottom:1px solid #d6cfc0}
.bar a.hist:hover{color:#1b1d22}
main{padding:0 28px 80px;max-width:1280px;margin:0 auto}
h2{font-size:13px;letter-spacing:2.4px;text-transform:uppercase;color:#8a8172;margin:34px 0 6px;padding-bottom:8px;
 border-bottom:1px solid #e6e0d4;display:flex;align-items:baseline;gap:10px}
h2 em{font-style:normal;font-size:12px;color:#b3aa99;letter-spacing:0}
.lead{display:grid;grid-template-columns:1.35fr 1fr;gap:26px;margin-top:18px}
@media(max-width:900px){.lead{grid-template-columns:1fr}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px 24px;margin-top:16px}
article{border-top:2px solid #1b1d22;padding-top:10px}
article.repo{border-top-color:#1f7a4d}
.tag{display:inline-block;font:10.5px/1 ui-monospace,Menlo,monospace;letter-spacing:1.2px;text-transform:uppercase;
 padding:3px 7px;border-radius:3px;margin-bottom:7px}
.tag.n{background:#fdeceb;color:#a5342a;border:1px solid #f4cfcb}
.tag.p{background:#e9f5ee;color:#15603c;border:1px solid #c8e3d5}
.tag.repo{background:#e9f5ee;color:#15603c;border:1px solid #c8e3d5}
h3{margin:0 0 6px;font-size:19px;line-height:1.42;letter-spacing:-.2px;font-weight:600}
.lead h3{font-size:26px;line-height:1.3}
h3 a{text-decoration:none;background-image:linear-gradient(#1b1d22,#1b1d22);background-size:0 1px;
 background-repeat:no-repeat;background-position:0 100%;transition:background-size .25s}
h3 a:hover{background-size:100% 1px}
p{margin:0 0 8px;color:#413c33;font-size:14.5px}
.lead p{font-size:15.5px;color:#2f2b23}
.meta{font:11.5px/1.6 ui-monospace,Menlo,monospace;color:#9a9282;display:flex;gap:10px;flex-wrap:wrap}
.meta .src{color:#6f6858}
.rel{color:#b8b0a0}
.rel:before{content:"· "}
.sec{font:11px/1 ui-monospace,Menlo,monospace;color:#b3aa99}
.org{margin-left:auto;color:#8a4a1f;text-decoration:none;border-bottom:1px solid #e0d0bd}
.note{margin-top:16px;padding:12px 14px;background:#f4f1e9;border-left:3px solid #d8d1c1;font-size:13px;color:#5d574c}
footer{border-top:3px double #1b1d22;margin-top:40px;padding:16px 28px;font-size:12px;color:#8a8377}
.searchbox{padding:9px 12px;border:1px solid #ded7c9;border-radius:6px;font:14px/1.4 inherit;background:#fff;width:min(340px,100%)}
.hrow{border-top:1px solid #e6e0d4;padding:11px 0;display:grid;grid-template-columns:74px 1fr;gap:14px;align-items:baseline}
.hrow .when{font:11px/1.5 ui-monospace,Menlo,monospace;color:#a49b8a}
.hrow h4{margin:0 0 3px;font-size:15.5px;font-weight:600;line-height:1.45}
.hrow h4 a{text-decoration:none}
.hrow h4 a:hover{text-decoration:underline}
.hrow .sub{font-size:13px;color:#6f6858}
.day{margin:30px 0 0;font:12px/1 ui-monospace,Menlo,monospace;letter-spacing:1.5px;color:#8a8172}
"""


def card(it, lead=False):
    kind_cls = "repo" if it["kind"] == "repo" else ""
    tag = ('<span class="tag p">项目</span>' if it["kind"] == "repo"
           else '<span class="tag n">资讯</span>')
    host = urllib.parse.urlparse(it["url"]).netloc.replace("www.", "")
    snap = it.get("snap")
    href = ("/snap/" + snap) if snap else it["url"]
    tgt = "" if snap else ' target="_blank" rel="noreferrer"'
    origin = ('<a class="org" href="%s" target="_blank" rel="noreferrer">外网原文</a>'
              % html.escape(it["url"])) if snap else ""
    return ('<article class="%s">%s<h3><a href="%s"%s>%s</a></h3>'
            '<p>%s</p><div class="meta"><span class="src">%s</span><span>%s</span>'
            '<span class="sec">%s</span>%s</div></article>'
            % (kind_cls, tag, html.escape(href), tgt,
               html.escape(it.get("zh_title") or it["title"]),
               html.escape(it.get("detail", "")), html.escape(it["source"]),
               fmt_time(it.get("time", "")), html.escape(host), origin))


def render(rows):
    now_local = datetime.now(timezone(timedelta(hours=8)))
    now_utc = datetime.now(timezone.utc)
    news_cut = now_utc - timedelta(hours=FRESH_HOURS)
    repo_cut = now_utc - timedelta(hours=REPO_FRESH_HOURS)

    def born(r):
        return parse_t(r.get("first_seen") or r.get("time"))

    fresh_news = [r for r in rows if r["kind"] != "repo" and parse_t(r.get("time")) >= news_cut]
    fresh_repos = [r for r in rows if r["kind"] == "repo" and born(r) >= repo_cut]
    fresh_news.sort(key=lambda r: parse_t(r.get("time")), reverse=True)
    fresh_repos.sort(key=lambda r: parse_t(r.get("first_seen") or r.get("time")), reverse=True)

    news = fresh_news[:MAX_ITEMS]
    repos = fresh_repos[:MAX_PER_SECTION * 2]
    lead = news[:2]
    rest = news[2:]

    def by_section(items):
        groups = {}
        for it in items:
            groups.setdefault(it.get("section") or FALLBACK_SECTION, []).append(it)
        order = [n for n, _ in SECTIONS] + [FALLBACK_SECTION]
        out = []
        for name in order:
            chunk = groups.get(name, [])[:MAX_PER_SECTION]
            if chunk:
                out.append('<h2>%s <em>%d 条</em></h2><div class="grid">%s</div>'
                           % (html.escape(name), len(chunk), "".join(card(i) for i in chunk)))
        return "".join(out)

    body = ""
    if lead:
        body += '<h2>今日要闻 <em>编辑精选</em></h2><div class="lead">%s</div>' % "".join(
            card(i) for i in lead)
    body += by_section(rest)
    if repos:
        body += '<h2>新开源项目 <em>%d 个 · 近 14 天</em></h2><div class="grid">%s</div>' % (
            len(repos), "".join(card(i) for i in repos))

    doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="300">
<title>AI 视界 · 国外 AI 资讯中文化</title><style>%s</style></head><body>
<header><div class="brand"><h1>AI 视界</h1><span class="kicker">foreign ai · 中文摘要</span></div>
<p class="desc">国外一手 AI 资讯与开源项目，机器翻译 + 模型摘要。超过 24 小时的条目自动移入历史。</p>
<div class="bar">
 <span class="pill"><span class="dot n"></span>资讯 <b>%d</b></span>
 <span class="pill"><span class="dot p"></span>项目 <b>%d</b></span>
 <span class="pill">更新 %s</span>
 <a class="hist" href="/history.html">查看历史 &rarr;</a>
</div></header>
<main>%s
<div class="note">点标题先看<b>本地快照</b>（页顶有“去外网原文”按钮），外网打不开也能读。红标=资讯，绿标=项目（标题保留原名）。两类都会在 24 小时后归档。</div>
</main>
<footer>自动生成 · 每 20 分钟刷新 · 机械翻译存在偏差，以原文为准 · 推送话题 /airadar</footer></body></html>""" % (
        STYLE, len(news), len(repos), now_local.strftime("%m-%d %H:%M"), body)
    tmp = OUT + ".tmp"
    open(tmp, "w", encoding="utf-8").write(doc)
    os.replace(tmp, OUT)


def render_history(rows):
    rows = sorted(rows, key=lambda r: parse_t(r.get("time")), reverse=True)[:1500]
    days, cur = [], None
    for r in rows:
        d = parse_t(r.get("time")).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        if d != cur:
            cur = d
            days.append('<div class="day">%s</div>' % d)
        host = urllib.parse.urlparse(r["url"]).netloc.replace("www.", "")
        href = ("/snap/" + r["snap"]) if r.get("snap") else r["url"]
        days.append('<div class="hrow"><span class="when">%s</span><div>'
                    '<h4><a href="%s">%s</a></h4>'
                    '<div class="sub">%s · %s · %s · <a href="%s" target="_blank" rel="noreferrer">原文</a></div></div></div>'
                    % (parse_t(r.get("time")).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M"),
                       html.escape(href), html.escape(r.get("zh_title") or r["title"]),
                       html.escape("项目" if r["kind"] == "repo" else "资讯"),
                       html.escape(r["source"]), html.escape(host), html.escape(r["url"])))
    doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>历史 · AI 视界</title>
<style>%s</style></head><body>
<header><div class="brand"><h1>历史</h1><span class="kicker">archive</span></div>
<p class="desc">全部已归档条目，按时间倒序，最多保留 4000 条。</p>
<div class="bar"><input class="searchbox" id="q" placeholder="搜标题 / 来源…">
<a class="hist" href="/">返回首页 &rarr;</a></div></header>
<main id="list">%s</main>
<footer>共 %d 条</footer>
<script>
const q=document.getElementById('q'),list=document.getElementById('list');
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();
 for(const el of list.querySelectorAll('.hrow')){el.style.display=!v||el.textContent.toLowerCase().includes(v)?'':'none';}});
</script></body></html>""" % (STYLE, "".join(days) or "<p>空</p>", len(rows))
    tmp = HISTORY + ".tmp"
    open(tmp, "w", encoding="utf-8").write(doc)
    os.replace(tmp, HISTORY)


def push(items):
    top = [i for i in items if i.get("zh_title")][:6]
    if not top:
        return
    body = "\n".join("\u00b7 " + i["zh_title"][:80] for i in top).encode()
    try:
        req = urllib.request.Request(NTFY, data=body, method="POST",
                                     headers={"Title": "AI radar +%d" % len(top), "Click": BASE})
        urllib.request.urlopen(req, timeout=25).read()
        log("pushed %d" % len(top))
    except Exception as ex:  # noqa: BLE001
        log("push: %s" % ex)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    st = load_state()
    seen = set(st["seen"])
    archive = load_archive()
    known = {r["url"] for r in archive}
    items = [i for i in (from_miniflux(seen) + from_github(seen) + from_hn(seen)) if i["url"] not in known]
    log("collected %d new" % len(items))

    uniq, used = [], set()
    for it in items:
        key = it["title"].strip().lower()
        if key in used:
            continue
        used.add(key)
        uniq.append(it)
    uniq = uniq[:MAX_ITEMS]
    stamp = datetime.now(timezone.utc).isoformat()
    for it in uniq:
        it["section"] = section_of(it)
        it["first_seen"] = stamp
    uniq.sort(key=lambda x: parse_t(x.get("time")), reverse=True)
    # 先落盘，保证页面与归档不因中途异常丢失
    archive.extend(uniq)
    save_archive(archive)
    st["seen"].extend(i["url"] for i in uniq)
    save_state(st)
    render(archive)
    for n in range(0, len(uniq), 12):
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(prep, uniq[n:n + 12]))
        save_archive(archive)
        render(archive)

    def snap_pass(it):
        if not it.get("snap"):
            it["snap"] = make_snapshot(it)
        return it

    missing = [r for r in archive if not r.get("snap")][:60]
    if missing:
        with ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(snap_pass, missing))
        save_archive(archive)
        render(archive)

    def title_pass(it):
        try:
            zh = t_llm(it["title"])
            if len(zh) > 6:
                it["zh_title"] = zh
                it["tr_llm"] = True
            else:
                it["tr_try"] = it.get("tr_try", 0) + 1
        except Exception as ex:  # noqa: BLE001
            it["tr_try"] = it.get("tr_try", 0) + 1
            log("title skip: %s" % ex)
        return it

    pending = [r for r in archive
               if r["kind"] != "repo" and not r.get("tr_llm")
               and r.get("tr_try", 0) < 3][:8]
    if pending:
        with ThreadPoolExecutor(max_workers=1) as ex:
            list(ex.map(title_pass, pending))
        save_archive(archive)
        render(archive)

    def llm_pass(it):
        try:
            d = detail(it, use_llm=True)
            if d:
                it["detail"] = d
                it["llm"] = True
        except Exception as ex:  # noqa: BLE001
            it["llm_try"] = it.get("llm_try", 0) + 1
            log("llm skip: %s" % ex)
        return it

    targets = [r for r in archive
               if not r.get("llm") and r.get("llm_try", 0) < 4][:MAX_LLM]
    if targets:
        with ThreadPoolExecutor(max_workers=1) as ex:
            list(ex.map(llm_pass, targets))
        save_archive(archive)
        render(archive)
    render_history(archive)
    prune_snapshots()
    if uniq:
        push(uniq)


if __name__ == "__main__":
    main()
