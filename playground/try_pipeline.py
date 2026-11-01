"""离线试跑：直接把一句话喂给 decide()，不需要邮箱、不发信。

    uv run python playground/try_pipeline.py

playground 在 app 之外，所以先把项目根加进 sys.path 再 import。

关于 nest_asyncio：课程里它会无条件 apply()，但那只在「编辑器交互窗口」里是对的——
那里已经有一个事件循环在跑，需要 nest_asyncio 才能再进去跑异步 agent。
在普通终端脚本里没有正在运行的循环，反而会让 pydantic-ai 的 run_sync 死锁
（实测卡住 7 分钟没有任何输出）。所以这里只在检测到已有循环时才应用。
"""

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
