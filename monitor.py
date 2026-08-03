#!/usr/bin/env python3
"""
Monitor do Portal PSG Senac Piauí.

Verifica a página de vagas, identifica editais em PDF novos (que ainda não
foram vistos antes), baixa cada um, procura pela palavra-chave configurada
(por padrão "enfermagem") dentro do texto do PDF e, se encontrar, dispara
uma notificação via ntfy.sh.

Estado (quais PDFs já foram processados) fica salvo em seen.json, para não
notificar a mesma coisa duas vezes.
"""

import json
import os
import re
import sys
import unicodedata
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VAGAS_URL = "https://psg.pi.senac.br/vagas/"
SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen.json")

# Palavra-chave que deve aparecer dentro do PDF para gerar notificação.
# Pode ser sobrescrita pela variável de ambiente KEYWORD.
KEYWORD = os.environ.get("KEYWORD", "enfermagem").lower()

# Configuração do ntfy.sh (definido via variáveis de ambiente / secrets)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")

# Configuração do CallMeBot (WhatsApp) - opcional, definido via secrets
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE")   # ex: 5586999999999 (com DDI+DDD, só números)
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SenacVagasMonitor/1.0)"
}


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def fetch_pdf_links():
    """Retorna lista de (titulo, url_absoluta) para cada link de PDF na página."""
    resp = requests.get(VAGAS_URL, headers=HEADERS, timeout=30, verify=False)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            full_url = urljoin(VAGAS_URL, href)

            # Tenta achar um título mais descritivo olhando o bloco/heading
            # mais próximo antes do link (ex: <h5>, <h4>, <strong>, etc.)
            title = None
            block = a.find_parent(["div", "li", "article"])
            if block:
                heading = block.find(["h1", "h2", "h3", "h4", "h5", "strong"])
                if heading:
                    title = heading.get_text(strip=True)
            if not title:
                title = a.get_text(strip=True) or full_url

            links.append((title, full_url))

    # remove duplicados mantendo ordem
    dedup = {}
    for title, url in links:
        dedup[url] = title
    return [(t, u) for u, t in dedup.items()]


