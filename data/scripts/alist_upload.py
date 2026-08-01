#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AList API 直传脚本 v4.3.3（最终版·严格退出契约）
- 流式上传 /api/fs/put（禁止 chunked）
- 任务追踪（有 task_id 时：先拿“上传任务”结论 → 成功再做 FS 校验；失败立即返回）
- FS 校验：目录强刷（/api/fs/list?refresh=true）→ 直查（/api/fs/get）
- 自适应等待窗口： base + perGB 附加，硬上限由 CONFIG['verify_wait_cap_secs'] 控制（默认 5 小时）
- put→form 回退（可选）：/api/fs/form，开启 CONFIG['fallback_to_form']=True 时才触发
- 删除本地：统一在最终阶段，且“删除前短确认”（FS 命中才删）
- 任务管理菜单
- 可观测性：进入异步校验前/每完成1项/最终阶段的清单输出，飞书通知
- 提升并发与“错峰”：线程池可配、启动相位抖动、轮询抖动、（可选）全局 QPS 限速、（可选）活跃追踪限流
"""

import os
import io
import time
import math
import argparse
import threading
import requests
import json
import hmac
import hashlib
import base64
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from datetime import datetime
from requests.exceptions import ReadTimeout, ConnectionError as ReqConnectionError
from typing import Optional

# ====================== 配置（可被命令行覆盖部分参数） ======================
CONFIG = {
    # 源目录（本地要上传的根）
    'source_dir': '/volume1/video/已整理',

    # AList 服务（不要带末尾斜杠）
    'alist_url': 'http://192.168.0.115:5244',
    'username': 'jeffrey',
    'password': 'shasha66',

    # 远程根路径（最终保存到 remote_root/相对路径/文件名）
    'remote_root': '/ChinaMbile/moviepilot',

    # 上传行为
    'chunk_size': 8 * 1024 * 1024,       # 进度读取块（8MB）
    'max_retries': 3,
    'connect_timeout': 10,               # 连接超时（秒）
    'read_timeout': 120,                 # 读取超时（等待响应），建议 120–180 秒

    # 删除策略（统一在最终阶段执行）
    'delete_local_after_upload': True,    # 成功后删除本地（受 delete_requires_task_success 约束）
    'delete_requires_task_success': True, # 仅在“任务状态成功”时才允许删除本地文件

    # 异步校验（后台轮询 /api/fs/list(refresh)/get）
    'async_verify': True,
    'verify_wait_secs': 7200,             # 基准等待窗口（秒）
    'verify_per_gb_addon': 1000,          # 每 GB 追加秒数（自适应）
    'verify_wait_cap_secs': 5 * 3600,     # ★ 最大兜底时长（秒）——默认 5 小时

    # put→form 自动回退（默认关闭；仅在 FS 校验失败时考虑回退一次）
    'fallback_to_form': False,

    # 大小跳过阈值（默认 4GB，可由 --max-size-gb 改）
    'skip_large_bytes': 4 * 1024**3,

    # —— 并发与轮询抖动（新增）——
    'verify_max_workers': 32,      # 线程池并发（建议 12/16/24/32 逐步调）
    'startup_jitter_secs': 2.0,    # 每个任务启动时的一次性相位抖动（0~S）
    'poll_base_interval': 10.0,    # 轮询基础间隔（秒）
    'poll_jitter_secs': 5.0,       # 每次轮询叠加抖动范围（0~S）
    'active_verify_semaphore': 0,  # 同时处于轮询中的任务上限（0=不额外限流）

    # —— 全局 QPS 限速（令牌桶）——
    # 0 / None 表示不开启；>0 表示严格限制为每秒 N 次请求（所有会调用 AList 的热点接口前都会过闸）
    'global_qps': 0.0,

    # EMBY（可选）
    'emby_host': 'http://192.168.0.115:8098',
    'emby_api_key': 'f89929d523034b03bfd6c3c2e1ac4c2c',

    # —— 飞书机器人（可选）——
    'feishu_enable': True,  # 总开关；置 False 即完全不发
    'feishu_webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/df512440-03e2-4541-83d7-321d6bd62261',
    'feishu_secret': 'sijqq9M81hVLcPC39thNng',    # 若机器人安全设置开启了“签名校验”，填入 Secret；没开则留空
    'feishu_notify': {
        'on_snapshot': False,     # 进入异步校验时发一张快照卡片
        'on_item_done': False,    # 每完成1项（🧩）时发一张简卡（建议可置False）
        'on_final_summary': True, # 最终统计时发一张汇总卡片（推荐开启）
        'detail_failed_top_n': 10,# 汇总卡片中失败TOP N条
        'detail_success_top_n': 0 # 0=全部；>0=最多展示N条
    }
}

# 识别为“永久失败”的错误关键字（遇到后不再 form 回退）
PERMANENT_FAIL_KEYWORDS = ['权益不足']

# ====================== 日志 ======================
class Logger:
    def __init__(self, log_file: Optional[str] = None, write_file: bool = False):
        # write_file=False：只打印，不写文件
        self.write_file = write_file
        self.log_file = log_file or f'./upload_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

    def _w(self, lvl: str, msg: str):
        if not self.write_file:
            return  # ✅ 不写文件

        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f'{ts} - {lvl} - {msg}\n')

    def info(self, msg: str):
        print(f'\033[94m[INFO]\033[0m {msg}')
        self._w('INFO', msg)

    def success(self, msg: str):
        print(f'\033[92m[SUCCESS]\033[0m {msg}')
        self._w('SUCCESS', msg)

    def warning(self, msg: str):
        print(f'\033[93m[WARNING]\033[0m {msg}')
        self._w('WARNING', msg)

    def error(self, msg: str):
        print(f'\033[91m[ERROR]\033[0m {msg}')
        self._w('ERROR', msg)

    def debug(self, msg: str):
        self.info(f'[DEBUG] {msg}')

logger = Logger(write_file=False)

# ====================== 全局 RateLimiter（可选） ======================
class RateLimiter:
    """简单令牌桶：严格实现全局 QPS（线程安全）"""
    def __init__(self, qps: float):
        self.interval = 1.0 / float(qps)
        self.lock = threading.Lock()
        self.next_time = time.time()
    def acquire(self):
        with self.lock:
            now = time.time()
            if now < self.next_time:
                time.sleep(self.next_time - now)
            self.next_time = max(now, self.next_time) + self.interval

_GLOBAL_RL: Optional[RateLimiter] = None
def _rl_acquire():
    if _GLOBAL_RL:
        _GLOBAL_RL.acquire()

# ====================== AList API 客户端 ======================
class AlistAPI:
    def __init__(self, base_url: str, username: str, password: str):
        self.base = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'AlistApiUploader/4.3.3'})
        self.token = None
        self.auth_scheme = 'auto'  # 'auto' | 'plain' | 'bearer'
        if not self.login():
            raise RuntimeError('AList 登录失败')

    # ---------- 登录与鉴权 ----------
    def _set_auth_header(self):
        if not self.token: return
        if self.auth_scheme == 'plain':
            self.session.headers['Authorization'] = self.token
        elif self.auth_scheme == 'bearer':
            self.session.headers['Authorization'] = f'Bearer {self.token}'
        else:
            self.session.headers['Authorization'] = self.token

    def build_auth_headers(self) -> dict:
        """用于线程内临时请求（避免多线程复用同一 Session）"""
        if not self.token: return {}
        if self.auth_scheme in ('auto', 'plain'):
            return {'Authorization': self.token, 'Accept': 'application/json'}
        return {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'}

    def login(self) -> bool:
        try:
            _rl_acquire()
            r = self.session.post(
                f'{self.base}/api/auth/login',
                json={'username': self.username, 'password': self.password},
                timeout=CONFIG['connect_timeout']
            )
            if r.status_code != 200:
                logger.error(f'登录 HTTP {r.status_code}: {r.text[:200]}'); return False
            data = r.json()
            if data.get('code') != 200:
                logger.error(f'登录失败: {data.get("message")}'); return False
            self.token = data['data']['token']
            self.auth_scheme = 'auto'
            self._set_auth_header()
            # 探测授权头用法（plain → bearer）
            _rl_acquire()
            test = self.session.post(f'{self.base}/api/fs/list', json={'path': '/'}, timeout=CONFIG['connect_timeout'])
            if test.status_code == 200 and (test.json().get('code') == 200):
                logger.success('AList 登录成功（Authorization: <token>）'); self.auth_scheme = 'plain'; return True
            self.auth_scheme = 'bearer'; self._set_auth_header()
            _rl_acquire()
            test2 = self.session.post(f'{self.base}/api/fs/list', json={'path': '/'}, timeout=CONFIG['connect_timeout'])
            if test2.status_code == 200 and (test2.json().get('code') == 200):
                logger.success('AList 登录成功（Authorization: Bearer <token>）'); return True
            logger.error('登录后权限校验失败'); return False
        except Exception as e:
            logger.error(f'登录异常: {e}'); return False

    # ---------- 工具 ----------
    @staticmethod
    def _encode_full_path(path: str) -> str:
        return quote(path, safe='')

    @staticmethod
    def _fmt(n: int) -> str:
        if n <= 0: return '0B'
        units = ('B','KB','MB','GB','TB','PB')
        i = int(math.floor(math.log(n, 1024)))
        p = math.pow(1024, i)
        return f'{n/p:.2f} {units[i]}'

    # ---------- FS 基础 ----------
    def get_file_info(self, remote_path: str):
        try:
            _rl_acquire()
            r = self.session.post(
                f'{self.base}/api/fs/get',
                json={'path': remote_path},
                timeout=CONFIG['connect_timeout']
            )
            if r.status_code != 200: return None
            data = r.json()
            return data.get('data') if data.get('code') == 200 else None
        except Exception:
            return None

    def mkdir_recursive(self, remote_dir: str) -> bool:
        try:
            cur = ''
            for seg in [p for p in remote_dir.strip('/').split('/') if p]:
                cur += '/' + seg
                _rl_acquire()
                r = self.session.post(
                    f'{self.base}/api/fs/mkdir',
                    json={'path': cur},
                    timeout=CONFIG['connect_timeout']
                )
                if r.status_code != 200:
                    logger.warning(f'mkdir HTTP {r.status_code}: {r.text[:180]}'); return False
            return True
        except Exception as e:
            logger.error(f'创建目录异常: {e}'); return False

    # ---------- 目录强刷校验 ----------
    def verify_by_list_refresh(self, remote_path: str, size: int, wait_secs: int, tries: int) -> bool:
        parent, name = os.path.dirname(remote_path), os.path.basename(remote_path)
        url = f'{self.base}/api/fs/list'
        headers = self.build_auth_headers()
        interval = max(1.0, wait_secs / max(1, tries))
        jitter = float(CONFIG.get('poll_jitter_secs', 0.0))
        for _ in range(max(1, tries)):
            try:
                _rl_acquire()
                r = requests.post(url, headers=headers,
                                  json={'path': parent, 'page': 1, 'per_page': 0, 'refresh': True},
                                  timeout=CONFIG['connect_timeout'])
                if r.status_code == 200:
                    data = r.json()
                    if data.get('code') == 200:
                        content = (data.get('data') or {}).get('content') or []
                        for item in content:
                            if item.get('name') == name and not item.get('is_dir', False) and item.get('size') == size:
                                return True
            except Exception:
                pass
            time.sleep(interval + (random.uniform(0, jitter) if jitter > 0 else 0))
        return False

    # ---------- /api/fs/get 直接校验 ----------
    def verify_size_direct(self, remote_path: str, size: int, wait_secs: int, tries: int) -> bool:
        headers = self.build_auth_headers()
        url = f'{self.base}/api/fs/get'
        interval = max(1.0, wait_secs / max(1, tries))
        jitter = float(CONFIG.get('poll_jitter_secs', 0.0))
        for _ in range(max(1, tries)):
            try:
                _rl_acquire()
                r = requests.post(url, headers=headers, json={'path': remote_path}, timeout=CONFIG['connect_timeout'])
                if r.status_code == 200:
                    data = r.json()
                    if data.get('code') == 200:
                        info = data.get('data') or {}
                        if info.get('size') == size:
                            return True
            except Exception:
                pass
            time.sleep(interval + (random.uniform(0, jitter) if jitter > 0 else 0))
        return False

    # ---------- 任务：智能列出（管理员优先 → 用户回退） ----------
    def list_upload_tasks(self, done: bool = False):
        suffix = 'done' if done else 'undone'
        headers = self.build_auth_headers()
        # 管理员视角
        try:
            _rl_acquire()
            r = self.session.get(f'{self.base}/api/admin/task/upload/{suffix}',
                                 headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 200 and isinstance(data.get('data'), list):
                    return data.get('data') or []
        except Exception:
            pass
        # 用户视角回退
        try:
            _rl_acquire()
            r = self.session.get(f'{self.base}/api/task/upload/{suffix}',
                                 headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                data = r.json()
                if data.get('code') == 200 and isinstance(data.get('data'), list):
                    return data.get('data') or []
        except Exception:
            pass
        return []

    # ---------- 任务：智能查询（用户优先 → 管理员回退） ----------
    def get_upload_task_info_smart(self, tid: str):
        headers = self.build_auth_headers()
        # 用户优先
        try:
            _rl_acquire()
            r = self.session.post(f'{self.base}/api/task/upload/info?tid={tid}',
                                  headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        # 管理员回退
        try:
            _rl_acquire()
            r = self.session.post(f'{self.base}/api/admin/task/upload/info?tid={tid}',
                                  headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ---------- 任务：取消（管理员优先 → 用户回退） ----------
    def cancel_upload_task(self, tid: str):
        headers = self.build_auth_headers()
        # 管理员 cancel
        try:
            _rl_acquire()
            r = self.session.post(f'{self.base}/api/admin/task/upload/cancel?tid={tid}',
                                  headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        # 用户回退（若构建支持）
        try:
            _rl_acquire()
            r = self.session.post(f'{self.base}/api/task/upload/cancel?tid={tid}',
                                  headers=headers, timeout=CONFIG['connect_timeout'])
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ---------- 表单上传（回退） ----------
    def upload_form(self, local_path: str, remote_path: str):
        filename = os.path.basename(local_path)
        size = os.path.getsize(local_path)
        headers = {
            'File-Path': self._encode_full_path(remote_path),
            'Accept': 'application/json',
            'As-Task': 'true',  # 进入任务系统
        }
        self._set_auth_header()
        try:
            with open(local_path, 'rb') as f:
                _rl_acquire()
                r = self.session.put(
                    f'{self.base}/api/fs/form',
                    files={'file': (filename, f, 'application/octet-stream')},
                    headers=headers,
                    timeout=(CONFIG['connect_timeout'], max(CONFIG['read_timeout'], 1800))
                )
            if r.status_code != 200:
                return False, f'HTTP {r.status_code} {r.text[:200]}', None
            data = r.json()
            if data.get('code') != 200:
                return False, data.get('message','unknown error'), None

            task = ((data.get('data') or {}).get('task')) or None
            if task:
                logger.info(f"📌 (form) 任务创建: id={task.get('id')} name={task.get('name')} progress={task.get('progress')}")

            # 成功后校验（短窗口）
            if self.verify_by_list_refresh(remote_path, size, wait_secs=180, tries=18) \
               or self.verify_size_direct(remote_path, size, wait_secs=60, tries=6):
                return True, 'form 上传成功', task
            return False, 'form 上传后校验失败', task
        except Exception as e:
            return False, f'form 上传异常: {e}', None

    # ---------- 上传（禁止 chunked；超时/断线 → 异步） ----------
    def upload_stream(self, local_path: str, remote_path: str):
        filename = os.path.basename(local_path)
        size = os.path.getsize(local_path); size_h = self._fmt(size)
        remote_dir = os.path.dirname(remote_path)

        info = self.get_file_info(remote_path)
        if info and info.get('size') == size:
            logger.warning(f'✅ 文件已存在且大小相同，跳过: {filename} ({size_h})')
            return 'ok', '已存在且大小相同', None

        if not self.mkdir_recursive(remote_dir):
            return 'fail', '创建目录失败', None

        class ProgressFile(io.BufferedReader):
            def __init__(self, raw, total, chunk, fmt):
                super().__init__(raw); self._t=total; self._c=chunk; self._fmt=fmt
                self._s=0; self._st=time.time(); self._last=self._st
            def read(self, amt=-1):
                if amt < 0: amt = self._c
                data = super().read(amt)
                if not data:
                    el = max(time.time()-self._st, 1e-6); sp = self._t/el
                    print(f'\r📤 进度: {100.0:5.1f}% | {self._fmt(self._t)}/{self._fmt(self._t)} | 速度: {self._fmt(int(sp))}/s | ETA: 0s', flush=True)
                    return data
                self._s += len(data); now=time.time()
                if now - self._last >= 0.2:
                    pc = (self._s/self._t)*100 if self._t else 0
                    el = now - self._st; sp = self._s/el if el>0 else 0; eta = (self._t-self._s)/sp if sp>0 else 0
                    print(f'\r📤 进度: {pc:5.1f}% | {self._fmt(self._s)}/{self._fmt(self._t)} | 速度: {self._fmt(int(sp))}/s | ETA: {int(eta)}s', end='', flush=True)
                    self._last = now
                return data

        for retry in range(CONFIG['max_retries']):
            try:
                headers = {
                    'Content-Type': 'application/octet-stream',
                    'Content-Length': str(size),
                    'File-Path': self._encode_full_path(remote_path),
                    'Accept': 'application/json',
                    'As-Task': 'true',
                }
                self._set_auth_header()
                with open(local_path, 'rb', buffering=0) as raw:
                    pf = ProgressFile(raw, size, CONFIG['chunk_size'], self._fmt)
                    _rl_acquire()
                    r = self.session.put(
                        f'{self.base}/api/fs/put',
                        data=pf,
                        headers=headers,
                        timeout=(CONFIG['connect_timeout'], CONFIG['read_timeout'])
                    )
                if r.status_code != 200:
                    msg = f'HTTP {r.status_code} {r.text[:200]}'
                    if retry == 0 and self.auth_scheme in ('auto','plain'):
                        self.auth_scheme='bearer'; self._set_auth_header()
                        logger.warning('上传失败，切换 Authorization: Bearer <token> 再重试'); time.sleep(1); continue
                    if retry < CONFIG['max_retries'] - 1:
                        logger.warning(f'上传失败，重试 {retry+1}/{CONFIG["max_retries"]}: {msg}'); time.sleep(2**retry); continue
                    return 'fail', msg, None

                try:
                    data = r.json()
                except Exception:
                    return 'fail', f'上传返回非 JSON: {r.text[:200]}', None
                if data.get('code') != 200:
                    msg = data.get('message','unknown error')
                    if retry < CONFIG['max_retries'] - 1:
                        logger.warning(f'上传失败，重试 {retry+1}/{CONFIG["max_retries"]}: {msg}'); time.sleep(2**retry); continue
                    return 'fail', msg, None

                task = ((data.get('data') or {}).get('task')) or None
                if task:
                    logger.info(f"📌 任务创建: id={task.get('id')} name={task.get('name')} progress={task.get('progress')}")

                if CONFIG.get('async_verify', True):
                    logger.warning(f'上传完成，交由异步校验: {filename}')
                    return 'pending', '等待异步校验', task
                else:
                    # 同步校验（仅在 async_verify=False 时）
                    if self.verify_by_list_refresh(remote_path, size, wait_secs=120, tries=12):
                        logger.success(f'✅ 上传完成: {filename} ({size_h})'); return 'ok','上传成功', task
                    if retry < CONFIG['max_retries'] - 1:
                        logger.warning(f'同步强刷未命中，重试 {retry+1}/{CONFIG["max_retries"]}'); time.sleep(2**retry); continue
                    return 'fail','同步校验失败', task

            except (ReadTimeout, ReqConnectionError) as e:
                if CONFIG.get('async_verify', True):
                    logger.warning(f'读取响应超时/连接中断：{e}，交由异步校验')
                    return 'pending','等待异步校验', None
                else:
                    if self.verify_by_list_refresh(remote_path, size, wait_secs=120, tries=12):
                        logger.success(f'✅ 上传已完成（超时后同步校验）: {filename} ({size_h})'); return 'ok','上传成功', None
                    if retry < CONFIG['max_retries'] - 1:
                        logger.warning(f'超时与校验未命中，重试 {retry+1}/{CONFIG["max_retries"]}'); time.sleep(2**retry); continue
                    return 'fail', f'上传异常且校验失败: {e}', None

            except Exception as e:
                if retry < CONFIG['max_retries'] - 1:
                    logger.warning(f'上传异常，重试 {retry+1}/{CONFIG["max_retries"]}: {e}'); time.sleep(2**retry); continue
                return 'fail', f'上传异常: {e}', None

        return 'fail', '超过最大重试次数', None

# ====================== 异步校验管理器（删除统一到最终阶段） ======================
class AsyncVerifyManager:
    def __init__(self, api: AlistAPI):
        self.api = api
        max_workers = int(CONFIG.get('verify_max_workers', 4))
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.futures = []
        self.lock = threading.Lock()
        self.form_sem = threading.Semaphore(1)  # 表单回退：单线程串行
        self._last_log_ts = {}  # 任务日志节流：task_id/name -> 上次打印时间戳
        self.results = []       # 每项: {'local':..., 'remote':..., 'ok':bool, 'succeeded_by_task':bool, 'size':int}

        # 可选：限制“处于轮询阶段”的活跃任务数量，避免惊群
        n_active = int(CONFIG.get('active_verify_semaphore', 0))
        self.active_sem = threading.Semaphore(n_active) if n_active > 0 else None

    def _adaptive_window(self, file_size: int):
        """
        自适应等待窗口：
          total_wait = min( verify_wait_secs + floor(size_GB) * verify_per_gb_addon , verify_wait_cap_secs )
          tries ≈ 每 10 秒一次（实际我们加了抖动）
        """
        base = int(CONFIG.get('verify_wait_secs', 300))
        addon = max(0, int(file_size / (1024**3))) * int(CONFIG.get('verify_per_gb_addon', 60))
        cap = max(60, int(CONFIG.get('verify_wait_cap_secs', 5 * 3600)))  # ★ 上限，默认 5 小时
        total = min(base + addon, cap)
        tries = max(30, total // 10)
        return total, tries

    def _record(self, local_file: str, remote_path: str, size: int, ok: bool, succeeded_by_task: bool):
        with self.lock:
            self.results.append({
                'local': local_file,
                'remote': remote_path,
                'size': size,
                'ok': ok,
                'succeeded_by_task': succeeded_by_task
            })

    def _remove_from_pending_and_log(self, stats: dict, local_file: str, task_id: Optional[str] = None):
        """
        从 stats['pending_items'] 移除当前文件，并打印剩余待校验数量与清单（含 task_id）
        """
        with self.lock:
            before = len(stats.get('pending_items', []))
            if before == 0:
                return
            new_list = []
            for it in stats['pending_items']:
                if it.get('local') == local_file:
                    continue
                if task_id and it.get('task_id') == task_id:
                    continue
                new_list.append(it)
            stats['pending_items'] = new_list
            after = len(new_list)

        logger.info(f'🧩 异步校验完成 1 项，剩余待校验: {after}')
        if after > 0:
            logger.info('  剩余待校验清单：')
            for idx, it in enumerate(new_list, 1):
                logger.info(f'    [{idx}] name={os.path.basename(it["local"])} | task_id={it.get("task_id") or "n/a"}')
            # 追加：飞书“每完成1项”简卡
            if CONFIG.get('feishu_enable'):
                feishu_card_item_done(after, new_list)

    def schedule(self, remote_path: str, local_file: str, local_size: int, stats: dict, task: Optional[dict] = None):
        wait_secs, tries = self._adaptive_window(local_size)
        phase = random.uniform(0.0, float(CONFIG.get('startup_jitter_secs', 0.0)))
        fut = self.executor.submit(self._verify_wrapper, remote_path, local_file, local_size, stats,
                                   wait_secs, tries, task, phase)
        self.futures.append(fut)

    # 外层兜底，彻底避免把异常抛给线程池
    def _verify_wrapper(self, remote_path: str, local_file: str, local_size: int, stats: dict,
                        wait_secs: int, tries: int, task: Optional[dict], phase: float):
        try:
            self._verify_task(remote_path, local_file, local_size, stats, wait_secs, tries, task, phase)
        except Exception as e:
            logger.error(f'校验任务异常(兜底): {e}')
            with self.lock:
                stats['failed'] += 1
            self._record(local_file, remote_path, local_size, ok=False, succeeded_by_task=False)
            self._remove_from_pending_and_log(stats, local_file, (task or {}).get('id'))

    def _verify_task(self, remote_path: str, local_file: str, local_size: int, stats: dict,
                     wait_secs: int, tries: int, task: Optional[dict], phase: float):
        """
        退出契约（严格按用户定义）：
          - 有 task_id:
              ① 仅追踪“上传任务”至明确终态（成功/失败/到期）。
              ② 任务失败 → 立即 return（不做 FS；除非 fallback_to_form=True，则后续回退）。
              ③ 任务成功 → 进入 FS 校验（在自适应窗口内给出“校验成功/失败”结论）→ 立即 return。
          - 无 task_id:
              直接按自适应窗口做 FS 校验（命中/到期即 return）。
        """
        # 启动相位：一次性错开
        if phase and phase > 0:
            time.sleep(phase)

        name = os.path.basename(local_file)
        task_id = (task or {}).get('id')
        succeeded_by_task = False  # 是否任务成功（用于统计 & 删除策略）
        no_form_retry = False      # 永久失败关键字 → 不回退 form

        # 本阶段轮询的统一“带抖动 sleep”
        def _sleep_with_jitter():
            base = float(CONFIG.get('poll_base_interval', 10.0))
            jit = float(CONFIG.get('poll_jitter_secs', 0.0))
            time.sleep(max(0.5, base + (random.uniform(0, jit) if jit > 0 else 0.0)))

        # ---------- 阶段0：若有 task_id，先得到“上传任务”结论 ----------
        if task_id:
            end_ts = time.time() + wait_secs
            logger.info(f'🔎 追踪任务: {task_id} ({name})，最长等待 {wait_secs}s（仅等待上传任务结论）')

            # 可选：限制活跃轮询
            if self.active_sem:
                self.active_sem.acquire()
            try:
                while time.time() < end_ts:
                    try:
                        info = self.api.get_upload_task_info_smart(task_id)
                        if info and info.get('code') == 200:
                            data = info.get('data')
                            if isinstance(data, dict):
                                t = data
                            elif isinstance(data, list) and data:
                                t = data[0]
                            else:
                                t = {}

                            state_raw = t.get('state')
                            status    = (t.get('status') or '').strip().lower()
                            error_str = (t.get('error')  or '').strip()
                            end_time  = t.get('end_time')

                            if isinstance(state_raw, str):
                                st = state_raw.strip().lower()
                            elif isinstance(state_raw, (int, float)):
                                st = int(state_raw)
                            else:
                                st = None

                            # —— 上传任务“成功/失败”终态 —— #
                            if (isinstance(st, int) and st == 2) or (isinstance(st, str) and st == 'succeeded') \
                               or (end_time and not error_str):
                                logger.info(f'✅ 任务完成: {task_id} (state={st}, end={end_time}, error={error_str or "None"})')
                                succeeded_by_task = True
                                break  # 进入 FS 校验阶段

                            if (isinstance(st, int) and st in (5,6,7)) or \
                               (isinstance(st, str) and st in ('failed','error','canceled','cancelled','stopped')) or \
                               error_str:
                                logger.error(f'🚫 任务失败: {task_id}, state={st}, status={status}, error={error_str}')
                                if any(k in (error_str or '') for k in PERMANENT_FAIL_KEYWORDS):
                                    no_form_retry = True
                                # 不做 FS，立即返回失败（除非允许回退）
                                if not CONFIG.get('fallback_to_form', False):
                                    stats['failed'] += 1
                                    self._record(local_file, remote_path, local_size, ok=False, succeeded_by_task=False)
                                    self._remove_from_pending_and_log(stats, local_file, task_id)
                                    return
                                # 允许回退：跳出任务循环，后续走 form 回退
                                break

                            # 进行中/未知，轮询（日志 30s 节流）
                            now = time.time()
                            if now - self._last_log_ts.get(task_id, 0) >= 30:
                                pg = t.get('progress')
                                logger.info(f'⏳ 任务进行中: {task_id}, state={st}, progress={pg}, status={status or "n/a"}')
                                self._last_log_ts[task_id] = now
                        else:
                            logger.debug(f'任务 {task_id} 查询无效响应或无权限')
                    except Exception as e:
                        logger.debug(f'任务 {task_id} 查询异常: {e}')
                    _sleep_with_jitter()
            finally:
                if self.active_sem:
                    self.active_sem.release()

            # 若到期仍未得到上传结论 → 按失败处理并返回
            if not succeeded_by_task and not CONFIG.get('fallback_to_form', False):
                logger.error(f'🚫 任务追踪到期未得出成功结论: {task_id} ({name})')
                stats['failed'] += 1
                self._record(local_file, remote_path, local_size, ok=False, succeeded_by_task=False)
                self._remove_from_pending_and_log(stats, local_file, task_id)
                return

        # ---------- 阶段1：FS 校验（仅当“任务成功”或“无 task_id”才执行） ----------
        def _fs_verify_once(total_wait_secs: int, total_tries: int) -> bool:
            # 1) 目录强刷（命中即止）
            if self.api.verify_by_list_refresh(remote_path, local_size, wait_secs=total_wait_secs, tries=total_tries):
                return True
            # 2) 直查 /api/fs/get（再给半窗口的机会，但最多 600 秒）
            return self.api.verify_size_direct(
                remote_path, local_size,
                wait_secs=min(600, total_wait_secs // 2),
                tries=max(5, total_tries // 2)
            )

        proceed_fs = (not task_id) or succeeded_by_task
        if proceed_fs:
            ok = _fs_verify_once(wait_secs, tries)
            if ok:
                logger.success(f'✅ 异步校验通过: {name}')
                stats['success'] += 1
                if succeeded_by_task:
                    stats['task_succeeded'] += 1
                self._record(local_file, remote_path, local_size, ok=True, succeeded_by_task=succeeded_by_task)
                self._remove_from_pending_and_log(stats, local_file, task_id)
                return

        # ---------- 阶段2：未命中 → （如开启）回退表单上传（仅当允许且非永久失败） ----------
        if CONFIG.get('fallback_to_form', False) and not no_form_retry:
            logger.warning(f'🔁 准备回退为表单上传重试: {name}')
            with self.form_sem:
                ok_form, msg_form, task2 = self.api.upload_form(local_file, remote_path)
            if ok_form:
                # 可选短轮询：只为“是否记为任务成功”计数，不影响成功判定
                t2id = (task2 or {}).get('id')
                if t2id:
                    short_end = time.time() + 300
                    while time.time() < short_end:
                        try:
                            info = self.api.get_upload_task_info_smart(t2id)
                            if info and info.get('code') == 200:
                                data = info.get('data')
                                if isinstance(data, dict):
                                    t = data
                                elif isinstance(data, list) and data:
                                    t = data[0]
                                else:
                                    t = {}
                                state_raw = t.get('state')
                                error_str = (t.get('error') or '').strip()
                                end_time  = t.get('end_time')
                                if isinstance(state_raw, str):
                                    st = state_raw.strip().lower()
                                elif isinstance(state_raw, (int, float)):
                                    st = int(state_raw)
                                else:
                                    st = None
                                if (isinstance(st, int) and st == 2) or (isinstance(st, str) and st == 'succeeded') \
                                   or (end_time and not error_str):
                                    succeeded_by_task = True
                                    break
                                if (isinstance(st, int) and st in (5,6,7)) or error_str:
                                    break
                        except Exception:
                            pass
                        time.sleep(5)

                logger.success(f'✅ 表单回退成功: {name}')
                stats['success'] += 1
                if succeeded_by_task:
                    stats['task_succeeded'] += 1
                self._record(local_file, remote_path, local_size, ok=True, succeeded_by_task=succeeded_by_task)
                self._remove_from_pending_and_log(stats, local_file, task_id)
                return
            else:
                logger.error(f'表单回退失败: {msg_form}')

        # ---------- 阶段3：最终失败（FS 到期未命中，且未回退或回退失败） ----------
        logger.error(f'❌ 异步校验失败: {name}')
        stats['failed'] += 1
        self._record(local_file, remote_path, local_size, ok=False, succeeded_by_task=succeeded_by_task)
        self._remove_from_pending_and_log(stats, local_file, task_id)
        return

    def wait_all(self):
        for fut in as_completed(self.futures):
            try:
                fut.result()
            except Exception as e:
                logger.error(f'异步校验线程异常(二次兜底): {e}')
        self.executor.shutdown(wait=True)

# ====================== 飞书机器人工具 ======================
def _feishu_sign(timestamp: str, secret: str) -> str:
    """飞书签名算法：base64(HMAC-SHA256("{ts}\n{secret}", ""))"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode('utf-8'), b'', hashlib.sha256).digest()
    return base64.b64encode(digest).decode('utf-8')

