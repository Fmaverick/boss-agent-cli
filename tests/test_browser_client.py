import json
from unittest.mock import patch, MagicMock

import httpx

from boss_agent_cli.api.browser_client import (
	CDP_DEFAULT_URL,
	_CDP_CONNECT_TIMEOUT_MS,
	HOME_URL,
	_HEADLESS_NETWORKIDLE_GRACE_MS,
	_NAV_TIMEOUT_MS,
	_SEARCH_RESPONSE_TIMEOUT_MS,
	BrowserSession,
)


def test_browser_session_defaults():
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="test-ua")
	assert session._is_cdp is False
	assert session._started is False
	assert session._cookies == {"wt2": "abc"}


def test_fetch_ws_url_success():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}
		mock_get.return_value = mock_resp
		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")
		assert ws == "ws://127.0.0.1:9222/devtools/browser/abc"
		mock_get.assert_called_once_with(
			"http://127.0.0.1:9222/json/version",
			timeout=3,
			trust_env=False,
		)


def test_fetch_ws_url_failure():
	with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")
		assert ws is None


def test_fetch_ws_url_invalid_json_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.side_effect = ValueError("invalid json")
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_fetch_ws_url_missing_websocket_debugger_url_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = {"Browser": "Chrome"}
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_fetch_ws_url_non_object_json_returns_none():
	with patch("httpx.get") as mock_get:
		mock_resp = MagicMock()
		mock_resp.json.return_value = ["not", "a", "devtools", "object"]
		mock_get.return_value = mock_resp

		ws = BrowserSession._fetch_ws_url("http://127.0.0.1:9222")

		assert ws is None


def test_read_devtools_active_port_missing(tmp_path):
	with patch("boss_agent_cli.api.browser_client._CHROME_USER_DATA_CANDIDATES", [tmp_path / "nonexistent"]):
		ws = BrowserSession._read_devtools_active_port()
		assert ws is None


def test_read_devtools_active_port_found(tmp_path):
	port_file = tmp_path / "DevToolsActivePort"
	port_file.write_text("9222\n/devtools/browser/test-id\n")
	with patch("boss_agent_cli.api.browser_client._CHROME_USER_DATA_CANDIDATES", [tmp_path]):
		ws = BrowserSession._read_devtools_active_port()
		assert ws == "ws://127.0.0.1:9222/devtools/browser/test-id"


def test_close_cdp_mode_reused_context_not_closed():
	"""CDP 复用用户 context 时 close() 只关闭 page，不关闭 context"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = False  # 复用的 context
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._page.close.assert_called_once()
	session._context.close.assert_not_called()  # 不关闭用户的 context
	session._browser.close.assert_not_called()


def test_close_cdp_mode_own_context_closed():
	"""CDP 自建 context 时 close() 关闭 page 和 context"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = True  # 自建的 context
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._page.close.assert_called_once()
	session._context.close.assert_called_once()


def test_close_headless_mode_closes_browser():
	"""Headless 模式下 close() 关闭整个 browser"""
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = False
	session._started = True
	session._page = MagicMock()
	session._browser = MagicMock()
	session._pw = MagicMock()

	session.close()

	session._browser.close.assert_called_once()


def test_close_is_idempotent_when_cdp_resources_are_partial_and_raise():
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = True
	session._own_context = True
	session._started = True
	session._page = MagicMock()
	session._context = MagicMock()
	session._pw = MagicMock()
	session._page.close.side_effect = RuntimeError("page already closed")
	session._context.close.side_effect = RuntimeError("context already closed")
	session._pw.stop.side_effect = RuntimeError("playwright already stopped")

	session.close()
	session.close()

	assert session._started is False
	assert session._page.close.call_count == 2
	assert session._context.close.call_count == 2
	assert session._pw.stop.call_count == 2


def test_close_is_idempotent_when_headless_resources_are_partial_and_raise():
	session = BrowserSession(cookies={}, user_agent="")
	session._is_cdp = False
	session._started = True
	session._browser = MagicMock()
	session._pw = MagicMock()
	session._browser.close.side_effect = RuntimeError("browser already closed")
	session._pw.stop.side_effect = RuntimeError("playwright already stopped")

	session.close()
	session.close()

	assert session._started is False
	assert session._browser.close.call_count == 2
	assert session._pw.stop.call_count == 2


