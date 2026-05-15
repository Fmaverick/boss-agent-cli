import sys
import time
from typing import Any, cast

from patchright.sync_api import sync_playwright

LOGIN_PAGE_URL = "https://www.zhipin.com/web/user/"
HOME_URL = "https://www.zhipin.com/"
_DEFAULT_CDP_URL = "http://localhost:9222"

# 超时常量（秒/毫秒）
_CDP_PROBE_TIMEOUT = 3           # CDP 探测 HTTP 超时（秒）
_NAV_TIMEOUT_MS = 15000          # 页面导航超时（毫秒）
_NETWORKIDLE_GRACE_MS = 3000     # 首页进入 networkidle 的额外宽限（毫秒）
_POST_LOGIN_WAIT = 3             # 登录成功后等待 cookie 传播（秒）
_STOKEN_GENERATION_WAIT = 2      # stoken 生成等待（秒）

_PLATFORM_BROWSER_CONFIG: dict[str, dict[str, str]] = {
	"zhipin": {
		"login_page_url": LOGIN_PAGE_URL,
		"home_url": HOME_URL,
		"cookie_domain": "zhipin",
		"success_cookie": "wt2",
	},
	"zhilian": {
		"login_page_url": "https://passport.zhaopin.com/v5/login",
		"home_url": "https://www.zhaopin.com/",
		"cookie_domain": "zhaopin",
		"success_cookie": "zp_token",
	},
}


def _get_platform_config(platform: str) -> dict[str, str]:
	config = _PLATFORM_BROWSER_CONFIG.get(platform)
	if config is None:
		raise ValueError(f"unsupported platform: {platform}")
	return config


def _extract_zhilian_client_id(page: Any) -> str:
	try:
		return cast("str", page.evaluate("""
			() => {
				const keys = ["x-zp-client-id", "x_zp_client_id", "clientId"];
				for (const key of keys) {
					const value = window.localStorage.getItem(key) || window.sessionStorage.getItem(key);
					if (value) return value;
				}
				return '';
			}
		"""))
	except Exception:
		return ""


def _collect_cdp_token(ctx: Any, *, platform: str, cookie_domain: str) -> dict[str, Any]:
	"""从当前 CDP context 收集已登录 token。"""
	all_cookies = {c["name"]: c["value"] for c in ctx.cookies() if cookie_domain in c.get("domain", "")}

	extraction_page = None
	for candidate in ctx.pages:
		try:
			if cookie_domain in candidate.url:
				extraction_page = candidate
				break
		except Exception:
			continue

	if extraction_page is None:
		extraction_page = ctx.new_page()
		try:
			extraction_page.goto(_get_platform_config(platform)["home_url"], wait_until="commit", timeout=_NAV_TIMEOUT_MS)
		except Exception:
			pass

	ua = extraction_page.evaluate("navigator.userAgent")
	stoken = all_cookies.get("__zp_stoken__", "") if platform == "zhipin" else ""
	if platform == "zhipin" and not stoken:
		stoken = _extract_stoken(extraction_page)
	x_zp_client_id = _extract_zhilian_client_id(extraction_page) if platform == "zhilian" else ""

	result: dict[str, Any] = {"cookies": all_cookies, "stoken": stoken, "user_agent": ua}
	if x_zp_client_id:
		result["x_zp_client_id"] = x_zp_client_id
	return result


def _warm_home_for_runtime(page: Any, home_url: str, *, stage: str) -> None:
	"""预热首页运行时；networkidle 只尽力等待，不作为必须条件。"""
	try:
		page.goto(home_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
	except Exception as e:
		print(f"[boss] {stage}：首页导航未在预期时间完成（{e}），继续尝试提取凭证", file=sys.stderr)
	try:
		page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_GRACE_MS)
	except Exception as e:
		print(f"[boss] {stage}：首页未进入 networkidle（{e}），继续提取凭证", file=sys.stderr)


def probe_cdp(cdp_url: str | None = None) -> str | None:
	"""探测 CDP 是否可用，返回 WebSocket URL 或 None。"""
	import httpx
	base = cdp_url or _DEFAULT_CDP_URL
	try:
		# 本地 CDP 端口不应走环境代理；部分用户代理配置会让 localhost 探测超时。
		resp = httpx.get(f"{base}/json/version", timeout=_CDP_PROBE_TIMEOUT, trust_env=False)
		return cast("str | None", resp.json().get("webSocketDebuggerUrl"))
	except (httpx.HTTPError, ValueError, KeyError):
		return None


def _create_cdp_page(cdp_url: str | None, target_url: str) -> tuple[str, str, str]:
	"""Create a temporary page through Chrome's raw CDP HTTP endpoint."""
	import json
	import urllib.request

	base = (cdp_url or _DEFAULT_CDP_URL).rstrip("/")
	create_url = f"{base}/json/new?{target_url}"
	try:
		req = urllib.request.Request(create_url, method="PUT")
		with urllib.request.urlopen(req, timeout=5) as resp:
			payload = json.load(resp)
			return base, cast("str", payload["id"]), cast("str", payload["webSocketDebuggerUrl"])
	except Exception as exc:
		raise RuntimeError(f"cannot create CDP page: {exc}") from exc


