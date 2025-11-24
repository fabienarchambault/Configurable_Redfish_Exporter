from fastapi import HTTPException,APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import IPvAnyAddress
from starlette import status
from ..core.rawCollector import jsonpathCollector,readYAMLTemplate
from os import path
import re
import json
import logging
import time
import yaml
from prometheus_client import generate_latest, Summary, REGISTRY, PROCESS_COLLECTOR, PLATFORM_COLLECTOR, Gauge, CollectorRegistry
# from ..config import Settings

REDFISH_DATA = '/tmp/redfish-data/'

# settings = Settings()
templateDir = config_path = path.join(path.dirname(__file__), '../core/templates/')

REGISTRY.unregister(PROCESS_COLLECTOR)
REGISTRY.unregister(PLATFORM_COLLECTOR)
REGISTRY.unregister(REGISTRY._names_to_collectors['python_gc_objects_collected_total'])
# registry = REGISTRY
REQUEST_TIME = Summary(
    'request_processing_seconds', 'Time spent processing request')
CACHE={}


router = APIRouter(
    prefix='/metrics',
    tags=['Prometheus Metrics']
)


@router.get("", status_code=status.HTTP_200_OK)
async def read_all(serverAddress: IPvAnyAddress = Query(None), \
                    # username: str = Query(None), \
                    # password: str = Query(None), \
                    config: str = Query(None)):
    # if (serverAddress is None) or (config is None) or (username is None) or (password is None):
    if (serverAddress is None) or (config is None):
    # if (serverAddress is None) or (config is None):
        raise HTTPException(status_code=401, detail = 'Collect metrics Failed, please add params')

    # if path.isfile(config_file_path):
    else:
        inventoryFile = REDFISH_DATA + 'inventory.yml'
        try:
            metricsConfigFile = templateDir + "configs/" + config + ".yml"
            metricsConfig = readYAMLTemplate(metricsConfigFile)
            if "Auth" in metricsConfig:
                username = metricsConfig['Auth']['Username']
                password = metricsConfig['Auth']['Password']
            else:
                logging.error("[%s] Can't find Auth in config file %s" % (serverAddress,config))
                return False
            cacheInfo="%s%s" % (serverAddress,config)
            if cacheInfo in CACHE:
                cachedResponse, timestamp = CACHE[cacheInfo]
                if time.time() - timestamp < int(60):
                    logging.info("[%s] Complete gathering health-check information from cache (1 minute cache to avoid DOS)" %serverAddress)
                    return PlainTextResponse(cachedResponse)

            with open(inventoryFile, 'r') as f:
                yamlContent = f.read()
                inventory = yaml.safe_load(yamlContent)
            block = {'serverAddress': str(serverAddress), 'username': str(username), 'password': str(password) ,'timeCalled': time.time()}
            if not inventory:
                with open('%sinventory.yml' %REDFISH_DATA, 'w') as f:
                    yaml.dump([block], f, default_flow_style=False)
            else:
                existed = False
                for server in inventory:
                    if server['serverAddress'] == str(serverAddress):
                        server['username'] = str(username)
                        server['password'] = str(password)
                        server['timeCalled'] = time.time()
                        existed = True
                    # if float(server['timeCalled']) > time.time() - 300:
                    #     inventory.remove(server)
                if existed is False:
                    inventory.append(block)
                with open('%sinventory.yml' %REDFISH_DATA, 'w') as f:
                    yaml.dump(inventory, f, default_flow_style=False)
        except Exception as err:
            logging.error("Generate instance failed: %s" %err)
            return False

    try:
        start_time = time.time()
        dataDir = '/tmp/redfish-data/NewData/%s.json' %serverAddress 
        # config_file_path = path.join(path.dirname(__file__), dataDir)
        with open(dataDir, 'r') as file:
            # dataContent = f.read()
            collectedData = json.load(file)
            # return data

        hostName = collectedData['Common'][0]['HostName']
        registry = CollectorRegistry()
        componentMetrics={}
        for metric in metricsConfig['Metrics']:
            standard = ['Name', 'Description', 'Label', 'Datapoint', 'Result', 'Type']
            errorFlag = 0
            for key in standard:
                # logging.info("[%s] Key: %s" % (serverAddress, key))
                if key not in metric:
                    logging.error("[%s] Can't find %s in metrics key, please check again!" % (serverAddress, key))
                    errorFlag = 1
            if errorFlag == 1:
                continue
            else:
                if metric['Type'] == 'Gauge':
                    componentMetrics[metric['Name']] = Gauge(metric['Name'],metric['Description'],metric['Label'],registry=registry)
                    elements = metric['Datapoint'].split('.')
                    logging.debug("[%s] Split datapoint %s to %s" % (serverAddress,metric['Datapoint'],elements))
                    if isinstance(elements,list):
                        firstPointData = collectedData
                    else:
                        logging.error("[%s] Elements after splited isn't a list: %s" % (serverAddress,elements))
                        continue
                    # logging.error("Fist Point: %s" % firstPointData)
                    idList = jsonpathCollector(firstPointData,str("$..Id"),output='fullpath&value')
                    for memberID in idList:
                        if elements[-1] in memberID and elements[0] in memberID:
                            labelList = list()
                            for label in metric['Label']:
                                if label == 'ServerAddress':
                                    labelList.append(serverAddress)
                                elif label == 'HostName':
                                    labelList.append(hostName)
                                else:
                                    newJSONPath = re.sub('Id', label, memberID)
                                    result = jsonpathCollector(firstPointData,newJSONPath)
                                    if result is not False:
                                        labelList.append(result[0])
                                    else:
                                        labelList.append('Unknown')
                                        continue
                            logging.debug("[%s] List Label: %s" % (serverAddress,labelList))
                            if 'State' in metric['Result'] or 'Health' in metric['Result']:
                                if 'StatusCode' not in metric:
                                    logging.error("[%s] Can't find StatusCode, please check again!" % (serverAddress))
                                    continue
                                state = 'Status.' + metric['Result']
                                newJSONPath = re.sub('Id', state, memberID)
                                logging.debug("[%s] newJSONPath: %s" % (serverAddress,newJSONPath))
                                value = jsonpathCollector(firstPointData,str(newJSONPath))
                                if value is False:
                                    logging.error("[%s] Value for %s isn't existed: %s" % (serverAddress,str(newJSONPath),value))
                                    codeNumber = 999
                                elif value is None:
                                    logging.warning("[%s] Value for %s is None: %s" % (serverAddress,str(newJSONPath),value))
                                    codeNumber = 99
                                elif value[0] is None:
                                    logging.warning("[%s] Value[0] for %s is None: %s" % (serverAddress,str(newJSONPath),value[0]))
                                    codeNumber = 99
                                else:
                                    if value[0].upper() in metric['StatusCode']:
                                        codeNumber = metric['StatusCode'][value[0].upper()]
                                        logging.debug("[%s] Value and CodeNumber: %s and %s" % (serverAddress,value,codeNumber))
                                    else:
                                        logging.error("[%s] Maybe value isn't correct: %s" % (serverAddress,value))
                                        codeNumber = 999
                                componentMetrics[metric['Name']].labels(*labelList).set(float(codeNumber))
                            else:
                                newJSONPath = re.sub('Id', metric['Result'], memberID)
                                value = jsonpathCollector(firstPointData,str(newJSONPath))
                                if value is False:
                                    componentMetrics[metric['Name']].labels(*labelList).set(999)
                                else:
                                    value =value[0]
                                logging.debug("Value type: %s" % type(value))
                                if isinstance(value,int) or isinstance(value,float):
                                    componentMetrics[metric['Name']].labels(*labelList).set(float(value))       
                                else:
                                    logging.error("[%s] Value %s isn't float: %s" % (serverAddress,metric['Result'],value))
                                    componentMetrics[metric['Name']].labels(*labelList).set(999)   
                            logging.debug("[%s] ID List Collected with in tree %s to %s" % (serverAddress,metric['Datapoint'],labelList))    
                        # else: 
                        #     logging.error("[%s] elements[-1] %s and elements[0] %s and memberID %s " % (serverAddress,elements[-1],elements[0],memberID))
                else:
                    logging.error("[%s] Not found Type %s, please call Admin" % (serverAddress, metric['Type']))

        metrics = generate_latest(registry)
        REQUEST_TIME.observe(time.time() - start_time)
        CACHE[cacheInfo] = (metrics, time.time())
        return PlainTextResponse(metrics)
            # inventory = yaml.safe_load(yamlContent)
    except Exception as err:
        logging.error("[%s] Read Inventory Configs failed: %s" %(serverAddress,err))
        return
