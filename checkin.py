import datetime
import json
import logging
import os
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from pypushdeer import PushDeer


def beijing_time_converter(timestamp):
    utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    beijing_dt = utc_dt.astimezone(beijing_tz)
    return beijing_dt.timetuple()


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if hasattr(handler, "formatter") and handler.formatter is not None:
        handler.formatter.converter = beijing_time_converter

logger = logging.getLogger(__name__)

# Environment variables
ENV_PUSH_KEY = "PUSHDEER_SENDKEY"
ENV_COOKIES = "GLADOS_COOKIES"
ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"
ENV_API_BASE_URL = "GLADOS_API_BASE_URL"
ENV_VERBOSE = "GLADOS_VERBOSE"

# API configuration
DEFAULT_API_BASE_URL = "https://railgun.info"
LEGACY_API_BASE_URL = "https://glados.cloud"
API_PATHS = {
    "checkin": "/api/user/checkin",
    "status": "/api/user/status",
    "points": "/api/user/points",
    "exchange": "/api/user/exchange",
}

# POST data
CHECKIN_DATA = {"token": "glados.network"}

# Exchange plan points
EXCHANGE_POINTS = {"plan100": 100, "plan200": 200, "plan500": 500}

# Request headers
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/102.0.0.0 Safari/537.36"
)


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return DEFAULT_API_BASE_URL
    if not base_url.startswith(("http://", "https://")):
        base_url = f"https://{base_url}"
    return base_url.rstrip("/")


def build_api_urls(base_url: str) -> Dict[str, str]:
    normalized_base_url = normalize_base_url(base_url)
    return {name: f"{normalized_base_url}{path}" for name, path in API_PATHS.items()}


def build_headers(base_url: str) -> Dict[str, str]:
    normalized_base_url = normalize_base_url(base_url)
    parsed_url = urlparse(normalized_base_url)
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return {
        "referer": f"{origin}/console/checkin",
        "origin": origin,
        "user-agent": USER_AGENT,
        "content-type": "application/json;charset=UTF-8",
    }


def is_verbose_enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> Tuple[str, List[str], str, str, bool]:
    push_key_env = os.environ.get(ENV_PUSH_KEY)
    raw_cookies_env = os.environ.get(ENV_COOKIES)
    exchange_plan_env = os.environ.get(ENV_EXCHANGE_PLAN)
    api_base_url_env = os.environ.get(ENV_API_BASE_URL)
    verbose_env = os.environ.get(ENV_VERBOSE)

    push_key = push_key_env or ""
    if not push_key:
        logger.warning("环境变量 '%s' 未设置。", ENV_PUSH_KEY)

    if not raw_cookies_env:
        logger.warning("环境变量 '%s' 未设置。", ENV_COOKIES)
        cookies_list = []
    else:
        cookies_list = [cookie.strip() for cookie in raw_cookies_env.split("&") if cookie.strip()]
        if not cookies_list:
            raise ValueError(f"环境变量 '{ENV_COOKIES}' 已设置，但未包含任何有效的 Cookie。")

    if not exchange_plan_env:
        logger.info("环境变量 '%s' 未设置，将跳过积分兑换。", ENV_EXCHANGE_PLAN)
        exchange_plan = ""
    elif exchange_plan_env in EXCHANGE_POINTS:
        exchange_plan = exchange_plan_env
        logger.info("使用指定的兑换计划: %s", exchange_plan)
    else:
        logger.warning(
            "环境变量 '%s' 的值 '%s' 无效，将使用默认兑换计划 'plan500'。",
            ENV_EXCHANGE_PLAN,
            exchange_plan_env,
        )
        exchange_plan = "plan500"

    api_base_url = normalize_base_url(api_base_url_env or DEFAULT_API_BASE_URL)
    verbose = is_verbose_enabled(verbose_env)

    logger.info("共加载了 %s 个 Cookie 用于签到。", len(cookies_list))
    logger.info("当前 %s %s。", ENV_PUSH_KEY, "已设置" if push_key else "未设置")
    logger.info("当前 %s: %s。", ENV_EXCHANGE_PLAN, exchange_plan or "未设置(不兑换)")
    logger.info("当前 API 域名: %s。", api_base_url)
    logger.info("当前 %s: %s。", ENV_VERBOSE, "开启" if verbose else "关闭")

    return push_key, cookies_list, exchange_plan, api_base_url, verbose


