"""Report existing login checks; never initiates an additional platform request."""
import json
import os


def report_login(platform, state):
    if os.getenv('MEDIACRAWLER_TASK_ID'):
        print('MC_LOGIN_STATE '+json.dumps({'platform':platform,'state':state}),flush=True)


async def checked_login(check, platform):
    try:
        result=await check
    except Exception:
        report_login(platform,'unknown')
        raise
    report_login(platform,'authenticated' if result else 'needs_local_action')
    return result