def strip_accents(text):
    """Remove acentos (á->a, ç->c, etc) para comparação mais tolerante."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )


def normalize_for_search(text):
    """
    Deixa o texto 'achatado' pra busca: sem acento, minúsculo, e sem
    espaços/hífens/quebras de linha no meio - assim uma palavra que veio
    quebrada tipo 'Enfer-\\nmagem' ou 'E n f e r m a g e m' ainda é
    encontrada como 'enfermagem'.
    """
    text = strip_accents(text).lower()
    # remove hífens de quebra de linha (ex: "enfer-\nmagem" -> "enfermagem")
    text = re.sub(r"-\s*\n\s*", "", text)
    # remove todo espaço em branco e hífens restantes
    text = re.sub(r"[\s\-]+", "", text)
    return text


def normalize_header(cell):
    """Normaliza o texto de um cabeçalho de tabela pra comparação (sem acento,
    minúsculo, sem quebra de linha)."""
    text = strip_accents(cell or "").lower()
    return re.sub(r"\s+", " ", text).strip()


def clean_cell(cell):
    """Limpa o texto de uma célula de tabela pra exibição (junta quebras de
    linha em espaço, remove espaços duplicados)."""
    text = (cell or "").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_course_rows(pdf, keyword):
    """
    Varre as tabelas de cada página do PDF procurando uma coluna 'Curso'.
    Quando uma linha contém a palavra-chave no nome do curso, monta um
    dicionário com os detalhes daquela linha (município, unidade, carga
    horária, período, horário, dias, vagas).

    Retorna uma lista de dicionários (pode ser vazia se o PDF não seguir
    esse formato de tabela, ou se não achar nenhuma linha correspondente).
    """
    flat_keyword = normalize_for_search(keyword)
    matches = []

    for page in pdf.pages:
        page_text = page.extract_text() or ""

        municipio_match = re.search(r"Munic[ií]pio:\s*([^\n]+)", page_text, re.IGNORECASE)
        unidade_match = re.search(r"Unidade Senac:\s*([^\n/]+)", page_text, re.IGNORECASE)
        municipio = municipio_match.group(1).strip() if municipio_match else None
        unidade = unidade_match.group(1).strip() if unidade_match else None

        for table in page.extract_tables():
            if not table or len(table) < 2:
                continue

            header = [normalize_header(c) for c in table[0]]
            col_idx = {}
            for i, h in enumerate(header):
                if h == "curso":
                    col_idx["curso"] = i
                elif "tipo de curso" in h:
                    col_idx["tipo"] = i
                elif "requisito" in h:
                    col_idx["prereq"] = i
                elif "carga" in h:
                    col_idx["carga"] = i
                elif "periodo" in h:
                    col_idx["periodo"] = i
                elif "horario" in h or "turno" in h:
                    col_idx["horario"] = i
                elif "dias" in h:
                    col_idx["dias"] = i
                elif "vagas" in h:
                    col_idx["vagas"] = i
                elif "reserva" in h:
                    col_idx["reservas"] = i

            if "curso" not in col_idx:
                continue  # essa tabela não é do formato "lista de cursos"

            for row in table[1:]:
                if not row or col_idx["curso"] >= len(row):
                    continue
                curso_cell = row[col_idx["curso"]] or ""
                if flat_keyword not in normalize_for_search(curso_cell):
                    continue

                def get(key):
                    idx = col_idx.get(key)
                    if idx is None or idx >= len(row):
                        return None
                    return clean_cell(row[idx])

                matches.append({
                    "curso": get("curso"),
                    "tipo": get("tipo"),
                    "municipio": municipio,
                    "unidade": unidade,
                    "carga_horaria": get("carga"),
                    "periodo": get("periodo"),
                    "horario": get("horario"),
                    "dias": get("dias"),
                    "vagas": get("vagas"),
                })

    return matches


def build_fuzzy_pattern(keyword):
    """Constrói um regex tolerante a espaços/hífens soltos entre as letras
    da palavra-chave, pra achar um trecho de contexto mesmo em texto corrido
    (quando não há uma tabela estruturada de cursos)."""
    chars = [re.escape(c) for c in strip_accents(keyword).lower()]
    return re.compile(r"[\s\-]*".join(chars), re.IGNORECASE)


def extract_context_snippet(full_text, keyword, radius=150):
    """Fallback pra quando o PDF não tem uma tabela de cursos reconhecível:
    procura a palavra-chave em texto corrido e retorna um trecho de contexto
    ao redor dela."""
    stripped = strip_accents(full_text).lower()
    pattern = build_fuzzy_pattern(keyword)
    match = pattern.search(stripped)
    if not match:
        return None
    start = max(0, match.start() - radius)
    end = min(len(stripped), match.end() + radius)
    snippet = full_text[start:end]  # usa o texto original (com acento) pro trecho
    return re.sub(r"\s+", " ", snippet).strip()


def analyze_pdf(pdf_url, keyword):
    """
    Baixa o PDF e analisa se ele contém a palavra-chave.

    Retorna um dicionário:
      {
        "matched": bool,
        "course_rows": [ {...detalhes por curso/turma...}, ... ],
        "snippet": str ou None,   # usado quando não há tabela reconhecível
      }
    """
    result = {"matched": False, "course_rows": [], "snippet": None}

    try:
        resp = requests.get(pdf_url, headers=HEADERS, timeout=60, verify=False)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [erro] falha ao baixar {pdf_url}: {e}")
        return result

    tmp_path = "/tmp/_edital_tmp.pdf"
    with open(tmp_path, "wb") as f:
        f.write(resp.content)

    try:
        import pdfplumber
        with pdfplumber.open(tmp_path) as pdf:
            full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)

            if not full_text.strip():
                print("  [aviso] PDF sem texto extraível, tentando OCR (provável scan/imagem)...")
                full_text = ocr_pdf(tmp_path)
                if not full_text.strip():
                    print("  [erro] OCR também não conseguiu extrair texto.")
                    return result
                flat_text = normalize_for_search(full_text)
                flat_keyword = normalize_for_search(keyword)
                result["matched"] = flat_keyword in flat_text
                if result["matched"]:
                    result["snippet"] = extract_context_snippet(full_text, keyword)
                return result

            # IMPORTANTE: checa a tabela de cursos ANTES/independente do texto
            # corrido. O texto corrido (extract_text) embaralha células de
            # tabela com múltiplas linhas (ex: "Auxiliar em\nsaúde Bucal"
            # pode aparecer separado de outras palavras da mesma célula),
            # então uma checagem só no texto corrido pode dar falso negativo
            # mesmo quando a tabela claramente contém o curso procurado.
            result["course_rows"] = extract_course_rows(pdf, keyword)

            flat_text = normalize_for_search(full_text)
            flat_keyword = normalize_for_search(keyword)
            text_match = flat_keyword in flat_text

            if result["course_rows"]:
                result["matched"] = True
            elif text_match:
                result["matched"] = True
                result["snippet"] = extract_context_snippet(full_text, keyword)
    except Exception as e:
        print(f"  [erro] falha ao ler PDF {pdf_url}: {e}")

    return result


def ocr_pdf(pdf_path):
    """Converte cada página do PDF em imagem e roda OCR (Tesseract).
    Usado como fallback quando o PDF é um scan/imagem sem texto real."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        print("  [erro] pytesseract/pdf2image não instalados, pulando OCR.")
        return ""

    try:
        pages = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        print(f"  [erro] falha ao converter PDF em imagem para OCR: {e}")
        return ""

    text_chunks = []
    for i, page_img in enumerate(pages, start=1):
        try:
            page_text = pytesseract.image_to_string(page_img, lang="por")
        except Exception as e:
            print(f"  [erro] falha no OCR da página {i}: {e}")
            page_text = ""
        text_chunks.append(page_text)

    return "\n".join(text_chunks)


