"""Soren NC verification must use the same creds as nextcloud write tools.

Regression: multi-presence Coves leave NEXTCLOUD_USER empty or not equal to the
acting identity. Tools still wrote via request/admin/founding-op fallback, then
Soren HEAD/PROPFIND'd with env-only auth → 401 noise in verification_log.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSorenNcHelpersShareWriteToolCreds:
    def test_nc_auth_uses_nextcloud_tools_auth(self):
        from src.tools import verification as v

        with patch("src.tools.nextcloud_tools._auth", return_value=("op-user", "op-pass")) as auth:
            assert v._nc_auth() == ("op-user", "op-pass")
            auth.assert_called_once_with()

    def test_nc_webdav_url_uses_nextcloud_tools_builder(self):
        from src.tools import verification as v

        with patch(
            "src.tools.nextcloud_tools._webdav_url",
            return_value="http://nc/remote.php/dav/files/op-user/AgentSkills/x.md",
        ) as webdav:
            url = v._nc_webdav_url("/AgentSkills/x.md")
            assert url.endswith("/AgentSkills/x.md")
            assert "op-user" in url
            webdav.assert_called_once_with("/AgentSkills/x.md")

    def test_nc_caldav_url_uses_acting_identity(self):
        from src.tools import verification as v

        with patch(
            "src.tools.nextcloud_tools._caldav_base",
            return_value="http://nc/remote.php/dav/calendars/op-user",
        ):
            assert v._nc_caldav_url("personal") == (
                "http://nc/remote.php/dav/calendars/op-user/personal/"
            )
            assert v._nc_caldav_url("/work/") == (
                "http://nc/remote.php/dav/calendars/op-user/work/"
            )

    def test_helpers_do_not_read_env_user_directly(self):
        """Guard against regressing to env("NEXTCLOUD_USER") in helpers."""
        import inspect
        from src.tools import verification as v

        for fn in (v._nc_auth, v._nc_webdav_url, v._nc_caldav_url):
            src = inspect.getsource(fn)
            assert 'env("NEXTCLOUD_USER")' not in src
            assert 'env("NEXTCLOUD_PASSWORD")' not in src


class TestSorenNcUploadVerifierUsesSharedAuth:
    @pytest.mark.asyncio
    async def test_verify_upload_passes_when_head_200_with_ctx_creds(self):
        from src.tools import verification as v

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(v, "_nc_auth", return_value=("op-user", "secret")) as auth, \
             patch.object(
                 v, "_nc_webdav_url",
                 return_value="http://nc/remote.php/dav/files/op-user/AgentSkills/a.md",
             ) as url_fn, \
             patch("src.tools.verification.httpx.AsyncClient", return_value=mock_client) as client_cls:
            passed, detail = await v.verify_upload(
                {"path": "/AgentSkills/a.md"},
                "Created file at /AgentSkills/a.md",
            )

        assert passed is True
        assert "Verified" in detail
        auth.assert_called_once_with()
        url_fn.assert_called_once_with("/AgentSkills/a.md")
        # Auth tuple must be the shared resolver output, not empty env
        client_cls.assert_called_once()
        kwargs = client_cls.call_args.kwargs
        assert kwargs.get("auth") == ("op-user", "secret")
        mock_client.request.assert_awaited_once()
        assert mock_client.request.await_args.args[0] == "HEAD"

    @pytest.mark.asyncio
    async def test_verify_mkdir_uses_shared_auth_on_propfind(self):
        from src.tools import verification as v

        mock_resp = MagicMock()
        mock_resp.status_code = 207

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(v, "_nc_auth", return_value=("admin", "admin-pass")) as auth, \
             patch.object(
                 v, "_nc_webdav_url",
                 return_value="http://nc/remote.php/dav/files/admin/AgentSkills/Ops",
             ), \
             patch("src.tools.verification.httpx.AsyncClient", return_value=mock_client) as client_cls:
            passed, detail = await v.verify_mkdir(
                {"path": "/AgentSkills/Ops"},
                "Created folder /AgentSkills/Ops",
            )

        assert passed is True
        assert "Verified" in detail
        auth.assert_called_once_with()
        assert client_cls.call_args.kwargs.get("auth") == ("admin", "admin-pass")
        assert mock_client.request.await_args.args[0] == "PROPFIND"

    @pytest.mark.asyncio
    async def test_verify_calendar_uses_caldav_helper(self):
        from src.tools import verification as v

        mock_resp = MagicMock()
        mock_resp.status_code = 207
        mock_resp.text = "SUMMARY:Standup"

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
                 v, "_nc_caldav_url",
                 return_value="http://nc/remote.php/dav/calendars/op-user/personal/",
             ) as cal_url, \
             patch.object(v, "_nc_auth", return_value=("op-user", "secret")), \
             patch("src.tools.verification.httpx.AsyncClient", return_value=mock_client):
            passed, detail = await v.verify_calendar_event(
                {"title": "Standup", "date": "2026-07-15", "calendar": "personal"},
                "Created event 'Standup'",
            )

        assert passed is True
        cal_url.assert_called_once_with("personal")
        assert mock_client.request.await_args.args[0] == "REPORT"
        assert "personal" in mock_client.request.await_args.args[1]
