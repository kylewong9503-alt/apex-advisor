"""场景标签：给每一次记录标“这是在什么场合发生的”，用于筛选和分类；不进入任何比较，也不影响分析结果。
默认一组常用场景，你可以随时加自己的（最多 12 个自定义）。自定义的存在加密的 entity 表里（kind="scene_def"）。
标签挂在“互动”上（world.interactions[iid]["scenes"]），观察通过互动继承它。"""
from __future__ import annotations

DEFAULT_SCENES = ("开会", "一对一", "汇报", "评审", "邮件/消息", "电话", "饭局/闲聊", "其他")
MAX_CUSTOM = 12
MAX_LEN = 12


def custom_scenes(store) -> list:
    return [d["name"] for _, d in store.entities("scene_def")]


def all_scenes(store) -> list:
    return list(DEFAULT_SCENES) + [n for n in custom_scenes(store) if n not in DEFAULT_SCENES]


def add_custom(store, name: str) -> str:
    """新增自定义场景；已有就直接返回。名字太长、为空、或自定义已满 12 个时抛 ValueError。"""
    name = " ".join(name.split())[:MAX_LEN]
    if not name:
        raise ValueError("场景名为空")
    if name in all_scenes(store):
        return name
    if len(custom_scenes(store)) >= MAX_CUSTOM:
        raise ValueError("自定义场景已满")
    store.save_entity("scene_def", f"S{len(custom_scenes(store)) + 1}", {"name": name})
    store.db.commit()
    return name
