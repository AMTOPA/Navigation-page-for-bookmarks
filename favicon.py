import os
import json
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
from PIL import Image
from io import BytesIO
from tqdm import tqdm
import warnings
from bs4 import XMLParsedAsHTMLWarning

# 配置
FAVICON_DIR = "favicons"
DEFAULT_FAVICON = "favicon.ico"  # 改为本地文件
TIMEOUT = 5  # 增加超时时间
MAX_WORKERS = 5  # 最大并发数

# 忽略BeautifulSoup的XML警告
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def init_favicon_dir():
    """初始化图标目录"""
    os.makedirs(FAVICON_DIR, exist_ok=True)

    # 检查本地默认图标是否存在
    if not os.path.exists(os.path.join(FAVICON_DIR, DEFAULT_FAVICON)):
        # 创建默认空白图标
        img = Image.new('RGB', (32, 32), color=(220, 220, 220))
        img.save(os.path.join(FAVICON_DIR, DEFAULT_FAVICON), "ICO")
        print(f"已创建本地默认图标: {DEFAULT_FAVICON}")


def get_domain(url):
    """从URL提取规范化域名"""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        domain = domain.replace("www.", "").split(":")[0]
        return domain if domain else "unknown"
    except:
        return "unknown"


def get_favicon_url(url):
    """从网页HTML中查找favicon链接"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=TIMEOUT)

        # 使用更可靠的解析方式
        if 'xml' in response.headers.get('Content-Type', '').lower():
            soup = BeautifulSoup(response.text, features='xml')
        else:
            soup = BeautifulSoup(response.text, 'html.parser')

        # 查找各种可能的favicon链接
        icon_links = [
            soup.find("link", rel="icon"),
            soup.find("link", rel="shortcut icon"),
            soup.find("link", rel="apple-touch-icon"),
            soup.find("meta", property="og:image")
        ]

        for icon in filter(None, icon_links):
            href = icon.get("href") or icon.get("content")
            if href and not href.startswith(("data:", "javascript:")):
                if not href.startswith(("http://", "https://")):
                    # 处理相对路径
                    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                    href = base + ("/" if not href.startswith("/") else "") + href
                return href
    except Exception as e:
        pass
    return None


def download_favicon(url, favicon_path):
    """下载并保存favicon"""
    try:
        # 尝试从HTML获取精确favicon链接
        html_favicon = get_favicon_url(url)
        if html_favicon:
            url = html_favicon
        else:
            # 回退到/favicon.ico
            parsed = urlparse(url)
            url = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

        response = requests.get(url, timeout=TIMEOUT)
        response.raise_for_status()

        # 验证图片有效性
        img = Image.open(BytesIO(response.content))

        # 转换为ICO格式并调整大小
        if img.size != (32, 32):
            img = img.resize((32, 32), Image.LANCZOS)

        img.save(favicon_path, "ICO")
        return True
    except Exception as e:
        print(f"下载图标失败: {url} - {str(e)}")
        return False


def process_single_bookmark(bookmark):
    """处理单个书签的favicon"""
    domain = get_domain(bookmark["url"])
    favicon_path = os.path.join(FAVICON_DIR, f"{domain}.ico")  # 改为.ico格式

    # 跳过已存在的图标
    if os.path.exists(favicon_path):
        bookmark["favicon"] = f"favicons/{domain}.ico"
        return bookmark

    # 尝试下载图标
    success = download_favicon(bookmark["url"], favicon_path)

    # 更新书签数据
    bookmark["favicon"] = f"favicons/{domain}.ico" if success else f"favicons/{DEFAULT_FAVICON}"
    return bookmark


def process_all_bookmarks(bookmarks):
    """处理所有书签，带进度条显示"""
    init_favicon_dir()
    processed = []

    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_single_bookmark, b) for b in bookmarks]

        # 添加进度条
        for future in tqdm(as_completed(futures), total=len(bookmarks), desc="下载网站图标"):
            try:
                processed.append(future.result())
            except Exception as e:
                print(f"\n处理书签时出错: {str(e)}")

    return processed