def make_request(
    url: str,
    method: str,
    headers: Dict[str, str],
    data: Optional[Dict] = None,
    cookies: str = "",
    verbose: bool = False,
) -> Optional[requests.Response]:
    session_headers = headers.copy()
    session_headers["cookie"] = cookies

    if verbose:
        logger.info("请求 %s %s", method.upper(), url)

    try:
        if method.upper() == "POST":
            response = requests.post(url, headers=session_headers, data=json.dumps(data), timeout=30)
        elif method.upper() == "GET":
            response = requests.get(url, headers=session_headers, timeout=30)
        else:
            logger.error("不支持的 HTTP 方法: %s", method)
            return None

        if verbose:
            logger.info("响应 %s %s: %s", method.upper(), url, response.text)

        if not response.ok:
            logger.warning("向 %s 发起的请求失败，状态码 %s。响应内容: %s", url, response.status_code, response.text)
            return None
        return response
    except requests.exceptions.RequestException as exc:
        logger.error("向 %s 发起请求时发生网络错误: %s", url, exc)
        return None


def parse_json_response(response: requests.Response, label: str) -> Optional[Dict]:
    try:
        return response.json()
    except json.JSONDecodeError:
        logger.error("解析%s响应 JSON 失败: %s", label, response.text)
        return None


def checkin_and_process(
    cookie: str,
    exchange_plan: str,
    api_urls: Dict[str, str],
    headers: Dict[str, str],
    verbose: bool = False,
) -> Tuple[str, str, str, str, str]:
    status_msg = "签到请求失败"
    points_gained = "0"
    remaining_days = "获取剩余天数失败"
    remaining_points = "获取剩余积分失败"
    exchange_msg = "兑换跳过或失败"
    current_points_numeric = 0

    checkin_response = make_request(
        api_urls["checkin"], "POST", headers, CHECKIN_DATA, cookies=cookie, verbose=verbose
    )
    if not checkin_response:
        return status_msg, points_gained, remaining_days, remaining_points, exchange_msg

    checkin_data = parse_json_response(checkin_response, "签到")
    if checkin_data is None:
        return status_msg, points_gained, remaining_days, remaining_points, exchange_msg

    response_code = checkin_data.get("code")
    response_message = checkin_data.get("message", "无消息字段")
    normalized_message = response_message.lower()
    points_gained = str(checkin_data.get("points", 0))

    try:
        points_gained_is_zero = float(points_gained) == 0
    except (ValueError, TypeError):
        points_gained_is_zero = False

    if points_gained_is_zero:
        checkin_records = checkin_data.get("list") or []
        if checkin_records:
            try:
                points_gained = str(int(float(checkin_records[0].get("change", 0))))
            except (ValueError, TypeError):
                points_gained = str(checkin_records[0].get("change", 0))

    if "checkin repeats" in normalized_message or "return tomorrow" in normalized_message:
        status_msg = "重复签到，明天再来"
        points_gained = "0"
    elif response_code == 0 or "checkin! got" in normalized_message:
        status_msg = f"签到成功，获得 {points_gained} 积分"
    else:
        status_msg = f"签到失败: {response_message}"
        points_gained = "0"

    status_response = make_request(api_urls["status"], "GET", headers, cookies=cookie, verbose=verbose)
    if status_response:
        status_data = parse_json_response(status_response, "状态")
        if status_data:
            left_days_float = status_data.get("data", {}).get("leftDays")
            try:
                remaining_days = f"{int(float(left_days_float))} 天"
            except (ValueError, TypeError):
                logger.error("解析剩余天数时出错: %s", left_days_float)
                remaining_days = "获取剩余天数失败 (数值转换错误)"
        else:
            remaining_days = "获取剩余天数失败 (JSON解析错误)"
    else:
        remaining_days = "获取剩余天数失败 (HTTP请求失败)"

    points_response = make_request(api_urls["points"], "GET", headers, cookies=cookie, verbose=verbose)
    if points_response:
        points_data = parse_json_response(points_response, "积分")
        if points_data:
            points_float = points_data.get("points")
            try:
                current_points_numeric = int(float(points_float))
                remaining_points = f"{current_points_numeric} 积分"
            except (ValueError, TypeError):
                logger.error("解析剩余积分时出错: %s", points_float)
                remaining_points = "获取剩余积分失败 (数值转换错误)"
        else:
            remaining_points = "获取剩余积分失败 (JSON解析错误)"
    else:
        remaining_points = "获取剩余积分失败 (HTTP请求失败)"

    if not exchange_plan:
        exchange_msg = "未设置计划，跳过兑换"
    else:
        required_points = EXCHANGE_POINTS.get(exchange_plan, 500)
        if current_points_numeric >= required_points:
            logger.info("开始兑换 %s 计划 (需要 %s 积分)", exchange_plan, required_points)
            exchange_response = make_request(
                api_urls["exchange"],
                "POST",
                headers,
                {"planType": exchange_plan},
                cookies=cookie,
                verbose=verbose,
            )
            if exchange_response:
                exchange_data = parse_json_response(exchange_response, "兑换")
                if exchange_data:
                    code = exchange_data.get("code", -1)
                    if code == 0:
                        exchange_msg = f"兑换成功：{exchange_plan}"
                    else:
                        detailed_msg = exchange_data.get("message", "未知错误")
                        exchange_msg = f"兑换失败: {exchange_plan}, 错误代码: {code}, 详情: {detailed_msg}"
                else:
                    exchange_msg = f"兑换响应解析失败: {exchange_plan}"
            else:
                exchange_msg = f"兑换请求失败：{exchange_plan}"
        else:
            logger.info("积分不足以兑换 %s。所需: %s, 当前: %s", exchange_plan, required_points, current_points_numeric)
            exchange_msg = f"积分不足，未兑换: {exchange_plan}"

    return status_msg, points_gained, remaining_days, remaining_points, exchange_msg


