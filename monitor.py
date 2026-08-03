#!/usr/bin/env python3
"""Monitor de editais do PSG Senac Piauí."""

from __future__ import annotations

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

KEYWORD = os.environ.get("KEYWORD", "enfermagem").strip().lower()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "").strip()
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "").strip()

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SenacVagasMonitor/1.1)"}
SENAC_HOST = urlparse(VAGAS_URL).hostname

# O portal está com certificado expirado. O aviso é desativado apenas porque
# verify=False é usado exclusivamente nas requisições para o domínio do SENAC.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


SESSION = build_session()


def senac_get(url: str, timeout: int = 60) -> requests.Response:
    if urlparse(url).hostname != SENAC_HOST:
        raise ValueError(f"senac_get recusou domínio externo: {url}")
    response = SESSION.get(url, timeout=timeout, verify=False)
    response.raise_for_status()
    return response


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[aviso] não foi possível ler {path.name}: {exc}. Usando estado vazio.")
        return default


def atomic_save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temp_path, path)


def fetch_pdf_links() -> list[tuple[str, str]]:
    response = senac_get(VAGAS_URL, timeout=30)
    soup = BeautifulSoup(response.text, "html.parser")
    links: list[tuple[str, str]] = []

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
        if not title:
            title = anchor.get_text(strip=True) or full_url
        links.append((title, full_url))

    deduplicated: dict[str, str] = {}
    for title, url in links:
        deduplicated[url] = title
    return [(title, url) for url, title in deduplicated.items()]


def strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize_for_search(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"-\s*\n\s*", "", text)
    return re.sub(r"[\s\-]+", "", text)


def normalize_header(cell: str | None) -> str:
    return re.sub(r"\s+", " ", strip_accents(cell or "").lower()).strip()


def clean_cell(cell: str | None) -> str:
    return re.sub(r"\s+", " ", (cell or "").replace("\n", " ")).strip()


def extract_course_rows(pdf: Any, keyword: str) -> list[dict[str, str | None]]:
    flat_keyword = normalize_for_search(keyword)
    matches: list[dict[str, str | None]] = []

    for page in pdf.pages:
        page_text = page.extract_text() or ""
        municipality_match = re.search(
            r"Munic[ií]pio:\s*([^\n]+)", page_text, re.IGNORECASE
        )
        unit_match = re.search(
            r"Unidade Senac:\s*([^\n/]+)", page_text, re.IGNORECASE
        )
        municipality = municipality_match.group(1).strip() if municipality_match else None
        unit = unit_match.group(1).strip() if unit_match else None

        for table in page.extract_tables():
            if not table or len(table) < 2:
                continue

            headers = [normalize_header(cell) for cell in table[0]]
            indexes: dict[str, int] = {}
            for index, header in enumerate(headers):
                if header == "curso":
                    indexes["curso"] = index
                elif "tipo de curso" in header:
                    indexes["tipo"] = index
                elif "carga" in header:
                    indexes["carga"] = index
                elif "periodo" in header:
                    indexes["periodo"] = index
                elif "horario" in header or "turno" in header:
                    indexes["horario"] = index
                elif "dias" in header:
                    indexes["dias"] = index
                elif "vagas" in header:
                    indexes["vagas"] = index

            if "curso" not in indexes:
                continue

            for row in table[1:]:
                if not row or indexes["curso"] >= len(row):
                    continue
                if flat_keyword not in normalize_for_search(row[indexes["curso"]] or ""):
                    continue

                def get(key: str) -> str | None:
                    index = indexes.get(key)
                    if index is None or index >= len(row):
                        return None
                    return clean_cell(row[index])

                matches.append({
                    "curso": get("curso"),
                    "tipo": get("tipo"),
                    "municipio": municipality,
                    "unidade": unit,
                    "carga_horaria": get("carga"),
                    "periodo": get("periodo"),
                    "horario": get("horario"),
                    "dias": get("dias"),
                    "vagas": get("vagas"),
                })
    return matches


def build_fuzzy_pattern(keyword: str) -> re.Pattern[str]:
    chars = [re.escape(char) for char in strip_accents(keyword).lower()]
    return re.compile(r"[\s\-]*".join(chars), re.IGNORECASE)