def test_try_connect_reuses_existing_context():
	"""CDP 连接应复用用户现有 context（规避 automation 检测）"""
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	mock_browser.contexts = [mock_user_context]
	mock_page = MagicMock()
	mock_user_context.new_page.return_value = mock_page

	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._is_cdp is True
	assert session._own_context is False  # 复用，非自建
	assert session._context is mock_user_context  # 直接使用用户 context
	# 验证：没有创建新 context
	mock_browser.new_context.assert_not_called()
	# 验证：page 在用户 context 中创建
	mock_user_context.new_page.assert_called_once()


def test_try_connect_reuses_existing_platform_page():
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	mock_existing_page = MagicMock()
	mock_existing_page.url = "https://www.zhipin.com/shanghai/"
	mock_user_context.pages = [mock_existing_page]
	mock_browser.contexts = [mock_user_context]

	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._page is mock_existing_page
	mock_user_context.new_page.assert_not_called()


def test_try_connect_creates_new_context_when_none_exists():
	"""CDP 连接无已存在 context 时创建新 context 并注入 cookies"""
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_browser.contexts = []  # 无已存在 context
	mock_new_context = MagicMock()
	mock_browser.new_context.return_value = mock_new_context
	mock_page = MagicMock()
	mock_new_context.new_page.return_value = mock_page

	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://localhost:9222/test")

	assert result is True
	assert session._is_cdp is True
	assert session._own_context is True  # 自建
	# 验证：创建了新 context
	mock_browser.new_context.assert_called_once()
	# 验证：cookies 被注入
	mock_new_context.add_cookies.assert_called_once()
	cookies_arg = mock_new_context.add_cookies.call_args[0][0]
	assert any(c["name"] == "wt2" for c in cookies_arg)


def test_start_headless_tolerates_networkidle_timeout():
	"""Headless 预热不应因 networkidle 等待超时而直接失败。"""
	logger = MagicMock()
	session = BrowserSession(cookies={"wt2": "abc"}, user_agent="UA", logger=logger)
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_context = MagicMock()
	mock_page = MagicMock()
	mock_page.wait_for_load_state.side_effect = Exception("Timeout 30000ms exceeded")
	mock_context.new_page.return_value = mock_page
	mock_browser.new_context.return_value = mock_context
	session._pw.chromium.launch.return_value = mock_browser

	session._start_headless()

	assert session._started is True
	assert session._is_cdp is False
	mock_page.goto.assert_called_once_with(
		HOME_URL,
		wait_until="domcontentloaded",
		timeout=_NAV_TIMEOUT_MS,
	)
	mock_page.wait_for_load_state.assert_called_once_with(
		"networkidle",
		timeout=_HEADLESS_NETWORKIDLE_GRACE_MS,
	)
	logger.info.assert_any_call("[boss] CDP 不可用（提示：需以 --remote-debugging-port=9222 启动 Chrome），降级到 headless patchright")
	assert any(
		"headless 首页未进入 networkidle" in call.args[0]
		for call in logger.info.call_args_list
	)


def test_ensure_started_falls_back_to_patchright_when_bridge_and_cdp_fail():
	session = BrowserSession(cookies={}, user_agent="")
	mock_pw = MagicMock()
	sentinel = {"headless_started": False}

	def mark_headless_started():
		sentinel["headless_started"] = True
		session._started = True

	with (
		patch.object(session, "_try_bridge", return_value=False) as mock_try_bridge,
		patch("boss_agent_cli.api.browser_client.sync_playwright") as mock_sync_playwright,
		patch.object(session, "_try_cdp", return_value=False) as mock_try_cdp,
		patch.object(session, "_start_headless", side_effect=mark_headless_started) as mock_start_headless,
	):
		mock_sync_playwright.return_value.start.return_value = mock_pw

		session._ensure_started()

	assert sentinel["headless_started"] is True
	assert session._started is True
	assert session._pw is mock_pw
	mock_try_bridge.assert_called_once()
	mock_sync_playwright.assert_called_once()
	mock_try_cdp.assert_called_once()
	mock_start_headless.assert_called_once()


