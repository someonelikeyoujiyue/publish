"""兰实大学事实库 + 体裁池。

prompt 模板（xhs_note / toutiao_weitt / video_narration）以前把全套兰实事实都塞给 LLM，
导致每篇笔记都拿"2007 认证 + 19 年零中断 + AACSB + 3-8 万学费"当稳定锚点，雷同严重。

现在改成 7 个独立 bucket（认证 / 费用 / 语言 / 规模 / 合作 / 校园 / 校友 + 行业背景），
每次跑 prompt 时随机抽 1-2 个 bucket 注入 + 1 个体裁种子。LLM 视野被收窄 → 每篇风格各异。

调用方：
  from publisher_hub.rsu_facts import draw_seed
  facts_text, angle = draw_seed(rng=random.Random(), k_buckets=2)
"""
from __future__ import annotations

import random as _random
from typing import Optional

# ── 兰实事实库（7 个 bucket + 1 个行业背景）────────────────────────────────
# 每个 bucket 里列若干 fact，prompt 注入时**全 bucket 都给**，让 LLM 在这个小集合里挑 1-3 条。
# 这样保证：同一篇笔记不会跨 bucket 乱拼。

FACT_BUCKETS: dict[str, list[str]] = {
    '认证与历史': [
        '2007 年首批进入中国教育部认证名单，至今 19 年连续零中断',
        '1990 年建校典礼由诗琳通公主亲临',
        '校名纪念拉玛五世之子 Rangsit 亲王',
        '现任校长曾任泰国卫生部长、外交部长、国会主席',
    ],
    '费用与生活成本': [
        '本科学费 3-8 万人民币 / 年',
        '生活费 6000-8000 元 / 月',
        '4 年总花费约 20-40 万人民币',
        '签证仅需约 360 美元存款证明',
    ],
    '授课语言与门槛': [
        '全中文 / 中英双语 / 全英文 三套教学轨道',
        '没有雅思 / 托福硬性要求',
        '没有高考分数硬性要求',
        '语言基础弱也能从中文 / 双语轨入手过渡',
    ],
    '规模与专业': [
        '134 个学位项目（本科 87 / 硕士 36 / 博士 11）',
        '36 个学院',
        '商学院获 AACSB 认证（全球商学院约 5% 通过）',
        '特色专业：传播艺术、航空、高尔夫管理、酒店管理',
    ],
    '国际合作': [
        '全球 200+ 大学学分互认',
        '英国双学位：西英格兰大学、谢菲尔德哈勒姆大学',
        '澳洲双学位：卧龙岗大学、莫道克大学',
        '可走 2+2 路径转去英澳完成后两年学位',
    ],
    '校园生活': [
        '校区位于曼谷北部巴吞他尼府',
        '距泰缅边境 600+ 公里、距泰柬边境 500+ 公里',
        '校内 18 洞高尔夫球场',
        '校内设医院、游泳池、健身房',
        '24 小时安保 + 校园警察',
    ],
    '传播艺术校友': [
        'Davika（《鬼夫》女主、Gucci 大使）出自传播艺术学院',
        'Narilya（《灵媒》女主）也是传播艺术校友',
        'Nadech 等 60+ 泰国一线明星出自传播艺术专业',
        '传播艺术学院在泰国娱乐圈是公认的造星摇篮',
    ],
}

# 行业背景：独立池，30% 概率追加一条作为"对比锚点"（不每次都用）
INDUSTRY_BACKGROUND: list[str] = [
    '2026 考研报名 343 万人，三年累计减少 131 万',
    '中国 2025 届毕业生 1222 万人，创历史新高',
    '2024 研究生就业率 33.2%，首次低于本科生 45.4%',
    '2024 在日中国留学生 12.3 万、在韩 8.6 万',
    '马来西亚、新加坡、菲律宾留学总花费普遍高于泰国',
    '中国赴泰游客 2025 同比降 34%（缅甸 / 柬边境犯罪被误投射到整个泰国）',
]


# ── 体裁池（每篇随机分配一种）────────────────────────────────────────────
# 体裁是 prompt 里直接拿来给 LLM 看的指令，所以是完整的写法描述句子。

