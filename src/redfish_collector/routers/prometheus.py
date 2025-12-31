from fastapi import HTTPException,APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import IPvAnyAddress
from starlette import status
from ..core.dataReconstruction import dataReconstructor
from ..core.rawCollector import dataCollector,jsonpathCollector,readYAMLTemplate
from os import path,makedirs
import re
import json
import logging
import time
from prometheus_client import generate_latest, Gauge, CollectorRegistry


def validate_server_address(serverAddress: str):
    """Validate that the serverAddress is a valid IP address with optional port."""
    # Split into address and port parts
    if ":" in serverAddress:
        parts = serverAddress.rsplit(":", 1)
        if len(parts) == 2:
            ip_address = parts[0]
            port = parts[1]
            # Validate IP address
            try:
                IPvAnyAddress(ip_address)
            except:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                   detail=f"Invalid IP address: {ip_address}")
            # Validate port
            if not port.isdigit() or int(port) < 1 or int(port) > 65535:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                  detail=f"Invalid port: {port}")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                              detail="Invalid serverAddress format")
    else:
        # No port specified, validate as plain IP address
        try:
            IPvAnyAddress(serverAddress)
        except:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                              detail=f"Invalid IP address: {serverAddress}")

REDFISH_DATA = '/tmp/redfish-data/'

templateDir = config_path = path.join(path.dirname(__file__), '../core/templates/')
CACHE={}

router = APIRouter(
    prefix='/metrics',
    tags=['Prometheus Metrics']
)

