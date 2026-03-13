# -*- coding: utf-8 -*-
"""
CSDN 站内搜索爬虫（Playwright 版）
- 根据关键词搜索文章，获取文章链接（支持动态渲染）
- 抓取每篇文章的标题、发布时间、正文、标签
- 支持从 cookies.json 加载 Cookie（可选）
- 默认抓取前4篇文章（可通过 max_articles 调整）
"""

import requests
from bs4 import BeautifulSoup
import time
import random
import re
import json
import os
from urllib.parse import quote, urljoin
from playwright.sync_api import sync_playwright


class CSDNSearchSpider:
    def __init__(self, cookie_file='cookies.json'):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
            'Referer': 'https://kunpeng-sc.csdnimg.cn/?timestamp=1645783940/',
            'Connection': 'keep-alive',
        })
        self.cookie_file = cookie_file
        self._load_cookies()
        self.min_delay = 3
        self.max_delay = 6

    def _load_cookies(self):
        """从 JSON 文件加载 Cookie 到 session（如果文件存在）"""
        if os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                self.session.cookies.update(cookies)
                print("✅ 已加载 Cookie 文件")
            except Exception as e:
                print(f"❌ 加载 Cookie 文件失败：{e}")
        else:
            print("⚠️ 未找到 Cookie 文件，将以无 Cookie 模式抓取（可能受限）")

    def _delay(self):
        """随机延时（用于 requests 请求）"""
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def search(self, keyword, max_pages=1):
        """
        根据关键词搜索 CSDN 博客，返回文章链接列表（使用 Playwright 渲染）
        :param keyword: 搜索关键词
        :param max_pages: 抓取的最大页数（每页约10条结果）
        :return: 文章URL列表
        """
        links = []
        with sync_playwright() as p:
            # 启动浏览器（headless=True 表示无界面运行，可改为 False 观察）
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # 如需携带 Cookie，可取消注释以下代码
            # cookies = self.session.cookies.get_dict()
            # if cookies:
            #     page.context.add_cookies([{'name': k, 'value': v, 'domain': '.csdn.net'} for k, v in cookies.items()])

            for page_num in range(1, max_pages + 1):
                url = f"https://so.csdn.net/so/search?q={quote(keyword)}&t=blog&p={page_num}"
                print(f"🔍 抓取搜索页：{url}")

                try:
                    # 访问页面，等待30秒超时
                    page.goto(url, timeout=30000)
                    # 等待文章链接出现（基于你提供的HTML结构，选择器为 a.block-title）
                    page.wait_for_selector('a.block-title', timeout=10000)

                    # 获取渲染后的HTML
                    html = page.content()
                    soup = BeautifulSoup(html, 'lxml')

                    # 提取所有符合条件的链接
                    items = soup.select('a.block-title')
                    print(f"  本页找到 {len(items)} 个链接")

                    for a in items:
                        href = a.get('href')
                        if href:
                            if href.startswith('http'):
                                links.append(href)
                            else:
                                full_url = urljoin(url, href)
                                links.append(full_url)

                    # 模拟人类浏览的随机等待
                    time.sleep(random.uniform(2, 4))

                except Exception as e:
                    print(f"  抓取异常：{e}")
                    continue

            browser.close()

        # 去重
        links = list(set(links))
        print(f"📌 关键词 '{keyword}' 共找到 {len(links)} 篇文章链接")
        return links

    def fetch_article(self, url):
        """抓取单篇文章的详细信息"""
        print(f"📄 正在抓取文章：{url}")
        self._delay()
        try:
            resp = self.session.get(url, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                print(f"  请求失败，状态码：{resp.status_code}")
                return None

            soup = BeautifulSoup(resp.text, 'lxml')

            # ---------- 提取标题 ----------
            title = None
            title_selectors = ['h1.title-article', 'h1#articleContentId', '.title-article', 'h1']
            for sel in title_selectors:
                title_tag = soup.select_one(sel)
                if title_tag:
                    title = title_tag.get_text(strip=True)
                    break
            if not title:
                json_ld = soup.find('script', type='application/ld+json')
                if json_ld and json_ld.string:
                    try:
                        data = json.loads(json_ld.string)
                        title = data.get('title') or data.get('headline')
                    except:
                        pass
            if not title:
                title_tag = soup.find('title')
                if title_tag:
                    title = title_tag.get_text(strip=True).replace('-CSDN博客', '').strip()
            if not title:
                title = "未知标题"

            # ---------- 提取发布时间 ----------
            pub_time = None
            json_ld = soup.find('script', type='application/ld+json')
            if json_ld and json_ld.string:
                try:
                    data = json.loads(json_ld.string)
                    pub_time = data.get('pubDate') or data.get('datePublished')
                except:
                    pass
            if not pub_time:
                meta_time = soup.find('meta', attrs={'property': 'article:published_time'})
                if meta_time and meta_time.get('content'):
                    pub_time = meta_time['content']
            if not pub_time:
                time_selectors = ['.time', '.article-meta .time', '.blog-article-info .time', 'span.time']
                for sel in time_selectors:
                    time_tag = soup.select_one(sel)
                    if time_tag:
                        pub_time = time_tag.get_text(strip=True)
                        break
            if not pub_time:
                text = resp.text
                patterns = [
                    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',
                    r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})',
                    r'(\d{4}-\d{2}-\d{2})'
                ]
                for pat in patterns:
                    match = re.search(pat, text)
                    if match:
                        pub_time = match.group(1)
                        break
            if not pub_time:
                pub_time = "未知时间"

            # ---------- 提取标签 ----------
            tags = []
            tag_selectors = ['.blog-tags-box a', '.tag-list a', '.article-tags a']
            for sel in tag_selectors:
                tag_tags = soup.select(sel)
                if tag_tags:
                    tags = [t.get_text(strip=True) for t in tag_tags]
                    break
            if not tags and json_ld and json_ld.string:
                try:
                    data = json.loads(json_ld.string)
                    keywords = data.get('keywords')
                    if keywords:
                        tags = [k.strip() for k in keywords.split(',')]
                except:
                    pass
            if not tags:
                meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
                if meta_keywords and meta_keywords.get('content'):
                    tags = [k.strip() for k in meta_keywords['content'].split(',')]

            # ---------- 提取正文 ----------
            content = ""
            content_selectors = [
                'div#article_content',
                'div#content_views',
                'div.article_content',
                'div.blog-content-box',
                'div.article-box'
            ]
            for sel in content_selectors:
                content_div = soup.select_one(sel)
                if content_div:
                    for script in content_div(['script', 'style']):
                        script.decompose()
                    content = content_div.get_text(separator='\n', strip=True)
                    break
            if not content:
                body = soup.find('body')
                if body:
                    content = body.get_text(separator='\n', strip=True)[:500]

            return {
                'url': url,
                'title': title,
                'publish_time': pub_time,
                'tags': tags,
                'content': content
            }
        except Exception as e:
            print(f"❌ 抓取文章时发生异常：{e}")
            return None

    def crawl_by_keyword(self, keyword, max_search_pages=2, max_articles=4):
        """
        根据关键词搜索并抓取文章详情
        :param keyword: 搜索关键词
        :param max_search_pages: 搜索的最大页数（默认2页）
        :param max_articles: 最多抓取的文章数量（默认4篇）
        :return: 文章信息列表
        """
        links = self.search(keyword, max_pages=max_search_pages)
        if not links:
            print("❌ 未找到任何文章链接")
            return []
        results = []
        for idx, url in enumerate(links):
            if idx >= max_articles:
                break
            article = self.fetch_article(url)
            if article:
                results.append(article)
        return results


wb= CSDNSearchSpider(cookie_file='cookies.json')