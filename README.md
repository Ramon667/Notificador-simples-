# Monitor de vagas — Técnico em Enfermagem (Senac PSG Piauí)

Este projeto verifica automaticamente, a cada 30 minutos, a página
https://psg.pi.senac.br/vagas/ em busca de editais em PDF novos. Quando um
PDF novo contém a palavra "enfermagem", você recebe uma notificação no
celular.

Roda de graça na nuvem via **GitHub Actions** — não precisa deixar seu
computador ligado. As notificações chegam pelo app gratuito **ntfy.sh**.

---

## Passo 1 — Instalar o app ntfy no celular

1. Baixe o app **ntfy** ([Android - Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
   ou [iOS - App Store](https://apps.apple.com/us/app/ntfy/id1625396347)).
2. Abra o app e toque em **"+"** (Subscribe to topic).
3. Escolha um **nome de canal único e difícil de adivinhar**, por exemplo:
   `senac-pi-enfermagem-a37xk9` (não use algo óbvio, pois qualquer pessoa
   que souber o nome do canal pode ver as mesmas notificações — é público,
   só não é listado em lugar nenhum).
4. Deixe o servidor padrão `ntfy.sh` mesmo.
5. Guarde esse nome, você vai usar no Passo 3.

## Passo 1.5 — (Opcional) Ativar notificação também por WhatsApp

Isso usa o **CallMeBot**, um serviço gratuito e não-oficial bem popular para
esse tipo de automação pessoal. Ele NÃO acessa sua conta de WhatsApp, só
manda mensagem de um número deles pro seu número.

1. Salve este número na sua agenda: **+34 644 59 71 67** (nome sugerido:
   "CallMeBot").
2. Pelo WhatsApp, mande para esse número exatamente esta mensagem:
   `I allow callmebot to send me messages`
3. Aguarde a resposta — ela vai conter sua **API Key** (um número).
4. Guarde essa API Key e também o **seu número de telefone completo com
   DDI e DDD, só números** (ex: `5586999999999` para um número do Piauí
   com DDD 86). Você vai usar os dois no Passo 3.

> Limite do plano grátis: geralmente é suficiente para uso pessoal como
> esse (poucas notificações esporádicas). Se algum dia parar de funcionar,
> é só repetir o passo 2 pra reativar.

## Passo 2 — Criar o repositório no GitHub

1. Crie uma conta gratuita em [github.com](https://github.com) (se ainda
   não tiver).
2. Crie um **novo repositório** (pode ser privado).
3. Faça upload de todos os arquivos desta pasta (`monitor.py`,
   `requirements.txt`, `seen.json`, e a pasta `.github/workflows/check.yml`)
   mantendo a mesma estrutura de pastas.
   - Mais fácil: no GitHub, clique em "Add file" → "Upload files" e arraste
     tudo (o GitHub recria a pasta `.github/workflows` automaticamente se
     você arrastar o arquivo `check.yml` com o caminho preservado — se não
     preservar, crie a pasta manualmente pelo site: "Create new file" e
     digite `.github/workflows/check.yml` como nome).

## Passo 3 — Configurar o nome do canal como "secret"

1. No repositório, vá em **Settings → Secrets and variables → Actions**.
2. Clique em **New repository secret**.
3. Nome: `NTFY_TOPIC`
4. Valor: o nome do canal que você escolheu no Passo 1
   (ex: `senac-pi-enfermagem-a37xk9`)
5. Salve.
6. (Se ativou o WhatsApp) Repita o processo criando mais dois secrets:
   - `CALLMEBOT_PHONE` → seu número completo com DDI+DDD (ex: `5586999999999`)
   - `CALLMEBOT_APIKEY` → a API key que o CallMeBot te enviou no Passo 1.5

## Passo 4 — Ativar e testar

1. Vá na aba **Actions** do repositório.
2. Se aparecer um aviso pra habilitar Actions, clique para habilitar.
3. Clique no workflow **"Monitorar vagas Senac PI"** e depois em
   **"Run workflow"** para testar manualmente uma vez.
4. Veja o log: ele deve mostrar quantos PDFs encontrou na página. Como é a
   primeira vez, tudo vai ser marcado como "já visto" no `seen.json` sem
   notificar (para não te avisar de editais antigos). A partir daqui, só
   PDFs realmente novos vão gerar notificação.
5. Dali em diante, ele roda sozinho a cada 30 minutos.

---

## Personalizações

- **Mudar a frequência:** edite a linha `cron` em
  `.github/workflows/check.yml`. Ex: `*/15 * * * *` = a cada 15 min,
  `0 * * * *` = uma vez por hora. Evite menos de 15 min (limite de uso
  razoável do GitHub Actions em contas grátis).
- **Mudar a palavra-chave:** troque `"enfermagem"` pela palavra que quiser
  na linha `KEYWORD` do arquivo `check.yml`. Pode usar algo mais específico
  como `"técnico em enfermagem"`.
- **Testar localmente no seu PC** (opcional, sem precisar do GitHub):
  ```bash
  pip install -r requirements.txt
  export NTFY_TOPIC="seu-canal-aqui"
  python monitor.py
  ```

## Como funciona por baixo dos panos

1. Baixa o HTML de `psg.pi.senac.br/vagas/` e extrai todos os links que
   terminam em `.pdf`.
2. Compara com a lista de PDFs já vistos (`seen.json`, salvo no próprio
   repositório).
3. Para cada PDF novo: baixa o arquivo e tenta extrair o texto normalmente.
   - Se o PDF for um **scan/imagem** (sem texto selecionável), roda OCR
     (Tesseract) automaticamente como reforço.
4. Procura a palavra-chave de forma tolerante a acentos, maiúsculas/
   minúsculas, espaçamento estranho e quebras de linha com hífen.
5. Se encontrar, tenta ler a **tabela de cursos** do PDF (formato usado
   pelos editais do Senac-PI) para extrair automaticamente: nome do curso,
   município/unidade, carga horária, período, horário/turno, dias da
   semana e número de vagas. Se o PDF não tiver esse formato de tabela
   (ex: comunicados, editais de outro layout), a notificação inclui em vez
   disso um trecho de contexto ao redor da palavra encontrada.
6. Dispara a notificação (ntfy + WhatsApp) já com esses detalhes, título do
   edital e link direto pra baixar o PDF.
7. Salva o novo estado de volta no repositório (commit automático) para a
   próxima verificação.

## Limitações a saber

- Se o site mudar a estrutura do HTML, pode ser necessário ajustar o script.
- A extração de detalhes da tabela (curso, horário, vagas etc.) foi feita
  com base no layout típico dos editais "Qualificação/Aperfeiçoamento" do
  Senac-PI. Editais com layout de tabela diferente ainda são detectados
  normalmente, só que a notificação cai no modo "trecho de contexto" em vez
  da ficha completa.
- OCR não é 100% perfeito — em scans de baixa qualidade/tortos pode
  ocasionalmente errar uma letra. Na prática, como buscamos só um trecho
  específico da palavra, isso raramente afeta a detecção.
- GitHub Actions grátis tem limite de minutos/mês em contas privadas
  (bem generoso para uma tarefa leve como essa, mas existe). O passo de
  OCR deixa cada execução um pouco mais longa quando há PDF novo, mas
  ainda é rápido (segundos).
