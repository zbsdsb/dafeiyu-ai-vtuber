"""人声来了把音乐压低、人声走了再慢慢抬回来（ducking）。

只放纯函数，不碰 pygame 和线程，方便单独测试。
"""


def duck_step(current, target, down_step=0.15, up_step=0.05):
    """音量向 target 靠一步。

    压低要快：人声一开口音乐就得让开，否则前几个字被盖住。
    恢复要慢：一下子顶回原音量会让人觉得音乐在“抢话”，慢慢抬起来更自然。
    """
    if current > target:
        return max(target, current - down_step)
    if current < target:
        return min(target, current + up_step)
    return current


def clamp_ratio(value, default):
    """把配置里的压低比例限制在 0~1；写成空值/乱码就用默认值。"""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, ratio))
