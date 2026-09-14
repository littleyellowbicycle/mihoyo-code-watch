"""hoyo-codes 聚合 API —— 国际服兑换码的兜底来源。

https://hoyo-codes.seria.moe 是 Hoyo Buddy 项目维护的公开接口，
会定期抓取各来源的兑换码并用 HoYoLAB 账号验证有效性，无需鉴权。

注意：它返回的是**国际服**码（验证用的账号是亚服），
国服能不能兑换要你自己确认。它的价值是"多一路信号"，
配合米游社搜索一起用，避免单个来源失效就全瞎。
"""

import logging

from ..http import HttpError, get_json
from . import Post, Source

log = logging.getLogger("codewatch.sources.hoyo_codes")

API = "https://hoyo-codes.seria.moe/codes?game={game}"

GAME_LABEL = {
    "genshin": "原神",
    "hkrpg": "崩坏：星穹铁道",
    "nap": "绝区零",
}

GAME_ALIAS = {
    "genshin": "genshin",
    "starrail": "hkrpg",
    "hkrpg": "hkrpg",
    "zzz": "nap",
    "nap": "nap",
}


class HoyoCodesApi(Source):
    name = "hoyo-codes"

    def __init__(self, cfg, now=None):
        raw = cfg.get("hoyo_codes.games", ["genshin", "hkrpg", "nap"])
        self.games = [GAME_ALIAS.get(str(g).lower(), str(g).lower()) for g in raw]

    def fetch(self):
        posts = []
        for game in self.games:
            try:
                data = get_json(API.format(game=game), timeout=20)
            except HttpError as exc:
                log.warning("hoyo-codes %s 抓取失败：%s", game, exc)
                continue

            label = GAME_LABEL.get(game, game)
            preset = [
                (str(item.get("code", "")).strip().upper(), "国际服",
                 item.get("rewards", ""))
                for item in (data.get("codes") or [])
                if item.get("status") == "OK" and item.get("code")
            ]
            if not preset:
                continue

            # text 留空：这批码是结构化返回的，不需要再过正则，
            # 否则 rewards 里的英文词会被误判成兑换码。
            posts.append(Post(
                source="hoyo-codes(国际服)",
                title=f"{label} 当前可用兑换码 {len(preset)} 个",
                text="",
                url="https://hoyo-codes.seria.moe",
                game=label,
                post_id=f"hoyocodes-{game}",
                preset_codes=preset,
            ))

        log.info("hoyo-codes 覆盖 %d 个游戏", len(posts))
        return posts
