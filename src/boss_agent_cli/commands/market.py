from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any

import click
from rich.panel import Panel
from rich.table import Table

from boss_agent_cli.api.endpoints import CITY_CODES
from boss_agent_cli.api.models import JobItem
from boss_agent_cli.auth.manager import AuthManager
from boss_agent_cli.commands._platform import get_platform_instance
from boss_agent_cli.display import console, handle_auth_errors, handle_error_output, handle_output
from boss_agent_cli.search_filters import parse_salary_range

DEFAULT_AI_PM_CITIES = ("杭州", "上海")
DEFAULT_AI_PM_QUERIES = (
	"AI 产品经理",
	"AIGC 产品经理",
	"大模型产品经理",
	"智能体产品经理",
	"Agent 产品经理",
	"AI应用产品经理",
	"产品经理",
)

PROFILE_POINTS = (
	"B 端和 C 端产品经验都能覆盖",
	"有 0-1 上线经验，能把需求拆到 PRD、流程和版本推进",
	"理解 AI / LLM / AIGC / 智能体相关项目",
	"做过运营，能补用户调研、数据复盘和业务闭环",
)

PRODUCT_SEQUENCE_TERMS = (
	"产品经理",
	"产品专家",
	"产品负责人",
	"产品助理",
	"产品专员",
	"产品管培生",
	"产品总监",
)

NON_PM_TITLE_TERMS = (
	"产品运营",
	"内容运营",
	"用户运营",
	"活动运营",
	"社群运营",
	"工程师",
	"开发工程师",
	"算法工程师",
	"测试",
	"设计师",
	"销售",
	"客服",
	"实施顾问",
)

AI_RELEVANCE_TERMS = (
	"ai",
	"aigc",
	"llm",
	"agent",
	"chatgpt",
	"大模型",
	"智能体",
	"人工智能",
	"生成式",
	"机器人",
	"语义",
	"语音",
	"视觉",
	"机器学习",
	"算法",
	"prompt",
	"rag",
	"workflow",
	"工作流",
	"coze",
	"dify",
	"n8n",
)

HIGH_VALUE_SKILLS = (
	"B端产品",
	"C端产品",
	"AI产品",
	"AI机器人",
	"语义类AI",
	"语音类AI",
	"视觉类AI",
	"机器学习类AI",
	"智能体",
	"SaaS",
	"ToB",
	"Prompt Engineering",
	"Workflow",
	"PRD",
	"用户调研",
	"数据分析",
)

SKILL_IGNORE_TERMS = {
	"在校/应届",
	"经验不限",
	"1-3年",
	"3-5年",
	"5-10年",
	"本科",
	"大专",
	"硕士",
	"博士",
	"学历不限",
}