def _feishu_post(payload: dict) -> bool:
    """统一POST，支持签名与最多3次重试（确保 UTF‑8 编码）"""
    if not CONFIG.get('feishu_enable') or not CONFIG.get('feishu_webhook'):
        return False

    url = CONFIG['feishu_webhook']
    secret = CONFIG.get('feishu_secret') or ''
    headers = {'Content-Type': 'application/json; charset=utf-8'}

    body = payload.copy()
    if secret:
        ts = str(int(time.time()))
        sign = _feishu_sign(ts, secret)
        body.update({'timestamp': ts, 'sign': sign})

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, data=data, timeout=10)
            if r.status_code == 200:
                try:
                    j = r.json()
                    ok = (j.get('StatusCode') == 0) or (j.get('code') == 0)
                except Exception:
                    ok = True
                if not ok:
                    logger.warning(f'飞书返回非零：{r.text[:200]}')
                return ok
            else:
                logger.warning(f'飞书HTTP {r.status_code}: {r.text[:200]}')
        except Exception as e:
            logger.warning(f'飞书推送异常({attempt+1}/3): {e}')
        time.sleep(1.5 * (attempt + 1))
    return False

def feishu_send_card(title: str, elements: list, color: str = 'blue') -> bool:
    """发送卡片消息"""
    payload = {
        'msg_type': 'interactive',
        'card': {
            'config': {'wide_screen_mode': True},
            'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': color},
            'elements': elements
        }
    }
    return _feishu_post(payload)

