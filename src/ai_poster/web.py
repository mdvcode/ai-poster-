import hashlib
import json
import secrets
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ai_poster.ai import QualityError
from ai_poster.db import post_version
from ai_poster.editorial import EXCLUSIONS, FEEDBACK, ContentRules
from ai_poster.images import ImageError, ImageGenerator
from ai_poster.telegram import TelegramError
from ai_poster.usage import UsageMeter, usage_scope

STATIC = Path(__file__).with_name("static")


def issue_login_code(store) -> str:
    code = secrets.token_hex(4).upper()
    store.set(
        "web_login",
        json.dumps(
            {
                "hash": hashlib.sha256(code.encode()).hexdigest(),
                "expires": time.time() + 600,
            }
        ),
    )
    return code


class WebAdmin:
    def __init__(self, bot):
        self.bot = bot
        self.store = bot.store
        self.images = ImageGenerator(bot.settings, bot.telegram.client, self.store)
        self.meter = UsageMeter(self.store)
        self.sessions = {}
        self.attempts = []
        port = bot.settings.web_port
        self.origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.app = Starlette(
            routes=[
                Route("/", self.index),
                Route("/assets/{name}", self.asset),
                Route("/api/login", self.login, methods=["POST"]),
                Route("/api/logout", self.logout, methods=["POST"]),
                Route("/api/state", self.state),
                Route("/api/usage", self.usage),
                Route("/api/rules", self.rules, methods=["GET", "PUT"]),
                Route("/api/posts", self.posts),
                Route("/api/images/{image_id}", self.image_file),
                Route("/api/posts/{post_id:int}", self.post, methods=["GET", "PATCH", "DELETE"]),
                Route("/api/posts/{post_id:int}/{action}", self.post_action, methods=["POST"]),
                Route("/api/control", self.control, methods=["POST"]),
                Route("/api/sources", self.add_source, methods=["POST"]),
                Route("/api/sources/{source_id:int}", self.remove_source, methods=["DELETE"]),
                Route("/api/channel", self.channel, methods=["POST"]),
            ]
        )
        self.app.add_middleware(BaseHTTPMiddleware, dispatch=self.guard)

    async def guard(self, request: Request, call_next):
        if request.headers.get("host") not in self.hosts:
            return JSONResponse({"error": "Недопустимый адрес."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if request.headers.get("x-requested-with") != "Tweebit" or request.headers.get(
                "origin", ""
            ) not in self.origins | {""}:
                return JSONResponse({"error": "Недопустимый запрос."}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "Ожидается JSON."}, status_code=415)
            if len(await request.body()) > 65536:
                return JSONResponse({"error": "Слишком большой запрос."}, status_code=413)
        if request.url.path.startswith("/api/") and request.url.path != "/api/login":
            token = request.cookies.get("tweebit_session", "")
            digest = hashlib.sha256(token.encode()).hexdigest()
            if self.sessions.get(digest, 0) <= time.time():
                return JSONResponse({"error": "Войдите с кодом из Telegram."}, status_code=401)
        try:
            response = await call_next(request)
        except (ValueError, KeyError, TypeError, IndexError):
            response = JSONResponse({"error": "Проверьте введённые данные."}, status_code=400)
        except (httpx.HTTPError, TelegramError):
            response = JSONResponse(
                {"error": "Сервис недоступен. Проверьте источник и доступ к API."}, status_code=502
            )
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'"
                ),
            }
        )
        return response

    async def index(self, request):
        return FileResponse(STATIC / "index.html")

    async def asset(self, request):
        name = request.path_params["name"]
        if name not in {"app.js", "style.css", "favicon.svg"}:
            return JSONResponse({"error": "Не найдено"}, status_code=404)
        return FileResponse(STATIC / name)

    async def login(self, request):
        now = time.time()
        self.attempts = [t for t in self.attempts if t > now - 60]
        if len(self.attempts) >= 5:
            return JSONResponse(
                {"error": "Слишком много попыток. Подождите минуту."}, status_code=429
            )
        self.attempts.append(now)
        code = (await request.json()).get("code", "")
        if not isinstance(code, str):
            raise ValueError("code")
        saved = json.loads(self.store.get("web_login", "{}"))
        digest = hashlib.sha256(code.strip().upper().encode()).hexdigest()
        if saved.get("expires", 0) <= now or not secrets.compare_digest(
            saved.get("hash", ""), digest
        ):
            return JSONResponse(
                {"error": "Код неверный или истёк. Получите новый: /web в боте."}, status_code=401
            )
        self.store.set("web_login", "{}")
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        token = secrets.token_urlsafe(32)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 12 * 3600
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "tweebit_session", token, httponly=True, samesite="strict", max_age=12 * 3600
        )
        return response

    async def logout(self, request):
        token = request.cookies.get("tweebit_session", "")
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("tweebit_session")
        return response

    async def usage(self, request):
        return JSONResponse(self.meter.summary())

    async def state(self, request):
        return JSONResponse(
            {
                "counts": self.store.counts(),
                "paused": self.store.get("paused") == "1",
                "mode": self.store.get("mode"),
                "channel": self.store.get("target_label") or self.store.get("target"),
                "sources": [dict(s) for s in self.store.sources()],
                "processing_id": self.bot.worker.processing_id,
                "language": self.bot.settings.output_language,
                "images": {
                    "configured": self.images.configured,
                    "used_today": self.store.image_attempts_today(),
                    "daily_limit": self.bot.settings.image_daily_limit,
                },
                "editorial": self.rules_state(),
            }
        )

    async def posts(self, request):
        group = request.query_params.get("filter", "ready")
        groups = {
            "ready": ["ready"],
            "pending": ["pending"],
            "blocked": ["blocked", "failed", "send_failed", "uncertain"],
            "published": ["published"],
            "deleted": ["deleted", "skipped"],
            "archive": ["filtered", "duplicate", "deleted", "skipped"],
            "duplicate": ["duplicate"],
            "filtered": ["filtered"],
            "all": ["pending", "ready", "blocked", "failed", "send_failed", "uncertain"],
        }
        states = groups[group]
        page = max(0, int(request.query_params.get("page", "0")))
        query = request.query_params.get("q", "").strip()[:200]
        placeholders = ",".join("?" for _ in states)
        where = (
            f"p.state IN ({placeholders}) "
            "AND (?='' OR instr(lower(coalesce(p.draft,p.original)),lower(?))>0)"
        )
        params = (*states, query, query)
        total = self.store.db.execute(
            f"SELECT count(*) FROM posts p WHERE {where}", params
        ).fetchone()[0]
        rows = self.store.db.execute(
            f"""SELECT p.id,p.state,p.draft,p.original,p.reason,p.created,
                p.published_at,p.edited_by_owner,p.editorial_score,p.editorial_override,s.handle,s.kind
                FROM posts p JOIN sources s ON s.id=p.source_id WHERE {where}
                ORDER BY p.id DESC LIMIT 20 OFFSET ?""",
            (*params, page * 20),
        ).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            item["preview"] = (r["draft"] or r["original"])[:240]
            del item["draft"], item["original"]
            items.append(item)
        return JSONResponse({"items": items, "total": total, "page": page})

    def detail(self, post):
        data = dict(post)
        data["version"] = post_version(post)
        data["body"] = post["draft"] or ""
        data["images"] = self.store.images_for(post["id"])
        data["stale"] = self.store.stale(post)
        data["usage"] = self.meter.post_summary(post["id"])
        data["waiting_reason"] = self.waiting_reason(post)
        data["image_busy"] = self.store.image_busy(post["id"])
        return data

    def waiting_reason(self, post):
        if post["state"] != "pending":
            return None
        if self.store.get("paused") == "1":
            return "Сбор на паузе."
        if post["next_attempt"] > time.time():
            return "Ожидает повторной попытки после ошибки API."
        rules = self.store.content_rules()
        if rules.enabled and self.store.drafts_today(rules) >= rules.daily_limit:
            return "Дневной лимит черновиков достигнут; ждёт следующего дня."
        if self.bot.worker.processing_id == post["id"]:
            return "AI пишет и проверяет этот пост."
        if not rules.enabled or post["editorial_override"]:
            return "Ожидает создания текста."
        if post["editorial_revision"] != rules.revision:
            return "Ждёт своей выборки для оценки; источники чередуются."
        return "Оценён; ждёт создания текста с учётом оценки и очередности источников."

    async def post(self, request):
        post_id = request.path_params["post_id"]
        post = self.store.post(post_id)
        if not post:
            return JSONResponse({"error": "Пост не найден."}, status_code=404)
        if request.method == "GET":
            return JSONResponse(self.detail(post))
        data = await request.json()
        async with self.bot.worker.lock:
            try:
                if request.method == "DELETE":
                    self.store.delete_post(post_id, data["version"], data.get("feedback"))
                else:
                    self.store.edit_post(post_id, data["text"], data["version"])
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(self.detail(self.store.post(post_id)))

    async def post_action(self, request):
        post_id = request.path_params["post_id"]
        action = request.path_params["action"]
        data = await request.json()
        if action == "generate-image":
            return await self.generate_image(post_id, data)
        if action == "style":
            return await self.style_post(post_id, data)
        async with self.bot.worker.lock:
            post = self.store.post(post_id)
            if not post or post_version(post) != data.get("version"):
                return JSONResponse(
                    {"error": "Пост изменился. Обновите страницу."}, status_code=409
                )
            if action == "publish":
                result = await self.bot.worker.publish(post_id, manual=True)
            elif action == "select-image":
                try:
                    self.store.select_image(post_id, data["version"], data.get("image_id"))
                except ValueError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=409)
                result = "Обложка выбрана." if data.get("image_id") else "Публикация без картинки."
            elif action == "choose":
                try:
                    self.store.choose_post(post_id, data["version"])
                except ValueError as exc:
                    return JSONResponse({"error": str(exc)}, status_code=409)
                self.bot.worker.wakeup.set()
                result = (
                    "Выбран вами. Создадим черновик при включённом сборе "
                    "в пределах дневного лимита."
                )
            elif action == "retry" and post["state"] in {"blocked", "failed", "send_failed"}:
                self.store.update_post(
                    post_id,
                    state="pending",
                    draft=None,
                    image_id=None,
                    image_candidate=None,
                    reason=None,
                    attempts=0,
                    next_attempt=0,
                    notified=0,
                )
                self.bot.worker.wakeup.set()
                result = "Пост поставлен на повторную обработку."
            else:
                return JSONResponse({"error": "Действие недоступно."}, status_code=409)
        return JSONResponse({"message": result, "post": self.detail(self.store.post(post_id))})

    async def image_file(self, request):
        image = self.store.image(request.path_params["image_id"])
        if not image:
            return JSONResponse({"error": "Изображение не найдено."}, status_code=404)
        return Response(image["content"], media_type="image/jpeg")

    async def generate_image(self, post_id, data):
        if not self.images.configured:
            return JSONResponse(
                {"error": "Добавьте FAL_KEY в .env и перезапустите сервис."}, status_code=503
            )
        direction = data.get("direction", "")
        if not isinstance(direction, str) or len(direction) > 1000:
            raise ValueError("direction")
        async with self.bot.worker.lock:
            try:
                attempt = self.store.start_image(
                    post_id, data["version"], self.bot.settings.image_daily_limit
                )
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            post = self.store.post(post_id)
        status = "failed"
        try:
            with usage_scope(post_id):
                prompt = await self.bot.worker.rewriter.image_prompt(post["draft"], direction)
                content = await self.images.generate(prompt)
            async with self.bot.worker.lock:
                current = self.store.post(post_id)
                if (
                    not current
                    or current["state"] != "ready"
                    or post_version(current) != data["version"]
                ):
                    return JSONResponse(
                        {"error": "Пост изменился во время генерации. Картинка не прикреплена."},
                        status_code=409,
                    )
                self.store.save_image(post_id, content, prompt)
                status = "succeeded"
        except ImageError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        finally:
            self.store.finish_image(attempt, status)
        return JSONResponse(
            {
                "post": self.detail(self.store.post(post_id)),
                "message": "Картинка готова. Просмотрите её и нажмите «Использовать».",
            }
        )

    async def style_post(self, post_id, data):
        post = self.store.post(post_id)
        if not post or post["state"] != "ready" or post_version(post) != data.get("version"):
            return JSONResponse({"error": "Черновик изменился или недоступен."}, status_code=409)
        try:
            with usage_scope(post_id, stage="restyle"):
                styled = await self.bot.worker.rewriter.restyle(post["draft"])
        except QualityError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        async with self.bot.worker.lock:
            try:
                self.store.edit_post(post_id, styled, data["version"])
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(
            {
                "message": "Пост оформлен. Проверьте предпросмотр.",
                "post": self.detail(self.store.post(post_id)),
            }
        )

    def rules_state(self):
        rules = self.store.content_rules()
        return {
            "rules": rules.model_dump(),
            "revision": rules.revision,
            "drafts_today": self.store.drafts_today(rules),
            "exclusions": EXCLUSIONS,
            "feedback": FEEDBACK,
        }

    async def rules(self, request):
        if request.method == "PUT":
            data = await request.json()
            rules = ContentRules.model_validate(data["rules"])
            async with self.bot.worker.lock:
                if data.get("revision") != self.store.content_rules().revision:
                    return JSONResponse(
                        {"error": "Правила изменились. Обновите страницу."}, status_code=409
                    )
                self.store.save_content_rules(rules)
                self.bot.worker.wakeup.set()
        return JSONResponse(self.rules_state())

    async def control(self, request):
        action = (await request.json())["action"]
        if action not in {"pause", "resume", "run"}:
            raise ValueError("action")
        result = await self.bot.command("/" + action, [])
        return JSONResponse({"message": result})

    async def add_source(self, request):
        data = await request.json()
        if data["kind"] not in {"telegram", "x"} or not isinstance(data["handle"], str):
            raise ValueError("source")
        return JSONResponse(
            {"message": await self.bot.command("/add", [data["kind"], data["handle"]])}
        )

    async def remove_source(self, request):
        return JSONResponse(
            {"message": await self.bot.command("/remove", [str(request.path_params["source_id"])])}
        )

    async def channel(self, request):
        value = (await request.json())["channel"]
        if not isinstance(value, str):
            raise ValueError("channel")
        return JSONResponse({"message": await self.bot.command("/channel", [value.strip()])})

    async def run(self):
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.bot.settings.web_port,
            log_level="warning",
            access_log=False,
            server_header=False,
        )
        server = uvicorn.Server(config)
        # The application owns SIGINT/SIGTERM; uvicorn must not replace those handlers.
        from contextlib import nullcontext

        server.capture_signals = nullcontext
        await server.serve()
