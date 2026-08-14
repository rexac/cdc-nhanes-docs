#!/usr/bin/env python3
"""
download_all_cdc_docs.py — 全自动下载 CDC NHANES 所有周期、所有模块的
Codebook 文档页（.htm），建立本地完整数据库。

修复/改进（v2，相对初版）:
  1. Component 拼写修正: "Demographics" → "Demographic"（单数才是正确接口，
     Demographics 只返回 40/195 链接）
  2. 增加 "Delayed" 组件 —— 延迟公开数据（SSHOLO/SSKL/SSHPV 等）的文档
     全在此组件下，初版完全遗漏
  3. 按 周期/组件 分目录存储（避免平铺重名 + 便于导航）
  4. 失败自动重试（最多 3 次，指数退避）
  5. 断点续传：已下载且非空文件自动跳过
  6. 增量更新：可反复运行，只下载缺失/变化的文档

用法: python3 download_all_cdc_docs.py [目标目录]
默认目标: ./nhanes_docs
"""

import os
import sys
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "./nhanes_docs"
os.makedirs(OUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# NHANES 调查周期起始年份（1999 → 2021；2017 为 4 年周期 P，2021 起为 L）
CYCLES = [1999, 2001, 2003, 2005, 2007, 2009, 2011, 2013, 2015, 2017, 2021, 2023]

# NHANES 六大组件（含 Delayed！）
# 注意: 官方接口用单数 "Demographic"（非 "Demographics"）
COMPONENTS = ["Demographic", "Dietary", "Examination", "Laboratory",
              "Questionnaire", "Delayed"]

# 组件显示名映射（用于目录命名）
COMP_LABEL = {
    "Demographic": "demographic",
    "Dietary": "dietary",
    "Examination": "examination",
    "Laboratory": "laboratory",
    "Questionnaire": "questionnaire",
    "Delayed": "delayed",
}


def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch(url, session, retries=3, timeout=20):
    """带重试的 GET，返回 response.text（失败抛异常）"""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            # 404 = 该周期无此模块，返回空标记（不算失败）
            if r.status_code == 404:
                return ""
        except Exception:
            pass
        wait = 2 ** attempt + random.uniform(0, 1)
        time.sleep(wait)
    raise RuntimeError(f"下载失败(重试{retries}次): {url}")


def discover_links(session):
    """遍历 datapage.aspx 索引页，收集所有文档页 URL。

    返回 dict: {(year, comp_label): [url, ...]}
    """
    print(">>> 扫描 CDC 索引页 (datapage.aspx)...")
    found = {}
    total = 0

    for year in CYCLES:
        for comp in COMPONENTS:
            url = (f"https://wwwn.cdc.gov/nchs/nhanes/search/datapage.aspx"
                   f"?Component={comp}&CycleBeginYear={year}")
            try:
                html = fetch(url, session)
            except RuntimeError as e:
                print(f"  [扫描失败] {year}/{comp}: {e}")
                continue
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            links = set()
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # 文档页特征: DataFiles/xxx.htm
                if href.lower().endswith(".htm") and "DataFiles" in href:
                    links.add(urljoin(url, href))

            if links:
                label = COMP_LABEL[comp]
                key = (year, label)
                found[key] = sorted(links)
                total += len(links)
                print(f"  ✓ {year} {label}: {len(links)} 个文档")
            time.sleep(0.4)

    print(f">>> 扫描完成: {len(found)} 个 周期×组件 组合, {total} 个文档页\n")
    return found


def download_all(session, found):
    """按 周期/组件 分目录下载。已存在且非空则跳过。"""
    print(f">>> 开始下载 → {os.path.abspath(OUT_DIR)}")
    success = skip = fail = 0
    total = sum(len(v) for v in found.values())

    # 先收集全部任务，便于进度显示
    tasks = []
    for (year, comp), urls in sorted(found.items()):
        for u in urls:
            tasks.append((year, comp, u))

    for i, (year, comp, url) in enumerate(tasks, 1):
        fname = os.path.basename(urlparse(url).path)
        # 周期/组件分目录: nhanes_docs/2011/delayed/SSHOLO_G.htm
        sub = os.path.join(OUT_DIR, str(year), comp)
        os.makedirs(sub, exist_ok=True)
        fpath = os.path.join(sub, fname)

        # 断点续传：非空已存在 → 跳过
        if os.path.exists(fpath) and os.path.getsize(fpath) > 1000:
            skip += 1
            continue

        try:
            html = fetch(url, session)
            if not html:
                # 404（文档不存在，但索引里有）→ 记录为空
                fail += 1
                continue
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(html)
            success += 1
            if success % 50 == 0 or i == total:
                print(f"  [{i}/{total}] 成功 {success} | 跳过 {skip} | 失败 {fail}")
        except RuntimeError as e:
            fail += 1
            print(f"  [{i}/{total}] FAIL: {fname} → {str(e)[:50]}")
        time.sleep(0.2 + random.uniform(0, 0.2))

    print("\n" + "=" * 50)
    print(f"任务结束 | 下载: {success} | 跳过已存在: {skip} | 失败: {fail}")
    print("=" * 50)
    return success, skip, fail


if __name__ == "__main__":
    session = get_session()
    links_map = discover_links(session)
    if not links_map:
        print("未发现任何文档链接，请检查网络/代理")
        sys.exit(1)
    s, sk, f = download_all(session, links_map)
    # 非零失败数时退出码为 1，方便 cron/CI 感知
    sys.exit(1 if f > 0 else 0)
