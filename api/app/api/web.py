"""Bootstrap do `LocalSessionToken` pelo HTML inicial.

[06](../../../docs/architecture/06-api-and-ui-boundaries.md) §1: o token é **injetado no
HTML inicial** servido pela FastAPI no build compilado, como
`<meta name="ff-session-token" content="…">`, com `Cache-Control: no-store`. **Não existe
rota de bootstrap** — essa era a segunda rota sem autenticação que REAUD-003 eliminou.

Escopo E2: o **mecanismo**. A interface do Dev Workspace é E3, e o frontend React em
`src/` não é tocado. Quando não há build compilado disponível, a rota responde
`404 {"code": "web_ui_unavailable"}` — sem inventar uma segunda SPA e sem exigir
`npm run build` para a suíte rodar.
"""

from __future__ import annotations

import html
import re

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.security import SESSION_TOKEN_META_NAME

router = APIRouter(include_in_schema=False)

_EXISTING_META = re.compile(
    rf"""<meta\s[^>]*name\s*=\s*["']{re.escape(SESSION_TOKEN_META_NAME)}["'][^>]*>""",
    re.IGNORECASE,
)
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_HEAD_OPEN = re.compile(r"<head[^>]*>", re.IGNORECASE)


def render_bootstrap_html(template: str, token: str) -> str:
    """Injeta a `<meta>` do token no HTML.

    Qualquer `<meta>` de token já presente no template é **removida** antes: um build
    antigo com token obsoleto não pode conviver com o atual, e duas `<meta>` iguais
    deixariam o SPA escolhendo qual ler.

    O valor é escapado mesmo sendo `token_urlsafe` (alfanumérico, `-` e `_`): confiar no
    formato do gerador para pular escaping é a classe de atalho que vira injeção quando
    alguém troca o gerador.
    """
    cleaned = _EXISTING_META.sub("", template)
    meta = f'<meta name="{SESSION_TOKEN_META_NAME}" content="{html.escape(token, quote=True)}">'

    head_close = _HEAD_CLOSE.search(cleaned)
    if head_close:
        return f"{cleaned[: head_close.start()]}{meta}{cleaned[head_close.start() :]}"

    head_open = _HEAD_OPEN.search(cleaned)
    if head_open:
        return f"{cleaned[: head_open.end()]}{meta}{cleaned[head_open.end() :]}"

    return f"{meta}{cleaned}"


@router.get("/", summary="SPA local com o token de sessão injetado")
def index(request: Request) -> Response:
    settings = request.app.state.settings
    index_path = settings.web_index_path

    if index_path is None or not index_path.is_file():
        # Sem build compilado o mecanismo existe, mas não há o que servir. A mensagem não
        # revela o caminho procurado — [01] §4 mantém paths locais do lado do backend.
        return JSONResponse(
            status_code=404,
            content={
                "code": "web_ui_unavailable",
                "message": "interface local não disponível neste build",
            },
        )

    rendered = render_bootstrap_html(
        index_path.read_text(encoding="utf-8"), request.app.state.session_token
    )
    return HTMLResponse(
        rendered,
        headers={"Cache-Control": "no-store"},
    )
