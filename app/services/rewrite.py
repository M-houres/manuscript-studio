from __future__ import annotations

import difflib
import json
import re

from ..schemas import DiffBlock, RewriteResult
from .model_router import model_router


FORMAL_REPLACEMENTS = {
    "\u5176\u5b9e": "\u4e8b\u5b9e\u4e0a",
    "\u5927\u5bb6\u90fd\u77e5\u9053": "\u5df2\u6709\u5171\u8bc6\u8ba4\u4e3a",
    "\u8bf4\u767d\u4e86": "\u6362\u8a00\u4e4b",
    "\u7279\u522b": "\u5c24\u5176",
    "\u633a": "\u8f83\u4e3a",
    "\u975e\u5e38": "\u8f83\u4e3a",
}


class RewriteService:
    def optimize(self, title: str, text: str, mode: str, model_alias: str) -> RewriteResult:
        heuristic = self._heuristic_rewrite(title, text, mode, model_alias)
        prompt = self._build_prompt(title, text, mode, heuristic)
        try:
            payload, provider_name = model_router.complete_json(
                model_alias,
                system_prompt=(
                    "You are an originality optimization editor. Return JSON with keys: summary, optimized_text, "
                    "strategy_notes, citation_prompts, diff_blocks. Keep the original meaning, improve originality and readability."
                ),
                user_prompt=prompt,
            )
            if payload:
                return self._coerce_payload(payload, title, len(text), mode, model_alias, provider_name)
        except Exception:
            pass
        return heuristic

    def _heuristic_rewrite(self, title: str, text: str, mode: str, model_alias: str) -> RewriteResult:
        optimized = text
        for source, target in FORMAL_REPLACEMENTS.items():
            optimized = optimized.replace(source, target)
        if mode != "light":
            optimized = re.sub(r"([，,])(?=.{28,}[，,])", "；", optimized)
        optimized = re.sub(
            r"\b(首先|其次|最后)\b",
            lambda match: {"首先": "首先", "其次": "进一步", "最后": "最终"}[match.group(1)],
            optimized,
        )
        optimized = re.sub(r"(因此)(因此)+", "因此", optimized)
        optimized = re.sub(r"\n{3,}", "\n\n", optimized)

        summary = "已对口语化、重复连接词和部分长句进行了原创表达优化。"
        strategy_notes = [
            "优先保留原意，只调整模板化表达和句式节奏。",
            "对过长复句做了拆分，降低阅读负担。",
            "保留需要人工确认的专业判断，不直接改写事实结论。",
        ]
        citation_prompts: list[str] = []
        for line in text.splitlines():
            if any(token in line for token in ["研究表明", "数据显示", "显著", "证明"]) and "[" not in line and "（" not in line:
                citation_prompts.append(f"建议为以下陈述补充来源：{line[:50]}")
        diff_blocks = self._build_diff(text, optimized)
        return RewriteResult(
            title=title,
            mode=mode,
            char_count=len(text),
            summary=summary,
            optimized_text=optimized,
            strategy_notes=strategy_notes,
            citation_prompts=citation_prompts[:4],
            diff_blocks=diff_blocks[:12],
            provider_name="heuristic",
            model_alias=model_alias,
        )

    def _coerce_payload(
        self,
        payload: dict,
        title: str,
        char_count: int,
        mode: str,
        model_alias: str,
        provider_name: str,
    ) -> RewriteResult:
        diff_blocks = [
            DiffBlock(
                change_type=item.get("change_type", "replace"),
                original=item.get("original", ""),
                updated=item.get("updated", ""),
                explanation=item.get("explanation", ""),
            )
            for item in payload.get("diff_blocks", [])
        ]
        return RewriteResult(
            title=title,
            mode=mode,
            char_count=char_count,
            summary=payload.get("summary", ""),
            optimized_text=payload.get("optimized_text", ""),
            strategy_notes=payload.get("strategy_notes", []),
            citation_prompts=payload.get("citation_prompts", []),
            diff_blocks=diff_blocks,
            provider_name=provider_name,
            model_alias=model_alias,
        )

    def _build_diff(self, original: str, updated: str) -> list[DiffBlock]:
        original_lines = [line.strip() for line in original.splitlines() if line.strip()]
        updated_lines = [line.strip() for line in updated.splitlines() if line.strip()]
        matcher = difflib.SequenceMatcher(a=original_lines, b=updated_lines)
        blocks: list[DiffBlock] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            blocks.append(
                DiffBlock(
                    change_type=tag,
                    original="\n".join(original_lines[i1:i2]),
                    updated="\n".join(updated_lines[j1:j2]),
                    explanation=self._explain_change(tag),
                )
            )
        return blocks

    def _explain_change(self, tag: str) -> str:
        explanations = {
            "replace": "替换原有表达，降低模板感并提升清晰度。",
            "insert": "补入过渡或限定语，让论述更完整。",
            "delete": "删除冗余内容，减少重复和空话。",
        }
        return explanations.get(tag, "对原文做了结构性微调。")

    def _build_prompt(self, title: str, text: str, mode: str, baseline: RewriteResult) -> str:
        return json.dumps(
            {
                "title": title,
                "text": text,
                "mode": mode,
                "baseline": baseline.to_dict(),
                "requirements": [
                    "Preserve meaning and stance.",
                    "Improve originality, readability, and terminology consistency.",
                    "Output Chinese.",
                ],
            },
            ensure_ascii=False,
        )


rewrite_service = RewriteService()
