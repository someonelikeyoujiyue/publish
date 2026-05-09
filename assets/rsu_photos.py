"""
兰实大学（RSU）内部素材图片清单
===================================
所有图片托管在服务器 47.236.168.208:8899，路径 /img/rsu/

用法：
    from newmedia.assets.rsu_photos import RSU_PHOTOS, RSU_BASE_URL, get_photo_url

    # 获取所有图片 URL 列表
    urls = [get_photo_url(f) for f in RSU_PHOTOS]

    # 按分类筛选
    campus = [p for p in RSU_PHOTOS if p["tags"] and "campus" in p["tags"]]
"""

RSU_BASE_URL = "http://47.236.168.208:8899/img/rsu"

# 图片清单（描述性文件名优先，原始文件名作为 fallback）
RSU_PHOTOS: list[dict] = [
    # ── 有描述性名称的图片 ──────────────────────────────────────────
    {"file": "gate-logo.jpg",               "desc": "校门 logo",          "tags": ["campus", "landmark"]},
    {"file": "international-building.jpg",  "desc": "国际楼",              "tags": ["campus", "building"]},
    {"file": "media-college-2.jpg",         "desc": "传媒学院 (2)",        "tags": ["campus", "building", "college"]},
    {"file": "media-college-corner.jpg",    "desc": "传媒学院一角",        "tags": ["campus", "building", "college"]},
    {"file": "architecture-college.jpg",    "desc": "建筑学院",            "tags": ["campus", "building", "college"]},
    {"file": "student-center-exterior.jpg", "desc": "学生活动中心外",      "tags": ["campus", "building"]},
    {"file": "activity-center-shrine.jpg",  "desc": "活动中心佛龛内部",    "tags": ["campus", "culture", "thai"]},
    {"file": "digital-media-building-1.jpg","desc": "数字传媒学院综合楼1",  "tags": ["campus", "building", "college"]},
    {"file": "sino-thai-exchange-center.jpg","desc": "中泰合作交流处（观音阁）","tags": ["campus", "culture", "chinese"]},
    {"file": "thai-temple-gilding.jpg",     "desc": "泰式寺庙镀金",        "tags": ["culture", "thai"]},
    {"file": "after-rain-rainbow.jpg",      "desc": "雨后彩虹",            "tags": ["campus", "scenery"]},
    {"file": "campus-photo-37574701160.jpg","desc": "校园照片",            "tags": ["campus"]},
    {"file": "sala-pano-process.jpg",       "desc": "Sala Pano 施工过程",  "tags": ["campus", "building"]},
    {"file": "sala-panorama.jpg",           "desc": "Sala 全景",           "tags": ["campus", "scenery"]},
    {"file": "rsu-campus-environment.gif",  "desc": "校园环境 GIF",        "tags": ["campus", "scenery", "gif"]},
    # ── 原始文件名图片 ─────────────────────────────────────────────
    {"file": "RSU.jpg",                     "desc": "兰实大学主图",        "tags": ["campus", "landmark"]},
    {"file": "RSU-003.jpg",                 "desc": "兰实大学 003",        "tags": ["campus"]},
    {"file": "RSU2015-04.jpg",              "desc": "兰实大学 2015-04",    "tags": ["campus"]},
    {"file": "RSU_Panorama1.jpg",           "desc": "兰实全景 1",          "tags": ["campus", "scenery"]},
    {"file": "RSU_Panorama1-2.jpg",         "desc": "兰实全景 1-2",        "tags": ["campus", "scenery"]},
    {"file": "DSC_1346.jpg",                "desc": "校园照片 DSC_1346",   "tags": ["campus"]},
    {"file": "DSC_3551.JPG",                "desc": "校园照片 DSC_3551",   "tags": ["campus"]},
    {"file": "DSC_8161.JPG",                "desc": "校园照片 DSC_8161",   "tags": ["campus"]},
    {"file": "DSC_8196_01.jpg",             "desc": "校园照片 DSC_8196",   "tags": ["campus"]},
    {"file": "DSC_9393-1.jpg",              "desc": "校园照片 DSC_9393",   "tags": ["campus"]},
    {"file": "DSC_9598.JPG",                "desc": "校园照片 DSC_9598",   "tags": ["campus"]},
    {"file": "IMG_0242.jpg",                "desc": "校园照片 IMG_0242",   "tags": ["campus"]},
    {"file": "IMG_0969.jpg",                "desc": "校园照片 IMG_0969",   "tags": ["campus"]},
    {"file": "IMG_4296.JPG",                "desc": "校园照片 IMG_4296",   "tags": ["campus"]},
    {"file": "IMG_6328.jpg",                "desc": "校园照片 IMG_6328",   "tags": ["campus"]},
    {"file": "IMG_7699.JPG",                "desc": "校园照片 IMG_7699",   "tags": ["campus"]},
    {"file": "IMG_9271-2.jpg",              "desc": "校园照片 IMG_9271",   "tags": ["campus"]},
    {"file": "_U7A8397.JPG",                "desc": "校园照片 U7A8397",    "tags": ["campus"]},
    {"file": "00001.jpg",                   "desc": "校园照片 00001",      "tags": ["campus"]},
    {"file": "0002.JPG",                    "desc": "校园照片 0002",       "tags": ["campus"]},
    {"file": "0003.jpg",                    "desc": "校园照片 0003",       "tags": ["campus"]},
    {"file": "0004.jpg",                    "desc": "校园照片 0004",       "tags": ["campus"]},
    {"file": "36766741624_98e581926a_o.jpg","desc": "校园照片（Flickr）1", "tags": ["campus", "scenery"]},
    {"file": "37790389271_ba5a65dcc8_o.jpg","desc": "校园照片（Flickr）2", "tags": ["campus", "scenery"]},
    {"file": "37843820152_98f565e8c6_o.jpg","desc": "校园照片（Flickr）3", "tags": ["campus", "scenery"]},
    {"file": "w05.jpg",                     "desc": "校园照片 w05",        "tags": ["campus"]},
]


def get_photo_url(photo: dict | str) -> str:
    """返回图片的完整 HTTP URL"""
    filename = photo["file"] if isinstance(photo, dict) else photo
    return f"{RSU_BASE_URL}/{filename}"


def get_photos_by_tag(tag: str) -> list[dict]:
    """按标签筛选图片"""
    return [p for p in RSU_PHOTOS if tag in (p.get("tags") or [])]


def get_all_urls(exclude_gif: bool = False, exclude_psd: bool = True) -> list[str]:
    """返回所有图片 URL 列表"""
    result = []
    for p in RSU_PHOTOS:
        f = p["file"].lower()
        if exclude_gif and f.endswith(".gif"):
            continue
        if exclude_psd and f.endswith(".psd"):
            continue
        result.append(get_photo_url(p))
    return result
