#!/usr/bin/env python3
"""米家兑换码哨兵（mihoyo-code-watch）

盯着米游社 / 社媒上的新兑换码，一发现就推到你微信。
只做「发现 + 提醒」，不碰你的游戏账号，所以没有封号风险。

用法：
    python run.py                # 正常跑一轮
    python run.py --dry-run      # 只打印，不推送、不改状态
    python run.py --reset        # 清空已推送记录，从头再来
"""

import argparse
import logging
import sys
import time

from watcher import __version__, extractor, notifier, state as state_mod
from watcher.config import load as load_config
from watcher.sources import build_sources

log = logging.getLogger("codewatch")

HEARTBEAT_SECONDS = 20 * 3600


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="米家兑换码哨兵")
    parser.add_argument("--root", default=".", help="工程根目录（config.json 所在位置）")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不推送也不写状态")
    parser.add_argument("--reset", action="store_true", help="清空已推送记录")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    return parser.parse_args(argv)


def collect(cfg, now):
    """跑所有数据源。单个源失败只记警告，绝不让整轮任务挂掉。"""
    posts = []
    for source in build_sources(cfg, now):
        try:
            fetched = source.fetch()
            log.info("  %-16s 返回 %d 条", source.name, len(fetched))
            posts.extend(fetched)
        except Exception as exc:  # noqa: BLE001 - 数据源是外部世界，什么都可能发生
            log.warning("  %-16s 失败：%s: %s", source.name, type(exc).__name__, exc)
    return posts


def analyse(cfg, posts, seen_codes):
    """从帖子里提取候选码，过滤掉已经推送过的。"""
    extract_cfg = cfg.get("extract", {})
    novel = []

    for post in posts:
        if post.announce:
            # 福利公告本身不带码，走另一条推送链路，不参与码提取
            continue

        if post.preset_codes:
            # 结构化数据源：码是明确字段，直接采信，不走启发式
            for code, region, note in post.preset_codes:
                if code in seen_codes:
                    continue
                seen_codes[code] = True
                hit = extractor.CodeHit(code, region, 10, [note or ""])
                novel.append((code, hit, post))
            continue

        hits = extractor.extract(
            post.blob,
            min_length=int(extract_cfg.get("min_length", 8)),
            max_length=int(extract_cfg.get("max_length", 32)),
            min_score=int(extract_cfg.get("min_score", 4)),
        )
        for hit in hits:
            if hit.code in seen_codes:
                continue
            seen_codes[hit.code] = True  # 同一轮内也去重
            novel.append((hit.code, hit, post))

    novel.sort(key=lambda item: (item[2].game or "", item[0]))
    return novel


def collect_announce(posts, state):
    """挑出没推送过的福利公告（TapTap 签到活动这类）。按 post_id 去重。"""
    seen = state.get("announced") or {}
    out, local = [], set()
    for post in posts:
        if not post.announce or not post.post_id:
            continue
        if post.post_id in seen or post.post_id in local:
            continue
        local.add(post.post_id)
        out.append(post)
    return out


