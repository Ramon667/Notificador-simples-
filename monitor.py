#!/usr/bin/env python3
"""Monitor de editais do PSG Senac Piauí — v2.0."""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
VAGAS_URL = "https://psg.pi.senac.br/vagas/"
SEEN_FILE = BASE_DIR / "seen.json"
STATUS_FILE = BASE_DIR / "status.json"
HISTORY_FILE = BASE_DIR / "history.json"
CONFIG_FILE = BASE_DIR / "config.json"
DASHBOARD_FILE = BASE_DIR / "docs" / "index.html"

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "").strip()
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "").strip()

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SenacVagasMonitor/2.0)"}
SENAC_HOST = urlparse(VAGAS_URL).hostname

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def log(symbol: str, message: str) -> None:
    print(f"{symbol} {message}")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        log("🟡", f"Não foi possível ler {path.name}: {exc}.")
        return default


def atomic_save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temp, path)


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_FILE, {})
    if not config.get("course_groups"):
        raise RuntimeError("config.json não possui course_groups.")
    return config


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update(HEADERS)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


SESSION = build_session()


def senac_get(url: str, timeout: int = 60) -> requests.Response:
    if urlparse(url).hostname != SENAC_HOST:
        raise ValueError(f"Domínio externo recusado: {url}")
    response = SESSION.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return response


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    )


