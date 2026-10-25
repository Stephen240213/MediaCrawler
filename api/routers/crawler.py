# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/routers/crawler.py
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

from fastapi import APIRouter, HTTPException, Header, Request, Query
from pydantic import BaseModel, Field
from typing import Optional
from ..services import workbench_store

from ..schemas import CrawlerStartRequest, CrawlerStatusResponse
from ..services import crawler_manager

router = APIRouter(prefix="/crawler", tags=["crawler"])


@router.post("/start")
async def start_crawler(request: CrawlerStartRequest, http_request: Request, idempotency_key: Optional[str] = Header(None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")):
    """Start crawler task"""
    actor=getattr(http_request.state,'identity',{}).get('sub','local')
    try:
        success = await crawler_manager.start(request,request_key=idempotency_key,actor=actor)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if not success:
        # Handle concurrent/duplicate requests: if process is already running, return 400 instead of 500
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")

    return {"status": "ok", "message": "Crawler started successfully", **(crawler_manager.last_start_result or {})}


class StopRequest(BaseModel):
    task_id: str = Field(min_length=1,max_length=128)


@router.post("/stop")
async def stop_crawler(payload: StopRequest):
    """Stop crawler task"""
    try:
        success = await crawler_manager.stop(payload.task_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from exc
    if not success:
        # Handle concurrent/duplicate requests: if process already exited/doesn't exist, return 400 instead of 500
        if not crawler_manager.process or crawler_manager.process.poll() is not None:
            raise HTTPException(status_code=400, detail="No crawler is running")
        raise HTTPException(status_code=500, detail="Failed to stop crawler")

    return {"status": "ok", "message": "Crawler stopped successfully"}


@router.get("/status", response_model=CrawlerStatusResponse)
async def get_crawler_status():
    """Get crawler status"""
    return crawler_manager.get_status()


@router.get("/logs")
async def get_logs(limit: int = Query(100,ge=1,le=500), task_id: Optional[str]=None, after: int=Query(0,ge=0), before: Optional[int]=Query(None,ge=1)):
    """Get recent logs"""
    if before is not None and after:
        raise HTTPException(422,'before 与 after 不能同时使用')
    task_id=task_id or crawler_manager.task_id
    return {"logs":workbench_store.task_logs(task_id,after,limit,before) if task_id else [],"task_id":task_id}


@router.get('/login-state')
async def login_state(platform: Optional[str]=None):
    tasks=workbench_store.list_documents('task')
    task=max((t for t in tasks if not platform or t.get('platform')==platform), key=lambda t:t.get('started_at',''),default=None)
    observed=task.get('login_observed_at') if task else None
    state=task.get('login_state','unknown') if task else 'unknown'
    labels={'authenticated':'上次任务的已有登录检查通过；不代表当前登录仍然有效。',
            'needs_local_action':'上次检查需要登录或人工处理，请在采集电脑的浏览器完成。',
            'unknown':'平台登录状态尚未通过独立检查确认；二维码、验证码或风控步骤需要在采集电脑完成。'}
    return {'platform':platform or (task.get('platform') if task else None), 'state':state,
            'observed_at':observed,'message':labels.get(state,labels['unknown']),
            'requires_local_browser':True,'remote_desktop':False,'qr_available':False}
