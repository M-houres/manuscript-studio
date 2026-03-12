# Manuscript Studio

A deployable MVP for two paid features:
- AI review for Chinese manuscripts
- Originality optimization with diff explanations

It includes:
- wallet balance and per-character billing
- switchable domestic-model routing (Qwen / DeepSeek by default)
- Alipay / WeChat / mock payment adapters
- text and `.docx` input
- Aliyun ECS deployment assets

## Local run
```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

## Pricing defaults
- AI review: 0.69 CNY / 1000 billable chars
- Originality optimization: 1.99 CNY / 1000 billable chars

## Notes
- If no model API key is configured, the app falls back to built-in heuristic review/rewrite.
- Payment callbacks are wired, but live Alipay / WeChat still require your real merchant credentials and domain.
- The default database is SQLite; Aliyun production should move to PostgreSQL.
