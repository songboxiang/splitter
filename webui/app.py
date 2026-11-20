#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ISO Splitter - 光盘 ISO 识别/分割/刮削 WebUI 后端
功能: 多挂载点浏览 / ISO 扫描 / MakeMKV 识别 title 与轨道 / TMDB+豆瓣刮削 /
      分割预览 / 原地或指定路径输出 / 任务进度
"""
import os, re, sys, json, time, subprocess, threading, shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs
from datetime import datetime

import requests
from flask import Flask, jsonify, render_template, request, Response

CONFIG_DIR = Path(os.environ.get("JIS_CONFIG_DIR", "/config"))
LOGS_DIR = Path(os.environ.get("JIS_LOGS", str(CONFIG_DIR / "logs")))
PORT = int(os.environ.get("JIS_PORT", "8088"))
SCAN_FILE = CONFIG_DIR / "scan.json"          # 扫描缓存
SETTINGS_FILE = CONFIG_DIR / "settings.json"  # 用户设置
HISTORY_FILE = CONFIG_DIR / "history.json"    # 分割历史
SCRATCH = CONFIG_DIR / "scratch"

for d in (CONFIG_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates")

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".flv", ".iso"}

# ══════════════════════════════════════════════════════════════
# 通用工具
# ══════════════════════════════════════════════════════════════
def log(msg):
    try:
        with open(LOGS_DIR / "app.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass

def load_json(path, default):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        log(f"load_json {path} err {e}")
    return default

def save_json(path, data):
    try:
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log(f"save_json {path} err {e}")
        return False

def load_settings():
    s = load_json(SETTINGS_FILE, {})
    base = {
        "tmdb_api_key": "",
        "tmdb_api_base": "https://api.themoviedb.org/3",   # 可改为反代地址
        "tmdb_image_base": "https://image.tmdb.org/t/p/w500",
        "proxy": "",             # http://host:port, 用于 requests
        "douban_enabled": True,
        "language": "zh-CN",
        "min_title_seconds": 300,  # 小于此时长的 title 默认不选中(预告/花絮)
        "name_template": "{title} - S{season:02d}E{episode:02d} - {epname}",
        "movie_template": "{title} ({year})",
        "output_mode": "inplace",   # inplace | custom
        "output_path": "",
        "auto_scrape": True,
        # MakeMKV beta key 自动管理
        "key_auto_update": False,      # 是否启用周期自动获取
        "key_update_hours": 72,        # 自动获取间隔(小时)
    }
    base.update(s)
    return base

def save_settings(s):
    return save_json(SETTINGS_FILE, s)

def requests_session():
    sess = requests.Session()
    s = load_settings()
    proxy = s.get("proxy", "").strip()
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    return sess

# ══════════════════════════════════════════════════════════════
# MakeMKV beta key: 抓取 / 解析 / 写入 / 周期更新
# 参考 jlesage/docker-makemkv 的成熟实现, 适配当前论坛页面(<code> 格式)
# ══════════════════════════════════════════════════════════════
KEY_SOURCE_URL = "https://forum.makemkv.com/forum/viewtopic.php?f=5&t=1053"
KEY_STATE_FILE = CONFIG_DIR / "key_state.json"
KEY_FILE = CONFIG_DIR / "keydata.txt"            # 用户自定义 key(优先级高于自动获取)
MAKEMKV_CONF = Path(os.environ.get("JIS_MAKEMKV_CONF", "/root/.MakeMKV/settings.conf"))

def _read_current_key():
    """读取 MakeMKV 配置文件中当前生效的 app_Key"""
    try:
        if MAKEMKV_CONF.exists():
            m = re.search(r'^\s*app_Key\s*=\s*"([^"]+)"', MAKEMKV_CONF.read_text(encoding="utf-8", errors="ignore"), re.M)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""

def _write_makemkv_key(key):
    """把 key 写入 MakeMKV 配置(幂等), 同时写 /config/keydata.txt 供重启后 entrypoint 载入"""
    key = (key or "").strip()
    if not key:
        return False, "key 为空"
    try:
        MAKEMKV_CONF.parent.mkdir(parents=True, exist_ok=True)
        text = MAKEMKV_CONF.read_text(encoding="utf-8", errors="ignore") if MAKEMKV_CONF.exists() else ""
        if re.search(r'^\s*app_Key\s*=', text, re.M):
            text = re.sub(r'^\s*app_Key\s*=.*$', f'app_Key = "{key}"', text, flags=re.M)
        else:
            text = text.rstrip() + f'\napp_Key = "{key}"\n'
        MAKEMKV_CONF.write_text(text, encoding="utf-8")
        KEY_FILE.write_text(f'app_Key = "{key}"\n', encoding="utf-8")
        return True, "ok"
    except Exception as e:
        log(f"write key err {e}")
        return False, str(e)

def fetch_makemkv_key():
    """从官方论坛抓取最新 beta key。返回 (key, expire_text, error)"""
    try:
        sess = requests.Session()
        sess.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
        resp = sess.get(KEY_SOURCE_URL, timeout=25)
        if resp.status_code != 200:
            return "", "", f"论坛返回 HTTP {resp.status_code}"
        html = resp.text
        # 当前格式: <code>T-XXXX</code>; 兼容旧的反引号格式
        m = re.search(r'<code>([^<]+)</code>', html)
        if not m:
            m = re.search(r'`([^`]+)`', html)
        key = m.group(1).strip() if m else ""
        # 校验: beta key 通常以 T- 开头且长度足够
        if not key or len(key) < 30 or not key.startswith("T-"):
            return "", "", "未解析到有效 beta key(页面格式可能已变更)"
        exp = re.search(r'valid until ([^.<]+)', html, re.I)
        expire = exp.group(1).strip() if exp else ""
        return key, expire, ""
    except Exception as e:
        return "", "", str(e)

def apply_fetched_key(manual=False):
    """抓取并应用最新 key, 更新状态文件。返回 dict 结果"""
    st = load_json(KEY_STATE_FILE, {})
    key, expire, err = fetch_makemkv_key()
    if err:
        log(f"key fetch failed: {err}")
        st.update({"last_check": datetime.now().isoformat(), "last_error": err, "ok": False})
        save_json(KEY_STATE_FILE, st)
        return {"ok": False, "error": err, "state": st}
    changed = key != st.get("key") or key != _read_current_key()
    if changed or manual:
        ok, werr = _write_makemkv_key(key)
        if not ok:
            st.update({"last_check": datetime.now().isoformat(), "last_error": werr, "ok": False})
            save_json(KEY_STATE_FILE, st)
            return {"ok": False, "error": werr, "state": st}
    st.update({
        "ok": True,
        "key": key,
        "masked": key[:6] + "..." + key[-6:],
        "expire": expire,
        "source": "auto",
        "last_check": datetime.now().isoformat(),
        "last_applied": datetime.now().isoformat() if (changed or manual) else st.get("last_applied"),
        "last_error": "",
    })
    save_json(KEY_STATE_FILE, st)
    log(f"key applied: {st['masked']} (expire {expire})")
    return {"ok": True, "changed": bool(changed), "key": st["masked"], "expire": expire, "state": st}

def key_auto_worker():
    """后台守护: 按 settings 中的间隔周期自动检查更新 key"""
    while True:
        s = load_settings()
        hours = s.get("key_update_hours", 72)
        try:
            hours = max(1, int(hours))
        except Exception:
            hours = 72
        time.sleep(hours * 3600)
        try:
            s = load_settings()
            if s.get("key_auto_update"):
                apply_fetched_key()
        except Exception as e:
            log(f"key auto worker err {e}")

threading.Thread(target=key_auto_worker, daemon=True).start()

# ══════════════════════════════════════════════════════════════
# 挂载点发现与目录浏览
# ══════════════════════════════════════════════════════════════
SKIP_MP = {"/proc", "/sys", "/dev", "/run", "/boot", "/etc/hosts",
           "/etc/resolv.conf", "/etc/hostname", "/etc/localtime"}

def get_mounts():
    """读 /proc/mounts, 返回可浏览的挂载点(去重, 过滤伪文件系统)"""
    mounts, seen = [], set()
    fake_fs = {"proc", "sysfs", "devtmpfs", "tmpfs", "devpts", "cgroup", "cgroup2",
               "securityfs", "pstore", "bpf", "tracefs", "debugfs", "hugetlbfs",
               "mqueue", "fusectl", "configfs", "autofs", "overlay", "nsfs", "squashfs"}
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return [{"path": "/config", "label": "配置目录", "fstype": "local", "source": "builtin"},
                {"path": "/media", "label": "媒体目录", "fstype": "local", "source": "builtin"}]
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        src, mp, fstype = parts[0], parts[1], parts[2]
        if fstype in fake_fs or fstype.startswith("fuse.") and "gvfs" in src:
            continue
        if mp in SKIP_MP or any(mp.startswith(x + "/") for x in SKIP_MP):
            continue
        if mp in seen:
            continue
        p = Path(mp)
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        # 跳过过深的容器内部路径
        if mp.count("/") > 4 and not (mp.startswith("/mnt") or mp.startswith("/media") or mp.startswith("/data")):
            continue
        seen.add(mp)
        label = Path(mp).name or mp
        mounts.append({"path": mp, "label": label, "fstype": fstype, "source": src})
    # 确保内置目录在列表
    for extra, lab in [("/config", "配置目录"), ("/media", "媒体目录")]:
        if extra not in seen:
            try:
                if Path(extra).is_dir():
                    mounts.append({"path": extra, "label": lab, "fstype": "local", "source": "builtin"})
            except Exception:
                pass
    mounts.sort(key=lambda m: (m["path"] != "/config", m["path"]))
    return mounts

def is_path_allowed(path):
    try:
        p = Path(path).resolve()
    except Exception:
        return False
    for m in get_mounts():
        try:
            p.relative_to(Path(m["path"]).resolve())
            return True
        except ValueError:
            continue
    return False

def list_dir(path):
    p = Path(path if path else "/")
    if not p.exists() or not p.is_dir():
        return None
    items = []
    try:
        for c in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = c.stat()
            except Exception:
                continue
            isdir = c.is_dir()
            if not isdir and c.suffix.lower() not in VIDEO_EXT:
                continue
            items.append({
                "name": c.name, "path": str(c), "type": "dir" if isdir else "file",
                "size": st.st_size if not isdir else 0,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "is_iso": c.suffix.lower() == ".iso",
            })
    except PermissionError:
        return None
    return {"path": str(p), "parent": str(p.parent) if str(p) != "/" else None, "items": items}

# ══════════════════════════════════════════════════════════════
# ISO 扫描
# ══════════════════════════════════════════════════════════════
def scan_isos(dirs, max_depth=4):
    found, seen = [], set()
    for d in dirs:
        if not d or not Path(d).is_dir():
            continue
        base = Path(d)
        for root, subdirs, files in os.walk(d):
            depth = Path(root).relative_to(base).parts.__len__()
            if depth >= max_depth:
                subdirs[:] = []
            subdirs[:] = [x for x in subdirs if not x.startswith(".")]
            for fn in files:
                if Path(fn).suffix.lower() != ".iso":
                    continue
                fp = Path(root) / fn
                try:
                    st = fp.stat()
                except Exception:
                    continue
                key = str(fp.resolve())
                if key in seen:
                    continue
                seen.add(key)
                found.append({
                    "path": str(fp), "name": fn,
                    "size": st.st_size,
                    "size_gb": round(st.st_size / 1024**3, 2),
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "dir": str(fp.parent),
                    "status": "pending",   # pending / identified / scraped / split
                    "title_count": 0,
                })
    found.sort(key=lambda x: x["path"])
    return found

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/mounts")
def api_mounts():
    return jsonify({"ok": True, "mounts": get_mounts()})

@app.route("/api/browse")
def api_browse():
    path = request.args.get("path", "")
    r = list_dir(path)
    if r is None:
        return jsonify({"ok": False, "error": "目录不存在或无权限"})
    return jsonify({"ok": True, **r})

@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(silent=True) or {}
    dirs = data.get("dirs", [])
    if not dirs:
        return jsonify({"ok": False, "error": "请先选择要扫描的目录"})
    items = scan_isos(dirs)
    cache = load_json(SCAN_FILE, {})
    old = {i["path"]: i for i in cache.get("items", [])}
    for it in items:
        if it["path"] in old:
            it["status"] = old[it["path"]].get("status", "pending")
            it["title_count"] = old[it["path"]].get("title_count", 0)
            it["scrape"] = old[it["path"]].get("scrape")
    save_json(SCAN_FILE, {"items": items, "dirs": dirs, "scanned_at": datetime.now().isoformat()})
    return jsonify({"ok": True, "items": items, "count": len(items)})

@app.route("/api/scan_state")
def api_scan_state():
    return jsonify({"ok": True, **load_json(SCAN_FILE, {"items": [], "dirs": []})})

@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        s = load_settings()
        for k in list(s.keys()):
            if k in data:
                s[k] = data[k]
        save_settings(s)
        return jsonify({"ok": True, "settings": s})
    return jsonify({"ok": True, "settings": load_settings()})

@app.route("/api/key/status")
def api_key_status():
    st = load_json(KEY_STATE_FILE, {})
    current = _read_current_key()
    s = load_settings()
    return jsonify({
        "ok": True,
        "configured": bool(current),
        "key": (current[:6] + "..." + current[-6:]) if current else "",
        "expire": st.get("expire", ""),
        "source": st.get("source", ""),
        "last_check": st.get("last_check", ""),
        "last_applied": st.get("last_applied", ""),
        "last_error": st.get("last_error", ""),
        "auto_update": s.get("key_auto_update", False),
        "update_hours": s.get("key_update_hours", 72),
    })

@app.route("/api/key/fetch", methods=["POST"])
def api_key_fetch():
    return jsonify(apply_fetched_key(manual=True))

@app.route("/api/key/settings", methods=["POST"])
def api_key_settings():
    data = request.get_json(silent=True) or {}
    s = load_settings()
    if "auto_update" in data:
        s["key_auto_update"] = bool(data["auto_update"])
    if "update_hours" in data:
        try:
            s["key_update_hours"] = max(1, min(720, int(data["update_hours"])))
        except Exception:
            pass
    save_settings(s)
    return jsonify({"ok": True, "auto_update": s["key_auto_update"], "update_hours": s["key_update_hours"]})

# ══════════════════════════════════════════════════════════════
# 路由: 系统信息 / 日志
# ══════════════════════════════════════════════════════════════
@app.route("/api/system")
def api_system():
    def run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    mk = run("makemkvcon 2>&1 | head -1")
    return jsonify({
        "ok": True,
        "makemkv": mk[:120],
        "makemkv_ok": "MakeMKV" in mk or "version" in mk.lower() or bool(mk),
        "ffmpeg": run("ffmpeg -version 2>&1 | head -1")[:120],
        "disk_free_gb": round(shutil.disk_usage("/config").free / 1024**3, 1),
        "key_configured": (CONFIG_DIR / "keydata.txt").exists(),
    })

# ══════════════════════════════════════════════════════════════
# MakeMKV 识别: 解析 -r info 输出
# ══════════════════════════════════════════════════════════════
# AP_ItemAttributeId: Type=1 Name=2 LangCode=3 LangName=4 CodecId=5
# CodecShort=6 CodecLong=7 ChapterCount=8 Duration=9 DiskSize=10
# DiskSizeBytes=11 SourceFileName=16 AudioSampleRate=17 VideoSize=19
# VideoFrameRate=21 OutputFileName=27 VolumeName=32
STREAM_TYPE_CODE = {"6201": "video", "6202": "audio", "6203": "subtitle"}

def _parse_qvals(raw):
    """解析 makemkv 的 csv 字符串, 处理引号与逗号"""
    out, cur, inq = [], "", False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            if inq and i + 1 < len(raw) and raw[i+1] == '"':
                cur += '"'; i += 2; continue
            inq = not inq
        elif ch == ',' and not inq:
            out.append(cur); cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    return out

def parse_makemkv_info(text):
    """把 makemkvcon -r info 的文本解析成结构化 title 列表"""
    disc = {"volume": "", "type": "", "title_count": 0}
    titles = {}
    order = []

    for line in text.splitlines():
        line = line.strip()
        try:
            if line.startswith("TCOUT:"):
                disc["title_count"] = int(line.split(":", 1)[1].strip())
            elif line.startswith("CINFO:"):
                v = _parse_qvals(line.split(":", 1)[1].strip())
                if len(v) >= 3:
                    aid, code, val = v[0], v[1], v[2]
                    if aid == "1": disc["type"] = val
                    elif aid in ("2", "30", "32") and not disc["volume"]: disc["volume"] = val
            elif line.startswith("TINFO:"):
                v = _parse_qvals(line.split(":", 1)[1].strip())
                if len(v) < 4:
                    continue
                tidx, aid, code, val = v[0], v[1], v[2], v[3]
                if tidx not in titles:
                    titles[tidx] = {"index": int(tidx), "streams": [], "attrs": {}}
                    order.append(tidx)
                titles[tidx]["attrs"][aid] = val
            elif line.startswith("SINFO:"):
                v = _parse_qvals(line.split(":", 1)[1].strip())
                if len(v) < 5:
                    continue
                tidx, sidx, aid, code, val = v[0], v[1], v[2], v[3], v[4]
                if tidx not in titles:
                    titles[tidx] = {"index": int(tidx), "streams": [], "attrs": {}}
                    order.append(tidx)
                st = None
                for s in titles[tidx]["streams"]:
                    if s["index"] == int(sidx):
                        st = s; break
                if st is None:
                    st = {"index": int(sidx), "type": "unknown", "attrs": {}}
                    titles[tidx]["streams"].append(st)
                if aid == "1":
                    st["type"] = STREAM_TYPE_CODE.get(code, val.lower() if val else "unknown")
                st["attrs"][aid] = val
        except Exception:
            continue

    result = []
    for tidx in order:
        t = titles[tidx]
        a = t["attrs"]
        dur = 0
        try:
            d = a.get("9", "0:00:00").split(":")
            dur = int(d[0]) * 3600 + int(d[1]) * 60 + int(float(d[2]))
        except Exception:
            pass
        streams = []
        for s in sorted(t["streams"], key=lambda x: x["index"]):
            sa = s["attrs"]
            streams.append({
                "index": s["index"], "type": s["type"],
                "lang": sa.get("3", ""), "lang_name": sa.get("4", ""),
                "codec": sa.get("6", "") or sa.get("7", ""),
                "codec_id": sa.get("5", ""),
                "channels": sa.get("14", ""),
                "resolution": sa.get("19", ""),
                "desc": sa.get("30", ""),
                "flags": sa.get("22", ""),
            })
        result.append({
            "index": t["index"],
            "duration": dur,
            "duration_text": a.get("9", ""),
            "size_bytes": int(a.get("11", "0") or 0),
            "size_gb": round(int(a.get("11", "0") or 0) / 1024**3, 2),
            "chapters": int(a.get("8", "0") or 0),
            "source": a.get("16", ""),
            "output_name": a.get("27", ""),
            "name": a.get("2", "") or a.get("30", ""),
            "streams": streams,
            "video_count": sum(1 for s in streams if s["type"] == "video"),
            "audio": [s for s in streams if s["type"] == "audio"],
            "subtitles": [s for s in streams if s["type"] == "subtitle"],
            "selected": dur >= load_settings().get("min_title_seconds", 300),
            "episode": None,
            "custom_name": "",
        })
    # 过滤掉小于阈值的 title 放到单独列表(前端仍可见, 默认不选)
    return {"ok": True, "disc": disc, "titles": result}

def run_makemkv_info(iso_path, timeout=300):
    """调用 makemkvcon info 读取 ISO。返回 (text, error)"""
    spec = f"iso:{iso_path}"
    try:
        p = subprocess.run(
            ["makemkvcon", "-r", "--cache=1", "info", spec],
            capture_output=True, text=True, timeout=timeout, errors="ignore"
        )
        text = p.stdout or ""
        # 有些版本信息输出到 stderr
        if not text.strip() and p.stderr:
            text = p.stderr
        return text, (p.stderr[-500:] if p.returncode != 0 and not text.strip() else "")
    except subprocess.TimeoutExpired:
        return "", "识别超时(可能 ISO 损坏或需要 key)"
    except FileNotFoundError:
        return "", "makemkvcon 未安装"
    except Exception as e:
        return "", str(e)

# 识别缓存: path -> {disc, titles, identified_at}
IDENT_FILE = CONFIG_DIR / "identify.json"

@app.route("/api/identify", methods=["POST"])
def api_identify():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    force = data.get("force", False)
    if not path or not Path(path).exists():
        return jsonify({"ok": False, "error": "ISO 不存在"})
    cache = load_json(IDENT_FILE, {})
    if not force and path in cache and cache[path].get("titles"):
        return jsonify({"ok": True, **cache[path], "cached": True})
    log(f"identify {path}")
    text, err = run_makemkv_info(path)
    if not text.strip():
        return jsonify({"ok": False, "error": err or "无法读取 ISO 信息(蓝光光盘可能需要有效的 MakeMKV key)"})
    parsed = parse_makemkv_info(text)
    if not parsed["titles"]:
        return jsonify({"ok": False, "error": "未识别到任何 title。" + ("蓝光需要 key: 请把 key 放到 /config/keydata.txt" if "blu" in text.lower() else "")})
    parsed["identified_at"] = datetime.now().isoformat()
    parsed["path"] = path
    cache[path] = parsed
    save_json(IDENT_FILE, cache)
    # 更新扫描状态
    sc = load_json(SCAN_FILE, {})
    for it in sc.get("items", []):
        if it["path"] == path:
            it["status"] = "identified"
            it["title_count"] = len(parsed["titles"])
    save_json(SCAN_FILE, sc)
    return jsonify({"ok": True, **parsed, "cached": False})

@app.route("/api/identify_all", methods=["POST"])
def api_identify_all():
    """批量识别(后台线程), 返回任务已启动"""
    data = request.get_json(silent=True) or {}
    paths = data.get("paths", [])
    def worker():
        for path in paths:
            try:
                log(f"batch identify {path}")
                text, err = run_makemkv_info(path)
                if text.strip():
                    parsed = parse_makemkv_info(text)
                    if parsed["titles"]:
                        cache = load_json(IDENT_FILE, {})
                        parsed["identified_at"] = datetime.now().isoformat()
                        parsed["path"] = path
                        cache[path] = parsed
                        save_json(IDENT_FILE, cache)
                        sc = load_json(SCAN_FILE, {})
                        for it in sc.get("items", []):
                            if it["path"] == path:
                                it["status"] = "identified"
                                it["title_count"] = len(parsed["titles"])
                        save_json(SCAN_FILE, sc)
            except Exception as e:
                log(f"batch identify err {path} {e}")
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "started": len(paths)})

@app.route("/api/identify_state/<path:encoded>")
def api_identify_state(encoded):
    path = unquote(encoded)
    cache = load_json(IDENT_FILE, {})
    if path in cache:
        return jsonify({"ok": True, **cache[path]})
    return jsonify({"ok": False, "error": "未识别"})

# ══════════════════════════════════════════════════════════════
# 标题解析: 从 ISO 文件名/目录名提取候选片名与集数线索
# ══════════════════════════════════════════════════════════════
NOISE_WORDS = ["bluray", "blu-ray", "bd", "dvd", "remux", "1080p", "2160p", "720p",
               "4k", "uhd", "x264", "x265", "h264", "h265", "aac", "ac3", "dts",
               "disc", "vol", "volume", "part", "iso", "cn", "chs", "cht", "gb",
               "完整", "全集", "合集", "蓝光", "高清", "国语", "粤语", "中字"]

def guess_media_name(path):
    """从 ISO 路径提取候选片名。返回 (name, hints)"""
    p = Path(path)
    parts = [p.parent.name, p.stem]
    hints = {"season": None, "episode_range": None, "year": None, "disc_no": None}
    raw = " ".join(parts)
    # 年份
    m = re.search(r"\b(19\d{2}|20\d{2})\b", raw)
    if m:
        hints["year"] = int(m.group(1))
    # 季
    m = re.search(r"[Ss](?:eason)?\s*(\d{1,2})", raw)
    if m:
        hints["season"] = int(m.group(1))
    # 集范围 S01E01-E12 / E01-E12 / EP01-12
    m = re.search(r"E(\d{1,3})\s*[-~至]\s*E?(\d{1,3})", raw, re.I)
    if m:
        hints["episode_range"] = [int(m.group(1)), int(m.group(2))]
    # 碟片号 Disc1 / Vol.2 / Part3
    m = re.search(r"(?:disc|vol\.?|volume|part|disk)\s*(\d{1,2})", raw, re.I)
    if m:
        hints["disc_no"] = int(m.group(1))
    # 片名: 取 stem, 去噪
    name = p.stem
    name = re.sub(r"[._]", " ", name)
    name = re.sub(r"[\[\(【].*?[\]\)】]", " ", name)
    for w in NOISE_WORDS:
        name = re.sub(r"\b" + re.escape(w) + r"\b", " ", name, flags=re.I)
    name = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", name)
    name = re.sub(r"[Ss]\d{1,2}(E\d+)?", " ", name)
    name = re.sub(r"(?i)\bE\d{1,3}\s*[-~]\s*E?\d{1,3}\b", " ", name)
    name = re.sub(r"(?i)\b(disc|vol|volume|part|disk)[\s._]*\d{1,2}\b", " ", name)
    name = re.sub(r"\s+\d{1,2}\s*$", " ", name)  # 去掉尾部残留的碟片号
    name = re.sub(r"\s+", " ", name).strip(" -–_")
    return name, hints

# ══════════════════════════════════════════════════════════════
# 刮削: TMDB (支持反代/代理) + 豆瓣网页 + 手动输入
# ══════════════════════════════════════════════════════════════
def tmdb_request(endpoint, params):
    s = load_settings()
    key = s.get("tmdb_api_key", "").strip()
    base = s.get("tmdb_api_base", "https://api.themoviedb.org/3").rstrip("/")
    if not key:
        return None, "未配置 TMDB API Key"
    params = dict(params)
    params["api_key"] = key
    params.setdefault("language", s.get("language", "zh-CN"))
    try:
        r = requests_session().get(base + endpoint, params=params, timeout=15)
        if r.status_code == 200:
            return r.json(), None
        return None, f"TMDB HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return None, f"TMDB 请求失败: {str(e)[:120]}"

def scrape_tmdb(name, media_type=None, year=None):
    """搜索 TMDB, 返回候选列表"""
    results, errs = [], []
    types = [media_type] if media_type in ("tv", "movie") else ["tv", "movie"]
    for mt in types:
        params = {"query": name, "page": 1}
        if year:
            params["first_air_date_year" if mt == "tv" else "year"] = year
        data, err = tmdb_request(f"/search/{mt}", params)
        if err:
            errs.append(err)
            continue
        for it in (data or {}).get("results", [])[:8]:
            results.append({
                "provider": "tmdb", "id": it.get("id"), "media_type": mt,
                "title": it.get("name") or it.get("title", ""),
                "original_title": it.get("original_name") or it.get("original_title", ""),
                "year": (it.get("first_air_date") or it.get("release_date") or "")[:4],
                "overview": (it.get("overview") or "")[:300],
                "poster": it.get("poster_path"),
                "popularity": it.get("popularity", 0),
            })
    results.sort(key=lambda x: x.get("popularity", 0), reverse=True)
    return results, errs

def tmdb_details(tmdb_id, media_type):
    """取详情: 剧集返回每集列表(用于自动分配集数)"""
    s = load_settings()
    data, err = tmdb_request(f"/{media_type}/{tmdb_id}", {"append_to_response": "credits"})
    if err or not data:
        return None, err
    out = {
        "title": data.get("name") or data.get("title", ""),
        "year": (data.get("first_air_date") or data.get("release_date") or "")[:4],
        "overview": (data.get("overview") or "")[:500],
        "poster": data.get("poster_path"),
        "genres": [g["name"] for g in data.get("genres", [])],
        "episodes": [],
    }
    if media_type == "tv":
        for season in data.get("seasons", []):
            sn = season.get("season_number")
            if sn is None or sn == 0:
                continue
            det, _ = tmdb_request(f"/tv/{tmdb_id}/season/{sn}", {})
            for ep in (det or {}).get("episodes", []):
                out["episodes"].append({
                    "season": sn, "episode": ep.get("episode_number"),
                    "name": ep.get("name", ""),
                    "overview": (ep.get("overview") or "")[:200],
                    "air_date": ep.get("air_date", ""),
                })
    return out, None

def scrape_douban(name, media_type=None):
    """豆瓣网页搜索(可访问), 返回候选。仅做补充"""
    results, errs = [], []
    try:
        url = "https://www.douban.com/j/search_suggest"
        r = requests_session().get(url, params={"q": name, "type": "movie"},
                                   headers={"User-Agent": "Mozilla/5.0",
                                            "Referer": "https://movie.douban.com/"}, timeout=12)
        if r.status_code == 200:
            for it in r.json().get("suggestions", [])[:8]:
                if it.get("type") != "movie":
                    continue
                # 解析 "标题 (年份)" 或带集数信息
                title = it.get("title", "")
                m = re.match(r"(.+?)\s*\((\d{4})\)", title)
                results.append({
                    "provider": "douban", "id": it.get("id"), "media_type": "movie",
                    "title": m.group(1) if m else title,
                    "year": m.group(2) if m else "",
                    "overview": "", "poster": None,
                    "popularity": 0, "raw": title,
                })
        else:
            errs.append(f"豆瓣 HTTP {r.status_code}")
    except Exception as e:
        errs.append(f"豆瓣请求失败: {str(e)[:100]}")
    return results, errs

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """刮削: 自动从路径推断片名, 或手动传入 name。返回候选供用户确认"""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    name = (data.get("name") or "").strip()
    media_type = data.get("media_type") or None
    if not name and path:
        name, hints = guess_media_name(path)
        data["hints"] = hints
    if not name:
        return jsonify({"ok": False, "error": "无法从路径推断片名, 请手动输入"})
    results, errs = [], []
    if load_settings().get("tmdb_api_key"):
        tm, e = scrape_tmdb(name, media_type)
        results += tm; errs += e
    if load_settings().get("douban_enabled", True):
        dm, e = scrape_douban(name, media_type)
        # 去重
        have = {(r["title"], r["year"]) for r in results}
        results += [r for r in dm if (r["title"], r["year"]) not in have]
        errs += e
    return jsonify({
        "ok": True, "query": name, "hints": data.get("hints", {}),
        "results": results[:20], "errors": errs,
        "need_manual": len(results) == 0,
    })

@app.route("/api/scrape/details", methods=["POST"])
def api_scrape_details():
    data = request.get_json(silent=True) or {}
    mid = data.get("id")
    mt = data.get("media_type", "movie")
    if not mid:
        return jsonify({"ok": False, "error": "缺少 id"})
    if mt == "tmdb" or data.get("provider") == "tmdb":
        mt = data.get("media_type", "movie")
        d, err = tmdb_details(mid, mt)
        if err:
            return jsonify({"ok": False, "error": err})
        return jsonify({"ok": True, "details": d})
    return jsonify({"ok": True, "details": {}})

# ══════════════════════════════════════════════════════════════
# 分割预览: 应用刮削结果 + 生成建议命名
# ══════════════════════════════════════════════════════════════
def sanitize_filename(s):
    s = re.sub(r'[\\/:*?"<>|]', "", str(s))
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "untitled"

def build_preview(path, selected_scrape, title_overrides):
    """根据选中的刮削结果和 title 覆盖, 生成分割预览"""
    ident = load_json(IDENT_FILE, {}).get(path, {})
    titles = ident.get("titles", [])
    if not titles:
        return None
    s = load_settings()
    media_type = selected_scrape.get("media_type", "movie")
    title = selected_scrape.get("title", "")
    year = selected_scrape.get("year", "")
    episodes = selected_scrape.get("episodes", [])
    season = (title_overrides or {}).get("season") or guess_media_name(path)[1].get("season") or 1
    ep_start = (title_overrides or {}).get("ep_start") or 1

    preview = []
    sel_idx = 0
    for t in titles:
        ov = (title_overrides or {}).get("titles", {}).get(str(t["index"]), {})
        selected = ov.get("selected", t.get("selected", False))
        custom = ov.get("custom_name", "")
        ep = ov.get("episode", t.get("episode"))
        if ep is None and selected and media_type == "tv":
            ep = ep_start + sel_idx
        if media_type == "tv" and episodes and ep is not None:
            epinfo = next((e for e in episodes if e.get("episode") == ep and e.get("season") == season), None)
            epname = epinfo.get("name", "") if epinfo else ""
        else:
            epname = ""
        if custom:
            newname = sanitize_filename(custom)
        elif media_type == "tv":
            tpl = s.get("name_template", "{title} - S{season:02d}E{episode:02d} - {epname}")
            try:
                newname = sanitize_filename(tpl.format(
                    title=title, season=season, episode=ep or 0,
                    epname=epname, year=year))
            except Exception:
                newname = sanitize_filename(f"{title} S{season:02d}E{ep or 0:02d}")
        else:
            tpl = s.get("movie_template", "{title} ({year})")
            newname = sanitize_filename(tpl.format(title=title, year=year))
        if t["duration"] < 60:
            newname = sanitize_filename("EXTRA_" + (t.get("source") or f"t{t['index']}"))
        preview.append({
            "index": t["index"], "selected": selected,
            "duration": t["duration"], "duration_text": t["duration_text"],
            "size_gb": t["size_gb"], "chapters": t["chapters"],
            "audio": t["audio"], "subtitles": t["subtitles"],
            "source": t.get("source", ""),
            "old_name": t.get("output_name", ""),
            "new_name": newname + ".mkv",
            "episode": ep, "epname": epname,
            "custom_name": custom,
        })
        if selected:
            sel_idx += 1
    return {
        "ok": True, "path": path,
        "disc": ident.get("disc", {}),
        "media_type": media_type, "title": title, "year": year,
        "season": season,
        "preview": preview,
        "selected_count": sum(1 for p in preview if p["selected"]),
    }

@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    scrape = data.get("scrape", {})
    overrides = data.get("overrides", {})
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"})
    r = build_preview(path, scrape, overrides)
    if not r:
        return jsonify({"ok": False, "error": "请先识别该 ISO"})
    return jsonify(r)

# ══════════════════════════════════════════════════════════════
# 任务: 执行分割 (makemkvcon mkv), 实时进度
# ══════════════════════════════════════════════════════════════
TASKS_FILE = CONFIG_DIR / "tasks.json"
_task_lock = threading.Lock()

def new_task(task):
    with _task_lock:
        tasks = load_json(TASKS_FILE, [])
        tasks.insert(0, task)
        save_json(TASKS_FILE, tasks[:50])
    return task

def update_task(tid, **kw):
    with _task_lock:
        tasks = load_json(TASKS_FILE, [])
        for t in tasks:
            if t["id"] == tid:
                t.update(kw)
                break
        save_json(TASKS_FILE, tasks[:50])

def resolve_output_dir(path, mode, custom):
    s = load_settings()
    p = Path(path)
    if mode == "custom" and custom:
        out = Path(custom)
    elif s.get("output_mode") == "custom" and s.get("output_path"):
        out = Path(s["output_path"])
    else:
        out = p.parent / (p.stem + "_split")
    out.mkdir(parents=True, exist_ok=True)
    return out

def run_split_task(tid, path, title_indices, output_dir, renames):
    """后台线程: 逐 title 调用 makemkvcon mkv"""
    try:
        update_task(tid, status="running", started_at=datetime.now().isoformat())
        loglines = []
        def logp(msg):
            loglines.append(msg)
            update_task(tid, log=loglines[-60:])
            log(f"[task {tid}] {msg}")

        spec = f"iso:{path}"
        idx_str = ",".join(str(i) for i in title_indices)
        logp(f"开始分割: {Path(path).name}")
        logp(f"titles: {idx_str}")
        logp(f"输出: {output_dir}")

        cmd = ["makemkvcon", "-r", "--progress=-same", "mkv", spec, idx_str, output_dir]
        logp("命令: " + " ".join(cmd[:5]) + " ...")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, errors="ignore")
        last_progress = -1
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("PRGV:"):
                try:
                    parts = line.split(":", 1)[1].split(",")
                    cur, total = int(parts[0]), int(parts[1])
                    pct = int(cur * 100 / total) if total else 0
                    if pct != last_progress:
                        last_progress = pct
                        update_task(tid, progress=pct)
                except Exception:
                    pass
            elif line.startswith("MSG:"):
                try:
                    v = _parse_qvals(line.split(":", 1)[1].strip())
                    if len(v) >= 4:
                        logp(v[3])
                except Exception:
                    pass
        proc.wait()
        rc = proc.returncode
        if rc != 0:
            logp(f"MakeMKV 退出码 {rc}")
        # 应用重命名 (makemkv 输出 t00.mkv 等, 按选择顺序映射)
        outputs = sorted(Path(output_dir).glob("*.mkv"),
                         key=lambda x: x.stat().st_mtime)
        renamed = []
        for i, outf in enumerate(outputs):
            if i < len(renames) and renames[i]:
                target = Path(output_dir) / sanitize_filename(renames[i])
                if target != outf and not target.exists():
                    try:
                        outf.rename(target)
                        renamed.append(target.name)
                        logp(f"重命名: {outf.name} -> {target.name}")
                    except Exception as e:
                        logp(f"重命名失败 {outf.name}: {e}")
        update_task(tid, status="done" if rc == 0 else "failed",
                    progress=100 if rc == 0 else last_progress,
                    finished_at=datetime.now().isoformat(),
                    outputs=[str(x) for x in outputs], renamed=renamed,
                    log=loglines[-60:])
        # 更新扫描状态 + 历史
        sc = load_json(SCAN_FILE, {})
        for it in sc.get("items", []):
            if it["path"] == path:
                it["status"] = "split" if rc == 0 else "identified"
        save_json(SCAN_FILE, sc)
        hist = load_json(HISTORY_FILE, [])
        hist.insert(0, {"path": path, "output": output_dir, "titles": title_indices,
                        "status": "done" if rc == 0 else "failed",
                        "at": datetime.now().isoformat(), "renamed": renamed})
        save_json(HISTORY_FILE, hist[:100])
    except Exception as e:
        update_task(tid, status="failed", error=str(e),
                    finished_at=datetime.now().isoformat())
        log(f"[task {tid}] error {e}")

@app.route("/api/split", methods=["POST"])
def api_split():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    selected = data.get("selected", [])   # title indices
    renames = data.get("renames", [])     # 按 selected 顺序的 new_name
    mode = data.get("output_mode", "inplace")
    custom = data.get("output_path", "")
    if not path or not Path(path).exists():
        return jsonify({"ok": False, "error": "ISO 不存在"})
    if not selected:
        return jsonify({"ok": False, "error": "请至少选择一个 title"})
    outdir = resolve_output_dir(path, mode, custom)
    tid = datetime.now().strftime("%Y%m%d%H%M%S") + str(len(selected))
    task = {"id": tid, "path": path, "status": "queued", "progress": 0,
            "title_count": len(selected), "output": str(outdir),
            "created_at": datetime.now().isoformat(), "log": []}
    new_task(task)
    threading.Thread(target=run_split_task, args=(tid, path, selected, str(outdir), renames), daemon=True).start()
    return jsonify({"ok": True, "task": task})

@app.route("/api/tasks")
def api_tasks():
    return jsonify({"ok": True, "tasks": load_json(TASKS_FILE, [])})

@app.route("/api/task/<tid>")
def api_task(tid):
    for t in load_json(TASKS_FILE, []):
        if t["id"] == tid:
            return jsonify({"ok": True, "task": t})
    return jsonify({"ok": False, "error": "任务不存在"})

@app.route("/api/history")
def api_history():
    return jsonify({"ok": True, "history": load_json(HISTORY_FILE, [])})

if __name__ == "__main__":
    log("ISO Splitter started")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
