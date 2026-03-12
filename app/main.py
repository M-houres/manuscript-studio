from __future__ import annotations

import csv
import json
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from email.message import EmailMessage
import smtplib
from logging import handlers
from pathlib import Path
from collections import defaultdict, deque
from datetime import datetime, timedelta
from io import StringIO
from math import ceil
from typing import Deque
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import BASE_DIR, settings
from .database import get_session, init_db
from .models import AdminAudit, AnalysisRun, BonusClaim, DocumentRecord, EmailToken, ModelCallLog, ModelConfig, User, WalletAccount
from .security import hash_password, verify_password
from .services.billing import billing_service
from .services.documents import document_service
from .services.model_router import model_router
from .services.payments import payment_service
from .services.pricing import pricing_service
from .services.review import review_service
from .services.rewrite import rewrite_service


NAV_ITEMS = [
    {"key": "home", "label": "首页", "path": "/", "icon": "H", "match": "exact"},
    {"key": "rewrite", "label": "降重降AI", "path": "/review", "icon": "降", "match": "prefix"},
    {"key": "aigc", "label": "AIGC检测", "path": "/aigc-detect", "icon": "检", "match": "prefix"},
    {"key": "literature", "label": "文献综述", "path": "/literature", "icon": "综", "match": "prefix"},
    {"key": "proposal", "label": "开题报告", "path": "/proposal", "icon": "题", "match": "prefix"},
    {"key": "generate", "label": "文章生成", "path": "/generate", "icon": "写", "match": "prefix"},
    {"key": "format", "label": "格式调整", "path": "/format", "icon": "格", "match": "prefix"},
    {"key": "editor", "label": "AI编辑器", "path": "/editor", "icon": "编", "match": "prefix"},
    {"key": "ppt", "label": "AI PPT", "path": "/ppt", "icon": "P", "match": "prefix"},
    {"key": "audit", "label": "AI审稿", "path": "/audit", "icon": "审", "match": "prefix"},
    {"key": "assets", "label": "我的资产", "path": "/assets", "icon": "资", "match": "prefix"},
    {"key": "history", "label": "历史任务", "path": "/history", "icon": "史", "match": "prefix"},
]


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "request %s %s %s %sms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)
        self._auth_buckets: dict[str, Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not settings.rate_limit_enabled:
            return await call_next(request)
        path = request.url.path
        if path.startswith("/static") or path.startswith("/healthz"):
            return await call_next(request)

        key = _client_ip(request)
        now = time.time()
        if self._is_over_limit(
            self._buckets[key],
            now,
            settings.rate_limit_window_seconds,
            settings.rate_limit_requests,
        ):
            return Response("Too Many Requests", status_code=429)

        if path in {"/login", "/register"} and request.method.upper() == "POST":
            if self._is_over_limit(
                self._auth_buckets[key],
                now,
                settings.rate_limit_auth_window_seconds,
                settings.rate_limit_auth_requests,
            ):
                return Response("Too Many Requests", status_code=429)

        return await call_next(request)

    @staticmethod
    def _is_over_limit(bucket: Deque[float], now: float, window_seconds: int, max_requests: int) -> bool:
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            return True
        bucket.append(now)
        return False


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'",
        )
        if settings.hsts_enabled:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger("manuscript-studio")
if settings.log_file:
    try:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = handlers.RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(settings.log_level.upper())
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

executor = ThreadPoolExecutor(max_workers=4)

app = FastAPI(title=settings.app_name)
if settings.allowed_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie_name,
    https_only=settings.session_cookie_secure,
    same_site=settings.session_cookie_samesite,
    max_age=settings.session_cookie_max_age,
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestLogMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


@app.on_event('startup')
def on_startup() -> None:
    init_db()


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str) -> None:
    if not settings.csrf_enabled:
        return
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=400, detail="Invalid CSRF token.")


def template_context(request: Request, **kwargs) -> dict:
    context = {
        "request": request,
        "csrf_token": ensure_csrf_token(request),
        "nav_items": NAV_ITEMS,
        "active_path": request.url.path,
        "is_admin": False,
    }
    context.update(kwargs)
    return context


def redirect_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?error={quote(message)}", status_code=303)


def redirect_notice(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?notice={quote(message)}", status_code=303)


def redirect_admin(message: str, *, kind: str = "notice") -> RedirectResponse:
    return RedirectResponse(f"/admin?{kind}={quote(message)}", status_code=303)


def current_user(request: Request, session: Session) -> User | None:
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    return session.query(User).filter(User.id == user_id).one_or_none()


def require_user(request: Request, session: Session) -> User:
    user = current_user(request, session)
    if not user:
        raise HTTPException(status_code=303, headers={'Location': '/login'})
    return user


def is_admin_user(user: User | None) -> bool:
    if not user:
        return False
    admin_set = {email.lower() for email in settings.admin_emails}
    super_set = {email.lower() for email in settings.super_admin_emails}
    if not admin_set and not super_set:
        return False
    return user.email.lower() in admin_set.union(super_set)


def is_super_admin_user(user: User | None) -> bool:
    if not user:
        return False
    if not settings.super_admin_emails:
        return False
    return user.email.lower() in {email.lower() for email in settings.super_admin_emails}


def require_admin(request: Request, session: Session) -> User:
    user = require_user(request, session)
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def require_super_admin(request: Request, session: Session) -> User:
    user = require_user(request, session)
    if not is_super_admin_user(user):
        raise HTTPException(status_code=403, detail="Super admin access required.")
    return user


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException) -> HTMLResponse:
    if exc.status_code in {403, 404}:
        title = "无权限访问" if exc.status_code == 403 else "页面未找到"
        return templates.TemplateResponse(
            "error.html",
            template_context(
                request,
                **page_context(request),
                title=title,
                status_code=exc.status_code,
                message=exc.detail if isinstance(exc.detail, str) else "请联系管理员。",
            ),
            status_code=exc.status_code,
        )
    return HTMLResponse(content=str(exc.detail), status_code=exc.status_code)


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception) -> HTMLResponse:
    return templates.TemplateResponse(
        "error.html",
        template_context(
            request,
            **page_context(request),
            title="服务异常",
            status_code=500,
            message="服务暂时不可用，请稍后重试。",
        ),
        status_code=500,
    )