THEMES = (
	{
		"key": "agent_workflow",
		"name": "Agent / Workflow / Skill 编排",
		"keywords": ("agent", "智能体", "workflow", "工作流", "skill", "技能", "coze", "dify", "n8n"),
		"meaning": "岗位更看重把大模型能力嵌进真实业务流程，而不只是做聊天入口。",
		"positioning": "突出你对智能体/AIGC项目的理解，并把 0-1 上线经验讲成“从场景拆解到流程编排再到上线验证”。",
	},
	{
		"key": "llm_productization",
		"name": "LLM 能力边界 / Prompt / 模型产品化",
		"keywords": ("llm", "大模型", "prompt", "模型", "api", "badcase", "rag", "上下文"),
		"meaning": "不少岗位要求产品经理能理解模型调用、效果评估和 badcase 归因。",
		"positioning": "强调你不是只会写 PRD，而是能和算法/工程讨论模型能力边界、效果指标和迭代优先级。",
	},
	{
		"key": "tob_delivery",
		"name": "ToB / SaaS / 客户共创",
		"keywords": ("tob", "b端", "saas", "客户", "交付", "解决方案", "行业场景", "企业"),
		"meaning": "上海和杭州都有一批岗位在找能把 AI 做进企业场景的人。",
		"positioning": "把你的 B 端经验、流程拆解能力和推进上线能力放到前面，补一句能承接客户不确定性。",
	},
	{
		"key": "c_end_aigc",
		"name": "C 端 / 内容 / AIGC 体验",
		"keywords": ("c端", "用户增长", "社区", "社交", "内容", "视频", "图像", "创作", "aigc"),
		"meaning": "C 端 AI 产品更关注用户体验、留存、内容生产链路和增长闭环。",
		"positioning": "用你的 C 端和运营经历讲用户调研、数据复盘、业务闭环，而不是只讲功能设计。",
	},
	{
		"key": "product_basics",
		"name": "PRD / 需求拆解 / 0-1 上线",
		"keywords": ("prd", "需求", "0-1", "从0到1", "上线", "流程", "项目推进", "竞品", "用户调研", "数据"),
		"meaning": "AI 相关岗位仍然在考传统产品基本功，只是场景换成了 AI。",
		"positioning": "把你的 PRD、流程拆解、上线推进和运营复盘串成一条完整产品闭环。",
	},
	{
		"key": "cross_function",
		"name": "算法 / 工程 / 业务跨团队协作",
		"keywords": ("算法", "工程", "研发", "技术", "跨团队", "协同", "资源协调"),
		"meaning": "AI 产品经理通常要在业务目标、模型效果和工程实现之间做翻译。",
		"positioning": "准备一个你推动多方协作上线的案例，重点讲目标对齐、取舍和结果复盘。",
	},
)


@click.group("market", help="岗位市场扫描与 JD 共性分析")
def market_group() -> None:
	"""Market analysis commands."""


@market_group.command("ai-pm")
@click.option("--city", "cities", multiple=True, default=DEFAULT_AI_PM_CITIES, help="目标城市，可重复传入")
@click.option("--query", "queries", multiple=True, help="追加或替换搜索关键词，可重复传入")
@click.option("--page", default=1, type=int, help="起始页码")
@click.option("--pages", default=1, type=int, help="每组关键词扫描页数")
@click.option("--limit", default=30, type=int, help="最多输出职位数")
@click.option("--detail-limit", default=6, type=int, help="最多拉取多少个代表性 JD 详情")
@click.option("--min-score", default=45, type=int, help="市场匹配分阈值")
@click.option("--no-detail", is_flag=True, default=False, help="只看列表页，不拉取详情 JD")
@click.pass_context
@handle_auth_errors("market.ai-pm")
def ai_pm_cmd(
	ctx: click.Context,
	cities: tuple[str, ...],
	queries: tuple[str, ...],
	page: int,
	pages: int,
	limit: int,
	detail_limit: int,
	min_score: int,
	no_detail: bool,
) -> None:
	"""扫描杭州/上海 AI 产品经理市场，并提炼 JD 共性要求。"""
	if ctx.obj.get("platform") != "zhipin":
		handle_error_output(
			ctx,
			"market.ai-pm",
			code="NOT_SUPPORTED",
			message="AI 产品经理市场扫描当前只支持 BOSS 直聘字段结构",
			recoverable=True,
			recovery_action="boss --platform zhipin market ai-pm",
		)
		return
	if page < 1 or pages < 1 or limit < 1 or detail_limit < 0:
		handle_error_output(ctx, "market.ai-pm", code="INVALID_PARAM", message="page/pages/limit/detail-limit 必须为正数")
		return

	city_list = list(cities or DEFAULT_AI_PM_CITIES)
	query_list = list(queries or DEFAULT_AI_PM_QUERIES)
	invalid_cities = [city for city in city_list if city not in CITY_CODES]
	if invalid_cities:
		handle_error_output(
			ctx,
			"market.ai-pm",
			code="INVALID_PARAM",
			message=f"未知城市: {', '.join(invalid_cities)}",
		)
		return

	data_dir = ctx.obj["data_dir"]
	logger = ctx.obj["logger"]
	auth = AuthManager(data_dir, logger=logger, platform=ctx.obj.get("platform", "zhipin"))

	with get_platform_instance(ctx, auth) as platform:
		try:
			report = _build_ai_pm_market_report(
				platform,
				logger,
				cities=city_list,
				queries=query_list,
				start_page=page,
				pages=pages,
				limit=limit,
				detail_limit=0 if no_detail else detail_limit,
				min_score=min_score,
			)
		except MarketScanPlatformError as exc:
			handle_error_output(ctx, "market.ai-pm", code=exc.code, message=exc.message)
			return

	hints = {
		"next_actions": [
			"用代表性 JD 的 requirement_themes 起草简历主线",
			"对 jobs 中 match_score 高的岗位执行 boss detail <security_id> --job-id <job_id>",
			"如果想更激进扩样，可加 --pages 2 或 --query 'AI Agent 产品经理'",
		],
	}
	handle_output(ctx, "market.ai-pm", report, render=render_ai_pm_market_report, hints=hints)


