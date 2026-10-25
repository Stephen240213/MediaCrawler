"""Single executor, separate loopback listeners. Run: python -m api.serve.

No production listener binds 0.0.0.0. Only the guarded listener is tunneled.
"""
import argparse
import asyncio
import signal
from pathlib import Path
import uvicorn


async def run(local_port=8080, remote_port=8081, remote=True):
    from api.main import app
    from api.services import crawler_manager, workbench_store
    from api.services.access import AccessSettings, Boundary
    from api.services.lease import Lease
    if local_port==remote_port:
        raise ValueError('本机端口与远程受保护端口必须不同')
    settings=AccessSettings.from_env() if remote else None
    with Lease(workbench_store.DB_PATH.parent/'service.lock'):
        async with app.router.lifespan_context(app):
            local=Boundary(app,local_origin=f'http://127.0.0.1:{local_port}')
            applications=[(local,local_port)]
            if remote:
                applications.append((Boundary(app,settings),remote_port))
            servers=[]
            for application,port in applications:
                # Do not trust forwarded addresses/Host from arbitrary clients.
                server=uvicorn.Server(uvicorn.Config(application,host='127.0.0.1',port=port,workers=1,lifespan='off',proxy_headers=False,access_log=False))
                server.install_signal_handlers=lambda:None
                servers.append(server)
            loop=asyncio.get_running_loop()
            def stop(*_):
                for server in servers:
                    server.should_exit=True
            for sig in (signal.SIGINT,signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig,stop)
                except NotImplementedError:
                    signal.signal(sig,stop)
            tasks=[asyncio.create_task(server.serve()) for server in servers]
            try:
                await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop()
                # Signal only the task supervised by this process. Never kill an
                # existing external CDP browser or guess a PID from an old database.
                if crawler_manager.process and crawler_manager.process.poll() is None:
                    await crawler_manager.stop(crawler_manager.task_id,outcome='interrupted')
                await asyncio.gather(*tasks,return_exceptions=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-port',type=int,default=8080)
    parser.add_argument('--remote-port',type=int,default=8081)
    parser.add_argument('--local-only',action='store_true')
    parser.add_argument('--env-file',help='本机私有环境文件路径，不提交 Git')
    args=parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not Path(args.env_file).is_file():
            parser.error('配置文件不存在')
        load_dotenv(args.env_file,override=False)
    import os
    os.environ.setdefault('UV_FROZEN','1')
    if os.getenv('MC_TOOL_PATH'):
        os.environ['PATH']=os.environ['MC_TOOL_PATH']+os.pathsep+os.environ.get('PATH','')
    for port in (args.local_port,args.remote_port):
        if not 1024<=port<=65535:
            parser.error('端口须为 1024 到 65535')
    asyncio.run(run(args.local_port,args.remote_port,not args.local_only))


if __name__=='__main__':
    main()
