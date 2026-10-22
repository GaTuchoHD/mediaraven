import asyncio
import logging
import os

from instagrapi import Client

import state
from config import IG_SESSION_FILE
from cookies import get_firefox_cookie_value
from messages import lmsg

logger = logging.getLogger(__name__)


def _restrict_perms(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as e:
        logger.debug(lmsg("instagram_login.falha_ao_aplicar", path=path, e=e))


def _sync_login_instagrapi() -> bool:
    ig_user = os.getenv("IG_USER")
    ig_pass = os.getenv("IG_PASS")

    if not ig_user or not ig_pass:
        sessionid = get_firefox_cookie_value(
            'sessionid',
            ('instagram.com',),
        )
        if not sessionid:
            logger.warning(lmsg("instagram_login.credenciais_do_instagram"))
            return False

        try:
            cl = Client()
            # Carregar settings salvos antes do login para preservar device_id,
            # user_agent e uuids — sem isso o Instagram rejeita a API privada
            # com LoginRequired mesmo com sessionid válido no browser.
            if os.path.exists(IG_SESSION_FILE):
                cl.load_settings(IG_SESSION_FILE)
            cl.login_by_sessionid(sessionid)
            cl.dump_settings(IG_SESSION_FILE)
            _restrict_perms(IG_SESSION_FILE)
            state.IG_CLIENT = cl
            logger.info("✅ Instagrapi autenticado com sessionid do banco Firefox.")
            return True
        except Exception as e:
            logger.warning(
                "Cookie do Instagram não foi aceito pelo Instagrapi (%s); "
                "mantendo o cliente anterior.",
                type(e).__name__,
            )
            return False

    try:
        cl = Client()
        if os.path.exists(IG_SESSION_FILE):
            logger.info(lmsg("instagram_login.carregando_sess_o_salva"))
            cl.load_settings(IG_SESSION_FILE)

        logger.info(lmsg("instagram_login.autenticando_instagrapi"))
        cl.login(ig_user, ig_pass)
        cl.dump_settings(IG_SESSION_FILE)
        _restrict_perms(IG_SESSION_FILE)

        state.IG_CLIENT = cl
        logger.info(lmsg("instagram_login.instagrapi_logado_e"))
        return True
    except Exception as e:
        logger.error(lmsg("instagram_login.erro_ao_inicializar", e=e), exc_info=True)
        return False


async def init_instagrapi_async(timeout: float = 60.0) -> bool:
    loop = asyncio.get_running_loop()
    try:
        return bool(await asyncio.wait_for(
            loop.run_in_executor(state.IG_POOL, _sync_login_instagrapi),
            timeout=timeout,
        ))
    except asyncio.TimeoutError:
        logger.warning(lmsg("instagram_login.login_do_instagram", timeout=timeout))
        return False
    except Exception as e:
        logger.error(lmsg("instagram_login.falha_inesperada_no", e=e), exc_info=True)
        return False