def test_try_cdp_attempts_http_ws_and_devtools_urls_before_falling_back():
	session = BrowserSession(cookies={}, user_agent="", cdp_url="http://127.0.0.1:9333")

	with (
		patch.object(session, "_try_connect", return_value=False) as mock_try_connect,
		patch.object(BrowserSession, "_fetch_ws_url", side_effect=["ws://127.0.0.1:9333/devtools/browser/custom", "ws://127.0.0.1:9222/devtools/browser/default"]) as mock_fetch_ws_url,
		patch.object(BrowserSession, "_read_devtools_active_port", return_value="ws://127.0.0.1:9222/devtools/browser/file") as mock_read_port,
	):
		result = session._try_cdp()

	assert result is False
	mock_read_port.assert_called_once()
	assert [call.args[0] for call in mock_try_connect.call_args_list] == [
		"ws://127.0.0.1:9333/devtools/browser/custom",
		"http://127.0.0.1:9333",
		"ws://127.0.0.1:9222/devtools/browser/default",
		CDP_DEFAULT_URL,
		"ws://127.0.0.1:9222/devtools/browser/file",
	]
	assert [call.args[0] for call in mock_fetch_ws_url.call_args_list] == [
		"http://127.0.0.1:9333",
		CDP_DEFAULT_URL,
	]


def test_try_connect_uses_explicit_cdp_timeout():
	session = BrowserSession(cookies={}, user_agent="")
	session._pw = MagicMock()

	mock_browser = MagicMock()
	mock_user_context = MagicMock()
	mock_user_context.pages = []
	mock_page = MagicMock()
	mock_user_context.new_page.return_value = mock_page
	mock_browser.contexts = [mock_user_context]
	session._pw.chromium.connect_over_cdp.return_value = mock_browser

	result = session._try_connect("ws://127.0.0.1:9222/devtools/browser/test")

	assert result is True
	session._pw.chromium.connect_over_cdp.assert_called_once_with(
		"ws://127.0.0.1:9222/devtools/browser/test",
		timeout=_CDP_CONNECT_TIMEOUT_MS,
	)


def test_request_returns_browser_evaluation_json_and_marks_throttle():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._page = MagicMock()
	session._throttle = MagicMock()
	expected = {"code": 0, "zpData": {"jobs": []}}
	mock_response = MagicMock()
	mock_response.json.return_value = expected
	mock_cm = MagicMock()
	mock_cm.__enter__.return_value = mock_cm
	mock_cm.__exit__.return_value = False
	mock_cm.value = mock_response
	session._page.expect_response.return_value = mock_cm

	with patch.object(session, "_search_request_via_raw_cdp", return_value=None):
		result = session.request(
			"POST",
			"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
			data={"query": "python", "page": 1, "city": "101020100", "pageSize": 15, "scene": 1},
		)

	assert result == expected
	session._throttle.wait.assert_called_once()
	session._throttle.mark.assert_called_once()
	session._page.expect_response.assert_called_once()
	session._page.goto.assert_called_once()


def test_request_prefers_raw_cdp_search_before_patchright_attach():
	session = BrowserSession(cookies={}, user_agent="")
	session._throttle = MagicMock()

	with (
		patch.object(session, "_search_request_via_raw_cdp", return_value={"code": 0, "zpData": {"jobList": []}}) as mock_raw_cdp,
		patch.object(session, "_ensure_started") as mock_ensure_started,
	):
		result = session.request(
			"POST",
			"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
			data={"query": "AIGC 产品经理", "page": 1, "city": "101020100", "pageSize": 15, "scene": 1},
		)

	assert result["code"] == 0
	mock_raw_cdp.assert_called_once()
	mock_ensure_started.assert_not_called()
	session._throttle.wait.assert_called_once()
	session._throttle.mark.assert_called_once()


def test_raw_cdp_search_capture_skips_empty_and_code_37_bodies():
	from boss_agent_cli.api import browser_client

	class FakeWS:
		def __enter__(self):
			return self

		def __exit__(self, exc_type, exc, tb):
			return False

		def send(self, raw):
			payload = json.loads(raw)
			if payload.get("method") == "Network.getResponseBody":
				self._queued_id = payload["id"]

		def recv(self, timeout=None):
			if hasattr(self, "_queued_id"):
				msg_id = self._queued_id
				del self._queued_id
				if msg_id == 1001:
					body = ""
				elif msg_id == 1002:
					body = '{"code":37,"message":"expired","zpData":{}}'
				else:
					body = '{"code":0,"message":"Success","zpData":{"jobList":[{"jobName":"AI 产品经理"}]}}'
				return json.dumps({"id": msg_id, "result": {"body": body}})
			self._response_index = getattr(self, "_response_index", 0) + 1
			return json.dumps({
				"method": "Network.responseReceived",
				"params": {
					"requestId": f"request-{self._response_index}",
					"response": {"url": "https://www.zhipin.com/wapi/zpgeek/search/joblist.json?_=1"},
				},
			})

	with (
		patch("urllib.request.urlopen") as mock_urlopen,
		patch("websockets.sync.client.connect", return_value=FakeWS()),
		patch("json.load", return_value={"id": "target", "webSocketDebuggerUrl": "ws://target"}),
	):
		mock_urlopen.return_value.__enter__.return_value = MagicMock()
		result = browser_client._cdp_capture_search_response(
			"http://127.0.0.1:9222",
			"https://www.zhipin.com/web/geek/jobs?query=AI",
			"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
			timeout=1,
		)

	assert result["code"] == 0
	assert result["zpData"]["jobList"][0]["jobName"] == "AI 产品经理"


