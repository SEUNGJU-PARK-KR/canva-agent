# Canva Connect integration notes

## Assets

Assets API는 Canva 라이브러리에 이미지와 영상을 올리고 이름, 태그, 메타데이터를 관리하는 데 씁니다. 외부 URL 업로드를 쓸 때는 URL이 공개 접근 가능해야 합니다.

## Exports

Export API는 비동기 작업입니다. 먼저 export job을 만들고, job ID로 상태를 확인합니다. 성공하면 다운로드 URL이 반환됩니다. 이 URL은 영구 보관 링크가 아니므로 필요한 파일은 별도 저장소로 복사하세요.

## Autofill

Autofill API는 브랜드 템플릿이나 기존 디자인의 필드에 텍스트, 이미지, 차트, 시트 데이터를 넣는 방식입니다. 템플릿 필드명과 데이터 키가 맞아야 안정적으로 동작합니다.

## Production checklist

- OAuth Authorization Code flow를 구현합니다.
- 토큰 저장소를 암호화합니다.
- 사용자별 Canva 권한 범위를 최소화합니다.
- 대량 업로드와 내보내기는 큐로 처리합니다.
- API 응답의 job status를 반드시 확인합니다.
- Preview API는 변경될 수 있으므로 버전 고정과 회귀 테스트를 둡니다.
