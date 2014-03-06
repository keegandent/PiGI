import leveldb
from datetime import datetime, timedelta
import threading
import os
import sys
import time
import json
import logging
from collections import deque
from configurator import cfg

log = logging.getLogger(__name__)

LOG_WRITE_RATE = 5
MAX_ENTRY_DIST = 30

script_dir = sys.path[0]

def dt2unix(dt):
    return int(dt.strftime("%s"))

def get_last_totalcount():

    log.info("Getting last totalcount")
    db = leveldb.LevelDB(cfg.get('db','path'))
    now = dt2unix(datetime.now())
    d = 1
    last_entries_keys = []
    i = 0

    while not last_entries_keys:

        log.debug("Searching further (%d)..."%d)
        last_entries_keys = list(db.RangeIter(key_from=str(now-d),include_value=False))

        # Abort safely when no results are returned (fresh instance)
        if not last_entries_keys and i == 0:
            return 0
        else:
            d = d*2
            i = i+1

    last_key = last_entries_keys[-1]
    entry_json = db.Get(last_key)
    entry = json.loads(entry_json)
    return entry['total']

def average_log_entries(entries,tube_rate_factor):
    result = []
    previous_entry = None
    for entry in entries:
        if not previous_entry:
            previous_entry = entry
            result.append(entry)
            continue

        seconds = float(entry.get("timestamp",0)) - int(previous_entry.get("timestamp",0))
        counts = float(entry.get("total",0)) - int(previous_entry.get("total",0))

        if counts < 0: counts=0
        if seconds != 0:
            cps = counts/seconds
            cpm = cps * 60
            eqd = round(cpm * tube_rate_factor,2)

            entry["cps"] = int(cps)
            entry["cpm"] = int(cpm)
            entry["doserate"] = eqd

            result.append(entry)
            previous_entry = entry
    return result

class GeigerLog(threading.Thread):
    def __init__(self,geiger):
        self.db = leveldb.LevelDB(cfg.get('db','path'))
        self.geiger = geiger
        threading.Thread.__init__(self)
        self.daemon = True
        self.start()
        self.last_log = None

    def run(self):
        log.info("Starting geigerlog")
        avg_age = dt2unix(datetime.now() - timedelta(minutes=15))
        avg_list = deque()
        entries_list = list(self.db.RangeIter(key_from=str(avg_age)))
        for e in entries_list: avg_list.append(json.loads(e[1]))
        while True:
            time.sleep(LOG_WRITE_RATE)
            avg_age = dt2unix(datetime.now() - timedelta(minutes=15))
            if avg_list:
                while avg_list[0]["timestamp"] < avg_age:
                    avg_list.popleft()

            state = self.geiger.get_state()
            avg_list.append(state)
            avg = round(sum([e["doserate"] for e in avg_list])/len(avg_list),3)
            state["doserate_avg"] = avg
            key = str(state["timestamp"])
            value = json.dumps(state)
            self.db.Put(key, value)
            self.last_log = state
            log.debug("Logging: %s : %s"%(key,value))
            log.debug(self.db.GetStats())

    def get_log_entries(self,start=None,end=None,age=None,amount=500):

        if end is None:
            end = dt2unix(datetime.now())
        if age:
            start = end - age
        elif start is None:
            start = int(self.db.RangeIter(key_from="0",include_value=False).next())

        result = []
        log.info("Fetching %s log entries from %d to %s"%(str(amount),start,end))
        if amount is None:
            entries_list = list(self.db.RangeIter(key_from=str(start)))
            last_time = start
            for e in entries_list:
                entry = json.loads(e[1])
                if int(entry["timestamp"])-last_time > MAX_ENTRY_DIST:
                    insert_time = last_time + LOG_WRITE_RATE
                    record_insert = {"cps":0,"cpm":0,"doserate":0.0,"doserate_avg":0.0,"total":entry["total"],"timestamp":insert_time}
                    while insert_time < int(entry["timestamp"]):
                        result.append(record_insert.copy())
                        insert_time += 10
                        record_insert["timestamp"]=insert_time
                last_time = int(entry["timestamp"])
                result.append(entry)

            if result:
                last = result[-1]
                if end - int(last["timestamp"]) > MAX_ENTRY_DIST:
                    insert_time = int(last["timestamp"]) + LOG_WRITE_RATE
                    record_insert = {"cps":0,"cpm":0,"doserate":0.0,"doserate_avg":0.0,"total":last["total"],"timestamp":insert_time}
                    while insert_time < end:
                        result.append(record_insert.copy())
                        insert_time += 10
                        record_insert["timestamp"]=insert_time
            return result

        delta_total = end - start
        delta_step = delta_total / amount
        step = 0
        while True:
            t = start + delta_step * step
            db_iter = self.db.RangeIter(key_from=str(t))
            try:
                (timestamp,entry_json) = db_iter.next()
            except StopIteration:
                break;

            entry = json.loads(entry_json)

            if int(timestamp)-t>MAX_ENTRY_DIST:
                entry["timestamp"] = str(t)
                entry["cps"] = 0
                entry["cpm"] = 0
                entry["doserate"] = 0
                entry["doserate_avg"] = 0

            if not result:
                result.append(entry)
            elif result[-1] != entry:
                result.append(entry)

            step += 1
        return average_log_entries(result,cfg.getfloat('geigercounter','tube_rate_factor'))

if __name__ == "__main__":
    print get_last_totalcount()
