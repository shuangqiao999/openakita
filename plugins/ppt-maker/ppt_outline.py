"""Outline generation and confirmation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ppt_models import DeckMode, SlideType


class OutlineBuilder:
    """Create a deterministic outline scaffold for all MVP modes."""

    def build(
        self,
        *,
        mode: DeckMode,
        title: str,
        slide_count: int,
        audience: str = "",
        requirements: dict[str, Any] | None = None,
        table_insights: dict[str, Any] | None = None,
        template_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = requirements or {}
        slides = self._slides_for_mode(mode, title, slide_count, table_insights)
        outline = {
            "title": title,
            "mode": mode.value,
            "audience": audience,
            "requirements": requirements,
            "storyline": [slide["purpose"] for slide in slides],
            "slides": slides,
            "needs_confirmation": True,
            "confirmation_questions": self._confirmation_questions(mode, template_profile),
        }
        if table_insights:
            outline["table_insights_summary"] = table_insights.get("key_findings", [])
        if template_profile:
            outline["template_profile_summary"] = {
                "name": template_profile.get("name"),
                "warnings": template_profile.get("warnings", []),
            }
        return outline

    def confirm(self, outline: dict[str, Any], updates: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {**outline, **(updates or {})}
        result["confirmed"] = True
        result["needs_confirmation"] = False
        return result

    def save(self, outline: dict[str, Any], project_dir: str | Path) -> Path:
        path = Path(project_dir) / "outline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _slides_for_mode(
        self,
        mode: DeckMode,
        title: str,
        slide_count: int,
        table_insights: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if mode == DeckMode.TABLE_TO_DECK:
            base = [
                ("数据概况", SlideType.DATA_OVERVIEW, "说明数据范围、口径和质量。"),
                ("核心指标", SlideType.METRIC_CARDS, "提炼最重要的经营指标。"),
                ("趋势与对比", SlideType.CHART_LINE, "展示关键趋势或分组对比。"),
                ("重点洞察", SlideType.INSIGHT_SUMMARY, "总结数据背后的业务含义。"),
                ("行动建议", SlideType.SUMMARY, "给出下一步建议。"),
            ]
            findings = (table_insights or {}).get("key_findings", [])
        else:
            base = [
                (title, SlideType.COVER, "建立主题和汇报场景。"),
                ("议程", SlideType.AGENDA, "让听众了解结构。"),
                ("背景与目标", SlideType.CONTENT, "解释为什么要做这件事。"),
                ("核心方案", SlideType.CONTENT, "展开主要观点。"),
                ("关键对比", SlideType.COMPARISON, "比较选择和取舍。"),
                ("路线图", SlideType.TIMELINE, "说明执行节奏。"),
                ("总结", SlideType.SUMMARY, "收束结论。"),
                ("Q&A", SlideType.CLOSING, "结束和互动。"),
            ]
            findings = []
        selected = base[: max(1, min(slide_count, len(base)))]
        while len(selected) < slide_count:
            selected.insert(-1, (f"补充内容 {len(selected)}", SlideType.CONTENT, "补充支撑材料。"))
        slides = []
        for index, (slide_title, slide_type, purpose) in enumerate(selected, start=1):
            slides.append(
                {
                    "id": f"slide_{index:02d}",
                    "index": index,
                    "title": slide_title,
                    "slide_type": slide_type.value,
                    "purpose": purpose,
                    "key_points": findings[:3] if slide_type == SlideType.INSIGHT_SUMMARY else [],
                }
            )
        return slides

    def _confirmation_questions(
        self,
        mode: DeckMode,
        template_profile: dict[str, Any] | None,
    ) -> list[str]:
        questions = ["页数是否合适？", "受众和汇报语气是否准确？", "是否需要调整章节顺序？"]
        if mode == DeckMode.TABLE_TO_DECK:
            questions.append("图表页是否覆盖了你最关心的指标？")
        if mode == DeckMode.TEMPLATE_DECK or template_profile:
            questions.append("是否接受模板 fallback，而不是 1:1 复刻所有母版效果？")
        return questions

