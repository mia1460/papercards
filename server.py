#!/usr/bin/env python3
"""PaperCards: 本地论文素材卡片库。只用 Python 标准库。

运行: python3 server.py   然后打开 http://127.0.0.1:8787
"""
import html
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PAPERCARDS_PORT", "8787"))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("PAPERCARDS_DATA", os.path.join(ROOT, "data"))
IMAGES = os.path.join(DATA, "images")
DB_PATH = os.path.join(DATA, "cards.sqlite")
STATIC = os.path.join(ROOT, "static")
ZOTERO_DIR = os.path.expanduser(os.environ.get("ZOTERO_DIR", "~/Zotero"))
UA = "PaperCards/1.0 (local research notes tool)"

os.makedirs(IMAGES, exist_ok=True)
_lock = threading.Lock()


# ---------------------------------------------------------------- database

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY,
            key TEXT UNIQUE,
            title TEXT, authors TEXT, year TEXT, venue TEXT,
            doi TEXT, arxiv TEXT, url TEXT,
            zotero_key TEXT, zotero_lib TEXT,
            created_at REAL
        );
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY,
            type TEXT,            -- quote | figure | data | idea
            content TEXT,
            image TEXT,
            note TEXT,
            tags TEXT,            -- JSON list
            paper_id INTEGER REFERENCES papers(id) ON DELETE SET NULL,
            page TEXT,
            att_key TEXT,         -- Zotero 附件 key，用于跳回原文
            src_url TEXT,
            resolving INTEGER DEFAULT 0,
            created_at REAL, updated_at REAL
        );
        """)
        # 老数据库升级：来源标签（会议、团队等）
        if "tags" not in [r[1] for r in c.execute("PRAGMA table_info(papers)")]:
            c.execute("ALTER TABLE papers ADD COLUMN tags TEXT")
        # 图片标注：原图 + 标注图形（JSON），image 字段存渲染后的带标注图片
        card_cols = [r[1] for r in c.execute("PRAGMA table_info(cards)")]
        for col in ("image_orig", "annot"):
            if col not in card_cols:
                c.execute(f"ALTER TABLE cards ADD COLUMN {col} TEXT")


def paper_dict(r):
    if r is None:
        return None
    d = dict(r)
    d["authors"] = json.loads(d["authors"] or "[]")
    d["tags"] = json.loads(d.get("tags") or "[]")
    return d


_img_size = {}


def image_size(name):
    """读图片宽高（PNG 读文件头，JPEG 扫描 SOF 段），用于网页提前占位。结果缓存。"""
    if name in _img_size:
        return _img_size[name]
    size = None
    try:
        with open(os.path.join(IMAGES, name), "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                size = struct.unpack(">II", head[16:24])
            elif head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    m = f.read(4)
                    if len(m) < 4 or m[0] != 0xFF:
                        break
                    seg_len = struct.unpack(">H", m[2:4])[0]
                    if m[1] in (0xC0, 0xC1, 0xC2):
                        h, w = struct.unpack(">HH", f.read(5)[1:5])
                        size = (w, h)
                        break
                    f.seek(seg_len - 2, 1)
    except OSError:
        pass
    _img_size[name] = size
    return size


def card_dict(c, r):
    d = dict(r)
    d["tags"] = json.loads(d["tags"] or "[]")
    if d.get("image"):
        size = image_size(d["image"])
        d["img_w"], d["img_h"] = size if size else (None, None)
    d["paper"] = paper_dict(c.execute("SELECT * FROM papers WHERE id=?", (d["paper_id"],)).fetchone()) if d["paper_id"] else None
    return d


def upsert_paper(c, meta, override=False):
    """override=True 时用 meta 覆盖已有字段（联网/Zotero 得到的权威信息）。meta: title, authors(list), year, venue, doi, arxiv, url, zotero_key, zotero_lib. 返回 paper id。"""
    meta = {k: v for k, v in meta.items() if v}
    if meta.get("doi"):
        meta["doi"] = meta["doi"].strip().lower()
        m = re.match(r"10\.48550/arxiv\.(.+)", meta["doi"])
        if m and not meta.get("arxiv"):
            meta["arxiv"] = m.group(1)
    if meta.get("arxiv"):
        meta["arxiv"] = re.sub(r"v\d+$", "", meta["arxiv"])
    # 先按各种标识找已有的论文，避免同一篇在 Zotero 和 Chrome 里变成两条
    row = None
    for field in ("arxiv", "doi", "zotero_key", "url"):
        if meta.get(field):
            row = c.execute(f"SELECT * FROM papers WHERE {field}=?", (meta[field],)).fetchone()
            if row:
                break
    if not row and meta.get("title"):
        row = c.execute("SELECT * FROM papers WHERE lower(title)=lower(?)", (meta["title"],)).fetchone()
    fields = ["title", "authors", "year", "venue", "doi", "arxiv", "url", "zotero_key", "zotero_lib"]
    if "authors" in meta:
        meta["authors"] = json.dumps(meta["authors"], ensure_ascii=False)
    if row:
        # 只补空字段；Zotero 来源的信息优先覆盖网页猜测
        updates = {}
        for f in fields:
            if meta.get(f) and (not row[f] or override or meta.get("zotero_key")):
                updates[f] = meta[f]
        if updates:
            c.execute("UPDATE papers SET " + ",".join(f"{k}=?" for k in updates) + " WHERE id=?",
                      (*updates.values(), row["id"]))
        return row["id"]
    key = next((f"{f}:{meta[f]}" for f in ("arxiv", "doi", "zotero_key", "url", "title") if meta.get(f)), str(uuid.uuid4()))
    cur = c.execute("INSERT INTO papers (key," + ",".join(fields) + ",created_at) VALUES (?" + ",?" * len(fields) + ",?)",
                    (key, *[meta.get(f) for f in fields], time.time()))
    return cur.lastrowid


# ---------------------------------------------------------------- Zotero

def zotero_db():
    path = os.path.join(ZOTERO_DIR, "zotero.sqlite")
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def zotero_item_meta(z, item_id):
    fields = {r[0]: r[1] for r in z.execute(
        "SELECT f.fieldName, v.value FROM itemData d JOIN fields f ON f.fieldID=d.fieldID "
        "JOIN itemDataValues v ON v.valueID=d.valueID WHERE d.itemID=?", (item_id,))}
    creators = [(r[0] or "") + (" " if r[0] else "") + (r[1] or "") for r in z.execute(
        "SELECT c.firstName, c.lastName FROM itemCreators ic JOIN creators c ON c.creatorID=ic.creatorID "
        "WHERE ic.itemID=? ORDER BY ic.orderIndex", (item_id,))]
    it = z.execute("SELECT key, libraryID FROM items WHERE itemID=?", (item_id,)).fetchone()
    lib = z.execute("SELECT groupID FROM groups WHERE libraryID=?", (it["libraryID"],)).fetchone()
    year = re.search(r"\d{4}", fields.get("date", "") or "")
    arxiv = None
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?$", fields.get("url", "") or "")
    if m:
        arxiv = m.group(1)
    return {
        "title": fields.get("title"),
        "authors": [a.strip() for a in creators if a.strip()],
        "year": year.group(0) if year else None,
        "venue": fields.get("publicationTitle") or fields.get("proceedingsTitle") or fields.get("conferenceName")
                 or fields.get("repository") or fields.get("university"),
        "doi": fields.get("DOI"),
        "arxiv": arxiv,
        "url": fields.get("url"),
        "zotero_key": it["key"],
        "zotero_lib": f"groups/{lib[0]}" if lib else "library",
    }


def zotero_current(window_title):
    """根据 Zotero 窗口标题 + 阅读器状态文件，找出正在读的 PDF 和页码。"""
    z = zotero_db()
    if not z:
        return None, None, None
    storage = os.path.join(ZOTERO_DIR, "storage")
    states = []
    for name in os.listdir(storage) if os.path.isdir(storage) else []:
        p = os.path.join(storage, name, ".zotero-reader-state")
        if os.path.exists(p):
            states.append((os.path.getmtime(p), name, p))
    states.sort(reverse=True)
    wt = (window_title or "").lower()
    chosen = None
    for _, att_key, p in states[:60]:
        r = z.execute("SELECT ia.parentItemID FROM items i JOIN itemAttachments ia ON ia.itemID=i.itemID WHERE i.key=?",
                      (att_key,)).fetchone()
        if not r or not r[0]:
            continue
        meta = zotero_item_meta(z, r[0])
        title = (meta.get("title") or "").lower()
        if title and wt and (title[:60] in wt or wt.split(" - ")[0][:60] in title):
            chosen = (att_key, p, meta)
            break
    if not chosen:
        return None, None, None
    att_key, p, meta = chosen
    try:
        page = str(json.load(open(p)).get("pageIndex", 0) + 1)
    except Exception:
        page = None
    return meta, att_key, page


def zotero_lookup(doi=None, arxiv=None):
    """网页上的论文如果也在 Zotero 库里，挂上 Zotero 链接。"""
    z = zotero_db()
    if not z:
        return None
    q = "SELECT d.itemID FROM itemData d JOIN fields f ON f.fieldID=d.fieldID JOIN itemDataValues v ON v.valueID=d.valueID WHERE "
    r = None
    if doi:
        r = z.execute(q + "f.fieldName='DOI' AND lower(v.value)=lower(?)", (doi,)).fetchone()
    if not r and arxiv:
        r = z.execute(q + "f.fieldName IN ('url','DOI','extra','archiveID') AND v.value LIKE ?", (f"%{arxiv}%",)).fetchone()
    return zotero_item_meta(z, r[0]) if r else None


# ---------------------------------------------------------------- web metadata

def http_get(url, accept="*/*", timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2_000_000).decode("utf-8", "replace")


def arxiv_meta(aid):
    xml = http_get(f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(aid)}")
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    e = ET.fromstring(xml).find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None:
        return {}
    doi = e.find("x:doi", ns)
    jref = e.find("x:journal_ref", ns)
    return {
        "title": " ".join(e.find("a:title", ns).text.split()),
        "authors": [a.find("a:name", ns).text for a in e.findall("a:author", ns)],
        "year": (e.find("a:published", ns).text or "")[:4],
        "venue": jref.text if jref is not None else "arXiv",
        "doi": doi.text if doi is not None else None,
        "arxiv": aid,
        "url": f"https://arxiv.org/abs/{aid}",
    }


def crossref_meta(doi):
    m = json.loads(http_get(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", "application/json"))["message"]
    parts = (m.get("issued") or {}).get("date-parts") or [[None]]
    return {
        "title": (m.get("title") or [None])[0],
        "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in m.get("author", [])],
        "year": str(parts[0][0]) if parts[0][0] else None,
        "venue": (m.get("container-title") or [None])[0],
        "doi": doi,
        "url": m.get("URL"),
    }


def ids_from_url(url):
    arxiv = doi = None
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", url or "")
    if m:
        arxiv = m.group(1)
    m = re.search(r"(10\.\d{4,9}/[^\s?#&]+)", urllib.parse.unquote(url or ""))
    if m:
        doi = re.sub(r"(\.pdf|/full|/abstract|/pdf)$", "", m.group(1))
    return arxiv, doi


def parse_meta_tags(page):
    tags = {}
    for m in re.finditer(r"<meta\s+[^>]*>", page, re.I):
        t = m.group(0)
        n = re.search(r'(?:name|property)\s*=\s*["\']([^"\']+)', t, re.I)
        v = re.search(r'content\s*=\s*["\']([^"\']*)', t, re.I)
        if n and v:
            tags.setdefault(n.group(1).lower(), []).append(html.unescape(v.group(1)))
    return tags


def meta_from_tags(tags):
    g = lambda *ks: next((tags[k][0] for k in ks if tags.get(k)), None)
    year = re.search(r"\d{4}", g("citation_publication_date", "citation_date", "citation_online_date", "dc.date") or "")
    return {
        "title": g("citation_title", "dc.title"),
        "authors": tags.get("citation_author") or tags.get("dc.creator") or [],
        "year": year.group(0) if year else None,
        "venue": g("citation_conference_title", "citation_journal_title", "citation_publisher"),
        "doi": g("citation_doi", "dc.identifier"),
        "arxiv": g("citation_arxiv_id"),
    }


def resolve_web(url, page_title, chrome_meta):
    """返回 (meta, need_network)。先用浏览器里拿到的 meta 标签，不够再联网补。"""
    arxiv, doi = ids_from_url(url)
    meta = meta_from_tags(chrome_meta or {})
    if meta.get("doi") and not str(meta["doi"]).startswith("10."):
        meta["doi"] = None
    meta["arxiv"] = meta.get("arxiv") or arxiv
    meta["doi"] = meta.get("doi") or doi
    meta["url"] = url
    if not meta.get("title"):
        meta["title"] = page_title
    return meta


def enrich_web(meta):
    """在后台线程里联网补全元数据。"""
    try:
        if meta.get("arxiv"):
            meta.update({k: v for k, v in arxiv_meta(meta["arxiv"]).items() if v})
        elif meta.get("doi"):
            meta.update({k: v for k, v in crossref_meta(meta["doi"]).items() if v})
        elif meta.get("url", "").startswith("http") and not meta.get("authors"):
            m2 = meta_from_tags(parse_meta_tags(http_get(meta["url"], "text/html")))
            meta.update({k: v for k, v in m2.items() if v})
            if m2.get("doi"):
                meta.update({k: v for k, v in crossref_meta(m2["doi"]).items() if v})
    except Exception as e:
        print("enrich failed:", meta.get("url"), e)
    try:
        zm = zotero_lookup(meta.get("doi"), meta.get("arxiv"))
        if zm:
            meta.update({k: v for k, v in zm.items() if v})
    except Exception as e:
        print("zotero lookup failed:", e)
    return meta


def guess_type(text):
    if not text:
        return "quote"
    nums = re.findall(r"\d+(?:\.\d+)?\s*(?:%|×|x\b|ms|s\b|GB|MB|TB|倍)?", text)
    return "data" if len(nums) >= 2 and len(text) < 600 else "quote"


# ---------------------------------------------------------------- capture

def capture(body):
    """快捷键捕获入口。body: kind(text|image), text, image_path, app, window_title, url, page_title, meta"""
    image = None
    if body.get("image_path") and os.path.exists(body["image_path"]):
        image = f"{uuid.uuid4().hex}.png"
        shutil.move(body["image_path"], os.path.join(IMAGES, image))
    text = (body.get("text") or "").strip()
    text = re.sub(r"-\n(?=[a-z])", "", text)        # PDF 断行连字符
    text = re.sub(r"(?<![\n.。:：])\n(?!\n)", " ", text)  # PDF 硬换行
    ctype = "figure" if image else guess_type(text)

    meta, att_key, page, need_net = None, None, None, False
    app = (body.get("app") or "").lower()
    if "zotero" in app:
        meta, att_key, page = zotero_current(body.get("window_title"))
    elif body.get("url"):
        meta = resolve_web(body["url"], body.get("page_title"), body.get("meta"))
        need_net = True

    now = time.time()
    with _lock, db() as c:
        pid = upsert_paper(c, meta) if meta and meta.get("title") else None
        cur = c.execute(
            "INSERT INTO cards (type,content,image,note,tags,paper_id,page,att_key,src_url,resolving,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ctype, text, image, "", json.dumps(body.get("tags") or [], ensure_ascii=False), pid, page, att_key,
             body.get("url"), 1 if need_net else 0, now, now))
        cid = cur.lastrowid

    if need_net:
        def work():
            m = enrich_web(dict(meta))
            with _lock, db() as c:
                new_pid = upsert_paper(c, m, override=True)
                c.execute("UPDATE cards SET paper_id=?, resolving=0 WHERE id=?", (new_pid, cid))
        threading.Thread(target=work, daemon=True).start()
    return cid


# ---------------------------------------------------------------- 自动截图（监视区域变化）

WATCH_CHANGED = 0.06   # 和上一张已保存的相比，超过这个比例的像素块变了 → 认为换页
WATCH_STABLE = 0.02    # 和上一帧相比低于这个比例 → 画面已稳定（翻页动画结束）
_watch = {"prev": None, "saved": None}


def image_sig(path, n=32):
    """把截图缩到 n×n 灰度，用于粗略比较画面是否变化。"""
    out = os.path.join(DATA, f".sig-{uuid.uuid4().hex}.bmp")
    try:
        subprocess.run(["sips", "-z", str(n), str(n), "-s", "format", "bmp", path, "--out", out],
                       capture_output=True, check=True)
        b = open(out, "rb").read()
    finally:
        if os.path.exists(out):
            os.remove(out)
    off = struct.unpack_from("<I", b, 10)[0]
    w, h = struct.unpack_from("<ii", b, 18)
    step = struct.unpack_from("<H", b, 28)[0] // 8
    row = (w * step + 3) & ~3
    return [sum(b[off + y * row + x * step: off + y * row + x * step + 3]) / 765 for y in range(abs(h)) for x in range(w)]


def sig_diff(a, b):
    return sum(abs(x - y) > 0.08 for x, y in zip(a, b)) / len(a)


def watch_frame(body):
    """收到一帧：换页且稳定就存成卡片。返回 (卡片 id 或 None, 是否需要尽快补一帧确认)。"""
    path = body.get("image_path")
    if not path or not os.path.exists(path):
        return None, False
    s = image_sig(path)
    prev, saved = _watch["prev"], _watch["saved"]
    _watch["prev"] = s
    changed = saved is None or sig_diff(s, saved) > WATCH_CHANGED
    stable = prev is not None and sig_diff(s, prev) < WATCH_STABLE
    if changed and stable:
        _watch["saved"] = s
        body["tags"] = ["自动截图"]
        return capture(body), False
    os.remove(path)
    return None, changed   # 变了但还没稳定（可能在翻页动画中）→ 让客户端马上再截一帧


def lookup_identifier(s):
    """手动添加来源：输入 DOI / arXiv 号 / 链接，自动补全。"""
    s = s.strip()
    arxiv, doi = ids_from_url(s)
    if not arxiv and re.fullmatch(r"(arxiv:)?\d{4}\.\d{4,5}(v\d+)?", s, re.I):
        arxiv = re.sub(r"^arxiv:", "", s, flags=re.I)
    meta = {"arxiv": arxiv, "doi": doi, "url": s if s.startswith("http") else None}
    if not (arxiv or doi or meta["url"]):
        return None
    return enrich_web(meta)


# ---------------------------------------------------------------- export

def cite_short(p):
    if not p:
        return ""
    a = p["authors"]
    who = (a[0].split()[-1] + (" et al." if len(a) > 2 else (" & " + a[1].split()[-1] if len(a) == 2 else ""))) if a else ""
    return ", ".join(filter(None, [who, p.get("year")]))


def bibtex(p):
    a = p["authors"]
    first = re.sub(r"\W", "", a[0].split()[-1].lower()) if a else "anon"
    word = re.sub(r"\W", "", (p.get("title") or "x").split()[0].lower())
    key = f"{first}{p.get('year') or ''}{word}"
    fields = [("title", "{" + (p.get("title") or "") + "}"), ("author", " and ".join(a)), ("year", p.get("year")),
              ("booktitle" if p.get("venue") and re.search(r"proc|conf|symposium|workshop", p["venue"], re.I) else "journal", p.get("venue")),
              ("doi", p.get("doi")), ("eprint", p.get("arxiv")), ("url", p.get("url"))]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
    return f"@article{{{key},\n{body}\n}}"


TYPE_LABELS = {"quote": "摘录", "figure": "图表", "data": "数据", "idea": "想法"}
HL_COLORS = {"yellow": "#ffd400", "red": "#ff6666", "green": "#5fb236", "blue": "#2ea8e5", "purple": "#a28ae5"}


def rich_to_markdown(raw):
    """卡片内容里的 =={color}高亮== / ++下划线++ 转成导出用的 <mark> / <u>。"""
    out, hl, ul, last = [], None, False, 0

    def push(t):
        if not t:
            return
        if ul:
            t = f"<u>{t}</u>"
        if hl:
            t = f'<mark style="background:{HL_COLORS[hl]}">{t}</mark>'
        out.append(t)
    for m in re.finditer(r"==\{(\w+)\}|==|\+\+", raw):
        push(raw[last:m.start()])
        last = m.end()
        if m.group(1):
            hl = m.group(1) if m.group(1) in HL_COLORS else "yellow"
        elif m.group(0) == "==":
            hl = None if hl else "yellow"
        else:
            ul = not ul
    push(raw[last:])
    return "".join(out)


def export_image_names(cards):
    """导出时的图片文件名：类型-序号（同类型内按创建时间从早到晚编号），如 图表-1.png。"""
    names, count = {}, {}
    for c in sorted((c for c in cards if c.get("image")), key=lambda c: c["created_at"]):
        label = TYPE_LABELS.get(c["type"], c["type"])
        count[label] = count.get(label, 0) + 1
        ext = os.path.splitext(c["image"])[1] or ".png"
        names[c["id"]] = f"{label}-{count[label]}{ext}"
    return names


def export_markdown(cards, mode="type", type_order=None, img_names=None):
    """cards 已按页面显示顺序排列。mode：type 按类型分节 / paper 按来源分节 / flat 平铺。"""
    type_order = [t for t in (type_order or []) if t in TYPE_LABELS] or ["idea", "quote", "figure", "data"]
    img_names = img_names or {}
    out = [f"# 论文素材导出（{time.strftime('%Y-%m-%d')}）\n"]

    def card_md(c, show_type=True):
        p = c["paper"]
        loc = f"（{cite_short(p)}{', p. ' + c['page'] if c.get('page') else ''}）" if p else ""
        tags = " ".join("#" + t for t in c["tags"])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c["created_at"]))
        head = (f"**[{TYPE_LABELS.get(c['type'], c['type'])}]** " if show_type else "") + f"`{stamp}` {tags}".rstrip()
        md = [head + "\n"]
        if c.get("image"):
            md.append(f"![{img_names.get(c['id'], '')}](images/{img_names.get(c['id'], c['image'])})\n")
        if c.get("content"):
            md.append("\n".join("> " + line for line in rich_to_markdown(c["content"]).splitlines()) + f" {loc}\n")
        elif loc:
            md.append(loc + "\n")
        if c.get("note"):
            md.append(f"备注：{c['note']}\n")
        return md

    if mode == "paper":
        sections = {}
        for c in cards:
            sections.setdefault(c["paper_id"], []).append(c)
        for pid, cs in sections.items():
            p = cs[0]["paper"]
            if p:
                out.append(f"## {p['title']}\n\n*{', '.join(p['authors'][:6])}{' 等' if len(p['authors']) > 6 else ''}* · {p.get('venue') or ''} {p.get('year') or ''}"
                           + (f" · [链接]({p['url']})" if p.get("url") else "")
                           + (f"\n\n来源标签：{' · '.join(p['tags'])}" if p.get("tags") else "") + "\n")
            else:
                out.append("## 未标注来源\n")
            for c in cs:
                out += card_md(c)
            out.append("")
    elif mode == "type":
        for t in type_order:
            cs = [c for c in cards if c["type"] == t]
            if not cs:
                continue
            out.append(f"## {TYPE_LABELS[t]}（{len(cs)}）\n")
            for c in cs:
                out += card_md(c, show_type=False)
            out.append("")
    else:
        for c in cards:
            out += card_md(c)
        out.append("")

    # 文末：涉及的全部来源 + BibTeX
    papers, seen = [], set()
    for c in cards:
        if c["paper"] and c["paper_id"] not in seen:
            seen.add(c["paper_id"])
            papers.append(c["paper"])
    if papers and mode != "paper":
        out.append("## 来源\n")
        for p in papers:
            out.append(f"- {p['title']}" + (f" — {cite_short(p)}" if cite_short(p) else "") + (f" · {p['venue']}" if p.get("venue") else "")
                       + (f" · <{p['url']}>" if p.get("url") else "") + (f"（{' · '.join(p['tags'])}）" if p.get("tags") else ""))
        out.append("")
    if papers:
        out.append("## BibTeX\n\n```bibtex\n" + "\n\n".join(bibtex(p) for p in papers) + "\n```\n")
    return "\n".join(out)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def static(self, name, ctype):
        with open(os.path.join(STATIC, name), "rb") as f:
            self.send(200, f.read(), ctype)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path
        if path == "/":
            return self.static("index.html", "text/html; charset=utf-8")
        if path == "/quick":
            return self.static("quick.html", "text/html; charset=utf-8")
        if path.startswith("/static/") and re.fullmatch(r"/static/[\w.-]+\.(js|css)", path):
            name = os.path.basename(path)
            if os.path.exists(os.path.join(STATIC, name)):
                return self.static(name, "text/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8")
        if path == "/api/ping":
            return self.send(200, {"ok": True})
        if path.startswith("/images/"):
            name = os.path.basename(path)
            fp = os.path.join(IMAGES, name)
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    return self.send(200, f.read(), "image/png", {"Cache-Control": "max-age=31536000"})
            return self.send(404, {"error": "not found"})
        with db() as c:
            if path == "/api/cards":
                rows = c.execute("SELECT * FROM cards ORDER BY created_at DESC").fetchall()
                return self.send(200, [card_dict(c, r) for r in rows])
            m = re.fullmatch(r"/api/cards/(\d+)", path)
            if m:
                r = c.execute("SELECT * FROM cards WHERE id=?", (m.group(1),)).fetchone()
                return self.send(200, card_dict(c, r)) if r else self.send(404, {"error": "not found"})
            if path == "/api/papers":
                rows = c.execute("SELECT p.*, COUNT(k.id) AS n FROM papers p LEFT JOIN cards k ON k.paper_id=p.id "
                                 "GROUP BY p.id ORDER BY MAX(COALESCE(k.created_at, p.created_at)) DESC").fetchall()
                return self.send(200, [paper_dict(r) for r in rows])
            if path == "/api/tags":
                counts = {}
                for (t,) in c.execute("SELECT tags FROM cards"):
                    for tag in json.loads(t or "[]"):
                        counts[tag] = counts.get(tag, 0) + 1
                return self.send(200, sorted(counts.items(), key=lambda x: -x[1]))
            if path == "/api/export":
                # ids 按页面显示顺序传入；mode / types 让导出的分节和类型顺序与页面一致
                ids = [int(i) for i in q.get("ids", "").split(",") if i]
                rows = {r["id"]: r for r in c.execute("SELECT * FROM cards ORDER BY created_at DESC").fetchall()}
                cards = [card_dict(c, rows[i]) for i in ids if i in rows] if ids else [card_dict(c, r) for r in rows.values()]
                mode = q.get("mode") if q.get("mode") in ("type", "paper", "flat") else "type"
                img_names = export_image_names(cards)
                # 打包成 zip：md + images/，解压后图片相对路径可用
                name = f"papercards-{time.strftime('%Y%m%d-%H%M')}"
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(f"{name}/{name}.md", export_markdown(cards, mode, q.get("types", "").split(","), img_names))
                    for card in cards:
                        if card.get("image") and os.path.exists(os.path.join(IMAGES, card["image"])):
                            zf.write(os.path.join(IMAGES, card["image"]), f"{name}/images/{img_names[card['id']]}")
                return self.send(200, buf.getvalue(), "application/zip",
                                 {"Content-Disposition": f"attachment; filename={name}.zip"})
            m = re.fullmatch(r"/api/papers/(\d+)/bibtex", path)
            if m:
                return self.send(200, bibtex(paper_dict(c.execute("SELECT * FROM papers WHERE id=?", (m.group(1),)).fetchone())), "text/plain; charset=utf-8")
        if path == "/api/lookup":
            try:
                return self.send(200, lookup_identifier(q.get("q", "")) or {})
            except Exception as e:
                return self.send(400, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/api/tags/rename", "/api/tags/delete"):
            b = self.body()
            old_t, new_t = (b.get("old") or "").strip(), (b.get("new") or "").strip()
            if not old_t or (path.endswith("rename") and not new_t):
                return self.send(400, {"error": "missing tag"})
            n = 0
            with _lock, db() as c:
                for r in c.execute("SELECT id, tags FROM cards").fetchall():
                    tags = json.loads(r["tags"] or "[]")
                    if old_t not in tags:
                        continue
                    out = []
                    for t in tags:
                        t = new_t if (t == old_t and path.endswith("rename")) else t
                        if t != old_t and t not in out:   # 删除 / 合并后去重
                            out.append(t)
                    c.execute("UPDATE cards SET tags=? WHERE id=?", (json.dumps(out, ensure_ascii=False), r["id"]))
                    n += 1
            return self.send(200, {"cards": n})
        if path == "/api/watch/start":
            _watch.update(prev=None, saved=None)
            return self.send(200, {"ok": True})
        if path == "/api/watch/frame":
            cid, confirm = watch_frame(self.body())
            return self.send(200, {"id": cid, "confirm": confirm})
        if path == "/api/capture":
            return self.send(200, {"id": capture(self.body())})
        if path == "/api/upload":   # 卡片墙里粘贴图片
            n = int(self.headers.get("Content-Length") or 0)
            name = f"{uuid.uuid4().hex}.png"
            with open(os.path.join(IMAGES, name), "wb") as f:
                f.write(self.rfile.read(n))
            return self.send(200, {"image": name})
        if path == "/api/cards":
            b = self.body()
            now = time.time()
            with _lock, db() as c:
                pid = b.get("paper_id")
                if not pid and b.get("paper"):
                    pid = upsert_paper(c, b["paper"])
                cur = c.execute("INSERT INTO cards (type,content,image,note,tags,paper_id,page,src_url,created_at,updated_at)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (b.get("type", "quote"), b.get("content", ""), b.get("image"), b.get("note", ""),
                                 json.dumps(b.get("tags", []), ensure_ascii=False), pid, b.get("page"), b.get("src_url"), now, now))
                return self.send(200, card_dict(c, c.execute("SELECT * FROM cards WHERE id=?", (cur.lastrowid,)).fetchone()))
        self.send(404, {"error": "not found"})

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        b = self.body()
        with _lock, db() as c:
            m = re.fullmatch(r"/api/cards/(\d+)", path)
            if m:
                cid = int(m.group(1))
                if "paper" in b:   # 新建或改挂来源
                    b["paper_id"] = upsert_paper(c, b.pop("paper")) if b["paper"] else None
                allowed = ["type", "content", "note", "tags", "paper_id", "page", "image", "image_orig", "annot"]
                upd = {k: (json.dumps(b[k], ensure_ascii=False) if k == "tags" else b[k]) for k in allowed if k in b}
                old = c.execute("SELECT image, image_orig FROM cards WHERE id=?", (cid,)).fetchone()
                if old and "image" in upd and old["image"] and old["image"] != upd["image"]:
                    # 换了标注图：旧的渲染图（不是原图时）删掉
                    keep = {upd.get("image_orig", old["image_orig"]), upd["image"]}
                    if old["image"] not in keep:
                        try:
                            os.remove(os.path.join(IMAGES, old["image"]))
                        except OSError:
                            pass
                if upd:
                    c.execute("UPDATE cards SET " + ",".join(f"{k}=?" for k in upd) + ", updated_at=? WHERE id=?",
                              (*upd.values(), time.time(), cid))
                return self.send(200, card_dict(c, c.execute("SELECT * FROM cards WHERE id=?", (cid,)).fetchone()))
            m = re.fullmatch(r"/api/papers/(\d+)", path)
            if m:
                allowed = ["title", "authors", "year", "venue", "doi", "url", "tags"]
                upd = {k: (json.dumps(b[k], ensure_ascii=False) if k in ("authors", "tags") else b[k]) for k in allowed if k in b}
                if upd:
                    c.execute("UPDATE papers SET " + ",".join(f"{k}=?" for k in upd) + " WHERE id=?", (*upd.values(), m.group(1)))
                return self.send(200, paper_dict(c.execute("SELECT * FROM papers WHERE id=?", (m.group(1),)).fetchone()))
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        m = re.fullmatch(r"/api/cards/(\d+)", path)
        if m:
            with _lock, db() as c:
                r = c.execute("SELECT image, image_orig FROM cards WHERE id=?", (m.group(1),)).fetchone()
                c.execute("DELETE FROM cards WHERE id=?", (m.group(1),))
                for f in {r["image"], r["image_orig"]} if r else ():
                    if f:
                        try:
                            os.remove(os.path.join(IMAGES, f))
                        except OSError:
                            pass
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    init_db()
    print(f"PaperCards running at http://127.0.0.1:{PORT}  (data: {DATA})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