def extract_context_snippet(full_text: str, keyword: str, radius: int = 150) -> str | None:
    match = build_fuzzy_pattern(keyword).search(strip_accents(full_text).lower())
    if not match:
        return None
    start = max(0, match.start() - radius)
    end = min(len(full_text), match.end() + radius)
    return re.sub(r"\s+", " ", full_text[start:end]).strip()


def ocr_pdf(pdf_path: str) -> str:
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        print("  [erro] pytesseract/pdf2image não instalados.")
        return ""

    try:
        pages = convert_from_path(pdf_path, dpi=200)
    except Exception as exc:
        print(f"  [erro] falha ao converter PDF para OCR: {exc}")
        return ""

    chunks: list[str] = []
    for number, image in enumerate(pages, start=1):
        try:
            chunks.append(pytesseract.image_to_string(image, lang="por"))
        except Exception as exc:
            print(f"  [erro] OCR da página {number}: {exc}")
    return "\n".join(chunks)


def analyze_pdf(pdf_url: str, keyword: str) -> dict[str, Any]:
    result = {
        "analysis_ok": False,
        "matched": False,
        "course_rows": [],
        "snippet": None,
    }

    try:
        response = senac_get(pdf_url, timeout=60)
    except (requests.RequestException, ValueError) as exc:
        print(f"  [erro] falha ao baixar PDF: {exc}")
        return result

    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
            temp.write(response.content)
            temp_name = temp.name

        import pdfplumber
        with pdfplumber.open(temp_name) as pdf:
            full_text = "\n".join((page.extract_text() or "") for page in pdf.pages)
            result["course_rows"] = extract_course_rows(pdf, keyword)

        if not full_text.strip():
            print("  [aviso] PDF sem texto; tentando OCR.")
            full_text = ocr_pdf(temp_name)

        flat_text = normalize_for_search(full_text)
        flat_keyword = normalize_for_search(keyword)
        text_match = bool(flat_text) and flat_keyword in flat_text

        result["matched"] = bool(result["course_rows"]) or text_match
        if result["matched"] and not result["course_rows"]:
            result["snippet"] = extract_context_snippet(full_text, keyword)
        result["analysis_ok"] = bool(full_text.strip() or result["course_rows"])
    except Exception as exc:
        print(f"  [erro] falha ao analisar PDF: {exc}")
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    return result


def format_notification_body(title: str, url: str, analysis: dict[str, Any]) -> str:
    lines = [f"📄 {title}"]
    rows = analysis.get("course_rows") or []

    if rows:
        for row in rows:
            lines.append("")
            if row.get("curso"):
                lines.append(f"🩺 Curso: {row['curso']}")
            if row.get("municipio"):
                location = row["municipio"]
                if row.get("unidade"):
                    location += f" — {row['unidade']}"
                lines.append(f"📍 {location}")
            if row.get("horario"):
                schedule = row["horario"]
                if row.get("dias"):
                    schedule += f" ({row['dias']})"
                lines.append(f"🕐 {schedule}")
            if row.get("periodo"):
                lines.append(f"📅 Período: {row['periodo']}")
            if row.get("carga_horaria"):
                lines.append(f"⏱ Carga horária: {row['carga_horaria']}")
            if row.get("vagas"):
                lines.append(f"🎓 Vagas: {row['vagas']}")
    elif analysis.get("snippet"):
        lines.extend(["", f'Trecho encontrado: "...{analysis["snippet"]}..."'])

    lines.extend(["", url])
    return "\n".join(lines)


def send_notification(title: str, url: str, analysis: dict[str, Any]) -> bool:
    if not NTFY_TOPIC:
        print("  [erro] NTFY_TOPIC não configurado.")
        return False

    try:
        response = SESSION.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=format_notification_body(title, url, analysis).encode("utf-8"),
            headers={
                "Title": f"Novo edital: {KEYWORD.title()}!",
                "Priority": "urgent",
                "Tags": "rotating_light",
                "Click": url,
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=20,
        )
        response.raise_for_status()
        print(f"  [ok] ntfy aceitou a notificação (HTTP {response.status_code}).")
        return True
    except requests.RequestException as exc:
        print(f"  [erro] ntfy: {exc}")
        if getattr(exc, "response", None) is not None:
            print(f"  [erro] resposta ntfy: {exc.response.text[:300]}")
        return False


