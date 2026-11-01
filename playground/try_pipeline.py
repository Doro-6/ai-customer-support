"""离线试跑 decide()，不需要邮箱、不发信。"""

import asyncio
import sys
from pathlib import Path

import nest_asyncio

sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    asyncio.get_running_loop()
except RuntimeError:
    pass  # 普通脚本：没有正在跑的循环，asyncio.run 会自己建一个，不需要 nest_asyncio
else:
    nest_asyncio.apply()  # 交互窗口：复用已有循环

from app.ai.decide import decide  # noqa: E402

question = "Panel 4 of my HP-S3 is cracked. Order 112-4839201-5528341"

decision = decide(question)
print(decision.model_dump_json(indent=2))