def _close_cdp_page(cdp_http_url: str, target_id: str) -> None:
	import urllib.request

	try:
		req = urllib.request.Request(f"{cdp_http_url}/json/close/{target_id}", method="GET")
		with urllib.request.urlopen(req, timeout=5):
			pass
	except Exception:
		pass


def _raw_cdp_evaluate(cdp_url: str | None, page_url: str, expression: str, *, timeout: float = 20.0, settle_seconds: float = 1.0) -> Any:
	"""Evaluate JavaScript in a temporary page using raw CDP, bypassing patchright attach."""
	import json

	import websockets.sync.client as ws_client

	cdp_http_url, target_id, target_ws = _create_cdp_page(cdp_url, page_url)
	try:
		with ws_client.connect(target_ws, max_size=8 * 1024 * 1024) as ws:
			ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
			ws.send(json.dumps({"id": 2, "method": "Runtime.enable"}))
			ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": page_url}}))

			deadline = time.time() + timeout
			settle_deadline = min(deadline, time.time() + settle_seconds)
			while time.time() < settle_deadline:
				try:
					raw = ws.recv(timeout=max(0.1, settle_deadline - time.time()))
				except TimeoutError:
					break
				msg = json.loads(raw)
				if msg.get("method") in ("Page.domContentEventFired", "Page.loadEventFired"):
					break

			ws.send(json.dumps({
				"id": 4,
				"method": "Runtime.evaluate",
				"params": {
					"expression": expression,
					"returnByValue": True,
					"awaitPromise": True,
				},
			}))
			while time.time() < deadline:
				raw = ws.recv(timeout=max(0.1, deadline - time.time()))
				msg = json.loads(raw)
				if msg.get("id") != 4:
					continue
				if err := msg.get("error"):
					raise RuntimeError(f"CDP Runtime.evaluate error: {err}")
				payload = msg.get("result", {})
				if exc_details := payload.get("exceptionDetails"):
					raise RuntimeError(
						f"JS exception: {exc_details.get('text')} - "
						f"{exc_details.get('exception', {}).get('description', '')[:300]}"
					)
				result = payload.get("result", {})
				return result.get("value", result)
			raise RuntimeError(f"CDP Runtime.evaluate timed out after {timeout:.0f}s")
	finally:
		_close_cdp_page(cdp_http_url, target_id)


