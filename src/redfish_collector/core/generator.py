from os import path,makedirs
import yaml
from ..core.dataReconstruction import dataReconstructor
from ..core.rawCollector import dataCollector
from concurrent.futures import ThreadPoolExecutor
import time
import logging
import asyncio

REDFISH_DATA = '/tmp/redfish-data/'
# logFormat = '%(asctime)s [%(levelname)s] %(message)s'
# logging.basicConfig(format=logFormat, level=logLevel.upper())

def redfishCollector(serverAddress,username,password):
    templateDir = path.join(path.dirname(__file__), '../core/templates/')
    # logging.info("Gathering...")
    logLevel='info'
    try:
        dataRaw, dataNewSchema, modelSchemaDir = asyncio.run(dataCollector(serverAddress,username,password,templateDir,logLevel))
        dataReconstructor(dataRaw, dataNewSchema, modelSchemaDir, serverAddress,logLevel)
        return
    except Exception as err:
        logging.error("[%s] Tried collecting data failed: %s" %(serverAddress,err))
        with open('%sNewData/%s.json' % (REDFISH_DATA, serverAddress), 'w') as f:
            pass
        raise

def generatorMultiThreading():
    while True:
        inventoryFile = REDFISH_DATA + 'inventory.yml'
        if not path.exists(REDFISH_DATA):
            logging.info(f"Directory {REDFISH_DATA} does not exist. Creating it.")
            makedirs(REDFISH_DATA)

        if not path.exists(inventoryFile):
            logging.info(f"{inventoryFile} does not exist. Creating it.")
            with open(inventoryFile, 'w') as f:
                f.write('') 
        # inventoryDir = path.join(path.dirname(__file__), '../core/templates/configs/inventory.yml')
        try:
            with open(inventoryFile, 'r') as f:
                yamlContent = f.read()
                inventory = yaml.safe_load(yamlContent)
        except Exception as err:
            logging.error("Read Inventory Configs failed: %s" %err)
            return
        if not inventory:
            logging.warning("Inventory is empty")
        else:
            max_workers = min(3, len(inventory)) 
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                for server in inventory:
                    executor.submit(redfishCollector, server['serverAddress'], server['username'], server['password'])
                # futures = [executor.submit(redfishCollector, server['serverAddress'], server['username'], server['password']) for server in inventory]

            for server in inventory:
                if float(server['timeCalled']) > time.time() - 300:
                    inventory.remove(server)
            if len(inventory) >= 1:
                with open('%sinventory.yml' %REDFISH_DATA, 'w') as f:
                    yaml.dump(inventory, f, default_flow_style=False)
            else:
                with open('%sinventory.yml' %REDFISH_DATA, 'w') as f:
                    f.write('')
        time.sleep(120)