def collect_sign_reminders(posts, state, today):
    """挑出今天需要提醒的「累计签到」活动。

    一次性公告解决不了这类活动：奖励按累计天数发（「签到满 5 天领头像框」），
    断一天就白费。所以活动进行期间每天都要提醒一次 —— 但一天只提醒一次，
    哪怕 cron 跑得再勤。去重键是 (slug, 日期)，存在 state 里。
    """
    out, local = [], set()
    for post in posts:
        slug = getattr(post, "sign_slug", "")
        if not slug or slug in local:
            continue
        if not state_mod.sign_reminder_due(state, slug, today):
            continue
        local.add(slug)
        out.append(post)
    return out


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log.info("米家兑换码哨兵 v%s 启动", __version__)

    cfg = load_config(args.root)
    now = time.time()
    state_path = cfg.state_path()

    if args.reset and state_path.exists():
        state_path.unlink()
        log.warning("已清空状态文件 %s", state_path)

    state, first_run = state_mod.load(state_path)
    log.info("记录中已有 %d 个历史兑换码", len(state["codes"]))

    posts = collect(cfg, now)
    log.info("共取得 %d 条待分析内容", len(posts))

    today = time.strftime("%Y-%m-%d")
    novel = analyse(cfg, posts, dict(state["codes"]))
    daily_reminder = bool(cfg.get("taptap.daily_reminder", True))
    announced = collect_announce(posts, state)
    reminders = collect_sign_reminders(posts, state, today) if daily_reminder else []

    if daily_reminder:
        # 签到活动只走「每日提醒」这一条链路，不进活动公告。
        # 否则第二天会出现重复：那天「每日提醒」因为"今天已提醒过"而不发，
        # 于是活动公告把它当成新活动又推一遍 —— 实测踩过这个坑。
        announced = [p for p in announced if not getattr(p, "sign_slug", "")]

    seed_only = first_run and cfg.get("first_run", "seed") == "seed"

    if seed_only:
        log.warning("首次运行：只登记不推送（否则会一口气刷屏 %d 个码 + %d 条活动）",
                    len(novel), len(announced) + len(reminders))
        for code, hit, post in novel:
            state_mod.record(state, code, post.source, post.game, hit.region, post.url, hit.score)
        for post in announced:
            state_mod.remember_announced(state, post.post_id)
        for post in reminders:
            state_mod.remember_sign_reminder(state, post.sign_slug, today)
        novel, announced, reminders = [], [], []

    if novel or announced or reminders:
        if novel:
            _print_hits(novel)
        if announced:
            log.info("发现 %d 条新福利/签到活动：", len(announced))
            for post in announced:
                log.info("  %s", (post.title or "")[:60])
        if reminders:
            log.info("今天需要签到 %d 个活动：", len(reminders))
            for post in reminders:
                log.info("  %s", (post.title or "")[:60])

        title = _title(novel, announced, reminders)
        body = _render(novel, announced, reminders)
        if args.dry_run:
            log.info("dry-run：跳过推送。内容预览：\n%s", body)
        else:
            try:
                notifier.send(cfg, title, body)
                for code, hit, post in novel:
                    state_mod.record(state, code, post.source, post.game,
                                     hit.region, post.url, hit.score)
                for post in announced:
                    state_mod.remember_announced(state, post.post_id)
                for post in reminders:
                    state_mod.remember_sign_reminder(state, post.sign_slug, today)
            except Exception as exc:  # noqa: BLE001
                log.error("推送失败，本轮不记录状态，下轮会重试：%s", exc)
                novel, announced, reminders = [], [], []
    else:
        log.info("没有新兑换码，也没有新的福利活动")

    if not args.dry_run:
        dropped = state_mod.prune_announced(state)
        if dropped:
            log.debug("清理了 %d 条过期公告记录", dropped)
        dropped_sign = state_mod.prune_sign_reminders(state)
        if dropped_sign:
            log.debug("清理了 %d 条过期签到提醒记录", dropped_sign)
        if now - float(state.get("last_heartbeat", 0)) > HEARTBEAT_SECONDS:
            state["last_heartbeat"] = int(now)
            log.debug("更新心跳时间，保持 GitHub Actions 定时任务不被回收")
        state_mod.save(state_path, state)

    log.info("本轮结束：新增 %d 个码 / %d 条活动 / %d 条签到提醒，历史累计 %d 个码",
             len(novel), len(announced), len(reminders), len(state["codes"]))
    return 0


def _title(novel, announced, reminders):
    bits = []
    if novel:
        bits.append(f"{len(novel)} 个新兑换码")
    if announced:
        bits.append(f"{len(announced)} 个新活动")
    if reminders:
        bits.append(f"{len(reminders)} 个待签到")
    return "🎁 发现 " + " / ".join(bits)


def _render(novel, announced, reminders):
    parts = []
    if reminders:
        parts.append(notifier.render_sign_reminder(reminders))
    if announced:
        parts.append(notifier.render_announce(announced))
    if novel:
        parts.append(notifier.render(novel))
    return "\n\n".join(parts)


def _print_hits(novel):
    log.info("识别到 %d 个新兑换码：", len(novel))
    for code, hit, post in novel:
        region = hit.region or "区域未标注"
        game = post.game or "游戏未识别"
        log.info("  %-16s %s · %s  (score=%d)", code, game, region, hit.score)


if __name__ == "__main__":
    sys.exit(main())
