import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

APP_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("INTEGRATION_DB", APP_DIR / "backend" / "integration_hub.sqlite3"))
RATE_LIMIT_SECRET = os.environ.get("RATE_LIMIT_SECRET", "local-development-rate-limit-secret")
MAX_RATE_LIMIT = 120
MAX_RESPONSE_BYTES = 1_000_000
MAX_STORED_RESPONSE_BYTES = 50_000
CLIENT = "API-Connect/1.0"


PROVIDERS: dict[str, dict[str, Any]] = {
    "jsonplaceholder": {
        "name": "JSONPlaceholder",
        "description": "API REST fictícia para testar posts e usuários.",
        "category": "Demonstração",
        "icon": "braces",
        "base_url": "https://jsonplaceholder.typicode.com",
        "operations": [
            {"id": "posts.list", "name": "Listar posts", "method": "GET", "description": "Busca os posts mais recentes.", "fields": [
                {"name": "limit", "label": "Quantidade", "type": "number", "required": False, "default": 10, "min": 1, "max": 100},
                {"name": "userId", "label": "Filtrar por usuário", "type": "number", "required": False, "min": 1, "max": 10},
            ]},
            {"id": "posts.get", "name": "Consultar post", "method": "GET", "description": "Busca um post pelo identificador.", "fields": [
                {"name": "id", "label": "ID do post", "type": "number", "required": True, "min": 1, "max": 100},
            ]},
            {"id": "posts.create", "name": "Criar post de teste", "method": "POST", "description": "Envia um post de demonstração; o serviço devolve um registro simulado.", "fields": [
                {"name": "title", "label": "Título", "type": "text", "required": True, "maxLength": 120},
                {"name": "body", "label": "Conteúdo", "type": "textarea", "required": True, "maxLength": 2000},
                {"name": "userId", "label": "ID do usuário", "type": "number", "required": True, "min": 1, "max": 10, "default": 1},
            ]},
            {"id": "users.list", "name": "Listar usuários", "method": "GET", "description": "Consulta os usuários de demonstração.", "fields": []},
        ],
    },
    "open-meteo": {
        "name": "Open-Meteo",
        "description": "Geocodificação e previsão meteorológica sem chave de API.",
        "category": "Clima",
        "icon": "cloud-sun",
        "base_url": "https://geocoding-api.open-meteo.com",
        "operations": [
            {"id": "locations.search", "name": "Buscar cidade", "method": "GET", "description": "Encontra coordenadas para uma cidade.", "fields": [
                {"name": "name", "label": "Cidade", "type": "text", "required": True, "maxLength": 100},
            ]},
            {"id": "weather.current", "name": "Consultar previsão", "method": "GET", "description": "Consulta o clima atual e a previsão para três dias.", "fields": [
                {"name": "latitude", "label": "Latitude", "type": "number", "required": True, "min": -90, "max": 90, "placeholder": "-23.55"},
                {"name": "longitude", "label": "Longitude", "type": "number", "required": True, "min": -180, "max": 180, "placeholder": "-46.63"},
            ]},
        ],
    },
    "github": {
        "name": "GitHub",
        "description": "Consulta informações públicas de repositórios.",
        "category": "Desenvolvimento",
        "icon": "github",
        "base_url": "https://api.github.com",
        "operations": [
            {"id": "repositories.get", "name": "Consultar repositório", "method": "GET", "description": "Busca métricas e detalhes de um repositório público.", "fields": [
                {"name": "owner", "label": "Proprietário", "type": "text", "required": True, "maxLength": 100, "placeholder": "fastapi"},
                {"name": "repo", "label": "Repositório", "type": "text", "required": True, "maxLength": 100, "placeholder": "fastapi"},
            ]},
        ],
    },
}


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime | None = None) -> str:
    return (value or now_utc()).isoformat(timespec="milliseconds")


