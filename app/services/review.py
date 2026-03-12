from __future__ import annotations

import json
import re
from collections import Counter

from ..schemas import ReviewDimensionScore, ReviewIssue, ReviewReport
from .model_router import model_router


ABSOLUTE_WORDS = ["必然", "一定", "完全", "彻底", "毫无疑问", "显著证明", "永远", "所有"]
COLLOQUIAL_WORDS = ["其实", "真的", "非常非常", "有点", "挺", "特别", "大家都知道", "说白了"]
SUMMARY_HEADINGS = ["摘要", "abstract"]
CONCLUSION_HEADINGS = ["结论", "总结", "结语", "conclusion"]
REFERENCE_HEADINGS = ["参考文献", "references"]


class ReviewService:
    def generate(self, title: str, text: str, model_alias: str) -> ReviewReport:
        heuristic_report = self._heuristic_report(title, text, model_alias)
        prompt = self._build_prompt(title, text, heuristic_report)
        try:
            payload, provider_name = model_router.complete_json(
                model_alias,
                system_prompt=(
                    "You are a senior Chinese manuscript reviewer. Return strict JSON with keys: "
                    "summary, overall_score, readiness, strengths, priorities, dimension_scores, issues, revised_outline."
                ),
                user_prompt=prompt,
            )
            if payload:
                return self._coerce_payload(payload, title, len(text), model_alias, provider_name)
        except Exception:
            pass
        return heuristic_report

    def _heuristic_report(self, title: str, text: str, model_alias: str) -> ReviewReport:
        paragraphs = [segment.strip() for segment in text.splitlines() if segment.strip()]
        issues: list[ReviewIssue] = []
        strengths: list[str] = []
        priorities: list[str] = []

        if 6 <= len(title) <= 32:
            strengths.append("标题长度适中，具备继续打磨成正式稿件的基础。")
        else:
            issues.append(
                self._issue(
                    "结构审稿",
                    "中",
                    1,
                    title[:40],
                    "标题长度不理想",
                    "标题过短会缺信息，过长会削弱聚焦。",
                    "将标题控制在 8-24 字，并补齐研究对象或结论导向。",
                    ["标题"],
                )
            )

        lowered = [paragraph.lower() for paragraph in paragraphs]
        if any(any(token in paragraph for token in SUMMARY_HEADINGS) for paragraph in lowered[:2]):
            strengths.append("前部存在摘要信号，适合继续整理摘要结构。")
        else:
            issues.append(
                self._issue(
                    "结构审稿",
                    "高",
                    1,
                    paragraphs[0][:60] if paragraphs else "",
                    "缺少明确摘要",
                    "专业稿件通常需要一个可独立阅读的摘要。",
                    "补写 120-200 字摘要，交代背景、方法、发现与结论。",
                    ["摘要", "结构完整性"],
                )
            )

        if any(any(token in paragraph for token in CONCLUSION_HEADINGS) for paragraph in lowered[-3:]):
            strengths.append("文末包含结论信号，稿件具备收束意识。")
        else:
            issues.append(
                self._issue(
                    "结构审稿",
                    "高",
                    len(paragraphs) or 1,
                    paragraphs[-1][:60] if paragraphs else "",
                    "缺少明确结论段",
                    "当前结尾更像自然停止，不足以形成闭环。",
                    "单独设置结论段，回收核心观点并说明价值与局限。",
                    ["结论"],
                )
            )

        if any(any(token in paragraph for token in REFERENCE_HEADINGS) for paragraph in lowered[-4:]):
            strengths.append("文末有参考资料信号，便于完善引证。")
        else:
            issues.append(
                self._issue(
                    "学术审稿",
                    "中",
                    len(paragraphs) or 1,
                    paragraphs[-1][:60] if paragraphs else "",
                    "缺少参考资料区块",
                    "学术和专业稿件缺少来源区块会削弱可信度。",
                    "补充参考文献或资料来源，并统一格式。",
                    ["引用", "可信度"],
                )
            )

        sentence_candidates = re.split(r"[。！？!?]\s*", text)
        long_sentences = [item.strip() for item in sentence_candidates if len(item.strip()) >= 70]
        for sentence in long_sentences[:3]:
            issues.append(
                self._issue(
                    "语言审稿",
                    "中",
                    self._locate_paragraph(paragraphs, sentence),
                    sentence[:60],
                    "句子过长",
                    "长句会放大歧义并降低阅读效率。",
                    "拆成两到三句，并把结论与依据分开表达。",
                    ["长句", "可读性"],
                )
            )

        colloquial_hits = [word for word in COLLOQUIAL_WORDS if word in text]
        if colloquial_hits:
            issues.append(
                self._issue(
                    "语言审稿",
                    "中",
                    self._locate_paragraph(paragraphs, colloquial_hits[0]),
                    colloquial_hits[0],
                    "存在口语化表达",
                    "口语词会削弱正式文本的专业感。",
                    f"替换口语词：{'、'.join(colloquial_hits[:5])}。",
                    ["正式度"],
                )
            )
        else:
            strengths.append("语气整体较克制，没有明显口语化痕迹。")

        absolute_hits = [word for word in ABSOLUTE_WORDS if word in text]
        if absolute_hits:
            issues.append(
                self._issue(
                    "风险审稿",
                    "高",
                    self._locate_paragraph(paragraphs, absolute_hits[0]),
                    absolute_hits[0],
                    "存在绝对化表述",
                    "绝对化措辞容易超出证据支持范围。",
                    "改为条件性、范围化表述，并补充依据。",
                    ["风险", "夸大表述"],
                )
            )

        citations = re.findall(r"\[[0-9]+\]|（[^）]*\d{4}[^）]*）|\([^)]*\d{4}[^)]*\)", text)
        if len(citations) == 0 and len(paragraphs) >= 4:
            midpoint = min(len(paragraphs) - 1, len(paragraphs) // 2)
            issues.append(
                self._issue(
                    "学术审稿",
                    "高",
                    max(1, len(paragraphs) // 2),
                    paragraphs[midpoint][:60] if paragraphs else "",
                    "论证缺少可见引用标记",
                    "多处判断像事实断言，但没有配套来源。",
                    "为关键数据、研究结论和判断语句补充来源。",
                    ["引用", "论据不足"],
                )
            )

        repetitive_phrases = self._find_repeated_phrases(text)
        for phrase, count in repetitive_phrases[:2]:
            issues.append(
                self._issue(
                    "语言审稿",
                    "中",
                    self._locate_paragraph(paragraphs, phrase),
                    phrase,
                    "重复表达较多",
                    f"短语“{phrase}”重复 {count} 次，降低表达新鲜度。",
                    "替换同义表达，或合并相近句子。",
                    ["重复", "原创表达"],
                )
            )

        if len(paragraphs) >= 5 and not citations:
            priorities.append("优先补齐摘要、结论和关键引用，再进入语句级润色。")
        priorities.append("处理绝对化措辞和长句，先解决高风险可读性问题。")
        if repetitive_phrases:
            priorities.append("对重复短语和模板化连接词做一轮原创表达优化。")
        priorities.append("完成修改后再做一次通读，核对术语是否统一。")

        dimension_scores = [
            ReviewDimensionScore("结构审稿", self._score_for(issues, "结构审稿"), "关注结构完整性、摘要与结论闭环。"),
            ReviewDimensionScore("语言审稿", self._score_for(issues, "语言审稿"), "关注病句、长句、重复与口语化。"),
            ReviewDimensionScore("学术审稿", self._score_for(issues, "学术审稿"), "关注引用、论证支撑和概念清晰度。"),
            ReviewDimensionScore("风险审稿", self._score_for(issues, "风险审稿"), "关注事实断言、绝对化和可信度。"),
        ]
        overall_score = max(45, round(sum(item.score for item in dimension_scores) / len(dimension_scores)))
        if overall_score >= 75:
            readiness = "可进入细修"
        elif overall_score >= 60:
            readiness = "建议先补结构与证据"
        else:
            readiness = "建议重整结构后再精修"

        summary = (
            "稿件已经具备成文基础，但目前最影响质量的是结构闭环、引用支撑和部分模板化表达。"
            if issues
            else "稿件整体结构较完整，可直接进入细节优化。"
        )
        revised_outline = [
            "1. 标题与摘要：明确对象、方法、结论。",
            "2. 引言：说明背景、问题与本文目标。",
            "3. 主体论证：按观点分段，每段先论点后依据。",
            "4. 结论：回收核心发现，补充局限与建议。",
            "5. 参考资料：统一来源格式。",
        ]

        return ReviewReport(
            title=title,
            summary=summary,
            char_count=len(text),
            overall_score=overall_score,
            readiness=readiness,
            strengths=strengths[:4],
            priorities=priorities[:4],
            dimension_scores=dimension_scores,
            issues=issues[:12],
            revised_outline=revised_outline,
            provider_name="heuristic",
            model_alias=model_alias,
        )

    def _coerce_payload(self, payload: dict, title: str, char_count: int, model_alias: str, provider_name: str) -> ReviewReport:
        dimension_scores = [
            ReviewDimensionScore(item.get("name", "未命名维度"), int(item.get("score", 70)), item.get("rationale", ""))
            for item in payload.get("dimension_scores", [])
        ]
        issues = [
            ReviewIssue(
                category=item.get("category", "AI审稿"),
                severity=item.get("severity", "中"),
                paragraph_index=int(item.get("paragraph_index", 1)),
                excerpt=item.get("excerpt", ""),
                finding=item.get("finding", ""),
                rationale=item.get("rationale", ""),
                recommendation=item.get("recommendation", ""),
                tags=item.get("tags", []),
            )
            for item in payload.get("issues", [])
        ]
        return ReviewReport(
            title=title,
            summary=payload.get("summary", ""),
            char_count=char_count,
            overall_score=int(payload.get("overall_score", 75)),
            readiness=payload.get("readiness", "可进入细修"),
            strengths=payload.get("strengths", []),
            priorities=payload.get("priorities", []),
            dimension_scores=dimension_scores or [ReviewDimensionScore("结构审稿", 75, "模型未返回分项理由。")],
            issues=issues,
            revised_outline=payload.get("revised_outline", []),
            provider_name=provider_name,
            model_alias=model_alias,
        )

    def _score_for(self, issues: list[ReviewIssue], category: str) -> int:
        deduction = 0
        for issue in issues:
            if issue.category != category:
                continue
            deduction += 12 if issue.severity == "高" else 6 if issue.severity == "中" else 3
        return max(48, 90 - deduction)

    def _find_repeated_phrases(self, text: str) -> list[tuple[str, int]]:
        cleaned = re.sub(r"\s+", "", text)
        grams = [cleaned[i : i + 6] for i in range(max(0, len(cleaned) - 5))]
        counts = Counter(gram for gram in grams if len(set(gram)) > 2)
        return [(gram, count) for gram, count in counts.most_common() if count >= 3]

    def _locate_paragraph(self, paragraphs: list[str], token: str) -> int:
        for index, paragraph in enumerate(paragraphs, start=1):
            if token in paragraph:
                return index
        return 1

    def _issue(
        self,
        category: str,
        severity: str,
        paragraph_index: int,
        excerpt: str,
        finding: str,
        rationale: str,
        recommendation: str,
        tags: list[str],
    ) -> ReviewIssue:
        return ReviewIssue(category, severity, paragraph_index, excerpt, finding, rationale, recommendation, tags)

    def _build_prompt(self, title: str, text: str, baseline: ReviewReport) -> str:
        return json.dumps(
            {
                "title": title,
                "text": text,
                "baseline": baseline.to_dict(),
                "requirements": [
                    "Keep the judgment practical and explainable.",
                    "Return Chinese text.",
                    "Focus on structure, language, academic rigor, and risks.",
                ],
            },
            ensure_ascii=False,
        )


review_service = ReviewService()
