from fastapi import FastAPI
from .routers import prometheus
from .core.generator import generatorMultiThreading as generator
from multiprocessing import Process
import uvicorn
# import logging
import argparse
from os import path, getpid

REDFISH_DATA = '/tmp/redfish-data/'

app = FastAPI(title="Redfish Collector", description="Redfish DMTF Collector using for physical server monitoring")
app.include_router(prometheus.router)
# app.include_router(generator.router)

def uvicornExec(host, port, config_path):
    uvicorn.run("redfish_collector.main:app", host=host, port=port, log_config=config_path)

def main():
    parser = argparse.ArgumentParser(description='Physical Server state Exporter for Prometheus')

    parser.add_argument('--host', type=str, dest='host', default='0.0.0.0', help='address to serve on')
    parser.add_argument('--port', type=int, dest='port', default=9814, help='port to bind')
    ### Temporary disable
    # parser.add_argument('--templatedir', type=str, dest='templatedir', help='Directory Configs')
    # parser.add_argument('--datadir', type=str, dest='datadir', help='Directory Data Old and New saved')

    args = parser.parse_args()
    config_path = path.join(path.dirname(__file__), 'logging/logging.yml')

    if args.host:
        host = args.host
    if args.port:
        port = args.port

    ### Temporary disable
    # if args.templatedir:
    #     template_dir = args.templatedir
    # if args.datadir:
    #     data_dir = args.datadir

    try:
        # logging.info("Main process PID: %s" % getpid())
        collectorProcess = Process(target=generator)
        collectorProcess.start()
        # logging.info("Started Collector with PID: %s, Parent PID: %s" %(collectorProcess.pid,getpid()))
        uvicornProcess = Process(target=uvicornExec, args=(host, port, config_path))
        uvicornProcess.start()
        # logging.info("Started Uvicorn with PID: %s, Parent PID: %s" %(uvicornProcess.pid,getpid()))
        uvicornProcess.join()
    except Exception as err:
        # logging.error("Error: %s" %err)
        collectorProcess.terminate()
        collectorProcess.join()
        # logging.info("Terminated background process")
        return
    except KeyboardInterrupt:
        # logging.info("shutting down")
        collectorProcess.terminate()
        collectorProcess.join()
        # logging.info("Terminated background process")
        return

if __name__ == "__main__":
    main()