def initialize_database() -> None:
    with database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_settings (
                provider TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                rate_limit INTEGER NOT NULL DEFAULT 60 CHECK (rate_limit BETWEEN 1 AND 120)
            );
            CREATE TABLE IF NOT EXISTS request_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                duration_ms INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_request_logs_created
                ON request_logs (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_request_logs_provider_created
                ON request_logs (provider, created_at DESC);
            CREATE TABLE IF NOT EXISTS rate_limit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                client_key TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rate_limit_events_window
                ON rate_limit_events (provider, client_key, created_at);
            """
        )
        for provider in PROVIDERS:
            connection.execute(
                "INSERT OR IGNORE INTO provider_settings (provider, enabled, rate_limit) VALUES (?, 1, 60)",
                (provider,),
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title="API Connect",
    description="Integrações REST pré-configuradas, limite de uso e histórico de chamadas.",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class ProviderSettingsUpdate(BaseModel):
    enabled: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=MAX_RATE_LIMIT)


class IntegrationCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


def clean_json(value: Any, maximum: int = MAX_STORED_RESPONSE_BYTES) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) > maximum:
        return json.dumps({"_truncated": True, "_message": "Resposta omitida do histórico por exceder o limite."})
    return encoded


def public_log(row: sqlite3.Row, include_response: bool = False) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "provider": row["provider"],
        "operation": row["operation"],
        "method": row["method"],
        "endpoint": row["endpoint"],
        "status_code": row["status_code"],
        "outcome": row["outcome"],
        "duration_ms": row["duration_ms"],
        "parameters": json.loads(row["request_json"]),
        "error_message": row["error_message"],
        "created_at": row["created_at"],
        "has_response": row["response_json"] is not None,
    }
    if include_response:
        item["response"] = json.loads(row["response_json"]) if row["response_json"] else None
    return item


def record_log(
    provider: str,
    operation: str,
    method: str,
    endpoint: str,
    status_code: int,
    outcome: str,
    duration_ms: int,
    parameters: dict[str, Any],
    response_data: Any = None,
    error_message: str | None = None,
) -> int:
    with database() as connection:
        cursor = connection.execute(
            """INSERT INTO request_logs
               (provider, operation, method, endpoint, status_code, outcome, duration_ms,
                request_json, response_json, error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider, operation, method, endpoint, status_code, outcome, duration_ms,
                clean_json(parameters) or "{}",
                clean_json(response_data),
                error_message[:500] if error_message else None,
                timestamp(),
            ),
        )
        connection.execute(
            """DELETE FROM request_logs
               WHERE id NOT IN (SELECT id FROM request_logs ORDER BY id DESC LIMIT 10000)"""
        )
        return int(cursor.lastrowid)