def log_admin_action(
    session: Session,
    *,
    actor_email: str,
    action: str,
    target: str = "",
    amount_cents: int = 0,
    note: str = "",
) -> None:
    session.add(AdminAudit(
        actor_email=actor_email,
        action=action,
        target=target,
        amount_cents=amount_cents,
        note=note,
    ))


def _run_with_timeout(fn, timeout_seconds: int):
    future = executor.submit(fn)
    return future.result(timeout=timeout_seconds)


def _smtp_ready() -> bool:
    return bool(settings.smtp_host and settings.smtp_sender)


def _send_email(to_address: str, subject: str, content: str) -> None:
    if not _smtp_ready():
        raise RuntimeError("SMTP not configured")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_sender
    message["To"] = to_address
    message.set_content(content)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(message)


def _create_email_token(session: Session, user: User, purpose: str, expires_in_minutes: int = 30) -> EmailToken:
    token = secrets.token_urlsafe(24)
    expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
    email_token = EmailToken(
        user_id=user.id,
        token=token,
        purpose=purpose,
        expires_at=expires_at,
    )
    session.add(email_token)
    session.commit()
    session.refresh(email_token)
    return email_token


def money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def page_context(request: Request, session: Session | None = None) -> dict:
    user = current_user(request, session) if session else None
    wallet = billing_service.ensure_wallet(session, user.id) if user and session else None
    providers = payment_service.available_providers()
    return {
        "user": user,
        "wallet": wallet,
        "providers": providers,
        "payments_enabled": bool(providers),
        "review_price": money(settings.review_price_per_1k_chars_cents),
        "rewrite_price": money(settings.rewrite_price_per_1k_chars_cents),
        "internal_bonus_yuan": settings.internal_bonus_cents / 100 if settings.internal_bonus_cents else 0,
        "max_upload_mb": settings.max_upload_mb,
        "is_admin": is_admin_user(user),
        "is_super_admin": is_super_admin_user(user),
        "email_verified": bool(user.email_verified) if user else False,
    }


def render_feature(
    request: Request,
    session: Session,
    *,
    title: str,
    subtitle: str,
    badge: str,
    steps: list[str],
    fields: list[dict],
) -> HTMLResponse:
    context = template_context(
        request,
        **page_context(request, session),
        title=title,
        subtitle=subtitle,
        badge=badge,
        steps=steps,
        fields=fields,
    )
    return templates.TemplateResponse("feature_page.html", context)


@app.get('/', response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    runs = []
    user = current_user(request, session)
    if user:
        runs = session.query(AnalysisRun).filter(AnalysisRun.user_id == user.id).order_by(AnalysisRun.created_at.desc()).limit(8).all()
    context = template_context(
        request,
        **page_context(request, session),
        runs=runs,
        profiles=model_router.list_profiles(),
    )
    return templates.TemplateResponse('home.html', context)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get('/audit', response_class=HTMLResponse)
def audit_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    context = template_context(
        request,
        **page_context(request, session),
        title="AI 审稿",
        profiles=model_router.list_profiles(),
    )
    return templates.TemplateResponse('audit.html', context)


@app.get('/review', response_class=HTMLResponse)
def rewrite_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    context = template_context(
        request,
        **page_context(request, session),
        title="降重降 AI",
        profiles=model_router.list_profiles(),
    )
    return templates.TemplateResponse('rewrite_page.html', context)


@app.get('/aigc-detect', response_class=HTMLResponse)
def aigc_detect_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="AIGC 检测",
        subtitle="检测文本的 AI 生成特征，输出可信度与风险提示。",
        badge="内测开放",
        steps=["上传文本", "设置阈值", "生成检测", "查看报告"],
        fields=[
            {"label": "检测内容", "kind": "textarea", "placeholder": "粘贴文本或上传文件", "hint": "支持 2 万字以内"},
            {"label": "学科领域", "kind": "select", "options": ["教育学", "计算机", "经济学", "其他"]},
            {"label": "风险阈值", "kind": "select", "options": ["严格", "标准", "宽松"]},
        ],
    )


@app.get('/literature', response_class=HTMLResponse)
def literature_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="文献综述",
        subtitle="结构化生成综述提纲与段落草稿，保留引用接口。",
        badge="设计中",
        steps=["确定主题", "选择语料", "生成提纲", "输出综述"],
        fields=[
            {"label": "研究主题", "kind": "input", "placeholder": "例如：AI 在教育评价中的应用"},
            {"label": "关键词", "kind": "input", "placeholder": "逗号分隔"},
            {"label": "引用风格", "kind": "select", "options": ["GB/T 7714", "APA", "MLA"]},
        ],
    )


@app.get('/proposal', response_class=HTMLResponse)
def proposal_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="开题报告",
        subtitle="分步骤输出研究问题、方法与可行性分析。",
        badge="设计中",
        steps=["填写题目", "研究背景", "研究方法", "输出方案"],
        fields=[
            {"label": "课题名称", "kind": "input", "placeholder": "填写研究标题"},
            {"label": "研究方向", "kind": "select", "options": ["理论研究", "应用研究", "实证研究"]},
            {"label": "补充说明", "kind": "textarea", "placeholder": "已有材料或约束条件"},
        ],
    )


