from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContentAsset(BaseModel):
    kind: Literal["headline", "body", "image_prompt", "cta", "caption", "note"]
    text: str
    canva_field: str | None = None


class CanvaSlide(BaseModel):
    slide_number: int = Field(ge=1)
    title: str
    goal: str
    assets: list[ContentAsset] = Field(default_factory=list)
    design_notes: list[str] = Field(default_factory=list)


class ContentPlan(BaseModel):
    title: str
    audience: str
    channel: str
    objective: str
    voice: str
    summary: str
    slides: list[CanvaSlide] = Field(default_factory=list)
    export_formats: list[str] = Field(default_factory=lambda: ["pdf"])
    review_checklist: list[str] = Field(default_factory=list)

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# {self.title}")
        lines.append("")
        lines.append(f"대상: {self.audience}")
        lines.append(f"채널: {self.channel}")
        lines.append(f"목표: {self.objective}")
        lines.append(f"톤: {self.voice}")
        lines.append("")
        lines.append("## 요약")
        lines.append(self.summary)
        lines.append("")
        lines.append("## 슬라이드 구성")
        for slide in self.slides:
            lines.append("")
            lines.append(f"### {slide.slide_number}. {slide.title}")
            lines.append(f"목표: {slide.goal}")
            if slide.assets:
                lines.append("")
                lines.append("콘텐츠")
                for asset in slide.assets:
                    field = f" → Canva 필드 `{asset.canva_field}`" if asset.canva_field else ""
                    lines.append(f"- {asset.kind}: {asset.text}{field}")
            if slide.design_notes:
                lines.append("")
                lines.append("디자인 메모")
                for note in slide.design_notes:
                    lines.append(f"- {note}")
        if self.review_checklist:
            lines.append("")
            lines.append("## 검수 체크리스트")
            for item in self.review_checklist:
                lines.append(f"- {item}")
        lines.append("")
        lines.append("## 내보내기 형식")
        lines.append(", ".join(self.export_formats))
        lines.append("")
        return "\n".join(lines)


class CanvaAutofillRequest(BaseModel):
    brand_template_id: str
    data: dict