@router.get("", status_code=status.HTTP_200_OK)
async def read_all(serverAddress: str = Query(None), config: str = Query(None), loglevel: str = Query("info")) -> PlainTextResponse:
    componentMetrics={}
    if (serverAddress is None) or (config is None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail = 'Collect metrics Failed, please add params')
    else: 
        # Validate serverAddress format
        validate_server_address(serverAddress)

        # Set logging level based on the loglevel parameter
        import logging
        logger = logging.getLogger()
        numeric_level = getattr(logging, loglevel.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError(f'Invalid log level: {loglevel}')
        logger.setLevel(numeric_level)
        logging.info(f"Set logging level to {loglevel.upper()}")

        registry = CollectorRegistry()
        componentMetrics['PhysicalServer_Query'] = Gauge('PhysicalServer_Query','physical server query status',['ServerAddress'],registry=registry)
        # timeCalled = time.ctime()
        serverAddress = str(serverAddress)
        if not path.exists(REDFISH_DATA):
            logging.info(f"Directory {REDFISH_DATA} does not exist. Creating it.")
            makedirs(REDFISH_DATA)
        try:
            metricsConfigFile = f'{templateDir}configs/{config}.yml'
            metricsConfig = readYAMLTemplate(metricsConfigFile)
            if "Auth" not in metricsConfig:
                logging.error(f"[{serverAddress}] Can't find Auth in config file {config}")
                componentMetrics['PhysicalServer_Query'].labels(str(serverAddress)).set(0)
                metrics = generate_latest(registry)
                logging.error(f"[{serverAddress}] Auth details missing in configuration file", exc_info=True)
                return PlainTextResponse(metrics)
            username = metricsConfig['Auth']['Username']
            password = metricsConfig['Auth']['Password']

            cacheInfo= f"{serverAddress}{config}"
            if cacheInfo in CACHE:
                cachedResponse, timestamp = CACHE[cacheInfo]
                if time.time() - timestamp < 180:
                    logging.info(f"[{serverAddress}] Serving health-check from cache.")
                    return PlainTextResponse(cachedResponse)
                
        except Exception as err:
            componentMetrics['PhysicalServer_Query'].labels(str(serverAddress)).set(0)
            metrics = generate_latest(registry)
            logging.error(f"[{serverAddress}] Metric collection failed: {err}", exc_info=True)
            return PlainTextResponse(metrics)
    try:
        dataRaw, dataNewSchema, modelSchemaDir = await dataCollector(serverAddress,username,password,templateDir,loglevel)
        logging.debug("[%s] dataNewSchema: %s" % (serverAddress, dataNewSchema))
        dataReconstructor(dataRaw, dataNewSchema, modelSchemaDir, serverAddress,loglevel)

        dataDir = f'{REDFISH_DATA}NewData/{serverAddress}.json'
        with open(dataDir, 'r') as file:
            collectedData = json.load(file)

        hostName = collectedData['Common'][0]['HostName']
        for metric in metricsConfig['Metrics']:
            standard = ['Name', 'Description', 'Label', 'Datapoint', 'Result', 'Type']
            errorFlag = 0
            for key in standard:
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

                    if not isinstance(collectedData.get(elements[0]), list):
                        logging.warning(f"[{serverAddress}] Data point root is not a list: {elements[0]}")
                        continue

                    # Special handling for Sensors metric
                    if metric['Name'] == 'Sensors' and len(elements) == 2 and elements[0] == 'Sensors' and elements[1] == 'DeviceName':
                        logging.debug("[%s] Processing Sensors metric with special logic" % serverAddress)
                        sensors_list = collectedData.get('Sensors', [{}])[0].get('Sensors', [])
                        for sensor in sensors_list:
                            labelList = [serverAddress]
                            if 'HostName' in metric['Label']:
                                labelList.append(hostName)
                            if 'DeviceName' in metric['Label']:
                                labelList.append(sensor.get('DeviceName', 'Unknown'))

                            logging.debug("[%s] Sensor label list: %s" % (serverAddress, labelList))

                            # Get the reading value
                            reading = sensor.get('Reading')
                            if reading is not None:
                                if isinstance(reading, (int, float)):
                                    componentMetrics[metric['Name']].labels(*labelList).set(float(reading))
                                else:
                                    logging.error("[%s] Sensor reading is not a number: %s" % (serverAddress, reading))
                                    componentMetrics[metric['Name']].labels(*labelList).set(999)
                            else:
                                logging.error("[%s] Sensor reading is None for device: %s" % (serverAddress, sensor.get('DeviceName', 'Unknown')))
                                componentMetrics[metric['Name']].labels(*labelList).set(999)
                    else:
                        idList = jsonpathCollector(collectedData,str("$..Id"),output='fullpath&value')
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
                                        result = jsonpathCollector(collectedData,newJSONPath)
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
                                value = jsonpathCollector(collectedData,str(newJSONPath))
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
                                        logging.error("[%s] Maybe value isn't correct at %s: %s" % (serverAddress, metric['Name'],value))
                                        codeNumber = 999
                                componentMetrics[metric['Name']].labels(*labelList).set(float(codeNumber))
                            else:
                                newJSONPath = re.sub('Id', metric['Result'], memberID)
                                value = jsonpathCollector(collectedData,str(newJSONPath))
                                if value is False:
                                    logging.error("[%s] Value for %s isn't existed: %s" % (serverAddress,str(newJSONPath),value))
                                    componentMetrics[metric['Name']].labels(*labelList).set(999)
                                else:
                                    value = value[0]
                                    logging.debug("Value type: %s" % type(value))
                                    if isinstance(value,int) or isinstance(value,float):
                                        componentMetrics[metric['Name']].labels(*labelList).set(float(value))
                                    else:
                                        logging.error("[%s] Value %s isn't float: %s" % (serverAddress,metric['Result'],value))
                                        componentMetrics[metric['Name']].labels(*labelList).set(999)
                            logging.debug("[%s] ID List Collected with in tree %s to %s" % (serverAddress,metric['Datapoint'],labelList)) 
                else:
                    logging.error("[%s] Not found Type %s, please call Admin" % (serverAddress, metric['Type']))

        componentMetrics['PhysicalServer_Query'].labels(str(serverAddress)).set(1)
        metrics = generate_latest(registry)
        CACHE[cacheInfo] = (metrics, time.time())
        return PlainTextResponse(metrics)

    except Exception as err:
        componentMetrics['PhysicalServer_Query'].labels(str(serverAddress)).set(0)
        metrics = generate_latest(registry)
        logging.error(f"[{serverAddress}] Metric collection failed: {err}", exc_info=True)
        rawPath = f'{REDFISH_DATA}RawData/{serverAddress}.json'
        newPath = f'{REDFISH_DATA}NewData/{serverAddress}.json'
        try:
            open(rawPath, 'w').close()
            open(newPath, 'w').close()
        except IOError as e:
            logging.error(f"[{serverAddress}] Failed to truncate files: {e}")
        return PlainTextResponse(metrics)
