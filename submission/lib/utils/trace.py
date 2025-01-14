from .data_types import Trace, BusinessIndex, PlatformIndex
import numpy as np
import time

from collections import defaultdict

def parse(traces):
    """
    Input:
        traces - list of Trace objects as in main.py

    Output:
        dictionary {<trace_id> : [<processed traces>]}

    Processing includes sorting and removing the time of the children from the elapsed time of the parent
    """
    
    trace_dict = defaultdict(list) # { trace_id : [traces]}

    # Split traces according to their id
    for trace in traces:
        trace_dict[trace.trace_id].append(trace)

    # Sort them according to start time
    for trace in trace_dict.values():
        trace.sort(key=lambda x: x.start_time)

    # Remove time of the children from the parent
    for trace in trace_dict.values():
        child_sums = defaultdict(int)
        for el in trace:
            child_sums[el.pid] += el.elapsed_time

        for el in filter(lambda x: x.id in child_sums, trace):
            el.elapsed_time -= child_sums[el.id]

    #TODO csf and remote service transformation. henry suggests OSB service = cmdb id
    #csf service = child cmdb_id
    for trace in trace_dict.values():
        csfs = {}
        for el in filter(lambda x: x.call_type == 'CSF', trace):
            csfs[el.id] = el
        
        for el in filter(lambda x: x.pid in csfs, trace):
            csfs[el.pid].service_name = el.cmdb_id

    #remote service = remote cmdb_id
    for trace in trace_dict.values():
        for el in filter(lambda x: x.call_type == 'RemoteProcess', trace):
            el.service_name = el.cmdb_id

    return trace_dict

def get_anomalous_hosts_count(limits, traces):
    traces = parse(traces)
    results = defaultdict(lambda: [0,0])

    anomalous_trace_count = 0
    
    missing_keys = []
    for trace_id, trace in traces.items():
        
        # generate trace tree depth
        depth = {'None' : 0}
        trace.sort(key=lambda x: x.start_time)
        for trace_span in trace:
            if trace_span.pid not in depth:
                continue

            trace_span.depth = depth[trace_span.pid] + 1
            depth[trace_span.id] = trace_span.depth

        for trace_span in trace:
            if trace_span.pid not in depth: # data missing atm
                trace_span.depth = -1
                continue
            trace_span.depth = depth[trace_span.pid] + 1
            depth[trace_span.id] = trace_span.depth

        if len(list(limits.keys())[0]) == 2:
            # no host in the key
            get_key = lambda x: (x.depth, x.call_type)
        else:
            get_key = lambda x: (x.depth, x.call_type, x.cmdb_id)
        

        is_anomalous = False
        # detect individual anomalies
        for trace_span in trace:
            key = get_key(trace_span)
            if key not in limits:
                # print(key)
                continue
            
            lower, upper = limits[key]
            res_key = (trace_span.service_name, trace_span.cmdb_id)
            if not lower <= trace_span.elapsed_time <= upper or trace_span.success == False:
                results[res_key][0] += 1
                is_anomalous = True
            results[res_key][1] += 1
        
        if is_anomalous:
            anomalous_trace_count += 1

    return anomalous_trace_count / len(traces), results # percentage of traces, individual counts


PARENT_DATA = {
    'docker_001':'os_017',
    'docker_002':'os_018',
    'docker_003':'os_019',
    'docker_004':'os_020',
    'docker_005':'os_017',
    'docker_006':'os_018',
    'docker_007':'os_019',
    'docker_008':'os_020',
    'os_021': 'os_001',
    'os_022': 'os_001',
}