def format_notification_body(title, url, analysis):
    """Monta o corpo da mensagem de notificação com os detalhes encontrados
    no PDF (curso, município, horário, vagas etc), quando disponíveis."""
    lines = [f"📄 {title}"]

    course_rows = analysis.get("course_rows") or []
    if course_rows:
        for row in course_rows:
            lines.append("")  # linha em branco entre cursos
            if row.get("curso"):
                lines.append(f"🩺 Curso: {row['curso']}")
            if row.get("municipio"):
                loc = row["municipio"]
                if row.get("unidade"):
                    loc += f" — {row['unidade']}"
                lines.append(f"📍 {loc}")
            if row.get("horario"):
                turno_dias = row["horario"]
                if row.get("dias"):
                    turno_dias += f" ({row['dias']})"
                lines.append(f"🕐 {turno_dias}")
            if row.get("periodo"):
                lines.append(f"📅 Período: {row['periodo']}")
            if row.get("carga_horaria"):
                lines.append(f"⏱ Carga horária: {row['carga_horaria']}")
            if row.get("vagas"):
                lines.append(f"🎓 Vagas: {row['vagas']}")
    elif analysis.get("snippet"):
        lines.append("")
        lines.append(f"Trecho encontrado no edital: \"...{analysis['snippet']}...\"")

    lines.append("")
    lines.append(url)
    return "\n".join(lines)


def send_notification(title, url, analysis):
    if not NTFY_TOPIC:
        print("[aviso] NTFY_TOPIC não configurado, pulando notificação.")
        return

    message = format_notification_body(title, url, analysis)
    try:
        requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": f"Novo edital: {KEYWORD.title()}!".encode("utf-8"),
                "Priority": "urgent",
                "Tags": "rotating_light",
                "Click": url,
            },
            timeout=15,
        )
        print(f"  [ok] notificação enviada para topic '{NTFY_TOPIC}'")
    except requests.RequestException as e:
        print(f"  [erro] falha ao enviar notificação: {e}")


def send_whatsapp_notification(title, url, analysis):
    if not CALLMEBOT_PHONE or not CALLMEBOT_APIKEY:
        print("[aviso] CALLMEBOT_PHONE/CALLMEBOT_APIKEY não configurados, pulando WhatsApp.")
        return

    body = format_notification_body(title, url, analysis)
    text = f"🚨 Novo edital: {KEYWORD.title()}!\n\n{body}"
    try:
        requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={
                "phone": CALLMEBOT_PHONE,
                "text": text,
                "apikey": CALLMEBOT_APIKEY,
            },
            timeout=15,
        )
        print("  [ok] notificação enviada via WhatsApp (CallMeBot)")
    except requests.RequestException as e:
        print(f"  [erro] falha ao enviar WhatsApp: {e}")


def main():
    print(f"Verificando {VAGAS_URL} ...")
    seen = load_seen()

    try:
        links = fetch_pdf_links()
    except requests.RequestException as e:
        print(f"[erro] falha ao acessar o site: {e}")
        sys.exit(1)

    print(f"Encontrados {len(links)} PDFs na página.")

    new_count = 0
    for title, url in links:
        if url in seen:
            continue

        new_count += 1
        print(f"Novo edital encontrado: {title}")
        print(f"  -> {url}")

        analysis = analyze_pdf(url, KEYWORD)
        seen[url] = {
            "title": title,
            "matched": analysis["matched"],
        }

        if analysis["matched"]:
            print(f"  [MATCH] contém a palavra-chave '{KEYWORD}'!")
            if analysis["course_rows"]:
                print(f"  {len(analysis['course_rows'])} linha(s) de curso encontrada(s) na tabela.")
            send_notification(title, url, analysis)
            send_whatsapp_notification(title, url, analysis)
        else:
            print(f"  não contém '{KEYWORD}', não notificando.")

    if new_count == 0:
        print("Nenhum edital novo desde a última verificação.")

    save_seen(seen)


if __name__ == "__main__":
    main()
