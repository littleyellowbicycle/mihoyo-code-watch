"""微博源（可选，必须自带 Cookie）。

实测：不带登录态访问 m.weibo.cn 会直接返回 432 风控页，
所以这个源默认关闭，只有你在配置里填了 Cookie 才会启用。

Cookie 获取：手机浏览器登录 m.weibo.cn，开发者工具里复制请求头里的
`SUB=...` 那一串（连同 SUB 一起）。它等价于账号密码，只放 GitHub Secrets。

如果你嫌麻烦，可以直接不启用这个源 —— 米游社搜索已经能覆盖
「前瞻直播码」和绝大多数社区流传的礼包码，微博源只是多一路保险。
"""

import html
import logging
import re
import time

from .. import extractor
from ..http import HttpError, get_json
from . import Post, Source

log = logging.getLogger("codewatch.sources.weibo")

API = ("https://m.weibo.cn/api/container/getIndex"
       "?type=uid&value={uid}&containerid=107603{uid}&page=1")

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(raw):
    """微博正文是 HTML 片段，去掉标签和链接残留，只留可读文本。"""
    text = TAG_RE.sub(" ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"网页链接|收起d|展开c|O网页链接", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class WeiboSuperTopic(Source):
    name = "微博"

    def __init__(self, cfg, now=None):
        self.cookie = cfg.get("weibo.cookie", "")
        self.accounts = cfg.get("weibo.accounts", {})
        self.fresh_hours = float(cfg.get("weibo.fresh_hours", 48))
        self.now = now or time.time()

    def fetch(self):
        cutoff = self.now - self.fresh_hours * 3600
        posts = []

        for name, uid in self.accounts.items():
            uid = str(uid).strip()
            if not uid:
                continue
            headers = {
                "User-Agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                               "Mobile/15E148 Safari/604.1"),
                "Referer": f"https://m.weibo.cn/u/{uid}",
                "X-Requested-With": "XMLHttpRequest",
                "Cookie": self.cookie,
            }
            try:
                data = get_json(API.format(uid=uid), headers=headers, retries=2)
            except HttpError as exc:
                log.warning("微博 %s(%s) 抓取失败：%s", name, uid, exc)
                continue

            if data.get("ok") != 1:
                log.warning("微博 %s(%s) 返回异常：ok=%s，通常是 Cookie 失效或风控",
                            name, uid, data.get("ok"))
                continue

            for card in (data.get("data") or {}).get("cards") or []:
                blog = card.get("mblog")
                if not blog:
                    continue
                created = _to_epoch(blog.get("created_at"))
                if created and created < cutoff:
                    continue
                body = strip_html(blog.get("text"))
                posts.append(Post(
                    source=f"微博({name})",
                    title=body[:50],
                    text=body,
                    url=f"https://m.weibo.cn/detail/{blog.get('id', '')}",
                    game=extractor.detect_game(body),
                    created_at=created,
                    post_id=str(blog.get("id") or ""),
                ))

        log.info("微博命中 %d 条", len(posts))
        return posts


def _to_epoch(created_at):
    """微博时间可能是 '刚刚' / '10分钟前' / '09-12'，统一转成 epoch，转不了就返回 0。"""
    if not created_at:
        return 0
    text = str(created_at)
    now = time.time()
    hour = 3600
    patterns = [
        (r"(\d+)\s*秒前", 1),
        (r"(\d+)\s*分钟前", 60),
        (r"(\d+)\s*小时前", hour),
    ]
    for pattern, unit in patterns:
        m = re.search(pattern, text)
        if m:
            return int(now - int(m.group(1)) * unit)
    if "刚刚" in text:
        return int(now)
    m = re.match(r"(\d{1,2})-(\d{1,2})", text)
    if m:
        lt = time.localtime(now)
        try:
            return int(time.mktime((lt.tm_year, int(m.group(1)), int(m.group(2)),
                                    0, 0, 0, 0, 0, -1)))
        except (ValueError, OverflowError):
            return 0
    return 0
