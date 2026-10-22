# Segurança operacional do MediaRaven

- Configure `ADMIN_USER_IDS` com IDs numéricos de administradores. Somente esse
  conjunto pode executar comandos administrativos. `ALLOW_ALL` controla
  apenas downloads, nunca administração.
- Nunca envie `.env`, tokens de Telegram, cookies, banco de sessões, diretório
  Firefox, arquivos SQLite, logs nem caminhos privados ao Git ou à imagem.
- Aplique segmentação de rede e regras de **saída** no Docker/host. A verificação
  de URL no aplicativo **não substitui** o bloqueio de redes RFC1918, link-local,
  serviços locais e metadados de nuvem em conexões iniciadas por FFmpeg,
  yt-dlp, Playwright e bibliotecas terceiras. As validações iniciais e de
  redirects reduzem risco, mas não dão proteção completa contra DNS rebinding
  ou referências HLS internas.
- Monte cookies em diretório persistente privado, com permissões restritas.
- A API HTTP de métricas usa loopback por padrão. Evite publicá-la sem proxy
  autenticado: contém informações de estado e endpoints de logs.
- Teste mudanças primeiro em staging; o aplicativo depende de comportamento
  variável de plataformas externas. Não publique release com CI vermelho.
