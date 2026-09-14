"""推送通道。默认 PushPlus（微信收消息，一个 token 搞定，国内可用）。

所有通道都只依赖标准库，失败会抛出异常由上层记录，不影响状态落盘。
"""

import logging

from .http import HttpError, get_text, post_json, quote

log = logging.getLogger("codewatch.notify")


def send(cfg, title, markdown):
    channel = (cfg.get("notify.channel") or "pushplus").lower()
    handler = {
        "pushplus": _pushplus,
        "serverchan": _serverchan,
        "wecom": _wecom,
        "bark": _bark,
        "telegram": _telegram,
        "none": _none,
    }.get(channel)

    if handler is None:
        raise HttpError(f"未知的推送通道：{channel}")

    handler(cfg, title, markdown)
    log.info("已通过 %s 推送", channel)


def _none(cfg, title, markdown):
    log.info("推送通道为 none，只写日志不发送")


def _pushplus(cfg, title, markdown):
    token = cfg.get("notify.pushplus.token", "")
    if not token:
        raise HttpError("缺少 PushPlus token")
    result = post_json("https://www.pushplus.plus/send", {
        "token": token,
        "title": title,
        "content": markdown,
        "template": "markdown",
    })
    if result.get("code") != 200:
        raise HttpError(f"PushPlus 返回异常：{result}")


def _serverchan(cfg, title, markdown):
    sendkey = cfg.get("notify.serverchan.sendkey", "")
    if not sendkey:
        raise HttpError("缺少 Server 酱 sendkey")
    result = post_json(f"https://sctapi.ftqq.com/{sendkey}.send", {
        "title": title,
        "desp": markdown,
    })
    if (result.get("data") or {}).get("errno"):
        raise HttpError(f"Server 酱返回异常：{result}")


def _wecom(cfg, title, markdown):
    webhook = cfg.get("notify.wecom.webhook", "")
    if not webhook:
        raise HttpError("缺少企业微信机器人 webhook")
    result = post_json(webhook, {
        "msgtype": "markdown",
        "markdown": {"content": f"## {title}\n{markdown}"},
    })
    if result.get("errcode"):
        raise HttpError(f"企业微信返回异常：{result}")


def _bark(cfg, title, markdown):
    base = cfg.get("notify.bark.url", "").rstrip("/")
    if not base:
        raise HttpError("缺少 Bark 地址")
    get_text(f"{base}/{quote(title)}/{quote(markdown[:900])}", retries=2)


def _telegram(cfg, title, markdown):
    bot_token = cfg.get("notify.telegram.bot_token", "")
    chat_id = cfg.get("notify.telegram.chat_id", "")
    if not bot_token or not chat_id:
        raise HttpError("缺少 Telegram bot_token 或 chat_id")
    result = post_json(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        {"chat_id": chat_id, "text": f"{title}\n\n{markdown}"},
    )
    if not result.get("ok"):
        raise HttpError(f"Telegram 返回异常：{result}")


def _with_auto_checkin(url):
    """给活动页链接带上自动签到参数。

    这个参数是 TapTap 活动页自己认的（app.js 里的 triggerAutoCheckin）。
    已登录状态下打开它，等于点了一次「立即签到」。
    """
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}auto_checkin=true"


def render_sign_reminder(posts):
    """渲染「今天该签到」提醒。

    与 render_announce 的分工：那条是「发现新活动」的一次性公告，
    这条是活动进行期间的每日提醒。因为它天天来，所以刻意写得短，
    只突出两件事：签了没（有 Cookie 才知道）、点哪个链接。
    """
    lines = [f"📌 **该签到了 —— 共 {len(posts)} 个活动**", ""]
    for post in posts:
        lines.append(f"### {post.game or post.title or post.source}")
        if post.progress:
            lines.append(f"　{post.progress}")
        link = _with_auto_checkin(post.url)
        if link:
            lines.append(f"　👉 [点这里自动签到]({link})")
        for row in (post.text or "").splitlines()[1:]:
            row = row.strip()
            if not row or row.startswith("活动页：") or row.startswith("发现时间："):
                continue
            lines.append(f"　{row}")
        lines.append("")

    lines.append("---")
    lines.append("💡 那个链接带 `?auto_checkin=true`，是活动页自带的自动签到参数：")
    lines.append("浏览器里已登录 TapTap 的话，点开即签到完成；未登录会先跳登录，登录后再点一次。")
    lines.append("累计签到型活动断一天可能就白费，所以每天提醒一次。")
    lines.append("本消息由 mihoyo-code-watch 自动生成。")
    return "\n".join(lines)


def render_announce(posts):
    """渲染福利公告（TapTap 签到活动这类，本身不带码，要你去签了才发）。

    posts: [Post]，已按 post_id 去过重。
    """
    lines = [f"发现 **{len(posts)}** 条新的渠道福利 / 签到活动", ""]
    for post in posts:
        lines.append(f"### {post.title or post.source}")
        if post.url:
            lines.append(f"[点这里进活动页]({post.url})")
        for row in (post.text or "").splitlines()[1:]:
            row = row.strip()
            # 链接已经在上一行给过了，正文里的裸 URL 就不用再刷一遍
            if row and not row.startswith("活动页："):
                lines.append(f"　{row}")
        lines.append("")
    lines.append("---")
    lines.append("⚠️ TapTap 签到活动页**网页版就能签** —— 官方活动说明原文写着「在本页面点击『立即签到』」。")
    lines.append("浏览器已登录 TapTap 时，给链接加上 `?auto_checkin=true` 会自动完成签到。")
    lines.append("礼包先到先得，看到「剩余库存」不多了就尽快去领。")
    lines.append("本消息由 mihoyo-code-watch 自动生成。")
    return "\n".join(lines)


def render(hits):
    """把命中结果渲染成一条 markdown 推送。

    hits: [(code, hit, post)] —— 已按游戏/区域归好组。
    """
    lines = [f"发现 **{len(hits)}** 个新兑换码", ""]
    grouped = {}
    for code, hit, post in hits:
        key = (post.game or "未识别游戏", hit.region or "未标注区域")
        grouped.setdefault(key, []).append((code, hit, post))

    for (game, region), items in grouped.items():
        lines.append(f"### {game} · {region}")
        for code, hit, post in items:
            lines.append(f"`{code}`")
            if post.preset_codes and hit.reasons and hit.reasons[0]:
                lines.append(f"　奖励：{hit.reasons[0]}")
            if post.title:
                lines.append(f"　来源：{post.source}｜{post.title[:40]}")
            else:
                lines.append(f"　来源：{post.source}")
            if post.url:
                lines.append(f"　[原帖]({post.url})")
        lines.append("")

    lines.append("---")
    lines.append("⚠️ 国服兑换码请在游戏内「设置 → 账户 → 兑换码」兑换，注意有效期。")
    lines.append("本消息由 mihoyo-code-watch 自动生成，请勿将含 Cookie 的配置外传。")
    return "\n".join(lines)
