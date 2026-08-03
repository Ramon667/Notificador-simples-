# Monitor SENAC PI — v2.0

## O que monitora

O monitor procura grupos de cursos definidos em `config.json`.

### Grupo Técnico em Enfermagem

Inclui termos como:

- técnico em enfermagem;
- técnico de enfermagem;
- curso técnico de enfermagem;
- formação técnica em enfermagem;
- enfermagem flexível;
- enfermagem.

### Grupo Redes

Inclui termos como:

- administrador de redes;
- administração de redes;
- redes de computadores;
- técnico em redes de computadores;
- infraestrutura de redes;
- suporte e manutenção de redes;
- cabeamento estruturado;
- configuração, gestão e segurança de redes;
- redes sem fio e networking.

A correspondência aceita acentos, quebras de linha, hífens e pequenas variações
de escrita. Os termos podem ser ampliados diretamente em `config.json`.

## Recursos

- múltiplos grupos de cursos;
- correspondência exata e aproximada;
- ntfy e WhatsApp já existentes;
- histórico em `history.json`;
- painel HTML em `docs/index.html`;
- GitHub Pages;
- OCR somente quando o PDF não possui texto útil;
- retries de rede;
- logs com símbolos;
- testes automatizados;
- `seen.json` para evitar duplicidade;
- `status.json` para a última execução.

## GitHub Pages

Depois de enviar os arquivos:

1. Abra **Settings → Pages**.
2. Em **Build and deployment**, selecione **GitHub Actions**.
3. Execute o workflow manualmente uma vez.
4. O job `deploy-pages` publicará o painel.

## Configuração

Edite `config.json` para adicionar ou remover nomes de cursos.

Não há filtro por cidade nesta versão.

## Secrets

Em **Settings → Secrets and variables → Actions**:

- `NTFY_TOPIC`;
- `CALLMEBOT_PHONE` opcional;
- `CALLMEBOT_APIKEY` opcional.
