"""跑 18 条黄金邮件，逐条比对 section 和 action。

    uv run python -m evals.run_evals

不需要邮箱、不发信：直接调用 decide()。但 agent 的工具仍然真的读 Supabase 里的
知识库，所以这套用例同时也在检验知识库文本本身。
"""

from dataclasses import dataclass

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from app.ai.decide import decide
from app.ai.schemas import Decision
from evals.golden_emails import GOLDEN_EMAILS


@dataclass
class SectionMatches(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return ctx.output.section == ctx.expected_output["section"]


@dataclass
class ActionMatches(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return ctx.output.action == ctx.expected_output["action"]


def run_pipeline(email: str) -> Decision:
    return decide(email)


dataset = Dataset(
    name="golden-emails",
    cases=[
        Case(
            name=case["name"],
            inputs=case["email"],
            expected_output={"section": case["section"], "action": case["action"]},
        )
        for case in GOLDEN_EMAILS
    ],
    evaluators=[SectionMatches(), ActionMatches()],
)

if __name__ == "__main__":
    # max_concurrency=1 不是保守，是必须：
    # pydantic-ai 的 httpx AsyncClient 绑在创建它的事件循环上，而 evaluate_sync 的
    # 并发 worker 各跑各的循环，并发时会抛
    # "RuntimeError: <asyncio.locks.Event ...> is bound to a different event loop"。
    # 串行跑，所有调用共用同一个循环，就没有这个问题。18 条大约 5 分钟。
    report = dataset.evaluate_sync(run_pipeline, max_concurrency=1)
    report.print(include_input=False, include_output=True, include_durations=False)
