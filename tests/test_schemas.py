from codex_canva_agent.schemas import ContentAsset, CanvaSlide, ContentPlan


def test_content_plan_to_markdown() -> None:
    plan = ContentPlan(
        title="Test",
        audience="Marketers",
        channel="LinkedIn",
        objective="Educate",
        voice="Clear",
        summary="Summary",
        slides=[
            CanvaSlide(
                slide_number=1,
                title="Intro",
                goal="Hook",
                assets=[ContentAsset(kind="headline", text="Hello", canva_field="title")],
            )
        ],
    )

    markdown = plan.to_markdown()

    assert "# Test" in markdown
    assert "Canva 필드 `title`" in markdown
