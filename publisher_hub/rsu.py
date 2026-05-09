"""RSU（兰实大学）素材图片清单 — 用于仿写时图片不足的兜底。

图片实际托管在 newmedia 服务器：http://47.236.168.208:8899/img/rsu/
本地 assets/rsu-photos/ 是物理备份。
"""
from __future__ import annotations

import random
from typing import Optional

DEFAULT_BASE_URL = 'http://47.236.168.208:8899/img/rsu'

RSU_PHOTOS: list[dict] = [
    # ── 描述性命名 ─────────────────────────────────────────────────
    {"file": "gate-logo.jpg",                "desc": "校门 logo",          "tags": ["campus", "landmark"]},
    {"file": "international-building.jpg",   "desc": "国际楼",              "tags": ["campus", "building"]},
    {"file": "media-college-2.jpg",          "desc": "传媒学院",            "tags": ["campus", "building", "college"]},
    {"file": "media-college-corner.jpg",     "desc": "传媒学院一角",        "tags": ["campus", "building", "college"]},
    {"file": "architecture-college.jpg",     "desc": "建筑学院",            "tags": ["campus", "building", "college"]},
    {"file": "student-center-exterior.jpg",  "desc": "学生活动中心",        "tags": ["campus", "building"]},
    {"file": "activity-center-shrine.jpg",   "desc": "佛龛内部",            "tags": ["campus", "culture", "thai"]},
    {"file": "digital-media-building-1.jpg", "desc": "数字传媒综合楼",      "tags": ["campus", "building", "college"]},
    {"file": "sino-thai-exchange-center.jpg","desc": "中泰合作交流处",      "tags": ["campus", "culture", "chinese"]},
    {"file": "thai-temple-gilding.jpg",      "desc": "泰式寺庙",            "tags": ["culture", "thai"]},
    {"file": "after-rain-rainbow.jpg",       "desc": "雨后彩虹",            "tags": ["campus", "scenery"]},
    {"file": "campus-photo-37574701160.jpg", "desc": "校园风光",            "tags": ["campus", "scenery"]},
    {"file": "sala-pano-process.jpg",        "desc": "Sala 施工过程",       "tags": ["campus", "building"]},
    {"file": "sala-panorama.jpg",            "desc": "Sala 全景",           "tags": ["campus", "scenery"]},
    # ── 原始文件名 ─────────────────────────────────────────────────
    {"file": "RSU.jpg",                      "desc": "兰实主图",            "tags": ["campus", "landmark"]},
    {"file": "RSU-003.jpg",                  "desc": "兰实大学",            "tags": ["campus"]},
    {"file": "RSU2015-04.jpg",               "desc": "兰实校园",            "tags": ["campus"]},
    {"file": "RSU_Panorama1.jpg",            "desc": "兰实全景 1",          "tags": ["campus", "scenery"]},
    {"file": "RSU_Panorama1-2.jpg",          "desc": "兰实全景 2",          "tags": ["campus", "scenery"]},
    {"file": "DSC_1346.jpg",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "DSC_3551.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "DSC_8161.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "DSC_8196_01.jpg",              "desc": "校园照",              "tags": ["campus"]},
    {"file": "DSC_9393-1.jpg",               "desc": "校园照",              "tags": ["campus"]},
    {"file": "DSC_9598.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_0242.jpg",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_0969.jpg",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_4296.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_6328.jpg",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_7699.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "IMG_9271-2.jpg",               "desc": "校园照",              "tags": ["campus"]},
    {"file": "_U7A8397.JPG",                 "desc": "校园照",              "tags": ["campus"]},
    {"file": "00001.jpg",                    "desc": "校园照",              "tags": ["campus"]},
    {"file": "0002.JPG",                     "desc": "校园照",              "tags": ["campus"]},
    {"file": "0003.jpg",                     "desc": "校园照",              "tags": ["campus"]},
    {"file": "0004.jpg",                     "desc": "校园照",              "tags": ["campus"]},
    {"file": "36766741624_98e581926a_o.jpg", "desc": "校园风光",            "tags": ["campus", "scenery"]},
    {"file": "37790389271_ba5a65dcc8_o.jpg", "desc": "校园风光",            "tags": ["campus", "scenery"]},
    {"file": "37843820152_98f565e8c6_o.jpg", "desc": "校园风光",            "tags": ["campus", "scenery"]},
    {"file": "w05.jpg",                      "desc": "校园照",              "tags": ["campus"]},
]


def url_for(photo: dict, base_url: str = DEFAULT_BASE_URL) -> str:
    return f'{base_url.rstrip("/")}/{photo["file"]}'


def pick_random(
    n: int,
    base_url: str = DEFAULT_BASE_URL,
    exclude: Optional[set[str]] = None,
    prefer_tags: Optional[list[str]] = None,
    seed: Optional[int] = None,
) -> list[str]:
    """随机选 n 张照片返回 URL 列表。

    Args:
        exclude:     已用的 URL 集合，避免重复
        prefer_tags: 优先选含这些 tag 的照片，不足再从全集补
        seed:        固定随机种子（可选，便于调试）

    返回去重后的 URL 列表（数量 ≤ n）。
    """
    rng = random.Random(seed) if seed is not None else random
    exclude = exclude or set()

    # 候选池：去掉已用的
    available = [
        p for p in RSU_PHOTOS
        if url_for(p, base_url) not in exclude
        and not p['file'].lower().endswith('.gif')   # 微信不喜欢 gif
    ]

    if prefer_tags:
        priority = [p for p in available if any(t in (p.get('tags') or []) for t in prefer_tags)]
        rest     = [p for p in available if p not in priority]
        rng.shuffle(priority)
        rng.shuffle(rest)
        ordered = priority + rest
    else:
        ordered = available[:]
        rng.shuffle(ordered)

    return [url_for(p, base_url) for p in ordered[:n]]