def _fmt_size(n: int) -> str:
    try:
        return AlistAPI._fmt(int(n))
    except Exception:
        return str(n)

# —— 拼装卡片：进入异步校验快照 ——
def feishu_card_snapshot(pending_items: list, total: int, skipped_same: int, skipped_large: int):
    if not CONFIG.get('feishu_notify', {}).get('on_snapshot', True): return
    lines = [f"待异步校验：**{len(pending_items)}** / 共 {total}",
             f"已跳过（同/大）：**{skipped_same} / {skipped_large}**"]
    for i, it in enumerate(pending_items[:10], 1):
        nm = os.path.basename(it['local'])
        tid = it.get('task_id') or 'n/a'
        lines.append(f"{i}. {nm}  `task_id={tid}`")
    elements = [{'tag': 'markdown', 'content': "\n".join(lines)}]
    feishu_send_card("📥 进入异步校验快照", elements, color='turquoise')

# —— 拼装卡片：每完成1项（简要） ——
def feishu_card_item_done(remain: int, remain_list: list):
    if not CONFIG.get('feishu_notify', {}).get('on_item_done', True): return
    title = f"🧩 已完成 1 项，剩余待校验：{remain}"
    lines = []
    for i, it in enumerate(remain_list[:8], 1):
        nm = os.path.basename(it['local']) if it.get('local') else 'n/a'
        tid = it.get('task_id') or 'n/a'
        lines.append(f"{i}. {nm}  `task_id={tid}`")
    elements = [{'tag':'markdown','content': "\n".join(lines) if lines else "（无剩余）"}]
    feishu_send_card(title, elements, color='purple')