@app.get('/generate', response_class=HTMLResponse)
def generate_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="文章生成",
        subtitle="按标题与结构大纲生成初稿，保留后续编辑接口。",
        badge="规划中",
        steps=["输入标题", "选择风格", "生成大纲", "输出初稿"],
        fields=[
            {"label": "标题", "kind": "input", "placeholder": "例如：数字化教育治理研究"},
            {"label": "写作风格", "kind": "select", "options": ["学术", "报告", "科普"]},
            {"label": "写作要求", "kind": "textarea", "placeholder": "字数、语气、结构要求"},
        ],
    )


@app.get('/format', response_class=HTMLResponse)
def format_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="格式调整",
        subtitle="统一标题层级、图表编号与参考文献格式。",
        badge="规划中",
        steps=["上传文件", "选择模板", "预览调整", "导出结果"],
        fields=[
            {"label": "文件", "kind": "input", "placeholder": "支持 .docx"},
            {"label": "格式模板", "kind": "select", "options": ["学校模板", "期刊模板", "自定义"]},
            {"label": "备注", "kind": "textarea", "placeholder": "特殊要求"},
        ],
    )


@app.get('/editor', response_class=HTMLResponse)
def editor_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="AI 编辑器",
        subtitle="在同一界面完成写作、润色与引用管理。",
        badge="规划中",
        steps=["创建文档", "实时编辑", "智能建议", "版本管理"],
        fields=[
            {"label": "文档名称", "kind": "input", "placeholder": "新的研究草稿"},
            {"label": "编辑模式", "kind": "select", "options": ["轻量", "标准", "深度"]},
            {"label": "描述", "kind": "textarea", "placeholder": "此文档用途与目标"},
        ],
    )


@app.get('/ppt', response_class=HTMLResponse)
def ppt_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return render_feature(
        request,
        session,
        title="AI PPT",
        subtitle="自动生成演示大纲与关键页提示。",
        badge="规划中",
        steps=["输入主题", "选择模板", "生成大纲", "导出 PPT"],
        fields=[
            {"label": "演示主题", "kind": "input", "placeholder": "例如：数字化转型成果汇报"},
            {"label": "页面数量", "kind": "select", "options": ["10 页", "15 页", "20 页"]},
            {"label": "要点提示", "kind": "textarea", "placeholder": "需要强调的数据或结论"},
        ],
    )


