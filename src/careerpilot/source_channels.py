"""Independent, read-only local acquisition channels. External crawling stays disabled."""

from careerpilot import growth, insights, media, source_library


def jobs(db, principal, query):
    return [
        {
            "id": v["id"],
            "version": v["version"],
            "title": v["fields"]["title"]["value"],
            "kind": "job",
            "risk": insights.risk_signals(v),
        }
        for v in insights.local_jobs(db, principal)
        if query.casefold() in v["content"].casefold()
    ]


def references(db, principal, query):
    return [
        {
            "id": v["id"],
            "version": v["version"],
            "title": v.get("title", "授权摘录"),
            "kind": "reference",
        }
        for v in source_library.listing(db, principal)
        if query.casefold() in (v.get("title", "") + " " + v.get("text", "")).casefold()
    ]


def indexed(db, principal, query):
    return [
        {
            "id": v["id"],
            "version": v["version"],
            "title": v.get("title", "授权索引"),
            "kind": v.get("kind", "media"),
        }
        for v in media.listing(db, principal, query)
    ]


CHANNELS = (
    ("local_jobs", "本人导入岗位", jobs),
    ("authorized_references", "本人授权摘录", references),
    ("authorized_media", "本人授权媒体索引", indexed),
)


def search(db, principal, query):
    result = []
    for key, title, adapter in CHANNELS:
        try:
            # A DB-backed failed adapter must also roll back its transaction before the next one.
            with db.begin_nested():
                rows = adapter(db, principal, query)[:100]
            result.append({"channel": key, "title": title, "status": "available", "items": rows})
        except Exception:
            result.append(
                {
                    "channel": key,
                    "title": title,
                    "status": "unavailable",
                    "items": [],
                    "notice": "此来源暂不可用，其他来源仍可浏览；未保留异常原文。",
                }
            )
    return {
        "channels": result,
        "checked_at": growth.utc_now(),
        "external_channels_enabled": False,
        "notice": (
            "仅本地来源插件；健康状态是本次查询结果，不表示外部平台可用。未授权外部采集保持关闭。"
        ),
    }
