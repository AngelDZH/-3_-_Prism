import csv
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
from explain_unassigned import (
    load_data, eng_skills, TRANSPORT_TO_KEY, parse_time, fmt_time,
    PENALTY_BY_PRIORITY, FIXED_COST_PER_ENGINEER, run_solver
)

SKILL_PRIORITY = {'emergency': '1', 'connection': '2', 'local': '3', 'equipment_order': '3'}
SKILL_DURATION = {'emergency': 80, 'connection': 70, 'local': 30, 'equipment_order': 20}

def _travel_km(matrix, transport_type, from_id, to_id, id_map=None):
    key = TRANSPORT_TO_KEY[transport_type]
    ids = matrix['ids']
    if id_map:
        from_id = id_map.get(from_id, from_id)
        to_id = id_map.get(to_id, to_id)
    fi, ti = ids.index(from_id), ids.index(to_id)
    return matrix[key + '_km'][fi][ti]

def _build_full_plan(engineers, all_requests_by_id, matrix, combined, unassigned_ids, id_map=None):
    engineers_by_id = {e['engineer_id']: e for e in engineers}
    by_engineer = {}
    for rid, (eid, arrival) in combined.items():
        by_engineer.setdefault(eid, []).append((arrival, rid))

    transport_ru_map = {'car': 'автомобиль', 'pedestrian': 'пешком',
                         'bicycle': 'велосипед', 'public_transport': 'общественный транспорт'}

    plan_assigned = []
    km_by_engineer = {}
    total_km = 0.0

    for eid, stops in by_engineer.items():
        eng = engineers_by_id[eid]
        transport_ru = transport_ru_map[eng['transport_type']]
        stops.sort()
        current_point = 'OFFICE_EAST'
        for arrival, rid in stops:
            req = all_requests_by_id[rid]
            km = _travel_km(matrix, eng['transport_type'], current_point, rid, id_map)
            total_km += km
            km_by_engineer[eid] = km_by_engineer.get(eid, 0) + km
            current_point = rid

            duration = int(req['service_time_min'])
            window_end = parse_time(req['window_end'])
            slack = window_end - arrival
            risk = slack < 15
            plan_assigned.append({
                'request_id': rid, 'engineer_id': eid,
                'arrival': fmt_time(arrival),
                'duration_min': duration,
                'transport': eng['transport_type'],
                'required_skill': req['required_skill'],
                'required_transport': req.get('required_transport', ''),
                'slack_min': slack,
                'risk': risk,
                'lat': float(req['latitude']), 'lon': float(req['longitude']),
                'reason': f"навык и транспорт ({transport_ru}) совпали, прибытие {fmt_time(arrival)}, "
                          f"работа {duration} мин, маршрут +{km:.1f} км"
                          + (f" ⚠ запас до конца окна всего {slack} мин" if risk else "")
            })

    plan_unassigned = []
    for rid in unassigned_ids:
        req = all_requests_by_id[rid]
        plan_unassigned.append({
            'request_id': rid,
            'reason': "не удалось разместить при пересчёте плана",
            'lat': float(req['latitude']), 'lon': float(req['longitude']),
        })

    engineers_summary = [
        {'engineer_id': eid,
         'skills': sorted(eng_skills(engineers_by_id[eid])),
         'transport_type': engineers_by_id[eid]['transport_type'],
         'jobs': len([a for a in plan_assigned if a['engineer_id'] == eid]),
         'km': round(km_by_engineer.get(eid, 0), 1)}
        for eid in sorted(by_engineer.keys())
    ]

    with open('offices.csv', encoding='utf-8') as f:
        f.readline()
        office_row = next(csv.DictReader(f, delimiter=';'))

    return {
        'metrics': {'assigned': len(plan_assigned), 'total': len(all_requests_by_id),
                    'engineers_used': len(by_engineer), 'total_km': round(total_km, 1)},
        'depot': {'lat': float(office_row['latitude']), 'lon': float(office_row['longitude'])},
        'engineers_summary': engineers_summary,
        'assigned': plan_assigned,
        'unassigned': plan_unassigned,
    }