class MarketScanPlatformError(Exception):
	def __init__(self, code: str, message: str):
		self.code = code
		self.message = message
		super().__init__(message)


def _build_ai_pm_market_report(
	platform: Any,
	logger: Any,
	*,
	cities: list[str],
	queries: list[str],
	start_page: int,
	pages: int,
	limit: int,
	detail_limit: int,
	min_score: int,
) -> dict[str, Any]:
	raw_jobs: list[dict[str, Any]] = []
	stats = {
		"search_requests": 0,
		"jobs_seen": 0,
		"jobs_filtered_out": 0,
		"search_errors": [],
	}

	for city in cities:
		for query in queries:
			for offset in range(pages):
				current_page = start_page + offset
				logger.info(f"market ai-pm: {city} / {query} / page {current_page}")
				raw = platform.search_jobs(query, city=city, page=current_page)
				stats["search_requests"] += 1
				if not platform.is_success(raw):
					code, message = platform.parse_error(raw)
					raise MarketScanPlatformError(code, message or f"{city} {query} 搜索失败")
				data = platform.unwrap_data(raw) or {}
				job_list = data.get("jobList", [])
				stats["jobs_seen"] += len(job_list)
				for raw_item in job_list:
					job = _normalize_market_job(raw_item, query)
					if not _is_ai_pm_job(job):
						stats["jobs_filtered_out"] += 1
						continue
					score, reasons = _score_market_job(job)
					if score < min_score:
						stats["jobs_filtered_out"] += 1
						continue
					job["match_score"] = score
					job["match_reasons"] = reasons
					raw_jobs.append(job)

	jobs = _dedupe_jobs(raw_jobs)
	jobs.sort(key=lambda item: (item.get("match_score", 0), _salary_high(item.get("salary", ""))), reverse=True)
	jobs = jobs[:limit]

	detail_result = _fetch_representative_details(platform, jobs, detail_limit, logger)
	details = detail_result["items"]

	return {
		"profile": {
			"target": "杭州/上海 AI 相关产品经理序列",
			"cities": cities,
			"queries": queries,
			"candidate_assumptions": list(PROFILE_POINTS),
		},
		"summary": {
			"search_requests": stats["search_requests"],
			"jobs_seen": stats["jobs_seen"],
			"jobs_matched": len(jobs),
			"jobs_filtered_out": stats["jobs_filtered_out"],
			"companies": len({job.get("company", "") for job in jobs if job.get("company")}),
			"detail_success": len(details),
			"detail_failed": len(detail_result["errors"]),
		},
		"market_signals": _analyze_market_signals(jobs, details),
		"jobs": jobs,
		"representative_jds": details,
		"detail_errors": detail_result["errors"],
	}


def _normalize_market_job(raw_item: dict[str, Any], query: str) -> dict[str, Any]:
	item = JobItem.from_api(raw_item).to_dict()
	item["query"] = query
	item["lid"] = raw_item.get("lid", "")
	item["raw_skills"] = raw_item.get("skills", []) or raw_item.get("jobLabels", []) or []
	return item


def _is_ai_pm_job(job: dict[str, Any]) -> bool:
	title = str(job.get("title") or "")
	if any(term in title for term in NON_PM_TITLE_TERMS):
		return False
	if not any(term in title for term in PRODUCT_SEQUENCE_TERMS):
		return False
	return _has_ai_relevance(_job_text(job, include_query=False))