def format_push_content(results: List[Dict[str, str]]) -> Tuple[str, str]:
    success_count = sum(1 for result in results if "成功" in result["status"])
    fail_count = sum(1 for result in results if "失败" in result["status"] or "失败" in result["exchange"])
    repeat_count = sum(1 for result in results if "重复" in result["status"])

    title = f"GLaDOS 签到, 成功{success_count}, 失败{fail_count}, 重复{repeat_count}"

    content_lines = []
    for index, result in enumerate(results, 1):
        line_parts = [
            f"账号{index}:",
            f"P:{result['points']}",
            f"剩余天数:{result['days']}",
            f"总积分:{result['points_total']}",
            f"| {result['status']}",
            f"; {result['exchange']}",
        ]
        content_lines.append(" ".join(line_parts))

    content = "\n".join(content_lines)
    return title, content


def main():
    push_key = ""
    try:
        push_key, cookies_list, exchange_plan, api_base_url, verbose = load_config()
        api_urls = build_api_urls(api_base_url)
        headers = build_headers(api_base_url)

        if not cookies_list:
            logger.error("未找到有效的 Cookie，退出程序。")
            title, content = "# 未找到 cookies!", ""
        else:
            results = []
            for index, cookie in enumerate(cookies_list, 1):
                logger.info("正在处理第 %s 个账户...", index)
                status, points, days, points_total, exchange = checkin_and_process(
                    cookie, exchange_plan, api_urls, headers, verbose=verbose
                )
                results.append(
                    {
                        "status": status,
                        "points": points,
                        "days": days,
                        "points_total": points_total,
                        "exchange": exchange,
                    }
                )

            title, content = format_push_content(results)
            logger.info("推送标题: %s", title)
            logger.info("推送内容:\n%s", content)

    except Exception as exc:
        logger.error("主程序执行过程中发生未预期的错误: %s", exc)
        title, content = "# 脚本执行出错", str(exc)

    if not push_key:
        logger.info("未设置 '%s'，跳过推送通知。", ENV_PUSH_KEY)
    else:
        try:
            pushdeer = PushDeer(pushkey=push_key)
            pushdeer.send_text(title, desp=content)
            logger.info("推送通知发送成功。")
        except Exception as exc:
            logger.error("发送推送通知失败: %s", exc)


if __name__ == "__main__":
    main()
