import os
import json
import shutil
from datetime import datetime
from difflib import SequenceMatcher
import time
from tqdm import tqdm
from sort import generate_tags,classify_bookmark


def load_config():
    """加载或创建配置文件"""
    config_path = "bookmark_config.json"
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        config = {
            "api_key": "",
            "last_processed": None,
            "processed_hashes": {}
        }
        # 首次使用时要求用户输入API密钥
        config["api_key"] = input("首次使用，请输入您的智谱清言API密钥: ")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return config


def save_config(config):
    """保存配置文件"""
    with open("bookmark_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def calculate_bookmark_hash(bookmark):
    """计算书签的哈希值用于比较"""
    import hashlib
    unique_str = f"{bookmark['name']}{bookmark['url']}{bookmark['domain']}"
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()


def extract_edge_bookmarks(html_file_path, output_json_path):
    """从Edge导出的HTML收藏文件中提取书签"""
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse
    import re
    from datetime import datetime

    print("\n步骤1: 从HTML文件中提取书签...")

    with open(html_file_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, 'html.parser')
    bookmarks = []

    for a_tag in soup.find_all('a'):
        name = a_tag.string if a_tag.string else "未命名"
        url = a_tag.get('href', '')
        add_date = int(a_tag.get('add_date', '0'))
        icon = a_tag.get('icon', '')
        add_date_readable = datetime.fromtimestamp(add_date).strftime('%Y-%m-%d %H:%M:%S') if add_date else ''
        domain = urlparse(url).netloc if url else ''

        # 自动生成标签
        tags = set()
        if domain:
            clean_domain = re.sub(r'^www\.|\.com$|\.cn$|\.net$|\.org$', '', domain.lower())
            tags.add(clean_domain)

        keywords = {
            'mail': ['mail', 'email', '邮箱'],
            'social': ['weibo', 'twitter', 'facebook', 'reddit', '社交'],
            'shopping': ['taobao', 'jd', 'amazon', '购物', '商城'],
            'video': ['youtube', 'bilibili', 'youku', '视频', 'tv'],
            'music': ['music', '网易云', 'qq音乐', 'spotify'],
            'education': ['edu', '学校', '大学', '学院', '学习', 'course'],
            'government': ['gov', '政府', '政务', '公共服务'],
            'finance': ['bank', '支付宝', 'pay', '金融', '理财', '证券'],
            'tech': ['github', 'stackoverflow', '开发', '编程', '技术']
        }

        text_to_check = f"{name.lower()} {url.lower()}"
        for tag, kw_list in keywords.items():
            if any(kw in text_to_check for kw in kw_list):
                tags.add(tag)

        file_types = {
            'pdf': '.pdf',
            'doc': ['.doc', '.docx'],
            'sheet': ['.xls', '.xlsx'],
            'ppt': ['.ppt', '.pptx'],
            'image': ['.jpg', '.jpeg', '.png', '.gif']
        }

        for file_tag, extensions in file_types.items():
            if isinstance(extensions, str):
                extensions = [extensions]
            if any(ext in url.lower() for ext in extensions):
                tags.add(file_tag)

        bookmark = {
            "name": name,
            "url": url,
            "domain": domain,
            "add_date": add_date,
            "add_date_readable": add_date_readable,
            "icon": icon,
            "tags": list(tags),
            "category": "",
            "favorite": False,
            "visited": False,
            "notes": "",
            "hash": calculate_bookmark_hash({
                "name": name,
                "url": url,
                "domain": domain
            })
        }
        bookmarks.append(bookmark)

    bookmarks.sort(key=lambda x: (x['domain'], -x['add_date']))

    with open(output_json_path, 'w', encoding='utf-8') as f:
        json.dump(bookmarks, f, ensure_ascii=False, indent=2)

    print(f"成功提取 {len(bookmarks)} 个书签到 {output_json_path}")
    return bookmarks


def classify_bookmarks(input_file, output_file, config):
    """分类和排序书签"""
    print("\n步骤2: 分类和排序书签...")

    # 确保API密钥已设置到环境变量
    os.environ['ZHIPU_API_KEY'] = config['api_key']

    # 加载现有已处理书签(如果有)
    processed_bookmarks = []
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            processed_bookmarks = json.load(f)

    # 加载新提取的书签
    with open(input_file, 'r', encoding='utf-8') as f:
        new_bookmarks = json.load(f)

    # 创建哈希映射以便快速查找
    processed_hashes = {bm['hash']: bm for bm in processed_bookmarks}
    new_hashes = {bm['hash']: bm for bm in new_bookmarks}

    # 找出需要处理的新书签(不在已处理中的)
    to_process = set(new_hashes.keys()) - set(processed_hashes.keys())
    processed_count = len(new_hashes) - len(to_process)

    if to_process:
        print(f"发现 {len(to_process)} 个新书签需要处理, {processed_count} 个已存在书签将跳过处理")

        # 初始化进度条
        progress_bar = tqdm(total=len(to_process), desc="处理进度", unit="个")

        # 处理新书签
        for i, bookmark in enumerate(new_bookmarks):
            if bookmark['hash'] in to_process:
                # 生成标签
                if not bookmark.get('tags'):
                    bookmark['tags'] = generate_tags(bookmark)

                # 分类
                if not bookmark.get('category'):
                    bookmark['category'] = classify_bookmark(bookmark)
                    time.sleep(1)  # API速率限制

                # 添加到已处理列表
                processed_bookmarks.append(bookmark)

                # 更新进度条
                progress_bar.update(1)
                progress_bar.set_postfix_str(f"当前: {bookmark['name'][:10]}...")

                # 每处理10个保存一次进度
                if i % 10 == 0:
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(processed_bookmarks, f, ensure_ascii=False, indent=2)

        # 关闭进度条
        progress_bar.close()

        # 最终保存
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(processed_bookmarks, f, ensure_ascii=False, indent=2)
    else:
        print("没有发现新书签需要处理")

    return processed_bookmarks

def download_favicons(input_file, output_file):
    """下载网站图标"""
    print("\n步骤3: 下载网站图标...")

    # 加载已分类书签
    with open(input_file, 'r', encoding='utf-8') as f:
        bookmarks = json.load(f)

    # 检查是否有favicon目录
    if not os.path.exists("favicons"):
        os.makedirs("favicons")

    # 导入favicon处理函数
    from favicon import process_all_bookmarks

    # 处理书签图标
    bookmarks_with_favicons = process_all_bookmarks(bookmarks)

    # 保存最终结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(bookmarks_with_favicons, f, ensure_ascii=False, indent=2)

    print(f"\n图标处理完成! 共处理 {len(bookmarks_with_favicons)} 个书签")


def main():
    # 加载配置
    config = load_config()

    # 定义文件路径
    html_file = "bookmarks.html"
    extracted_file = "bookmarks_extracted.json"
    classified_file = "bookmarks_classified.json"
    final_file = "bookmarks_final.json"

    # 检查HTML文件是否存在
    if not os.path.exists(html_file):
        print(f"错误: 未找到 {html_file} 文件")
        print("请将Edge导出的书签HTML文件放在同一目录下并命名为 bookmarks.html")
        return

    # 步骤1: 提取书签
    if not os.path.exists(extracted_file) or os.path.getmtime(html_file) > os.path.getmtime(extracted_file):
        extract_edge_bookmarks(html_file, extracted_file)
    else:
        print("\n已提取的书签文件是最新的，跳过提取步骤")

    # 步骤2: 分类书签
    if not os.path.exists(classified_file) or os.path.getmtime(extracted_file) > os.path.getmtime(classified_file):
        classify_bookmarks(extracted_file, classified_file, config)
    else:
        print("\n已分类的书签文件是最新的，跳过分类步骤")

    # 步骤3: 下载图标
    if not os.path.exists(final_file) or os.path.getmtime(classified_file) > os.path.getmtime(final_file):
        download_favicons(classified_file, final_file)
    else:
        print("\n最终书签文件是最新的，跳过图标下载步骤")

    # 更新配置
    config['last_processed'] = datetime.now().isoformat()
    save_config(config)

    print("\n所有处理步骤完成!")


if __name__ == "__main__":
    main()