# —— 拼装卡片：最终统计汇总（推荐） ——
def feishu_card_final_summary(stats: dict, log_file_path: str):
    notify_cfg = CONFIG.get('feishu_notify', {}) or {}
    if not CONFIG.get('feishu_enable') or not notify_cfg.get('on_final_summary', True):
        return

    succ = int(stats.get('success', 0))
    fail = int(stats.get('failed', 0))
    skipped_same = int(stats.get('skipped_same', 0))
    skipped_large = int(stats.get('skipped_large', 0))
    deleted = int(stats.get('deleted', 0))
    total = int(stats.get('total', 0))
    try:
        rate = ((stats.get('success',0) + stats.get('reuploaded',0)) / total) * 100.0 if total > 0 else 0.0
    except Exception:
        rate = 0.0

    failed_merge = (stats.get('files_failed_immediate') or []) + (stats.get('files_failed_async') or [])
    topN_fail = int(notify_cfg.get('detail_failed_top_n', 5) or 5)
    fail_lines = []
    for i, it in enumerate(failed_merge[:topN_fail], 1):
        nm = os.path.basename((it or {}).get('local', '') or '')
        sz = _fmt_size((it or {}).get('size', 0))
        fail_lines.append(f"{i}. {nm}  {sz}")

    elements = [
        {'tag':'markdown','content':
         f"**总体**\n- 总数：**{total}**\n- ✅ 成功：**{succ}**\n- ❌ 失败：**{fail}**\n"
         f"- ⏭️ 跳过（同/大）：**{skipped_same}/{skipped_large}**\n- 🗑️ 已删除本地：**{deleted}**\n- 成功率：**{rate:.1f}%**"}
    ]
    elements.append({'tag':'hr'})
    elements.append({'tag':'markdown','content': "**失败TOP**\n" + ("\n".join(fail_lines) if fail_lines else "（无）")})

    succ_sync  = stats.get('files_success_sync', []) or []
    succ_async = stats.get('files_success_async', []) or []
    succ_all   = succ_sync + succ_async

    succ_limit = notify_cfg.get('detail_success_top_n', 0)
    try:
        succ_limit = int(succ_limit)
    except Exception:
        succ_limit = 0

    def _send_success_chunk(title_prefix: str, chunk: list, offset: int = 0):
        ok_lines = []
        for idx, it in enumerate(chunk, 1):
            nm = os.path.basename((it or {}).get('local', '') or '')
            sz = _fmt_size((it or {}).get('size', 0))
            ok_lines.append(f"{idx + offset}. {nm}  {sz}")
        if ok_lines:
            feishu_send_card(
                f"{title_prefix}",
                [{'tag':'markdown','content': "\n".join(ok_lines)}],
                color='green'
            )

    if succ_all:
        if succ_limit > 0:
            show = succ_all[:succ_limit]
            elements.append({'tag':'hr'})
            elements.append({'tag':'markdown','content':
                             f"**成功清单（前 {len(show)} 条 / 共 {len(succ_all)}）**\n" +
                             "\n".join([f'{i+1}. {os.path.basename((it or {}).get("local",""))}  {_fmt_size((it or {}).get("size",0))}' for i, it in enumerate(show)])})
        else:
            page_size = 50
            pages = (len(succ_all) + page_size - 1) // page_size
            if pages == 1:
                elements.append({'tag':'hr'})
                elements.append({'tag':'markdown','content':
                                 f"**成功清单（全部 {len(succ_all)} 条）**\n" +
                                 "\n".join([f'{i+1}. {os.path.basename((it or {}).get("local",""))}  {_fmt_size((it or {}).get("size",0))}' for i, it in enumerate(succ_all)])})
            else:
                for p in range(pages):
                    chunk = succ_all[p*page_size:(p+1)*page_size]
                    _send_success_chunk(f"✅ 上传成功清单（第 {p+1}/{pages} 张 | 共 {len(succ_all)}）", chunk, offset=p*page_size)

    if log_file_path:
        elements.append({'tag':'hr'})
        elements.append({'tag':'markdown','content': f"**日志文件**\n`{log_file_path}`"})

    feishu_send_card("📊 上传任务完成（AList 直传）", elements, color='blue')