def _has_ai_relevance(text: str) -> bool:
	text_lower = text.lower()
	return any(term.lower() in text_lower for term in AI_RELEVANCE_TERMS)


def _job_text(job: dict[str, Any], *, include_query: bool = True) -> str:
	parts = [
		job.get("title", ""),
		job.get("company", ""),
		job.get("industry", ""),
		" ".join(job.get("skills", []) or []),
		" ".join(job.get("raw_skills", []) or []),
		" ".join(job.get("welfare", []) or []),
	]
	if include_query:
		parts.append(job.get("query", ""))
	return " ".join(str(part) for part in parts if part)


def _score_market_job(job: dict[str, Any]) -> tuple[int, list[str]]:
	score = 0
	reasons: list[str] = []
	title = str(job.get("title") or "")
	text = _job_text(job)
	text_lower = text.lower()

	if any(term.lower() in title.lower() for term in ("ai", "aigc", "大模型", "智能体", "agent", "AI应用".lower())):
		score += 35
		reasons.append("标题直接命中 AI/AIGC/大模型/智能体")
	elif _has_ai_relevance(text):
		score += 20
		reasons.append("职位标签或行业体现 AI 相关性")

	skills = set(job.get("skills", []) or []) | set(job.get("raw_skills", []) or [])
	valuable_skills = [skill for skill in HIGH_VALUE_SKILLS if skill.lower() in text_lower or skill in skills]
	if valuable_skills:
		score += min(25, 8 + len(valuable_skills) * 4)
		reasons.append("技能标签匹配: " + "、".join(valuable_skills[:4]))

	exp = str(job.get("experience") or "")
	if exp in {"1-3年", "经验不限", "在校/应届", "应届", "1年以内"} or "应届" in exp:
		score += 25
		reasons.append(f"经验口径友好: {exp or '未注明'}")
	elif exp == "3-5年":
		score += 8
		reasons.append("经验略高但可作为市场参照")
	elif exp in {"5-10年", "10年以上"}:
		score -= 20
		reasons.append(f"经验要求偏高: {exp}")

	industry = str(job.get("industry") or "")
	if any(term in industry for term in ("人工智能", "互联网", "移动互联网", "企业服务", "电子商务", "在线教育", "计算机软件")):
		score += 10
		reasons.append(f"行业相关: {industry}")

	stage = str(job.get("stage") or "")
	if stage in {"已上市", "B轮", "C轮", "D轮及以上", "不需要融资"}:
		score += 5
		reasons.append(f"公司阶段较稳: {stage}")

	scale = str(job.get("scale") or "")
	if scale in {"100-499人", "500-999人", "1000-9999人", "10000人以上"}:
		score += 5
		reasons.append(f"团队规模可参考: {scale}")

	if "产品助理" in title or "产品专员" in title:
		score -= 6
		reasons.append("职级可能偏初级")
	if "产品总监" in title or "产品负责人" in title:
		score -= 8
		reasons.append("职级可能偏高，优先作为市场样本")

	return max(score, 0), reasons


def _dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
	seen: dict[str, dict[str, Any]] = {}
	for job in jobs:
		key = job.get("job_id") or job.get("security_id") or "|".join(
			str(job.get(part, "")) for part in ("company", "title", "city")
		)
		if key not in seen:
			job["queries"] = [job.get("query", "")]
			seen[key] = job
			continue
		existing = seen[key]
		query = job.get("query", "")
		if query and query not in existing["queries"]:
			existing["queries"].append(query)
		if job.get("match_score", 0) > existing.get("match_score", 0):
			existing["match_score"] = job.get("match_score", 0)
			existing["match_reasons"] = job.get("match_reasons", [])
	return list(seen.values())


