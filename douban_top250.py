"""豆瓣电影 Top 250：采集第 1—10 页，字段解析失败时保存为 JSON null。

安装依赖：python -m pip install requests beautifulsoup4
运行脚本：python douban_top250.py
输出文件：当前目录下的 douban_top250.json（每次运行会覆盖）
"""

import json
import logging
import math
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://movie.douban.com/top250"
PAGE_SIZE = 25
PAGE_COUNT = 10
REQUEST_INTERVAL = 2  # 相邻页面请求间隔，单位：秒
OUTPUT_FILE = Path("douban_top250.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DoubanTop250Learning/1.0)",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def parse_rating(text):
    """评分保存为数字；无效数字、NaN、无穷大、超出范围均视为解析失败。"""
    value = float(text)
    if not math.isfinite(value) or not 0 <= value <= 10:
        raise ValueError("评分必须是 0—10 之间的有限数字")
    return value


def parse_detail_url(href):
    """补全相对链接，并确认它指向豆瓣电影详情页。"""
    url = urljoin(BASE_URL, href)
    parsed = urlparse(url)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.netloc != "movie.douban.com"
        or not re.fullmatch(r"/subject/\d+/?", parsed.path)
    ):
        raise ValueError("不是有效的豆瓣电影详情页链接")
    return url


def read_field(item, selector, attribute=None, converter=None):
    """只保护当前字段：缺失、空值或解析异常返回 None，不影响其他字段。"""
    try:
        element = item.select_one(selector)
        if element is None:
            return None
        value = element.get(attribute) if attribute else element.get_text(strip=True)
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        return converter(value) if converter else value
    except Exception as exc:
        # 按需求在单字段边界兜底，不会吞掉网络错误或文件写入错误。
        logging.warning("字段 %s 解析失败，保存为 null：%s", selector, exc)
        return None


def parse_page(html, expected_page=None):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("ol.grid_view > li")
    if not items:
        raise ValueError("未找到电影列表，可能收到验证页面或页面结构已变化")

    # 防止请求第 N 页时，网站实际返回第 1 页而造成重复。
    current = soup.select_one(".paginator .thispage")
    if current is not None and expected_page is not None:
        if current.get_text(strip=True) != str(expected_page):
            raise ValueError("返回的页码与请求页码不一致")

    return [
        {
            "name": read_field(item, ".hd .title"),  # 取第一个标题（中文名）
            "rating": read_field(item, ".rating_num", converter=parse_rating),
            "quote": read_field(item, ".quote"),  # 不依赖 span 是否带 inq 类名
            "url": read_field(item, ".hd a", attribute="href", converter=parse_detail_url),
        }
        for item in items
    ]


def save_json(movies, output_file):
    """先写临时文件，再替换结果文件；None 自动转换为 JSON 的 null。"""
    output_file = Path(output_file)
    temporary = output_file.with_name(output_file.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(movies, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    temporary.replace(output_file)


def crawl(output_file=OUTPUT_FILE):
    movies = []
    failed_pages = []
    short_pages = []
    seen_urls = set()
    try:
        with requests.Session() as session:
            session.headers.update(HEADERS)
            for page in range(1, PAGE_COUNT + 1):
                if page > 1:
                    time.sleep(REQUEST_INTERVAL)

                # 第 1 页 start=0，第 2 页 start=25，...，第 10 页 start=225。
                start = (page - 1) * PAGE_SIZE
                logging.info("抓取第 %d/%d 页，start=%d", page, PAGE_COUNT, start)
                try:
                    with session.get(
                        BASE_URL,
                        params={"start": start, "filter": ""},
                        timeout=(10, 20),  # 连接超时、读取超时
                    ) as response:
                        response.raise_for_status()
                        response.encoding = "utf-8"
                        page_movies = parse_page(response.text, expected_page=page)
                except (requests.RequestException, ValueError) as exc:
                    failed_pages.append(page)
                    logging.warning("第 %d 页失败，继续下一页：%s", page, exc)
                    continue

                added = 0
                for movie in page_movies:
                    url = movie["url"]
                    if url is not None and url in seen_urls:
                        continue
                    movies.append(movie)
                    added += 1
                    if url is not None:
                        seen_urls.add(url)

                if len(page_movies) != PAGE_SIZE or added != PAGE_SIZE:
                    short_pages.append(page)
                    logging.warning("第 %d 页解析 %d 条，去重后新增 %d 条", page, len(page_movies), added)
                save_json(movies, output_file)  # 每页完成后保存，保留已抓取进度
    except KeyboardInterrupt:
        logging.warning("用户中断，保存已抓取的数据")
    finally:
        save_json(movies, output_file)

    logging.info("已保存 %d 条记录：%s", len(movies), Path(output_file).resolve())
    if failed_pages:
        logging.warning("抓取失败的页码：%s", failed_pages)
    if short_pages:
        logging.warning("条数异常或存在重复的页码：%s", short_pages)
    if failed_pages or short_pages or len(movies) != PAGE_SIZE * PAGE_COUNT:
        logging.warning("本次结果不完整，请根据日志检查；不会用空记录凑满 250 条")
    return movies


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s：%(message)s")
    try:
        crawl()
    except OSError as exc:
        logging.error("无法保存 JSON，请检查目录权限和磁盘空间：%s", exc)