# ====================== 工具函数 ======================
def find_all_files(root: str):
    files = []
    try:
        for r, _, names in os.walk(root):
            for n in names:
                files.append(os.path.join(r, n))
    except Exception as e:
        logger.error(f'扫描文件失败: {e}')
    return files

def cleanup_empty_dirs(file_path: str):
    try:
        src = CONFIG['source_dir'].rstrip('/')
        d = os.path.dirname(file_path)
        while d and d.startswith(src):
            if os.path.exists(d) and not os.listdir(d):
                os.rmdir(d); logger.info(f'删除空目录: {d}')
                d = os.path.dirname(d)
            else:
                break
    except Exception as e:
        logger.debug(f'清理目录出错: {e}')

def refresh_emby():
    key = CONFIG.get('emby_api_key') or ''
    if not key:
        logger.warning('EMBY API密钥未配置，跳过刷新'); return False
    try:
        r = requests.post(f'{CONFIG["emby_host"].rstrip("/")}/Library/Refresh',
                          headers={'X-Emby-Token': key}, json={}, timeout=10)
        if r.status_code == 204:
            logger.success('EMBY库刷新请求已发送'); return True
        logger.warning(f'EMBY刷新失败: HTTP {r.status_code}'); return False
    except Exception as e:
        logger.error(f'EMBY刷新异常: {e}'); return False

