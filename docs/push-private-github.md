# Push to a private GitHub repository

이 프로젝트를 새 프라이빗 저장소로 올리는 예시입니다.

```bash
gh auth login

gh repo create YOUR_GITHUB_ID/codex-canva-content-agent \
  --private \
  --source=. \
  --remote=origin \
  --push
```

GitHub CLI를 쓰지 않는다면 다음처럼 진행합니다.

```bash
git init
git add .
git commit -m "Create Codex Canva content agent"
git branch -M main
git remote add origin git@github.com:YOUR_GITHUB_ID/codex-canva-content-agent.git
git push -u origin main
```

이미 저장소가 있다면 `git remote add origin`만 해당 주소로 바꿔서 실행하세요.