def _fetch_representative_details(platform: Any, jobs: list[dict[str, Any]], limit: int, logger: Any) -> dict[str, Any]:
	if limit <= 0:
		return {"items": [], "errors": []}

	items: list[dict[str, Any]] = []
	errors: list[dict[str, str]] = []
	for job in jobs[:limit]:
		job_id = job.get("job_id", "")
		if not job_id:
			continue
		try:
			raw = platform.job_detail(job_id)
		except Exception as exc:
			logger.info(f"market ai-pm detail failed, fallback to job_card: {job.get('company', '')} {exc}")
			raw = _try_job_card_fallback(platform, job)
		if raw is None:
			errors.append({"job_id": job_id, "company": job.get("company", ""), "message": "detail and job_card fallback failed"})
			continue
		if platform.is_success(raw):
			data = platform.unwrap_data(raw) or {}
			items.append(_normalize_any_detail(data, job))
			continue

		fallback = _try_job_card_fallback(platform, job)
		if fallback is not None and platform.is_success(fallback):
			data = platform.unwrap_data(fallback) or {}
			items.append(_normalize_any_detail(data, job))
			continue
		else:
			code, message = platform.parse_error(raw)
			errors.append({"job_id": job_id, "company": job.get("company", ""), "message": f"{code}: {message}"})
	return {"items": items, "errors": errors}


def _try_job_card_fallback(platform: Any, job: dict[str, Any]) -> dict[str, Any] | None:
	security_id = job.get("security_id", "")
	if not security_id:
		return None
	try:
		return platform.job_card(security_id, job.get("lid", ""))
	except (AttributeError, NotImplementedError, TypeError, OSError, KeyError):
		return None


def _normalize_any_detail(data: dict[str, Any], fallback_job: dict[str, Any]) -> dict[str, Any]:
	if "jobCard" in data:
		return _normalize_card_detail(data.get("jobCard", {}) or {}, fallback_job)
	return _normalize_detail(data, fallback_job)


def _normalize_detail(data: dict[str, Any], fallback_job: dict[str, Any]) -> dict[str, Any]:
	job_info = data.get("jobInfo", {}) or {}
	brand_info = data.get("brandComInfo", {}) or {}
	boss_info = data.get("bossInfo", {}) or {}
	description = data.get("jobDetail", "") or job_info.get("postDescription", "") or ""
	detail = {
		"job_id": job_info.get("encryptJobId") or fallback_job.get("job_id", ""),
		"security_id": job_info.get("securityId") or fallback_job.get("security_id", ""),
		"title": job_info.get("jobName") or fallback_job.get("title", ""),
		"company": brand_info.get("brandName") or fallback_job.get("company", ""),
		"salary": job_info.get("salaryDesc") or fallback_job.get("salary", ""),
		"city": job_info.get("cityName") or fallback_job.get("city", ""),
		"experience": job_info.get("experienceName") or fallback_job.get("experience", ""),
		"education": job_info.get("degreeName") or fallback_job.get("education", ""),
		"skills": job_info.get("jobLabels") or job_info.get("skills") or fallback_job.get("skills", []),
		"boss_name": boss_info.get("name", ""),
		"boss_title": boss_info.get("title", ""),
		"description_excerpt": _excerpt(description, 420),
	}
	detail["key_requirements"] = _extract_key_requirement_lines(description)
	detail["requirement_themes"] = _themes_for_text(_detail_text(detail, description))
	detail["matching_angles"] = _build_matching_angles(detail, description)
	return detail


def _normalize_card_detail(card: dict[str, Any], fallback_job: dict[str, Any]) -> dict[str, Any]:
	description = card.get("postDescription", "") or ""
	detail = {
		"job_id": card.get("encryptJobId") or fallback_job.get("job_id", ""),
		"security_id": card.get("securityId") or fallback_job.get("security_id", ""),
		"title": card.get("jobName") or fallback_job.get("title", ""),
		"company": card.get("brandName") or fallback_job.get("company", ""),
		"salary": card.get("salaryDesc") or fallback_job.get("salary", ""),
		"city": card.get("cityName") or fallback_job.get("city", ""),
		"experience": card.get("experienceName") or card.get("jobExperience") or fallback_job.get("experience", ""),
		"education": card.get("degreeName") or card.get("jobDegree") or fallback_job.get("education", ""),
		"skills": card.get("jobLabels") or card.get("skills") or fallback_job.get("skills", []),
		"boss_name": card.get("bossName", ""),
		"boss_title": card.get("bossTitle", ""),
		"description_excerpt": _excerpt(description, 420),
	}
	detail["key_requirements"] = _extract_key_requirement_lines(description)
	detail["requirement_themes"] = _themes_for_text(_detail_text(detail, description))
	detail["matching_angles"] = _build_matching_angles(detail, description)
	return detail


