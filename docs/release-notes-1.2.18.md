# MediaRaven v1.2.18 — Homelab hardening

Versão de manutenção focada em segurança, previsibilidade e operação em containers.
Nenhuma credencial, cookie, token, endereço de servidor privado ou dado de produção
é incluído nesta publicação.

## Alterações
- Administração por `ADMIN_USER_IDS`, independente da lista de chats e usuários
  autorizados a baixar conteúdo.
- Limite de fila e timeout de espera, preservando o número máximo de downloads
  simultâneos e informando sobre lotação.
- Cache Telegram isolado por idioma solicitado.
- Persistência serializada de métricas SQLite.
- Limpeza temporária restrita ao diretório da aplicação.
- Saída graciosa do bot ao solicitar reinício.
- Proteção uniforme de downloads HTTP com verificação de URL, redirects e
  limitação de bytes; fallback autenticado usa streaming em vez de buffer
  completo do Playwright.
- Imagem Docker sem secrets, usuário não root, healthcheck e exemplos de volumes
  persistentes para estado e cookies.
- Workflow para validar mudanças e publicar GHCR/Release somente após sucesso.
- Notas explícitas sobre riscos residuais de ferramentas externas e segmentação
  de rede necessária no host.

## Alterações de configuração
- Definir `ADMIN_USER_IDS` no ambiente privado para acessar comandos admin.
- Novos `MAX_PENDING_REQUESTS` e `MAX_QUEUE_WAIT_SECONDS`.
- No Docker, persistir `METRICS_DB_PATH` e `MEDIA_CACHE_DB_PATH` em volume.
- `LOCAL_API_HOST` continua apontando para um servidor Bot API externo.

## Verificação
A imagem e esta release somente serão publicadas pelo workflow após testes
automatizados concluírem com sucesso. O status do workflow é a fonte de verdade;
um commit isolado não significa build ou implantação validada.

## Verificação da branch de auditoria

- GitHub Actions Linux/Python 3.12: **442 testes aprovados, zero falhas, quatro avisos de depreciação**.
- Docker build: **aprovado**.
- Execução: https://github.com/GaTuchoHD/mediaraven/actions/runs/39857692712
- Não foram efetuados downloads reais nem implantação na instalação privada.
