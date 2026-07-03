# Workflows

## LinkedIn carousel

```bash
codex-canva plan input/brief.example.md \
  --channel linkedin-carousel \
  --out output/linkedin-plan.json \
  --markdown output/linkedin-plan.md
```

검토자는 Markdown을 읽고 메시지와 표현을 다듬습니다. 그다음 JSON에서 Canva 필드 매핑을 만듭니다.

## Canva asset upload

```bash
codex-canva canva upload-url \
  --name "carousel-cover" \
  --url "https://example.com/cover.png" \
  --tag campaign \
  --tag linkedin \
  --out output/cover-upload.json
```

업로드 작업이 성공하면 반환된 asset ID를 Autofill 데이터에 넣습니다.

## Brand template autofill

```bash
codex-canva canva autofill \
  --brand-template-id "BAGxxxxxxxx" \
  --data templates/canva_autofill_map.example.json \
  --out output/autofill-job.json
```

## Export

```bash
codex-canva canva export \
  --design-id "DAGxxxxxxxx" \
  --format pdf \
  --out output/export-job.json

codex-canva canva poll-export \
  --export-id "export-job-id" \
  --out output/export-complete.json
```