ANGLE_POOL: list[str] = [
    '【个人体验视角】像一个真实在读学生在记录某个具体场景或一天发生的事；'
    '可以带情绪但不矫情；用"我"或第三人称都行；'
    '不写"学姐分享干货"这种 KOL 腔，写得像微信朋友圈日记，但更克制。',

    '【数据冷知识】围绕 1 个让人意外的数字 / 时间点 / 排名展开，'
    '配 1-2 句背景解释为什么这个数字有意义；不要套"3 件套对比"，'
    '只讲这 1 件事讲深。',

    '【政策时间线】用时间顺序列出 2-4 个关键节点（年份 + 事件），'
    '体裁更接近"科普"而非"安利"；语气平实；'
    '末段简短解释为什么这条时间线对中国学生重要。',

    '【校园场景速写】围绕兰实的某个具体物理场景（高尔夫场 / 校园医院 / '
    '巴吞他尼校区 / 某个学院的特色建筑），用画面感的描述带出特点；'
    '少用数字、多用感官细节（颜色 / 声音 / 空间感）。',

    '【学生 day-in-life】描述某专业某学生一天的节奏：早上几点起、'
    '上什么课、午饭吃什么、晚上做什么；通过日常带出专业特色或校园生态；'
    '不要写成宣传，写成"日常 vlog 文字版"。',

    '【单点深挖】挑 1 个最容易被忽略的事实点（不是"19 年认证"也不是"3-8 万学费"，'
    '这些被讲烂了），围绕它展开 3-4 段；可以从历史背景 / 制度逻辑 / '
    '实际影响等角度切入。',

    '【路径科普】不写兰实本身，写适合什么样的学生：'
    '专科直升硕士 / 高考滑档 / 考研落榜 / 想要欧美跳板 / 想低成本读商科…'
    '从"你是 X 类型学生" 出发解释这条路怎么走通；可以提兰实 1 次作落点，'
    '不要全程兰实兰实。',

    '【校友切片】围绕某一位或某一类校友（传播艺术明星、商科 AACSB 毕业生、'
    '航空管理出来的从业者）展开；介绍他们的轨迹 + 兰实在其中扮演的角色；'
    '不要堆砌名字列表，挑 1-2 个讲深。',
]


def draw_seed(
    rng: Optional[_random.Random] = None,
    k_buckets: int = 2,
    industry_prob: float = 0.3,
) -> tuple[str, str]:
    """随机抽 facts 子集 + 体裁，返回 (facts_text, angle)。

    Args:
        rng:           Random 实例。不传就用 module 级（每次进程内独立）。
        k_buckets:     抽几个事实 bucket（默认 2）
        industry_prob: 多大概率把 1 条行业背景追加进来（默认 30%）

    Returns:
        facts_text: 已格式化好的多 bucket 文本，可直接 .format(seed_facts=...) 用
        angle:     体裁说明，可直接 .format(seed_angle=...) 用
    """
    r = rng or _random.Random()

    bucket_names = list(FACT_BUCKETS.keys())
    r.shuffle(bucket_names)
    chosen_buckets = bucket_names[:max(1, k_buckets)]

    parts: list[str] = []
    for name in chosen_buckets:
        items = FACT_BUCKETS[name]
        parts.append(f'【{name}】')
        for it in items:
            parts.append(f'· {it}')
        parts.append('')

    # 30% 概率加 1 条行业背景作可选对比锚点
    if r.random() < industry_prob:
        parts.append('【行业背景（可作对比锚点，1 句即可）】')
        parts.append(f'· {r.choice(INDUSTRY_BACKGROUND)}')

    facts_text = '\n'.join(parts).rstrip()
    angle = r.choice(ANGLE_POOL)
    return facts_text, angle


def empty_seed_for_template_smoke_test() -> tuple[str, str]:
    """开发时给 .format() 一个非空字符串占位，避免 KeyError。生产不应该用。"""
    return '【认证与历史】\n· 2007 年首批进入中国教育部认证名单', ANGLE_POOL[0]