@app.get('/assets', response_class=HTMLResponse)
def assets_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    user = require_user(request, session)
    page = max(1, int(request.query_params.get("page", "1")))
    query = request.query_params.get("q", "").strip()
    page_size = 12
    base_query = session.query(DocumentRecord).filter(DocumentRecord.user_id == user.id)
    if query:
        base_query = base_query.filter(DocumentRecord.title.like(f"%{query}%"))
    total = base_query.count()
    total_pages = max(1, ceil(total / page_size)) if total else 1
    docs = (
        base_query
        .order_by(DocumentRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return templates.TemplateResponse(
        "assets.html",
        template_context(
            request,
            **page_context(request, session),
            title="我的资产",
            documents=docs,
            page=page,
            total_pages=total_pages,
            query=query,
        ),
    )


@app.get('/history', response_class=HTMLResponse)
def history_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    user = require_user(request, session)
    page = max(1, int(request.query_params.get("page", "1")))
    kind_filter = request.query_params.get("kind", "all")
    model_filter = request.query_params.get("model", "all")
    page_size = 15
    base_query = session.query(AnalysisRun).filter(AnalysisRun.user_id == user.id)
    if kind_filter in {"review", "rewrite"}:
        base_query = base_query.filter(AnalysisRun.kind == kind_filter)
    if model_filter and model_filter != "all":
        base_query = base_query.filter(AnalysisRun.model_alias == model_filter)
    total = base_query.count()
    total_pages = max(1, ceil(total / page_size)) if total else 1
    runs = (
        base_query
        .order_by(AnalysisRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    models = [
        row[0]
        for row in session.query(AnalysisRun.model_alias)
        .filter(AnalysisRun.user_id == user.id)
        .distinct()
        .order_by(AnalysisRun.model_alias.asc())
        .all()
    ]
    return templates.TemplateResponse(
        "history.html",
        template_context(
            request,
            **page_context(request, session),
            title="历史任务",
            runs=runs,
            page=page,
            total_pages=total_pages,
            models=models,
            kind_filter=kind_filter,
            model_filter=model_filter,
        ),
    )


@app.get('/history/export')
def export_history(request: Request, session: Session = Depends(get_session)) -> Response:
    user = require_user(request, session)
    kind_filter = request.query_params.get("kind", "all")
    model_filter = request.query_params.get("model", "all")
    base_query = session.query(AnalysisRun).filter(AnalysisRun.user_id == user.id)
    if kind_filter in {"review", "rewrite"}:
        base_query = base_query.filter(AnalysisRun.kind == kind_filter)
    if model_filter and model_filter != "all":
        base_query = base_query.filter(AnalysisRun.model_alias == model_filter)
    runs = base_query.order_by(AnalysisRun.created_at.desc()).limit(500).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["run_no", "kind", "model_alias", "provider", "total_price_yuan", "billed_chars", "created_at"])
    for run in runs:
        writer.writerow([
            run.run_no,
            run.kind,
            run.model_alias,
            run.provider_name,
            f"{run.total_price_cents / 100:.2f}",
            run.billed_chars,
            run.created_at.strftime("%Y-%m-%d %H:%M"),
        ])
    filename = f"history_{user.id}.csv"
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get('/privacy', response_class=HTMLResponse)
def privacy_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return templates.TemplateResponse(
        "legal.html",
        template_context(
            request,
            **page_context(request, session),
            title="隐私政策",
            summary="说明数据收集范围、用途与保护措施。",
            sections=[
                {
                    "title": "信息收集范围",
                    "items": [
                        "账号信息：邮箱、姓名等注册信息。",
                        "使用数据：访问时间、设备信息、必要日志。",
                        "文稿内容：用户主动上传或输入的文本与文件。",
                    ],
                },
                {
                    "title": "使用目的",
                    "items": [
                        "提供审稿与优化服务并生成结果。",
                        "计费与账户管理。",
                        "改进服务质量与安全。",
                    ],
                },
                {
                    "title": "保存与保护",
                    "items": [
                        "数据仅在提供服务所需范围内保存。",
                        "采用访问控制、最小权限与日志审计。",
                        "用户可申请删除账号与历史记录。",
                    ],
                },
                {
                    "title": "共享与披露",
                    "items": [
                        "未经同意不会对外披露内容。",
                        "法律法规或监管要求除外。",
                    ],
                },
                {
                    "title": "联系我们",
                    "items": [
                        "如需删除数据或咨询，请联系管理员。",
                    ],
                },
            ],
        ),
    )


@app.get('/terms', response_class=HTMLResponse)
def terms_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return templates.TemplateResponse(
        "legal.html",
        template_context(
            request,
            **page_context(request, session),
            title="用户协议",
            summary="明确服务范围、责任边界与使用规范。",
            sections=[
                {
                    "title": "服务范围",
                    "items": [
                        "提供文稿审稿与原创性优化等服务。",
                        "部分功能处于内测阶段，可能调整或下线。",
                    ],
                },
                {
                    "title": "用户义务",
                    "items": [
                        "不得上传违法违规或侵权内容。",
                        "需保证提交内容的合法使用权。",
                    ],
                },
                {
                    "title": "结果说明",
                    "items": [
                        "AI 结果仅供参考，用户需自行判断与修改。",
                        "平台不对最终发表或学术结果负责。",
                    ],
                },
                {
                    "title": "计费与退款",
                    "items": [
                        "计费规则以页面展示为准。",
                        "内测阶段可能提供免费额度或活动。",
                    ],
                },
                {
                    "title": "协议变更",
                    "items": [
                        "如有调整将以站内公告或页面更新为准。",
                    ],
                },
            ],
        ),
    )


@app.get('/admin', response_class=HTMLResponse)
def admin_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    user = require_admin(request, session)
    configs = session.query(ModelConfig).order_by(ModelConfig.alias.asc()).all()
    config_map = {config.alias: config for config in configs}
    profiles = model_router.list_profiles()
    model_rows = []
    for profile in profiles:
        config = config_map.get(profile.alias)
        model_rows.append(
            {
                "alias": profile.alias,
                "provider_name": profile.provider_name,
                "model": profile.model,
                "base_url": profile.base_url,
                "enabled": profile.enabled,
                "api_key_set": bool(profile.api_key),
                "source": "db" if config else "env",
                "config_enabled": bool(config.enabled) if config else None,
            }
        )

    edit_alias = request.query_params.get("alias")
    edit_model = None
    if edit_alias:
        config = config_map.get(edit_alias)
        if config:
            edit_model = {
                "alias": config.alias,
                "provider_name": config.provider_name,
                "model": config.model,
                "base_url": config.base_url,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "enabled": bool(config.enabled),
                "description": config.description,
            }
        else:
            matched = next((item for item in profiles if item.alias == edit_alias), None)
            if matched:
                edit_model = {
                    "alias": matched.alias,
                    "provider_name": matched.provider_name,
                    "model": matched.model,
                    "base_url": matched.base_url,
                    "temperature": matched.temperature,
                    "max_tokens": matched.max_tokens,
                    "enabled": matched.enabled,
                    "description": matched.description,
                }

    query = request.query_params.get("q", "").strip()
    user_query = session.query(User)
    if query:
        like = f"%{query}%"
        user_query = user_query.filter(or_(User.email.like(like), User.name.like(like)))
    users = user_query.order_by(User.created_at.desc()).limit(50).all()
    user_rows = []
    for item in users:
        wallet = billing_service.ensure_wallet(session, item.id)
        user_rows.append(
            {
                "id": item.id,
                "email": item.email,
                "name": item.name,
                "balance_cents": wallet.balance_cents,
            }
        )

    presets = [
        {"name": "Qwen（通义）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
        {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4"},
        {"name": "Moonshot（Kimi）", "base_url": "https://api.moonshot.cn/v1"},
        {"name": "Baichuan", "base_url": "https://api.baichuan-ai.com/v1"},
        {"name": "01.AI（Yi）", "base_url": "https://api.lingyiwanwu.com/v1"},
    ]
    logs = session.query(ModelCallLog).order_by(ModelCallLog.created_at.desc()).limit(50).all()
    audits = session.query(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(50).all()
    since = datetime.utcnow() - timedelta(hours=24)
    success_24h = session.query(ModelCallLog).filter(ModelCallLog.created_at >= since, ModelCallLog.success == True).count()
    fail_24h = session.query(ModelCallLog).filter(ModelCallLog.created_at >= since, ModelCallLog.success == False).count()
    total_users = session.query(func.count(User.id)).scalar() or 0
    total_runs = session.query(func.count(AnalysisRun.id)).scalar() or 0
    total_revenue_cents = session.query(func.coalesce(func.sum(AnalysisRun.total_price_cents), 0)).scalar() or 0
    revenue_24h_cents = (
        session.query(func.coalesce(func.sum(AnalysisRun.total_price_cents), 0))
        .filter(AnalysisRun.created_at >= since)
        .scalar()
        or 0
    )
    avg_latency_24h = (
        session.query(func.avg(ModelCallLog.latency_ms))
        .filter(ModelCallLog.created_at >= since, ModelCallLog.success == True)
        .scalar()
        or 0
    )
    avg_latency_all = session.query(func.avg(ModelCallLog.latency_ms)).scalar() or 0

    return templates.TemplateResponse(
        "admin.html",
        template_context(
            request,
            **page_context(request, session),
            title="管理台",
            admin_user=user,
            model_rows=model_rows,
            edit_model=edit_model,
            users=user_rows,
            user_query=query,
            presets=presets,
            logs=logs,
            audits=audits,
            success_24h=success_24h,
            fail_24h=fail_24h,
            total_users=total_users,
            total_runs=total_runs,
            total_revenue=total_revenue_cents / 100,
            revenue_24h=revenue_24h_cents / 100,
            avg_latency_24h=int(avg_latency_24h),
            avg_latency_all=int(avg_latency_all),
            error=request.query_params.get("error"),
            notice=request.query_params.get("notice"),
        ),
    )


@app.get('/contact', response_class=HTMLResponse)
def contact_page(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return templates.TemplateResponse(
        "contact.html",
        template_context(
            request,
            **page_context(request, session),
            title="联系与支持",
            summary="内测期间如需开通额度、反馈问题或获取资料，请联系管理员。",
        ),
    )


@app.post('/admin/models/save')
def admin_save_model(
    request: Request,
    alias: str = Form(...),
    provider_name: str = Form("custom"),
    model: str = Form(...),
    base_url: str = Form(...),
    api_key: str = Form(""),
    temperature: float = Form(0.2),
    max_tokens: int = Form(2000),
    enabled: str | None = Form(None),
    description: str = Form(""),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_super_admin(request, session)
    alias = alias.strip()
    if not alias or not model.strip() or not base_url.strip():
        return redirect_admin("请填写别名、模型与 Base URL。", kind="error")

    config = session.query(ModelConfig).filter(ModelConfig.alias == alias).one_or_none()
    if config is None:
        config = ModelConfig(
            alias=alias,
            provider_name=provider_name.strip() or "custom",
            model=model.strip(),
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            temperature=temperature,
            max_tokens=max_tokens,
            enabled=1 if enabled else 0,
            description=description.strip(),
        )
        session.add(config)
    else:
        config.provider_name = provider_name.strip() or config.provider_name
        config.model = model.strip()
        config.base_url = base_url.strip()
        if api_key.strip():
            config.api_key = api_key.strip()
        config.temperature = temperature
        config.max_tokens = max_tokens
        config.enabled = 1 if enabled else 0
        config.description = description.strip()
        session.add(config)
    log_admin_action(
        session,
        actor_email=user.email,
        action="model_save",
        target=alias,
    )
    session.commit()
    return redirect_admin("模型配置已保存。")


@app.post('/admin/models/toggle')
def admin_toggle_model(
    request: Request,
    alias: str = Form(...),
    enabled: int = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_admin(request, session)
    config = session.query(ModelConfig).filter(ModelConfig.alias == alias).one_or_none()
    if config is None:
        return redirect_admin("仅支持切换已保存的自定义配置。", kind="error")
    config.enabled = 1 if enabled else 0
    session.add(config)
    log_admin_action(
        session,
        actor_email=user.email,
        action="model_toggle",
        target=alias,
        note="enabled" if enabled else "disabled",
    )
    session.commit()
    return redirect_admin("模型状态已更新。")


@app.post('/admin/models/reset')
def admin_reset_model(
    request: Request,
    alias: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_super_admin(request, session)
    config = session.query(ModelConfig).filter(ModelConfig.alias == alias).one_or_none()
    if config:
        session.delete(config)
        log_admin_action(
            session,
            actor_email=user.email,
            action="model_reset",
            target=alias,
        )
        session.commit()
        return redirect_admin("已移除自定义配置，将回退环境变量。")
    return redirect_admin("未找到该配置。", kind="error")


@app.post('/admin/wallets/adjust')
def admin_adjust_wallet(
    request: Request,
    email: str = Form(...),
    amount_yuan: float = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_super_admin(request, session)
    target = session.query(User).filter(User.email == email.strip()).one_or_none()
    if not target:
        return redirect_admin("未找到该邮箱用户。", kind="error")
    amount_cents = int(amount_yuan * 100)
    if amount_cents == 0:
        return redirect_admin("调整金额不能为 0。", kind="error")
    wallet = billing_service.ensure_wallet(session, target.id)
    note = reason.strip() or "管理员调整"
    if amount_cents > 0:
        billing_service.credit_wallet(session, wallet, amount_cents, note)
        log_admin_action(
            session,
            actor_email=user.email,
            action="wallet_add",
            target=target.email,
            amount_cents=amount_cents,
            note=note,
        )
        session.commit()
        return redirect_admin("已增加余额。")
    try:
        billing_service.spend(session, wallet, abs(amount_cents), note)
    except ValueError:
        return redirect_admin("余额不足，无法扣减。", kind="error")
    log_admin_action(
        session,
        actor_email=user.email,
        action="wallet_deduct",
        target=target.email,
        amount_cents=amount_cents,
        note=note,
    )
    session.commit()
    return redirect_admin("已扣减余额。")


@app.post('/admin/users/reset_password')
def admin_reset_password(
    request: Request,
    email: str = Form(...),
    new_password: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_super_admin(request, session)
    target = session.query(User).filter(User.email == email.strip()).one_or_none()
    if not target:
        return redirect_admin("未找到该邮箱用户。", kind="error")
    if len(new_password.strip()) < 6:
        return redirect_admin("新密码至少 6 位。", kind="error")
    target.password_hash = hash_password(new_password.strip())
    session.add(target)
    log_admin_action(
        session,
        actor_email=user.email,
        action="password_reset",
        target=target.email,
    )
    session.commit()
    return redirect_admin("密码已重置。")


@app.post('/admin/models/test')
def admin_test_model(
    request: Request,
    alias: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_admin(request, session)
    profile = model_router.resolve(alias)
    if not profile:
        return redirect_admin("未找到该模型配置。", kind="error")
    if not profile.enabled:
        return redirect_admin("模型未启用或未配置 Key。", kind="error")
    start = time.perf_counter()
    try:
        model_router.complete_text(
            alias,
            system_prompt="You are a health checker.",
            user_prompt="Reply with OK.",
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        short_err = str(exc).strip()
        if len(short_err) > 160:
            short_err = short_err[:160] + "..."
        log_admin_action(
            session,
            actor_email=user.email,
            action="model_test_fail",
            target=alias,
            note=short_err,
        )
        session.commit()
        detail = f"模型联通失败：{short_err}" if short_err else "模型联通失败，请检查 Key/Base URL。"
        return redirect_admin(detail, kind="error")
    log_admin_action(
        session,
        actor_email=user.email,
        action="model_test_ok",
        target=alias,
        note=f"{latency_ms}ms",
    )
    session.commit()
    return redirect_admin(f"模型联通成功，耗时 {latency_ms}ms。")


@app.get('/admin/users/export')
def export_users(request: Request, session: Session = Depends(get_session)) -> Response:
    require_super_admin(request, session)
    query = request.query_params.get("q", "").strip()
    user_query = session.query(User)
    if query:
        like = f"%{query}%"
        user_query = user_query.filter(or_(User.email.like(like), User.name.like(like)))
    users = user_query.order_by(User.created_at.desc()).limit(2000).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "email", "name", "balance_yuan", "created_at"])
    for user in users:
        wallet = session.query(WalletAccount).filter(WalletAccount.user_id == user.id).one_or_none()
        balance = wallet.balance_cents if wallet else 0
        writer.writerow([
            user.id,
            user.email,
            user.name,
            f"{balance / 100:.2f}",
            user.created_at.strftime("%Y-%m-%d %H:%M"),
        ])
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"},
    )


@app.get('/admin/logs/export')
def export_model_logs(request: Request, session: Session = Depends(get_session)) -> Response:
    require_admin(request, session)
    logs = session.query(ModelCallLog).order_by(ModelCallLog.created_at.desc()).limit(1000).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at", "alias", "provider", "model", "success", "latency_ms", "error_message"])
    for item in logs:
        writer.writerow([
            item.created_at.strftime("%Y-%m-%d %H:%M"),
            item.alias,
            item.provider_name,
            item.model,
            "yes" if item.success else "no",
            item.latency_ms,
            item.error_message or "",
        ])
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=model_logs.csv"},
    )


@app.get('/admin/audits/export')
def export_admin_audits(request: Request, session: Session = Depends(get_session)) -> Response:
    require_super_admin(request, session)
    audits = session.query(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(1000).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["created_at", "actor_email", "action", "target", "amount_yuan", "note"])
    for item in audits:
        writer.writerow([
            item.created_at.strftime("%Y-%m-%d %H:%M"),
            item.actor_email,
            item.action,
            item.target,
            f"{item.amount_cents / 100:.2f}",
            item.note,
        ])
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=admin_audits.csv"},
    )


@app.post('/bonus/claim')
def claim_bonus(
    request: Request,
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_user(request, session)
    if settings.internal_bonus_cents <= 0:
        return redirect_notice("内测赠送已关闭")
    existing = session.query(BonusClaim).filter(BonusClaim.user_id == user.id).one_or_none()
    if existing:
        return redirect_notice("已领取过内测额度")
    wallet = billing_service.ensure_wallet(session, user.id)
    billing_service.credit_wallet(session, wallet, settings.internal_bonus_cents, "内测领取")
    session.add(BonusClaim(user_id=user.id, amount_cents=settings.internal_bonus_cents))
    session.commit()
    return redirect_notice("内测额度领取成功")

@app.get('/register', response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse('register.html', template_context(request, error=None))


@app.post('/register', response_class=HTMLResponse)
def register(
    request: Request,
    email: str = Form(...),
    name: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    existing = session.query(User).filter(User.email == email).one_or_none()
    if existing:
        return templates.TemplateResponse(
            "register.html",
            template_context(request, error="该邮箱已注册。"),
            status_code=400,
        )
    user = User(email=email, name=name, password_hash=hash_password(password), email_verified=False)
    session.add(user)
    session.commit()
    session.refresh(user)
    wallet = billing_service.ensure_wallet(session, user.id)
    if settings.signup_bonus_cents > 0:
        billing_service.credit_wallet(session, wallet, settings.signup_bonus_cents, "注册赠送")
    request.session.clear()
    request.session['user_id'] = user.id
    if _smtp_ready():
        token = _create_email_token(session, user, "verify", expires_in_minutes=60)
        verify_url = f"{settings.app_base_url}/verify/{token.token}"
        try:
            _send_email(
                user.email,
                "请验证您的邮箱",
                f"请点击以下链接完成邮箱验证：{verify_url}",
            )
            return RedirectResponse('/?notice=已发送验证邮件', status_code=303)
        except Exception:
            pass
    return RedirectResponse('/', status_code=303)


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse('login.html', template_context(request, error=None))


@app.post('/login', response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    user = session.query(User).filter(User.email == email).one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            template_context(request, error="邮箱或密码错误。"),
            status_code=400,
        )
    request.session.clear()
    request.session['user_id'] = user.id
    if not user.email_verified:
        return RedirectResponse('/?notice=邮箱未验证，可在个人中心重新发送', status_code=303)
    return RedirectResponse('/', status_code=303)


@app.post('/logout')
def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse('/', status_code=303)


@app.get('/verify/{token}', response_class=HTMLResponse)
def verify_email(token: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    email_token = session.query(EmailToken).filter(EmailToken.token == token, EmailToken.purpose == "verify").one_or_none()
    if not email_token or email_token.used_at or email_token.expires_at < datetime.utcnow():
        return templates.TemplateResponse(
            "verify_result.html",
            template_context(request, title="验证失败", message="验证链接无效或已过期。"),
            status_code=400,
        )
    user = session.query(User).filter(User.id == email_token.user_id).one()
    user.email_verified = True
    email_token.used_at = datetime.utcnow()
    session.add(user)
    session.add(email_token)
    session.commit()
    return templates.TemplateResponse(
        "verify_result.html",
        template_context(request, title="验证成功", message="邮箱已验证，可正常使用全部功能。"),
    )


@app.post('/verify/resend')
def resend_verification(request: Request, csrf_token: str = Form(...), session: Session = Depends(get_session)) -> RedirectResponse:
    verify_csrf(request, csrf_token)
    user = require_user(request, session)
    if user.email_verified:
        return redirect_notice("邮箱已验证")
    if not _smtp_ready():
        return redirect_notice("邮件服务未配置，请联系管理员")
    token = _create_email_token(session, user, "verify", expires_in_minutes=60)
    verify_url = f"{settings.app_base_url}/verify/{token.token}"
    try:
        _send_email(
            user.email,
            "请验证您的邮箱",
            f"请点击以下链接完成邮箱验证：{verify_url}",
        )
    except Exception:
        return redirect_notice("发送失败，请稍后再试")
    return redirect_notice("验证邮件已发送")


@app.get('/password/forgot', response_class=HTMLResponse)
def forgot_password_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("password_forgot.html", template_context(request, error=None))


@app.post('/password/forgot', response_class=HTMLResponse)
def forgot_password(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    verify_csrf(request, csrf_token)
    user = session.query(User).filter(User.email == email.strip()).one_or_none()
    if not user:
        return templates.TemplateResponse(
            "password_forgot.html",
            template_context(request, error="邮箱不存在。"),
            status_code=400,
        )
    if not _smtp_ready():
        return templates.TemplateResponse(
            "password_forgot.html",
            template_context(request, error="邮件服务未配置，请联系管理员。"),
            status_code=400,
        )
    token = _create_email_token(session, user, "reset", expires_in_minutes=30)
    reset_url = f"{settings.app_base_url}/password/reset/{token.token}"
    try:
        _send_email(
            user.email,
            "密码重置",
            f"请点击以下链接重置密码（30 分钟内有效）：{reset_url}",
        )
    except Exception:
        return templates.TemplateResponse(
            "password_forgot.html",
            template_context(request, error="发送失败，请稍后再试。"),
            status_code=400,
        )
    return templates.TemplateResponse(
        "password_forgot.html",
        template_context(request, error=None, notice="重置邮件已发送，请检查邮箱。"),
    )


@app.get('/password/reset/{token}', response_class=HTMLResponse)
def reset_password_page(token: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    email_token = session.query(EmailToken).filter(EmailToken.token == token, EmailToken.purpose == "reset").one_or_none()
    if not email_token or email_token.used_at or email_token.expires_at < datetime.utcnow():
        return templates.TemplateResponse(
            "verify_result.html",
            template_context(request, title="链接无效", message="重置链接无效或已过期。"),
            status_code=400,
        )
    return templates.TemplateResponse("password_reset.html", template_context(request, token=token, error=None))


@app.post('/password/reset')
def reset_password(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    email_token = session.query(EmailToken).filter(EmailToken.token == token, EmailToken.purpose == "reset").one_or_none()
    if not email_token or email_token.used_at or email_token.expires_at < datetime.utcnow():
        return templates.TemplateResponse(
            "password_reset.html",
            template_context(request, token=token, error="链接无效或已过期。"),
            status_code=400,
        )
    if len(password.strip()) < 6:
        return templates.TemplateResponse(
            "password_reset.html",
            template_context(request, token=token, error="密码至少 6 位。"),
            status_code=400,
        )
    user = session.query(User).filter(User.id == email_token.user_id).one()
    user.password_hash = hash_password(password.strip())
    email_token.used_at = datetime.utcnow()
    session.add(user)
    session.add(email_token)
    session.commit()
    return templates.TemplateResponse(
        "verify_result.html",
        template_context(request, title="密码已重置", message="请使用新密码登录。"),
    )


def _load_source(title: str, text: str, upload: UploadFile | None) -> tuple[str, str, str]:
    if upload and upload.filename:
        source_text, source_name = document_service.extract_from_upload(upload)
        return title or upload.filename.rsplit(".", 1)[0], source_text, source_name
    return title or "未命名文稿", text, "manual-input"


@app.post('/audit', response_class=HTMLResponse)
def run_review(
    request: Request,
    title: str = Form(''),
    text: str = Form(''),
    model_alias: str = Form(settings.default_review_model_alias),
    csrf_token: str = Form(...),
    upload: UploadFile | None = None,
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    user = require_user(request, session)
    try:
        title, source_text, source_name = _load_source(title, text, upload)
    except ValueError as exc:
        return redirect_error(str(exc))
    quote = pricing_service.quote('review', source_text)
    wallet = billing_service.ensure_wallet(session, user.id)
    if quote.total_price_cents <= 0:
        return redirect_error("请输入文稿内容")
    if wallet.balance_cents < quote.total_price_cents:
        return redirect_error("余额不足，请先充值")

    try:
        report = _run_with_timeout(
            lambda: review_service.generate(title, source_text, model_alias),
            settings.task_timeout_seconds,
        )
    except FutureTimeout:
        return redirect_error("处理超时，请稍后重试或缩短内容")

    billing_service.spend(session, wallet, quote.total_price_cents, f"AI审稿 {title}")
    document = DocumentRecord(user_id=user.id, title=title, source_name=source_name, text_content=source_text, char_count=quote.char_count)
    session.add(document)
    session.commit()
    session.refresh(document)
    profile = model_router.resolve(model_alias)
    fallback_notice = None
    if report.provider_name == "heuristic":
        if profile and profile.enabled:
            fallback_notice = "模型调用失败或超时，已降级为本地审稿。"
        else:
            fallback_notice = "模型未配置，已使用本地审稿。"
    run = AnalysisRun(
        run_no=str(uuid4()),
        user_id=user.id,
        document_id=document.id,
        kind='review',
        billed_chars=quote.char_count,
        unit_price_cents=quote.unit_price_cents,
        total_price_cents=quote.total_price_cents,
        model_alias=model_alias,
        provider_name=report.provider_name,
        result_json=json.dumps(report.to_dict(), ensure_ascii=False),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return templates.TemplateResponse(
        'review_result.html',
        template_context(
            request,
            user=user,
            wallet=billing_service.ensure_wallet(session, user.id),
            run=run,
            report=report,
            fallback_notice=fallback_notice,
        ),
    )


@app.post('/review', response_class=HTMLResponse)
def run_rewrite(
    request: Request,
    title: str = Form(''),
    text: str = Form(''),
    mode: str = Form('standard'),
    model_alias: str = Form(settings.default_rewrite_model_alias),
    csrf_token: str = Form(...),
    upload: UploadFile | None = None,
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    user = require_user(request, session)
    try:
        title, source_text, source_name = _load_source(title, text, upload)
    except ValueError as exc:
        return redirect_error(str(exc))
    quote = pricing_service.quote('rewrite', source_text)
    wallet = billing_service.ensure_wallet(session, user.id)
    if quote.total_price_cents <= 0:
        return redirect_error("请输入文稿内容")
    if wallet.balance_cents < quote.total_price_cents:
        return redirect_error("余额不足，请先充值")

    try:
        result = _run_with_timeout(
            lambda: rewrite_service.optimize(title, source_text, mode, model_alias),
            settings.task_timeout_seconds,
        )
    except FutureTimeout:
        return redirect_error("处理超时，请稍后重试或缩短内容")

    billing_service.spend(session, wallet, quote.total_price_cents, f"原创性优化 {title}")
    document = DocumentRecord(user_id=user.id, title=title, source_name=source_name, text_content=source_text, char_count=quote.char_count)
    session.add(document)
    session.commit()
    session.refresh(document)
    profile = model_router.resolve(model_alias)
    fallback_notice = None
    if result.provider_name == "heuristic":
        if profile and profile.enabled:
            fallback_notice = "模型调用失败或超时，已降级为本地优化。"
        else:
            fallback_notice = "模型未配置，已使用本地优化。"
    run = AnalysisRun(
        run_no=str(uuid4()),
        user_id=user.id,
        document_id=document.id,
        kind='rewrite',
        billed_chars=quote.char_count,
        unit_price_cents=quote.unit_price_cents,
        total_price_cents=quote.total_price_cents,
        model_alias=model_alias,
        provider_name=result.provider_name,
        result_json=json.dumps(result.to_dict(), ensure_ascii=False),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return templates.TemplateResponse(
        'rewrite_result.html',
        template_context(
            request,
            user=user,
            wallet=billing_service.ensure_wallet(session, user.id),
            run=run,
            result=result,
            fallback_notice=fallback_notice,
        ),
    )


@app.post('/payments/orders')
def create_payment_order(
    request: Request,
    amount_yuan: int = Form(...),
    provider: str = Form('mock'),
    channel: str = Form('web'),
    csrf_token: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    verify_csrf(request, csrf_token)
    if not (settings.enable_payments or settings.enable_mock_topup):
        return redirect_error("充值暂未开放")
    user = require_user(request, session)
    amount_cents = amount_yuan * 100
    try:
        order, creation = payment_service.create_order(session, user.id, amount_cents, provider, channel)
    except RuntimeError as exc:
        return redirect_error(str(exc))
    return templates.TemplateResponse(
        'payment_created.html',
        template_context(
            request,
            user=user,
            order=order,
            creation=creation,
        ),
    )


@app.get('/payments/mock/{order_no}/pay')
def mock_pay(order_no: str, session: Session = Depends(get_session)) -> RedirectResponse:
    if not settings.enable_mock_topup:
        raise HTTPException(status_code=404)
    payment_service.mark_paid(session, order_no)
    return redirect_notice("充值成功")


@app.get('/payments/return')
def payment_return(out_trade_no: str | None = None, session: Session = Depends(get_session)) -> RedirectResponse:
    if not settings.enable_payments:
        raise HTTPException(status_code=404)
    if out_trade_no:
        payment_service.mark_paid(session, out_trade_no)
    return redirect_notice("支付结果已同步")


@app.post('/payments/callback/alipay')
async def alipay_callback(request: Request, session: Session = Depends(get_session)) -> Response:
    if not settings.enable_payments:
        raise HTTPException(status_code=404)
    form = dict(await request.form())
    order_no = form.get('out_trade_no')
    if order_no:
        payment_service.mark_paid(session, order_no, provider_order_id=form.get('trade_no'))
    return Response('success')


@app.post('/payments/callback/wechat')
async def wechat_callback(request: Request, session: Session = Depends(get_session)) -> Response:
    if not settings.enable_payments:
        raise HTTPException(status_code=404)
    payload = await request.body()
    try:
        data = json.loads(payload.decode('utf-8'))
        resource = data.get('resource', {})
        out_trade_no = resource.get('out_trade_no') or resource.get('attach')
        transaction_id = resource.get('transaction_id')
        if out_trade_no:
            payment_service.mark_paid(session, out_trade_no, provider_order_id=transaction_id)
    except json.JSONDecodeError:
        pass
    return Response('{"code":"SUCCESS","message":"成功"}', media_type="application/json")


@app.get('/runs/{run_no}', response_class=HTMLResponse)
def view_run(run_no: str, request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    user = require_user(request, session)
    run = session.query(AnalysisRun).filter(AnalysisRun.run_no == run_no, AnalysisRun.user_id == user.id).one()
    payload = json.loads(run.result_json)
    if run.kind == 'review':
        return templates.TemplateResponse(
            "saved_run.html",
            template_context(
                request,
                user=user,
                run=run,
                payload_text=json.dumps(payload, ensure_ascii=False, indent=2),
                kind="review",
            ),
        )
    return templates.TemplateResponse(
        "saved_run.html",
        template_context(
            request,
            user=user,
            run=run,
            payload_text=json.dumps(payload, ensure_ascii=False, indent=2),
            kind="rewrite",
        ),
    )
