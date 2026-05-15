import json
from unittest.mock import patch

from click.testing import CliRunner

from boss_agent_cli.main import cli


def _ctx_mock(mock_cls):
	instance = mock_cls.return_value
	instance.__enter__ = lambda self: self
	instance.__exit__ = lambda self, *a: None
	instance.is_success.side_effect = lambda response: response.get("code") == 0
	instance.unwrap_data.side_effect = lambda response: response.get("zpData")
	instance.parse_error.return_value = ("UNKNOWN", "failed")
	return instance


@patch("boss_agent_cli.commands.market.AuthManager")
@patch("boss_agent_cli.commands.market.get_platform_instance")
def test_market_ai_pm_filters_and_summarizes(mock_platform_cls, mock_auth_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.search_jobs.return_value = {
		"code": 0,
		"zpData": {
			"jobList": [
				{
					"encryptJobId": "j_step",
					"jobName": "【急招】ai产品经理",
					"brandName": "阶跃星辰",
					"salaryDesc": "35-65K·16薪",
					"cityName": "上海",
					"jobExperience": "1-3年",
					"jobDegree": "本科",
					"skills": ["B端产品", "ToB", "agent", "LLM", "Workflow", "Prompt Engineering"],
					"brandIndustry": "人工智能",
					"brandScaleName": "100-499人",
					"brandStageName": "B轮",
					"securityId": "sec_step",
				},
				{
					"encryptJobId": "j_ops",
					"jobName": "AI产品运营",
					"brandName": "运营公司",
					"salaryDesc": "15-25K",
					"cityName": "上海",
					"jobExperience": "1-3年",
					"jobDegree": "本科",
					"skills": ["AI产品"],
					"brandIndustry": "互联网",
					"securityId": "sec_ops",
				},
				{
					"encryptJobId": "j_generic",
					"jobName": "产品经理",
					"brandName": "普通公司",
					"salaryDesc": "15-25K",
					"cityName": "上海",
					"jobExperience": "1-3年",
					"jobDegree": "本科",
					"skills": ["支付产品"],
					"brandIndustry": "金融",
					"securityId": "sec_generic",
				},
				{
					"encryptJobId": "j_fresh",
					"jobName": "AI产品经理",
					"brandName": "应届友好公司",
					"salaryDesc": "20-40K",
					"cityName": "杭州",
					"jobExperience": "经验不限",
					"jobDegree": "本科",
					"skills": ["C端产品", "AIGC"],
					"brandIndustry": "人工智能",
					"brandScaleName": "500-999人",
					"brandStageName": "已上市",
					"securityId": "sec_fresh",
				},
			]
		},
	}
	platform.job_detail.return_value = {
		"code": 0,
		"zpData": {
			"jobInfo": {
				"encryptJobId": "j_step",
				"securityId": "sec_step",
				"jobName": "【急招】ai产品经理",
				"salaryDesc": "35-65K·16薪",
				"cityName": "上海",
				"experienceName": "1-3年",
				"degreeName": "本科",
				"jobLabels": ["B端产品", "ToB", "agent", "LLM", "Workflow", "Prompt Engineering"],
			},
			"brandComInfo": {"brandName": "阶跃星辰"},
			"bossInfo": {"name": "王总", "title": "产品负责人"},
			"jobDetail": (
				"负责企业 Agent 产品设计，围绕金融顾问和零售场景寻找高价值业务流程。"
				"输出 PRD，设计 Workflow 和 Skill，理解 LLM API 能力边界和 Prompt Engineering。"
				"推动 Demo 到生产上线，和算法、工程、客户共创完成商业闭环。"
			),
		},
	}

	runner = CliRunner()
	result = runner.invoke(
		cli,
		[
			"--json",
			"market",
			"ai-pm",
			"--city",
			"上海",
			"--query",
			"AI 产品经理",
			"--limit",
			"10",
			"--detail-limit",
			"1",
		],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["command"] == "market.ai-pm"

	data = payload["data"]
	titles = {job["title"] for job in data["jobs"]}
	assert "【急招】ai产品经理" in titles
	assert "AI产品经理" in titles
	assert "AI产品运营" not in titles
	assert "产品经理" not in titles
	assert data["summary"]["jobs_matched"] == 2
	assert data["summary"]["detail_success"] == 1
	assert data["market_signals"]["requirement_themes"][0]["count"] >= 1
	assert any("Workflow" in item["name"] or "agent" in item["name"] for item in data["market_signals"]["top_skills"])
	platform.job_detail.assert_called_once_with("j_step")


@patch("boss_agent_cli.commands.market.AuthManager")
@patch("boss_agent_cli.commands.market.get_platform_instance")
def test_market_ai_pm_supports_no_detail(mock_platform_cls, mock_auth_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.search_jobs.return_value = {
		"code": 0,
		"zpData": {
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "AIGC产品经理",
					"brandName": "同花顺",
					"salaryDesc": "25-40K",
					"cityName": "杭州",
					"jobExperience": "1-3年",
					"jobDegree": "本科",
					"skills": ["C端产品", "AIGC"],
					"brandIndustry": "互联网",
					"securityId": "sec1",
				},
			]
		},
	}

	result = CliRunner().invoke(
		cli,
		["--json", "market", "ai-pm", "--city", "杭州", "--query", "AIGC 产品经理", "--no-detail"],
	)

	assert result.exit_code == 0, result.output
	payload = json.loads(result.output)
	assert payload["data"]["summary"]["detail_success"] == 0
	assert payload["data"]["representative_jds"] == []
	platform.job_detail.assert_not_called()


@patch("boss_agent_cli.commands.market.AuthManager")
@patch("boss_agent_cli.commands.market.get_platform_instance")
def test_market_ai_pm_falls_back_to_job_card_detail(mock_platform_cls, mock_auth_cls):
	platform = _ctx_mock(mock_platform_cls)
	platform.search_jobs.return_value = {
		"code": 0,
		"zpData": {
			"jobList": [
				{
					"encryptJobId": "j1",
					"jobName": "AI产品经理",
					"brandName": "网易",
					"salaryDesc": "21-35K·16薪",
					"cityName": "杭州",
					"jobExperience": "在校/应届",
					"jobDegree": "本科",
					"skills": ["AI产品"],
					"brandIndustry": "互联网",
					"brandScaleName": "10000人以上",
					"brandStageName": "已上市",
					"securityId": "sec1",
					"lid": "lid1",
				},
			]
		},
	}
	platform.job_detail.return_value = {"code": 1, "message": "缺少必要参数"}
	platform.job_card.return_value = {
		"code": 0,
		"zpData": {
			"jobCard": {
				"encryptJobId": "j1",
				"securityId": "sec1",
				"jobName": "AI产品经理",
				"brandName": "网易",
				"salaryDesc": "21-35K·16薪",
				"cityName": "杭州",
				"experienceName": "在校/应届",
				"degreeName": "本科",
				"jobLabels": ["AI产品", "C端产品"],
				"postDescription": "负责 AI 产品 0-1 上线，输出 PRD，推进算法和工程协作，基于用户反馈做数据复盘。",
			}
		},
	}

	result = CliRunner().invoke(
		cli,
		["--json", "market", "ai-pm", "--city", "杭州", "--query", "AI 产品经理", "--detail-limit", "1"],
	)

	assert result.exit_code == 0, result.output
	data = json.loads(result.output)["data"]
	assert data["summary"]["detail_success"] == 1
	assert data["representative_jds"][0]["company"] == "网易"
	assert "PRD" in " ".join(data["representative_jds"][0]["key_requirements"])
	platform.job_card.assert_called_once_with("sec1", "lid1")


def test_schema_includes_market_command():
	result = CliRunner().invoke(cli, ["schema"])
	assert result.exit_code == 0
	commands = json.loads(result.output)["data"]["commands"]
	assert "market" in commands
	assert "ai-pm" in commands["market"]["subcommands"]
