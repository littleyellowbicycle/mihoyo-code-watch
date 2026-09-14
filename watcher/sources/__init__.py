"""数据源统一接口。

每个数据源只负责"把有可能含兑换码的帖子/公告抓回来"，
识别兑换码是 extractor 的事，去重是 state 的事。
"""

import logging

log = logging.getLogger("codewatch.sources")


class Post:
    """一条待分析的原始内容。

    preset_codes 用于结构化数据源（例如 hoyo-codes API）：
    那边返回的码是明确字段，不需要也不应该再过一遍启发式正则 ——
    否则奖励描述里的 "Mystic Enhancement Ore" 会被识别成兑换码 ENHANCEMENT。
    格式：[(code, region, note), ...]

    announce 用于「本身不是兑换码、但你必须知道」的福利情报
    （例如 TapTap 的签到活动：现在还没有码，要你去签了才发）。
    这类条目跳过码提取，按 post_id 去重，直接进推送。

    sign_slug 用于「累计签到」型活动：这类活动只推一次公告是不够的，
    因为奖励按累计天数发（「签到满 5 天领头像框」），中间断一天就白费。
    这类条目会额外走「每日提醒」链路 —— 活动进行期间每天提醒一次，
    按 (slug, 日期) 去重，不会变成每天轰炸。
    """

    __slots__ = ("source", "title", "text", "url", "game", "created_at",
                 "post_id", "preset_codes", "announce", "sign_slug", "progress")

    def __init__(self, source, title, text, url="", game=None, created_at=0,
                 post_id="", preset_codes=None, announce=False,
                 sign_slug="", progress=""):
        self.source = source
        self.title = title or ""
        self.text = text or ""
        self.url = url
        self.game = game
        self.created_at = created_at or 0
        self.post_id = post_id or ""
        self.preset_codes = preset_codes or []
        self.announce = bool(announce)
        self.sign_slug = sign_slug or ""
        self.progress = progress or ""

    @property
    def blob(self):
        """标题 + 正文，识别时一起看。"""
        return f"{self.title}\n{self.text}"

    def __repr__(self):
        return f"Post({self.source}, {self.title[:20]!r})"


class Source:
    name = "unnamed"

    def fetch(self):
        raise NotImplementedError


def build_sources(cfg, now):
    """按配置装配启用的数据源。任何数据源失败都不影响其它数据源。"""
    from . import hoyo_codes, miyoushe, taptap, weibo

    sources = []
    if cfg.enabled("miyoushe"):
        sources.append(miyoushe.MiyousheSearch(cfg, now))
    if cfg.enabled("hoyo_codes"):
        sources.append(hoyo_codes.HoyoCodesApi(cfg, now))
    if cfg.enabled("taptap"):
        sources.append(taptap.TapTapEvents(cfg, now))
    if cfg.enabled("weibo"):
        cookie = cfg.get("weibo.cookie", "")
        if not cookie:
            log.warning("微博源已启用但没填 cookie，跳过（微博必须带登录态才给数据）")
        else:
            sources.append(weibo.WeiboSuperTopic(cfg, now))
    return sources