def run_replan(cutoff_str='14:00', event_type='new_request',
               event_skill='emergency', event_window_start='14:00',
               event_window_end='16:00', event_base_request='REQ_050',
               event_id='REQ_URGENT_NEW', cancel_request_id=None,
               unavailable_engineer_id=None):
    CUTOFF = parse_time(cutoff_str)

    engineers, requests, matrix = load_data()
    ids = matrix['ids']
    requests_by_orig_id = {r['request_id']: r for r in requests}

    original_plan = run_solver()
    orig_assignment = {
        a['request_id']: (a['engineer_id'], parse_time(a['arrival']))
        for a in original_plan['assigned']
    }
    orig_unassigned_ids = {u['request_id'] for u in original_plan['unassigned']}

    done_ids = {rid for rid, (_, arr) in orig_assignment.items() if arr < CUTOFF}
    future_assigned_ids = {rid for rid, (_, arr) in orig_assignment.items() if arr >= CUTOFF}
    still_relevant_unassigned = {
        rid for rid in orig_unassigned_ids
        if parse_time(requests_by_orig_id[rid]['window_end']) > CUTOFF
    }

    remaining_ids = future_assigned_ids | still_relevant_unassigned

    if event_type == 'cancel' and cancel_request_id in remaining_ids:
        remaining_ids = remaining_ids - {cancel_request_id}

    remaining_requests = [requests_by_orig_id[rid] for rid in remaining_ids]

    new_event = None
    if event_type == 'new_request':
        base = requests_by_orig_id[event_base_request]
        new_event = {
            'request_id': event_id, 'required_skill': event_skill,
            'required_transport': '', 'service_time_min': str(SKILL_DURATION[event_skill]),
            'priority': SKILL_PRIORITY[event_skill],
            'window_start': event_window_start, 'window_end': event_window_end,
            'latitude': base['latitude'], 'longitude': base['longitude'],
        }
        remaining_requests.append(new_event)

    node_ids = ['OFFICE_EAST'] + [r['request_id'] for r in remaining_requests]
    node_index_by_id = {rid: i for i, rid in enumerate(node_ids)}
    matrix_idx = []
    for rid in node_ids:
        real_id = event_base_request if (event_type == 'new_request' and rid == event_id) else rid
        matrix_idx.append(ids.index(real_id))

    n_nodes = len(node_ids)
    n_vehicles = len(engineers)
    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    service_time = [0] + [int(r['service_time_min']) for r in remaining_requests]
    time_cb_idx, cost_cb_idx = [], []

    for e in engineers:
        key = TRANSPORT_TO_KEY[e['transport_type']]

        def make_time_cb(key=key):
            def cb(fi_, ti_):
                from_node = manager.IndexToNode(fi_)
                if manager.IndexToNode(ti_) == 0:
                    return service_time[from_node]
                fi = matrix_idx[from_node]
                ti = matrix_idx[manager.IndexToNode(ti_)]
                return int(matrix[key][fi][ti] / 60) + service_time[from_node]
            return cb

        def make_cost_cb(key=key):
            def cb(fi_, ti_):
                if manager.IndexToNode(ti_) == 0:
                    return 0
                fi = matrix_idx[manager.IndexToNode(fi_)]
                ti = matrix_idx[manager.IndexToNode(ti_)]
                return int(matrix[key + '_km'][fi][ti] * 100)
            return cb

        time_cb_idx.append(routing.RegisterTransitCallback(make_time_cb()))
        cost_cb_idx.append(routing.RegisterTransitCallback(make_cost_cb()))

    for v in range(n_vehicles):
        routing.SetArcCostEvaluatorOfVehicle(cost_cb_idx[v], v)
        routing.SetFixedCostOfVehicle(FIXED_COST_PER_ENGINEER, v)

    routing.AddDimensionWithVehicleTransits(time_cb_idx, 24 * 60, 24 * 60, False, 'Time')
    time_dim = routing.GetDimensionOrDie('Time')

    eng_index_by_id = {e['engineer_id']: v for v, e in enumerate(engineers)}

    for v, e in enumerate(engineers):
        eid = e['engineer_id']
        shift_start, shift_end = parse_time(e['shift_start']), parse_time(e['shift_end'])
        had_done = any(o_eid == eid and o_arr < CUTOFF for o_eid, o_arr in orig_assignment.values())
        effective_start = max(shift_start, CUTOFF) if had_done else shift_start

        if event_type == 'unavailable' and eid == unavailable_engineer_id:
            effective_end = min(shift_end, CUTOFF)
        else:
            effective_end = shift_end

        time_dim.CumulVar(routing.Start(v)).SetRange(effective_start, effective_start)
        time_dim.CumulVar(routing.End(v)).SetMax(effective_end)

    for node in range(1, n_nodes):
        req = remaining_requests[node - 1]
        idx = manager.NodeToIndex(node)
        ws, we = parse_time(req['window_start']), parse_time(req['window_end'])
        if event_type == 'new_request' and req['request_id'] == event_id:
            ws = max(ws, CUTOFF)
        time_dim.CumulVar(idx).SetRange(ws, we)

        allowed = []
        for v, e in enumerate(engineers):
            if event_type == 'unavailable' and e['engineer_id'] == unavailable_engineer_id:
                continue
            if req['required_skill'] not in eng_skills(e):
                continue
            if req['required_transport'] and req['required_transport'] != e['transport_type']:
                continue
            allowed.append(v)

        if allowed:
            routing.VehicleVar(idx).SetValues(allowed + [-1])
        else:
            routing.ActiveVar(idx).SetValue(0)

        routing.AddDisjunction([idx], PENALTY_BY_PRIORITY[int(req['priority'])])

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(10)
    solution = routing.SolveWithParameters(params)

    if not solution:
        return {'error': 'решение не найдено'}

    new_assignment = {}
    for v, e in enumerate(engineers):
        eid = e['engineer_id']
        index = routing.Start(v)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != 0:
                rid = node_ids[node]
                arrival = solution.Value(time_dim.CumulVar(index))
                new_assignment[rid] = (eid, arrival)
            index = solution.Value(routing.NextVar(index))

    diff = []
    for rid in remaining_ids:
        old_eid, old_arr = orig_assignment.get(rid, (None, None))
        if rid in new_assignment:
            new_eid, new_arr = new_assignment[rid]
            if old_eid is None:
                diff.append(f"{rid}: было не назначено -> теперь {new_eid} в {fmt_time(new_arr)}")
            elif old_eid != new_eid:
                diff.append(f"{rid}: было {old_eid} в {fmt_time(old_arr)} -> стало {new_eid} в {fmt_time(new_arr)}")
        else:
            if old_eid is not None:
                diff.append(f"{rid}: было {old_eid} в {fmt_time(old_arr)} -> теперь не назначено")

    if event_type == 'cancel':
        diff.append(f"{cancel_request_id}: отменена клиентом, убрана из плана")
    elif event_type == 'unavailable':
        diff.append(f"{unavailable_engineer_id}: стал недоступен с {fmt_time(CUTOFF)}, его будущие заявки переназначены")
    else:
        event_label = {'emergency': 'новая авария', 'connection': 'новое срочное подключение',
                       'local': 'новая срочная локальная заявка'}.get(event_skill, 'новое событие')
        if event_id in new_assignment:
            eid, arr = new_assignment[event_id]
            diff.append(f"{event_id} ({event_label}): назначена {eid} в {fmt_time(arr)}")
        else:
            diff.append(f"{event_id} ({event_label}): не удалось назначить")

    combined = {rid: val for rid, val in orig_assignment.items() if rid not in remaining_ids}
    if event_type == 'cancel' and cancel_request_id in combined:
        del combined[cancel_request_id]
    for rid, val in new_assignment.items():
        combined[rid] = val

    all_relevant_ids = remaining_ids | ({event_id} if event_type == 'new_request' else set())
    new_unassigned_ids = [rid for rid in all_relevant_ids if rid not in new_assignment]

    all_requests_by_id = dict(requests_by_orig_id)
    if new_event:
        all_requests_by_id[event_id] = new_event


    id_map = {event_id: event_base_request} if new_event else None
    full_plan = _build_full_plan(engineers, all_requests_by_id, matrix, combined, new_unassigned_ids, id_map)

    return {
        'cutoff': fmt_time(CUTOFF),
        'event_type': event_type,
        'done_before_cutoff': len(done_ids),
        'affected_count': len(diff),
        'unchanged_count': len(remaining_ids) - len(diff) + 1,
        'changes': diff,
        'new_plan': full_plan,
    }

if __name__ == '__main__':
    print('=== Сценарий: новая авария ===')
    result = run_replan()
    print(f"Изменилось: {result['affected_count']}, без изменений: {result['unchanged_count']}")
    print(f"Новый план: {result['new_plan']['metrics']}")
