import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from openai import OpenAI

DIM = "\033[2m"  # 变暗（思考区开）
RESET = "\033[0m"  # 恢复默认（思考区关）

# 北京时间固定 UTC+8（中国不实行夏令时，所以用固定偏移就够，不必装 tzdata）
BEIJING = timezone(timedelta(hours=8))

# ── DeepSeek 定价：元 / 百万 token ────────────────────────────────
# 查证日期 2026-10-08，官方定价自 2026-09-10 12:00 起生效。
# 来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
PRICE_CACHE_HIT = 0.02  # 输入 · 缓存命中（空闲时段）
PRICE_CACHE_MISS = 1.00  # 输入 · 缓存未命中（空闲时段）
PRICE_OUTPUT = 4.00  # 输出（空闲时段）
PEAK_MULTIPLIER = 2.0  # 高峰时段 = 空闲时段 × 2
TOKENS_PER_UNIT = 1_000_000  # 单价按「每百万 token」计


def is_peak_now(now: datetime | None = None) -> bool:
    """当前是否处于高峰时段。

    高峰 = 周一至周五 9:00–12:00、14:00–18:00（北京时间），
    其余时段（含周末全天）都是空闲时段，价格是高峰的一半。

    已知偏差：官网把「中国法定节假日」也算作空闲时段，但本地代码拿不到
    节假日表，这里会把节假日误判成高峰 —— 结果是费用被**高估**。
    （保守方向：宁可估高，也别让你以为花得比实际少）
    """
    now = now or datetime.now(BEIJING)
    if now.weekday() >= 5:  # 5 = 周六, 6 = 周日
        return False
    hour = now.hour + now.minute / 60  # 折算成小数小时，好写区间
    return (9 <= hour < 12) or (14 <= hour < 18)


def estimate_cost(usage) -> float:
    """估算本次调用的费用，单位：元。

    计费口径（官网原话：费用 = token 数 × 单价）：
        输入分成两笔算 —— 缓存命中的部分便宜（0.02 元/百万），
        未命中的部分按正常输入价（1 元/百万）；输出单独一笔（4 元/百万）。
        三笔相加，再按峰谷时段决定要不要 ×2。
    """
    cost = (
        usage.prompt_cache_hit_tokens * PRICE_CACHE_HIT
        + usage.prompt_cache_miss_tokens * PRICE_CACHE_MISS
        + usage.completion_tokens * PRICE_OUTPUT
    ) / TOKENS_PER_UNIT

    return cost * PEAK_MULTIPLIER if is_peak_now() else cost


def main() -> None:
    load_dotenv()  # ① 读 .env

    api_key = os.getenv("DEEPSEEK_API_KEY")  # ② 从环境变量取 key
    if not api_key:
        raise SystemExit("没找到 DEEPSEEK_API_KEY，检查 .env 文件")

    client = OpenAI(  # ③ 建客户端
        api_key=api_key,
        base_url="https://api.deepseek.com",  # 指向 DeepSeek
    )

    stream = client.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "system", "content": "你是一个学习助手，用简洁的中文回答。"},
            {"role": "user", "content": "用一句话介绍你自己，15字以内"},
        ],
        stream=True,
        # 兼容真正的 OpenAI 用（不加这行，OpenAI 不返回 usage）。
        # 实测 DeepSeek 默认就带 usage，加不加都能拿到 —— 留着是为了换厂商时不会静默丢用量。
        stream_options={"include_usage": True},
    )

    thinking_started = False  # 判断思考标题是否打印
    answer_started = False  # 判断回答标题是否打印
    reason_count = 0  # 计数器
    content_count = 0
    usage = None  # 用量，最后一块才来

    for chunk in stream:
        if chunk.usage is not None:
            # ⚠️ 实测：usage 和 finish_reason 挤在**同一块**上，且这一块 choices 不为空。
            # 所以不能在这里 continue —— 写 Agent 循环时，finish_reason 正要靠这一块取。
            usage = chunk.usage
            continue
        if not chunk.choices:  # 防御性写法：万一有的块没 choices
            continue
        delta = chunk.choices[0].delta
        rc = getattr(delta, "reasoning_content", None)
        if rc:
            reason_count += 1
            if not thinking_started:
                print(f"{DIM}思考中……\n")
                thinking_started = True
            print(rc, end="", flush=True)
        elif delta.content:
            content_count += 1
            if not answer_started:
                print(f"{RESET}\n\n回答：\n")
                answer_started = True
            print(delta.content, end="", flush=True)

    print(RESET)
    print(f"思考块数：{reason_count}，回答块数：{content_count}")

    if usage is None:
        print("⚠️ 没拿到 usage，这次算不了钱")
        return

    print(
        f"用量：输入命中 {usage.prompt_cache_hit_tokens}"
        f" / 未命中 {usage.prompt_cache_miss_tokens}"
        f" / 输出 {usage.completion_tokens}"
    )
    period = "高峰时段" if is_peak_now() else "空闲时段"
    print(f"本次费用约 {estimate_cost(usage):.8f} 元（{period}）")


if __name__ == "__main__":
    main()
