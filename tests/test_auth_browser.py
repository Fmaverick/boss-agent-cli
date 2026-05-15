from unittest.mock import MagicMock, patch

import pytest

from boss_agent_cli.auth.browser import (
	HOME_URL,
	LOGIN_PAGE_URL,
	_NAV_TIMEOUT_MS,
	_NETWORKIDLE_GRACE_MS,
	probe_cdp,
	login_via_cdp,
	login_via_browser,
	refresh_stoken,
	refresh_stoken_via_cdp,
)


def _mock_playwright_context(mock_browser: MagicMock) -> MagicMock:
	mock_chromium = MagicMock()
	mock_chromium.launch.return_value = mock_browser
	mock_playwright = MagicMock()
	mock_playwright.chromium = mock_chromium
	mock_context_manager = MagicMock()
	mock_context_manager.__enter__ = MagicMock(return_value=mock_playwright)
	mock_context_manager.__exit__ = MagicMock(return_value=False)
	return mock_context_manager


def _mock_cdp_playwright(mock_context: MagicMock) -> tuple[MagicMock, MagicMock, MagicMock]:
	mock_page = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.contexts = [mock_context]

	mock_playwright = MagicMock()
	mock_playwright.chromium.connect_over_cdp.return_value = mock_browser

	mock_launcher = MagicMock()
	mock_launcher.start.return_value = mock_playwright
	return mock_launcher, mock_playwright, mock_page


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_stops_playwright_on_timeout(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_context.cookies.return_value = []
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(TimeoutError):
			login_via_cdp(timeout=1)

	mock_page.close.assert_called_once()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_stops_playwright_when_user_agent_extraction_fails(mock_sleep, mock_probe_cdp):
	mock_context = MagicMock()
	mock_runtime_page = MagicMock()
	mock_runtime_page.url = "https://www.zhipin.com/shanghai/"
	mock_runtime_page.evaluate.side_effect = RuntimeError("user agent unavailable")
	mock_context.pages = [mock_runtime_page]
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]
	mock_launcher, mock_playwright, mock_page = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		with pytest.raises(RuntimeError, match="user agent unavailable"):
			login_via_cdp(timeout=1)

	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_cdp_extracts_stoken_from_cookie_and_existing_page(mock_sleep, mock_probe_cdp):
	mock_login_page = MagicMock()
	mock_login_page.url = "https://www.zhipin.com/web/user/"
	mock_runtime_page = MagicMock()
	mock_runtime_page.url = "https://www.zhipin.com/shanghai/"
	mock_runtime_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_login_page
	mock_context.pages = [mock_runtime_page, mock_login_page]
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[
			{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
			{"name": "__zp_stoken__", "value": "stoken-from-cookie", "domain": ".zhipin.com"},
		],
	]

	mock_launcher, mock_playwright, _ = _mock_cdp_playwright(mock_context)

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	assert result["stoken"] == "stoken-from-cookie"
	assert result["user_agent"] == "UA"
	mock_runtime_page.evaluate.assert_called_once_with("navigator.userAgent")
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
def test_login_via_cdp_reuses_existing_logged_in_context(mock_probe_cdp):
	mock_runtime_page = MagicMock()
	mock_runtime_page.url = "https://www.zhipin.com/shanghai/"
	mock_runtime_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.pages = [mock_runtime_page]
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[
			{"name": "wt2", "value": "token", "domain": ".zhipin.com"},
			{"name": "__zp_stoken__", "value": "stoken-from-cookie", "domain": ".zhipin.com"},
		],
	]

	mock_browser = MagicMock()
	mock_browser.contexts = [mock_context]

	mock_playwright = MagicMock()
	mock_playwright.chromium.connect_over_cdp.return_value = mock_browser

	mock_launcher = MagicMock()
	mock_launcher.start.return_value = mock_playwright

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=mock_launcher):
		result = login_via_cdp(timeout=1)

	assert result["stoken"] == "stoken-from-cookie"
	assert result["user_agent"] == "UA"
	mock_context.new_page.assert_not_called()
	mock_playwright.stop.assert_called_once()


@patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken")
@patch("boss_agent_cli.auth.browser.time.sleep", return_value=None)
def test_login_via_browser_tolerates_networkidle_timeout(mock_sleep, mock_extract_stoken):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")
	mock_page.evaluate.return_value = "UA"

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page
	mock_context.cookies.side_effect = [
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
		[{"name": "wt2", "value": "token", "domain": ".zhipin.com"}],
	]

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = login_via_browser(timeout=2, platform="zhipin")

	assert result["stoken"] == "fresh-stoken"
	assert result["user_agent"] == "UA"
	mock_browser.new_context.assert_called_once()
	mock_page.goto.assert_any_call(LOGIN_PAGE_URL, wait_until="domcontentloaded")
	mock_page.goto.assert_any_call(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_extract_stoken.assert_called_once_with(mock_page)
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser._extract_stoken", return_value="fresh-stoken")
def test_refresh_stoken_tolerates_networkidle_timeout(mock_extract_stoken):
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")

	mock_context = MagicMock()
	mock_context.new_page.return_value = mock_page

	mock_browser = MagicMock()
	mock_browser.new_context.return_value = mock_context

	with patch("boss_agent_cli.auth.browser.sync_playwright", return_value=_mock_playwright_context(mock_browser)):
		result = refresh_stoken({"wt2": "cookie"}, "UA")

	assert result == "fresh-stoken"
	mock_browser.new_context.assert_called_once_with(user_agent="UA")
	mock_context.add_cookies.assert_called_once()
	mock_page.goto.assert_called_once_with(HOME_URL, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	mock_page.wait_for_load_state.assert_called_once_with("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	mock_extract_stoken.assert_called_once_with(mock_page)
	mock_browser.close.assert_called_once()


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser._raw_cdp_evaluate", return_value="fresh-cdp-token")
def test_refresh_stoken_via_cdp_uses_raw_cdp_evaluate(mock_raw_eval, mock_probe_cdp):
	result = refresh_stoken_via_cdp("http://localhost:9222")

	assert result == "fresh-cdp-token"
	mock_probe_cdp.assert_called_once_with("http://localhost:9222")
	assert mock_raw_eval.call_args.args[0] == "http://localhost:9222"
	assert mock_raw_eval.call_args.args[1] == HOME_URL
	assert mock_raw_eval.call_args.kwargs["timeout"] == 20


@patch("boss_agent_cli.auth.browser.probe_cdp", return_value="ws://localhost/devtools/browser")
@patch("boss_agent_cli.auth.browser._raw_cdp_evaluate", return_value="")
def test_refresh_stoken_via_cdp_raises_when_stoken_missing(mock_raw_eval, mock_probe_cdp):
	with pytest.raises(RuntimeError, match="页面未生成 stoken"):
		refresh_stoken_via_cdp("http://localhost:9222")


@patch("httpx.get")
def test_probe_cdp_ignores_env_proxy(mock_get):
	mock_get.return_value.json.return_value = {"webSocketDebuggerUrl": "ws://localhost/devtools/browser"}

	result = probe_cdp("http://localhost:9222")

	assert result == "ws://localhost/devtools/browser"
	mock_get.assert_called_once_with(
		"http://localhost:9222/json/version",
		timeout=3,
		trust_env=False,
	)
