#!/usr/bin/env python

import os
import queue
import socket
import time
import threading
from threading import Thread
import copy
import json
from datetime import datetime

import stomp
import tools
import siteMapping

TOPIC = "/topic/perfsonar.raw.packet-retransmits"
INDEX_PREFIX = 'ps_retransmits-'
siteMapping.reload()


class MyListener(object):

    def on_message(self, headers, message):
        q.put(message)

    def on_error(self, headers, message):
        print('received an error %s' % message)
        os._exit(1)

    def on_heartbeat_timeout(self):
        print('AMQ - lost heartbeat. Needs a reconnect!')
        connect_to_MQ(reset=True)

    def on_disconnected(self):
        print('AMQ - no connection. Needs a reconnect!')
        connect_to_MQ(reset=True)


def connect_to_MQ(reset=False):

    if tools.connection is not None:
        if reset and tools.connection.is_connected():
            tools.connection.disconnect()
            tools.connection = None

        if tools.connection.is_connected():
            return

    print("connecting to MQ")
    tools.connection = None

    addresses = socket.getaddrinfo('clever-turkey.rmq.cloudamqp.com', 61614)
    ip = addresses[0][4][0]
    host_and_ports = [(ip, 61614)]
    print(host_and_ports)

    tools.connection = stomp.Connection(
        host_and_ports=host_and_ports,
        use_ssl=True,
        vhost=RMQ_parameters['RMQ_VHOST']
    )
    tools.connection.set_listener('MyConsumer', MyListener())
    tools.connection.start()
    tools.connection.connect(RMQ_parameters['RMQ_USER'], RMQ_parameters['RMQ_PASS'], wait=True)
    tools.connection.subscribe(destination=TOPIC, ack='auto', id=RMQ_parameters['RMQ_ID'], headers={"durable": True, "auto-delete": False})
    return


def eventCreator():
    aLotOfData = []
    es_conn = tools.get_es_connection()
    while True:
        d = q.get()
        m = json.loads(d)

        data = {
            '_type': 'doc'
        }
        # print(m)
        source = m['meta']['source']
        destination = m['meta']['destination']
        data['MA'] = m['meta']['measurement_agent']
        data['src'] = source
        data['dest'] = destination
        data['src_host'] = m['meta']['input_source']
        data['dest_host'] = m['meta']['input_destination']
        data['ipv6'] = False
        if ':' in source or ':' in destination:
            data['ipv6'] = True
        so = siteMapping.getPS(source)
        de = siteMapping.getPS(destination)
        if so != None:
            data['src_site'] = so[0]
            data['src_VO'] = so[1]
        if de != None:
            data['dest_site'] = de[0]
            data['dest_VO'] = de[1]
        data['src_production'] = siteMapping.isProductionThroughput(source)
        data['dest_production'] = siteMapping.isProductionThroughput(
            destination)
        if not 'datapoints'in m:
            print(threading.current_thread().name,
                  'no datapoints in this message!')
            q.task_done()
            continue
        su = m['datapoints']
        for ts, th in su.items():
            dati = datetime.utcfromtimestamp(float(ts))
            data['_index'] = INDEX_PREFIX + str(dati.year) + "." + str(dati.month)  # + "." + str(dati.day)
            data['timestamp'] = int(float(ts) * 1000)
            data['_id'] = hash((m['meta']['org_metadata_key'], data['timestamp']))
            data['retransmits'] = th
            # print(data)
            aLotOfData.append(copy.copy(data))
        q.task_done()

        if len(aLotOfData) > 100:
            succ = tools.bulk_index(aLotOfData, es_conn=es_conn, thread_name=threading.current_thread().name)
            if succ is True:
                aLotOfData = []

        if len(aLotOfData) > 10000:
            print('too many entries in memory. sleep for a minute.')
            time.sleep(60)


RMQ_parameters = tools.get_RMQ_connection_parameters()


q = queue.Queue()
# start eventCreator threads
for i in range(1):
    t = Thread(target=eventCreator)
    t.daemon = True
    t.start()


while True:
    connect_to_MQ()
    time.sleep(55)
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "threads:", threading.active_count(), "qsize:", q.qsize())