def provider_settings(provider: str) -> sqlite3.Row:
    with database() as connection:
        row = connection.execute(
            "SELECT provider, enabled, rate_limit FROM provider_settings WHERE provider = ?",
            (provider,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Integração não encontrada.")
    return row


def make_client_key(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    digest = hmac.new(RATE_LIMIT_SECRET.encode(), address.encode(), hashlib.sha256)
    return digest.hexdigest()


def consume_rate_limit(provider: str, client_key: str, limit: int) -> tuple[bool, int, int]:
    current_time = now_utc()
    cutoff = timestamp(current_time - timedelta(minutes=1))
    current_timestamp = timestamp(current_time)
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        used = connection.execute(
            """SELECT COUNT(*) FROM rate_limit_events
               WHERE provider = ? AND client_key = ? AND created_at >= ?""",
            (provider, client_key, cutoff),
        ).fetchone()[0]
        if used >= limit:
            oldest = connection.execute(
                """SELECT created_at FROM rate_limit_events
                   WHERE provider = ? AND client_key = ? AND created_at >= ?
                   ORDER BY created_at ASC LIMIT 1""",
                (provider, client_key, cutoff),
            ).fetchone()
            reset_at = datetime.fromisoformat(oldest["created_at"]) + timedelta(minutes=1) if oldest else current_time
            return False, 0, max(1, int((reset_at - current_time).total_seconds()))
        connection.execute(
            "INSERT INTO rate_limit_events (provider, client_key, created_at) VALUES (?, ?, ?)",
            (provider, client_key, current_timestamp),
        )
        connection.execute("DELETE FROM rate_limit_events WHERE created_at < ?", (timestamp(current_time - timedelta(days=1)),))
        return True, limit - used - 1, 60


def integration_for(provider: str) -> dict[str, Any]:
    integration = PROVIDERS.get(provider)
    if integration is None:
        raise HTTPException(status_code=404, detail="Integração não encontrada.")
    return integration


def operation_for(provider: str, operation_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    integration = integration_for(provider)
    operation = next((item for item in integration["operations"] if item["id"] == operation_id), None)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operação não encontrada para esta integração.")
    return integration, operation


def validate_parameters(provider: str, operation: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    fields = {item["name"]: item for item in operation["fields"]}
    unknown = set(parameters) - set(fields)
    if unknown:
        raise ValueError(f"Parâmetro(s) não permitido(s): {', '.join(sorted(unknown))}.")
    cleaned: dict[str, Any] = {}
    for name, field in fields.items():
        value = parameters.get(name)
        if value is None or value == "":
            if field.get("required"):
                raise ValueError(f"O campo «{field['label']}» é obrigatório.")
            if "default" in field and value is None:
                cleaned[name] = field["default"]
            continue
        if field["type"] == "number":
            if isinstance(value, bool):
                raise ValueError(f"O campo «{field['label']}» precisa ser numérico.")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"O campo «{field['label']}» precisa ser numérico.") from exc
            if not (field["min"] <= number <= field["max"]):
                raise ValueError(f"O campo «{field['label']}» deve estar entre {field['min']} e {field['max']}.")
            if number.is_integer():
                number = int(number)
            cleaned[name] = number
            continue
        if not isinstance(value, str):
            raise ValueError(f"O campo «{field['label']}» precisa ser texto.")
        text = value.strip()
        if not text:
            if field.get("required"):
                raise ValueError(f"O campo «{field['label']}» é obrigatório.")
            continue
        if len(text) > field.get("maxLength", 2000):
            raise ValueError(f"O campo «{field['label']}» excede o limite de caracteres.")
        if provider == "github" and name in {"owner", "repo"}:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", text):
                raise ValueError(f"O campo «{field['label']}» contém caracteres não permitidos.")
        cleaned[name] = text
    return cleaned


def build_upstream_request(
    provider: str,
    operation: dict[str, Any],
    parameters: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    method = operation["method"]
    if provider == "jsonplaceholder":
        if operation["id"] == "posts.list":
            return "https://jsonplaceholder.typicode.com/posts", method, {
                "_limit": parameters.get("limit", 10),
                **({"userId": parameters["userId"]} if "userId" in parameters else {}),
            }, None
        if operation["id"] == "posts.get":
            return f"https://jsonplaceholder.typicode.com/posts/{parameters['id']}", method, {}, None
        if operation["id"] == "posts.create":
            return "https://jsonplaceholder.typicode.com/posts", method, {}, {
                "title": parameters["title"],
                "body": parameters["body"],
                "userId": parameters["userId"],
            }
        return "https://jsonplaceholder.typicode.com/users", method, {}, None
    if provider == "open-meteo" and operation["id"] == "locations.search":
        return "https://geocoding-api.open-meteo.com/v1/search", method, {
            "name": parameters["name"], "count": 5, "language": "pt", "format": "json",
        }, None
    if provider == "open-meteo" and operation["id"] == "weather.current":
        return "https://api.open-meteo.com/v1/forecast", method, {
            "latitude": parameters["latitude"],
            "longitude": parameters["longitude"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 3,
            "timezone": "auto",
        }, None
    if provider == "github" and operation["id"] == "repositories.get":
        return f"https://api.github.com/repos/{parameters['owner']}/{parameters['repo']}", method, {}, None
    raise HTTPException(status_code=404, detail="Operação não encontrada para esta integração.")


def dashboard_data() -> dict[str, Any]:
    now = now_utc()
    today = timestamp(now.replace(hour=0, minute=0, second=0, microsecond=0))
    last_24_hours = timestamp(now - timedelta(hours=24))
    with database() as connection:
        today_total = connection.execute(
            "SELECT COUNT(*) FROM request_logs WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        day_rows = connection.execute(
            """SELECT outcome, duration_ms FROM request_logs
               WHERE created_at >= ? ORDER BY created_at""", (last_24_hours,)
        ).fetchall()
        providers = []
        for provider, definition in PROVIDERS.items():
            settings = connection.execute(
                "SELECT enabled, rate_limit FROM provider_settings WHERE provider = ?", (provider,)
            ).fetchone()
            provider_logs = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successful,
                          MAX(created_at) AS last_call
                   FROM request_logs WHERE provider = ? AND created_at >= ?""",
                (provider, last_24_hours),
            ).fetchone()
            providers.append({
                "id": provider,
                "name": definition["name"],
                "enabled": bool(settings["enabled"]),
                "rate_limit_per_minute": settings["rate_limit"],
                "requests_24h": provider_logs["total"],
                "successful_24h": provider_logs["successful"] or 0,
                "last_call": provider_logs["last_call"],
            })
        recent_rows = connection.execute(
            "SELECT * FROM request_logs ORDER BY id DESC LIMIT 8"
        ).fetchall()
        hourly = {hour: 0 for hour in range(24)}
        hour_rows = connection.execute(
            """SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hour, COUNT(*) AS total
               FROM request_logs WHERE created_at >= ? GROUP BY hour""",
            (last_24_hours,),
        ).fetchall()
        for row in hour_rows:
            hourly[row["hour"]] = row["total"]
    successes = sum(1 for row in day_rows if row["outcome"] == "success")
    failures = len(day_rows) - successes
    average_latency = round(sum(row["duration_ms"] for row in day_rows) / len(day_rows)) if day_rows else 0
    return {
        "requests_today": today_total,
        "requests_24h": len(day_rows),
        "successful_24h": successes,
        "failed_24h": failures,
        "average_latency_ms": average_latency,
        "success_rate": round(successes * 100 / len(day_rows)) if day_rows else 100,
        "hourly_requests": [{"hour": f"{hour:02d}:00", "count": hourly[hour]} for hour in range(24)],
        "providers": providers,
        "recent_logs": [public_log(row) for row in recent_rows],
    }


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    with database() as connection:
        connection.execute("SELECT 1")
    return {"status": "ok", "database": "connected"}


@app.get("/api/integrations")
def list_integrations() -> list[dict[str, Any]]:
    with database() as connection:
        settings = {
            row["provider"]: row
            for row in connection.execute("SELECT provider, enabled, rate_limit FROM provider_settings")
        }
    result = []
    for provider, definition in PROVIDERS.items():
        current = settings[provider]
        result.append({
            "id": provider,
            "name": definition["name"],
            "description": definition["description"],
            "category": definition["category"],
            "icon": definition["icon"],
            "enabled": bool(current["enabled"]),
            "rate_limit_per_minute": current["rate_limit"],
            "operations": definition["operations"],
        })
    return result


@app.get("/api/dashboard")
def get_dashboard() -> dict[str, Any]:
    return dashboard_data()


@app.patch("/api/integrations/{provider}/settings")
def update_integration_settings(provider: str, data: ProviderSettingsUpdate) -> dict[str, Any]:
    integration_for(provider)
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Informe ao menos uma configuração para atualizar.")
    assignments = []
    values: list[Any] = []
    for key, value in changes.items():
        column = "enabled" if key == "enabled" else "rate_limit"
        assignments.append(f"{column} = ?")
        values.append(int(value) if key == "enabled" else value)
    values.append(provider)
    with database() as connection:
        connection.execute(
            f"UPDATE provider_settings SET {', '.join(assignments)} WHERE provider = ?",
            values,
        )
        row = connection.execute(
            "SELECT enabled, rate_limit FROM provider_settings WHERE provider = ?", (provider,)
        ).fetchone()
    return {
        "provider": provider,
        "enabled": bool(row["enabled"]),
        "rate_limit_per_minute": row["rate_limit"],
    }


@app.get("/api/logs")
def list_logs(
    provider: str | None = None,
    outcome: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    conditions: list[str] = []
    values: list[Any] = []
    if provider:
        integration_for(provider)
        conditions.append("provider = ?")
        values.append(provider)
    if outcome:
        if outcome not in {"success", "upstream_error", "rate_limited", "disabled", "invalid_request"}:
            raise HTTPException(status_code=422, detail="Filtro de resultado inválido.")
        conditions.append("outcome = ?")
        values.append(outcome)
    if q and q.strip():
        conditions.append("(provider LIKE ? OR method LIKE ? OR endpoint LIKE ? OR operation LIKE ? OR error_message LIKE ?)")
        pattern = f"%{q.strip()}%"
        values.extend((pattern, pattern, pattern, pattern, pattern))
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with database() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM request_logs{where}", values).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM request_logs{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
    return {"items": [public_log(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/logs/{log_id}")
def get_log(log_id: int) -> dict[str, Any]:
    with database() as connection:
        row = connection.execute("SELECT * FROM request_logs WHERE id = ?", (log_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Registro não encontrado.")
    return public_log(row, include_response=True)


@app.delete("/api/logs", status_code=204)
def clear_logs() -> Response:
    with database() as connection:
        connection.execute("DELETE FROM request_logs")
    return Response(status_code=204)


@app.post("/api/integrations/{provider}/{operation_id}")
async def execute_integration(
    provider: str,
    operation_id: str,
    data: IntegrationCall,
    request: Request,
) -> JSONResponse:
    integration, operation = operation_for(provider, operation_id)
    method = operation["method"]
    endpoint = integration["base_url"]
    try:
        parameters = validate_parameters(provider, operation, data.parameters)
    except ValueError as exc:
        record_log(provider, operation_id, method, endpoint, 422, "invalid_request", 0, data.parameters, error_message=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    endpoint, method, query, body = build_upstream_request(provider, operation, parameters)
    settings = provider_settings(provider)
    started = time.perf_counter()
    if not settings["enabled"]:
        message = "Esta integração está desativada."
        record_log(provider, operation_id, method, endpoint, 503, "disabled", 0, parameters, error_message=message)
        raise HTTPException(status_code=503, detail=message)
    allowed, remaining, reset = consume_rate_limit(provider, make_client_key(request), settings["rate_limit"])
    headers = {
        "X-RateLimit-Limit": str(settings["rate_limit"]),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset),
    }
    if not allowed:
        message = "Limite de uso atingido para esta integração. Aguarde antes de tentar novamente."
        record_log(provider, operation_id, method, endpoint, 429, "rate_limited", 0, parameters, error_message=message)
        raise HTTPException(status_code=429, detail=message, headers=headers)
    try:
        request_headers = {"User-Agent": CLIENT, "Accept": "application/json"}
        if provider == "github":
            request_headers["X-GitHub-Api-Version"] = "2022-11-28"
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False, headers=request_headers) as client:
            upstream = await client.request(method, endpoint, params=query or None, json=body)
        elapsed = round((time.perf_counter() - started) * 1000)
        if len(upstream.content) > MAX_RESPONSE_BYTES:
            message = "A resposta do serviço externo excedeu o tamanho permitido."
            record_log(provider, operation_id, method, endpoint, 502, "upstream_error", elapsed, parameters, error_message=message)
            raise HTTPException(status_code=502, detail=message, headers=headers)
        if not 200 <= upstream.status_code < 300:
            message = f"O serviço externo respondeu com HTTP {upstream.status_code}."
            record_log(
                provider, operation_id, method, endpoint, 502, "upstream_error", elapsed, parameters,
                error_message=message,
            )
            raise HTTPException(status_code=502, detail=message, headers=headers)
        try:
            payload = upstream.json()
        except ValueError as exc:
            message = "O serviço externo retornou uma resposta JSON inválida."
            record_log(provider, operation_id, method, endpoint, 502, "upstream_error", elapsed, parameters, error_message=message)
            raise HTTPException(status_code=502, detail=message, headers=headers) from exc
        log_id = record_log(
            provider, operation_id, method, endpoint, upstream.status_code, "success", elapsed,
            parameters, response_data=payload,
        )
        return JSONResponse(
            status_code=upstream.status_code,
            content={"request_id": log_id, "provider": provider, "operation": operation_id, "data": payload},
            headers=headers,
        )
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        message = "O serviço externo excedeu o tempo limite de 8 segundos."
        record_log(provider, operation_id, method, endpoint, 504, "upstream_error", elapsed, parameters, error_message=message)
        raise HTTPException(status_code=504, detail=message, headers=headers) from exc
    except httpx.RequestError as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        message = "Não foi possível conectar ao serviço externo."
        record_log(provider, operation_id, method, endpoint, 502, "upstream_error", elapsed, parameters, error_message=message)
        raise HTTPException(status_code=502, detail=message, headers=headers) from exc


@app.get("/api/docs", include_in_schema=False)
def api_docs_redirect() -> Response:
    return Response(status_code=307, headers={"Location": "/docs"})