def normalize_text(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def compact_text(text: str) -> str:
    return normalize_text(text).replace(" ", "")


def fuzzy_term_match(text: str, term: str, threshold: float) -> bool:
    normalized_text = normalize_text(text)
    normalized_term = normalize_text(term)
    if not normalized_term:
        return False

    if normalized_term in normalized_text:
        return True

    compact_source = compact_text(text)
    compact_term = compact_text(term)
    if compact_term and compact_term in compact_source:
        return True

    words = normalized_text.split()
    term_words = normalized_term.split()
    window_size = max(len(term_words), 1)
    for size in range(max(1, window_size - 1), window_size + 2):
        for index in range(0, max(0, len(words) - size + 1)):
            candidate = " ".join(words[index:index + size])
            if difflib.SequenceMatcher(None, candidate, normalized_term).ratio() >= threshold:
                return True
    return False


def find_course_matches(text: str, config: dict[str, Any]) -> list[dict[str, str]]:
    threshold = float(config.get("matching", {}).get("fuzzy_threshold", 0.84))
    found: list[dict[str, str]] = []
    for group in config["course_groups"]:
        for term in group.get("terms", []):
            if fuzzy_term_match(text, term, threshold):
                found.append({"group": group["name"], "term": term})
                break
    return found


def fetch_pdf_links() -> list[tuple[str, str]]:
    response = senac_get(VAGAS_URL, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.lower().endswith(".pdf"):
            continue
        full_url = urljoin(VAGAS_URL, href)
        title = None
        block = anchor.find_parent(["div", "li", "article"])
        if block:
            heading = block.find(["h1", "h2", "h3", "h4", "h5", "strong"])
            if heading:
                title = heading.get_text(strip=True)
        links.append((title or anchor.get_text(strip=True) or full_url, full_url))

    dedup = {}
    for title, url in links:
        dedup[url] = title
    return [(title, url) for url, title in dedup.items()]


def extract_pdf_text(pdf_path: str) -> tuple[str, Any]:
    import pdfplumber
    pdf = pdfplumber.open(pdf_path)
    text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return text, pdf


def ocr_pdf(pdf_path: str) -> str:
    from pdf2image import convert_from_path
    import pytesseract

    pages = convert_from_path(pdf_path, dpi=180)
    chunks = []
    for index, image in enumerate(pages, start=1):
        log("🟡", f"OCR da página {index}/{len(pages)}.")
        chunks.append(pytesseract.image_to_string(image, lang="por"))
    return "\n".join(chunks)


def extract_context_snippet(text: str, term: str, radius: int = 170) -> str | None:
    normalized = normalize_text(text)
    target = normalize_text(term)
    position = normalized.find(target)
    if position < 0:
        return None
    start = max(0, position - radius)
    end = min(len(normalized), position + len(target) + radius)
    return normalized[start:end]


def analyze_pdf(pdf_url: str, config: dict[str, Any]) -> dict[str, Any]:
    result = {
        "analysis_ok": False,
        "matched": False,
        "matches": [],
        "snippet": None,
        "used_ocr": False,
    }

    response = senac_get(pdf_url, timeout=60)
    temp_name = ""
    pdf = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
            temp.write(response.content)
            temp_name = temp.name

        text, pdf = extract_pdf_text(temp_name)

        # OCR só é usado quando o PDF praticamente não tem texto extraível.
        if len(normalize_text(text)) < 30:
            result["used_ocr"] = True
            log("🟡", "PDF sem texto útil; ativando OCR.")
            if pdf:
                pdf.close()
                pdf = None
            text = ocr_pdf(temp_name)

        matches = find_course_matches(text, config)
        result["matches"] = matches
        result["matched"] = bool(matches)
        result["analysis_ok"] = bool(normalize_text(text))

        if matches:
            result["snippet"] = extract_context_snippet(text, matches[0]["term"])
    finally:
        if pdf:
            pdf.close()
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    return result


def format_notification_body(title: str, url: str, analysis: dict[str, Any]) -> str:
    lines = [f"📄 {title}", ""]
    for match in analysis.get("matches", []):
        lines.append(f"🎯 Categoria: {match['group']}")
        lines.append(f"🔎 Termo reconhecido: {match['term']}")
    if analysis.get("snippet"):
        lines.extend(["", f'Trecho: "...{analysis["snippet"]}..."'])
    lines.extend(["", url])
    return "\n".join(lines)


def send_ntfy(title: str, url: str, analysis: dict[str, Any], config: dict[str, Any]) -> bool:
    if not config.get("notifications", {}).get("ntfy_enabled", True):
        return True
    if not NTFY_TOPIC:
        log("🔴", "NTFY_TOPIC não configurado.")
        return False

    response = SESSION.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=format_notification_body(title, url, analysis).encode("utf-8"),
        headers={
            "Title": "Nova oferta relevante no SENAC PI",
            "Priority": "urgent",
            "Tags": "rotating_light",
            "Click": url,
            "Content-Type": "text/plain; charset=utf-8",
        },
        timeout=20,
    )
    response.raise_for_status()
    log("🟢", f"ntfy aceitou a notificação (HTTP {response.status_code}).")
    return True


def send_whatsapp(title: str, url: str, analysis: dict[str, Any], config: dict[str, Any]) -> bool:
    if not config.get("notifications", {}).get("whatsapp_enabled", True):
        return False
    if not CALLMEBOT_PHONE or not CALLMEBOT_APIKEY:
        log("🔵", "WhatsApp não configurado; canal ignorado.")
        return False

    response = SESSION.get(
        "https://api.callmebot.com/whatsapp.php",
        params={
            "phone": CALLMEBOT_PHONE,
            "text": format_notification_body(title, url, analysis),
            "apikey": CALLMEBOT_APIKEY,
        },
        timeout=20,
    )
    response.raise_for_status()
    body = response.text.lower()
    explicit_errors = (
        "invalid apikey",
        "apikey is invalid",
        "not authorized",
        "not activated",
    )
    if any(term in body for term in explicit_errors):
        log("🔴", f"CallMeBot recusou: {response.text[:300]}")
        return False
    log("🟢", f"CallMeBot respondeu HTTP {response.status_code}.")
    return True


def append_history(history: list[dict[str, Any]], item: dict[str, Any]) -> None:
    history.append(item)
    # Mantém o histórico limitado para não crescer indefinidamente.
    del history[:-500]


def generate_dashboard(status: dict[str, Any], history: list[dict[str, Any]], config: dict[str, Any]) -> None:
    if not config.get("dashboard", {}).get("enabled", True):
        return

    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    recent = list(reversed(history[-25:]))
    rows = []
    for item in recent:
        categories = ", ".join(item.get("matched_groups", [])) or "—"
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.get('detected_at', ''))}</td>"
            f"<td>{html.escape(item.get('title', ''))}</td>"
            f"<td>{html.escape(categories)}</td>"
            f"<td>{'Sim' if item.get('ntfy_sent') else 'Não'}</td>"
            f"<td><a href='{html.escape(item.get('url', ''))}'>PDF</a></td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(config.get('dashboard', {}).get('title', 'Monitor SENAC PI'))}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:auto;padding:24px;background:#f6f7f9;color:#111}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
