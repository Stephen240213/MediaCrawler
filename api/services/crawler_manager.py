# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/services/crawler_manager.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import subprocess
import signal
import os
from typing import Optional, List
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import sys
from datetime import timezone
from .redaction import redact
from .lease import available

from ..schemas import CrawlerStartRequest, LogEntry
from . import workbench_store
from model.xhs_search_filters import XhsSearchProgress


class CrawlerManager:
    """Crawler process manager"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.status = "idle"
        self.started_at: Optional[datetime] = None
        self.current_config: Optional[CrawlerStartRequest] = None
        self._log_id = 0
        self._logs: List[LogEntry] = []
        self._read_task: Optional[asyncio.Task] = None
        # Project root directory
        self._project_root = Path(__file__).parent.parent.parent
        # Log queue - for pushing to WebSocket
        self._log_queue: Optional[asyncio.Queue] = None
        self.task_id = None
        self.task = None
        self.error_message = None
        self.last_start_result = None

    def _finish_task(self, outcome, exit_code=None):
        if self.task and self.task['outcome'] == 'running':
            self.task.update(outcome=outcome, finished_at=datetime.now(timezone.utc).isoformat(), exit_code=exit_code)
            workbench_store.save_document('task', self.task)
            from . import asset_catalog
            from api.routers.workbench import DATA_ROOT
            asset_catalog.invalidate(DATA_ROOT)

    @property
    def logs(self) -> List[LogEntry]:
        if self.task_id:
            return [LogEntry(**row) for row in workbench_store.task_logs(self.task_id)]
        return self._logs

    def get_log_queue(self) -> asyncio.Queue:
        """Get or create log queue"""
        if self._log_queue is None:
            self._log_queue = asyncio.Queue()
        return self._log_queue

    def _create_log_entry(self, message: str, level: str = "info") -> LogEntry:
        """Create log entry"""
        raw_message=message
        message = redact(message, (self.current_config.cookies if self.current_config else '',))
        if message.startswith('MC_CONTENT ') and self.task:
            from . import collection_batches
            try:
                event=collection_batches.ContentEvent.model_validate_json(raw_message.removeprefix('MC_CONTENT '))
                event.title=redact(event.title,(self.current_config.cookies if self.current_config else '',))
                collection_batches.record(self.task['id'],event)
                snapshot=collection_batches.members(self.task['id'])
                self.task['dataset_counts']={**snapshot['counts'],'total':snapshot['total']}
                workbench_store.save_document('task',self.task)
                message=f'批次资料 · {event.action} · {event.content_id}'
            except ValueError:pass
        if message.startswith('MC_LOGIN_STATE ') and self.task:
            try:
                event=json.loads(message.removeprefix('MC_LOGIN_STATE '))
                state=event.get('state')
                if event.get('platform')==self.task['platform'] and state in ('unknown','authenticated','needs_local_action'):
                    self.task.update(login_state=state, login_observed_at=datetime.now(timezone.utc).isoformat())
                    workbench_store.save_document('task',self.task)
                    labels={'unknown':'登录流程状态待确认','authenticated':'已有登录检查通过','needs_local_action':'需要在采集电脑完成登录或人工处理'}
                    message=labels[state]
            except (ValueError,TypeError,AttributeError):
                pass
        if message.startswith('MC_PROGRESS ') and self.task:
            try:
                progress = XhsSearchProgress.model_validate_json(message.removeprefix('MC_PROGRESS '))
                self.task['progress'] = progress.model_dump()
                workbench_store.save_document('task', self.task)
                message = f'网页筛选 · {progress.phase} · {progress.keyword} · 已采集 {progress.collected} · {progress.stop_reason}'
            except ValueError:
                pass
        self._log_id += 1
        stamp = datetime.now(timezone.utc).isoformat()
        if self.task_id:
            self._log_id = workbench_store.append_log(self.task_id, stamp, level, message)
        entry = LogEntry(
            id=self._log_id,
            timestamp=stamp,
            task_id=self.task_id,
            level=level,
            message=message
        )
        self._logs.append(entry)
        # Keep last 500 logs
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]
        return entry

    async def _push_log(self, entry: LogEntry):
        """Push log to queue"""
        if self._log_queue is not None:
            try:
                self._log_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def _parse_log_level(self, line: str) -> str:
        """Parse log level"""
        line_upper = line.upper()
        if "ERROR" in line_upper or "FAILED" in line_upper:
            return "error"
        elif "WARNING" in line_upper or "WARN" in line_upper:
            return "warning"
        elif "SUCCESS" in line_upper or "完成" in line or "成功" in line:
            return "success"
        elif "DEBUG" in line_upper:
            return "debug"
        return "info"

    async def start(self, config: CrawlerStartRequest, request_key=None, actor="local") -> bool:
        """Start crawler process"""
        async with self._lock:
            if config.collection_method == 'redfox_mcp':
                from .redfox import credential
                if not credential(config.platform.value):
                    raise ValueError('未找到 RedFox API Key，请配置采集电脑的 REDFOX_API_KEY 或 Codex MCP 持久配置')
            request_key = request_key or uuid4().hex
            fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
            candidate = {'id':uuid4().hex, 'platform':config.platform.value,
                         'crawler_type':config.crawler_type.value, 'started_at':datetime.now(timezone.utc).isoformat(),
                         'finished_at':None, 'outcome':'running', 'exit_code':None,
                         'target':redact(config.keywords or config.specified_ids or config.creator_ids), 'actor':actor,
                         'login_state':'unknown', 'login_message':'需要登录或验证码时，请在采集电脑的浏览器完成；工作台不远程控制桌面。'}
            candidate['collection_method'] = config.collection_method
            if config.collection_method == 'redfox_mcp':
                candidate.update(mcp_tool=config.mcp_tool, target=config.mcp_tool,
                                 login_message='RedFox MCP 使用服务凭据，无需浏览器登录')
            if config.xhs_filters:
                candidate['xhs_filters']=config.xhs_filters.model_dump()
            import config as runtime_config
            candidate['effective_options']=dict(start_page=config.start_page,max_notes_count=config.max_notes_count if config.max_notes_count is not None else runtime_config.CRAWLER_MAX_NOTES_COUNT,
                max_comments_count=config.max_comments_count if config.max_comments_count is not None else runtime_config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,enable_media=config.enable_media,enable_comments=config.enable_comments,save_option=config.save_option.value)
            if config.platform.value=='xhs' and config.collection_method == 'browser':candidate['membership_version']=1
            admission, document = workbench_store.reserve_task(candidate,actor,request_key,fingerprint)
            if admission == 'conflict':
                raise ValueError('同一请求标识不能用于不同参数')
            if admission == 'replay':
                self.last_start_result={'task_id':document['id'],'replayed':True}
                return True
            if admission == 'busy':
                return False
            self.task_id=candidate['id']; self.task=candidate
            if not available(workbench_store.DB_PATH.parent/'execution.lock'):
                self.error_message='存在遗留采集进程，请在采集电脑检查后重试'
                self._finish_task('interrupted')
                self.status='error'
                return False
            self.last_start_result={'task_id':self.task_id,'replayed':False}

            # Clear old logs
            self._logs = []
            self._log_id = 0

            # Clear pending queue (don't replace object to avoid WebSocket broadcast coroutine holding old queue reference)
            if self._log_queue is None:
                self._log_queue = asyncio.Queue()
            else:
                try:
                    while True:
                        self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            # Build command line arguments
            cmd = self._build_command(config)
            self.current_config = config
            self.error_message = None
            self.started_at = datetime.now(timezone.utc)
            # Log start information
            entry = self._create_log_entry(f"Starting crawler: {' '.join(cmd)}", "info")
            await self._push_log(entry)

            try:
                # Start subprocess
                self.process = subprocess.Popen(
                    [sys.executable, '-m', 'api.task_runner', *cmd],
                    start_new_session=(os.name != 'nt'),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    bufsize=1,
                    cwd=str(self._project_root),
                    env={**os.environ, "PYTHONUNBUFFERED": "1", "MEDIACRAWLER_TASK_ID": self.task_id, "MEDIACRAWLER_TASK_COOKIES": config.cookies, "MEDIACRAWLER_EXECUTION_LOCK": str(workbench_store.DB_PATH.parent/"execution.lock"),
                         **({'MEDIACRAWLER_MCP_REQUEST': config.model_dump_json()} if config.collection_method == 'redfox_mcp' else {})}
                )

                self.status = "running"
                self.started_at = datetime.now(timezone.utc)
                self.current_config = config

                entry = self._create_log_entry(
                    f"Crawler started on platform: {config.platform.value}, type: {config.crawler_type.value}",
                    "success"
                )
                await self._push_log(entry)

                # Start log reading task
                self._read_task = asyncio.create_task(self._read_output())

                return True
            except Exception as e:
                self.status = "error"
                self.error_message = redact(e, (self.current_config.cookies if self.current_config else '',))
                self._finish_task('failed')
                entry = self._create_log_entry(f"Failed to start crawler: {str(e)}", "error")
                await self._push_log(entry)
                return False

    async def stop(self, task_id=None, outcome='cancelled') -> bool:
        """Stop crawler process"""
        async with self._lock:
            if task_id is not None and task_id != self.task_id:
                raise ValueError('任务已变更，请刷新状态后重试')
            if not self.process or self.process.poll() is not None:
                return False

            self.status = "stopping"
            entry = self._create_log_entry("Sending SIGTERM to crawler process...", "warning")
            await self._push_log(entry)

            try:
                if os.name == 'nt':
                    await asyncio.to_thread(subprocess.run, ['taskkill','/PID',str(self.process.pid),'/T'], capture_output=True, check=False)
                else:
                    os.killpg(self.process.pid, signal.SIGTERM)

                # Wait for graceful exit (up to 15 seconds)
                for _ in range(30):
                    if self.process.poll() is not None:
                        break
                    await asyncio.sleep(0.5)

                # If still not exited, force kill
                if self.process.poll() is None:
                    entry = self._create_log_entry("Process not responding, sending SIGKILL...", "warning")
                    await self._push_log(entry)
                    if os.name == 'nt':
                        await asyncio.to_thread(subprocess.run, ['taskkill','/PID',str(self.process.pid),'/T','/F'], capture_output=True, check=False)
                    else:
                        os.killpg(self.process.pid, signal.SIGKILL)
                await asyncio.to_thread(self.process.wait, timeout=5)

                entry = self._create_log_entry("Crawler process terminated", "info")
                await self._push_log(entry)

            except Exception as e:
                entry = self._create_log_entry(f"Error stopping crawler: {str(e)}", "error")
                await self._push_log(entry)
                if self.process and self.process.poll() is None:
                    self.status = 'error'
                    self.error_message = redact(e, (self.current_config.cookies if self.current_config else '',))
                    return False

            self._finish_task(outcome, self.process.returncode)
            self.status = "idle"
            self.current_config = None

            # Cancel log reading task
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None

            return True

    def recover(self):
        active = self.task_id if self.process and self.process.poll() is None else None
        workbench_store.recover_tasks(active)
        if not active:
            tasks=workbench_store.list_documents('task')
            self.task=max(tasks,key=lambda t:t.get('started_at',''),default=None)
            self.task_id=self.task['id'] if self.task else None
            self.status='idle'
            if not available(workbench_store.DB_PATH.parent/'execution.lock'):
                self.status='error'
                self.error_message='检测到上次服务遗留的执行进程；请在采集电脑检查，不会自动恢复或重复采集。'

    def get_status(self) -> dict:
        """Get current status"""
        return {
            "status": self.status,
            "platform": self.current_config.platform.value if self.current_config else None,
            "crawler_type": self.current_config.crawler_type.value if self.current_config else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "error_message": self.error_message,
            "task": self.task,
        }

    def _build_command(self, config: CrawlerStartRequest) -> list:
        """Build main.py command line arguments"""
        if config.collection_method == 'redfox_mcp':
            return [sys.executable, '-m', 'api.services.redfox']
        cmd = ["uv", "run", "python", "main.py"]

        cmd.extend(["--platform", config.platform.value])
        cmd.extend(["--lt", config.login_type.value])
        cmd.extend(["--type", config.crawler_type.value])
        cmd.extend(["--save_data_option", config.save_option.value])

        # Pass different arguments based on crawler type
        if config.crawler_type.value == "search" and config.keywords:
            cmd.extend(["--keywords", config.keywords])
        elif config.crawler_type.value == "detail" and config.specified_ids:
            cmd.extend(["--specified_id", config.specified_ids])
        elif config.crawler_type.value == "creator" and config.creator_ids:
            cmd.extend(["--creator_id", config.creator_ids])

        if config.start_page != 1:
            cmd.extend(["--start", str(config.start_page)])

        cmd.extend(["--get_comment", "true" if config.enable_comments else "false"])
        cmd.extend(["--get_sub_comment", "true" if config.enable_sub_comments else "false"])
        cmd.extend(["--get_media", "true" if config.enable_media else "false"])

        if config.max_notes_count is not None:
            cmd.extend(["--crawler_max_notes_count", str(config.max_notes_count)])

        if config.max_comments_count is not None:
            cmd.extend(["--max_comments_count_singlenotes", str(config.max_comments_count)])


        if config.xhs_filters is not None:
            cmd.extend(['--xhs_filters', config.xhs_filters.model_dump_json()])

        cmd.extend(["--headless", "true" if config.headless else "false"])

        return cmd

    async def _read_output(self):
        """Asynchronously read process output"""
        loop = asyncio.get_event_loop()

        try:
            while self.process and self.process.poll() is None:
                # Read a line in thread pool
                line = await loop.run_in_executor(
                    None, self.process.stdout.readline
                )
                if line:
                    line = line.strip()
                    if line:
                        level = self._parse_log_level(line)
                        entry = self._create_log_entry(line, level)
                        await self._push_log(entry)

            # Read remaining output
            if self.process and self.process.stdout:
                remaining = await loop.run_in_executor(
                    None, self.process.stdout.read
                )
                if remaining:
                    for line in remaining.strip().split('\n'):
                        if line.strip():
                            level = self._parse_log_level(line)
                            entry = self._create_log_entry(line.strip(), level)
                            await self._push_log(entry)

            # Process ended
            if self.status == "running":
                exit_code = self.process.returncode if self.process else -1
                if exit_code == 0:
                    entry = self._create_log_entry("Crawler completed successfully", "success")
                    self._finish_task('completed', exit_code)
                else:
                    entry = self._create_log_entry(f"Crawler exited with code: {exit_code}", "warning")
                    self.error_message = f'采集进程退出，退出码 {exit_code}'
                    self._finish_task('failed', exit_code)
                await self._push_log(entry)
                self.status = "idle" if exit_code == 0 else "error"

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error_message = redact(e, (self.current_config.cookies if self.current_config else '',))
            entry = self._create_log_entry(f"Error reading output: {str(e)}", "error")
            await self._push_log(entry)


# Global singleton
crawler_manager = CrawlerManager()