def filter_results(services, maximum, debug=False):
    # docker can be remote or local
    remote = defaultdict(int)
    local  = defaultdict(int)
    for service in filter(lambda x: 'docker' in x[0], services):
        if service[0] == service[1] or (service[0] in PARENT_DATA and service[1] == PARENT_DATA[service[0]]):
            local[service[0]] += 1
        else:
            remote[service[0]] += 1
    if len(local) == 0:
        # network problem
        docker = [[x, None] for x in remote.keys()]
    else:
        docker = [[x, 'cpu_container_used'] for x in local.keys()]
    
    # os
    os_021 = list(filter(lambda x: x[1] == 'os_021', services))
    os_022 = list(filter(lambda x: x[1] == 'os_022', services))

    os_res = []
    if len(os_021) > 0 and len(os_022) > 0:
        os_res.extend([['os_001', x] for x in ('Sent_queue','Received_queue')])
    elif len(os_021) > 0:
        os_res.extend([['os_021', x] for x in ('Sent_queue','Received_queue')])
    docker_os = os_res + docker

    # fly remote
    fly_remote = list(filter(lambda x: 'fly_remote' in x[0], services))
    if len(fly_remote) > 0:
        fly_remote = [['os_009', x] for x in ('Sent_queue','Received_queue')]
    else:
        fly_remote = []

    # problem in parent server
    dockers_calls = set(map(lambda x: x[0] if 'docker' in x[0] else x[1], filter(lambda x: 'docker' in x[0] or 'docker' in x[1], services)))
    parents = defaultdict(int)
    for call in dockers_calls:
        parents[PARENT_DATA[call]] += 1
    
    os_parent = [[x[0], y] for x in filter(lambda x: x[1] > 1, parents.items()) for y in ('Sent_queue','Received_queue')]

    # FIXME there was no db in meow meow's thing. i am putting in all db possibilities
    dbs = [[x,y] for x in set(map(lambda x: x[0], filter(lambda x: 'db' in x[0], services))) for y in ('On_Off_State', 'tnsping_result_time', 'Proc_User_Used_Pct', 'Proc_Used_Pct','Sess_Connect')]

    if not dbs and (len(list(filter(lambda x: x, [docker_os, fly_remote, os_parent, dbs]))) > 1
                                 or (len(docker) > 1 and docker[0][1] == None)
                                 or maximum < 0.7):
        # FIXME not 100% sure of the result, im putting in the logic to skip this one and try out later
        if debug:
            print('[INFO] Anomalies were found, but couldn\'t identify the root cause')
            print([*docker, *os_res, *fly_remote, *os_parent, *dbs])
            with open('client.log', 'a+') as f:
                f.write('[INFO] Anomalies were found, but couldn\'t identify the root cause\n')
                f.write(str([*docker, *os_res, *fly_remote, *os_parent, *dbs]) + '\n')
        return False, [*docker, *os_res, *fly_remote, *os_parent, *dbs]
    if dbs:
        return True, dbs
    return True, [*docker, *os_res, *fly_remote, *os_parent, *dbs]

def table(limits, traces, debug=False):
    anom_count, result = get_anomalous_hosts_count(limits, traces)
    
    # filter out CSF and 0 results
    analysis = list(map(lambda x: (x[0], x[1][0] / x[1][1]), filter(lambda x: x[1][0] != 0 and 'csf' not in x[0][0], result.items())))

    if debug:
        columns = sorted(set(map(lambda x: x[0][1], analysis)))
        rows = sorted(set(map(lambda x: x[0][0], analysis)))
        import pandas as pd
        df = pd.DataFrame(columns=columns, index=rows).fillna(0)
        for item in analysis:
            (row, col), val = item
            df.loc[row, col] = val
        pd.set_option("display.max_rows", None, "display.max_columns", None)
        with open('client.log', 'a+') as f:
            f.write('--------------------------------------\n')
            f.write(str(time.time()) + '\n')
            f.write(str(analysis) + '\n')
            f.write(str(df) + '\n')

    # Filter not enough amount of trace info. 1000 spans is +- 17 traces only
    if not analysis or sum(map(lambda x: x[1][1], result.items())) < 1000:
        return None

    maximum = max(analysis, key=lambda x: x[1])
    print(f'Maximum is {maximum}')

    threshold = 0.4

    if maximum[1] < threshold:
        return None

    related = maximum[1] - 0.05 # only count those within 0.05% 

    final_services = list(map(lambda x: x[0], filter(lambda x: x[1] >= related, analysis)))

    return filter_results(final_services, maximum[1], debug=debug)