def _extract_key_requirement_lines(description: str) -> list[str]:
	lines = []
	for raw_line in description.replace("；", "\n").replace("。", "\n").splitlines():
		line = raw_line.strip(" \t-•、")
		if len(line) < 6:
			continue
		if len(line) > 120:
			line = line[:117] + "..."
		lines.append(line)
	return lines[:8]


def _build_matching_angles(detail: dict[str, Any], description: str) -> list[str]:
	text = _detail_text(detail, description).lower()
	angles: list[str] = []
	if any(term in text for term in ("tob", "b端", "saas", "客户", "交付", "企业")):
		angles.append("主打 B 端产品、流程拆解和跨团队推进，把经验讲成“能把 AI 落到业务流程并上线”。")
	if any(term in text for term in ("c端", "社区", "社交", "内容", "用户", "增长")):
		angles.append("主打 C 端体验和运营复盘，用用户调研、数据复盘、业务闭环证明你能做增长与留存。")
	if any(term in text for term in ("agent", "智能体", "workflow", "工作流", "coze", "dify")):
		angles.append("把 AI/LLM/智能体理解具体化到 workflow、skill、场景拆解和效果验证。")
	if any(term in text for term in ("prompt", "大模型", "llm", "模型", "rag", "badcase")):
		angles.append("强调你能理解模型能力边界，和算法/工程一起定位 badcase、定义迭代优先级。")
	if any(term in text for term in ("prd", "需求", "0-1", "上线", "项目推进", "流程")):
		angles.append("准备一个 0-1 上线案例，按“背景-拆解-PRD-协同-上线-复盘”讲。")
	if not angles:
		angles.append("用 B/C 端产品经验 + 0-1 上线 + 运营闭环做通用匹配主线，再补 AI 项目理解。")
	return angles[:4]


def _analyze_market_signals(jobs: list[dict[str, Any]], details: list[dict[str, Any]]) -> dict[str, Any]:
	salary_ranges = [parse_salary_range(job.get("salary", "")) for job in jobs]
	salary_ranges = [item for item in salary_ranges if item]
	low_values = [item[0] for item in salary_ranges]
	high_values = [item[1] for item in salary_ranges]
	all_texts = [_job_text(job) for job in jobs] + [_detail_text(detail, "") for detail in details]

	return {
		"city_distribution": _counter_items(Counter(job.get("city", "") for job in jobs if job.get("city"))),
		"experience_distribution": _counter_items(Counter(job.get("experience", "") for job in jobs if job.get("experience"))),
		"salary": {
			"sample_count": len(salary_ranges),
			"median_low_k": int(median(low_values)) if low_values else None,
			"median_high_k": int(median(high_values)) if high_values else None,
			"bands": _salary_band_counts(salary_ranges),
		},
		"top_skills": _top_skills(jobs),
		"requirement_themes": _theme_counts(all_texts),
	}


