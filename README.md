# Monitor de vagas do Senac PSG Piauí — v1.1

Monitora a página de vagas do Senac Piauí, analisa PDFs novos e envia uma
notificação quando encontra a palavra-chave configurada, por padrão
`enfermagem`.

## Recursos

- execução automática pelo GitHub Actions a cada 30 minutos;
- extração de tabelas com `pdfplumber`;
- OCR com Tesseract para PDFs digitalizados;
- ntfy como canal principal;
- CallMeBot/WhatsApp como canal opcional;
- retry automático para falhas temporárias de rede;
- `seen.json` para impedir notificações duplicadas;
- `status.json` com o resultado da última execução;
- resumo na página da execução do GitHub Actions;
- testes automáticos antes de cada monitoramento.

## Estrutura

```text
.github/workflows/check.yml
tests/test_monitor.py
monitor.py
requirements.txt
seen.json
status.json
```

## Secrets

Em **Settings → Secrets and variables → Actions**, crie:

- `NTFY_TOPIC`: somente o nome do tópico, por exemplo
  `senac-pi-enfermagem-a37xk9`;
- `CALLMEBOT_PHONE`: opcional;
- `CALLMEBOT_APIKEY`: opcional.

Nunca coloque esses valores diretamente nos arquivos.

## Como testar

Abra **Actions → Monitorar vagas Senac PI → Run workflow**.

A execução valida a sintaxe, roda os testes, executa o monitor, atualiza
`seen.json` e `status.json`, e publica um resumo na tela da execução.

## Como confirmar a automação

Na lista de execuções:

- `workflow_dispatch` significa execução manual;
- `schedule` significa execução automática.

O cron usa os minutos 7 e 37 de cada hora para reduzir atrasos comuns no
início da hora.

## Observações

O portal do Senac está com certificado HTTPS expirado. A validação TLS é
desativada somente para o domínio `psg.pi.senac.br`; ntfy, CallMeBot e
outros domínios continuam com validação normal.

O ntfy é o canal obrigatório para considerar um edital notificado. Se o
ntfy falhar, o edital não é salvo como concluído e será tentado novamente.
