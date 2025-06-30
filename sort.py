import json
import os
import requests
import time
import re
from tqdm import tqdm
from urllib.parse import urlparse

# 从环境变量获取API密钥
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY")
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

# 定义分类类别
CATEGORIES = [
    "工作", "教育", "社交", "购物", "娱乐", "新闻",
    "工具", "金融", "技术", "政府", "健康", "旅游", "其他"
]

# 定义标签关键词映射
TAG_KEYWORDS = {
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

def call_zhipu_api(prompt, model="glm-4-flash"):
    """调用智谱清言API"""
    # 从环境变量获取API密钥
    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        raise ValueError("未设置智谱清言API密钥，请通过环境变量ZHIPU_API_KEY设置")

    headers = {
        "Authorization": f"Bearer {api_key}",  # 使用从环境变量获取的密钥
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个专业的网站分类助手。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 10
    }

    try:
        response = requests.post(ZHIPU_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"API调用失败: {e}")
        return "其他"

def classify_bookmark(bookmark):
    """使用GLM-4-Flash对单个书签进行分类"""
    prompt = f"""
    请根据以下网站信息，从给定的分类中选择最合适的一个分类。
    只能返回分类名称，不要包含其他任何内容。

    ### 可选分类列表:
    {', '.join(CATEGORIES)}

    ### 网站信息:
    名称: {bookmark['name']}
    网址: {bookmark['url']}
    域名: {bookmark['domain']}
    现有标签: {', '.join(bookmark.get('tags', []))}

    ### 分类结果:
    """

    classification = call_zhipu_api(prompt)

    # 验证分类结果
    if classification in CATEGORIES:
        return classification
    else:
        # 尝试从返回文本中提取有效分类
        for category in CATEGORIES:
            if category in classification:
                return category
        return "其他"

def generate_tags(bookmark):
    """根据书签信息生成标签"""
    tags = set()

    # 添加域名相关标签
    domain = bookmark['domain']
    if domain:
        clean_domain = re.sub(r'^www\.|\.com$|\.cn$|\.net$|\.org$', '', domain.lower())
        tags.add(clean_domain)

    # 检查名称和URL中的关键词
    text_to_check = f"{bookmark['name'].lower()} {bookmark['url'].lower()}"
    for tag, kw_list in TAG_KEYWORDS.items():
        if any(kw in text_to_check for kw in kw_list):
            tags.add(tag)

    # 添加文件类型标签
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
        if any(ext in bookmark['url'].lower() for ext in extensions):
            tags.add(file_tag)

    return list(tags)

def process_bookmarks(input_file, output_file, batch_size=5, delay=0.5):
    """处理书签文件"""
    # 加载书签数据
    with open(input_file, 'r', encoding='utf-8') as f:
        bookmarks = json.load(f)

    # 初始化进度条
    progress_bar = tqdm(total=len(bookmarks), desc="分类书签", unit="个")

    # 处理每个书签
    for i, bookmark in enumerate(bookmarks):
        # 生成标签
        if 'tags' not in bookmark or not bookmark['tags']:
            bookmark['tags'] = generate_tags(bookmark)

        # 分类
        if 'category' not in bookmark or not bookmark['category']:
            bookmark['category'] = classify_bookmark(bookmark)
            time.sleep(delay)  # API速率限制

        # 更新进度条
        progress_bar.update(1)
        progress_bar.set_postfix_str(f"当前: {bookmark['name'][:10]}...")

        # 分批保存
        if i % batch_size == 0:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(bookmarks, f, ensure_ascii=False, indent=2)

    # 关闭进度条
    progress_bar.close()

    # 最终保存
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(bookmarks, f, ensure_ascii=False, indent=2)

    print(f"\n处理完成! 共处理了 {len(bookmarks)} 个书签。")
if __name__ == "__main__":
    # 独立运行时检查API密钥
    if not ZHIPU_API_KEY:
        ZHIPU_API_KEY = input("请输入智谱清言API密钥: ")
        os.environ["ZHIPU_API_KEY"] = ZHIPU_API_KEY

    input_file = "bookmarks_extracted.json"
    output_file = "bookmarks_classified.json"

    process_bookmarks(input_file, output_file)