# ====================== 任务管理（查看/取消） ======================
def task_manager(api: AlistAPI):
    try:
        undone = api.list_upload_tasks(done=False) or []
        if not undone:
            logger.info('未发现未完成的上传任务。')
            return
        logger.info('未完成的上传任务（最多显示50条）：')
        for i, t in enumerate(undone[:50], 1):
            tid = t.get('id'); name = t.get('name'); pg = t.get('progress')
            stat = t.get('status'); st = t.get('state')
            logger.info(f'  [{i}] id={tid}  state={st}  progress={pg}  name={name}  status={stat}')

        print('\n操作选项：')
        print('  a. 取消所有未完成上传任务')
        print('  i. 按逗号分隔输入要取消的任务ID（例如：id1,id2,...)')
        print('  回车直接返回上传流程')
        op = input('请选择操作: ').strip()
        if not op:
            return
        if op.lower() == 'a':
            for t in undone:
                tid = t.get('id')
                if not tid: continue
                resp = api.cancel_upload_task(tid)
                msg = (resp.get("message") if isinstance(resp, dict) else resp)
                logger.info(f'取消 {tid} → {msg}')
            return
        # 指定ID取消
        ids = [x.strip() for x in op.split(',') if x.strip()]
        for tid in ids:
            resp = api.cancel_upload_task(tid)
            msg = (resp.get("message") if isinstance(resp, dict) else resp)
            logger.info(f'取消 {tid} → {msg}')
    except Exception as e:
        logger.error(f'任务管理异常: {e}')