.card{{background:#fff;padding:18px;border-radius:14px;box-shadow:0 2px 12px #0001}}
table{{width:100%;border-collapse:collapse;background:#fff;margin-top:18px}}
th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}
h1{{margin-bottom:6px}} small{{color:#555}}
</style>
</head>
<body>
<h1>{html.escape(config.get('dashboard', {}).get('title', 'Monitor SENAC PI'))}</h1>
<small>Última atualização: {html.escape(str(status.get('finished_at')))}</small>
<div class="cards">
<div class="card"><b>Sucesso</b><br>{status.get('success')}</div>
<div class="card"><b>PDFs encontrados</b><br>{status.get('pdfs_found')}</div>
<div class="card"><b>Novos</b><br>{status.get('new_pdfs')}</div>
<div class="card"><b>Matches</b><br>{status.get('matches')}</div>
<div class="card"><b>ntfy enviados</b><br>{status.get('ntfy_sent')}</div>
<div class="card"><b>Falhas</b><br>{status.get('analysis_failures')}</div>
</div>
<h2>Histórico recente</h2>
<table>
<thead><tr><th>Data UTC</th><th>Edital</th><th>Categorias</th><th>ntfy</th><th>Arquivo</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="5">Nenhum registro ainda.</td></tr>'}</tbody>
</table>
</body>
</html>"""
    DASHBOARD_FILE.write_text(page, encoding="utf-8")


def print_summary(status: dict[str, Any]) -> None:
    print("\n" + "=" * 56)
    print("RESUMO DA EXECUÇÃO")
    for label, key in (
        ("Início UTC", "started_at"),
        ("Fim UTC", "finished_at"),
        ("Sucesso", "success"),
        ("PDFs encontrados", "pdfs_found"),
        ("PDFs novos", "new_pdfs"),
        ("Matches", "matches"),
        ("ntfy enviados", "ntfy_sent"),
        ("WhatsApp enviados", "whatsapp_sent"),
        ("Falhas de análise", "analysis_failures"),
        ("Pendentes", "pending_notifications"),
    ):
        print(f"{label + ':':22} {status.get(key)}")
    print("=" * 56)


def main() -> int:
    config = load_config()
    seen = load_json(SEEN_FILE, {})
    history = load_json(HISTORY_FILE, [])
    status = {
        "started_at": utc_now_iso(),
        "finished_at": None,
        "success": False,
        "pdfs_found": 0,
        "new_pdfs": 0,
        "matches": 0,
        "ntfy_sent": 0,
        "whatsapp_sent": 0,
        "analysis_failures": 0,
        "pending_notifications": 0,
        "last_error": None,
    }

    try:
        log("🔵", f"Verificando {VAGAS_URL}")
        links = fetch_pdf_links()
        status["pdfs_found"] = len(links)
        log("🟢", f"{len(links)} PDFs encontrados.")

        for title, url in links:
            if url in seen:
                continue

            status["new_pdfs"] += 1
            log("🔵", f"Novo edital: {title}")

            try:
                analysis = analyze_pdf(url, config)
            except Exception as exc:
                status["analysis_failures"] += 1
                log("🔴", f"Falha ao analisar PDF: {exc}")
                continue

            if not analysis["analysis_ok"]:
                status["analysis_failures"] += 1
                log("🟡", "Análise inconclusiva; será tentada novamente.")
                continue

            matched = analysis["matched"]
            ntfy_sent = False
            whatsapp_sent = False

            if matched:
                status["matches"] += 1
                groups = [m["group"] for m in analysis["matches"]]
                log("🎯", f"Oferta relevante: {', '.join(groups)}")
                try:
                    ntfy_sent = send_ntfy(title, url, analysis, config)
                except Exception as exc:
                    log("🔴", f"Falha no ntfy: {exc}")

                try:
                    whatsapp_sent = send_whatsapp(title, url, analysis, config)
                except Exception as exc:
                    log("🔴", f"Falha no WhatsApp: {exc}")

                if not ntfy_sent:
                    status["pending_notifications"] += 1
                    log("🟡", "Edital ficará pendente para nova tentativa.")
                    continue

                status["ntfy_sent"] += 1
                if whatsapp_sent:
                    status["whatsapp_sent"] += 1
            else:
                groups = []
                log("⚪", "Nenhum curso de interesse encontrado.")

            processed_at = utc_now_iso()
            seen[url] = {
                "title": title,
                "matched": matched,
                "matched_groups": groups,
                "ntfy_sent": ntfy_sent,
                "whatsapp_sent": whatsapp_sent,
                "used_ocr": analysis.get("used_ocr", False),
                "processed_at": processed_at,
            }
            append_history(history, {
                "detected_at": processed_at,
                "title": title,
                "url": url,
                "matched": matched,
                "matched_groups": groups,
                "ntfy_sent": ntfy_sent,
                "whatsapp_sent": whatsapp_sent,
            })

        if status["new_pdfs"] == 0:
            log("⚪", "Nenhum edital novo.")

        atomic_save_json(SEEN_FILE, seen)
        atomic_save_json(HISTORY_FILE, history)
        status["success"] = True
        code = 0
    except Exception as exc:
        status["last_error"] = f"{type(exc).__name__}: {exc}"
        log("🔴", status["last_error"])
        code = 1
    finally:
        status["finished_at"] = utc_now_iso()
        atomic_save_json(STATUS_FILE, status)
        generate_dashboard(status, history, config)
        print_summary(status)

    return code


if __name__ == "__main__":
    sys.exit(main())
