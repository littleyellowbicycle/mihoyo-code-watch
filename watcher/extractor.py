"""从帖子正文里识别米哈游兑换码。

米哈游兑换码没有官方公开的固定格式，只能靠启发式规则识别：
  1. 形态：8-32 位大写字母 + 数字的组合，两端不能粘连其它字母数字
  2. 上下文：附近出现"兑换码 / 礼包码 / 前瞻"等词才给高分
  3. 前缀：GENSHIN / HSR / ZZZ / NAP / TOTHEMOON 这类已知前缀直接加分
  4. 区域：靠"国服 / 国际服"这类标记就近归位（同一帖子里两者常常并列）

宁可漏报也不要误报 —— 推送里塞一堆假码比不推送更糟。
"""

import re

REGION_RULES = [
    ("国服", ("国服", "官服", "官服兑换码", "陆服", "米游社服", "cn_gf", "cn_qd")),
    ("国际服", ("国际服", "海外服", "亚服", "美服", "欧服", "台港澳", "os_", "asia", "usa", "euro", "cht")),
]

GAME_RULES = [
    ("原神", ("原神", "提瓦特", "旅行者", "原石", "空月之歌", "至冬", "祈愿")),
    ("崩坏：星穹铁道", ("星穹铁道", "星铁", "崩铁", "开拓者", "星琼", "星轨")),
    ("绝区零", ("绝区零", "绳匠", "菲林", "zzz", "零号空洞")),
    ("崩坏3", ("崩坏3", "崩坏三", "舰长", "水晶", "女武神")),
    ("未定事件簿", ("未定事件簿", "律师", "泪之约定")),
]

CONTEXT_KEYWORDS = (
    "兑换码", "兑换", "礼包码", "礼包", "礼品码", "cdkey", "cdk",
    "前瞻", "直播", "福利", "白嫖", "领取", "限时",
)

PREFIX_HINTS = (
    "GENSHIN", "STARRAIL", "ZZZ", "NAP", "HONKAI", "TOTHEMOON",
    "HSR", "GS", "SR", "MYS",
)

STOPWORDS = frozenset({
    "UID", "HTTP", "HTTPS", "WWW", "HTML", "GITHUB", "ANDROID", "IOS",
    "APK", "CPU", "GPU", "RAM", "USB", "PDF", "EXCEL", "WORD", "PPT",
    "VIP", "NPC", "DPS", "APP", "BUG", "PVP", "RPG", "FAQ", "API",
    "URL", "IDEA", "PNG", "JPEG", "MP4", "QPS", "SSD", "HDD", "JSON",
    "CDKEY", "CDKEYS", "LOGIN", "QQGROUP", "WECHAT", "TAPTAP", "BILIBILI",
    "MYSQL", "LINUX", "WINDOWS", "CHROME", "GMAIL", "GOOGLE", "APPLE",
    "STEAM", "EPIC", "PSN", "XBOX", "SONY", "MIHOYO", "HOYOVERSE",
})

CANDIDATE_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-Z]{8,32}(?![0-9A-Za-z])")


class CodeHit:
    __slots__ = ("code", "region", "score", "reasons")

    def __init__(self, code, region, score, reasons):
        self.code = code
        self.region = region
        self.score = score
        self.reasons = reasons

    def __repr__(self):
        return f"CodeHit({self.code!r}, region={self.region!r}, score={self.score})"


def detect_game(*texts):
    """按关键词判断这是哪个游戏的福利，判断不出来就返回 None。"""
    blob = " ".join(t for t in texts if t).lower()
    for game, words in GAME_RULES:
        if any(w.lower() in blob for w in words):
            return game
    return None


def _nearest_region(text, pos, window=200):
    """向前找最近的一个区域标记。米游社帖子常写「国服兑换码：… 国际服兑换码：…」。"""
    best, best_dist = None, window + 1
    start = max(0, pos - window)
    for region, words in REGION_RULES:
        for word in words:
            idx = text.rfind(word, start, pos)
            if idx >= 0 and pos - idx < best_dist:
                best_dist = pos - idx
                best = region
    return best


def _has_context(text, pos, radius=90):
    start = max(0, pos - radius)
    end = min(len(text), pos + radius)
    window = text[start:end].lower()
    return any(k in window for k in CONTEXT_KEYWORDS)


def _looks_like_code(token):
    if token in STOPWORDS:
        return False
    if token.isdigit():
        return False
    letters = sum(c.isalpha() for c in token)
    if letters < 3:
        return False
    if len(set(token)) <= 2:
        return False
    return True


def extract(text, min_length=8, max_length=32, min_score=4):
    """从一段文本里提取候选兑换码，返回按分数降序的 CodeHit 列表。"""
    if not text:
        return []

    upper = text.upper()
    hits, seen = [], set()

    for match in CANDIDATE_RE.finditer(upper):
        token = match.group(0)
        if not (min_length <= len(token) <= max_length):
            continue
        if token in seen or not _looks_like_code(token):
            continue

        pos = match.start()
        score, reasons = 1, ["形态符合"]
        if _has_context(text, pos):
            score += 2
            reasons.append("附近有兑换码语境")
        if token.startswith(PREFIX_HINTS):
            score += 2
            reasons.append("命中已知前缀")
        if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
            score += 1
            reasons.append("字母数字混合")
        if 10 <= len(token) <= 16:
            score += 1
            reasons.append("长度典型")

        if score >= min_score:
            seen.add(token)
            hits.append(CodeHit(token, _nearest_region(text, pos), score, reasons))

    hits.sort(key=lambda h: (-h.score, h.code))
    return hits
