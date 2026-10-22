# MediaRaven — versão Homelab

Bot do Telegram para extrair e enviar mídias de redes sociais, mantido no fork
[GaTuchoHD/mediaraven](https://github.com/GaTuchoHD/mediaraven).

Esta árvore contém a variante usada em produção, exportada em 22/10/2026.
O código informa versão `1.2.18`; esta é a linhagem da variante homelab e não
significa que incorpora todas as mudanças do upstream `1.3.2`.

## Recursos da nossa versão

- Download de vídeos, fotos e álbuns, com integrações de Instagram, Reddit,
  X, Threads, TikTok, Urlebird, Facebook, Kick e fallback genérico.
- Posts do X sem mídia retornam texto, autor e link original.
- Limite de upload verificado antes e depois da conversão; álbuns parciais
  têm tratamento específico e os vídeos não são preparados novamente a cada retry.
- Cache de mídia, métricas e tempos por etapa, verificação de saúde e watchdog.
- Leitura de cookies locais e sessões, proteção de redirects e filtragem de logs.

A disponibilidade de cada plataforma depende da autenticação, dos bloqueios
externos e da mídia. Não há credenciais ou sessões incluídas neste repositório.

## Instalação

Requer Python 3.11+, ffmpeg, Firefox para cookies quando necessário e um servidor
local Telegram Bot API para o envio de arquivos grandes.

```sh
git clone https://github.com/GaTuchoHD/mediaraven.git
cd mediaraven
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env_example .env
cp messages.example.json messages.json
cp log_messages.example.json log_messages.json
```

No Windows, ative com `.venv\Scripts\Activate.ps1` e use `Copy-Item` para as cópias.
Edite `.env` localmente: `TELEGRAM_BOT_TOKEN`, `LOCAL_API_HOST`, `BASE_DOWNLOAD_DIR`
e as listas de chats/usuários autorizados. O diretório de downloads deve ser
absoluto. O servidor Telegram Bot API deve estar acessível antes de iniciar:

```sh
python mediaraven.py
```

Configurações adicionais estão nos arquivos `.env_example` e
`customization.example.json`. Use variáveis de ambiente e arquivos locais para
credenciais; nunca os envie ao Git.

## Testes e limites de validação

```sh
pip install -r requirements-dev.txt
pytest -q
```

Consulte [VALIDATION.md](VALIDATION.md) para o resultado completo, incluindo
falhas conhecidas. O snapshot não constitui uma nova implantação no NAS.
Os componentes privados de Docker e da ponte HTTP do Rolês-SP não estão incluídos.

## Origem e licença

Variante de [lordaztur/mediaraven](https://github.com/lordaztur/mediaraven),
com alterações operacionais e funcionais do Homelab. Mantida a licença MIT
original em [LICENSE](LICENSE). Os créditos e avisos de copyright do código
original permanecem aplicáveis.

## Containers e segurança

O `Dockerfile`, `compose.example.yml` e `.github/workflows/ci-release.yml`
preparam builds públicos de Docker **sem segredos**. Use uma cópia local de
`.env_example` como `.env`, nunca comite tokens ou cookies e não monte
perfis privados na imagem durante o build. `ADMIN_USER_IDS` deve conter IDs
numéricos reais configurados apenas no ambiente privado para habilitar
comandos de manutenção; permissões de grupo não são administrativas.

O `compose.example.yml` utiliza um servidor Telegram Bot API **externo**
(`LOCAL_API_HOST`), que deve compartilhar o caminho de downloads do bot quando
arquivos locais forem enviados. Confirme que o servidor Bot API enxerga os
arquivos no mesmo caminho absoluto. Métricas e cache podem ser persistidos em
`/data/state` com `METRICS_DB_PATH` e `MEDIA_CACHE_DB_PATH`.

Arquivos temporários são apagados apenas sob `BASE_DOWNLOAD_DIR`. O Docker
não impede sozinho requisições a redes privadas provenientes de ferramentas
externas como FFmpeg, yt-dlp e navegadores; é obrigatório aplicar regras de
egress na infraestrutura para isolar a rede de gerenciamento do NAS.

Consulte [SECURITY.md](SECURITY.md) e [notas da versão](docs/release-notes-1.2.18.md).