def login_via_cdp(*, cdp_url: str | None = None, timeout: int = 120, platform: str = "zhipin") -> dict[str, Any]:
	"""
	通过 CDP 连接用户 Chrome 扫码登录。
	返回 token dict，失败抛异常。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用，请先运行 boss-chrome 启动带调试端口的 Chrome")

	print("[boss] 正在 CDP Chrome 中打开登录页...", file=sys.stderr)
	pw = sync_playwright().start()
	try:
		browser = pw.chromium.connect_over_cdp(ws_url)
		ctx = browser.contexts[0] if browser.contexts else browser.new_context()

		# 若用户已在 CDP Chrome 中登录，直接提取当前会话，避免重复打开登录页造成卡顿。
		try:
			existing_cookies = ctx.cookies()
		except Exception:
			existing_cookies = []
		if any(c["name"] == success_cookie and cookie_domain in c.get("domain", "") for c in existing_cookies):
			print("[boss] 检测到现有登录态，直接提取凭证...", file=sys.stderr)
			return _collect_cdp_token(ctx, platform=platform, cookie_domain=cookie_domain)

		page = ctx.new_page()
		try:
			try:
				page.goto(
					login_page_url,
					wait_until="commit", timeout=_NAV_TIMEOUT_MS,
				)
			except Exception:
				pass

			print(f"[boss] 请在 Chrome 中扫码登录，等待中...（超时 {timeout}s）", file=sys.stderr)

			for i in range(timeout):
				time.sleep(1)
				cookies = ctx.cookies()
				success = [c for c in cookies if c["name"] == success_cookie and cookie_domain in c.get("domain", "")]
				if success:
					print("[boss] 检测到登录成功！", file=sys.stderr)
					break
				if i > 0 and i % 15 == 0:
					print(f"[boss] 等待中... {i}s", file=sys.stderr)
			else:
				raise TimeoutError(f"CDP 扫码登录超时（{timeout}s）")

			return _collect_cdp_token(ctx, platform=platform, cookie_domain=cookie_domain)
		finally:
			try:
				page.close()
			except Exception:
				pass
	finally:
		pw.stop()


def sync_token_from_cdp(*, cdp_url: str | None = None, platform: str = "zhipin") -> dict[str, Any]:
	"""从当前已连接的 CDP Chrome 同步已登录会话，不触发登录流程。"""
	config = _get_platform_config(platform)
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用，请先启动带调试端口的 Chrome")

	pw = sync_playwright().start()
	try:
		browser = pw.chromium.connect_over_cdp(ws_url)
		ctx = browser.contexts[0] if browser.contexts else browser.new_context()
		cookies = ctx.cookies()
		if not any(c["name"] == success_cookie and cookie_domain in c.get("domain", "") for c in cookies):
			raise RuntimeError("当前 CDP Chrome 中未检测到已登录会话，请先在该 Chrome 中登录")
		return _collect_cdp_token(ctx, platform=platform, cookie_domain=cookie_domain)
	finally:
		pw.stop()


def login_via_browser(*, timeout: int = 120, platform: str = "zhipin") -> dict[str, Any]:
	"""
	使用 patchright（Playwright 反检测 fork）打开登录页。
	双重检测登录成功：监听 API 响应 + 轮询 wt2 cookie。
	"""
	config = _get_platform_config(platform)
	login_page_url = config["login_page_url"]
	home_url = config["home_url"]
	cookie_domain = config["cookie_domain"]
	success_cookie = config["success_cookie"]
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=False)
		context = browser.new_context(
			viewport={"width": 1280, "height": 800},
			locale="zh-CN",
			timezone_id="Asia/Shanghai",
		)
		page = context.new_page()

		page.goto(login_page_url, wait_until="domcontentloaded")
		print("已打开 BOSS 直聘登录页。", file=sys.stderr)
		print(f"请扫码或手机号登录（超时 {timeout} 秒）...", file=sys.stderr)

		# 双重检测：API 响应 或 wt2 cookie 出现，任一触发即认为登录成功
		login_detected = False

		def _on_response(response: Any) -> None:
			nonlocal login_detected
			url = response.url
			if (url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/loginConfirm")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/qrcode/dispatcher")
				or url.startswith("https://www.zhipin.com/wapi/zppassport/login/phoneV2")):
				login_detected = True

		page.on("response", _on_response)

		deadline = time.time() + timeout
		while time.time() < deadline and not login_detected:
			# 也通过 cookie 检测（覆盖 API 匹配不上的情况）
			try:
				cookies_list = context.cookies()
				if any(c["name"] == success_cookie and cookie_domain in c.get("domain", "") for c in cookies_list):
					login_detected = True
					break
			except Exception:
				pass
			time.sleep(1)

		if not login_detected:
			browser.close()
			raise TimeoutError(f"扫码登录超时（{timeout}秒）")

		print("检测到登录成功，正在提取凭证...", file=sys.stderr)
		time.sleep(_POST_LOGIN_WAIT)

		# 跳转主站提取完整 cookies 和 stoken
		_warm_home_for_runtime(page, home_url, stage="登录后回到首页")

		cookies_list = context.cookies()
		cookies = {c["name"]: c["value"] for c in cookies_list if cookie_domain in c.get("domain", "")}
		user_agent = page.evaluate("navigator.userAgent")
		stoken = _extract_stoken(page) if platform == "zhipin" else ""
		x_zp_client_id = _extract_zhilian_client_id(page) if platform == "zhilian" else ""

		browser.close()

	result: dict[str, Any] = {
		"cookies": cookies,
		"stoken": stoken,
		"user_agent": user_agent,
	}
	if x_zp_client_id:
		result["x_zp_client_id"] = x_zp_client_id
	return result


def refresh_stoken_via_cdp(cdp_url: str | None = None) -> str:
	"""通过 CDP Chrome 刷新 stoken（指纹一致，不会被拒）。"""
	ws_url = probe_cdp(cdp_url)
	if not ws_url:
		raise ConnectionError("CDP 不可用")

	stoken = cast("str", _raw_cdp_evaluate(
		cdp_url,
		HOME_URL,
		"""
		new Promise((resolve) => {
			const read = () => {
				const match = document.cookie.match(/__zp_stoken__=([^;]+)/);
				if (match) return match[1];
				if (window.__zp_stoken__) return window.__zp_stoken__;
				return '';
			};
			const existing = read();
			if (existing) {
				resolve(existing);
				return;
			}
			setTimeout(() => resolve(read()), 2000);
		})
		""",
		timeout=20,
	))

	if not stoken:
		raise RuntimeError("CDP 刷新 stoken 失败：页面未生成 stoken")
	return stoken


def refresh_stoken(cookies: dict[str, Any], user_agent: str) -> str:
	"""通过 headless patchright 刷新 stoken（兜底方案）。"""
	with sync_playwright() as p:
		browser = p.chromium.launch(headless=True)
		context = browser.new_context(user_agent=user_agent)
		context.add_cookies([
			{"name": name, "value": value, "domain": ".zhipin.com", "path": "/"}
			for name, value in cookies.items()
		])
		page = context.new_page()
		_warm_home_for_runtime(page, HOME_URL, stage="刷新 stoken")
		stoken = _extract_stoken(page)
		browser.close()

	return stoken


def _extract_stoken(page: Any) -> str:
	try:
		stoken = page.evaluate("""
			() => {
				const match = document.cookie.match(/__zp_stoken__=([^;]+)/);
				return match ? match[1] : '';
			}
		""")
		if not stoken:
			stoken = page.evaluate("() => window.__zp_stoken__ || ''")
		return cast("str", stoken)
	except Exception:
		return ""