def send_whatsapp_notification(
    title: str, url: str, analysis: dict[str, Any]
) -> bool:
    if not CALLMEBOT_PHONE or not CALLMEBOT_APIKEY:
        print("  [info] WhatsApp não configurado; canal ignorado.")
        return False

    text = f"🚨 Novo edital: {KEYWORD.title()}!\n\n{format_notification_body(title, url, analysis)}"
    try:
        response = SESSION.get(
            "https://api.callmebot.com/whatsapp.php",
            params={
                "phone": CALLMEBOT_PHONE,
                "text": text,
                "apikey": CALLMEBOT_APIKEY,
            },
            timeout=20,
        )
        response.raise_for_status()
        body = response.text.strip()
        explicit_errors = (
            "invalid apikey",
            "apikey is invalid",
            "phone number is not authorized",
            "not authorized",
            "not activated",
        )
        if any(term in body.lower() for term in explicit_errors):
            print(f"  [erro] CallMeBot recusou: {body[:350]}")
            return False
        print(f"  [ok] CallMeBot respondeu HTTP {response.status_code}.")
        return True
    except requests.RequestException as exc:
        print(f"  [erro] CallMeBot: {exc}")
        return False


def write_status(status: dict[str, Any]) -> None:
    atomic_save_json(STATUS_FILE, status)


def print_summary(status: dict[str, Any]) -> None:
    print("\n" + "=" * 56)
    print("RESUMO DA EXECUÇÃO")
    print(f"Início (UTC):        {status['started_at']}")
    print(f"Fim (UTC):           {status['finished_at']}")
    print(f"Sucesso:             {status['success']}")
    print(f"PDFs encontrados:    {status['pdfs_found']}")
    print(f"PDFs novos:          {status['new_pdfs']}")
    print(f"Matches:             {status['matches']}")
    print(f"ntfy enviados:       {status['ntfy_sent']}")
    print(f"WhatsApp enviados:   {status['whatsapp_sent']}")
    print(f"Falhas de análise:   {status['analysis_failures']}")
    print(f"Pendentes de ntfy:   {status['pending_notifications']}")
    print("=" * 56)


def main() -> int:
    started_at = utc_now_iso()
    status = {
        "started_at": started_at,
        "finished_at": None,
        "success": False,
        "keyword": KEYWORD,
        "pdfs_found": 0,
        "new_pdfs": 0,
        "matches": 0,
        "ntfy_sent": 0,
        "whatsapp_sent": 0,
        "analysis_failures": 0,
        "pending_notifications": 0,
        "last_error": None,
    }

    seen = load_json(SEEN_FILE, {})
    print(f"Verificando {VAGAS_URL} ...")

    try:
        links = fetch_pdf_links()
        status["pdfs_found"] = len(links)
        print(f"Encontrados {len(links)} PDFs na página.")

        for title, url in links:
            if url in seen:
                continue

            status["new_pdfs"] += 1
            print(f"\nNovo edital: {title}\n  -> {url}")
            analysis = analyze_pdf(url, KEYWORD)

            if not analysis["analysis_ok"]:
                status["analysis_failures"] += 1
                print("  [aviso] análise não concluída; será tentada novamente.")
                continue

            matched = bool(analysis["matched"])
            if matched:
                status["matches"] += 1
                print(f"  [MATCH] contém '{KEYWORD}'.")

                ntfy_sent = send_notification(title, url, analysis)
                whatsapp_sent = send_whatsapp_notification(title, url, analysis)

                if ntfy_sent:
                    status["ntfy_sent"] += 1
                else:
                    status["pending_notifications"] += 1
                    print("  [aviso] não será marcado como concluído; ntfy falhou.")
                    continue

                if whatsapp_sent:
                    status["whatsapp_sent"] += 1
            else:
                ntfy_sent = False
                whatsapp_sent = False
                print(f"  não contém '{KEYWORD}'.")

            seen[url] = {
                "title": title,
                "matched": matched,
                "ntfy_sent": ntfy_sent,
                "whatsapp_sent": whatsapp_sent,
                "processed_at": utc_now_iso(),
            }

        if status["new_pdfs"] == 0:
            print("Nenhum edital novo desde a última verificação.")

        atomic_save_json(SEEN_FILE, seen)
        status["success"] = True
        return_code = 0
    except Exception as exc:
        status["last_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[erro fatal] {status['last_error']}")
        return_code = 1
    finally:
        status["finished_at"] = utc_now_iso()
        write_status(status)
        print_summary(status)

    return return_code


if __name__ == "__main__":
    sys.exit(main())