# ====================== 命令行参数 ======================
def parse_args():
    parser = argparse.ArgumentParser(description='AList API 直传（v4.3.3）')
    parser.add_argument('--quick', action='store_true',
                        help='快速模式：将 verify_wait_secs 调为 180s、verify_per_gb_addon 调为 10s/GB')
    parser.add_argument('--max-size-gb', type=float, default=4.0,
                        help='大小跳过阈值（GB），默认 4GB；设置为 0 表示不跳过')
    parser.add_argument('--qps', type=float, default=None,
                        help='全局 QPS 限速（例如 1.0 表示每秒 1 次请求）；缺省使用 CONFIG.global_qps')
    parser.add_argument('--workers', type=int, default=None,
                        help='覆盖 CONFIG.verify_max_workers 的线程池大小')
    return parser.parse_args()

# ====================== 主程序 ======================
def main():
    args = parse_args()
    # 应用 quick/阈值参数
    if args.quick:
        CONFIG['verify_wait_secs'] = 180
        CONFIG['verify_per_gb_addon'] = 10
    if args.max_size_gb is not None:
        CONFIG['skip_large_bytes'] = max(0, int(args.max_size_gb * 1024**3))
    if args.qps is not None:
        CONFIG['global_qps'] = float(max(0.0, args.qps))
    if args.workers is not None and args.workers > 0:
        CONFIG['verify_max_workers'] = int(args.workers)

    # 初始化全局 RateLimiter（可选）
    global _GLOBAL_RL
    qps = float(CONFIG.get('global_qps') or 0.0)
    if qps > 0.0:
        _GLOBAL_RL = RateLimiter(qps=qps)
        logger.info(f'全局限速：QPS = {qps:.2f}')
    else:
        _GLOBAL_RL = None

    logger.info('=' * 60)
    logger.info('AList 文件上传任务（API直传 /api/fs/put，自动登录，异步校验版 v4.3.3：含 put→form 回退 & 任务追踪 & 任务管理 & 最终统一删除）')
    logger.info(f'源目录: {CONFIG["source_dir"]}')
    logger.info(f'AList:  {CONFIG["alist_url"]}')
    logger.info(f'远程根: {CONFIG["remote_root"]}')
    logger.info(f'日志文件: {logger.log_file}')
    logger.info('=' * 60)

    try:
        api = AlistAPI(CONFIG['alist_url'], CONFIG['username'], CONFIG['password'])
    except Exception as e:
        logger.error(f'无法连接到 AList: {e}'); return

    # 菜单：任务管理 or 上传
    print("\n📋 模式选择")
    print("0. 任务管理（查看/取消 未完成的上传任务）")
    print("1. 测试前3个文件上传")
    print("2. 全量处理源目录下所有文件")
    print("3. 自定义数量上传")
    mode = input("\n请选择 (0-3, 默认1): ").strip()

    if mode == '0':
        task_manager(api)
        logger.info('已完成任务管理，继续进入上传流程 ...')

    logger.info('扫描文件...')
    files = find_all_files(CONFIG['source_dir'])
    if not files:
        logger.warning('未找到任何文件'); return
    logger.info(f'找到 {len(files)} 个文件')

    # 选择处理数量
    if mode == '2':
        files_to_process = files
    elif mode == '3':
        try:
            files_to_process = files[:int(input('请输入要处理的文件数量: '))]
        except:
            files_to_process = files[:3]
    else:
        files_to_process = files[:3]

    logger.info(f'将处理 {len(files_to_process)} 个文件')

    # ===== 统计与清单 =====
    stats = {
        'total': len(files_to_process),
        'success': 0,
        'reuploaded': 0,
        'skipped_same': 0,
        'skipped_large': 0,
        'failed': 0,
        'deleted': 0,
        # 任务成功确认
        'task_succeeded': 0,
        'task_confirmed_deleted': 0,
        'task_succeeded_not_deleted': 0,
        'no_task_but_verified': 0,
        'pending_to_verify': 0,

        # 清单
        'files_skipped_same': [],
        'files_skipped_large': [],
        'files_success_sync': [],
        'files_failed_immediate': [],
        'pending_items': [],
        'files_success_async': [],
        'files_failed_async': [],
        'files_deleted': []
    }

    verify_mgr = AsyncVerifyManager(api)

    for i, local_file in enumerate(files_to_process, 1):
        name = os.path.basename(local_file)
        logger.info(f'处理文件 {i}/{stats["total"]}: {name}')

        rel = os.path.relpath(local_file, CONFIG['source_dir']).replace('\\', '/')
        remote_path = f'{CONFIG["remote_root"].rstrip("/")}/{rel}'

        try:
            local_size = os.path.getsize(local_file)
        except Exception as e:
            logger.error(f'获取文件大小失败: {e}')
            stats['failed'] += 1
            stats['files_failed_immediate'].append({'local': local_file, 'remote': remote_path, 'size': 0, 'reason': 'stat-error'})
            continue

        # 大小阈值跳过
        if CONFIG.get('skip_large_bytes', 0) > 0 and local_size > CONFIG['skip_large_bytes']:
            logger.warning(f'⏭️ 超过大小阈值({CONFIG["skip_large_bytes"]/(1024**3):.0f}GB)，跳过: {name} ({local_size/(1024**3):.2f} GB)')
            stats['skipped_large'] += 1
            stats['files_skipped_large'].append({'local': local_file, 'remote': remote_path, 'size': local_size})
            continue

        info = api.get_file_info(remote_path)
        if info and info.get('size') == local_size:
            stats['skipped_same'] += 1
            stats['files_skipped_same'].append({'local': local_file, 'remote': remote_path, 'size': local_size})
            logger.warning(f'✅ 文件已存在且大小相同，跳过: {name}')
            continue
        elif info and info.get('size') and info.get('size') != local_size:
            logger.warning(f'🔄 文件大小不同: 本地 {api._fmt(local_size)} ≠ 远程 {api._fmt(info.get("size"))}')

        status, msg, task = api.upload_stream(local_file, remote_path)

        if status == 'ok':
            # 非异步校验路径（只在 async_verify=False 才会走）
            stats['success'] += 1
            stats['files_success_sync'].append({'local': local_file, 'remote': remote_path, 'size': local_size})

        elif status == 'pending':
            stats['pending_to_verify'] += 1
            verify_mgr.schedule(remote_path, local_file, local_size, stats, task=task)
            stats['pending_items'].append({
                'local': local_file,
                'remote': remote_path,
                'size': local_size,
                'task_id': (task or {}).get('id')
            })

        else:
            stats['failed'] += 1
            stats['files_failed_immediate'].append({'local': local_file, 'remote': remote_path, 'size': local_size, 'reason': msg})

        logger.info(
            f'进度: {i}/{stats["total"]} | ✅新传: {stats["success"]} | 🔄重传: {stats["reuploaded"]} '
            f'| ⏭️跳过(同/大): {stats["skipped_same"]}/{stats["skipped_large"]} | ❌失败: {stats["failed"]}'
        )
        print()

    # ===== 即时摘要（进入 wait_all 前）=====
    if CONFIG.get('async_verify', True):
        logger.info('—— 即时摘要（进入异步校验等待）——')
        logger.info(f'  待异步校验: {stats["pending_to_verify"]}')
        if stats['pending_items']:
            logger.info('  当前等待校验的文件：')
            for idx, item in enumerate(stats['pending_items'], 1):
                logger.info(f'    [{idx}] name={os.path.basename(item["local"])} | size={AlistAPI._fmt(item["size"])} | remote="{item["remote"]}" | task_id={item.get("task_id") or "n/a"}')
        else:
            logger.info('  （无待校验文件）')
        logger.info(f'  已跳过(同/大): {stats["skipped_same"]}/{stats["skipped_large"]}')
        logger.info(f'  已直接成功(同步路径): {stats["success"]}  |  已失败: {stats["failed"]}')
        
    # 追加：飞书快照卡片
    if CONFIG.get('feishu_enable'):
        feishu_card_snapshot(stats['pending_items'], stats['total'], stats['skipped_same'], stats['skipped_large'])
        
    # 等待所有异步校验完成
    verify_mgr.wait_all()

    # ===== 最终阶段：统一删除本地 & 细分统计 =====
    if verify_mgr.results:
        for res in verify_mgr.results:
            local_file = res['local']
            remote_path = res['remote']
            size = res['size']
            ok = res['ok']
            succeeded_by_task = res['succeeded_by_task']

            if ok:
                if not succeeded_by_task:
                    stats['no_task_but_verified'] += 1

                # 记录异步成功清单
                stats['files_success_async'].append({'local': local_file, 'remote': remote_path, 'size': size, 'task_confirmed': bool(succeeded_by_task)})

                # 删除前“短确认”
                allow_delete = CONFIG.get('delete_local_after_upload', False) and \
                               ( (not CONFIG.get('delete_requires_task_success', True)) or succeeded_by_task )
                if allow_delete:
                    fs_ok = False
                    try:
                        if api.verify_by_list_refresh(remote_path, size, wait_secs=10, tries=3):
                            fs_ok = True
                        elif api.verify_size_direct(remote_path, size, wait_secs=10, tries=3):
                            fs_ok = True
                    except Exception:
                        fs_ok = False

                    if not fs_ok:
                        logger.warning(f'🛑 远端未确认可见（或尺寸未命中），跳过删除本地: {os.path.basename(local_file)}')
                        if succeeded_by_task:
                            stats['task_succeeded_not_deleted'] += 1
                    else:
                        try:
                            os.remove(local_file)
                            cleanup_empty_dirs(local_file)
                            stats['deleted'] += 1
                            stats['files_deleted'].append({'local': local_file, 'remote': remote_path, 'size': size, 'task_confirmed': bool(succeeded_by_task)})
                            if succeeded_by_task:
                                stats['task_confirmed_deleted'] += 1
                            logger.success(f'🗑️ 已删除本地: {os.path.basename(local_file)}')
                        except Exception as e:
                            logger.error(f'删除本地失败(最终阶段): {e}')
                            if succeeded_by_task:
                                stats['task_succeeded_not_deleted'] += 1
                else:
                    if succeeded_by_task:
                        stats['task_succeeded_not_deleted'] += 1
            else:
                # 异步失败清单
                stats['files_failed_async'].append({'local': local_file, 'remote': remote_path, 'size': size, 'reason': 'async-verify-failed'})

    # ===== 最终统计摘要 =====
    logger.info('=' * 60); logger.success('上传任务完成!')
    logger.info('📊 统计摘要(含最终成功确认):')
    logger.info(f'  总文件数: {stats["total"]}')
    logger.info(f'  ✅ 新上传成功(总): {stats["success"]}')
    logger.info(f'  ⏭️ 跳过(大小相同): {stats["skipped_same"]}')
    logger.info(f'  ⏭️ 跳过(超过阈值): {stats["skipped_large"]}')
    logger.info(f'  ❌ 上传失败: {stats["failed"]}')
    logger.info(f'  🗑️ 已删除本地: {stats["deleted"]}')
    logger.info(f'  📌 任务成功(已确认，task.id)：{stats["task_succeeded"]}')
    logger.info(f'    ├─ 已删除本地：{stats["task_confirmed_deleted"]}')
    logger.info(f'    └─ 成功但未删除：{stats["task_succeeded_not_deleted"]}')
    logger.info(f'  🤝 无任务ID但已验证成功：{stats["no_task_but_verified"]}')
    if stats['total'] > 0:
        rate = ((stats['success'] + stats['reuploaded']) / stats['total']) * 100.0
        logger.info(f'  成功率: {rate:.1f}%')
    logger.info('=' * 60)

    # ===== 打印详细文件清单 =====
    def _print_list(title, items, show_reason=False):
        logger.info(title)
        if not items:
            logger.info('  （空）')
            return
        for i, it in enumerate(items, 1):
            name = os.path.basename(it['local'])
            size_h = AlistAPI._fmt(it.get('size', 0))
            msg = f'  [{i}] name={name} | size={size_h} | remote="{it["remote"]}"'
            if show_reason and it.get('reason'):
                msg += f' | reason={it["reason"]}'
            if 'task_confirmed' in it:
                msg += f' | task_confirmed={it["task_confirmed"]}'
            logger.info(msg)

    _print_list('📄 跳过清单（大小相同）:', stats['files_skipped_same'])
    _print_list('📄 跳过清单（超过阈值）:', stats['files_skipped_large'])
    _print_list('📄 成功清单（同步立即成功）:', stats['files_success_sync'])
    _print_list('📄 成功清单（异步确认成功）:', stats['files_success_async'])
    _print_list('📄 已删除本地清单（最终阶段）:', stats['files_deleted'])
    failed_merge = stats['files_failed_immediate'] + stats['files_failed_async']
    _print_list('📄 失败清单（含立即失败与异步校验失败）:', failed_merge, show_reason=True)

    # 追加：飞书最终汇总卡片
    if CONFIG.get('feishu_enable'):
        feishu_card_final_summary(stats, logger.log_file)

    if CONFIG.get('emby_api_key'):
        logger.info('刷新 EMBY 媒体库...')
        refresh_emby()

    logger.info('任务完成!'); logger.info(f'详细日志: {logger.log_file}')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.warning('\n👋 用户中断，任务终止')
    except Exception as e:
        logger.error(f'❌ 程序异常: {e}')
        import traceback; traceback.print_exc()