def _top_skills(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
	counter: Counter[str] = Counter()
	for job in jobs:
		for skill in (job.get("skills", []) or []) + (job.get("raw_skills", []) or []):
			if skill and str(skill) not in SKILL_IGNORE_TERMS:
				counter[str(skill)] += 1
	return _counter_items(counter, limit=12)


def _theme_counts(texts: list[str]) -> list[dict[str, Any]]:
	items: list[dict[str, Any]] = []
	for theme in THEMES:
		keywords = theme["keywords"]
		count = sum(1 for text in texts if any(keyword.lower() in text.lower() for keyword in keywords))
		if count <= 0:
			continue
		items.append({
			"key": theme["key"],
			"name": theme["name"],
			"count": count,
			"evidence_keywords": [kw for kw in keywords if any(kw.lower() in text.lower() for text in texts)][:6],
			"what_it_means": theme["meaning"],
			"how_you_can_position": theme["positioning"],
		})
	return sorted(items, key=lambda item: item["count"], reverse=True)


def _themes_for_text(text: str) -> list[str]:
	return [
		theme["name"]
		for theme in THEMES
		if any(keyword.lower() in text.lower() for keyword in theme["keywords"])
	]


def _salary_band_counts(ranges: list[tuple[int, int]]) -> list[dict[str, Any]]:
	bands = Counter()
	for low, high in ranges:
		mid = (low + high) / 2
		if mid < 15:
			bands["<15K"] += 1
		elif mid < 25:
			bands["15-25K"] += 1
		elif mid < 40:
			bands["25-40K"] += 1
		else:
			bands["40K+"] += 1
	return _counter_items(bands)


def _counter_items(counter: Counter[str], *, limit: int = 20) -> list[dict[str, Any]]:
	return [
		{"name": name, "count": count}
		for name, count in counter.most_common(limit)
		if name
	]


def _salary_high(value: str) -> int:
	parsed = parse_salary_range(value)
	return parsed[1] if parsed else 0


def _excerpt(text: str, limit: int) -> str:
	cleaned = " ".join(text.split())
	if len(cleaned) <= limit:
		return cleaned
	return cleaned[: limit - 3] + "..."


def _detail_text(detail: dict[str, Any], description: str) -> str:
	return " ".join(
		str(part)
		for part in (
			detail.get("title", ""),
			detail.get("company", ""),
			detail.get("city", ""),
			" ".join(detail.get("skills", []) or []),
			description,
			detail.get("description_excerpt", ""),
		)
		if part
	)


def render_ai_pm_market_report(data: dict[str, Any]) -> None:
	summary = data.get("summary", {})
	profile = data.get("profile", {})
	console.print(Panel(
		(
			f"[bold cyan]{profile.get('target', 'AI 产品经理市场')}[/bold cyan]\n"
			f"matched: [green]{summary.get('jobs_matched', 0)}[/green] / seen: {summary.get('jobs_seen', 0)} "
			f"/ companies: {summary.get('companies', 0)} / details: {summary.get('detail_success', 0)}"
		),
		title="market ai-pm",
		border_style="cyan",
	))

	jobs = data.get("jobs", [])[:12]
	if jobs:
		table = Table(title="top AI PM jobs", show_lines=True)
		table.add_column("#", style="dim", width=3)
		table.add_column("title", style="bold cyan", max_width=28)
		table.add_column("company", style="green", max_width=18)
		table.add_column("city", style="blue", width=8)
		table.add_column("salary", style="yellow", width=12)
		table.add_column("exp", width=10)
		table.add_column("score", width=6)
		for index, job in enumerate(jobs, 1):
			table.add_row(
				str(index),
				str(job.get("title", "-")),
				str(job.get("company", "-")),
				str(job.get("city", "-")),
				str(job.get("salary", "-")),
				str(job.get("experience", "-")),
				str(job.get("match_score", "-")),
			)
		console.print(table)

	themes = data.get("market_signals", {}).get("requirement_themes", [])[:6]
	if themes:
		theme_table = Table(title="JD common requirements", show_lines=True)
		theme_table.add_column("theme", style="bold")
		theme_table.add_column("count", width=6)
		theme_table.add_column("how to position", max_width=70)
		for item in themes:
			theme_table.add_row(
				item.get("name", "-"),
				str(item.get("count", 0)),
				item.get("how_you_can_position", "-"),
			)
		console.print(theme_table)

	for detail in data.get("representative_jds", [])[:3]:
		console.print(Panel(
			"\n".join([
				f"[bold]{detail.get('company', '-')} - {detail.get('title', '-')}[/bold]",
				"匹配讲法:",
				*detail.get("matching_angles", [])[:3],
			]),
			border_style="green",
		))