def test_request_prefers_raw_cdp_detail_before_patchright_attach():
	session = BrowserSession(cookies={}, user_agent="")
	session._throttle = MagicMock()

	with (
		patch.object(session, "_detail_request_via_raw_cdp", return_value={"code": 0, "zpData": {"jobInfo": {}}}) as mock_raw_cdp,
		patch.object(session, "_ensure_started") as mock_ensure_started,
	):
		result = session.request(
			"GET",
			"https://www.zhipin.com/wapi/zpgeek/job/detail.json",
			params={"encryptJobId": "encrypted_j1"},
		)

	assert result["code"] == 0
	mock_raw_cdp.assert_called_once()
	mock_ensure_started.assert_not_called()
	session._throttle.wait.assert_called_once()
	session._throttle.mark.assert_called_once()


def test_search_request_navigates_and_retries_once_on_code_37():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._page = MagicMock()
	mock_response_1 = MagicMock()
	mock_response_1.json.return_value = {"code": 37, "message": "expired", "zpData": {}}
	mock_response_2 = MagicMock()
	mock_response_2.json.return_value = {"code": 0, "message": "Success", "zpData": {"jobList": [{"jobName": "AI产品经理"}]}}
	mock_cm_1 = MagicMock()
	mock_cm_1.__enter__.return_value = mock_cm_1
	mock_cm_1.__exit__.return_value = False
	mock_cm_1.value = mock_response_1
	mock_cm_2 = MagicMock()
	mock_cm_2.__enter__.return_value = mock_cm_2
	mock_cm_2.__exit__.return_value = False
	mock_cm_2.value = mock_response_2
	session._page.expect_response.side_effect = [mock_cm_1, mock_cm_2]

	result = session._search_request(
		"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
		{"query": "AI 产品经理", "city": "101210100", "page": 1, "pageSize": 15, "scene": 1},
		"https://www.zhipin.com/web/geek/job",
	)

	assert result["code"] == 0
	assert session._page.goto.call_count == 2
	assert session._page.expect_response.call_count == 2


def test_search_request_falls_back_to_in_page_fetch_when_page_response_times_out():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._page = MagicMock()
	session._page.expect_response.side_effect = Exception("Timeout 8000ms exceeded")
	session._page.evaluate.return_value = {"code": 0, "zpData": {"jobList": [{"jobName": "AIGC产品经理"}]}}

	result = session._search_request(
		"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
		{"query": "AIGC 产品经理", "city": "101020100", "page": 1, "pageSize": 15, "scene": 1},
		"https://www.zhipin.com/web/geek/job",
	)

	assert result["code"] == 0
	assert session._page.expect_response.call_count == 1
	session._page.goto.assert_called_once()
	session._page.evaluate.assert_called_once()


def test_search_request_uses_shorter_page_response_timeout():
	session = BrowserSession(cookies={}, user_agent="")
	session._started = True
	session._page = MagicMock()
	mock_response = MagicMock()
	mock_response.json.return_value = {"code": 0, "zpData": {"jobList": []}}
	mock_cm = MagicMock()
	mock_cm.__enter__.return_value = mock_cm
	mock_cm.__exit__.return_value = False
	mock_cm.value = mock_response
	session._page.expect_response.return_value = mock_cm

	session._search_request(
		"https://www.zhipin.com/wapi/zpgeek/search/joblist.json",
		{"query": "AI 产品经理", "city": "101210100", "page": 1, "pageSize": 15, "scene": 1},
		"https://www.zhipin.com/web/geek/job",
	)

	assert session._page.expect_response.call_args.kwargs["timeout"] == _SEARCH_RESPONSE_TIMEOUT_MS
